"""The Ollama embedder against a fake local server (task I-3d). Oversized input is task 3e."""
import socket

import pytest

from embedder_contract import check_order_preserved, distinct_texts
from embedding import Embedder
from embedding.errors import BadResponse, EmbeddingError, EmbeddingTooLong, ModelNotFound, OllamaUnavailable
from embedding.ollama import OllamaEmbedder
from fake_ollama import FakeOllama

DIM = 8


@pytest.fixture
def sleeps():
    return []


def make(server, sleeps, **kw):
    kw.setdefault("model", "test-model")
    return OllamaEmbedder(base_url=server.url, dim=DIM, sleep=sleeps.append, **kw)


def test_satisfies_the_interface():
    with FakeOllama(dim=DIM) as server:
        e = make(server, [])
        assert isinstance(e, Embedder)
        assert e.model_name == "test-model" and e.dim == DIM


def test_vector_order_matches_text_order_through_real_batching():
    with FakeOllama(dim=DIM) as server:
        check_order_preserved(make(server, []))  # one call, batches of 16, batches of 7


def test_texts_are_sent_in_batches_of_batch_size():
    with FakeOllama(dim=DIM) as server:
        make(server, [], batch_size=16).embed_documents(distinct_texts(40))
        assert [len(r["input"]) for r in server.requests] == [16, 16, 8]


def test_documents_go_in_as_is_and_the_request_asks_for_no_truncation_and_keep_alive():
    texts = distinct_texts(3)
    with FakeOllama(dim=DIM) as server:
        make(server, [], keep_alive="45m", num_ctx=8192).embed_documents(texts)
        (req,) = server.requests
        assert req["input"] == texts                  # no wrapper on documents
        assert req["model"] == "test-model"
        assert req["truncate"] is False               # over-limit text must be an error, not a silent cut
        assert req["keep_alive"] == "45m"             # decision 10: keep the model loaded
        assert req["options"]["num_ctx"] == 8192      # do not rely on Ollama's small default context


def test_query_is_wrapped_in_an_instruction_and_differs_from_the_plain_document_vector():
    with FakeOllama(dim=DIM) as server:
        e = make(server, [])
        q = e.embed_query("where is retry handled?")
        (req,) = server.requests
        (sent,) = req["input"]
        assert sent.startswith("Instruct:") and "Query: where is retry handled?" in sent
        assert q != e.embed_documents(["where is retry handled?"])[0]


def test_empty_batch_sends_nothing():
    with FakeOllama(dim=DIM) as server:
        assert make(server, []).embed_documents([]) == []
        assert server.requests == []


def test_bad_arguments_are_refused_before_any_request():
    with FakeOllama(dim=DIM) as server:
        e = make(server, [])
        with pytest.raises(ValueError):
            e.embed_documents(["a", "b"], ids=["one"])
        with pytest.raises(TypeError):
            e.embed_documents(["a", 1])
        assert server.requests == []


def test_wrong_vector_length_is_an_error_not_a_silent_mismatch():
    with FakeOllama(dim=DIM, wrong_dim=True) as server:
        with pytest.raises(BadResponse, match=r"9 numbers.*expects 8"):
            make(server, []).embed_documents(["a"])


def test_wrong_number_of_vectors_is_an_error():
    with FakeOllama(dim=DIM, wrong_count=True) as server:
        with pytest.raises(BadResponse, match=r"2 vectors for 3 texts"):
            make(server, []).embed_documents(["a", "b", "c"])


def test_cold_start_is_retried_with_growing_pauses(sleeps):
    with FakeOllama(dim=DIM, fail_first=2, fail_status=503) as server:
        out = make(server, sleeps, retries=3, backoff=0.5).embed_documents(["a"])
        assert len(out) == 1 and len(server.requests) == 3
        assert sleeps == [0.5, 1.0]


def test_dropped_connection_is_retried_too(sleeps):
    with FakeOllama(dim=DIM, fail_first=1, fail_status=0) as server:  # 0 = close the socket with no answer
        assert len(make(server, sleeps, retries=3).embed_documents(["a"])) == 1
        assert len(server.requests) == 2


def test_gives_up_after_the_retries_with_a_clear_error(sleeps):
    with FakeOllama(dim=DIM, fail_first=99, fail_status=503) as server:
        with pytest.raises(OllamaUnavailable, match="503"):
            make(server, sleeps, retries=2).embed_documents(["a"])
        assert len(server.requests) == 3  # first try + 2 retries


def test_ollama_not_running_says_how_to_start_it(sleeps):
    with socket.socket() as s:  # grab a free port, then close it so nothing listens there
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    e = OllamaEmbedder(base_url=f"http://127.0.0.1:{port}", dim=DIM, sleep=sleeps.append, retries=2)
    with pytest.raises(OllamaUnavailable, match=r"127\.0\.0\.1.*ollama serve"):
        e.embed_documents(["a"])
    assert len(sleeps) == 2


def test_unknown_model_says_how_to_pull_it_and_is_not_retried(sleeps):
    with FakeOllama(dim=DIM) as server:
        with pytest.raises(ModelNotFound, match=r"ollama pull missing-model"):
            make(server, sleeps, model="missing-model").embed_documents(["a"])
        assert len(server.requests) == 1 and sleeps == []


def test_other_client_errors_are_not_retried(sleeps):
    with FakeOllama(dim=DIM, fail_first=5, fail_status=400) as server:
        with pytest.raises(EmbeddingError, match="400"):
            make(server, sleeps).embed_documents(["a"])
        assert len(server.requests) == 1 and sleeps == []


def test_warmup_loads_the_model_and_survives_a_cold_start(sleeps):
    with FakeOllama(dim=DIM, fail_first=1, fail_status=503) as server:
        make(server, sleeps).warmup()
        assert len(server.requests) == 2
        assert server.requests[-1]["keep_alive"]


# ---- task 3e: text over the model's limit is an error that names the chunk ----

LIMIT = 100
SHORT, LONG = "s" * 10, "L" * 500


def test_over_limit_text_names_every_offending_chunk_and_returns_nothing(sleeps):
    with FakeOllama(dim=DIM, max_chars=LIMIT) as server:
        with pytest.raises(EmbeddingTooLong) as err:
            make(server, sleeps).embed_documents([SHORT, LONG, SHORT, LONG], ids=["c1", "c2", "c3", "c4"])
        assert err.value.chunk_ids == ["c2", "c4"]
        assert "c2" in str(err.value) and "c4" in str(err.value)
        assert "c1" not in str(err.value) and "c3" not in str(err.value)
        assert sleeps == []                                   # not retried: waiting cannot make text shorter
        assert all(r["truncate"] is False for r in server.requests)  # and we never asked Ollama to cut it


def test_without_ids_the_error_names_the_position():
    with FakeOllama(dim=DIM, max_chars=LIMIT) as server:
        with pytest.raises(EmbeddingTooLong, match=r"text #1") as err:
            make(server, []).embed_documents([SHORT, LONG])
        assert err.value.chunk_ids == ["text #1"]


def test_one_bad_chunk_in_a_later_batch_still_fails_the_whole_call():
    texts = [SHORT] * 4 + [LONG] + [SHORT] * 2
    ids = [f"c{i}" for i in range(len(texts))]
    with FakeOllama(dim=DIM, max_chars=LIMIT) as server:
        with pytest.raises(EmbeddingTooLong) as err:
            make(server, [], batch_size=2).embed_documents(texts, ids=ids)
        assert err.value.chunk_ids == ["c4"]


def test_a_vector_is_never_returned_for_a_cut_text():
    # The real danger: with truncate left on, Ollama answers with a vector of the CUT text and no error.
    with FakeOllama(dim=DIM, max_chars=LIMIT) as server:
        with pytest.raises(EmbeddingTooLong):
            make(server, []).embed_documents([LONG], ids=["only"])


def test_texts_within_the_limit_are_fine():
    with FakeOllama(dim=DIM, max_chars=LIMIT) as server:
        assert len(make(server, []).embed_documents([SHORT, "x" * LIMIT])) == 2


def test_other_400_errors_are_not_mistaken_for_too_long():
    with FakeOllama(dim=DIM, fail_first=1, fail_status=400) as server:  # answers {"error": "model is loading"}
        with pytest.raises(EmbeddingError) as err:
            make(server, []).embed_documents(["a"], ids=["c1"])
        assert not isinstance(err.value, EmbeddingTooLong)


def test_a_too_long_question_is_an_error_too():
    with FakeOllama(dim=DIM, max_chars=LIMIT) as server:
        with pytest.raises(EmbeddingTooLong):
            make(server, []).embed_query("q" * 500)
