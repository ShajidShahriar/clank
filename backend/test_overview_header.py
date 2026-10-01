"""The overview's first line is the class line as written, bases included."""
from chunker import chunk_file


def overview_of(tmp_path, name, source, symbol):
    p = tmp_path / name
    p.write_text(source)
    return next(c for c in chunk_file(str(p)) if c["kind"] == "class_overview" and c["symbol"] == symbol)


def first_line(chunk):
    return chunk["text"].split("\n")[0]


def test_python_bases_are_kept_including_generics(tmp_path):
    src = "class SessionMixin(MutableMapping[str, t.Any]):\n    permanent = False\n"
    assert first_line(overview_of(tmp_path, "a.py", src, "SessionMixin")) == "class SessionMixin(MutableMapping[str, t.Any]):"


def test_python_multiple_and_keyword_bases(tmp_path):
    src = "class Both(Base, Mixin, metaclass=Meta):\n    x = 1\n"
    assert first_line(overview_of(tmp_path, "a.py", src, "Both")) == "class Both(Base, Mixin, metaclass=Meta):"


def test_python_multiline_bases_are_kept_verbatim(tmp_path):
    src = "class Wide(\n    First,\n    Second,\n):\n    x = 1\n"
    assert overview_of(tmp_path, "a.py", src, "Wide")["text"].startswith("class Wide(\n    First,\n    Second,\n):\n")


def test_python_decorator_and_nested_class_keep_their_lines(tmp_path):
    src = "@dataclass(frozen=True)\nclass Point(Base):\n    x: int = 0\n\n    class Meta(Other):\n        pass\n"
    assert overview_of(tmp_path, "a.py", src, "Point")["text"].startswith("@dataclass(frozen=True)\nclass Point(Base):\n")
    inner = overview_of(tmp_path, "a.py", src, "Meta")
    assert inner["parent"] == "Point" and first_line(inner) == "class Meta(Other):"


def test_js_extends_is_kept(tmp_path):
    src = "class Counter extends Base {\n  count = 0;\n}\n"
    assert first_line(overview_of(tmp_path, "a.js", src, "Counter")) == "class Counter extends Base"


def test_js_export_default_and_member_expression_extends(tmp_path):
    src = "export default class App extends React.Component {\n  render() { return 1; }\n}\n"
    assert first_line(overview_of(tmp_path, "a.js", src, "App")) == "export default class App extends React.Component"


def test_class_without_bases_is_unchanged(tmp_path):
    assert first_line(overview_of(tmp_path, "a.py", "class Plain:\n    x = 1\n", "Plain")) == "class Plain:"
    assert first_line(overview_of(tmp_path, "a.js", "class Plain {\n  x = 1;\n}\n", "Plain")) == "class Plain"
