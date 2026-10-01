import tree_sitter_python as tspython
import tree_sitter_javascript as tsjavascript
from tree_sitter import Language

PY_LANGUAGE = Language(tspython.language())
JS_LANGUAGE = Language(tsjavascript.language())


def classify_python_node(node):
    target, text_node = node, node
    if node.type == "decorated_definition":
        for child in node.children:
            if child.type in ("function_definition", "class_definition"):
                target = child
                break
        else:
            return None

    if target.type in ("function_definition", "class_definition"):
        name_node = target.child_by_field_name("name")
        name = name_node.text.decode("utf-8") if name_node else "anonymous"
        kind = "function" if target.type == "function_definition" else "class"
        return {"kind": kind, "def_node": target, "text_node": text_node, "name": name}
    return None


def classify_js_node(node):
    target, text_node = node, node

    if node.type == "export_statement":
        decl = node.child_by_field_name("declaration")
        if decl is None:
            for child in node.children:
                if child.type in ("function_declaration", "class_declaration"):
                    decl = child
                    break
        if decl is None:
            return None
        target = decl
        text_node = node 

    if target.type in ("function_declaration", "class_declaration", "method_definition"):
        name_node = target.child_by_field_name("name")
        name = name_node.text.decode("utf-8") if name_node else "anonymous"
        kind = "class" if target.type == "class_declaration" else "function"
        return {"kind": kind, "def_node": target, "text_node": text_node, "name": name}

    if target.type in ("lexical_declaration", "variable_declaration"):
        for declarator in target.children:
            if declarator.type != "variable_declarator":
                continue
            value = declarator.child_by_field_name("value")
            if value is not None and value.type in ("arrow_function", "function_expression"):
                name_node = declarator.child_by_field_name("name")
                name = name_node.text.decode("utf-8") if name_node else "anonymous"
                return {"kind": "function", "def_node": value, "text_node": text_node, "name": name}
    return None


PY_IMPORT_TYPES = {"import_statement", "import_from_statement", "future_import_statement"}


def is_python_import(node):
    return node.type in PY_IMPORT_TYPES


def _is_require_call(node):
    if node is None or node.type != "call_expression":
        return False
    fn = node.child_by_field_name("function")
    return fn is not None and fn.type == "identifier" and fn.text == b"require"


def is_js_import(node):
    """`import ... from`, `const x = require('x')` and a bare `require('x')`."""
    if node.type == "import_statement":
        return True
    if node.type in ("lexical_declaration", "variable_declaration"):
        declarators = [c for c in node.children if c.type == "variable_declarator"]
        return bool(declarators) and all(_is_require_call(d.child_by_field_name("value")) for d in declarators)
    if node.type == "expression_statement" and node.children:
        return _is_require_call(node.children[0])
    return False


LANGUAGE_CONFIGS = {
    ".py": {"language": PY_LANGUAGE, "classify_node": classify_python_node, "is_import": is_python_import},
    ".js": {"language": JS_LANGUAGE, "classify_node": classify_js_node, "is_import": is_js_import},
    ".jsx": {"language": JS_LANGUAGE, "classify_node": classify_js_node, "is_import": is_js_import},
}