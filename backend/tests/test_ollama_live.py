"""Task I-3f: the real embedder against a real Ollama. Skipped when Ollama or the model is not there.

Run alone with:  uv run pytest -q -m live
These tests are also where the facts the fake server only ASSUMES get checked, so a change in Ollama's
behaviour shows up here (see the "characterization" test).
"""
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from embedder_contract import check_order_preserved
from embedding import EmbeddingTooLong, ModelNotFound, OllamaEmbedder
from embedding.ollama import DEFAULT_DIM, DEFAULT_MODEL

URL = "http://localhost:11434"


def _ollama_has_the_model():
    try:
        with urllib.request.urlopen(f"{URL}/api/tags", timeout=2) as r:
            return DEFAULT_MODEL in [m["name"] for m in json.loads(r.read())["models"]]
    except (OSError, ValueError, KeyError):
        return False


pytestmark = [pytest.mark.live, pytest.mark.skipif(not _ollama_has_the_model(), reason=f"Ollama with {DEFAULT_MODEL} is not running")]


@pytest.fixture(scope="module")
def embedder():
    e = OllamaEmbedder()
    e.warmup()
    return e


def post(body):
    req = urllib.request.Request(f"{URL}/api/embed", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def test_vectors_have_the_configured_length_and_unit_size(embedder):
    (v,) = embedder.embed_documents(["def f(): pass"])
    assert len(v) == DEFAULT_DIM == embedder.dim == 1024
    assert all(type(x) is float for x in v)
    assert sum(x * x for x in v) ** 0.5 == pytest.approx(1.0, abs=1e-3)


def test_vector_order_matches_text_order(embedder):
    # Measured on this model: batch vs alone differ by up to ~1.5e-4, two different texts by at least ~2e-2.
    check_order_preserved(embedder, tol=1e-3)


SNIPPETS = {
    "retry": "def fetch_with_retry(url, attempts=3):\n    for i in range(attempts):\n        try:\n            return http_get(url)\n        except TimeoutError:\n            time.sleep(2 ** i)\n    raise RuntimeError('gave up')",
    "css": ".button { background: #1a73e8; border-radius: 4px; padding: 8px 16px; color: white; }",
    "sql": "SELECT customers.name, SUM(orders.total) FROM customers JOIN orders ON orders.customer_id = customers.id GROUP BY customers.name;",
    "math": "def gcd(a, b):\n    while b:\n        a, b = b, a % b\n    return a",
}


def test_a_question_lands_closest_to_the_right_chunk(embedder):
    docs = embedder.embed_documents(list(SNIPPETS.values()))
    for question, expected in [
        ("how do we retry a failed network request?", "retry"),
        ("which style makes a blue rounded button?", "css"),
        ("total spent per customer", "sql"),
        ("greatest common divisor", "math"),
    ]:
        q = embedder.embed_query(question)
        scores = {name: sum(a * b for a, b in zip(q, d)) for name, d in zip(SNIPPETS, docs)}
        assert max(scores, key=scores.get) == expected, (question, scores)


def test_over_limit_text_is_an_error_naming_the_chunk(embedder):
    huge = "word " * 20_000  # ~20k tokens, far over the 8192 context
    with pytest.raises(EmbeddingTooLong) as err:
        embedder.embed_documents(["fine", huge, "fine too"], ids=["c1", "c2", "c3"])
    assert err.value.chunk_ids == ["c2"]


def test_characterization_ollama_cuts_silently_when_truncate_is_on():
    # Why we always send truncate=false. If Ollama ever starts erroring here, this test fails and
    # we can re-read decision 7.
    reply = post({"model": DEFAULT_MODEL, "input": ["word " * 20_000], "truncate": True, "options": {"num_ctx": 8192}})
    assert len(reply["embeddings"][0]) == 1024            # a normal-looking vector, no error ...
    assert reply["prompt_eval_count"] < 8200              # ... made from only the first ~8192 tokens


def test_unknown_model_is_reported_with_the_pull_command():
    with pytest.raises(ModelNotFound, match="ollama pull no-such-model"):
        OllamaEmbedder(model="no-such-model", retries=0).embed_documents(["a"])


def test_the_model_stays_loaded_for_the_keep_alive_time(embedder):
    embedder.embed_documents(["keep me loaded"])
    with urllib.request.urlopen(f"{URL}/api/ps", timeout=5) as r:
        loaded = {m["name"]: m for m in json.loads(r.read())["models"]}
    expires = datetime.fromisoformat(loaded[DEFAULT_MODEL]["expires_at"])
    assert expires - datetime.now(timezone.utc) > timedelta(minutes=20)  # the default would be 5 minutes
    assert loaded[DEFAULT_MODEL]["context_length"] == embedder.num_ctx   # a different num_ctx would reload the model


def test_the_identity_carries_the_digest_that_ollama_lists(embedder):
    with urllib.request.urlopen(f"{URL}/api/tags", timeout=5) as r:
        digests = {m["name"]: m["digest"] for m in json.loads(r.read())["models"]}
    short = digests[DEFAULT_MODEL].removeprefix("sha256:")[:12]
    assert embedder.model_name == f"{DEFAULT_MODEL}@{short}"      # the same 12 characters `ollama list` prints as the model's ID
    assert len(short) == 12
