"""The vector store contract (task I-4.5a). Every implementation must pass the same tests: the in-memory one now, Chroma next.

One store holds the vectors of ONE project (chunk ids hash the path, not the project, so two projects share ids).
SQLite is the truth; the store only maps id -> vector and finds nearest neighbours. Scores are cosine SIMILARITY (higher is
better, 1.0 = same direction), pinned here so a vector library's default distance can never change what a number means.
"""
import math

import pytest

from vectorstore import ChromaVectorStore, InMemoryVectorStore

FACTORIES = {
    "memory": lambda tmp_path: InMemoryVectorStore(),
    "chroma": lambda tmp_path: ChromaVectorStore(tmp_path / "chroma", "project_1"),
}


@pytest.fixture(params=list(FACTORIES))
def store(request, tmp_path):
    return FACTORIES[request.param](tmp_path)


def test_an_empty_store(store):
    assert store.count() == 0 and store.ids() == set()
    assert store.query([1.0, 0.0], 5) == []


def test_upsert_then_ids_count_and_nearest(store):
    store.upsert(["a", "b", "c"], [[1.0, 0.0], [0.0, 1.0], [0.9, 0.1]])
    assert store.count() == 3 and store.ids() == {"a", "b", "c"}
    ranked = store.query([1.0, 0.0], 3)
    assert [i for i, _ in ranked] == ["a", "c", "b"]
    scores = [s for _, s in ranked]
    assert scores[0] == pytest.approx(1.0, abs=1e-4)
    assert scores[1] == pytest.approx(0.9 / math.hypot(0.9, 0.1), abs=1e-4)
    assert scores[2] == pytest.approx(0.0, abs=1e-4)
    assert scores == sorted(scores, reverse=True)


def test_scores_are_cosine_so_the_length_of_a_vector_does_not_matter(store):
    store.upsert(["short", "long", "other"], [[1.0, 0.0], [50.0, 0.0], [0.0, 1.0]])
    top = dict(store.query([3.0, 0.0], 3))
    assert top["short"] == pytest.approx(1.0, abs=1e-4) and top["long"] == pytest.approx(1.0, abs=1e-4)
    assert top["other"] == pytest.approx(0.0, abs=1e-4)


def test_upserting_an_existing_id_replaces_its_vector(store):
    store.upsert(["a", "b"], [[1.0, 0.0], [0.0, 1.0]])
    store.upsert(["a"], [[0.0, 1.0]])
    assert store.count() == 2
    assert dict(store.query([0.0, 1.0], 2)) == pytest.approx({"a": 1.0, "b": 1.0}, abs=1e-4)


def test_delete_removes_and_ignores_unknown_ids(store):
    store.upsert(["a", "b", "c"], [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    store.delete(["b", "never-stored"])
    store.delete([])
    assert store.ids() == {"a", "c"} and "b" not in [i for i, _ in store.query([0.0, 1.0], 10)]


def test_k_larger_than_the_store_returns_everything_and_zero_returns_nothing(store):
    store.upsert(["a", "b"], [[1.0, 0.0], [0.0, 1.0]])
    assert len(store.query([1.0, 0.0], 100)) == 2
    assert store.query([1.0, 0.0], 0) == []
    assert len(store.query([1.0, 0.0], 1)) == 1


def test_an_empty_upsert_is_fine(store):
    store.upsert([], [])
    assert store.count() == 0


def test_bad_input_is_refused_and_nothing_is_stored(store):
    with pytest.raises(ValueError):
        store.upsert(["a", "b"], [[1.0, 0.0]])                    # one vector short
    with pytest.raises(ValueError):
        store.upsert(["a", "a"], [[1.0, 0.0], [0.0, 1.0]])        # same id twice in one call
    with pytest.raises(ValueError):
        store.upsert(["a", "b"], [[1.0, 0.0], [float("nan"), 1.0]])
    with pytest.raises(ValueError):
        store.upsert(["a"], [[0.0, 0.0]])                          # cosine of a zero vector means nothing
    with pytest.raises(ValueError):
        store.upsert(["a"], [[]])
    assert store.count() == 0


def test_all_vectors_in_a_store_must_have_the_same_length(store):
    store.upsert(["a"], [[1.0, 0.0]])
    with pytest.raises(ValueError):
        store.upsert(["b"], [[1.0, 0.0, 0.0]])
    with pytest.raises(ValueError):
        store.query([1.0, 0.0, 0.0], 1)
    assert store.ids() == {"a"}


def test_a_bad_query_is_refused(store):
    store.upsert(["a"], [[1.0, 0.0]])
    for bad in ([0.0, 0.0], [float("inf"), 0.0], []):
        with pytest.raises(ValueError):
            store.query(bad, 1)
    with pytest.raises(ValueError):
        store.query([1.0, 0.0], -1)


def test_clear_empties_the_store_and_allows_a_new_dimension(store):
    store.upsert(["a"], [[1.0, 0.0]])
    store.clear()
    assert store.count() == 0 and store.ids() == set()
    store.upsert(["a"], [[1.0, 0.0, 0.0]])   # a different model, a different size: fine after a clear
    assert store.count() == 1


def test_ids_returns_a_copy(store):
    store.upsert(["a"], [[1.0, 0.0]])
    store.ids().add("hacked")
    assert store.ids() == {"a"}
