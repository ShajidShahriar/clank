"""Oversized chunks: split at statements first, then lines, then characters."""
from chunker import MAX_CHUNK_TOKENS, chunk_file, estimate_tokens


def chunks_of(tmp_path, name, source):
    p = tmp_path / name
    p.write_text(source) if isinstance(source, str) else p.write_bytes(source)
    return chunk_file(str(p))


def lines_covered(chunks):
    covered = set()
    for c in chunks:
        covered.update(range(c["start_line"], c["end_line"] + 1))
    return covered


def assert_all_under_cap(chunks):
    for c in chunks:
        assert estimate_tokens(c["text"]) <= MAX_CHUNK_TOKENS, (c["kind"], c["symbol"], len(c["text"]))


def test_estimate_tokens_is_chars_over_three():
    assert estimate_tokens("a" * 30) == 10
    assert estimate_tokens("") == 0


def test_200_line_function_splits_at_statements(tmp_path):
    body = "".join(f"    total += compute(item_{i}, 12345)\n" for i in range(200))
    src = "def big(a, b):\n    total = 0\n" + body + "    return total\n"
    chunks = [c for c in chunks_of(tmp_path, "a.py", src) if c["kind"] == "function"]
    assert len(chunks) > 1
    assert_all_under_cap(chunks)
    source_lines = src.split("\n")
    for i, c in enumerate(chunks):
        assert c["text"] == "\n".join(source_lines[c["start_line"] - 1:c["end_line"]])  # honest ranges
        assert c["text"].split("\n")[0].startswith(("def big", "    total", "    return")) or "compute" in c["text"].split("\n")[0]
        assert not c["synthetic"]
    # every line of the function is in exactly one part, in order, no overlap
    assert chunks[0]["start_line"] == 1 and chunks[-1]["end_line"] == 203
    for before, after in zip(chunks, chunks[1:]):
        assert after["start_line"] == before["end_line"] + 1
    # parts after the first say what they belong to
    assert chunks[0]["embed_text"].count("def big(a, b):") == 1
    assert all("def big(a, b):\n" in c["embed_text"] for c in chunks[1:])
    assert {c["symbol"] for c in chunks} == {"big"}  # the real name, not big_part0
    assert [c["part"] for c in chunks] == list(range(len(chunks)))
    assert all(c["part_count"] == len(chunks) for c in chunks)
    assert all(f"(part {i + 1}/{len(chunks)})" in c["embed_text"].split("\n")[0] for i, c in enumerate(chunks))
    assert not any("part" in c["symbol"] for c in chunks)


def test_parts_do_not_cut_inside_a_multiline_statement(tmp_path):
    block = "    if flag:\n        first(1)\n        second(2)\n        third(3)\n"
    src = "def f(flag):\n" + block * 80
    chunks = [c for c in chunks_of(tmp_path, "b.py", src) if c["kind"] == "function"]
    assert len(chunks) > 1
    assert_all_under_cap(chunks)
    for c in chunks[1:]:
        assert c["text"].startswith("    if flag:")  # every later part starts at a statement
        assert c["text"].endswith("third(3)")


def test_one_giant_statement_falls_back_to_line_windows(tmp_path):
    items = "".join(f"        'item number {i} in a long list',\n" for i in range(300))
    src = "def f():\n    values = [\n" + items + "    ]\n    return values\n"
    chunks = [c for c in chunks_of(tmp_path, "c.py", src) if c["kind"] == "function"]
    assert len(chunks) > 2
    assert_all_under_cap(chunks)
    source_lines = src.split("\n")
    for c in chunks:
        assert c["text"] == "\n".join(source_lines[c["start_line"] - 1:c["end_line"]])
    assert lines_covered(chunks) >= set(range(1, 304))


def test_single_50kb_line_is_hard_split_by_characters(tmp_path):
    src = 'BLOB = "' + "x" * 50_000 + '"\n'
    chunks = chunks_of(tmp_path, "d.py", src)
    assert len(chunks) > 15  # 50 KB at ~2,250 new chars per piece
    assert_all_under_cap(chunks)
    assert {(c["start_line"], c["end_line"]) for c in chunks} == {(1, 1)}
    assert all(c["synthetic"] for c in chunks)  # text is a slice of a line, not whole lines
    assert chunks[0]["text"].startswith('BLOB = "xxx')
    assert chunks[-1]["text"].endswith('x"')


def test_minified_js_ends_up_under_the_cap(tmp_path):
    src = "".join(f"function f{i}(a){{return a*{i}+1}}var v{i}=f{i}({i});" for i in range(1500))
    assert len(src) > 50_000 and "\n" not in src
    chunks = chunks_of(tmp_path, "min.js", src + "\n")
    assert 1 < len(chunks) < 200  # not one copy of the whole line per function (that was 225,000 chunks)
    assert all(c["kind"] == "module_code" for c in chunks)
    assert_all_under_cap(chunks)
    assert lines_covered(chunks) == {1}


def test_oversized_js_method_parts_carry_the_signature(tmp_path):
    body = "".join(f"    this.total += step({i}, 99999);\n" for i in range(150))
    src = "class W {\n  run(x) {\n" + body + "  }\n}\n"
    methods = [c for c in chunks_of(tmp_path, "w.js", src) if c["kind"] == "method"]
    assert len(methods) > 1
    assert_all_under_cap(methods)
    assert all("run(x)" in c["embed_text"] for c in methods)
    assert all(c["symbol"] == "run" and c["parent"] == "W" for c in methods)
    assert methods[0]["embed_text"].startswith(f"w.js · W.run (part 1/{len(methods)})\n")


def test_module_code_run_splits_at_top_level_statements(tmp_path):
    src = "".join(f"CONSTANT_NUMBER_{i} = {i} * 1000 + 17\n" for i in range(200))
    chunks = chunks_of(tmp_path, "m.py", src)
    assert len(chunks) > 1 and all(c["kind"] == "module_code" for c in chunks)
    assert_all_under_cap(chunks)
    assert all(c["text"].startswith("CONSTANT_NUMBER_") and c["text"].endswith(tuple("0123456789")) for c in chunks)


def test_small_chunks_are_left_alone(tmp_path):
    chunks = chunks_of(tmp_path, "s.py", "def f():\n    return 1\n")
    assert [c["symbol"] for c in chunks] == ["f"]


def test_two_functions_on_one_line_do_not_each_claim_the_line(tmp_path):
    chunks = chunks_of(tmp_path, "t.js", "function a() {} function b() {}\nfunction c() {}\n")
    assert [(c["kind"], c["symbol"]) for c in chunks] == [("module_code", None), ("function", "c")]


def test_trailing_comment_does_not_demote_a_function(tmp_path):
    chunks = chunks_of(tmp_path, "t.js", "function a() {\n  return 1;\n} // end of a\nfunction b() {}\n")
    assert [c["symbol"] for c in chunks if c["kind"] == "function"] == ["a", "b"]


def test_signature_is_not_stranded_before_one_giant_statement(tmp_path):
    jsx = "".join(f"      <li key={{{i}}} className=\"item\">{{props.items[{i}].label}}</li>\n" for i in range(120))
    src = "export function List(props: Props) {\n  return (\n    <ul>\n" + jsx + "    </ul>\n  );\n}\n"
    chunks = [c for c in chunks_of(tmp_path, "list.tsx", src) if c["kind"] == "function"]
    assert len(chunks) > 1
    assert chunks[0]["text"].startswith("export function List(props: Props) {\n  return (")
    assert all(len(c["text"].split("\n")) > 1 for c in chunks)  # no part is a bare signature line
    assert_all_under_cap(chunks)
