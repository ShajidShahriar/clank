"""The 7.11 gate: should a second embedding model be tried at all? Read from the STORED results; no embedding, no pipeline code (task I-7.11).

The plan's rule: run 7.11 only if found-in-context at 6000 tokens is still below 17 of 20 (85%) AND at least 3 misses are MEANING misses, after every miss
has been labelled by cause. A meaning miss = the answer is not among the 30 nearest chunks at all: the model did not connect the question to it. A miss
that was retrieved but ranked low, or ranked but left out of the context by the budget, is a ranking or budget problem that another model does not fix.
The 20 counts every question with an answer; the holdout is hidden, so the target is scaled to the questions looked at (17 of 20 is 12 of 14, rounded up).
"""
import argparse
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .score import matches

TARGET = 17 / 20
MEANING_MISS = "not retrieved"
MEANING_MISSES_NEEDED = 3


def _row(hit: dict) -> dict:
    return {"rel_path": hit["path"], "symbol": hit["symbol"], "parent": hit["parent"], "names": hit["names"]}


def label_miss(question: dict) -> tuple[str, str]:
    """(cause, detail) for a counted question whose answer is not in the real context."""
    if not question["expect"]:
        raise ValueError(f"{question['id']} has no answer to look for: it cannot miss")
    if question["found_in_context"]:
        raise ValueError(f"{question['id']}: the answer was found in the context, there is no miss to label")
    position = next((i for i, h in enumerate(question["pool"], 1) if any(matches(want, _row(h)) for want in question["expect"])), None)
    if position is None:
        return MEANING_MISS, "the answer is not among the 30 nearest chunks"
    rank = question["rank"]
    detail = f"rank {rank}, pool position {position}"
    if rank is not None and rank <= 3:
        return "ranked top 3, not in context", detail
    if rank is not None and rank <= 10:
        return "ranked 4-10, not in context", detail
    return "ranked 11-30", detail


@dataclass
class Gate:
    n: int
    found: int
    target_count: int
    below_target: bool
    misses: list = field(default_factory=list)     # [(question id, cause, detail)]
    meaning_misses: int = 0
    run: bool = False
    reason: str = ""


def gate_7_11(questions: list[dict], target: float = TARGET, include_holdout: bool = False) -> Gate:
    counted = [q for q in questions if q["expect"] and (q["split"] == "tune" or include_holdout)]
    if not counted:
        raise ValueError("there are no counted questions to read the gate from")
    for q in counted:
        if "pool" not in q:
            raise ValueError(f"{q['id']} has no saved pool: run the eval with --keep 30")
    n, found = len(counted), sum(q["found_in_context"] for q in counted)
    target_count = math.ceil(target * n - 1e-9)
    gate = Gate(n=n, found=found, target_count=target_count, below_target=found < target_count)
    gate.misses = [(q["id"], *label_miss(q)) for q in counted if not q["found_in_context"]]
    gate.meaning_misses = sum(1 for _, cause, _ in gate.misses if cause == MEANING_MISS)
    causes = ", ".join(f"{qid} ({cause})" for qid, cause, _ in gate.misses) or "none"
    if not gate.below_target:
        gate.reason = f"target met: {found} of {n} in the context (the target is {target_count}); the model stays. Misses: {causes}"
    elif gate.meaning_misses < MEANING_MISSES_NEEDED:
        plural = "" if gate.meaning_misses == 1 else "es"
        gate.reason = (f"below the target ({found} of {n}, the target is {target_count}) but only {gate.meaning_misses} meaning miss{plural} "
                       f"(the rule needs {MEANING_MISSES_NEEDED}): the model stays. Causes: {causes}")
    else:
        gate.run = True
        gate.reason = (f"run 7.11: below the target ({found} of {n}, the target is {target_count}) and {gate.meaning_misses} meaning misses "
                       f"(the rule needs {MEANING_MISSES_NEEDED}). Causes: {causes}")
    return gate


def main(argv=None, out=print) -> int:
    parser = argparse.ArgumentParser(prog="gate_7_11", description="Read the 7.11 gate (a second embedding model?) from stored results.")
    parser.add_argument("--runs", nargs="+", required=True, help="results files with the REAL found_in_context and rank, one per repo (policy on)")
    parser.add_argument("--pools", nargs="+", help="results files made with --keep 30, one per repo in the same order, when the runs have no pool")
    parser.add_argument("--include-holdout", action="store_true", help="only at the very end of the project")
    args = parser.parse_args(argv)
    if args.pools and len(args.pools) != len(args.runs):
        out("error: --runs and --pools need the same number of files (one per repo, same order)")
        return 2
    try:
        questions = []
        for index, path in enumerate(args.runs):
            run = json.loads(Path(path).read_text())
            pools = {}
            if args.pools:
                pools = {q["id"]: q["pool"] for q in json.loads(Path(args.pools[index]).read_text())["questions"]}
            for q in run["questions"]:
                merged = dict(q, id=q["id"], repo=run["meta"].get("repo", "?"))
                if q["id"] in pools:
                    merged["pool"] = pools[q["id"]]
                questions.append(merged)
        gate = gate_7_11(questions, include_holdout=args.include_holdout)
    except ValueError as problem:
        out(f"error: {problem}")
        return 2
    scope = "including the holdout" if args.include_holdout else "tune split only, holdout hidden"
    out(f"7.11 gate ({scope}): {gate.found} of {gate.n} counted questions have their answer in the real context; the target is {gate.target_count}")
    for qid, cause, detail in gate.misses:
        out(f"  miss {qid}: {cause} ({detail})")
    out(f"gate: {gate.reason}")
    return 0
