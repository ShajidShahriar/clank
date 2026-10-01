"""Cutting an oversized chunk into parts: between statements, then by lines, then by characters."""
from .core import MAX_CHUNK_TOKENS, OVERLAP_TOKENS, estimate_tokens, make_chunk


def statement_segments(lines, units, base_row):
    """Cut a chunk's lines into segments that start at statement boundaries.

    `units` are (start_row, end_row) of the statements inside the chunk (direct children of a
    function body, or the nodes of a module run). Every line lands in exactly one segment: the
    first segment also holds whatever comes before the first statement (signature, decorators,
    leading comment), and the last also holds closing lines such as `}`.
    Returns inclusive (lo, hi) index ranges into `lines`.
    """
    starts = sorted({u[0] - base_row for u in units if 0 < u[0] - base_row < len(lines)})
    cuts = [0] + starts
    return [(lo, (cuts[i + 1] - 1) if i + 1 < len(cuts) else len(lines) - 1) for i, lo in enumerate(cuts)]

def split_by_lines(lines, lo, hi, max_tokens=None):
    """Windows of whole lines with a little overlap. A single line over the cap is cut by
    characters. Returns (lo, hi, text_override); text_override is only set for a cut-up line."""
    max_chars, overlap_chars = (max_tokens or MAX_CHUNK_TOKENS) * 3, OVERLAP_TOKENS * 3
    groups, i = [], lo
    while i <= hi:
        if len(lines[i]) >= max_chars:  # too long on its own: hard character split
            step = max(1, max_chars - overlap_chars)
            for pos in range(0, len(lines[i]), step):
                groups.append((i, i, lines[i][pos:pos + max_chars]))
                if pos + max_chars >= len(lines[i]):
                    break
            i += 1
            continue
        j, size = i, 0
        while j <= hi and len(lines[j]) < max_chars and size + len(lines[j]) + 1 <= max_chars:
            size += len(lines[j]) + 1
            j += 1
        groups.append((i, j - 1, None))
        # next window starts a few lines back so neighbouring windows overlap, but always moves forward
        back, kept = j - 1, 0
        while back > i and kept + len(lines[back]) + 1 <= overlap_chars:
            kept += len(lines[back]) + 1
            back -= 1
        i = max(i + 1, back + 1) if j <= hi else j
    return groups

def split_oversized(chunk: dict, units=None, signature=None, text_prefix=None) -> list[dict]:
    """Cut an oversized chunk into parts under the token cap, preferring clean boundaries:
      (a) at statement boundaries (`units`), grouping statements until the cap is reached;
      (b) if one statement alone is over the cap, by whole lines (with a little overlap);
      (c) if one line alone is over the cap, by characters.
    Each part gets an honest line range, and every part after the first carries the
    function's `signature` in its embed_text so it still says what it belongs to.
    `text_prefix` (class overviews) is repeated at the top of every part's text, first one included,
    and the cap shrinks by its size so each part still fits. For a synthetic chunk `units` are row
    ranges within its own text."""
    lines = chunk["text"].split("\n")
    base_row = 0 if chunk["synthetic"] else chunk["start_line"] - 1
    cap = MAX_CHUNK_TOKENS
    if text_prefix:
        cap = max(MAX_CHUNK_TOKENS - estimate_tokens(text_prefix + "\n"), MAX_CHUNK_TOKENS // 4)
    segments = statement_segments(lines, units, base_row) if units else [(0, len(lines) - 1)]

    def tokens(lo, hi):
        return estimate_tokens("\n".join(lines[lo:hi + 1]))

    groups, current = [], None
    for lo, hi in segments:
        if tokens(lo, hi) > cap:
            # A statement too big for one part is cut by lines. Whatever small group is waiting
            # (typically the signature line) joins the start of that cut instead of being stranded
            # as a part of its own.
            groups.extend(split_by_lines(lines, current[0] if current else lo, hi, cap))
            current = None
        elif current and tokens(current[0], hi) <= cap:
            current = (current[0], hi)
        else:
            if current:
                groups.append((*current, None))
            current = (lo, hi)
    if current:
        groups.append((*current, None))

    if len(groups) == 1 and groups[0][2] is None and not text_prefix:
        return [chunk]
    parts = []
    for n, (lo, hi, override) in enumerate(groups):
        whole = chunk["synthetic"]  # a summary's pieces all point at the full range they summarize
        parts.append(make_chunk(
            kind=chunk["kind"],
            symbol=chunk["symbol"],
            parent=chunk["parent"], file_path=chunk["file_path"], rel_path=chunk["rel_path"],
            start_line=chunk["start_line"] if whole else chunk["start_line"] + lo,
            end_line=chunk["end_line"] if whole else chunk["start_line"] + hi,
            text=(text_prefix + "\n" if text_prefix else "") + (override if override is not None else "\n".join(lines[lo:hi + 1])),
            synthetic=chunk["synthetic"] or override is not None,
            parse_error=chunk["parse_error"],
            context=signature if n > 0 else None,
            ordinal=chunk["ordinal"], part=n, part_count=len(groups), names=chunk["names"],
        ))
    return parts

def finalize_chunk(chunk: dict, units=None, signature=None) -> list[dict]:
    """Every chunk passes through here — splits it further only if oversized."""
    if estimate_tokens(chunk["text"]) > MAX_CHUNK_TOKENS:
        return split_oversized(chunk, units, signature)
    return [chunk]
