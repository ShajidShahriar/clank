"""Sibling expansion: a hit that is one part of a split chunk reaches the LLM as the WHOLE chunk, in order.

The four shapes of split parts (checked against real chunker output):
- statement splits: parts cover contiguous lines; join them.
- line-window splits: parts OVERLAP by a few lines; drop the repeated lines (and check they really are repeats).
- class overviews: synthetic parts, each pointing at the whole class and repeating the class header; keep the header once.
- character cuts: synthetic pieces of ONE long line; they cannot be joined from the rows, so the live line is read.

Integrity rules, in order:
1. All parts present (0..part_count-1) and consistent -> stitched from the rows (`source="index"`).
2. Otherwise the lines are read from the LIVE file by pointer (`source="file"`), but only if (a) the missing parts are interior (the pointers
   of the first and last part are known) and (b) the file's hash still equals the indexed one: a pointer into a changed file is a guess.
3. Otherwise what exists is returned with an explicit marker for each missing part and `complete=False`. Never a quietly shorter chunk.
"""
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import chunk_store
from chunker.core import decode_text, estimate_tokens

from .budget import PASSAGE_OVERHEAD_TOKENS


@dataclass
class Passage:
    chunk_ids: list           # the rows it is made of, in order
    rel_path: str
    symbol: str | None
    parent: str | None
    kind: str
    start_line: int
    end_line: int
    text: str
    score: float              # the best score among the hits that led here
    source: str               # "index": built from stored rows, "file": the lines were read from the live file by pointer
    complete: bool            # False: some of it is missing and the text says where
    missing: list = field(default_factory=list)   # part numbers (0-based) that were not stored
    stale: bool = False       # the file changed (or could not be read) since it was indexed: its line numbers and code may be out of date
    names: list = field(default_factory=list)     # the symbols the chunk contains (a group lists all its members)
    narrowed: bool = False    # the chunk was too long for the ceiling: only the parts around the hits are shown (see _narrow)
    full_range: tuple | None = None               # (first line, last line) of the whole chunk, when narrowed
    shown_parts: list = field(default_factory=list)   # which parts (0-based) are shown, when narrowed
    shown_ranges: list = field(default_factory=list)  # the exact (first, last) lines of each run of shown parts, when narrowed (a gap is NOT shown)
    part_count: int | None = None                 # how many parts the whole chunk has, when narrowed


def expand(conn, project_id, repo_path, hits, ceiling_tokens=None, narrow_to_tokens=None) -> list[Passage]:
    """One passage per chunk, in the rank order of its best hit. Several hits on parts of one chunk become one passage.

    `ceiling_tokens` (None = no limit): a stitched chunk bigger than this is NARROWED to the parts that were hit plus their neighbours, aiming for
    `narrow_to_tokens` (default: the ceiling itself). The best hit part is always kept."""
    groups: dict = {}
    for hit in hits:
        row = hit.chunk
        key = ("whole", row["id"]) if row["part"] is None else ("split", row["rel_path"], row["kind"], row["parent"], row["symbol"], row["ordinal"])
        if key not in groups:
            groups[key] = [row, hit.score, {}]
        groups[key][1] = max(groups[key][1], hit.score)
        if row["part"] is not None:
            groups[key][2][row["part"]] = max(groups[key][2].get(row["part"], hit.score), hit.score)   # which parts were hit, and how well
    return [_whole(row, score) if row["part"] is None else _split(conn, project_id, repo_path, row, score, hit_parts, ceiling_tokens, narrow_to_tokens)
            for row, score, hit_parts in groups.values()]


def read_live_lines(repo_path, rel_path, start, end):
    """Lines start..end (1-indexed, inclusive) of a file in the repo, read the way the chunker read it. None if it cannot be done safely."""
    root = Path(repo_path).resolve()
    target = (root / rel_path).resolve()
    if not target.is_relative_to(root):
        return None                                   # a path that leaves the repo is never read
    try:
        lines = decode_text(target.read_bytes()).split("\n")
    except OSError:
        return None
    if start < 1 or end > len(lines) or end < start:
        return None
    return "\n".join(lines[start - 1:end])


def _whole(row, score) -> Passage:
    return Passage([row["id"]], row["rel_path"], row["symbol"], row["parent"], row["kind"], row["start_line"], row["end_line"],
                   row["text"], score, "index", True, [], names=list(row["names"]))


def _split(conn, project_id, repo_path, row, score, hit_parts=None, ceiling_tokens=None, narrow_to_tokens=None) -> Passage:
    present = chunk_store.get_siblings(conn, project_id, row["id"]) or [row]
    present = sorted(present, key=lambda p: p["part"])
    count = present[0]["part_count"]
    numbers = {p["part"] for p in present}
    missing = [n for n in range(count) if n not in numbers]
    first, last = present[0], present[-1]

    def passage(text, start, end, source, complete):
        built = Passage([p["id"] for p in present], first["rel_path"], first["symbol"], first["parent"], first["kind"],
                        start, end, text, score, source, complete, missing, names=list(first["names"]))
        too_long = ceiling_tokens is not None and estimate_tokens(text) + PASSAGE_OVERHEAD_TOKENS > ceiling_tokens
        return _narrow(built, present, hit_parts or {}, count, narrow_to_tokens or ceiling_tokens) if too_long else built

    if not missing:
        text = _stitch_rows(present)
        if text is not None:
            return passage(text, first["start_line"], max(p["end_line"] for p in present), "index", True)

    # parts missing, or the stored parts do not fit together: the live file is the next best source, if it is the file that was indexed
    interior = first["part"] == 0 and last["part"] == count - 1
    if interior:
        start, end = min(p["start_line"] for p in present), max(p["end_line"] for p in present)
        if _file_is_unchanged(conn, project_id, repo_path, first["rel_path"]):
            text = read_live_lines(repo_path, first["rel_path"], start, end)
            if text is not None:
                return passage(text, start, end, "file", True)

    # nothing trustworthy to fill the gaps with: return what exists and say exactly what is not there
    return passage(_with_markers(present, count), min(p["start_line"] for p in present), max(p["end_line"] for p in present), "index", False)


def _stitch_rows(present):
    """The chunk's text from its stored parts, or None if they do not fit together (the caller then reads the file)."""
    if not any(p["synthetic"] for p in present):
        return _join_lines(present)
    if all(p["synthetic"] for p in present) and present[0]["kind"] == "class_overview":
        return _join_overview(present)
    return None                                       # character cuts of one line


def _join_lines(parts):
    """Contiguous or overlapping line-exact parts -> their lines, each exactly once. None if anything does not line up."""
    for p in parts:
        if len(p["text"].split("\n")) != p["end_line"] - p["start_line"] + 1:
            return None                               # the text is not the lines its pointer claims
    lines = parts[0]["text"].split("\n")
    end = parts[0]["end_line"]
    for p in parts[1:]:
        if p["start_line"] > end + 1:
            return None                               # a gap
        overlap = max(0, end - p["start_line"] + 1)
        mine = p["text"].split("\n")
        if overlap > len(mine) or lines[len(lines) - overlap:] != mine[:overlap]:
            return None                               # the overlap is not a repeat of what we already have
        lines += mine[overlap:]
        end = max(end, p["end_line"])
    return "\n".join(lines)


def _join_overview(parts):
    """Class overview parts repeat the class header (leading comment + class line). Keep it once."""
    split = [p["text"].split("\n") for p in parts]
    header = 0
    while all(len(s) > header for s in split) and len({s[header] for s in split}) == 1:
        header += 1
    if header == 0:
        return None
    lines = split[0] + [line for s in split[1:] for line in s[header:]]
    return "\n".join(lines)


def _with_markers(present, count):
    """What exists, in order, with a marker line wherever a part is not available."""
    by_number = {p["part"]: p for p in present}
    blocks, run = [], []

    def flush():
        if run:
            text = _join_lines(run) if not any(p["synthetic"] for p in run) else None
            blocks.append(text if text is not None else "\n".join(p["text"] for p in run))
            run.clear()

    for n in range(count):
        if n in by_number:
            run.append(by_number[n])
        else:
            flush()
            blocks.append(f"[... part {n + 1} of {count} not available ...]")
    flush()
    return "\n".join(blocks)


def _file_is_unchanged(conn, project_id, repo_path, rel_path) -> bool:
    stored = chunk_store.file_hash(conn, project_id, rel_path)
    root = Path(repo_path).resolve()
    target = (root / rel_path).resolve()
    if stored is None or not target.is_relative_to(root):
        return False
    try:
        return hashlib.sha1(target.read_bytes()).hexdigest() == stored
    except OSError:
        return False


def _narrow(whole: Passage, present, hit_scores, count, ceiling) -> Passage:
    """A chunk longer than the ceiling: show only the parts that were hit (best first, as many as fit), then their immediate neighbours while there
    is room, with a marker for every gap. What is shown is verbatim; what is not shown is named by its line range, so it can be read from the file."""
    by_part = {p["part"]: p for p in present}

    def cost(n):
        return estimate_tokens(by_part[n]["text"]) + 1

    wanted = sorted((n for n in hit_scores if n in by_part), key=lambda n: -hit_scores[n]) or [present[len(present) // 2]["part"]]
    chosen, used = [], PASSAGE_OVERHEAD_TOKENS
    for n in wanted:
        if chosen and used + cost(n) > ceiling:
            break                                          # the best hit always comes first; later hits only if they fit
        chosen.append(n)
        used += cost(n)
    for n in list(chosen):                                 # neighbours of what is shown, while there is room
        for m in (n - 1, n + 1):
            if m in by_part and m not in chosen and used + cost(m) <= ceiling:
                chosen.append(m)
                used += cost(m)
    chosen.sort()

    synthetic = any(p["synthetic"] for p in present)       # overview parts all point at the whole class: gaps are named by part, not by line
    runs, current = [], []
    for n in chosen:
        if current and n == current[-1] + 1:
            current.append(n)
        else:
            current = [n]
            runs.append(current)
    first_line, last_line = min(p["start_line"] for p in present), max(p["end_line"] for p in present)

    def gap(from_part, to_part, from_line, to_line):
        return f"[... parts {from_part + 1}-{to_part + 1} not shown ...]" if synthetic else f"[... lines {from_line}-{to_line} not shown ...]"

    blocks = []
    for index, run in enumerate(runs):
        rows = [by_part[n] for n in run]
        previous_end = by_part[runs[index - 1][-1]]["end_line"] if index else None
        if index == 0 and run[0] != 0 and by_part.get(0) is not None:
            blocks.append(gap(0, run[0] - 1, first_line, rows[0]["start_line"] - 1))
        elif index:
            blocks.append(gap(runs[index - 1][-1] + 1, run[0] - 1, previous_end + 1, rows[0]["start_line"] - 1))
        blocks.append((_join_lines(rows) if not synthetic else None) or "\n".join(r["text"] for r in rows))
    if runs[-1][-1] != count - 1 and by_part.get(count - 1) is not None:
        blocks.append(gap(runs[-1][-1] + 1, count - 1, by_part[runs[-1][-1]]["end_line"] + 1, last_line))
    shown_rows = [by_part[n] for n in chosen]
    ranges = [(first_line, last_line)] if synthetic else [(by_part[run[0]]["start_line"], by_part[run[-1]]["end_line"]) for run in runs]
    return Passage([r["id"] for r in shown_rows], whole.rel_path, whole.symbol, whole.parent, whole.kind,
                   shown_rows[0]["start_line"] if not synthetic else first_line, shown_rows[-1]["end_line"] if not synthetic else last_line,
                   "\n".join(blocks), whole.score, "index", False, whole.missing, names=whole.names,
                   narrowed=True, full_range=(first_line, last_line), shown_parts=chosen, shown_ranges=ranges, part_count=count)
