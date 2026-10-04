"""retrieve: the whole search path in its decided order: search (rank) -> expand (stitch the parts) -> fit to the token budget."""
from dataclasses import dataclass, field

from .budget import ceiling_tokens, default_cost, fit_to_budget, narrow_target_tokens
from .core import search
from .policy import DEFAULT_DEMOTION
from .fresh import check_freshness
from .stitch import expand


@dataclass
class Retrieval:
    passages: list                                  # what to give the LLM, best first
    dropped: list = field(default_factory=list)     # passages that did not fit the budget ("N more results not shown")
    tokens_used: int = 0
    over_budget: bool = False                       # the best passage alone is bigger than the budget
    hidden_files: dict = field(default_factory=dict)   # {rel_path: reason}: files that could not be indexed, whose chunks were left out
    stale_files: list = field(default_factory=list)    # shown, but changed since they were indexed (their passages are marked STALE)
    deleted_files: list = field(default_factory=list)  # no longer exist: their passages were dropped
    ranking_note: str | None = None                     # why a requested demotion of tests and changelogs was not applied (for the caller, not the LLM)


def retrieve(conn, project_id, repo_path, embedder, store, question, k, max_tokens, cost=default_cost, test_policy=DEFAULT_DEMOTION) -> Retrieval:
    found = search(conn, project_id, embedder, store, question, k=k, test_policy=test_policy)   # refuses a stale index, hides flagged files, demotes tests
    passages = expand(conn, project_id, repo_path, found.hits, ceiling_tokens(max_tokens), narrow_target_tokens(max_tokens))   # a hit on one part becomes the whole chunk (or the part
    passages, stale, deleted = check_freshness(conn, project_id, repo_path, passages)        # around the hit if the chunk is huge); is it still true?
    fitted = fit_to_budget(passages, max_tokens, cost)                        # counted AFTER expansion, and after deleted files are dropped
    return Retrieval(fitted.passages, fitted.dropped, fitted.tokens_used, fitted.over_budget, found.hidden_files, stale, deleted, found.ranking_note)
