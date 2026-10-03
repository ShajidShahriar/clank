"""The context text the LLM receives: one block per passage, then notes about what it is NOT seeing.

A block is a header line (file, line range, symbol, kind) and the code in a fence. The fence is longer than any run of backticks inside the code,
because Markdown chunks contain fences of their own and a fixed ``` would let them break out. The score is not shown (it would only bias the model).

The budget counts EXACTLY what is sent. The real text is built for each candidate (the first m passages and the notes that go with that choice) and the
largest prefix that fits is kept, so the final text never exceeds `max_tokens` unless the best passage alone is bigger than the whole budget (then
it is returned alone, flagged, and a note says so). Still a strict prefix of the ranking, like `fit_to_budget` (which `retrieve` uses when the
caller wants passages, not text).
"""
import re
from dataclasses import dataclass, field

from chunker.core import estimate_tokens
from indexing.tags import classify_file

from .core import search
from .stitch import Passage, expand

NONE_FOUND = "No matching code was found."
MAX_LISTED_FILES = 20
MAX_REASON_CHARS = 100
OVER_BUDGET_NOTE = "The best match is longer than the space available and is shown on its own."


@dataclass
class Context:
    text: str                                       # exactly what to give the LLM
    passages: list                                  # the passages shown, best first
    dropped: list = field(default_factory=list)     # matching passages that did not fit
    hidden_files: dict = field(default_factory=dict)   # {rel_path: reason}
    tokens_used: int = 0                            # estimate_tokens(text)
    over_budget: bool = False


def render_passage(passage: Passage) -> str:
    label = f"{passage.parent}.{passage.symbol}" if passage.parent and passage.symbol else (passage.symbol or passage.kind)
    header = f"### {passage.rel_path}:{passage.start_line}-{passage.end_line} · {label} ({passage.kind})"
    if not passage.complete:
        numbers = ", ".join(str(n + 1) for n in passage.missing)
        header += f" · INCOMPLETE (part{'s' if len(passage.missing) != 1 else ''} {numbers} not available)"
    longest = max((len(run) for run in re.findall(r"`+", passage.text)), default=0)
    fence = "`" * max(3, longest + 1)
    language = classify_file(passage.rel_path).language or ""
    return f"{header}\n{fence}{language}\n{passage.text}\n{fence}"


def _hidden_note(hidden_files: dict) -> str:
    if not hidden_files:
        return ""
    names = sorted(hidden_files)
    listed = [f"`{name}` ({_short(hidden_files[name])})" for name in names[:MAX_LISTED_FILES]]
    more = f", and {len(names) - MAX_LISTED_FILES} more" if len(names) > MAX_LISTED_FILES else ""
    return ("Files that could not be indexed (nothing from them is shown above, and they may matter): " + ", ".join(listed) + more + ".")


def _short(reason: str) -> str:
    first = reason.strip().split("\n")[0]
    return first if len(first) <= MAX_REASON_CHARS else first[:MAX_REASON_CHARS] + "…"


def _notes(hidden_note: str, dropped: int, over_budget: bool) -> str:
    lines = []
    if hidden_note:
        lines.append(hidden_note)
    if dropped:
        lines.append(f"{dropped} more matching result{'s' if dropped != 1 else ''} did not fit and {'were' if dropped != 1 else 'was'} left out.")
    if over_budget:
        lines.append(OVER_BUDGET_NOTE)
    return "[Notes]\n" + "\n".join(f"- {line}" for line in lines) if lines else ""


def build_context(conn, project_id, repo_path, embedder, store, question, k, max_tokens) -> Context:
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens < 1:
        raise ValueError(f"max_tokens must be a positive integer, got {max_tokens!r}")
    found = search(conn, project_id, embedder, store, question, k=k)           # rank (refuses a stale index, hides flagged files)
    passages = expand(conn, project_id, repo_path, found.hits)                 # stitch the parts of split chunks
    hidden_note = _hidden_note(found.hidden_files)
    blocks = [render_passage(p) for p in passages]
    n = len(passages)

    # estimate_tokens(text) = len(text) // 3, so "fits in max_tokens" is exactly "len(text) <= 3 * max_tokens + 2". Build the REAL text for each
    # candidate (the first m passages, then the notes that go with that choice) and keep the largest prefix that fits: no rounding slack.
    limit = 3 * max_tokens + 2

    def assemble(m, say_dropped=True, over_budget=False):
        body = "\n\n".join(blocks[:m]) if m else NONE_FOUND
        notes = _notes(hidden_note, (n - m) if say_dropped else 0, over_budget)
        return body + ("\n\n" + notes if notes else "")

    shown, over, text = 0, False, None
    for m in range(n, -1, -1):
        candidate = assemble(m)
        if m == 0 and n > 0:
            break                                                              # never settle for nothing when there is a best passage
        if len(candidate) <= limit:
            shown, text = m, candidate
            break
    if text is None and n > 0:
        # not even the best passage fits with a "left out" note. Without that note (it is information, not content) it may still fit.
        candidate = assemble(1, say_dropped=False)
        if len(candidate) <= limit:
            shown, text = 1, candidate
        else:
            # the best passage alone is bigger than the whole budget: return it anyway, on its own, flagged
            shown, over, text = 1, True, assemble(1, say_dropped=False, over_budget=True)
    if text is None:
        shown, text = 0, assemble(0)
    return Context(text, passages[:shown], passages[shown:], found.hidden_files, estimate_tokens(text), over)
