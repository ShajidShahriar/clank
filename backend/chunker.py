# ---------------------------------------------------------
# chunk_python_file(file_path):
#   load a Python-aware parser (tree-sitter)
#   read the file as raw bytes
#   parse the bytes into a syntax tree
#
#   chunks = []
#
#   walk(node):
#       if node is a function or a class definition:
#           slice out this node's exact source text (by byte range)
#           record {type, start_line, end_line, text} into chunks
#           STOP — do not look inside this node any further
#       else:
#           for each child of this node:
#               walk(child)   <- recurse
#
#   walk(the whole file's root node)
#   return chunks
# ---------------------------------------------------------


import tree_sitter_python as tspython
from tree_sitter import Language, Parser

PY_LANGUAGE = Language(tspython.language())

def chunk_python_file(file_path: str):
    parser = Parser(PY_LANGUAGE)

    with open(file_path, "rb") as f:
        source_code = f.read()

    tree = parser.parse(source_code)
    root_node = tree.root_node

    chunks = []

    def walk(node):
        if node.type in ("function_definition", "class_definition"):
            chunk_text = source_code[node.start_byte:node.end_byte].decode("utf-8")
            chunks.append({
                "type": node.type,
                "start_line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
                "text": chunk_text,
            })
            return

        for child in node.children:
            walk(child)

    walk(root_node)
    return chunks