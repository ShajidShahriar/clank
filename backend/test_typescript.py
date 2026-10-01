"""TypeScript: everything JS has, plus interface / type / enum / abstract class / namespace / overloads."""
import pathlib

from chunker import chunk_file

EDGE = pathlib.Path(__file__).parent / "fixtures" / "edge"


def chunks_of(tmp_path, name, source):
    p = tmp_path / name
    p.write_text(source)
    return chunk_file(str(p))


def kinds(chunks):
    return [(c["kind"], c["parent"], c["symbol"]) for c in chunks]


def test_interface_type_and_enum_are_their_own_chunks(tmp_path):
    src = ("interface Props extends Base {\n  id: number;\n}\n\n"
           "export type Id = string | number;\n\nenum Color { Red, Green }\nexport const enum Dir { Up }\n"
           "export interface Other { x: 1 }\n")
    assert kinds(chunks_of(tmp_path, "a.ts", src)) == [
        ("interface", None, "Props"), ("type", None, "Id"), ("enum", None, "Color"),
        ("enum", None, "Dir"), ("interface", None, "Other"),
    ]


def test_interface_chunk_text_and_embed_label(tmp_path):
    chunks = chunks_of(tmp_path, "a.ts", "/** Props. */\nexport interface Props {\n  id: number;\n}\n")
    props = chunks[0]
    assert props["text"] == "/** Props. */\nexport interface Props {\n  id: number;\n}"
    assert props["embed_text"].startswith("a.ts · Props\n") and props["names"] == ["Props"]


def test_abstract_class_keeps_heritage_and_members(tmp_path):
    src = ("abstract class Shape<T> extends Base implements I {\n  private count = 0;\n"
           "  abstract area(): number;\n  static create(): Shape<any> { return null as any; }\n"
           "  handle = (e: Event): void => { this.count++; };\n}\n")
    chunks = chunks_of(tmp_path, "a.ts", src)
    overview = next(c for c in chunks if c["kind"] == "class_overview")
    assert overview["text"].split("\n")[0] == "abstract class Shape<T> extends Base implements I"
    assert "private count = 0" in overview["text"] and "handle = (e: Event): void =>" in overview["text"]
    assert sorted(c["symbol"] for c in chunks if c["kind"] == "method") == ["area", "create", "handle"]


def test_overloads_are_chunks_and_the_implementation_too(tmp_path):
    src = "function over(a: string): string;\nfunction over(a: number): number;\nfunction over(a: any) { return a; }\n"
    chunks = chunks_of(tmp_path, "a.ts", src)
    assert [(c["kind"], c["symbol"]) for c in chunks] == [("function", "over")] * 3
    assert len({c["id"] for c in chunks}) == 3


def test_namespace_and_declare_module_are_containers(tmp_path):
    src = "namespace NS {\n  export function f() {}\n  export interface Inner { y: number }\n}\n\ndeclare module 'ext-lib' {\n  export const y: number;\n}\n"
    chunks = chunks_of(tmp_path, "a.ts", src)
    assert ("method", "NS", "f") in [(c["kind"], c["parent"], c["symbol"]) for c in chunks]
    assert ("interface", "NS", "Inner") in [(c["kind"], c["parent"], c["symbol"]) for c in chunks]
    assert ("class_overview", None, "ext-lib") in [(c["kind"], c["parent"], c["symbol"]) for c in chunks]  # quotes dropped


def test_declare_function_and_typed_arrow_const_and_default_export(tmp_path):
    src = "declare function ext(x: number): void;\nexport const App: React.FC<P> = (props) => { return 1; };\nexport default function main(): void {}\n"
    assert [(c["kind"], c["symbol"]) for c in chunks_of(tmp_path, "a.ts", src)] == [
        ("function", "ext"), ("function", "App"), ("function", "main")]


def test_decorated_exported_class_keeps_its_decorator(tmp_path):
    src = "@Component({ selector: 'x' })\nexport class Cmp {\n  constructor(private a: number) {}\n}\n"
    overview = next(c for c in chunks_of(tmp_path, "a.ts", src) if c["kind"] == "class_overview")
    assert overview["text"].startswith("@Component({ selector: 'x' })\nexport class Cmp\n")


def test_type_only_and_require_imports_are_imports(tmp_path):
    src = "import type { A } from './a';\nimport fs = require('fs');\n\nconst x = 1;\n"
    assert [c["kind"] for c in chunks_of(tmp_path, "a.ts", src)] == ["imports", "module_code"]


def test_tsx_components_and_class_components(tmp_path):
    chunks = chunk_file(str(EDGE / "tsx_component.tsx"))
    assert [(c["kind"], c["parent"], c["symbol"]) for c in chunks if c["kind"] != "imports"] == [
        ("interface", None, "ButtonProps"), ("function", None, "Button"), ("function", None, "Card"),
        ("method", "Page", "toggle"), ("method", "Page", "render"), ("class_overview", None, "Page")]
    button = next(c for c in chunks if c["symbol"] == "Button")
    assert button["text"].startswith("/** A button. */\nexport function Button")  # JSDoc travels with it


def test_big_interface_splits_between_members(tmp_path):
    members = "".join(f"  property_number_{i}: SomeLongTypeName<{i}> | undefined;\n" for i in range(150))
    chunks = chunks_of(tmp_path, "a.ts", f"export interface Big {{\n{members}}}\n")
    assert len(chunks) > 1 and all(c["kind"] == "interface" and c["symbol"] == "Big" for c in chunks)
    assert all(c["text"].split("\n")[0].strip().startswith(("export interface", "property_number_")) for c in chunks)
    assert [c["part"] for c in chunks] == list(range(len(chunks)))


def test_syntax_error_in_ts_falls_back_to_text(tmp_path):
    chunks = chunks_of(tmp_path, "a.ts", "interface Broken {\n  id: ;\n}\n")
    assert [c["kind"] for c in chunks] == ["text_fallback"] and chunks[0]["parse_error"]


def test_exported_constants_are_named_in_the_group_label(tmp_path):
    chunks = chunks_of(tmp_path, "a.ts", "export const LIMIT = 3;\nexport const NAME = 'x';\n")
    assert [c["names"] for c in chunks] == [["LIMIT", "NAME"]]
