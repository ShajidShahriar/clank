"""Top-level code that is not a def/class comes out as "imports" or "module_code" runs."""
from chunker import chunk_file

PY = b'''# helpers
import os
from pathlib import Path

# the limit
MAX = 3
DEBUG = True

def f():
    pass

import late
X = 1
'''

JS = b"""import fs from 'fs';
const path = require('path');
require('dotenv');

const PORT = 3000;
module.exports = { PORT };

function f() {}
"""

MIXED = b"""const a = require('a'), b = 5;
"""


def run(tmp_path, name, source):
    p = tmp_path / name
    p.write_bytes(source)
    return [(c["kind"], c["start_line"], c["end_line"]) for c in chunk_file(str(p))]


def test_python_runs_split_by_kind_and_by_defs(tmp_path):
    assert run(tmp_path, "a.py", PY) == [
        ("imports", 1, 3),       # leading comment stays with the imports it sits above
        ("module_code", 5, 7),   # "# the limit" moves down with the code it describes
        ("function", 9, 10),
        ("imports", 12, 12),
        ("module_code", 13, 13),
    ]


def test_js_import_and_require_are_imports(tmp_path):
    assert run(tmp_path, "a.js", JS) == [
        ("imports", 1, 3),
        ("module_code", 5, 6),
        ("function", 8, 8),
    ]


def test_require_mixed_with_other_declarators_is_not_an_import(tmp_path):
    assert run(tmp_path, "m.js", MIXED) == [("module_code", 1, 1)]


def test_comment_only_file_is_module_code(tmp_path):
    assert run(tmp_path, "c.py", b"# just a note\n") == [("module_code", 1, 1)]
