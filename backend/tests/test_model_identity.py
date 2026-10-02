"""Task I-5.3 at the index level: the model's digest is part of its identity, so replacing the weights behind a tag re-embeds everything,
and the identity is read AFTER warmup (only then can an embedder know it)."""
import chunk_store
from embedding import FakeEmbedder
from test_index_project import assert_stores_agree, repo, run  # noqa: F401  (repo is a pytest fixture)


class LearnsItsIdentityAtWarmup(FakeEmbedder):
    """Like the real embedder: the digest is only known once warmup() has asked the server."""

    def __init__(self, digest_after_warmup):
        super().__init__()
        self._later = digest_after_warmup

    def warmup(self):
        super().warmup()
        self.digest = self._later


def test_replaced_weights_behind_the_same_tag_mean_everything_is_embedded_again(conn, repo):
    old = FakeEmbedder(digest="weights-1")
    first, _, store = run(conn, repo, old)
    n = len(chunk_store.all_ids(conn, 1))
    same_again, _, _ = run(conn, repo, FakeEmbedder(digest="weights-1"), store)
    assert same_again.embedded == 0 and not same_again.store_rebuilt              # the same weights: nothing to do

    new = FakeEmbedder(digest="weights-2")                                        # same tag, same size, new weights
    report, _, _ = run(conn, repo, new, store)
    assert report.store_rebuilt and report.embedded == n == new.text_count
    assert chunk_store.models_in_use(conn, 1) == {("fake-hash-8@weights-2", 8)}
    assert store.signature() == ("fake-hash-8@weights-2", 8)
    assert_stores_agree(conn, store, new)
    assert first.embedded == n


def test_going_from_an_unknown_digest_to_a_known_one_also_embeds_again(conn, repo):
    # e.g. an old Ollama that did not list digests, then an update: we cannot know the vectors match, so we do not pretend to
    _, e, store = run(conn, repo, FakeEmbedder())
    report, _, _ = run(conn, repo, FakeEmbedder(digest="d1"), store)
    assert report.store_rebuilt and report.embedded > 0


def test_the_identity_is_read_after_warmup(conn, repo):
    e = LearnsItsIdentityAtWarmup("learned-late")
    first, _, store = run(conn, repo, e)
    assert chunk_store.models_in_use(conn, 1) == {("fake-hash-8@learned-late", 8)}   # rows carry the identity warmup revealed
    assert store.signature() == ("fake-hash-8@learned-late", 8)
    second, _, _ = run(conn, repo, LearnsItsIdentityAtWarmup("learned-late"), store)
    assert second.embedded == 0 and not second.store_rebuilt                         # if the identity were read before warmup, this run would redo everything
