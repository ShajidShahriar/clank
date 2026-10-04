"""The command line around the eval runner. `scripts/run_eval.py` only calls `main`.

    CLANK_DATA_DIR=~/.clank-eval uv run python scripts/run_eval.py --repo flask --path /path/to/flask

Three protections, each on purpose:
- It refuses to run without an explicit CLANK_DATA_DIR, so eval projects never land in the user's real data folder.
- It refuses a repo that is not at the commit pinned in the question file (except Clank itself, which moves with every commit: a warning).
- It does not print holdout questions unless asked (`--include-holdout`): the holdout is looked at once, at the very end. The file still saves them.
"""
import argparse
import hashlib
import os
import time
from pathlib import Path

import db
import datadir
from embedding import OllamaEmbedder
from embedding.query_variants import QUERY_VARIANTS, variant_embedder
from search import DEFAULT_DEMOTION
from vectorstore import open_project_store

from .runner import git_head, load_questions, run_repo, write_results
from .score import score_question, summarize

HERE = Path(__file__).resolve().parent
CLANK_ROOT = HERE.parent.parent
DEFAULT_QUESTIONS = HERE / "questions.yaml"
RESULTS_DIR = HERE / "results"


class Refusal(Exception):
    pass


def _parser():
    p = argparse.ArgumentParser(prog="run_eval", description="Index a repo and ask the eval questions of that repo.")
    p.add_argument("--repo", required=True)
    p.add_argument("--path", help="the repo's folder (default for clank: this repo)")
    p.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    p.add_argument("--out", help="results file (default: eval/results/<repo>-<time>.json)")
    p.add_argument("--k", type=int, default=10)
    p.add_argument("--max-tokens", type=int, default=6000)
    p.add_argument("--index-only", action="store_true", help="index, ask nothing (run the questions in another sitting)")
    p.add_argument("--no-index", action="store_true", help="ask without indexing (reuse the index that is there)")
    p.add_argument("--reindex", action="store_true", help="index a second time and time it (nothing has changed)")
    p.add_argument("--allow-rev-mismatch", action="store_true")
    p.add_argument("--include-holdout", action="store_true")
    p.add_argument("--only", help="comma-separated question ids")
    p.add_argument("--test-policy", choices=["default", "off"], default="default", help="demote tests and changelogs in the ranking: `default` is the product behavior, `off` is the plain raw order")
    p.add_argument("--keep", type=int, help="also save a pool of this many hits per question (at least 10), for offline ranking experiments")
    p.add_argument("--query-variant", choices=sorted(QUERY_VARIANTS), help="word the question differently (7.5); `instruct` is the control and must reproduce the baseline")
    return p


def _project_id(conn, repo, path) -> int:
    name = f"eval-{repo}"
    row = conn.execute("SELECT id FROM projects WHERE name = ?", (name,)).fetchone()
    if row is None:
        cursor = conn.execute("INSERT INTO projects (name, repo_path, created_at) VALUES (?, ?, ?)", (name, str(path), time.strftime("%Y-%m-%dT%H:%M:%S")))
        conn.commit()
        return cursor.lastrowid
    conn.execute("UPDATE projects SET repo_path = ? WHERE id = ?", (str(path), row["id"]))
    conn.commit()
    return row["id"]


def _percent(value) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def _report(result, scores, include_holdout, say):
    if "index" in result:
        i = result["index"]
        say(f"index: {i['seconds']} s, {i['files_seen']} files, {i['chunks']} chunks, {i['embedded']} embedded in {i['embed_calls']} calls ({i['describe']})")
    if "index_again" in result:
        i = result["index_again"]
        say(f"index again, nothing changed: {i['seconds']} s, {i['embedded']} embedded in {i['embed_calls']} calls")
    by_id = {q["id"]: q for q in result["questions"]}
    for s in scores:
        if s.split == "holdout" and not include_holdout:
            continue
        q = by_id[s.id]
        if s.counted:
            say(f"{s.id} {s.kind:<8} rank {s.rank if s.rank else 'miss':<4} in-context {'yes' if q['found_in_context'] else 'no':<3} {q['timings_ms']['total']:.0f} ms")
        else:
            best = q["top10"][0]["score"] if q["top10"] else None
            say(f"{s.id} negative best score {best if best is None else round(best, 3)}")
    for split in ("tune", "holdout"):
        if split == "holdout" and not include_holdout:
            hidden = sum(1 for s in scores if s.split == "holdout" and s.counted)
            if hidden:
                say(f"holdout: {hidden} questions, hidden (use --include-holdout at the very end)")
            continue
        sm = summarize(scores, split=split)
        if sm["n"]:
            found = sum(by_id[s.id]["found_in_context"] for s in scores if s.counted and s.split == split) / sm["n"]
            say(f"{split}: {sm['n']} questions  top-1 {_percent(sm['top1'])}  top-3 {_percent(sm['top3'])}  top-10 {_percent(sm['top10'])}  found in context {_percent(found)}")


def _run(args, embedder, store_factory, say) -> int:
    if not os.environ.get("CLANK_DATA_DIR"):
        raise Refusal("set CLANK_DATA_DIR to a folder just for the eval (for example ~/.clank-eval): it keeps eval projects out of your real data")
    if args.index_only and args.no_index:
        raise Refusal("--index-only and --no-index together would do nothing")
    meta = load_questions(args.questions)
    if args.repo not in meta["repos"]:
        raise Refusal(f"unknown repo {args.repo!r}; the question file has {sorted(meta['repos'])}")
    if args.path:
        path = Path(args.path).expanduser().resolve()
    elif args.repo == "clank":
        path = CLANK_ROOT
    else:
        raise Refusal(f"--path is needed for {args.repo}")
    if not path.is_dir():
        raise Refusal(f"{path} is not a folder")
    pinned, actual = meta["repos"][args.repo]["rev"], git_head(path)
    if actual != pinned:
        message = f"{args.repo} is at {actual}, not at the pinned commit {pinned}"
        if args.repo != "clank" and not args.allow_rev_mismatch:
            raise Refusal(message + " (results would not be comparable; --allow-rev-mismatch to run anyway)")
        say(f"warning: {message}")

    questions = meta["questions"]
    if args.only:
        wanted = [i.strip() for i in args.only.split(",") if i.strip()]
        unknown = [i for i in wanted if i not in {q["id"] for q in questions}]
        if unknown:
            raise Refusal(f"unknown question ids: {', '.join(unknown)}")
        questions = [q for q in questions if q["id"] in wanted]
    if args.index_only:
        questions = []

    wiped = db.init_db()
    conn = db.get_connection()
    try:
        project_id = _project_id(conn, args.repo, path)
        store = store_factory(project_id) if store_factory else open_project_store(datadir.data_dir(), project_id)
        if wiped:
            store.clear()
        sha = hashlib.sha256(Path(args.questions).read_bytes()).hexdigest()[:16]
        base = embedder if embedder is not None else OllamaEmbedder()
        result = run_repo(conn, project_id, args.repo, path, variant_embedder(base, args.query_variant) if args.query_variant else base, store, questions,
                          k=args.k, max_tokens=args.max_tokens, index=not args.no_index, reindex=args.reindex, pinned_rev=pinned, questions_sha=sha, keep=args.keep,
                          test_policy=DEFAULT_DEMOTION if args.test_policy == "default" else None)
    finally:
        conn.close()
    out = Path(args.out) if args.out else RESULTS_DIR / f"{args.repo}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    write_results(result, out)
    scores = [score_question(next(q for q in questions if q["id"] == r["id"]), [
        {"rel_path": h["path"], "symbol": h["symbol"], "parent": h["parent"], "names": h["names"]} for h in r["top10"]]) for r in result["questions"]]
    _report(result, scores, args.include_holdout, say)
    say(f"saved {out}")
    if "aborted" in result:
        say(f"stopped: {result['aborted']}")
        return 1
    return 0


def main(argv=None, *, embedder=None, store_factory=None, out=print) -> int:
    args = _parser().parse_args(argv)
    try:
        return _run(args, embedder, store_factory, out)
    except (Refusal, ValueError) as problem:
        out(f"error: {problem}")
        return 2
