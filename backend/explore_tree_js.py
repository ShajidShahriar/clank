from tree_sitter import Parser
from languages import JS_LANGUAGE

parser = Parser(JS_LANGUAGE)

with open("dummy_class_body.py", "rb") as f:
    source_code = f.read()

tree = parser.parse(source_code)
class_node = tree.root_node.children[0]
body = class_node.child_by_field_name("body")


for child in tree.root_node.children:
    print(child.type)

for child in body.children:
    print(child.type, child.is_named)

