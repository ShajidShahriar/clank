"""The embedder interface and the fake embedder (tasks I-3a, I-3b).

The fake has no meaning at all: same text gives the same numbers, different text gives different
numbers. Indexing tests use it to ask "did we embed the right chunks?" without Ollama running.
"""
import math

import pytest

from embedding import Embedder, FakeEmbedder
from embedding.errors import EmbeddingTooLong


def test_fake_satisfies_the_interface():
    assert isinstance(FakeEmbedder(), Embedder)


def test_same_text_gives_the_same_vector_even_across_instances():
    a, b = FakeEmbedder(), FakeEmbedder()
    assert a.embed_documents(["def f(): pass"]) == b.embed_documents(["def f(): pass"])
    assert a.embed_documents(["x"]) == a.embed_documents(["x"])


def test_different_texts_give_different_vectors():
    texts = [f"chunk number {i}" for i in range(300)]
    vectors = FakeEmbedder().embed_documents(texts)
    assert len({tuple(v) for v in vectors}) == 300


def test_every_vector_has_the_configured_length():
    for dim in (1, 8, 33, 1024):
        e = FakeEmbedder(dim=dim)
        assert e.dim == dim
        assert all(len(v) == dim for v in e.embed_documents(["a", "b", "c"]))
        assert len(e.embed_query("q")) == dim


def test_vectors_are_plain_floats_of_length_one():
    (v,) = FakeEmbedder().embed_documents(["anything"])
    assert all(type(x) is float for x in v)
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-9)


def test_model_name_is_set_and_changes_with_dim():
    assert FakeEmbedder().model_name
    assert FakeEmbedder(dim=8).model_name != FakeEmbedder(dim=16).model_name  # a dim change must look like a model change


def test_empty_batch_gives_empty_result():
    e = FakeEmbedder()
    assert e.embed_documents([]) == []
    assert e.text_count == 0


def test_it_counts_what_it_embeds():
    e = FakeEmbedder()
    e.embed_documents(["a", "b", "c"])
    e.embed_documents(["d"])
    e.embed_query("q")
    assert e.text_count == 4        # documents only
    assert e.batch_count == 2
    assert e.query_count == 1
    assert e.embedded_texts == ["a", "b", "c", "d"]


def test_query_vector_matches_the_document_vector_for_the_same_text():
    # The real embedder wraps queries in an instruction. The fake does not, so a test can find a chunk
    # by asking with that chunk's exact text.
    e = FakeEmbedder()
    assert e.embed_query("hello") == e.embed_documents(["hello"])[0]


def test_ids_argument_is_accepted_and_must_match_the_texts():
    e = FakeEmbedder()
    assert len(e.embed_documents(["a", "b"], ids=["1", "2"])) == 2
    with pytest.raises(ValueError):
        e.embed_documents(["a", "b"], ids=["only-one"])


def test_non_string_input_is_refused():
    with pytest.raises(TypeError):
        FakeEmbedder().embed_documents(["fine", 42])


# ---- task 3e: the fake can enforce a limit too, so indexer tests can exercise the failure path ----


def test_fake_with_a_limit_refuses_long_text_and_names_the_chunk():
    e = FakeEmbedder(max_chars=20)
    with pytest.raises(EmbeddingTooLong) as err:
        e.embed_documents(["short", "x" * 50, "also short"], ids=["a", "b", "c"])
    assert err.value.chunk_ids == ["b"] and "b" in str(err.value)
    assert e.text_count == 0 and e.batch_count == 0   # nothing was embedded or counted


def test_fake_without_a_limit_accepts_anything():
    assert len(FakeEmbedder().embed_documents(["x" * 100_000])) == 1


# ---- hardening from the I-3 review: warmup() is part of the interface, so an indexer can call it on any embedder ----

def test_warmup_is_part_of_the_interface_and_the_fake_supports_it():
    assert hasattr(Embedder, "warmup")
    e = FakeEmbedder()
    e.warmup()
    assert e.warmup_count == 1
    assert e.text_count == 0 and e.batch_count == 0  # warming up embeds nothing that counts
