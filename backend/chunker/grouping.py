"""Merging neighbouring tiny chunks into one `group` chunk."""
from .core import MAX_CHUNK_TOKENS, estimate_tokens, lines_text, make_chunk

# Neighbouring chunks that are each smaller than this get merged into one group (see group_small_chunks)
MIN_MERGE_TOKENS = 125
GROUP_SMALL_CHUNKS = True


def group_small_chunks(chunks, source_lines, file_path, rel_path):
    """Merge neighbouring tiny chunks (each under MIN_MERGE_TOKENS) into one `group` chunk, up to
    the cap, so a file of ten 3-line helpers is not ten near-empty chunks.

    Only chunks that sit side by side in the same scope merge (same `parent`: top level, or the
    methods of one class). Anything else between them (a class, a big function, a split piece)
    ends the run, so nothing merges across a class boundary. Imports merge only with imports,
    never with code. The group's text is the verbatim lines from its first member to its last
    (blank lines and comments in between included); its embed_text label lists every member's
    name so retrieval can still find each one: "utils.py · helper_a, helper_b, MAX_RETRIES".
    """
    def tiny(c):
        return (c["kind"] in ("function", "method", "module_code", "imports", "interface", "type", "enum") and not c["synthetic"]
                and not c["parse_error"] and c["part"] is None
                and estimate_tokens(c["text"]) < MIN_MERGE_TOKENS)

    def fits(first, nxt):
        compatible = (first["kind"] == "imports") == (nxt["kind"] == "imports")
        merged = lines_text(source_lines, first["start_line"] - 1, nxt["end_line"] - 1)
        return compatible and estimate_tokens(merged) <= MAX_CHUNK_TOKENS

    scopes = {}
    for c in chunks:
        scopes.setdefault(c["parent"], []).append(c)

    groups_at, absorbed = {}, set()  # first member's position -> group; ids of all members

    def close(run, scope):
        if len(run) < 2:
            return
        names = [n for c in run for n in c["names"]]
        group = make_chunk(
            kind="group", symbol=None, parent=scope, file_path=file_path, rel_path=rel_path,
            start_line=run[0]["start_line"], end_line=run[-1]["end_line"], source_lines=source_lines,
            names=names, label=", ".join(names) or run[0]["kind"], id_key=run[0]["id"],
        )
        groups_at[id(run[0])] = group
        absorbed.update(id(c) for c in run)

    for scope, items in scopes.items():
        items = sorted(items, key=lambda c: (c["start_line"], c["end_line"]))
        run = []
        for c in items:
            if tiny(c) and (not run or fits(run[0], c)):
                run.append(c)
                continue
            close(run, scope)
            run = [c] if tiny(c) else []
        close(run, scope)

    return [groups_at.get(id(c), c) for c in chunks if id(c) not in absorbed or id(c) in groups_at]
