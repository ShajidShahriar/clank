"""The eval runner (I-7.3): index a repo, ask every question of that repo, keep everything in ONE json file per run.

The file holds what a later analysis needs (the top 10 of every question with tags and scores, timings, the model identity, the chunker fingerprint,
the chunk cap, the repo commits), so the numbers can be looked at again, or scored with a changed ruler, without embedding anything.

Query time is split into stages by wrapping the embedder and the vector store (they time themselves) and by timing the three steps `build_context`
calls (search, expand, check_freshness) while one question runs. The wrappers are put back even when a question fails.
"""
import json
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import yaml

import chunk_store
import search.context as context_module
from chunker.core import MAX_CHUNK_TOKENS
from embedding.timed import TimedEmbedder
from indexing import index_project
from indexing.fingerprint import chunker_fingerprint
from search import build_context

from .score import QuestionScore, in_context, score_question

KINDS = {"concept", "name", "behavior", "docs", "tests", "negative"}
SPLITS = {"tune", "holdout"}


# ---- the question file

def load_questions(path) -> dict:
    """{'repos': {...}, 'questions': [...]}, or ValueError saying what is wrong with the file."""
    data = yaml.safe_load(Path(path).read_text())
    repos, questions = data.get("repos") or {}, data.get("questions") or []
    seen = set()
    for q in questions:
        where = f"question {q.get('id')!r}"
        if q["id"] in seen:
            raise ValueError(f"{where}: duplicate id")
        seen.add(q["id"])
        if q.get("repo") not in repos:
            raise ValueError(f"{where}: unknown repo {q.get('repo')!r}")
        if q.get("kind") not in KINDS:
            raise ValueError(f"{where}: kind must be one of {sorted(KINDS)}")
        if q.get("split") not in SPLITS:
            raise ValueError(f"{where}: split must be one of {sorted(SPLITS)}")
        if not str(q.get("question") or "").strip():
            raise ValueError(f"{where}: the question text is empty")
        expect = q.get("expect")
        if not isinstance(expect, list):
            raise ValueError(f"{where}: expect must be a list")
        if q["kind"] == "negative" and expect:
            raise ValueError(f"{where}: a negative must have an empty expect")
        if q["kind"] != "negative" and not expect:
            raise ValueError(f"{where}: a counted question needs an answer in expect")
        for entry in expect:
            if not entry.get("path"):
                raise ValueError(f"{where}: every expect entry needs a path")
    return {"repos": repos, "questions": questions}


# ---- timing wrappers

class TimedStore:
    def __init__(self, inner):
        self.inner = inner
        self.query_seconds = 0.0

    def query(self, vector, k):
        start = time.perf_counter()
        try:
            return self.inner.query(vector, k)
        finally:
            self.query_seconds += time.perf_counter() - start

    def __getattr__(self, name):
        return getattr(self.inner, name)


@contextmanager
def _timed_stages():
    """While open, `build_context`'s search, expand and check_freshness add their time to `spent` (seconds); `captured` keeps the SearchResult."""
    spent = {"search": 0.0, "expand": 0.0, "fresh": 0.0}
    captured = {}
    names = {"search": "search", "expand": "expand", "fresh": "check_freshness"}
    originals = {key: getattr(context_module, name) for key, name in names.items()}

    def wrap(key):
        def timed(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = originals[key](*args, **kwargs)
            finally:
                spent[key] += time.perf_counter() - start
            if key == "search":
                captured["found"] = result
            return result
        return timed

    try:
        for key, name in names.items():
            setattr(context_module, name, wrap(key))
        yield spent, captured
    finally:
        for key, name in names.items():
            setattr(context_module, name, originals[key])


# ---- a run

def git_head(path) -> str | None:
    done = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else None


def _file_tags(conn, project_id, paths):
    rows = conn.execute("SELECT rel_path, is_test, is_changelog FROM files WHERE project_id = ?", (project_id,)).fetchall()
    return {r["rel_path"]: (bool(r["is_test"]), bool(r["is_changelog"])) for r in rows if r["rel_path"] in paths}


def _index(conn, project_id, repo_path, embedder: TimedEmbedder, store):
    calls, texts = embedder.document_calls, embedder.document_texts
    start = time.perf_counter()
    report = index_project(conn, project_id, repo_path, embedder, store)
    seconds = time.perf_counter() - start
    record = {
        "seconds": round(seconds, 3), "describe": report.describe(), "stopped": report.stopped is not None,
        "files_seen": report.files_seen, "files_written": report.files_written, "files_unchanged": report.files_unchanged,
        "chunks": len(chunk_store.all_ids(conn, project_id)), "embedded": report.embedded, "skipped": [list(s) for s in report.skipped],
        "embed_calls": embedder.document_calls - calls, "embed_texts": embedder.document_texts - texts,
    }
    return report, record


def _ask(conn, project_id, repo_path, embedder: TimedEmbedder, store: TimedStore, question, k, max_tokens) -> dict:
    embed_before, vector_before = embedder.query_seconds, store.query_seconds
    with _timed_stages() as (spent, captured):
        start = time.perf_counter()
        ctx = build_context(conn, project_id, repo_path, embedder, store, question["question"], k, max_tokens)
        total = time.perf_counter() - start
    found = captured["found"]
    embed, vector = embedder.query_seconds - embed_before, store.query_seconds - vector_before
    parts = {
        "embed_query": embed, "vector_query": vector, "sqlite": spent["search"] - embed - vector,
        "expand": spent["expand"], "fresh": spent["fresh"],
    }
    parts["build"] = total - sum(parts.values())
    timings = {name: round(max(seconds, 0.0) * 1000, 3) for name, seconds in parts.items()}
    timings["total"] = round(total * 1000, 3)

    hits = [h.chunk for h in found.hits]
    tags = _file_tags(conn, project_id, {c["rel_path"] for c in hits})
    top10 = [
        {"id": h.chunk["id"], "score": h.score, "path": h.chunk["rel_path"], "symbol": h.chunk["symbol"], "parent": h.chunk["parent"],
         "names": h.chunk["names"], "kind": h.chunk["kind"], "part": h.chunk["part"],
         "is_test": tags.get(h.chunk["rel_path"], (False, False))[0], "is_changelog": tags.get(h.chunk["rel_path"], (False, False))[1]}
        for h in found.hits[:10]
    ]
    score = score_question(question, hits)
    return {
        "id": question["id"], "kind": question["kind"], "split": question["split"], "question": question["question"], "expect": question["expect"],
        "top10": top10, "rank": score.rank, "top1": score.top1, "top3": score.top3, "top10_hit": score.top10,
        "found_in_context": in_context(question["expect"], ctx.passages) if question["expect"] else False,
        "context_tokens": ctx.tokens_used, "over_budget": ctx.over_budget, "passages": len(ctx.passages), "dropped": len(ctx.dropped),
        "dropped_paths": [p.rel_path for p in ctx.dropped],
        "timings_ms": timings,
    }


def run_repo(conn, project_id, repo_name, repo_path, embedder, store, questions, *, k=10, max_tokens=6000, index=True, reindex=False,
             pinned_rev=None, questions_sha=None) -> dict:
    """Index the repo (unless `index=False`), then ask every question whose `repo` is `repo_name`. Returns the whole run as a dict."""
    timed_embedder, timed_store = TimedEmbedder(embedder), TimedStore(store)
    actual = git_head(repo_path)
    result = {"meta": {
        "repo": repo_name, "repo_path": str(repo_path), "rev_pinned": pinned_rev, "rev_actual": actual, "rev_matches": actual is not None and actual == pinned_rev,
        "model": None, "dim": None, "chunker_fingerprint": chunker_fingerprint(), "chunk_cap_tokens": MAX_CHUNK_TOKENS,
        "k": k, "max_tokens": max_tokens, "questions_sha": questions_sha, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, "questions": []}
    if index:
        report, result["index"] = _index(conn, project_id, repo_path, timed_embedder, timed_store)
        if reindex:
            _, result["index_again"] = _index(conn, project_id, repo_path, timed_embedder, timed_store)
        if report.stopped is not None:
            result["aborted"] = report.describe()
            result["meta"].update(model=embedder.model_name, dim=embedder.dim)
            return result
    else:
        embedder.warmup()
    result["meta"].update(model=embedder.model_name, dim=embedder.dim)
    for question in questions:
        if question["repo"] == repo_name:
            result["questions"].append(_ask(conn, project_id, repo_path, timed_embedder, timed_store, question, k, max_tokens))
    return result


# ---- the file on disk

def write_results(result: dict, path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2))


def rescore(result: dict, questions: list[dict]) -> list[QuestionScore]:
    """Score a saved run against `questions` as they are NOW (nothing is embedded). Only questions that were asked in the run are scored."""
    asked = {q["id"]: q for q in result["questions"]}
    scores = []
    for question in questions:
        saved = asked.get(question["id"])
        if saved is None:
            continue
        hits = [{"rel_path": h["path"], "symbol": h["symbol"], "parent": h["parent"], "names": h["names"]} for h in saved["top10"]]
        scores.append(score_question(question, hits))
    return scores
