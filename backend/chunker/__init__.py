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
#
# Where things live (this package, `from chunker import chunk_file`):
#   __init__.py   chunk_file: reads the file and routes it by extension (this map)
#   code.py       the tree-sitter path: functions, classes + overview, runs of top-level code
#   markdown.py   Markdown sections and plain-text config files
#   splitting.py  split_oversized / finalize_chunk: the cap (statements, then lines, then characters)
#   grouping.py   group_small_chunks: merge tiny neighbours (and the GROUP_SMALL_CHUNKS switch)
#   core.py       constants, decode_text, estimate_tokens, Ordinals, make_chunk (creates every chunk)
#   ../languages.py   per-language rules: grammar, classify_node, is_import, defined_names
# ---------------------------------------------------------

import os

from languages import LANGUAGE_CONFIGS

from .code import chunk_code
from .core import MAX_CHUNK_TOKENS, estimate_tokens, make_chunk
from .grouping import MIN_MERGE_TOKENS
from .markdown import CONFIG_EXTENSIONS, MARKDOWN_EXTENSIONS, chunk_markdown, chunk_plain_text

__all__ = ["chunk_file", "make_chunk", "estimate_tokens", "MAX_CHUNK_TOKENS", "MIN_MERGE_TOKENS"]


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

    return chunk_code(file_path, rel_path, source_code, config)
