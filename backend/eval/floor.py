"""The relevance-floor analysis on saved runs (task I-7.8). Offline: no embedding, no pipeline code.

The rule was written BEFORE any number was seen (devlog 74):
  W = the lowest raw score of an answer that is in the top 3;  N = the highest raw score any hit of a negative question gets.
  If W - N is at least 0.05 an absolute floor = (N + W) / 2 rounded DOWN to 0.01 is allowed, and adopted only if it removes no answer that is in the
  top 10 on any question (that includes every answer in the real context). Otherwise no absolute floor. A relative margin (best score minus a margin)
  can only trim the tail: the best hit always survives, so it can never silence an off-topic question. "No floor" is a legitimate result.
Scores are the RAW cosine (the demotion never changes them). The holdout is left out unless asked for.
"""
import argparse
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

from .score import matches

CLEAR_GAP = 0.05
FLOOR_GRID = [0.40, 0.45, 0.50, 0.55, 0.58, 0.60, 0.62, 0.64, 0.66, 0.68, 0.70]
MARGIN_GRID = [0.02, 0.04, 0.06, 0.08, 0.10, 0.15, 0.20]


def _row(hit: dict) -> dict:
    return {"rel_path": hit["path"], "symbol": hit["symbol"], "parent": hit["parent"], "names": hit["names"]}


def _best_answer_score(question: dict):
    """The best raw score among the top-10 hits that match the expected answer, or None."""
    scores = [h["score"] for h in question["top10"] if any(matches(want, _row(h)) for want in question["expect"])]
    return max(scores) if scores else None


def _wanted(runs, include_holdout):
    models = {run["meta"].get("model") for run in runs}
    if len(models) > 1:
        raise ValueError(f"the runs used different models ({', '.join(sorted(map(str, models)))}): raw scores are not comparable")
    for run in runs:
        for q in run["questions"]:
            if include_holdout or q["split"] == "tune":
                yield run["meta"].get("repo", "?"), q


@dataclass
class Stats:
    W: float
    N: float
    A10: float
    misses: list = field(default_factory=list)          # counted questions whose answer is not in the top 10
    per_repo: dict = field(default_factory=dict)        # {repo: {"W": .., "N": ..}}
    answers: dict = field(default_factory=dict)         # {question id: best raw score of its answer}
    negatives: dict = field(default_factory=dict)       # {question id: highest raw score of any hit}

    @property
    def gap(self) -> float:
        return self.W - self.N

    @property
    def clear_gap(self) -> bool:
        return round(self.gap, 9) >= CLEAR_GAP


def floor_stats(runs: list[dict], include_holdout: bool = False) -> Stats:
    top3, answers, negatives, misses, per_repo = [], {}, {}, [], {}
    for repo, q in _wanted(runs, include_holdout):
        mine = per_repo.setdefault(repo, {"W": None, "N": None})
        if not q["expect"]:
            if q["top10"]:
                best = max(h["score"] for h in q["top10"])
                negatives[q["id"]] = best
                mine["N"] = best if mine["N"] is None else max(mine["N"], best)
            continue
        best = _best_answer_score(q)
        if best is None:
            misses.append(q["id"])
            continue
        answers[q["id"]] = best
        if q["rank"] is not None and q["rank"] <= 3:
            top3.append(best)
            mine["W"] = best if mine["W"] is None else min(mine["W"], best)
    if not negatives:
        raise ValueError("there is no negative question with hits in these runs: N cannot be measured")
    if not top3:
        raise ValueError("there is no answer in the top 3 in these runs: W cannot be measured")
    return Stats(W=min(top3), N=max(negatives.values()), A10=min(answers.values()), misses=misses, per_repo=per_repo, answers=answers, negatives=negatives)


@dataclass
class Simulation:
    answers_lost: list = field(default_factory=list)    # answers in the top 10 that the floor would remove
    context_lost: list = field(default_factory=list)    # of those, the ones that were in the real context
    silenced: list = field(default_factory=list)        # negatives left with no hit at all
    not_silenced: list = field(default_factory=list)
    mean_hits_kept: float = 0.0                         # over the counted questions, of the top 10


def simulate(runs: list[dict], min_score: float | None = None, margin: float | None = None, include_holdout: bool = False) -> Simulation:
    """What a floor (absolute `min_score`, relative `margin` below the best raw score of the question, or both) would do to the saved top-10 lists."""
    sim, kept_counts = Simulation(), []
    for _, q in _wanted(runs, include_holdout):
        if not q["top10"]:
            continue
        scores = [h["score"] for h in q["top10"]]
        threshold = max(-math.inf if min_score is None else min_score, -math.inf if margin is None else max(scores) - margin)
        keeps = lambda s: round(s, 9) >= round(threshold, 9)
        kept = [s for s in scores if keeps(s)]
        if not q["expect"]:
            (sim.not_silenced if kept else sim.silenced).append(q["id"])
            continue
        kept_counts.append(len(kept))
        best = _best_answer_score(q)
        if best is not None and not keeps(best):
            sim.answers_lost.append(q["id"])
            if q["found_in_context"]:
                sim.context_lost.append(q["id"])
    sim.mean_hits_kept = sum(kept_counts) / len(kept_counts) if kept_counts else 0.0
    return sim


def propose_floor(stats: Stats, runs: list[dict], include_holdout: bool = False) -> tuple[float | None, str]:
    """Apply the rule. Returns (floor or None, the reason in a sentence)."""
    if not stats.clear_gap:
        return None, (f"no absolute floor: the lowest answer in the top 3 (W = {stats.W:.3f}) is only {stats.gap:.3f} above the best negative "
                      f"(N = {stats.N:.3f}); the rule needs a gap of at least {CLEAR_GAP}")
    floor = math.floor((stats.N + stats.W) / 2 * 100 + 1e-9) / 100
    sim = simulate(runs, min_score=floor, include_holdout=include_holdout)
    if sim.answers_lost:
        return None, (f"not adopted: a floor of {floor:.2f} would remove the answer of {', '.join(sim.answers_lost)} from the top 10 "
                      f"(the lowest answer in the top 10 is {stats.A10:.3f})")
    return floor, f"adopt: a floor of {floor:.2f} sits between N = {stats.N:.3f} and W = {stats.W:.3f} and removes no answer from the top 10"


def render(runs: list[dict], include_holdout: bool = False) -> str:
    s = floor_stats(runs, include_holdout)
    where = "including the holdout" if include_holdout else "tune split only, holdout hidden"
    lines = [f"relevance floor on raw scores ({where})", "",
             f"W = {s.W:.3f}  lowest score of an answer in the top 3",
             f"N = {s.N:.3f}  highest score of any hit of a negative",
             f"gap W - N = {s.gap:.3f}   A10 = {s.A10:.3f}  lowest answer anywhere in the top 10", "", "per repo:"]
    for repo, v in sorted(s.per_repo.items()):
        fmt = lambda x: "-" if x is None else f"{x:.3f}"
        lines.append(f"  {repo:<8} W {fmt(v['W'])}   N {fmt(v['N'])}")
    lines += ["", "best raw score of the answer, per counted question:  " + ", ".join(f"{i} {v:.3f}" for i, v in sorted(s.answers.items()))]
    if s.misses:
        lines.append("answer not in the top 10:  " + ", ".join(sorted(s.misses)))
    lines.append("best raw score of each negative:  " + ", ".join(f"{i} {v:.3f}" for i, v in sorted(s.negatives.items())))
    lines += ["", "absolute floor        answers lost   negatives silenced   hits kept (mean of top 10)"]
    for f in FLOOR_GRID:
        sim = simulate(runs, min_score=f, include_holdout=include_holdout)
        lines.append(f"  floor {f:.2f}        {len(sim.answers_lost):>5}          {len(sim.silenced)} of {len(sim.silenced) + len(sim.not_silenced)}              {sim.mean_hits_kept:.1f}")
    lines += ["", "relative margin       answers lost   negatives silenced   hits kept (mean of top 10)"]
    for m in MARGIN_GRID:
        sim = simulate(runs, margin=m, include_holdout=include_holdout)
        lines.append(f"  margin {m:.2f}       {len(sim.answers_lost):>5}          {len(sim.silenced)} of {len(sim.silenced) + len(sim.not_silenced)}              {sim.mean_hits_kept:.1f}")
    floor, why = propose_floor(s, runs, include_holdout)
    lines += ["", f"rule: {why}"]
    return "\n".join(lines)


def main(argv=None, out=print) -> int:
    parser = argparse.ArgumentParser(prog="floor_report", description="Relevance-floor analysis on saved runs (raw scores).")
    parser.add_argument("--runs", nargs="+", required=True, help="results files, one per repo (the policy-on runs)")
    parser.add_argument("--include-holdout", action="store_true", help="only at the very end of the project")
    args = parser.parse_args(argv)
    try:
        runs = [json.loads(Path(p).read_text()) for p in args.runs]
        out(render(runs, args.include_holdout))
    except ValueError as problem:
        out(f"error: {problem}")
        return 2
    return 0
