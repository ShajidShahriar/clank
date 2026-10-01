import tree_sitter_python as tspython
import tree_sitter_javascript as tsjavascript
import tree_sitter_typescript as tstypescript
from tree_sitter import Language

PY_LANGUAGE = Language(tspython.language())
JS_LANGUAGE = Language(tsjavascript.language())
TS_LANGUAGE = Language(tstypescript.language_typescript())
TSX_LANGUAGE = Language(tstypescript.language_tsx())


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

    # class field holding a function: `handle = (e) => {...}` is a method in everything but syntax
    if target.type == "field_definition":
        value = target.child_by_field_name("value")
        if value is not None and value.type in ("arrow_function", "function_expression"):
            name_node = target.child_by_field_name("property")
            name = name_node.text.decode("utf-8") if name_node else "anonymous"
            return {"kind": "function", "def_node": value, "text_node": text_node, "name": name}
        return None

    # `res.send = function send() {}`, `exports.render = (v) => v`, `Foo.prototype.bar = function () {}`
    if target.type == "expression_statement" and target.children:
        expr = target.children[0]
        if expr.type == "assignment_expression":
            left, right = expr.child_by_field_name("left"), expr.child_by_field_name("right")
            if (left is not None and left.type in ("member_expression", "identifier")
                    and right is not None and right.type in ("arrow_function", "function_expression")):
                name = "".join(left.text.decode("utf-8", errors="replace").split())
                return {"kind": "function", "def_node": right, "text_node": text_node, "name": name}
        return None

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


# TypeScript: everything JS has (functions, classes, arrow consts, ...) plus these. Each is its own
# chunk, because a TS file is mostly types and they are what people search for.
TS_NAMED_DECLARATIONS = {
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
}
TS_WRAPPERS = ("export_statement", "ambient_declaration")  # `export ...` and `declare ...`


def _node_name(node):
    name_node = node.child_by_field_name("name")
    return name_node.text.decode("utf-8", errors="replace").strip("\"'") if name_node else "anonymous"


def _ts_declaration(target, text_node):
    """The classification of a TypeScript-only declaration, or None if `target` is not one."""
    if target.type in TS_NAMED_DECLARATIONS:
        return {"kind": "function", "chunk_kind": TS_NAMED_DECLARATIONS[target.type],
                "def_node": target, "text_node": text_node, "name": _node_name(target)}
    if target.type in ("abstract_class_declaration", "internal_module", "module"):
        # `abstract class`, `namespace Foo {}`, `declare module "x" {}`: a container with members
        return {"kind": "class", "def_node": target, "text_node": text_node, "name": _node_name(target)}
    if target.type in ("function_signature", "abstract_method_signature", "method_signature"):
        return {"kind": "function", "def_node": target, "text_node": text_node, "name": _node_name(target)}
    if target.type == "public_field_definition":  # `handle = (e: Event): void => {...}` in a class
        value = target.child_by_field_name("value")
        if value is not None and value.type in ("arrow_function", "function_expression"):
            return {"kind": "function", "def_node": value, "text_node": text_node, "name": _node_name(target)}
    return None


def classify_ts_node(node):
    target = node
    if node.type in TS_WRAPPERS:
        target = node.child_by_field_name("declaration")
        if target is None:  # `declare function f(): void;` has no declaration field
            target = next((c for c in node.children if c.is_named and c.type not in ("decorator", "comment")), None)
    elif node.type == "expression_statement" and node.children and node.children[0].type == "internal_module":
        target = node.children[0]  # `namespace Foo {}` parses as an expression statement
    found = _ts_declaration(target, node) if target is not None else None
    return found if found else classify_js_node(node)


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


def _identifiers(node):
    """Names bound by an assignment target: `a`, `a, b`, `(a, b)`."""
    if node is None:
        return []
    if node.type == "identifier":
        return [node.text.decode("utf-8", errors="replace")]
    if node.type in ("pattern_list", "tuple_pattern", "list_pattern"):
        return [n for c in node.children for n in _identifiers(c)]
    return []


def defined_names_python(node):
    """Names a top-level statement defines, so a group can list `MAX_RETRIES` by name."""
    if node.type == "expression_statement" and node.children and node.children[0].type == "assignment":
        return _identifiers(node.children[0].child_by_field_name("left"))
    return []


def defined_names_js(node):
    if node.type == "export_statement":  # `export const X = ...`
        declaration = node.child_by_field_name("declaration")
        return defined_names_js(declaration) if declaration is not None else []
    if node.type in ("lexical_declaration", "variable_declaration"):
        return [n for d in node.children if d.type == "variable_declarator"
                for n in _identifiers(d.child_by_field_name("name"))]
    if node.type == "expression_statement" and node.children and node.children[0].type == "assignment_expression":
        left = node.children[0].child_by_field_name("left")
        if left is not None and left.type in ("identifier", "member_expression"):
            return ["".join(left.text.decode("utf-8", errors="replace").split())]
    return []


LANGUAGE_CONFIGS = {
    ".py": {"language": PY_LANGUAGE, "classify_node": classify_python_node, "is_import": is_python_import, "defined_names": defined_names_python},
    ".js": {"language": JS_LANGUAGE, "classify_node": classify_js_node, "is_import": is_js_import, "defined_names": defined_names_js},
    ".jsx": {"language": JS_LANGUAGE, "classify_node": classify_js_node, "is_import": is_js_import, "defined_names": defined_names_js},
    ".ts": {"language": TS_LANGUAGE, "classify_node": classify_ts_node, "is_import": is_js_import, "defined_names": defined_names_js},
    ".tsx": {"language": TSX_LANGUAGE, "classify_node": classify_ts_node, "is_import": is_js_import, "defined_names": defined_names_js},
}