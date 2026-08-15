import tree_sitter_python as tspython
from tree_sitter import Language, Parser

PY_LANGUAGE = Language(tspython.language())

parser = Parser(PY_LANGUAGE)

with open("dummy_decorators.py", "rb") as f:
    source_code = f.read()

tree = parser.parse(source_code)
root_node = tree.root_node

for child in root_node.children:
    print(child.type)
