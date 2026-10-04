"""The report on the prefix budget rule (task I-7.9). Offline, from saved pools; `scripts/budget_report.py` only calls `main`.

The rule was written BEFORE any number (devlog 76): the cost of the prefix rule at a budget is the number of tune questions whose answer is in the top 10
but not in the context. The alternative, "squeeze" (skip a passage that does not fit, try the next), can only add passages. It is adopted only if, at a
budget of 4000 or more, it recovers at least 3 more questions than the prefix rule; 2 or fewer is a tie and a tie keeps the current rule; a gain only
at a small budget is not enough. The holdout is left out unless asked for.
"""
import argparse
import json
from pathlib import Path

from .compare import TIE_MARGIN
from .replay import make_policy, replay_run

DEFAULT_BUDGETS = [2000, 4000, 6000]
DEFAULT_POLICIES = ["none", "demote:0.15"]
MIN_BUDGET_THAT_COUNTS = 4000
SWITCH_AT = TIE_MARGIN + 1


def decide(recovered: dict[int, int]) -> tuple[str, str]:
    """`recovered` maps a budget to how many more questions squeeze keeps than the prefix rule. Returns ("squeeze" | "prefix", a sentence)."""
    counting = {b: n for b, n in recovered.items() if b >= MIN_BUDGET_THAT_COUNTS}
    for budget in sorted(counting):
        if counting[budget] >= SWITCH_AT:
            return "squeeze", (f"switch to squeeze: at a budget of {budget} it recovers {counting[budget]} more questions "
                               f"(the rule needs {SWITCH_AT} or more at a budget of {MIN_BUDGET_THAT_COUNTS} or more)")
    best = max(counting.values(), default=0)
    if best > 0:
        return "prefix", (f"keep the prefix rule: the best gain at a budget of {MIN_BUDGET_THAT_COUNTS} or more is {best} "
                          f"(a difference of {TIE_MARGIN} questions or fewer is a tie, and a tie keeps the current rule)")
    return "prefix", f"keep the prefix rule: squeeze recovers nothing at a budget of {MIN_BUDGET_THAT_COUNTS} or more"


def _load(paths) -> list[dict]:
    runs = [json.loads(Path(p).read_text()) for p in paths]
    for path, run in zip(paths, runs):
        if not run["meta"].get("keep") or any("pool" not in q for q in run["questions"]):
            raise ValueError(f"{path} has no saved pool: run the eval with --keep 30 first")
    models = {run["meta"].get("model") for run in runs}
    if len(models) > 1:
        raise ValueError(f"the runs used different models ({', '.join(sorted(map(str, models)))}): not comparable")
    return runs


def _budget(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise ValueError(f"a budget must be a whole number of tokens, got {text!r}") from None
    if value < 1:
        raise ValueError(f"a budget must be at least 1 token, got {value}")
    return value


def main(argv=None, out=print) -> int:
    parser = argparse.ArgumentParser(prog="budget_report", description="Is the right answer ranked but dropped by the prefix budget rule? (saved pools, offline)")
    parser.add_argument("--runs", nargs="+", required=True, help="results files made with --keep 30 (one per repo)")
    parser.add_argument("--budgets", nargs="+", default=[str(b) for b in DEFAULT_BUDGETS], help="token budgets to try")
    parser.add_argument("--policies", nargs="+", default=DEFAULT_POLICIES, help="ranking policies, see replay_policies.py")
    parser.add_argument("--verdict-policy", default="demote:0.15", help="the policy the verdict is based on (the first one if it is not in --policies)")
    parser.add_argument("--include-holdout", action="store_true", help="only at the very end of the project")
    args = parser.parse_args(argv)
    try:
        budgets = [_budget(b) for b in args.budgets]
        for spec in args.policies:
            make_policy(spec)
        runs = _load(args.runs)
        verdict_policy = args.verdict_policy if args.verdict_policy in args.policies else args.policies[0]

        rows, sections, recovered_for_verdict, n = [], [], {}, 0
        for spec in args.policies:
            for budget in budgets:
                prefix = [replay_run(r, spec, max_tokens=budget, rule="prefix") for r in runs]
                squeeze = [replay_run(r, spec, max_tokens=budget, rule="squeeze") for r in runs]
                counted = [(run["meta"].get("repo", "?"), q, s) for run, pr, sq in zip(runs, prefix, squeeze)
                           for q, s in zip(pr["questions"], sq["questions"])
                           if q["expect"] and (q["split"] == "tune" or args.include_holdout)]
                n = len(counted)
                in_top10 = sum(q["top10_hit"] for _, q, _ in counted)
                kept_prefix = sum(q["found_in_context"] for _, q, _ in counted)
                kept_squeeze = sum(s["found_in_context"] for _, _, s in counted)
                dropped = [f"{repo}:{q['id']} (rank {q['rank']})" for repo, q, _ in counted if q["top10_hit"] and not q["found_in_context"]]
                rows.append(f"{spec:<14} {budget:>6}   {in_top10:>10}   {kept_prefix:>7} {kept_squeeze:>8}   {len(dropped):>7}   {kept_squeeze - kept_prefix:>9}")
                sections.append(f"  {spec} @ {budget}: " + (", ".join(dropped) if dropped else "none"))
                if spec == verdict_policy:
                    recovered_for_verdict[budget] = kept_squeeze - kept_prefix
        scope = "including the holdout" if args.include_holdout else "tune split only, holdout hidden"
        out(f"the prefix budget rule on saved pools ({scope}); n={n} counted questions")
        out(f"{'policy':<14} {'budget':>6}   {'in top 10':>10}   {'prefix':>7} {'squeeze':>8}   {'dropped':>7}   {'recovered':>9}   (in context, by rule)")
        for row in rows:
            out(row)
        out("")
        out("ranked but dropped by the prefix rule:")
        for section in sections:
            out(section)
        out("")
        out(f"verdict (policy {verdict_policy}): {decide(recovered_for_verdict)[1]}")
    except ValueError as problem:
        out(f"error: {problem}")
        return 2
    return 0
