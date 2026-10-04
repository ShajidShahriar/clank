"""Task I-7.5: ways of wording the question for the embedder, for the eval only.

A variant changes ONLY how the question becomes a vector. It must report the same identity as the embedder it wraps (the stored index
belongs to that model), and the `instruct` variant is a CONTROL: it must send exactly what production sends, so running it must give the
baseline back, bit for bit. Everything the variants send goes out as a plain text (no second wrapper on top).
"""
import pytest

from embedding import Embedder, FakeEmbedder
from embedding.ollama import QUERY_TASK, OllamaEmbedder
from embedding.query_variants import QUERY_VARIANTS, VariantQueryEmbedder, variant_embedder
from fake_ollama import FakeOllama

DIM = 8
QUESTION = "where is retry handled?"


def make(server):
    return OllamaEmbedder(model="test-model", base_url=server.url, dim=DIM, sleep=lambda s: None)


def sent_by(embedder_call):
    with FakeOllama(dim=DIM) as server:
        embedder_call(server)
        (request,) = server.requests
        (text,) = request["input"]
        return text


def test_the_control_variant_sends_exactly_what_production_sends():
    production = sent_by(lambda server: make(server).embed_query(QUESTION))
    control = sent_by(lambda server: variant_embedder(make(server), "instruct").embed_query(QUESTION))
    assert control == production == f"Instruct: {QUERY_TASK}\nQuery: {QUESTION}"


def test_the_raw_variant_sends_the_bare_question():
    assert sent_by(lambda server: variant_embedder(make(server), "raw").embed_query(QUESTION)) == QUESTION


def test_the_alt_variant_uses_the_models_default_retrieval_instruction():
    sent = sent_by(lambda server: variant_embedder(make(server), "alt").embed_query(QUESTION))
    assert sent == f"Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: {QUESTION}"
    assert sent != f"Instruct: {QUERY_TASK}\nQuery: {QUESTION}"


def test_variants_are_named_and_unknown_names_are_refused():
    assert set(QUERY_VARIANTS) == {"instruct", "raw", "alt"}
    with pytest.raises(ValueError, match="nope"):
        variant_embedder(FakeEmbedder(), "nope")


def test_a_variant_keeps_the_identity_dimension_and_warmup_of_the_embedder_it_wraps():
    inner = FakeEmbedder(digest="abc")
    v = variant_embedder(inner, "raw")
    assert isinstance(v, Embedder) and isinstance(v, VariantQueryEmbedder)
    assert (v.model_name, v.dim) == (inner.model_name, inner.dim)
    inner.digest = "def"
    assert v.model_name == inner.model_name, "the identity is read live, not copied (a re-pull changes it)"
    v.warmup()
    assert inner.warmup_count == 1


def test_documents_pass_through_untouched():
    inner = FakeEmbedder()
    v = variant_embedder(inner, "raw")
    assert v.embed_documents(["a", "b"]) == inner.embed_documents(["a", "b"])


def test_the_raw_variant_gives_the_vector_of_the_bare_text():
    inner = FakeEmbedder()
    assert variant_embedder(inner, "raw").embed_query(QUESTION) == inner.embed_documents([QUESTION])[0]


def test_the_name_of_a_variant_is_kept_for_the_results_file():
    assert variant_embedder(FakeEmbedder(), "alt").variant == "alt"
