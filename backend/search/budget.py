"""The token budget: how much of the retrieved context fits in what the LLM can be given.

Order of events (decided in the plan): RANK first, EXPAND second, count tokens AFTER expansion. A hit on one part of a split function costs the
whole function, because that is what the LLM will be given; counting the hit would let a 1,800-token passage through a 600-token budget.

Two rules, both deliberate:
- The result is always a PREFIX of the ranking. When a passage does not fit, nothing after it is squeezed in around it: the LLM never sees a
  lower-ranked passage while a higher-ranked one is missing.
- If the BEST passage alone is bigger than the whole budget it is still returned, alone, flagged `over_budget`. Dropping the best answer is the
  worst outcome, and chunks are capped at 800 tokens, so this only happens for a very long split chunk. The caller decides what to do with the flag.
Passages are never cut: they are in or out.
"""
from dataclasses import dataclass, field

from chunker.core import estimate_tokens

# What the context text (task 6.4) adds around each passage: a file, symbol and line-range header. 6.4 passes its own cost function that
# counts the real formatted block; this is the default until then.
PASSAGE_OVERHEAD_TOKENS = 24

# "The best passage is never dropped" needs a ceiling, or one huge function could overflow the LLM's window. No passage is bigger than
# 2x the budget, less this allowance for the notes that go with it; a bigger one is narrowed (see stitch.py). One chunk is at most 800 tokens, so
# a single part always fits unless the budget itself is tiny (then that one part is returned, flagged over_budget).
NOTES_ALLOWANCE_TOKENS = 250


def ceiling_tokens(max_tokens: int) -> int:
    """A passage bigger than this is narrowed."""
    return max(1, 2 * max_tokens - NOTES_ALLOWANCE_TOKENS)


def narrow_target_tokens(max_tokens: int) -> int:
    """What a narrowed passage aims for: back inside the budget (the ceiling only decides WHEN to narrow). The best hit part is always kept, even if
    that one part alone is bigger than this."""
    return max(1, max_tokens - NOTES_ALLOWANCE_TOKENS)


@dataclass
class BudgetResult:
    passages: list = field(default_factory=list)   # the ones that fit, in rank order
    dropped: list = field(default_factory=list)    # everything from the first one that did not fit, in rank order
    tokens_used: int = 0
    over_budget: bool = False                      # the best passage alone is bigger than the budget (and is returned anyway)


def default_cost(passage) -> int:
    return estimate_tokens(passage.text) + PASSAGE_OVERHEAD_TOKENS


def fit_to_budget(passages, max_tokens, cost=default_cost) -> BudgetResult:
    """Keep passages from the top of the ranking while they fit in `max_tokens`."""
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
        raise ValueError(f"max_tokens must be a positive integer, got {max_tokens!r}")
    result = BudgetResult()
    for index, passage in enumerate(passages):
        needed = cost(passage)
        if result.tokens_used + needed <= max_tokens:
            result.passages.append(passage)
            result.tokens_used += needed
        elif index == 0:
            result.passages.append(passage)           # the best answer is never dropped
            result.tokens_used = needed
            result.over_budget = True
            result.dropped = list(passages[1:])
            return result
        else:
            result.dropped = list(passages[index:])   # a prefix: nothing after the first misfit is considered
            return result
    return result
