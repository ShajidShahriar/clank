import logging

import pytest

from chunker import MAX_CHUNK_TOKENS, chunk_file, estimate_tokens

BROKEN_PY = b"import os\n\ndef broken(:\n    return 1\n\ndef fine():\n    return 2\n"
BROKEN_JS = b"function broken( {\n  return 1;\n}\n\nfunction fine() {\n  return 2;\n}\n"


def chunks_of(tmp_path, name, source):
    p = tmp_path / name
    p.write_bytes(source)
    return chunk_file(str(p))


@pytest.mark.parametrize("name,source", [("b.py", BROKEN_PY), ("b.js", BROKEN_JS)])
def test_parse_error_file_becomes_text_fallback(tmp_path, caplog, name, source):
    with caplog.at_level(logging.WARNING, logger="chunker"):
        chunks = chunks_of(tmp_path, name, source)
    assert chunks and all(c["kind"] == "text_fallback" and c["parse_error"] for c in chunks)
    assert chunks[0]["start_line"] == 1
    assert any("parse error" in r.message and name in r.message for r in caplog.records)
    assert "fine" in "\n".join(c["text"] for c in chunks)  # nothing dropped


def test_big_parse_error_file_is_split_into_windows(tmp_path):
    body = "".join(f"value_{i} = {i}\n" for i in range(2000)).encode()
    chunks = chunks_of(tmp_path, "big.py", b"def broken(:\n" + body)
    assert len(chunks) > 1
    assert all(c["parse_error"] for c in chunks)
    assert all(estimate_tokens(c["text"]) <= MAX_CHUNK_TOKENS for c in chunks)
    assert chunks[0]["start_line"] == 1 and chunks[-1]["end_line"] == 2001


def test_clean_file_has_no_parse_error_and_no_warning(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="chunker"):
        chunks = chunks_of(tmp_path, "ok.py", b"def f():\n    return 1\n")
    assert chunks and not any(c["parse_error"] for c in chunks)
    assert not caplog.records


@pytest.mark.parametrize("name", ["e.py", "e.js"])
@pytest.mark.parametrize("source", [b"", b"\n\n  \n", b"\r\n\r\n"])
def test_empty_and_blank_files_return_no_chunks(tmp_path, name, source):
    assert chunks_of(tmp_path, name, source) == []


def test_crlf_never_leaks_a_carriage_return(tmp_path):
    py = b"import os\r\n\r\n# about f\r\ndef f(a,\r\n      b):\r\n    return a\r\n\r\nclass A:\r\n    X = 1\r\n    def m(self,\r\n          z):\r\n        return z\r\n"
    chunks = chunks_of(tmp_path, "w.py", py)
    for c in chunks:
        assert "\r" not in c["text"] and "\r" not in c["embed_text"]
    func = next(c for c in chunks if c["symbol"] == "f")
    assert (func["start_line"], func["end_line"]) == (3, 6)  # same line numbers as the original file
    assert func["text"] == "# about f\ndef f(a,\n      b):\n    return a"
    overview = next(c for c in chunks if c["kind"] == "class_overview")
    assert "def m(self,\n" in overview["text"] and "\r" not in overview["text"]


def test_crlf_in_js_and_text_fallback(tmp_path):
    js = chunks_of(tmp_path, "w.js", b"/** doc */\r\nfunction f() {\r\n  return 1;\r\n}\r\n")
    assert all("\r" not in c["text"] for c in js)
    broken = chunks_of(tmp_path, "x.js", b"function broken( {\r\n  return 1;\r\n}\r\n")
    assert all("\r" not in c["text"] for c in broken)
