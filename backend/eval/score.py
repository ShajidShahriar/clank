"""The ruler for I-7: did the expected answer show up, and at what rank? Pure functions, no Ollama, no database.

A hit is a stored chunk row (a dict). A question's `expect` is ANY-OF a list of {path, symbol} entries. One entry matches a chunk when the path equals the
chunk's `rel_path` AND the symbol is right: `None` means the path alone is enough; otherwise it equals the chunk's `symbol` (a part of a split chunk keeps
the symbol, so a part counts), or is one of a group's `names`, or is `Parent.symbol` for a method. Names are compared whole, never by substring.
Negatives have an empty `expect`: they are scored but never counted in the hit rate (they are for the relevance floor).
"""
from dataclasses import dataclass


def matches(entry: dict, chunk) -> bool:
    """Does one expected {path, symbol} match this chunk (a row dict, or an object with the same attributes, like a Passage)?"""
    get = chunk.get if isinstance(chunk, dict) else lambda key, default=None: getattr(chunk, key, default)
    if entry["path"] != get("rel_path"):
        return False
    wanted = entry.get("symbol")
    if wanted is None:
        return True
    symbol, parent = get("symbol"), get("parent")
    if symbol is not None and (wanted == symbol or (parent and wanted == f"{parent}.{symbol}")):
        return True
    return wanted in (get("names") or [])


def rank_of(expect: list[dict], hits: list) -> int | None:
    """1-based rank of the first hit that matches any expected entry, or None."""
    for rank, hit in enumerate(hits, 1):
        if any(matches(entry, hit) for entry in expect):
            return rank
    return None


def in_context(expect: list[dict], passages: list) -> bool:
    """Is the expected answer among the passages the LLM actually received?"""
    return any(matches(entry, p) for entry in expect for p in passages)


@dataclass
class QuestionScore:
    id: str
    kind: str
    split: str
    counted: bool          # False for a negative (empty `expect`)
    rank: int | None
    top1: bool
    top3: bool
    top10: bool


def score_question(question: dict, hits: list) -> QuestionScore:
    expect = question["expect"]
    counted = bool(expect)
    rank = rank_of(expect, hits) if counted else None
    return QuestionScore(
        id=question["id"], kind=question["kind"], split=question["split"], counted=counted, rank=rank,
        top1=rank is not None and rank <= 1, top3=rank is not None and rank <= 3, top10=rank is not None and rank <= 10,
    )


def _rates(counted: list[QuestionScore]) -> dict:
    n = len(counted)
    rate = lambda flag: sum(getattr(s, flag) for s in counted) / n if n else None
    return {
        "n": n, "top1": rate("top1"), "top3": rate("top3"), "top10": rate("top10"),
        "ranks": {s.id: s.rank for s in counted}, "misses": [s.id for s in counted if s.rank is None],
    }


def summarize(scores: list[QuestionScore], split: str | None = None, by_kind: bool = False) -> dict:
    """Hit rates over the counted questions (negatives left out), optionally for one split and broken down by kind."""
    counted = [s for s in scores if s.counted and (split is None or s.split == split)]
    summary = _rates(counted)
    if by_kind:
        summary["by_kind"] = {kind: _rates([s for s in counted if s.kind == kind]) for kind in sorted({s.kind for s in counted})}
    return summary
