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
    return source_code[text_node.start_byte:end].decode("utf-8").strip()

def chunk_class(node, source_code, file_path, text_node=None, parent_prefix=None):
    text_node = text_node or node
    class_name = node.child_by_field_name("name").text.decode("utf-8")
    full_name = f"{parent_prefix}.{class_name}" if parent_prefix else class_name
    chunks = []

    docstring_node = get_docstring_node(node)
    docstring = None
    if docstring_node:
        docstring = source_code[docstring_node.start_byte:docstring_node.end_byte].decode("utf-8")

    method_signatures = []
    class_level_statements = []
    body = node.child_by_field_name("body")

    for child in body.children:
        if child is docstring_node:
            continue  # already captured separately, skip to avoid duplicating it

        m_text_node, m_def_node = unwrap_decorated(child)

        if m_def_node.type == "function_definition":
            method_name = m_def_node.child_by_field_name("name").text.decode("utf-8")
            method_signatures.append(get_signature(m_def_node, source_code, m_text_node))
            method_text = source_code[m_text_node.start_byte:m_text_node.end_byte].decode("utf-8")
            method_chunk = {
                "type": "method",
                "name": method_name,
                "parent": full_name,
                "file_path": file_path,
                "start_line": m_text_node.start_point[0] + 1,
                "end_line": m_text_node.end_point[0] + 1,
                "text": f"# Inside class {full_name}:\n{method_text}",
            }
            chunks.extend(finalize_chunk(method_chunk))

        elif m_def_node.type == "class_definition":
            # nested class: recurse, tag it with a dotted name so retrieval can tell it's nested
            nested_name = m_def_node.child_by_field_name("name").text.decode("utf-8")
            chunks.extend(chunk_class(m_def_node, source_code, file_path, m_text_node, parent_prefix=full_name))
            method_signatures.append(f"class {nested_name}: ...")

        else:
            # class-level attribute, pass statement, etc. — small enough to fold into the overview directly
            stmt_text = source_code[child.start_byte:child.end_byte].decode("utf-8")
            class_level_statements.append(stmt_text)

    overview_text = f"class {full_name}:\n"
    if docstring:
        overview_text += f"    {docstring}\n"
    if class_level_statements:
        overview_text += "\n".join(f"    {s}" for s in class_level_statements) + "\n"
    overview_text += "\n".join(f"    {sig}" for sig in method_signatures)

    overview_chunk = {
        "type": "class_overview",
        "name": full_name,
        "parent": parent_prefix,
        "file_path": file_path,
        "start_line": text_node.start_point[0] + 1,
        "end_line": text_node.end_point[0] + 1,
        "text": overview_text,
    }
    chunks.append(overview_chunk)

    return chunks

def chunk_python_file(file_path: str) -> list[dict]:
    parser = Parser(PY_LANGUAGE)
    with open(file_path, "rb") as f:
        source_code = f.read()

    tree = parser.parse(source_code)
    root_node = tree.root_node

    chunks = []
    module_level_ranges = []  # bytes NOT covered by a function/class, for module_level chunk

    for child in root_node.children:
        text_node, def_node = unwrap_decorated(child)

        if def_node.type == "class_definition":
            chunks.extend(chunk_class(def_node, source_code, file_path, text_node))
        elif def_node.type == "function_definition":
            func_name = def_node.child_by_field_name("name").text.decode("utf-8")
            func_text = source_code[text_node.start_byte:text_node.end_byte].decode("utf-8")
            func_chunk = {
                "type": "function",
                "name": func_name,
                "parent": None,
                "file_path": file_path,
                "start_line": text_node.start_point[0] + 1,
                "end_line": text_node.end_point[0] + 1,
                "text": func_text,
            }
            chunks.extend(finalize_chunk(func_chunk))
        else:
            module_level_ranges.append((child.start_byte, child.end_byte))

    if module_level_ranges:
        module_text = "\n".join(
            source_code[s:e].decode("utf-8") for s, e in module_level_ranges
        ).strip()
        if module_text:
            module_chunk = {
                "type": "module_level",
                "name": None,
                "parent": None,
                "file_path": file_path,
                "start_line": None,
                "end_line": None,
                "text": module_text,
            }
            chunks.extend(finalize_chunk(module_chunk))

    return chunks