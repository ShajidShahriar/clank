"""Replay ranking policies OFFLINE on a saved pool (task I-7.6, step 1). No embedding, no database, no pipeline code involved.

A policy only reorders what was stored, so the saved pool must be wider than the top 10 (`run_eval.py --keep 30`). Policies:
  none | hide | hide_gated | multiply:F | demote:D | demote_ungated:D
`demote` is the real `search.policy.apply_test_policy` (so what is measured is what would ship); the others exist to be compared with it.

Rank is taken in the first ten of the reordered list. "Found in context" is an ESTIMATE with the real budget rule (distinct passages in order, a prefix
that fits, the best one always kept), using the passage cost saved with each hit. `estimate_matches_real` checks the estimate against the real recorded
answers of the run it came from: do not trust the estimate where it disagrees.
"""
import math

from search.core import Hit
from search.policy import apply_test_policy, intent_of

from .score import matches, rank_of

POLICY_HELP = "none | hide | hide_gated | multiply:F | demote:D | demote_ungated:D"


def _number(spec: str, text: str, low_open: bool = False, high: float | None = None) -> float:
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"policy {spec!r}: {text!r} is not a number") from None
    if not math.isfinite(value) or value < 0 or (low_open and value == 0) or (high is not None and value > high):
        raise ValueError(f"policy {spec!r}: {text!r} is out of range")
    return value


def make_policy(spec: str):
    """A function `(entries, question) -> entries reordered (or filtered)`. The entries are never changed and keep their raw scores."""
    name, _, arg = spec.partition(":")
    flagged = lambda entry: entry["is_test"] or entry["is_changelog"]
    if name == "none" and not arg:
        return lambda entries, question: list(entries)
    if name == "hide" and not arg:
        return lambda entries, question: [x for x in entries if not flagged(x)]
    if name == "hide_gated" and not arg:
        def hide_gated(entries, question):
            intent = intent_of(question)
            return [x for x in entries if not ((x["is_test"] and not intent.test) or (x["is_changelog"] and not intent.changelog))]
        return hide_gated
    if name == "multiply" and arg:
        factor = _number(spec, arg, low_open=True, high=1.0)
        def multiply(entries, question):
            scored = [(x["score"] * factor if flagged(x) else x["score"], i, x) for i, x in enumerate(entries)]
            return [x for _, _, x in sorted(scored, key=lambda t: (-round(t[0], 9), t[1]))]
        return multiply
    if name in ("demote", "demote_ungated") and arg:
        margin = _number(spec, arg)
        def demote(entries, question):
            hits = [Hit({"rel_path": x["path"], "i": i}, x["score"]) for i, x in enumerate(entries)]
            tags = {x["path"]: (x["is_test"], x["is_changelog"]) for x in entries}
            ordered = apply_test_policy(hits, question if name == "demote" else "no intent here", tags, margin=margin)
            return [entries[a.hit.chunk["i"]] for a in ordered]
        return demote
    raise ValueError(f"unknown policy {spec!r}; choose from {POLICY_HELP}")


def _as_row(entry: dict) -> dict:
    return {"rel_path": entry["path"], "symbol": entry["symbol"], "parent": entry["parent"], "names": entry["names"]}


BUDGET_RULES = ("prefix", "squeeze")


def simulate_context(entries: list[dict], expect: list[dict], max_tokens: int, k: int = 10, rule: str = "prefix") -> bool:
    """Would the expected answer be in the context text? Passages are the distinct `passage_key`s of the first k entries in order; the first one is
    always kept, each next one only while the total fits. `prefix` (the real rule): the first one that does not fit ends the list. `squeeze` (an
    alternative, only for comparison): a passage that does not fit is skipped and the next ones are tried."""
    if rule not in BUDGET_RULES:
        raise ValueError(f"unknown budget rule {rule!r}; choose from {', '.join(BUDGET_RULES)}")
    groups: dict[str, list[dict]] = {}
    for entry in entries[:k]:
        groups.setdefault(entry["passage_key"], []).append(entry)
    used = 0
    for position, members in enumerate(groups.values()):
        cost = members[0]["cost"]
        if position > 0 and used + cost > max_tokens:
            if rule == "prefix":
                break
            continue
        used += cost
        if any(matches(want, _as_row(m)) for want in expect for m in members):
            return True
    return False


def replay_run(run: dict, spec: str, k: int = 10, max_tokens: int | None = None, rule: str = "prefix") -> dict:
    """The run as it would have come out under policy `spec` (and optionally another token budget and budget rule): new ranks, flags and
    found-in-context. The input is not changed."""
    if rule not in BUDGET_RULES:
        raise ValueError(f"unknown budget rule {rule!r}; choose from {', '.join(BUDGET_RULES)}")
    policy = make_policy(spec)
    max_tokens = run["meta"]["max_tokens"] if max_tokens is None else max_tokens
    out = {"meta": {**run["meta"], "policy": spec, "max_tokens": max_tokens, "rule": rule}, "questions": []}
    for q in run["questions"]:
        ordered = policy(q["pool"], q["question"])
        rank = rank_of(q["expect"], [_as_row(x) for x in ordered[:k]]) if q["expect"] else None
        out["questions"].append({
            **{key: q[key] for key in ("id", "kind", "split", "question", "expect")},
            "pool": ordered, "rank": rank, "top1": rank is not None and rank <= 1, "top3": rank is not None and rank <= 3,
            "top10_hit": rank is not None and rank <= 10,
            "found_in_context": simulate_context(ordered, q["expect"], max_tokens, k, rule) if q["expect"] else False,
        })
    return out


def estimate_matches_real(run: dict) -> dict:
    """{question id: does the estimate (no policy) agree with the REAL found_in_context the run recorded?} for the counted questions."""
    estimate = {q["id"]: q["found_in_context"] for q in replay_run(run, "none")["questions"]}
    return {q["id"]: estimate[q["id"]] == q["found_in_context"] for q in run["questions"] if q["expect"]}
