"""Shared pieces: constants, text decoding, token counting, ids, and `make_chunk`, the one place a
chunk dict is created."""
import hashlib
import logging
import os

log = logging.getLogger("chunker")  # one logger name for the whole package

MAX_CHUNK_TOKENS = 800
OVERLAP_TOKENS = 50


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

def lines_text(source_lines, start_row, end_row) -> str:
    """Whole source lines start_row..end_row (0-indexed, inclusive), verbatim (indentation
    and any \\r kept), so a chunk's text always equals source_lines[start_line - 1 : end_line]."""
    return "\n".join(source_lines[start_row:end_row + 1])

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
