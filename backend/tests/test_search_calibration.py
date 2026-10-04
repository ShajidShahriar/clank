"""Task 7.14: a margin or a cutoff is matched to the model by its TAG (the part before "@"), not by the exact `tag@digest`.

A re-pulled tag gets a new digest. Before this task that silently turned the demotion off after re-indexing. Now:
- same tag, same digest: applied, no note;
- same tag, other digest: still applied, and `calibration_note` says what it was measured on and what the index uses (devlog 72: margins from 0.15
  to 0.50 gave identical results, so a small drift in the weights should not matter, but the caller is told);
- other tag: not applied, and the old `ranking_note` / `cutoff_note` says so;
- the same rule for the relevance cutoff;
- the note is for the caller (SearchResult, Retrieval, Context), NEVER in the text given to the LLM.
Hand-made 2-d hits give exact scores.
"""
import pytest

from hand_made import Question2D, make_world
from search import DemotionPolicy, RelevanceCutoff, build_context, retrieve, search
from search.calibration import calibration_match

TAG = "fake-hash-2"                      # what Question2D().model_name is without a digest


class DigestQuestion(Question2D):
    def __init__(self, digest):
        super().__init__()
        self.digest = digest


@pytest.fixture
def world(conn, tmp_path):
    return make_world(conn, tmp_path)


def indexed_for(world, digest):
    """A world whose store was built for `fake-hash-2@digest`, plus the embedder that matches it."""
    store = world({"tests/t.py": dict(scores=[0.70], test=True), "src/a.py": dict(scores=[0.66]), "src/b.py": dict(scores=[0.40])})
    embedder = DigestQuestion(digest)
    store.set_signature(embedder.model_name, 2)
    return store, embedder


def demotion(digest="aaa"):
    return DemotionPolicy(0.06, calibrated_for=f"{TAG}@{digest}")


def ids(result):
    return [h.chunk["id"] for h in result.hits]


# ---- the pure rule

@pytest.mark.parametrize("measured, current, applies, note_has", [
    ("m:1@aaa", "m:1@aaa", True, None),                                    # same tag, same digest
    ("m:1@aaa", "m:1@bbb", True, ["measured on aaa", "this index uses bbb", "results should be close, re-run the eval to confirm"]),
    ("m:1@aaa", "other:1@aaa", False, None),                               # a different tag: the digest being equal does not help
    ("m:1@aaa", "m:2@aaa", False, None),                                   # "m:1" is not a prefix match of "m:2"
    ("m:1", "m:1", True, None),                                            # no digests on either side
    ("m:1@aaa", "m:1", True, ["measured on aaa", "no digest"]),            # the embedder never warmed up: the tag still matches
])
def test_calibration_match_compares_the_tag_and_keeps_the_digest_as_provenance(measured, current, applies, note_has):
    ok, note = calibration_match(measured, current)
    assert ok is applies
    if note_has is None:
        assert note is None
    else:
        assert all(part in note for part in note_has)


# ---- demotion

def test_same_tag_and_same_digest_demotes_with_no_note(conn, world):
    store, embedder = indexed_for(world, "aaa")
    result = search(conn, 1, embedder, store, "q", k=3, test_policy=demotion("aaa"))
    assert ids(result)[0] == "src/a.py#0" and result.ranking == "demoted"
    assert result.calibration_note is None and result.ranking_note is None


def test_same_tag_and_another_digest_still_demotes_and_says_what_it_was_measured_on(conn, world):
    store, embedder = indexed_for(world, "bbb")
    result = search(conn, 1, embedder, store, "q", k=3, test_policy=demotion("aaa"))
    assert ids(result) == ["src/a.py#0", "tests/t.py#0", "src/b.py#0"] and result.ranking == "demoted"
    assert result.calibration_note == "measured on aaa, this index uses bbb; results should be close, re-run the eval to confirm"
    assert result.ranking_note is None, "the demotion DID apply, so there is nothing to say about it not applying"


def test_another_tag_demotes_nothing_and_keeps_the_old_note(conn, world):
    store, embedder = indexed_for(world, "aaa")
    result = search(conn, 1, embedder, store, "q", k=3, test_policy=DemotionPolicy(0.06, calibrated_for="other-model:1@aaa"))
    assert ids(result)[0] == "tests/t.py#0" and result.ranking == "raw"
    assert "not demoted" in result.ranking_note and "other-model:1@aaa" in result.ranking_note and result.calibration_note is None


def test_the_shipped_default_survives_a_re_pull_of_its_tag():
    from search import DEFAULT_DEMOTION
    ok, note = calibration_match(DEFAULT_DEMOTION.calibrated_for, "qwen3-embedding:0.6b@123456789abc")
    assert ok and "ac6da0dfba84" in note and "123456789abc" in note


# ---- cutoff

def cutoff(digest="aaa", **kw):
    return RelevanceCutoff(calibrated_for=f"{TAG}@{digest}", **(kw or dict(min_score=0.60)))


def test_a_cutoff_with_the_same_tag_and_digest_cuts_with_no_note(conn, world):
    store, embedder = indexed_for(world, "aaa")
    result = search(conn, 1, embedder, store, "q", k=5, test_policy=None, cutoff=cutoff("aaa"))
    assert len(result.hits) == 2 and result.below_cutoff == 1 and result.calibration_note is None and result.cutoff_note is None


def test_a_cutoff_with_another_digest_still_cuts_and_says_so(conn, world):
    store, embedder = indexed_for(world, "bbb")
    result = search(conn, 1, embedder, store, "q", k=5, test_policy=None, cutoff=cutoff("aaa"))
    assert len(result.hits) == 2 and result.below_cutoff == 1
    assert result.calibration_note == "measured on aaa, this index uses bbb; results should be close, re-run the eval to confirm" and result.cutoff_note is None


def test_a_cutoff_for_another_tag_cuts_nothing_and_keeps_the_old_note(conn, world):
    store, embedder = indexed_for(world, "aaa")
    result = search(conn, 1, embedder, store, "q", k=5, test_policy=None, cutoff=RelevanceCutoff(min_score=0.6, calibrated_for="x:1@aaa"))
    assert len(result.hits) == 3 and result.below_cutoff == 0 and "not applied" in result.cutoff_note and result.calibration_note is None


def test_both_policies_on_another_digest_give_one_note_per_distinct_message(conn, world):
    store, embedder = indexed_for(world, "bbb")
    same = search(conn, 1, embedder, store, "q", k=5, test_policy=demotion("aaa"), cutoff=cutoff("aaa"))
    assert same.calibration_note.count("measured on aaa") == 1, "the same message is not said twice"
    differ = search(conn, 1, embedder, store, "q", k=5, test_policy=demotion("aaa"), cutoff=cutoff("ccc"))
    assert "measured on aaa" in differ.calibration_note and "measured on ccc" in differ.calibration_note


# ---- the note is for the caller, never for the LLM

def test_retrieve_and_build_context_carry_the_note_and_the_llm_text_never_does(conn, world):
    store, embedder = indexed_for(world, "bbb")
    r = retrieve(conn, 1, world.repo, embedder, store, "q", k=3, max_tokens=6000, test_policy=demotion("aaa"), cutoff=cutoff("aaa"))
    assert "measured on aaa" in r.calibration_note
    ctx = build_context(conn, 1, world.repo, embedder, store, "q", k=3, max_tokens=6000, test_policy=demotion("aaa"), cutoff=cutoff("aaa"))
    assert "measured on aaa" in ctx.calibration_note
    for word in ("measured on", "aaa", "bbb", "re-run the eval", "calibration"):
        assert word not in ctx.text, f"{word!r} leaked into the text the LLM reads"
    assert [p.rel_path for p in ctx.passages][0] == "src/a.py", "and the demotion applied"
