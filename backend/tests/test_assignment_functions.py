from chunker import chunk_file

SRC = """'use strict';
var path = require('path');

/**
 * Send a response.
 */
res.send = function send(body) {
  return this.end(body);
};

app.use = function use(fn) { return this; };
exports.render = function (view) { return view; };
module.exports.helper = (a) => a + 1;
Foo.prototype.bar = function () {
  return 1;
};
config.limit = 5;
module.exports = { foo: 1 };
"""


def chunks_of(tmp_path, source=SRC):
    p = tmp_path / "x.js"
    p.write_text(source)
    return chunk_file(str(p))


def test_assigned_functions_are_named_function_chunks(tmp_path):
    names = [c["symbol"] for c in chunks_of(tmp_path) if c["kind"] == "function"]
    assert names == ["res.send", "app.use", "exports.render", "module.exports.helper", "Foo.prototype.bar"]


def test_assigned_function_chunk_has_jsdoc_and_whole_body(tmp_path):
    send = next(c for c in chunks_of(tmp_path) if c["symbol"] == "res.send")
    assert send["text"].startswith("/**\n * Send a response.")
    assert "return this.end(body);" in send["text"] and send["text"].endswith("};")
    assert send["embed_text"].startswith("x.js · res.send\n")
    assert send["parent"] is None


def test_assignments_of_plain_values_stay_module_code(tmp_path):
    chunks = chunks_of(tmp_path)
    rest = [c for c in chunks if c["kind"] == "module_code"]
    assert any("config.limit = 5;" in c["text"] for c in rest)
    assert any("module.exports = { foo: 1 };" in c["text"] for c in rest)
    assert not any(c["kind"] == "function" and "config" in c["symbol"] for c in chunks)


def test_edge_fixture_is_now_all_named(tmp_path):
    import pathlib
    fixture = pathlib.Path(__file__).parent / "fixtures" / "edge" / "express_style.js"
    kinds = [(c["kind"], c["symbol"]) for c in chunk_file(str(fixture))]
    assert [k for k in kinds if k[0] == "function"] == [
        ("function", "res.send"), ("function", "app.use"),
        ("function", "exports.render"), ("function", "module.exports.helper"),
    ]
