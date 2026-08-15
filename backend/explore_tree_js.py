from tree_sitter import Parser
from languages import JS_LANGUAGE

parser = Parser(JS_LANGUAGE)

with open("dummy_js_jsdoc.js", "rb") as f:
    source_code = f.read()

tree = parser.parse(source_code)

for child in tree.root_node.children:
    print(child.type)
