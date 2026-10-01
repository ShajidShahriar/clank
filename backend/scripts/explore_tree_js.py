"""Manual script, not a test: print the top-level node types of a JS file and the members of its first class.
Run from backend/:  uv run python scripts/explore_tree_js.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tree_sitter import Parser
from languages import JS_LANGUAGE

parser = Parser(JS_LANGUAGE)

with open("dummy_js_class_body.js", "rb") as f:
    source_code = f.read()

tree = parser.parse(source_code)
class_node = tree.root_node.children[0]
body = class_node.child_by_field_name("body")

for child in tree.root_node.children:
    print(child.type)

for child in body.children:
    print(child.type, child.is_named)
