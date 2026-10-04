"""The report for step 1 of 7.6: every ranking policy side by side against `none`, on saved pools (no embedding, no pipeline code).
`scripts/replay_policies.py` only calls `main`. Built on the tested replay and comparison code; what it adds is the estimate check, the guard for
questions that ask for tests, the hidden holdout and the refusals.
"""
import argparse
import json
from pathlib import Path

from .compare import TIE_MARGIN, compare_runs, render
from .replay import POLICY_HELP, estimate_matches_real, make_policy, replay_run

DEFAULT_POLICIES = ["none", "hide", "hide_gated", "multiply:0.9", "multiply:0.8",
                    "demote:0.02", "demote:0.04", "demote:0.06", "demote:0.08", "demote:0.10", "demote:0.15", "demote_ungated:0.06"]


def _load(paths) -> list[dict]:
    runs = [json.loads(Path(p).read_text()) for p in paths]
    for path, run in zip(paths, runs):
        if not run["meta"].get("keep") or any("pool" not in q for q in run["questions"]):
            raise ValueError(f"{path} has no saved pool: run the eval with --keep 30 first")
    models = {run["meta"].get("model") for run in runs}
    if len(models) > 1:
        raise ValueError(f"the runs used different models ({', '.join(sorted(map(str, models)))}): scores are not comparable")
    return runs


def _verdict(comparison, spec: str) -> str:
    return comparison.verdict("top3").replace("b is better", f"{spec} is better").replace("a is better", "none is better")


def main(argv=None, out=print) -> int:
    parser = argparse.ArgumentParser(prog="replay_policies", description="Replay ranking policies on saved pools, question by question.")
    parser.add_argument("--runs", nargs="+", required=True, help="results files made with --keep 30 (one per repo)")
    parser.add_argument("--policies", nargs="+", default=DEFAULT_POLICIES, help=POLICY_HELP)
    parser.add_argument("--show", help="also print the per-question table of this policy against none")
    parser.add_argument("--include-holdout", action="store_true", help="only at the very end of the project")
    args = parser.parse_args(argv)
    try:
        for spec in args.policies + ([args.show] if args.show else []):
            make_policy(spec)
        runs = _load(args.runs)
        baseline = [replay_run(run, "none") for run in runs]

        agreement = {}
        for run in runs:
            agreement.update({(run["meta"]["repo"], qid): ok for qid, ok in estimate_matches_real(run).items()})
        tune_ids = {(run["meta"]["repo"], q["id"]) for run in runs for q in run["questions"] if q["split"] == "tune"}
        disagree = sorted(f"{repo}:{qid}" for (repo, qid), ok in agreement.items() if not ok and ((repo, qid) in tune_ids or args.include_holdout))
        out(f"estimate agrees with the real run on {sum(agreement.values())} of {len(agreement)} questions"
            + (f"; disagrees on {', '.join(disagree)}" if disagree else ""))

        header = None
        rows, warnings = [], []
        for spec in args.policies:
            replayed = [replay_run(run, spec) for run in runs]
            c = compare_runs(list(zip(baseline, replayed)), include_holdout=args.include_holdout)
            header = c.n
            counts = c.counts
            rows.append(f"{spec:<20} {counts['top1']['b']:>5} {counts['top3']['b']:>5} {counts['top10']['b']:>6} {counts['context']['b']:>7}   "
                        f"{c.better:>6} {c.worse:>5} {c.same:>4}   {_verdict(c, spec)}")
            warnings += [f"HURTS tests question {r.id} ({r.repo}) under {spec}" for r in c.rows if r.kind == "tests" and r.move == "worse"]
        out(f"policies on saved pools, n={header} counted questions ({'including' if args.include_holdout else 'tune split only, holdout hidden'}); "
            f"a difference of {TIE_MARGIN} questions or fewer is a tie")
        out(f"{'policy':<20} {'top-1':>5} {'top-3':>5} {'top-10':>6} {'context':>7}   {'better':>6} {'worse':>5} {'same':>4}   verdict")
        for row in rows:
            out(row)
        for warning in warnings:
            out(warning)
        if args.show:
            shown = compare_runs(list(zip(baseline, [replay_run(run, args.show) for run in runs])), include_holdout=args.include_holdout)
            out("")
            out(render(shown, "none", args.show))
    except ValueError as problem:
        out(f"error: {problem}")
        return 2
    return 0
