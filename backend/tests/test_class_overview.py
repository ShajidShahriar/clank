from chunker import MAX_CHUNK_TOKENS, chunk_file, estimate_tokens

BIG_BODY = "\n".join(f"    this.total += {i};" for i in range(300))

JS = f"""class Widget {{
  count = 0;
  #secret = 1;
  handle = (e) => {{
{BIG_BODY}
  }};
  other = function(a) {{ return a; }};
  static make = async () => 1;
  render() {{ return 1; }}
}}
""".encode()

PY_DOC = b'''class Config:
    """Settings for the app."""
    DEBUG = True

    def go(self):
        pass
'''


def chunks_of(tmp_path, name, source):
    p = tmp_path / name
    p.write_bytes(source)
    return chunk_file(str(p))


def test_js_function_fields_become_methods(tmp_path):
    chunks = chunks_of(tmp_path, "w.js", JS)
    # an oversized method comes back as several chunks that all keep its real symbol
    methods = sorted({c["symbol"] for c in chunks if c["kind"] == "method"})
    assert methods == ["handle", "make", "other", "render"]


def test_big_arrow_field_gives_small_overview_plus_method_chunk(tmp_path):
    chunks = chunks_of(tmp_path, "w.js", JS)
    overview = next(c for c in chunks if c["kind"] == "class_overview")
    assert "this.total" not in overview["text"]
    assert "handle = (e) =>" in overview["text"]       # signature only
    assert "count = 0" in overview["text"] and "#secret = 1" in overview["text"]  # plain fields stay
    assert len(overview["text"].splitlines()) < 15
    handle_parts = [c for c in chunks if c["kind"] == "method" and c["symbol"] == "handle"]
    assert "this.total += 299;" in "\n".join(c["text"] for c in handle_parts)


def test_overview_is_capped_through_finalize_chunk(tmp_path):
    fields = "\n".join(f"  field{i} = 'a long value to pad things out {i}';" for i in range(400))
    chunks = chunks_of(tmp_path, "w.js", f"class Big {{\n{fields}\n  m() {{}}\n}}\n".encode())
    overviews = [c for c in chunks if c["kind"] == "class_overview"]
    assert len(overviews) > 1
    assert all(estimate_tokens(c["text"]) <= MAX_CHUNK_TOKENS for c in overviews)
    # pieces of a synthetic summary all point at the whole class, since they are not source lines
    assert {(c["start_line"], c["end_line"]) for c in overviews} == {(1, 403)}


def test_python_class_docstring_is_in_overview_before_attributes(tmp_path):
    chunks = chunks_of(tmp_path, "c.py", PY_DOC)
    text = next(c for c in chunks if c["kind"] == "class_overview")["text"]
    assert text.index("Settings for the app.") < text.index("DEBUG = True")


def test_js_jsdoc_still_leads_the_overview(tmp_path):
    chunks = chunks_of(tmp_path, "d.js", b"/** Widget docs. */\nclass W {\n  go = () => 1;\n}\n")
    text = next(c for c in chunks if c["kind"] == "class_overview")["text"]
    assert text.startswith("/** Widget docs. */\nclass W\n")
