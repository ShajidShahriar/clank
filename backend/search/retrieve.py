"""retrieve: the whole search path in its decided order: search (rank) -> expand (stitch the parts) -> fit to the token budget."""
from dataclasses import dataclass, field

from .budget import default_cost, fit_to_budget
from .core import search
from .stitch import expand


@dataclass
class Retrieval:
    passages: list                                  # what to give the LLM, best first
    dropped: list = field(default_factory=list)     # passages that did not fit the budget ("N more results not shown")
    tokens_used: int = 0
    over_budget: bool = False                       # the best passage alone is bigger than the budget
    hidden_files: dict = field(default_factory=dict)   # {rel_path: reason}: files that could not be indexed, whose chunks were left out


def retrieve(conn, project_id, repo_path, embedder, store, question, k, max_tokens, cost=default_cost) -> Retrieval:
    found = search(conn, project_id, embedder, store, question, k=k)          # refuses a stale index, hides flagged files
    passages = expand(conn, project_id, repo_path, found.hits)                # a hit on one part becomes the whole chunk
    fitted = fit_to_budget(passages, max_tokens, cost)                        # counted AFTER expansion
    return Retrieval(fitted.passages, fitted.dropped, fitted.tokens_used, fitted.over_budget, found.hidden_files)
