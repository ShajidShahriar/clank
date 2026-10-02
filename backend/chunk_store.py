"""Save and read chunks in SQLite. SQLite is the truth about a chunk; Chroma only holds vectors.

Every function takes an open connection and a project_id. Chunk ids hash the file path, not the
project, so two projects can hold the same id: always look chunks up with both.
"""
import json
import sqlite3

# Fields that are the same in the chunk dict and in the table (see the chunk format in the handoff).
PLAIN_FIELDS = (
    "id", "content_hash", "ordinal", "kind", "symbol", "parent", "part", "part_count",
    "file_path", "rel_path", "start_line", "end_line", "text", "embed_text",
)
BOOL_FIELDS = ("synthetic", "parse_error")  # stored as 0/1, read back as bool
STORED_ONLY_FIELDS = ("embed_model", "embed_dim")  # set when a vector exists, not part of a chunk

_ROW_COLUMNS = PLAIN_FIELDS + ("names",) + BOOL_FIELDS
_SQL_BATCH = 500  # stay well under SQLite's limit on `?` placeholders


def _query(conn, sql, params=()):
    """Run a query and get sqlite3.Row results, whatever row_factory the caller's connection has.
    (A cursor's own row_factory leaves the connection's setting alone.)"""
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    return cur.execute(sql, params)


def _to_row(project_id, chunk):
    values = [chunk[f] for f in PLAIN_FIELDS]
    values.append(json.dumps(chunk["names"]))
    values.extend(int(chunk[f]) for f in BOOL_FIELDS)
    return (project_id, *values)


def _from_row(row):
    chunk = {f: row[f] for f in PLAIN_FIELDS}
    chunk["names"] = json.loads(row["names"])
    for f in BOOL_FIELDS:
        chunk[f] = bool(row[f])
    for f in STORED_ONLY_FIELDS:
        chunk[f] = row[f]
    return chunk


_INSERT = (
    f"INSERT INTO chunks (project_id, {', '.join(_ROW_COLUMNS)}) "
    f"VALUES ({', '.join('?' * (len(_ROW_COLUMNS) + 1))}) "
    "ON CONFLICT (project_id, id) DO UPDATE SET "
    + ", ".join(f"{c} = excluded.{c}" for c in _ROW_COLUMNS if c != "id")
    # The vector no longer matches if the embedded content changed, so clear the record. In DO UPDATE every
    # right-hand side reads the OLD row, so `content_hash` here is the stored one even though the same SET list
    # also overwrites it. (Tested: test_changed_chunk_loses_its_embedding_record.)
    + ", embed_model = CASE WHEN content_hash = excluded.content_hash THEN embed_model END"
    + ", embed_dim = CASE WHEN content_hash = excluded.content_hash THEN embed_dim END"
)


def _check_embedded(embedded, chunk_ids):
    """Refuse a bad {chunk_id: (model, dim)} before anything is written."""
    for chunk_id, record in embedded.items():
        if chunk_id not in chunk_ids:
            raise ValueError(f"embedded record for {chunk_id}, which is not one of the chunks being saved")
        model, dim = record
        if not isinstance(model, str) or not model:
            raise ValueError(f"embedded record for {chunk_id}: model must be a non-empty string, got {model!r}")
        if not isinstance(dim, int) or isinstance(dim, bool) or dim < 1:
            raise ValueError(f"embedded record for {chunk_id}: dim must be a positive integer, got {dim!r}")


def save_file_chunks(conn, project_id, rel_path, file_hash, chunks, *,
                     language=None, is_test=False, is_changelog=False, embedded=None, chunker_version=None):
    """Make the file's rows match `chunks`, in one transaction (all or nothing).

    Chunks already stored are updated in place and keep their embed_model / embed_dim unless their
    content_hash changed. Rows for chunks that are no longer in `chunks` are deleted, and their ids
    are returned (the indexer must delete the same ids from Chroma).

    `embedded` is {chunk_id: (model_name, dim)} for chunks whose vector is already in Chroma. It is written in the
    same transaction as the rows, so a row either says "embedded by this model" or it still needs embedding.

    `chunker_version` (the chunker fingerprint) is stored with the file so a later run can tell whether the same file
    bytes would still chunk the same way.
    """
    embedded = embedded or {}
    ids = [c["id"] for c in chunks]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"duplicate chunk ids in one save for {rel_path}: {dupes}")
    _check_embedded(embedded, set(ids))
    with conn:
        conn.execute(
            "INSERT INTO files (project_id, rel_path, hash, is_test, is_changelog, language, chunker_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (project_id, rel_path) DO UPDATE SET "
            "hash = excluded.hash, is_test = excluded.is_test, "
            "is_changelog = excluded.is_changelog, language = excluded.language, "
            "chunker_version = excluded.chunker_version",
            (project_id, rel_path, file_hash, int(is_test), int(is_changelog), language, chunker_version),
        )
        old_ids = ids_for_file(conn, project_id, rel_path)
        conn.executemany(_INSERT, [_to_row(project_id, c) for c in chunks])
        conn.executemany(
            "UPDATE chunks SET embed_model = ?, embed_dim = ? WHERE project_id = ? AND id = ?",
            [(model, dim, project_id, chunk_id) for chunk_id, (model, dim) in embedded.items()])
        gone = old_ids - {c["id"] for c in chunks}
        conn.executemany("DELETE FROM chunks WHERE project_id = ? AND id = ?", [(project_id, i) for i in gone])
    return gone


def get_chunks(conn, project_id, ids):
    """Rows for `ids`, in the order asked. Unknown ids are skipped (an orphan vector has no row)."""
    ids = list(dict.fromkeys(ids))
    found = {}
    for start in range(0, len(ids), _SQL_BATCH):
        batch = ids[start:start + _SQL_BATCH]
        marks = ", ".join("?" * len(batch))
        for row in _query(conn, f"SELECT * FROM chunks WHERE project_id = ? AND id IN ({marks})", (project_id, *batch)):
            found[row["id"]] = _from_row(row)
    return [found[i] for i in ids if i in found]


def get_siblings(conn, project_id, chunk_id):
    """All parts of the same split chunk, in part order. A whole chunk returns just itself; unknown id returns []."""
    me = _query(conn, "SELECT * FROM chunks WHERE project_id = ? AND id = ?", (project_id, chunk_id)).fetchone()
    if me is None:
        return []
    if me["part"] is None:
        # Only split pieces have siblings. Two whole chunks can share kind/symbol/parent/ordinal
        # (two `group` chunks in one file do), so the key below would wrongly pair them.
        return [_from_row(me)]
    rows = _query(
        conn,
        # IS, not =, so a NULL parent or symbol matches a NULL
        "SELECT * FROM chunks WHERE project_id = ? AND rel_path = ? AND kind = ? "
        "AND parent IS ? AND symbol IS ? AND ordinal IS ? ORDER BY part",
        (project_id, me["rel_path"], me["kind"], me["parent"], me["symbol"], me["ordinal"]),
    )
    return [_from_row(r) for r in rows]


def needs_embedding(conn, project_id, model, dim):
    """Ids of chunks with no vector record for THIS model and dimension, in file and line order.

    Includes rows saved without a record and rows embedded by another model or size. "Same content_hash"
    is not enough to call a chunk done; this is the question that decides.
    """
    rows = _query(
        conn,
        # IS NOT, not <>, so a NULL (never embedded) counts as different
        "SELECT id FROM chunks WHERE project_id = ? AND (embed_model IS NOT ? OR embed_dim IS NOT ?) "
        "ORDER BY rel_path, start_line, COALESCE(part, 0), id",
        (project_id, model, dim))
    return [r["id"] for r in rows]


def file_hashes(conn, project_id):
    """{rel_path: file hash} for every file stored for the project (a file with no chunks has a row too)."""
    rows = _query(conn, "SELECT rel_path, hash FROM files WHERE project_id = ?", (project_id,))
    return {r["rel_path"]: r["hash"] for r in rows}


def file_states(conn, project_id):
    """{rel_path: (file hash, chunker version)} for every stored file. The version is None if it was never recorded."""
    rows = _query(conn, "SELECT rel_path, hash, chunker_version FROM files WHERE project_id = ?", (project_id,))
    return {r["rel_path"]: (r["hash"], r["chunker_version"]) for r in rows}


def files_needing_embedding(conn, project_id, model, dim):
    """rel_paths of files with at least one chunk that has no vector record for this model and dimension."""
    rows = _query(conn, "SELECT DISTINCT rel_path FROM chunks WHERE project_id = ? AND (embed_model IS NOT ? OR embed_dim IS NOT ?)",
                  (project_id, model, dim))
    return {r["rel_path"] for r in rows}


def chunks_for_file(conn, project_id, rel_path):
    """The stored rows of one file, in line order."""
    rows = _query(conn, "SELECT * FROM chunks WHERE project_id = ? AND rel_path = ? ORDER BY start_line, COALESCE(part, 0), id",
                  (project_id, rel_path))
    return [_from_row(r) for r in rows]


def models_in_use(conn, project_id):
    """The distinct (model, dim) records on the project's rows. Rows that were never embedded have none."""
    rows = _query(conn, "SELECT DISTINCT embed_model, embed_dim FROM chunks WHERE project_id = ? "
                        "AND embed_model IS NOT NULL AND embed_dim IS NOT NULL", (project_id,))
    return {(r["embed_model"], r["embed_dim"]) for r in rows}


def forget_embeddings(conn, project_id):
    """Mark every row of the project as never embedded (the vector store is about to be rebuilt). Returns how many rows."""
    with conn:
        return conn.execute("UPDATE chunks SET embed_model = NULL, embed_dim = NULL WHERE project_id = ? "
                            "AND (embed_model IS NOT NULL OR embed_dim IS NOT NULL)", (project_id,)).rowcount


def ids_for_file(conn, project_id, rel_path):
    rows = _query(conn, "SELECT id FROM chunks WHERE project_id = ? AND rel_path = ?", (project_id, rel_path))
    return {r["id"] for r in rows}


def all_ids(conn, project_id):
    return {r["id"] for r in _query(conn, "SELECT id FROM chunks WHERE project_id = ?", (project_id,))}


def delete_file(conn, project_id, rel_path):
    """Delete the file's chunks, then the file row. Returns the removed chunk ids. Safe to call twice.

    The chunks are deleted explicitly, not only through ON DELETE CASCADE: SQLite's foreign-key
    enforcement is off by default on every new connection, and a connection opened without the
    pragma would otherwise leave the chunks behind (search would keep returning deleted code).
    """
    with conn:
        removed = ids_for_file(conn, project_id, rel_path)
        conn.execute("DELETE FROM chunks WHERE project_id = ? AND rel_path = ?", (project_id, rel_path))
        conn.execute("DELETE FROM files WHERE project_id = ? AND rel_path = ?", (project_id, rel_path))
    return removed
