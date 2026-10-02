"""Vector i must belong to text i (task I-3c). A reordered batch is a silent bug: search still runs, it just
finds the wrong code. So we prove the check itself can fail, using embedders broken on purpose."""
import pytest

from embedder_contract import check_order_preserved
from embedding import FakeEmbedder


class Broken(FakeEmbedder):
    """Only misbehaves on batches of 2+ texts, so the single-text ground truth stays correct."""

    def embed_documents(self, texts, ids=None):
        out = super().embed_documents(texts, ids)
        return self.damage(out) if len(out) > 1 else out

    def damage(self, vectors):
        raise NotImplementedError


class Reverses(Broken):
    def damage(self, vectors):
        return vectors[::-1]


class SwapsFirstTwo(Broken):
    def damage(self, vectors):
        return [vectors[1], vectors[0], *vectors[2:]]


class DropsLast(Broken):
    def damage(self, vectors):
        return vectors[:-1]


class DuplicatesFirst(Broken):
    def damage(self, vectors):
        return [vectors[0], *vectors[:-1]]  # same length, but the first vector now appears twice


class ShufflesOnlyBigBatches(Broken):
    """Fine for small batches, wrong past 16: the kind of bug you only see on a real repo."""

    def damage(self, vectors):
        return vectors[::-1] if len(vectors) > 16 else vectors


def test_fake_keeps_order():
    check_order_preserved(FakeEmbedder())
    check_order_preserved(FakeEmbedder(dim=1024))


@pytest.mark.parametrize("broken", [Reverses, SwapsFirstTwo, DropsLast, DuplicatesFirst, ShufflesOnlyBigBatches])
def test_the_check_catches_broken_embedders(broken):
    with pytest.raises(AssertionError):
        check_order_preserved(broken())


def test_failure_message_says_which_position_got_which_text():
    with pytest.raises(AssertionError, match=r"position 0 holds the wrong vector: it matches text 1"):
        check_order_preserved(SwapsFirstTwo(), batch_sizes=(None,))


def test_failure_message_for_a_short_batch():
    with pytest.raises(AssertionError, match=r"returned 39 vectors"):
        check_order_preserved(DropsLast(), batch_sizes=(None,))


def test_tolerance_allows_tiny_differences_but_not_a_reorder():
    class Jitter(FakeEmbedder):
        def embed_documents(self, texts, ids=None):
            return [[x + 1e-7 for x in v] for v in super().embed_documents(texts, ids)] if len(texts) > 1 \
                else super().embed_documents(texts, ids)

    check_order_preserved(Jitter(), tol=1e-5)          # last-decimals noise is fine
    with pytest.raises(AssertionError):
        check_order_preserved(Jitter(), tol=0.0)       # and tol=0 really is strict
    with pytest.raises(AssertionError):
        check_order_preserved(Reverses(), tol=1e-5)    # a tolerance must never hide a reorder
