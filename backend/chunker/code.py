"""The tree-sitter path: functions, classes (with a synthetic overview), runs of top-level code."""
from tree_sitter import Parser

from . import grouping
from .core import MAX_CHUNK_TOKENS, Ordinals, decode_text, estimate_tokens, lines_text, log, make_chunk
from .splitting import finalize_chunk, split_oversized

# A comment block taller than this above a definition is a file header (license, banner), not its doc
MAX_LEADING_COMMENT_LINES = 30


def body_units(def_node):
    """(start_row, end_row) of each statement directly inside a function's body."""
    body = def_node.child_by_field_name("body")
    return [(c.start_point[0], c.end_point[0]) for c in body.children if c.is_named] if body else []

def get_signature(node, source_code, text_node=None) -> str:
    """The definition up to its body. With no body (a type alias, an overload signature) it is
    the first line, so a long declaration is not repeated whole as "context"."""
    text_node = text_node or node
    body = node.child_by_field_name("body")
    end = body.start_byte if body else node.end_byte
    text = decode_text(source_code[text_node.start_byte:end]).strip()
    return text if body else text.split("\n")[0]

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
    class_name = name_node.text.decode("utf-8", errors="replace").strip("\"'") if name_node else "anonymous"  # `declare module "x"` has a quoted name
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
                    kind=result.get("chunk_kind", "method"), symbol=result["name"], parent=full_name, file_path=file_path,
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


def chunk_code(file_path, rel_path, source_code, config):
    """Chunk a .py / .js / .jsx / .ts / .tsx file using its language config (see languages.py)."""
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
                kind=result.get("chunk_kind", "function"), symbol=result["name"], parent=None, file_path=file_path, rel_path=rel_path, ordinals=ordinals,
                start_line=start_row + 1, end_line=result["text_node"].end_point[0] + 1,
                source_lines=source_lines,
            ), body_units(result["def_node"]), get_signature(result["def_node"], source_code, result["text_node"])))
    flush_run()
    if grouping.GROUP_SMALL_CHUNKS:
        chunks = grouping.group_small_chunks(chunks, source_lines, file_path, rel_path)
    return chunks
