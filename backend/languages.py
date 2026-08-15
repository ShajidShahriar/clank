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


LANGUAGE_CONFIGS = {
    ".py": {"language": PY_LANGUAGE, "classify_node": classify_python_node},
    ".js": {"language": JS_LANGUAGE, "classify_node": classify_js_node},
    ".jsx": {"language": JS_LANGUAGE, "classify_node": classify_js_node},
}