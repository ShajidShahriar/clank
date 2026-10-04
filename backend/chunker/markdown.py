"""Markdown (one chunk per heading section) and plain-text config files. No parser needed."""
import re

from .core import Ordinals, decode_text, make_chunk
from .splitting import finalize_chunk


MARKDOWN_EXTENSIONS = {".md", ".markdown"}
CONFIG_EXTENSIONS = {".json", ".yaml", ".yml"}  # indexed as plain text windows (see chunk_file)
RST_EXTENSIONS = {".rst"}                         # reStructuredText docs: plain text windows, kind "doc_text" (devlog 79 to 81)

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
