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


# ---- hardening from the I-3 review ----

GARBAGE = [
    b"not json at all",
    b"",
    b"\xff\xfe\x00garbage",                       # not even text
    b'{"embeddings": [null]}',
    b'{"embeddings": "oops"}',
    b'{"embeddings": [["a","b","c","d","e","f","g","h"]]}',   # right length, not numbers
    b'{"embeddings": [[true,true,true,true,true,true,true,true]]}',
    b'{"embeddings": [[NaN,NaN,NaN,NaN,NaN,NaN,NaN,NaN]]}',
    b"[1, 2, 3]",
    b"null",
    b'{"error": "something odd"}',                # a 200 that carries no embeddings
]


@pytest.mark.parametrize("raw", GARBAGE, ids=lambda b: repr(b)[:40])
def test_garbage_answers_become_bad_response_not_a_raw_python_error(raw):
    with FakeOllama(dim=DIM, raw_reply=raw) as server:
        with pytest.raises(BadResponse):
            make(server, []).embed_documents(["a"], ids=["c1"])


def test_garbage_for_a_question_is_also_bad_response():
    with FakeOllama(dim=DIM, raw_reply=b'{"embeddings": [null]}') as server:
        with pytest.raises(BadResponse):
            make(server, []).embed_query("q")


def test_integer_numbers_are_returned_as_floats():
    ints = b'{"embeddings": [[0, 1, 0, 1, 0, 1, 0, 1]]}'
    with FakeOllama(dim=DIM, raw_reply=ints) as server:
        (v,) = make(server, []).embed_documents(["a"])
        assert v == [0.0, 1.0] * 4 and all(type(x) is float for x in v)


def test_an_unrelated_400_that_says_exceeds_is_not_blamed_on_the_chunks():
    with FakeOllama(dim=DIM, fail_first=1, fail_status=400, fail_message="batch size exceeds the limit of 8") as server:
        with pytest.raises(EmbeddingError) as err:
            make(server, []).embed_documents(["a"], ids=["c1"])
        assert not isinstance(err.value, EmbeddingTooLong)
        assert "exceeds the limit of 8" in str(err.value)  # and the real reason is still shown


def test_default_timeout_keeps_the_worst_case_for_a_hung_ollama_short():
    e = OllamaEmbedder()
    assert e.timeout <= 60 and (e.retries + 1) * e.timeout <= 240  # 4 minutes at most, not 8


# ---- model identity: the tag AND its digest (task I-5.3) ----
# A tag such as qwen3-embedding:0.6b can be pulled again and point at different weights. The name does not change, the vectors do.
# So the identity stored on every row and on the vector store is tag@digest, and the existing "different model -> embed again" rules do the rest.

def test_after_warmup_the_identity_includes_the_models_digest_but_requests_still_use_the_plain_tag():
    with FakeOllama(dim=DIM, digests={"test-model": "sha256:abcdef0123456789" + "0" * 40}) as server:
        e = make(server, [])
        assert e.model_name == "test-model"                       # not known yet: warmup() has not run
        e.warmup()
        assert e.model_name == "test-model@abcdef012345"          # first 12 characters, the same short ID `ollama list` shows
        e.embed_documents(["a"])
        assert {r["model"] for r in server.requests} == {"test-model"}   # Ollama is always asked for the tag, never for the identity


def test_pulling_the_tag_again_changes_the_identity_at_the_next_warmup():
    with FakeOllama(dim=DIM, digests={"test-model": "sha256:" + "a" * 64}) as server:
        e = make(server, [])
        e.warmup()
        before = e.model_name
        server.digests["test-model"] = "sha256:" + "b" * 64       # ollama pull gave the tag new weights
        e.warmup()
        assert e.model_name != before and e.model_name.startswith("test-model@bbbbbbbbbbbb")


def test_a_digest_without_the_sha256_prefix_is_handled():
    with FakeOllama(dim=DIM, digests={"test-model": "0123456789abcdef" * 4}) as server:
        e = make(server, [])
        e.warmup()
        assert e.model_name == "test-model@0123456789ab"


def test_a_model_missing_from_the_tag_list_keeps_the_plain_tag_instead_of_failing():
    with FakeOllama(dim=DIM, digests={"some-other-model": "sha256:" + "c" * 64}) as server:
        e = make(server, [])
        e.warmup()
        assert e.model_name == "test-model"


def test_a_tag_without_a_version_matches_its_latest_entry():
    with FakeOllama(dim=DIM, models=("embedder:latest",), digests={"embedder:latest": "sha256:" + "d" * 64}) as server:
        e = make(server, [], model="embedder:latest")
        e.warmup()
        plain = OllamaEmbedder(model="embedder", base_url=server.url, dim=DIM, sleep=lambda s: None)
        assert e.model_name == "embedder:latest@dddddddddddd"
        assert plain._find_digest({"models": [{"name": "embedder:latest", "digest": "sha256:" + "d" * 64}]}) == "dddddddddddd"


@pytest.mark.parametrize("raw", [b"not json", b"[]", b'{"models": "oops"}', b'{"models": [null]}', b'{"models": [{"name": "test-model", "digest": 5}]}'],
                         ids=lambda b: repr(b)[:30])
def test_garbage_from_the_tag_list_is_a_bad_response_not_a_silent_loss_of_protection(raw):
    with FakeOllama(dim=DIM, tags_reply=raw) as server:
        with pytest.raises(BadResponse):
            make(server, []).warmup()


def test_a_failing_tag_list_is_retried_and_then_reported_like_any_other_ollama_problem(sleeps):
    with FakeOllama(dim=DIM, tags_status=503, tags_reply=b'{"error": "loading"}') as server:
        with pytest.raises(OllamaUnavailable, match="503"):
            make(server, sleeps, retries=2).warmup()
        assert server.tag_requests == 3 and len(sleeps) == 2


def test_the_identity_and_the_dimension_are_checked_with_the_same_embedder_object_over_several_warmups():
    with FakeOllama(dim=DIM) as server:
        e = make(server, [])
        e.warmup()
        first = e.model_name
        e.warmup()
        assert e.model_name == first and server.tag_requests == 2
