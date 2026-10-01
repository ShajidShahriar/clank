# ---------------------------------------------------------
# Turns one file into a list of chunks (dicts) for retrieval. Every chunk is a verbatim run of the
# file's lines with a line range (`text`), plus `embed_text`: what actually gets embedded.
# This is pseudocode of the current design; keep it in step with the code.
#
# chunk_file(path, repo_root):
#   .md / .markdown   -> chunk_markdown: one "doc_section" per heading (symbol "README > Setup"),
#                        text before the first heading is "doc_intro"; fenced code is never a heading
#   .json/.yaml/.yml  -> chunk_plain_text: kind "config", plain line windows (discovery skips big ones)
#   .py / .js / .jsx  -> the code path below (languages.py supplies, per language: the tree-sitter
#                        grammar, classify_node, is_import, defined_names)
#   anything else     -> []   (no chunker yet, e.g. .ts)
#   an empty or whitespace-only file -> []   (on purpose)
#
#   code path:
#     read bytes, parse with tree-sitter
#     if the tree has a syntax error: don't guess structure. Emit the whole file as kind
#         "text_fallback" line windows with parse_error=True, and log a warning
#
#     for each top-level node:
#         classify_node says "function" or "class" (a def claimed) or nothing (unclaimed)
#         - statements sharing a line with another statement are treated as unclaimed, because a chunk
#           is whole lines (this keeps minified code from copying one huge line per function)
#         - unclaimed nodes pile up in a run; the run closes at a def/class or when its category flips
#           between "imports" (import/require) and "module_code" (everything else)
#         - a comment block right above a def (no blank line, at most 30 lines) moves into that def
#         - function -> one "function" chunk;  class -> chunk_class_generic
#
#   chunk_class_generic(class):
#       each method (including JS class fields holding an arrow/function) -> a "method" chunk
#       everything else in the body + each method's signature -> one "class_overview" chunk, whose
#           text is BUILT (synthetic=True): the class line as written (decorators, bases, extends),
#           then attributes and signatures. If it is over the cap, every part repeats the class line.
#       nested classes recurse
#
#   group_small_chunks: neighbouring tiny chunks (each < MIN_MERGE_TOKENS, same scope, imports only
#       with imports, never across a class) merge into one kind="group" chunk, up to the cap;
#       its embed_text lists every member's name
#
# make_chunk(...)       the ONLY place a chunk dict is created: requires start/end line, builds
#                       embed_text ("<rel path> · <Class.symbol>" + text), id, content_hash, ordinal
# finalize_chunk(chunk) the funnel every chunk passes through: if over MAX_CHUNK_TOKENS (800, counted as
#                       len(text) // 3 until a real tokenizer is chosen) -> split_oversized:
#                         (a) between statements, grouping them up to the cap
#                         (b) one statement still too big: by whole lines, with a little overlap
#                         (c) one line still too big: by characters
#                       every part keeps the real symbol and gets part / part_count; later parts carry
#                       the function signature in embed_text
# ---------------------------------------------------------

import hashlib
import logging
import os
import re
from tree_sitter import Parser
from languages import LANGUAGE_CONFIGS

log = logging.getLogger(__name__)
MAX_CHUNK_TOKENS = 800
OVERLAP_TOKENS = 50
# Neighbouring chunks that are each smaller than this get merged into one group (see group_small_chunks)
MIN_MERGE_TOKENS = 125
GROUP_SMALL_CHUNKS = True
# A comment block taller than this above a definition is a file header (license, banner), not its doc
MAX_LEADING_COMMENT_LINES = 30

def decode_text(raw: bytes) -> str:
    """Bytes from the file to chunk text: bad bytes become U+FFFD, and CRLF becomes LF so a
    stray \\r never ends up in a chunk. Byte offsets and line numbers are taken from the
    original bytes, and a \\r\\n is one line break either way, so they still line up."""
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n")

def estimate_tokens(text: str) -> int:
    """Rough token count: about 3 characters per token. Replace with the embedding model's real
    tokenizer once one is chosen; this is the only place that needs to change."""
    return len(text) // 3

class Ordinals:
    """Counts chunks that would otherwise share an identity within one file: two `def f`, a
    property getter and its setter, several import runs. The first is 0, the next 1, and so on."""
    def __init__(self):
        self.seen = {}

    def next(self, kind, parent, symbol):
        key = (kind, parent, symbol)
        self.seen[key] = self.seen.get(key, -1) + 1
        return self.seen[key]

def sha1_hex(text: str, length: int = 16) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]

def make_chunk(*, kind, symbol, parent, file_path, start_line, end_line,
               source_lines=None, text=None, rel_path=None, synthetic=False, parse_error=False,
               context=None, ordinals=None, ordinal=0, part=None, names=None, label=None, id_key=None, part_count=None) -> dict:
    """The only place a chunk dict is created. Every rule about chunk shape goes here.

    start_line / end_line are required (1-indexed, inclusive); a chunk with no line pointer
    raises instead of slipping through. With no `text`, the text is the verbatim source lines
    start_line..end_line. A caller passes `text` only for built text (class_overview, which is
    marked synthetic) or for a split piece whose lines are already cut.
    `text` is always what is in the file. `embed_text` is what gets embedded: a one-line label
    ("<relative path> · <Class.method>") on top of the text, so the file and class context
    travels with the chunk without ever touching `text`. `context` (a function signature, for
    the later parts of a split function) goes between the label and the text.
    Identity: `id` = sha1(rel_path :: qualified symbol :: kind :: ordinal)[:16]. It does not
    depend on line numbers or on the text, so it survives edits above it and edits to the chunk
    itself. `ordinal` comes from the file's `Ordinals` counter (or is passed explicitly: the
    parts of a split chunk reuse their parent's, plus a `part` number). `content_hash` is
    sha1(embed_text)[:16]: it changes exactly when the chunk's embedded content (or its path or
    symbol label) changes, which tells an indexer what to re-embed.
    A piece of a split chunk keeps the real `symbol`; `part` (0-based) and `part_count` say which
    piece it is, and "(part 1/3)" appears in embed_text only, never in the symbol.
    `names` lists the symbols the chunk contains (a function's own name by default, a constant's
    name for a module_code run); a group lists all its members'. `label` overrides the name shown
    in embed_text; `id_key` replaces the qualified symbol in the id (a group uses its first
    member's id so it stays stable while that member does).
    `synthetic` means the text is NOT exactly source_lines[start_line-1:end_line]: a built summary
    (class_overview) or a piece of one over-long line.
    """
    if start_line is None or end_line is None:
        raise ValueError(f"chunk {kind} {symbol!r} in {file_path} needs start_line and end_line")
    if text is None:
        if source_lines is None:
            raise ValueError("make_chunk needs source_lines or text")
        text = lines_text(source_lines, start_line - 1, end_line - 1)
    rel_path = rel_path or os.path.basename(file_path)
    qualified = f"{parent}.{symbol}" if parent and symbol else (symbol or "")
    label = label or qualified or kind
    names = names if names is not None else ([qualified] if qualified else [])
    if ordinals is not None:
        ordinal = ordinals.next(kind, parent, symbol)
    ordinal_key = f"{ordinal}" if part is None else f"{ordinal}.{part}"
    part_note = f" (part {part + 1}/{part_count})" if part is not None and part_count else ""
    embed_text = f"{rel_path} · {label}{part_note}\n" + (f"{context}\n" if context else "") + text
    return {
        "id": sha1_hex(f"{rel_path}::{id_key if id_key is not None else qualified}::{kind}::{ordinal_key}"),
        "content_hash": sha1_hex(embed_text),
        "ordinal": ordinal, "part": part, "part_count": part_count, "names": names,
        "kind": kind, "symbol": symbol, "parent": parent,
        "file_path": file_path, "rel_path": rel_path,
        "start_line": start_line, "end_line": end_line,
        "text": text, "embed_text": embed_text,
        "synthetic": synthetic, "parse_error": parse_error,
    }

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
            if current:
                groups.append((*current, None))
                current = None
            groups.extend(split_by_lines(lines, lo, hi, cap))
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

def body_units(def_node):
    """(start_row, end_row) of each statement directly inside a function's body."""
    body = def_node.child_by_field_name("body")
    return [(c.start_point[0], c.end_point[0]) for c in body.children if c.is_named] if body else []

def get_signature(node, source_code, text_node=None) -> str:
    text_node = text_node or node
    body = node.child_by_field_name("body")
    end = body.start_byte if body else node.end_byte
    return decode_text(source_code[text_node.start_byte:end]).strip()

def lines_text(source_lines, start_row, end_row) -> str:
    """Whole source lines start_row..end_row (0-indexed, inclusive), verbatim (indentation
    and any \\r kept), so a chunk's text always equals source_lines[start_line - 1 : end_line]."""
    return "\n".join(source_lines[start_row:end_row + 1])

def take_leading_comments(pending, node):
    """Remove and return the comments at the end of `pending` that sit directly above `node`
    (no blank line between). That is how a JSDoc / # comment gets attached to its definition.
    A block taller than MAX_LEADING_COMMENT_LINES is not attached (a license header would be).
    A comment trailing another statement on its own line (`x = 1  # note`) is not leading."""
    lead, next_row = [], node.start_point[0]
    while pending and pending[-1].type == "comment" and pending[-1].end_point[0] == next_row - 1:
        before = pending[-1].prev_sibling
        if before is not None and before.end_point[0] == pending[-1].start_point[0]:
            break
        comment = pending.pop()
        lead.insert(0, comment)
        next_row = comment.start_point[0]
    if lead and lead[-1].end_point[0] - lead[0].start_point[0] + 1 > MAX_LEADING_COMMENT_LINES:
        pending.extend(lead)  # too tall to be a docstring: leave it where it was
        return []
    return lead

def first_row(node, lead):
    return (lead[0] if lead else node).start_point[0]

def chunk_class_generic(class_node, text_node, source_code, file_path, classify_node, parent_prefix=None, source_lines=None, lead=(), rel_path=None, ordinals=None):
    ordinals = ordinals if ordinals is not None else Ordinals()
    if source_lines is None:
        source_lines = decode_text(source_code).split("\n")
    name_node = class_node.child_by_field_name("name")
    class_name = name_node.text.decode("utf-8", errors="replace") if name_node else "anonymous"
    full_name = f"{parent_prefix}.{class_name}" if parent_prefix else class_name

    chunks, method_signatures, other_statements = [], [], []
    body = class_node.child_by_field_name("body")
    if body:
        pending = []  # comments seen since the last member, waiting to see what they sit above
        def keep_as_statement(node):
            other_statements.append(decode_text(source_code[node.start_byte:node.end_byte]))
        for child in body.children:
            if child.type == "comment":
                pending.append(child)
                continue
            result = classify_node(child)
            if not result:
                if child.is_named:
                    for c in pending:
                        keep_as_statement(c)
                    pending.clear()
                    keep_as_statement(child)
                continue  # unnamed tokens like "{" and ";" don't end a comment's wait
            member_lead = take_leading_comments(pending, result["text_node"])
            for c in pending:
                keep_as_statement(c)
            pending.clear()
            if result["kind"] == "function":
                start_row = first_row(result["text_node"], member_lead)
                chunk = make_chunk(
                    kind="method", symbol=result["name"], parent=full_name, file_path=file_path,
                    start_line=start_row + 1, end_line=result["text_node"].end_point[0] + 1,
                    source_lines=source_lines, rel_path=rel_path, ordinals=ordinals,
                )
                signature = get_signature(result["def_node"], source_code, result["text_node"])
                chunks.extend(finalize_chunk(chunk, body_units(result["def_node"]), signature))
                method_signatures.append(signature)
            elif result["kind"] == "class":
                chunks.extend(chunk_class_generic(result["def_node"], result["text_node"], source_code, file_path, classify_node, full_name, source_lines, member_lead, rel_path, ordinals))
                method_signatures.append(f"class {result['name']}: ...")
        for c in pending:
            keep_as_statement(c)

    # The class line exactly as written (decorators, `export`, bases, `extends`), because
    # "what subclasses X?" is answered by this line.
    header = "\n".join([decode_text(c.text) for c in lead] + [get_signature(class_node, source_code, text_node)])
    entries = [f"    {s}" for s in other_statements] + [f"    {m}" for m in method_signatures]
    body = "\n".join(entries)

    overview_chunk = make_chunk(
        kind="class_overview", symbol=class_name, parent=parent_prefix, file_path=file_path, rel_path=rel_path, ordinals=ordinals,
        start_line=first_row(text_node, lead) + 1, end_line=text_node.end_point[0] + 1,
        text=header + ("\n" + body if body else ""), synthetic=True,  # a built summary, not source lines
    )
    if estimate_tokens(overview_chunk["text"]) <= MAX_CHUNK_TOKENS or not entries:
        chunks.append(overview_chunk)
    else:
        # Too big for one chunk: split the members, never separate them from the class line. Every
        # part starts with the full header, so a part is never a bare list of signatures.
        units, row = [], 0
        for entry in entries:
            units.append((row, row + entry.count("\n")))
            row += entry.count("\n") + 1
        chunks.extend(split_oversized({**overview_chunk, "text": body}, units=units, text_prefix=header))
    return chunks



# --- Grouping tiny neighbours ---------------------------------------------------------------

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
        return (c["kind"] in ("function", "method", "module_code", "imports") and not c["synthetic"]
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

# --- Markdown and config files -------------------------------------------------------------
# No parser needed: headings are found line by line, and fenced code blocks are skipped over.

MARKDOWN_EXTENSIONS = {".md", ".markdown"}
CONFIG_EXTENSIONS = {".json", ".yaml", ".yml"}  # indexed as plain text windows (see chunk_file)

HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
SETEXT_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
NOT_SETEXT_TEXT_RE = re.compile(r"^ {0,3}(>|[-*+][ \t]|\d+[.)][ \t]|\||    )")

def markdown_fence_rows(lines):
    """True for every row inside a fenced code block (the fence lines included)."""
    inside, fence = [], None
    for line in lines:
        m = FENCE_RE.match(line)
        if fence is None:
            fence = (m.group(1)[0], len(m.group(1))) if m else None
            inside.append(fence is not None)
        else:
            inside.append(True)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] and not line.strip().lstrip(fence[0]):
                fence = None
    return inside

def markdown_headings(lines, in_fence):
    """[(row, level, title, last_row)] for ATX (`## Title`) and setext (`Title` over `===`/`---`)
    headings, ignoring fenced code and a leading `---` front-matter block."""
    first_row = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() in ("---", "..."):
                first_row = i + 1
                break
    headings, row = [], first_row
    while row < len(lines):
        line = lines[row]
        if in_fence[row]:
            row += 1
            continue
        m = HEADING_RE.match(line)
        if m:
            headings.append((row, len(m.group(1)), m.group(2).strip(), row))
        elif (line.strip() and row + 1 < len(lines) and not in_fence[row + 1]
              and SETEXT_RE.match(lines[row + 1]) and not NOT_SETEXT_TEXT_RE.match(line)
              and (row == first_row or not lines[row - 1].strip())):
            headings.append((row, 1 if lines[row + 1].lstrip().startswith("=") else 2, line.strip(), row + 1))
            row += 1
        row += 1
    return headings

def markdown_blocks(lines, in_fence, lo, hi):
    """(start_row, end_row) of each paragraph / list / fenced code block between lo and hi."""
    blocks, start = [], None
    for row in range(lo, hi + 1):
        blank = not lines[row].strip() and not in_fence[row]
        if blank and start is not None:
            blocks.append((start, row - 1))
            start = None
        elif not blank and start is None:
            start = row
        elif start is not None and in_fence[row] and not in_fence[row - 1]:
            blocks.append((start, row - 1))  # a fence opens straight after text: it is its own block
            start = row
    if start is not None:
        blocks.append((start, hi))
    return blocks

def chunk_markdown(file_path, rel_path, source_code):
    """One chunk per heading section, symbol = the heading path ("README > Setup > Install").
    A heading with no text of its own is folded into the next section, so a chunk never
    consists of a bare title. Text before the first heading is a `doc_intro` chunk."""
    lines = decode_text(source_code).split("\n")
    in_fence = markdown_fence_rows(lines)
    headings = markdown_headings(lines, in_fence)
    ordinals = Ordinals()
    chunks = []

    def trimmed(lo, hi):
        while hi >= lo and not lines[hi].strip():
            hi -= 1
        while lo <= hi and not lines[lo].strip():
            lo += 1
        return lo, hi

    def emit(kind, symbol, lo, hi):
        lo, hi = trimmed(lo, hi)
        if hi < lo:
            return
        chunks.extend(finalize_chunk(make_chunk(
            kind=kind, symbol=symbol, parent=None, file_path=file_path, rel_path=rel_path,
            start_line=lo + 1, end_line=hi + 1, source_lines=lines, ordinals=ordinals,
        ), units=markdown_blocks(lines, in_fence, lo, hi)))

    if not headings:
        emit("doc_intro", None, 0, len(lines) - 1)
        return chunks
    emit("doc_intro", None, 0, headings[0][0] - 1)

    path, pending_start = [], None
    for k, (row, level, title, last_row) in enumerate(headings):
        while path and path[-1][0] >= level:
            path.pop()
        path.append((level, title))
        end = (headings[k + 1][0] - 1) if k + 1 < len(headings) else len(lines) - 1
        start = row if pending_start is None else pending_start
        has_body = any(lines[r].strip() for r in range(last_row + 1, end + 1))
        if not has_body and k + 1 < len(headings):
            pending_start = start  # bare title: carry it into the next section
            continue
        pending_start = None
        emit("doc_section", " > ".join(t for _, t in path), start, end)
    return chunks

def chunk_plain_text(file_path, rel_path, source_code, kind):
    """No structure assumed: the whole file, cut into line windows by finalize_chunk if needed."""
    lines = decode_text(source_code).rstrip("\n").split("\n")
    return finalize_chunk(make_chunk(
        kind=kind, symbol=None, parent=None, file_path=file_path, rel_path=rel_path,
        start_line=1, end_line=len(lines), source_lines=lines, ordinals=Ordinals(),
    ))

def chunk_file(file_path: str, repo_root: str | None = None) -> list[dict]:
    ext = file_path[file_path.rfind("."):]
    config = LANGUAGE_CONFIGS.get(ext)
    if not config and ext not in MARKDOWN_EXTENSIONS | CONFIG_EXTENSIONS:
        return []

    rel_path = os.path.relpath(file_path, repo_root).replace(os.sep, "/") if repo_root else os.path.basename(file_path)
    with open(file_path, "rb") as f:
        source_code = f.read()
    if not source_code.strip():
        return []  # empty or whitespace-only: nothing to index, on purpose

    if ext in MARKDOWN_EXTENSIONS:
        return chunk_markdown(file_path, rel_path, source_code)
    if ext in CONFIG_EXTENSIONS:
        return chunk_plain_text(file_path, rel_path, source_code, "config")

    ordinals = Ordinals()
    parser = Parser(config["language"])

    tree = parser.parse(source_code)
    source_lines = decode_text(source_code).split("\n")

    if tree.root_node.has_error:
        # Don't guess at structure in a file the parser couldn't read: index it as plain text windows.
        log.warning("parse error in %s: indexing as text_fallback windows", rel_path)
        last_line = len(source_lines) - (1 if source_lines[-1] == "" else 0)
        return finalize_chunk(make_chunk(
            kind="text_fallback", symbol=None, parent=None, file_path=file_path, rel_path=rel_path, ordinals=ordinals,
            start_line=1, end_line=last_line, source_lines=source_lines, parse_error=True,
        ))

    chunks, run = [], []
    run_kind = None  # "imports" or "module_code"; None while the run holds only comments

    def flush_run():
        """Emit one chunk for a run of adjacent unclaimed top-level nodes: kind "imports" if the
        run is import/require statements, otherwise "module_code". Text is the whole lines the
        run spans (gaps, blank lines and comments included), so it matches its line pointer."""
        nonlocal run_kind
        kind, run_kind = run_kind or "module_code", None
        if not run:
            return
        start_row, end_row = run[0].start_point[0], run[-1].end_point[0]
        if lines_text(source_lines, start_row, end_row).strip():
            chunks.extend(finalize_chunk(make_chunk(
                kind=kind, symbol=None, parent=None, file_path=file_path, rel_path=rel_path, ordinals=ordinals,
                start_line=start_row + 1, end_line=end_row + 1, source_lines=source_lines,
                names=[name for n in run for name in config["defined_names"](n)],
            ), units=[(n.start_point[0], n.end_point[0]) for n in run]))
        run.clear()

    # A chunk is whole lines, so two top-level statements on one line (minified code) cannot each
    # own it: every one of them would carry the whole line. Treat statements on shared lines as
    # plain code instead of as separate functions/classes.
    statements = [n for n in tree.root_node.children if n.type != "comment"]
    crowded_rows = {b.start_point[0] for a, b in zip(statements, statements[1:]) if b.start_point[0] == a.end_point[0]}
    crowded_rows |= {a.end_point[0] for a, b in zip(statements, statements[1:]) if b.start_point[0] == a.end_point[0]}

    for child in tree.root_node.children:
        result = config["classify_node"](child)
        if result and {result["text_node"].start_point[0], result["text_node"].end_point[0]} & crowded_rows:
            result = None
        if not result:
            if child.type != "comment":
                category = "imports" if config["is_import"](child) else "module_code"
                if run_kind and category != run_kind:
                    # kind changes here: comments right above this statement go with it, not the old run
                    carried = []
                    while run and run[-1].type == "comment":
                        carried.insert(0, run.pop())
                    flush_run()
                    run.extend(carried)
                run_kind = category
            run.append(child)
            continue
        lead = take_leading_comments(run, result["text_node"])
        flush_run()
        if result["kind"] == "class":
            chunks.extend(chunk_class_generic(result["def_node"], result["text_node"], source_code, file_path, config["classify_node"], source_lines=source_lines, lead=lead, rel_path=rel_path, ordinals=ordinals))
        else:
            start_row = first_row(result["text_node"], lead)
            chunks.extend(finalize_chunk(make_chunk(
                kind="function", symbol=result["name"], parent=None, file_path=file_path, rel_path=rel_path, ordinals=ordinals,
                start_line=start_row + 1, end_line=result["text_node"].end_point[0] + 1,
                source_lines=source_lines,
            ), body_units(result["def_node"]), get_signature(result["def_node"], source_code, result["text_node"])))
    flush_run()
    if GROUP_SMALL_CHUNKS:
        chunks = group_small_chunks(chunks, source_lines, file_path, rel_path)
    return chunks
