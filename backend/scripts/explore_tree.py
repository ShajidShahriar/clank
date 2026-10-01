"""Manual script, not a test: print the top-level node types of a Python file's syntax tree.
Run from backend/:  uv run python scripts/explore_tree.py
"""

import tree_sitter_python as tspython
from tree_sitter import Language, Parser

PY_LANGUAGE = Language(tspython.language())

parser = Parser(PY_LANGUAGE)

with open("tests/fixtures/dummy/dummy_decorators.py", "rb") as f:
    source_code = f.read()

tree = parser.parse(source_code)
root_node = tree.root_node

for child in root_node.children:
    print(child.type)
