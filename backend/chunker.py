# ---------------------------------------------------------
# unwrap_decorated(node):
#   if node is a decorated_definition:
#       find its function_definition/class_definition child
#       return (node, that child)        <- (text_node, def_node)
#   else:
#       return (node, node)              <- undecorated: both are the same node
#
#
# chunk_python_file(file_path):
#   load a Python-aware parser (tree-sitter)
#   read the file as raw bytes, parse into a syntax tree
#
#   chunks = []
#   module_level_ranges = []   <- byte ranges not claimed by any def/class
#
#   for each top-level child of the file's root node:
#       text_node, def_node = unwrap_decorated(child)
#       <- text_node includes the decorator line(s) if present, def_node is the real def
#
#       if def_node is a class_definition:
#           chunks += chunk_class(def_node, text_node)      <- see below
#       elif def_node is a function_definition:
#           chunk = {text_node's full text (decorators included), type "function"}
#           chunks += finalize_chunk(chunk)                 <- splits it if oversized
#       else:
#           remember this child's byte range in module_level_ranges
#           (imports, top-level constants, top-level statements, ...)
#
#   if module_level_ranges is non-empty:
#       join their text into one module_level chunk
#       chunks += finalize_chunk(that chunk)
#
#   return chunks
#
#
# chunk_class(node, text_node):
#   class_name = the class's name
#   docstring  = class body's first statement, if it's a bare string
#   method_signatures = []
#   chunks = []
#
#   for each direct child of the class body:
#       m_text_node, m_def_node = unwrap_decorated(child)
#       if m_def_node is a function_definition (a method, decorated or not):
#           record its "def ...():" line (decorators included) into method_signatures
#           chunk = {m_text_node's full text, type "method", parent = class_name}
#           chunks += finalize_chunk(chunk)                 <- splits it if oversized
#
#   overview_chunk = "class <name>:" + docstring (if any)
#                    + all collected method signatures
#                    (signatures only — no method bodies)
#   chunks += [overview_chunk]                    <- small by construction, never split
#
#   return chunks
#
#
# finalize_chunk(chunk):
#   estimate token count of chunk's text
#   if over MAX_CHUNK_TOKENS:
#       return split_oversized(chunk)   <- fixed-size line  windows, with overlap
#   else:
#       return [chunk]
# ---------------------------------------------------------




import tree_sitter_python as tspython
from tree_sitter import Language, Parser
from languages import LANGUAGE_CONFIGS

PY_LANGUAGE = Language(tspython.language())
MAX_CHUNK_TOKENS = 400
OVERLAP_TOKENS = 50

def estimate_tokens(text: str) -> int:
    return int(len(text.split()) / 0.75)

def unwrap_decorated(node):
    """
    Given any node, return (text_node, def_node):
    - text_node: where the chunk's TEXT should start from (includes decorators if present)
    - def_node: the actual function_definition/class_definition to inspect for name/body
    """
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ("function_definition", "class_definition"):
                return node, child
    return node, node

def split_oversized(chunk: dict) -> list[dict]:
    """Fallback: fixed-size split with overlap, only called on oversized chunks.
    Splits by LINE, not word, to preserve code structure/indentation."""
    lines = chunk["text"].split("\n")
    total_tokens = estimate_tokens(chunk["text"])

    if total_tokens == 0 or not lines:
        return [chunk]

    tokens_per_line = total_tokens / len(lines)
    window_lines = max(1, int(MAX_CHUNK_TOKENS / tokens_per_line))
    overlap_lines = min(int(OVERLAP_TOKENS / tokens_per_line), window_lines - 1)
    step = max(1, window_lines - overlap_lines)  # guard against infinite loop

    sub_chunks = []
    start = 0
    part_num = 0
    while start < len(lines):
        end = min(start + window_lines, len(lines))
        sub_text = "\n".join(lines[start:end])

        if chunk["start_line"] is not None:
            sub_start_line = chunk["start_line"] + start
            sub_end_line = chunk["start_line"] + end - 1
        else:
            sub_start_line = None
            sub_end_line = None

        sub_chunks.append({
            **chunk,
            "text": sub_text,
            "name": f"{chunk['name']}_part{part_num}" if chunk["name"] else None,
            "start_line": sub_start_line,
            "end_line": sub_end_line,
        })
        start += step
        part_num += 1

    return sub_chunks

def finalize_chunk(chunk: dict) -> list[dict]:
    """Every chunk passes through here — splits it further only if oversized."""
    if estimate_tokens(chunk["text"]) > MAX_CHUNK_TOKENS:
        return split_oversized(chunk)
    return [chunk]

def get_docstring_node(node):
    body = node.child_by_field_name("body")
    if body and body.children:
        first_stmt = body.children[0]
        if first_stmt.type == "expression_statement" and first_stmt.children[0].type == "string":
            return first_stmt
    return None
def get_signature(node, source_code, text_node=None) -> str:
    text_node = text_node or node
    body = node.child_by_field_name("body")
    end = body.start_byte if body else node.end_byte
    return source_code[text_node.start_byte:end].decode("utf-8", errors="replace").strip()

def lines_text(source_lines, start_row, end_row) -> str:
    """Whole source lines start_row..end_row (0-indexed, inclusive), verbatim (indentation
    and any \\r kept), so a chunk's text always equals source_lines[start_line - 1 : end_line]."""
    return "\n".join(source_lines[start_row:end_row + 1])

def take_leading_comments(pending, node):
    """Remove and return the comments at the end of `pending` that sit directly above `node`
    (no blank line between). That is how a JSDoc / # comment gets attached to its definition.
    A comment trailing another statement on its own line (`x = 1  # note`) is not leading."""
    lead, next_row = [], node.start_point[0]
    while pending and pending[-1].type == "comment" and pending[-1].end_point[0] == next_row - 1:
        before = pending[-1].prev_sibling
        if before is not None and before.end_point[0] == pending[-1].start_point[0]:
            break
        comment = pending.pop()
        lead.insert(0, comment)
        next_row = comment.start_point[0]
    return lead

def first_row(node, lead):
    return (lead[0] if lead else node).start_point[0]

def chunk_class_generic(class_node, text_node, source_code, file_path, classify_node, parent_prefix=None, source_lines=None, lead=()):
    if source_lines is None:
        source_lines = source_code.decode("utf-8", errors="replace").split("\n")
    name_node = class_node.child_by_field_name("name")
    class_name = name_node.text.decode("utf-8", errors="replace") if name_node else "anonymous"
    full_name = f"{parent_prefix}.{class_name}" if parent_prefix else class_name

    chunks, method_signatures, other_statements = [], [], []
    body = class_node.child_by_field_name("body")
    if body:
        pending = []  # comments seen since the last member, waiting to see what they sit above
        def keep_as_statement(node):
            other_statements.append(source_code[node.start_byte:node.end_byte].decode("utf-8", errors="replace"))
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
                chunk = {
                    "type": "method", "name": result["name"], "parent": full_name,
                    "file_path": file_path,
                    "start_line": start_row + 1,
                    "end_line": result["text_node"].end_point[0] + 1,
                    "text": lines_text(source_lines, start_row, result["text_node"].end_point[0]),
                    "header": f"# Inside {full_name}:",
                }
                chunks.extend(finalize_chunk(chunk))
                signature = get_signature(result["def_node"], source_code, result["text_node"])
                method_signatures.append(signature)
            elif result["kind"] == "class":
                chunks.extend(chunk_class_generic(result["def_node"], result["text_node"], source_code, file_path, classify_node, full_name, source_lines, member_lead))
                method_signatures.append(f"class {result['name']}: ...")
        for c in pending:
            keep_as_statement(c)

    overview_lines = [c.text.decode("utf-8", errors="replace") for c in lead]
    overview_lines.append(f"class {full_name}:")
    overview_lines += [f"    {s}" for s in other_statements]
    overview_lines += [f"    {m}" for m in method_signatures]

    overview_chunk = {
        "type": "class_overview", "name": full_name, "parent": parent_prefix,
        "synthetic": True,  # text is a built summary, not source lines
        "file_path": file_path,
        "start_line": first_row(text_node, lead) + 1, "end_line": text_node.end_point[0] + 1,
        "text": "\n".join(overview_lines),
    }
    chunks.append(overview_chunk)
    return chunks



def chunk_file(file_path: str) -> list[dict]:
    ext = file_path[file_path.rfind("."):]
    config = LANGUAGE_CONFIGS.get(ext)
    if not config:
        return []

    parser = Parser(config["language"])
    with open(file_path, "rb") as f:
        source_code = f.read()
    tree = parser.parse(source_code)

    source_lines = source_code.decode("utf-8", errors="replace").split("\n")
    chunks, run = [], []

    def flush_run():
        """Emit one module_level chunk for a run of adjacent unclaimed top-level nodes.
        Text is the whole lines the run spans, so it matches its line pointer exactly."""
        if not run:
            return
        start_row, end_row = run[0].start_point[0], run[-1].end_point[0]
        text = "\n".join(source_lines[start_row:end_row + 1])
        if text.strip():
            chunks.extend(finalize_chunk({
                "type": "module_level", "name": None, "parent": None,
                "file_path": file_path,
                "start_line": start_row + 1, "end_line": end_row + 1,
                "text": text,
            }))
        run.clear()

    for child in tree.root_node.children:
        result = config["classify_node"](child)
        if not result:
            run.append(child)
            continue
        lead = take_leading_comments(run, result["text_node"])
        flush_run()
        if result["kind"] == "class":
            chunks.extend(chunk_class_generic(result["def_node"], result["text_node"], source_code, file_path, config["classify_node"], source_lines=source_lines, lead=lead))
        else:
            start_row = first_row(result["text_node"], lead)
            chunks.extend(finalize_chunk({
                "type": "function", "name": result["name"], "parent": None,
                "file_path": file_path,
                "start_line": start_row + 1,
                "end_line": result["text_node"].end_point[0] + 1,
                "text": lines_text(source_lines, start_row, result["text_node"].end_point[0]),
            }))
    flush_run()
    return chunks
