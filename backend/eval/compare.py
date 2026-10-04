"""Compare two saved runs, question by question (I-7.5). The user's rule: a difference of 2 questions or fewer is a TIE, and a tie keeps the
current behavior. One question is 5 to 25 points here, so the table matters more than any total. The holdout is left out unless asked for.
"""
import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from .holdout import require_final

TIE_MARGIN = 2
METRICS = {"top1": "top-1", "top3": "top-3", "top10": "top-10", "context": "found in context"}
_FLAG = {"top1": "top1", "top3": "top3", "top10": "top10_hit", "context": "found_in_context"}


@dataclass
class Row:
    repo: str
    id: str
    kind: str
    rank_a: int | None
    rank_b: int | None
    move: str            # better / worse / same (for run b against run a)


@dataclass
class Comparison:
    rows: list[Row] = field(default_factory=list)
    counts: dict = field(default_factory=dict)      # {metric: {"a": n, "b": n}}

    @property
    def n(self) -> int:
        return len(self.rows)

    def _count(self, move: str) -> int:
        return sum(r.move == move for r in self.rows)

    better = property(lambda self: self._count("better"))
    worse = property(lambda self: self._count("worse"))
    same = property(lambda self: self._count("same"))

    def verdict(self, metric: str = "top3") -> str:
        gain = self.counts[metric]["b"] - self.counts[metric]["a"]
        if abs(gain) <= TIE_MARGIN:
            return "tie"
        return "b is better" if gain > 0 else "a is better"


def _move(rank_a, rank_b) -> str:
    if rank_a == rank_b:                  # two misses (None and None) are equal too
        return "same"
    if rank_b is None:
        return "worse"
    if rank_a is None or rank_b < rank_a:
        return "better"
    return "worse"


def compare_runs(pairs, include_holdout: bool = False) -> Comparison:
    """`pairs` is a list of (run_a, run_b), one pair per repo; the rows of all repos are added together."""
    result = Comparison(counts={m: {"a": 0, "b": 0} for m in METRICS})
    for a, b in pairs:
        if a["meta"].get("model") != b["meta"].get("model"):
            raise ValueError(f"the two runs of {a['meta'].get('repo')} used a different model ({a['meta'].get('model')} and {b['meta'].get('model')}): not comparable")
        by_id_b = {q["id"]: q for q in b["questions"]}
        for qa in a["questions"]:
            if not qa["expect"] or (qa["split"] == "holdout" and not include_holdout):
                continue
            qb = by_id_b.get(qa["id"])
            if qb is None:
                raise ValueError(f"question {qa['id']} is in the first run but not in the second")
            result.rows.append(Row(a["meta"].get("repo", "?"), qa["id"], qa["kind"], qa["rank"], qb["rank"], _move(qa["rank"], qb["rank"])))
            for metric, flag in _FLAG.items():
                result.counts[metric]["a"] += bool(qa[flag])
                result.counts[metric]["b"] += bool(qb[flag])
    return result


def render(c: Comparison, name_a: str, name_b: str) -> str:
    named = lambda verdict: verdict.replace("a is better", f"{name_a} is better").replace("b is better", f"{name_b} is better")
    lines = [f"{'repo':<8} {'id':<4} {'kind':<9} {name_a:>8} {name_b:>8}  move"]
    show = lambda rank: "miss" if rank is None else str(rank)
    for r in c.rows:
        lines.append(f"{r.repo:<8} {r.id:<4} {r.kind:<9} {show(r.rank_a):>8} {show(r.rank_b):>8}  {r.move}")
    lines.append("")
    lines.append(f"{c.n} questions: {c.better} better, {c.worse} worse, {c.same} same ({name_b} against {name_a})")
    for metric, label in METRICS.items():
        counts = c.counts[metric]
        lines.append(f"{label:<17} {name_a} {counts['a']:>2}   {name_b} {counts['b']:>2}   {named(c.verdict(metric))}")
    lines.append(f"verdict on top-3 (a difference of {TIE_MARGIN} questions or fewer is a tie, and a tie keeps the current behavior): {named(c.verdict('top3'))}")
    return "\n".join(lines)


def main(argv=None, out=print) -> int:
    parser = argparse.ArgumentParser(prog="compare_runs", description="Compare saved eval runs question by question (b against a).")
    parser.add_argument("--a", nargs="+", required=True, help="results files of the current behavior, one per repo")
    parser.add_argument("--b", nargs="+", required=True, help="results files of the change, in the same repo order")
    parser.add_argument("--names", nargs=2, default=["a", "b"], metavar=("NAME_A", "NAME_B"))
    parser.add_argument("--include-holdout", action="store_true", help="only at the very end of the project")
    args = parser.parse_args(argv)
    if len(args.a) != len(args.b):
        out("error: --a and --b need the same number of files (one per repo, same order)")
        return 2
    try:
        pairs = [(json.loads(Path(a).read_text()), json.loads(Path(b).read_text())) for a, b in zip(args.a, args.b)]
        if args.include_holdout:
            require_final([run for pair in pairs for run in pair])
        comparison = compare_runs(pairs, include_holdout=args.include_holdout)
    except ValueError as problem:
        out(f"error: {problem}")
        return 2
    out(render(comparison, *args.names))
    return 0
