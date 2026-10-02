"""The database must refuse bad chunk rows itself (task I-2b), not rely on the chunker being right."""
import sqlite3

import pytest

GOOD = dict(
    id="c1", project_id=1, rel_path="a.py", content_hash="h", kind="function",
    start_line=1, end_line=2, text="t", embed_text="e", part=None, part_count=None,
)


@pytest.fixture(autouse=True)
def a_file(conn):
    conn.execute("INSERT INTO files (project_id, rel_path, hash) VALUES (1, 'a.py', 'h')")
    conn.commit()


def insert(conn, **overrides):
    row = {**GOOD, **overrides}
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO chunks ({cols}) VALUES ({marks})", list(row.values()))


def test_a_good_row_is_accepted(conn):
    insert(conn)
    insert(conn, id="c2", part=0, part_count=3)
    insert(conn, id="c3", start_line=5, end_line=5)  # one-line chunk: start == end is fine
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 3


@pytest.mark.parametrize("bad", [
    dict(start_line=0),                      # lines are 1-indexed
    dict(start_line=-4),
    dict(start_line=5, end_line=4),          # start after end
    dict(start_line=None),                   # missing line
    dict(end_line=None),
    dict(text=""),                           # empty text
    dict(embed_text=""),
    dict(part=3, part_count=3),              # part is 0-based, so 3 of 3 is out of range
    dict(part=-1, part_count=3),
    dict(part=0, part_count=0),
    dict(part=0, part_count=None),           # a part needs a count
    dict(part=None, part_count=3),           # and a count needs a part
    dict(synthetic=2),                       # flags are 0 or 1
    dict(parse_error=-1),
    dict(kind=""),
    dict(content_hash=""),
], ids=lambda d: ",".join(f"{k}={v!r}" for k, v in d.items()))
def test_database_refuses_bad_row(conn, bad):
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn, **bad)


def test_duplicate_id_in_one_project_is_refused(conn):
    insert(conn)
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn)


def test_chunk_with_no_file_row_is_refused(conn):
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn, rel_path="missing.py")


def test_names_cannot_be_null(conn):
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn, names=None)
