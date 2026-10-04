"""Task I-7.3: the runner. It indexes a repo, asks every question of that repo, and writes ONE json file per run with everything a later
analysis needs (top-10 per question, timings, model identity, chunker fingerprint, cap, repo commits), so nothing is ever re-embedded just
to look at the numbers again. Proved here with the fake embedder on a tiny repo: no Ollama, no heat.
"""
import copy
import json
import subprocess
import time
from pathlib import Path

import pytest
import yaml

import chunk_store
import search.context as context_module
from chunker.core import MAX_CHUNK_TOKENS
from embedding import FakeEmbedder
from embedding.errors import OllamaUnavailable
from eval import runner
from eval.runner import load_questions, rescore, run_repo, write_results
from indexing.fingerprint import chunker_fingerprint
from failing_embedders import Raising
from vectorstore import InMemoryVectorStore

QUESTIONS_FILE = Path(__file__).resolve().parent.parent / "eval" / "questions.yaml"

FILES = {
    "shop.py": "def order_total(items):\n    return sum(i.price for i in items)\n\n\ndef apply_discount(total, pct):\n    return total * (1 - pct)\n",
    "net.py": "def retry_request(url, attempts=3):\n    for _ in range(attempts):\n        pass\n    return None\n",
    "tests/test_shop.py": "def test_order_total():\n    assert order_total([]) == 0\n",
    "CHANGELOG.md": "# Changes\n\n## 1.0\n\nAdded order totals and discounts.\n",
}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    for rel, text in FILES.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def embed_text_of(conn, rel_path, symbol=None):
    rows = chunk_store.chunks_for_file(conn, 1, rel_path)
    return next(r["embed_text"] for r in rows if symbol is None or r["symbol"] == symbol)


def make_question(id, text, expect, kind="name", split="tune", repo="tiny"):
    return {"id": id, "repo": repo, "kind": kind, "split": split, "question": text, "expect": expect}


def go(conn, repo, questions, embedder=None, store=None, **kw):
    embedder = embedder or FakeEmbedder()
    store = store or InMemoryVectorStore()
    return run_repo(conn, 1, "tiny", repo, embedder, store, questions, **kw)


@pytest.fixture
def asked(conn, repo):
    """Index once to learn the chunk texts, then ask questions whose text IS a chunk's embed text (the fake finds those at rank 1)."""
    embedder, store = FakeEmbedder(), InMemoryVectorStore()
    first = run_repo(conn, 1, "tiny", repo, embedder, store, [])
    questions = [
        make_question("q01", embed_text_of(conn, "net.py", "retry_request"), [{"path": "net.py", "symbol": "retry_request"}]),
        make_question("q02", embed_text_of(conn, "shop.py", "order_total"), [{"path": "shop.py", "symbol": "order_total"}]),
        make_question("q03", "an unrelated question", [], kind="negative"),
    ]
    return embedder, store, first, questions


# ---- the real question file is pinned by tests: what the user approved is what runs

def test_the_approved_question_file_has_the_approved_shape():
    meta = load_questions(QUESTIONS_FILE)
    qs = meta["questions"]
    assert [q["id"] for q in qs] == [f"q{i:02d}" for i in range(1, 26)]
    counted = [q for q in qs if q["expect"]]
    assert len(counted) == 20 and len(qs) - len(counted) == 5
    assert {r: sum(q["repo"] == r for q in counted) for r in ("clank", "flask", "express")} == {"clank": 6, "flask": 8, "express": 6}
    assert [q["id"] for q in qs if q["kind"] == "negative"] == ["q21", "q22", "q23", "q24", "q25"]
    assert all(q["expect"] == [] for q in qs if q["kind"] == "negative")
    assert [q["id"] for q in qs if q["split"] == "holdout"] == ["q03", "q06", "q09", "q12", "q15", "q18", "q21", "q24"]
    for name, repo in meta["repos"].items():
        assert len(repo["rev"]) == 40 and set(repo["rev"]) <= set("0123456789abcdef"), name


def test_a_malformed_question_file_is_refused_with_a_reason(tmp_path):
    good = yaml.safe_load(QUESTIONS_FILE.read_text())

    def refused(mutate, words):
        broken = copy.deepcopy(good)
        mutate(broken)
        path = tmp_path / "q.yaml"
        path.write_text(yaml.safe_dump(broken))
        with pytest.raises(ValueError, match=words):
            load_questions(path)

    refused(lambda d: d["questions"][1].update(id="q01"), "duplicate")
    refused(lambda d: d["questions"][0].update(kind="vibes"), "kind")
    refused(lambda d: d["questions"][0].update(split="train"), "split")
    refused(lambda d: d["questions"][0].update(repo="django"), "repo")
    refused(lambda d: d["questions"][0].update(expect=[]), "needs an answer")
    refused(lambda d: d["questions"][20].update(expect=[{"path": "a.py", "symbol": None}]), "negative")
    refused(lambda d: d["questions"][0].update(question="  "), "question")
    refused(lambda d: d["questions"][0].update(expect=[{"symbol": "x"}]), "path")


# ---- one run

def test_a_run_records_what_a_later_analysis_needs(conn, repo, asked):
    embedder, store, _, questions = asked
    r = run_repo(conn, 1, "tiny", repo, embedder, store, questions, k=10, max_tokens=2000, pinned_rev="a" * 40)
    meta = r["meta"]
    assert meta["repo"] == "tiny" and meta["model"] == embedder.model_name and meta["dim"] == embedder.dim
    assert meta["chunker_fingerprint"] == chunker_fingerprint()
    assert meta["chunk_cap_tokens"] == MAX_CHUNK_TOKENS
    assert (meta["k"], meta["max_tokens"]) == (10, 2000)
    assert meta["rev_pinned"] == "a" * 40 and meta["rev_actual"] is None and meta["rev_matches"] is False
    assert [q["id"] for q in r["questions"]] == ["q01", "q02", "q03"]


def test_each_question_keeps_its_top_ten_with_the_tags_and_a_rank(conn, repo, asked):
    embedder, store, _, questions = asked
    r = run_repo(conn, 1, "tiny", repo, embedder, store, questions)
    q1, q2, q3 = r["questions"]
    assert (q1["rank"], q1["top1"], q1["found_in_context"]) == (1, True, True)
    assert (q2["rank"], q2["found_in_context"]) == (1, True)
    assert q3["rank"] is None and q3["top10"], "a negative has no rank, but its scores are kept (they feed the relevance floor)"
    scores = [h["score"] for h in q1["top10"]]
    assert scores == sorted(scores, reverse=True) and len(scores) <= 10
    best = q1["top10"][0]
    assert (best["path"], best["symbol"], best["is_test"], best["is_changelog"]) == ("net.py", "retry_request", False, False)
    by_path = {h["path"]: h for q in r["questions"] for h in q["top10"]}
    assert by_path["tests/test_shop.py"]["is_test"] is True and by_path["tests/test_shop.py"]["is_changelog"] is False
    assert by_path["CHANGELOG.md"]["is_changelog"] is True and by_path["CHANGELOG.md"]["is_test"] is False


def test_only_the_questions_of_this_repo_are_asked(conn, repo, asked):
    embedder, store, _, questions = asked
    questions[1]["repo"] = "other"
    r = run_repo(conn, 1, "tiny", repo, embedder, store, questions)
    assert [q["id"] for q in r["questions"]] == ["q01", "q03"]


def test_found_in_context_is_false_when_the_answer_did_not_make_the_final_text(conn, repo, asked):
    embedder, store, _, questions = asked
    r = run_repo(conn, 1, "tiny", repo, embedder, store, questions, k=10, max_tokens=1)    # a one-token budget: only the best passage survives
    q1, q2, _ = r["questions"]
    assert q1["found_in_context"] is True and q1["over_budget"] is True
    questions[0]["question"] = embed_text_of(conn, "shop.py", "apply_discount")            # asks for apply_discount but expects retry_request
    r = run_repo(conn, 1, "tiny", repo, embedder, store, questions, k=10, max_tokens=1)
    assert r["questions"][0]["rank"] is not None and r["questions"][0]["found_in_context"] is False


# ---- the index step

def test_the_index_step_counts_embed_calls_against_chunks(conn, repo):
    embedder = FakeEmbedder()
    r = go(conn, repo, [], embedder=embedder)
    idx = r["index"]
    n = len(chunk_store.all_ids(conn, 1))
    assert idx["chunks"] == n and idx["embedded"] == n == embedder.text_count
    assert idx["embed_texts"] == n and idx["embed_calls"] == embedder.batch_count >= 1
    assert idx["files_seen"] == 4 and idx["seconds"] >= 0 and idx["describe"].startswith("done")


def test_a_second_index_pass_with_nothing_changed_embeds_nothing(conn, repo):
    embedder, store = FakeEmbedder(), InMemoryVectorStore()
    r = run_repo(conn, 1, "tiny", repo, embedder, store, [], reindex=True)
    again = r["index_again"]
    assert again["embedded"] == 0 and again["embed_calls"] == 0 and again["seconds"] >= 0
    assert r["index"]["embedded"] > 0
    assert "index_again" not in run_repo(conn, 1, "tiny", repo, embedder, store, [])


def test_an_index_that_stops_ends_the_run_with_the_reason_and_asks_nothing(conn, repo):
    embedder = Raising(error=OllamaUnavailable("not running"), when=lambda texts: True)
    r = go(conn, repo, [make_question("q01", "anything", [{"path": "net.py", "symbol": None}])], embedder=embedder)
    assert r["aborted"].startswith("stopped") and "not running" in r["aborted"]
    assert r["questions"] == []


def test_index_false_reuses_the_existing_index(conn, repo, asked):
    embedder, store, _, questions = asked
    calls = embedder.batch_count
    r = run_repo(conn, 1, "tiny", repo, embedder, store, questions, index=False)
    assert embedder.batch_count == calls and "index" not in r and len(r["questions"]) == 3


# ---- timings

class SlowEmbedder(FakeEmbedder):
    def embed_query(self, text):
        time.sleep(0.03)
        return super().embed_query(text)


class SlowStore(InMemoryVectorStore):
    def query(self, vector, k):
        time.sleep(0.03)
        return super().query(vector, k)


def test_query_time_is_split_into_embedding_the_vector_store_and_the_rest(conn, repo):
    embedder, store = SlowEmbedder(), SlowStore()
    run_repo(conn, 1, "tiny", repo, embedder, store, [])
    question = make_question("q01", embed_text_of(conn, "net.py", "retry_request"), [{"path": "net.py", "symbol": "retry_request"}])
    t = run_repo(conn, 1, "tiny", repo, embedder, store, [question], index=False)["questions"][0]["timings_ms"]
    assert set(t) == {"embed_query", "vector_query", "sqlite", "expand", "fresh", "build", "total"}
    assert t["embed_query"] >= 25 and t["vector_query"] >= 25, "each slow part is charged to its own bucket"
    assert t["embed_query"] < 200 and t["vector_query"] < 200
    assert t["sqlite"] < 25 and t["expand"] < 25 and t["fresh"] < 25, "the slow parts must not be counted a second time in another bucket"
    assert t["total"] >= t["embed_query"] + t["vector_query"]
    assert all(v >= 0 for v in t.values())
    assert abs(sum(v for k, v in t.items() if k != "total") - t["total"]) < 1.0


def test_the_stage_timers_never_leave_the_modules_patched(conn, repo, asked):
    embedder, store, _, questions = asked
    originals = (context_module.search, context_module.expand, context_module.check_freshness)
    run_repo(conn, 1, "tiny", repo, embedder, store, questions)
    assert (context_module.search, context_module.expand, context_module.check_freshness) == originals

    class Boom(InMemoryVectorStore):
        def query(self, vector, k):
            raise RuntimeError("boom")
    bad = Boom()
    bad._signature = store._signature
    with pytest.raises(RuntimeError, match="boom"):
        run_repo(conn, 1, "tiny", repo, embedder, bad, questions, index=False)
    assert (context_module.search, context_module.expand, context_module.check_freshness) == originals


# ---- the file on disk

def test_the_results_file_round_trips_and_rescoring_it_gives_the_same_ranks(conn, repo, asked, tmp_path):
    embedder, store, _, questions = asked
    r = run_repo(conn, 1, "tiny", repo, embedder, store, questions)
    out = tmp_path / "out" / "tiny.json"
    write_results(r, out)
    saved = json.loads(out.read_text())
    assert saved["meta"]["model"] == embedder.model_name
    scores = rescore(saved, questions)
    assert [s.rank for s in scores] == [q["rank"] for q in r["questions"]] == [1, 1, None]


def test_rescoring_uses_the_expectations_given_now_not_the_ones_saved(conn, repo, asked):
    embedder, store, _, questions = asked
    r = run_repo(conn, 1, "tiny", repo, embedder, store, questions)
    changed = copy.deepcopy(questions)
    changed[0]["expect"] = [{"path": "shop.py", "symbol": "apply_discount"}]
    ranks = [s.rank for s in rescore(r, changed)]
    assert ranks[0] != 1 and ranks[1] == 1


def test_git_head_of_a_repo_and_of_a_folder_that_is_not_one(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert runner.git_head(plain) is None
    repo = tmp_path / "g"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n")
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q")
    git("-c", "user.email=a@b.c", "-c", "user.name=t", "add", ".")
    git("-c", "user.email=a@b.c", "-c", "user.name=t", "commit", "-q", "-m", "x")
    assert runner.git_head(repo) == git("rev-parse", "HEAD") and len(runner.git_head(repo)) == 40


# ---- k, the cap of ten, and the warm-up

def test_top_ten_is_cut_at_ten_when_k_is_larger_and_k_is_respected_when_smaller(conn, tmp_path):
    root = tmp_path / "many"
    root.mkdir()
    for i in range(15):
        (root / f"m{i:02d}.py").write_text(f"def fn_{i}(x):\n    return x + {i}\n")
    embedder, store = FakeEmbedder(), InMemoryVectorStore()
    run_repo(conn, 1, "tiny", root, embedder, store, [])
    question = make_question("q01", embed_text_of(conn, "m03.py", "fn_3"), [{"path": "m03.py", "symbol": "fn_3"}])
    wide = run_repo(conn, 1, "tiny", root, embedder, store, [question], k=15, index=False)["questions"][0]
    assert len(wide["top10"]) == 10 and wide["passages"] > 0
    narrow = run_repo(conn, 1, "tiny", root, embedder, store, [question], k=3, index=False)["questions"][0]
    assert len(narrow["top10"]) == 3


class LearnsItsDigestAtWarmup(FakeEmbedder):
    """Like the real Ollama embedder: the model identity is only complete after warmup()."""

    def warmup(self):
        super().warmup()
        self.digest = "abc123"


def test_asking_without_indexing_still_warms_the_embedder_up(conn, repo):
    store = InMemoryVectorStore()
    run_repo(conn, 1, "tiny", repo, LearnsItsDigestAtWarmup(), store, [])      # the index it builds carries the digest
    fresh = LearnsItsDigestAtWarmup()                                          # a new process: not warmed up yet
    question = make_question("q01", embed_text_of(conn, "net.py", "retry_request"), [{"path": "net.py", "symbol": "retry_request"}])
    r = run_repo(conn, 1, "tiny", repo, fresh, store, [question], index=False)
    assert fresh.warmup_count == 1 and r["meta"]["model"].endswith("@abc123")
    assert r["questions"][0]["rank"] == 1


@pytest.mark.parametrize("step, bucket, others", [
    ("expand", "expand", ("fresh", "sqlite", "embed_query", "vector_query")),
    ("check_freshness", "fresh", ("expand", "sqlite", "embed_query", "vector_query")),
])
def test_the_expand_and_freshness_steps_are_each_charged_to_their_own_bucket(conn, repo, asked, monkeypatch, step, bucket, others):
    embedder, store, _, questions = asked
    real = getattr(context_module, step)

    def slow(*args, **kwargs):
        time.sleep(0.03)
        return real(*args, **kwargs)
    monkeypatch.setattr(context_module, step, slow)
    t = run_repo(conn, 1, "tiny", repo, embedder, store, questions[:1], index=False)["questions"][0]["timings_ms"]
    assert t[bucket] >= 25
    assert all(t[name] < 25 for name in others)


def test_the_index_the_runner_builds_carries_the_real_model_identity_not_the_wrappers(conn, repo):
    embedder, store = LearnsItsDigestAtWarmup(), InMemoryVectorStore()
    run_repo(conn, 1, "tiny", repo, embedder, store, [])
    identity = (embedder.model_name, embedder.dim)
    assert identity[0].endswith("@abc123")
    assert store.signature() == identity
    assert chunk_store.models_in_use(conn, 1) == {identity}


def test_a_run_where_results_were_left_out_of_the_context_can_still_be_saved(conn, repo, asked, tmp_path):
    embedder, store, _, questions = asked
    r = run_repo(conn, 1, "tiny", repo, embedder, store, questions, k=10, max_tokens=1)     # only the best passage fits: the rest are left out
    q1 = r["questions"][0]
    assert isinstance(q1["dropped"], int) and q1["dropped"] > 0 and q1["dropped"] + q1["passages"] >= 2
    assert q1["dropped_paths"] and all(isinstance(x, str) for x in q1["dropped_paths"]) and len(q1["dropped_paths"]) == q1["dropped"]
    write_results(r, tmp_path / "out.json")                                                  # used to crash: the passages themselves were stored
    assert json.loads((tmp_path / "out.json").read_text())["questions"][0]["dropped"] == q1["dropped"]


def test_the_results_file_says_which_query_wording_was_used(conn, repo):
    from embedding.query_variants import variant_embedder
    plain = go(conn, repo, [])
    assert plain["meta"]["query_variant"] is None
    store = InMemoryVectorStore()
    run_repo(conn, 1, "tiny", repo, FakeEmbedder(), store, [])
    raw = run_repo(conn, 1, "tiny", repo, variant_embedder(FakeEmbedder(), "raw"), store, [], index=False)
    assert raw["meta"]["query_variant"] == "raw"


# ---- the pool (7.6): more than ten hits saved, with what an offline replay needs

def many_functions(tmp_path, n=15, big=False):
    root = tmp_path / "pool"
    root.mkdir()
    for i in range(n):
        (root / f"m{i:02d}.py").write_text(f"def fn_{i}(x):\n    return x + {i}\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_m.py").write_text("def test_fn_3():\n    assert fn_3(1) == 4\n")
    if big:
        (root / "big.py").write_text("def huge():\n" + "".join(f"    value_{i} = {i}\n" for i in range(240)) + "    return value_0\n")
    return root


def pool_run(conn, root, keep, question_text_of=("m03.py", "fn_3"), **kw):
    embedder, store = FakeEmbedder(), InMemoryVectorStore()
    run_repo(conn, 1, "tiny", root, embedder, store, [])
    question = make_question("q01", embed_text_of(conn, *question_text_of), [{"path": question_text_of[0], "symbol": question_text_of[1]}])
    return run_repo(conn, 1, "tiny", root, embedder, store, [question], index=False, keep=keep, **kw)["questions"][0]


def test_without_keep_there_is_no_pool_and_with_keep_the_pool_has_that_many_hits(conn, tmp_path):
    root = many_functions(tmp_path)
    assert "pool" not in pool_run(conn, root, None)
    q = pool_run(conn, root, 14)
    assert len(q["pool"]) == 14


def test_the_first_ten_of_the_pool_are_the_top_ten_in_the_same_order_with_the_same_scores(conn, tmp_path):
    q = pool_run(conn, many_functions(tmp_path), 16)
    assert [(h["id"], h["score"]) for h in q["pool"][:10]] == [(h["id"], h["score"]) for h in q["top10"]]
    assert [h["score"] for h in q["pool"]] == sorted((h["score"] for h in q["pool"]), reverse=True)


def test_a_keep_below_ten_is_refused(conn, tmp_path):
    with pytest.raises(ValueError, match="at least"):
        pool_run(conn, many_functions(tmp_path), 9)


def test_every_pool_hit_carries_its_tags_and_the_cost_of_the_passage_it_would_make(conn, tmp_path):
    from search.context import render_passage
    from chunker.core import estimate_tokens
    from search.stitch import expand
    root = many_functions(tmp_path)
    q = pool_run(conn, root, 14)
    by_path = {h["path"]: h for h in q["pool"]}
    assert by_path["tests/test_m.py"]["is_test"] is True and by_path["m03.py"]["is_test"] is False
    assert all(isinstance(h["cost"], int) and h["cost"] > 0 and h["passage_key"] for h in q["pool"])
    first = chunk_store.chunks_for_file(conn, 1, "m03.py")[0]
    from search.core import Hit
    passage = expand(conn, 1, root, [Hit(first, 1.0)])[0]
    assert by_path["m03.py"]["cost"] == estimate_tokens(render_passage(passage))


def test_two_hits_on_parts_of_one_split_chunk_share_a_passage_key_and_a_cost(conn, tmp_path):
    root = many_functions(tmp_path, n=3, big=True)
    q = pool_run(conn, root, 14, question_text_of=("big.py", "huge"))
    parts = [h for h in q["pool"] if h["path"] == "big.py"]
    assert len(parts) >= 2 and all(h["part"] is not None for h in parts)
    assert len({h["passage_key"] for h in parts}) == 1 and len({h["cost"] for h in parts}) == 1


def test_the_pool_is_saved_in_the_results_file_and_meta_records_keep(conn, tmp_path):
    root = many_functions(tmp_path)
    embedder, store = FakeEmbedder(), InMemoryVectorStore()
    r = run_repo(conn, 1, "tiny", root, embedder, store, [make_question("q01", "anything", [{"path": "m03.py", "symbol": "fn_3"}])], keep=12)
    assert r["meta"]["keep"] == 12 and run_repo(conn, 1, "tiny", root, embedder, store, [], index=False)["meta"]["keep"] is None
    write_results(r, tmp_path / "o.json")
    assert len(json.loads((tmp_path / "o.json").read_text())["questions"][0]["pool"]) == 12


def test_the_pool_does_not_change_the_timings_or_the_context_of_the_normal_run(conn, tmp_path):
    root = many_functions(tmp_path)
    plain, pooled = pool_run(conn, root, None), pool_run(conn, root, 14)
    assert (plain["rank"], plain["found_in_context"], plain["context_tokens"], plain["passages"]) == (pooled["rank"], pooled["found_in_context"], pooled["context_tokens"], pooled["passages"])
    assert set(pooled["timings_ms"]) == set(plain["timings_ms"])


def test_two_different_functions_of_one_file_have_different_passage_keys(conn, tmp_path):
    root = many_functions(tmp_path, n=2)
    (root / "two.py").write_text("def fn_a(x):\n    return x\n\n\ndef fn_b(x):\n    return x * 2\n")
    q = pool_run(conn, root, 14, question_text_of=("two.py", "fn_a"))
    keys = {h["symbol"]: h["passage_key"] for h in q["pool"] if h["path"] == "two.py"}
    assert {"fn_a", "fn_b"} <= set(keys) and keys["fn_a"] != keys["fn_b"]


# ---- the demotion policy (7.6): passed through, recorded, and never applied to the replay pool

def test_the_run_records_the_policy_and_whether_each_question_was_really_demoted(conn, repo):
    from search import DemotionPolicy
    embedder, store = FakeEmbedder(), InMemoryVectorStore()
    run_repo(conn, 1, "tiny", repo, embedder, store, [])
    question = make_question("q01", embed_text_of(conn, "net.py", "retry_request"), [{"path": "net.py", "symbol": "retry_request"}])
    applied = DemotionPolicy(0.06, calibrated_for=embedder.model_name)
    r = run_repo(conn, 1, "tiny", repo, embedder, store, [question], index=False, test_policy=applied)
    assert r["meta"]["test_policy"] == {"margin": 0.06, "changelog_margin": None, "calibrated_for": embedder.model_name}
    assert r["questions"][0]["ranking"] == "demoted" and r["questions"][0]["ranking_note"] is None
    off = run_repo(conn, 1, "tiny", repo, embedder, store, [question], index=False, test_policy=None)
    assert off["meta"]["test_policy"] is None and off["questions"][0]["ranking"] == "raw" and off["questions"][0]["ranking_note"] is None


def test_the_default_policy_with_a_model_it_was_not_measured_on_is_recorded_as_not_applied(conn, repo, asked):
    embedder, store, _, questions = asked
    r = run_repo(conn, 1, "tiny", repo, embedder, store, questions[:1], index=False)
    q = r["questions"][0]
    assert r["meta"]["test_policy"]["calibrated_for"].startswith("qwen3-embedding")
    assert q["ranking"] == "raw" and "not demoted" in q["ranking_note"], "a run must show when the policy did NOT apply, or its numbers would be misread"


def test_the_policy_reaches_build_context_and_the_replay_pool_is_always_raw(conn, repo, asked, monkeypatch):
    from search import DemotionPolicy
    embedder, store, _, questions = asked
    seen = {"context": [], "pool": []}
    real_context, real_search = runner.build_context, runner.run_search
    monkeypatch.setattr(runner, "build_context", lambda *a, **kw: (seen["context"].append(kw.get("test_policy", "missing")), real_context(*a, **kw))[1])
    monkeypatch.setattr(runner, "run_search", lambda *a, **kw: (seen["pool"].append(kw.get("test_policy", "missing")), real_search(*a, **kw))[1])
    given = DemotionPolicy(0.06, calibrated_for=embedder.model_name)
    run_repo(conn, 1, "tiny", repo, embedder, store, questions[:1], index=False, test_policy=given, keep=12)
    assert seen["context"] == [given], "the question is answered with the policy"
    assert seen["pool"] == [None], "the pool is saved in RAW order: a replay applies its own policy on top, never twice"
