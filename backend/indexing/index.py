"""index_project: discover -> chunk -> plan -> embed -> vector store -> SQLite -> delete stale, one file at a time.

The order is indexing decision 4, and it is what makes a re-run after any crash converge:
  1. embed (no database transaction is open while the model runs)
  2. write the vectors to the vector store
  3. commit the file's rows (with the model that embedded them) to SQLite  <- the chunk officially exists only now
  4. delete the vectors of chunks that left the file
A crash after step 2 leaves an orphan vector with no row: harmless, search reads rows through SQLite, and the end of a completed run removes it.
A crash after step 3 leaves a stale vector that step 4 would have deleted: also an orphan. Nothing ever leaves a row claiming a vector it does not have.

Errors from the embedder (`EmbeddingError` and its subclasses) are NOT caught: they stop the run, the files already done stay done, and a re-run
continues. Which errors should retry or skip instead is the failure-handling step (I-5).
"""
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

import chunk_store
import datadir
from chunker import chunk_file
from file_discovery import discover_files

from .embed import embed_chunks
from .fingerprint import chunker_fingerprint
from .lock import project_lock
from .sync import plan_sync
from .tags import classify_file


@dataclass
class IndexReport:
    files_seen: int = 0
    files_written: int = 0       # files that were chunked and saved
    files_unchanged: int = 0     # files skipped without chunking: same bytes, same chunker, every chunk already embedded
    new: int = 0                 # chunk counts, summed over the files
    changed: int = 0
    needs_vector: int = 0
    moved: int = 0
    unchanged: int = 0
    embedded: int = 0            # chunks sent to the embedder = new + changed + needs_vector
    deleted_chunks: int = 0
    deleted_files: int = 0
    orphans_removed: int = 0
    store_rebuilt: bool = False  # the vector store was cleared because it was built for another model, or for none
    skipped: list = field(default_factory=list)   # [(rel_path, reason)] files the chunker could not handle
    seconds: float = 0.0


def index_project(conn, project_id, repo_path, embedder, store, *, progress=None, lock_dir=None) -> IndexReport:
    """Bring SQLite and the vector store in line with the repo on disk. Safe to run again after any interruption.

    `progress(done, total, rel_path)` is called after each file. Only one run per project at a time: a second call raises
    `IndexAlreadyRunning` at once, before doing any work.
    """
    with project_lock(lock_dir if lock_dir is not None else datadir.lock_dir(), project_id):
        return _index_project(conn, project_id, repo_path, embedder, store, progress)


def _index_project(conn, project_id, repo_path, embedder, store, progress) -> IndexReport:
    started = time.monotonic()
    root = Path(repo_path).resolve()
    model, dim = embedder.model_name, embedder.dim
    report = IndexReport()

    _match_store_to_model(conn, project_id, store, model, dim, report)

    files = sorted(discover_files(str(root)), key=lambda p: p.relative_to(root).as_posix())
    report.files_seen = len(files)
    stored_files = chunk_store.file_hashes(conn, project_id)
    states = chunk_store.file_states(conn, project_id)
    unfinished = chunk_store.files_needing_embedding(conn, project_id, model, dim)
    version = chunker_fingerprint()

    for done, path in enumerate(files, start=1):
        rel = path.relative_to(root).as_posix()
        try:
            file_hash = hashlib.sha1(path.read_bytes()).hexdigest()
            if states.get(rel) == (file_hash, version) and rel not in unfinished:
                chunks = None   # nothing to do: same bytes, same chunker, and every chunk of the file already has its vector
            else:
                chunks = chunk_file(str(path), repo_root=str(root))
        except Exception as problem:  # deliberately broad: a bug in the chunker on ONE file must not stop the whole run
            report.skipped.append((rel, f"{type(problem).__name__}: {problem}"))
        else:
            if chunks is None:
                report.files_unchanged += 1
            else:
                # not guarded: an embedding, vector store or database error stops the run (see the module docstring)
                _sync_file(conn, project_id, rel, file_hash, chunks, version, embedder, store, model, dim, report)
        if progress:
            progress(done, len(files), rel)

    on_disk = {p.relative_to(root).as_posix() for p in files}
    for rel in sorted(set(stored_files) - on_disk):
        removed = chunk_store.delete_file(conn, project_id, rel)   # rows first ...
        if removed:
            store.delete(list(removed))                             # ... then their vectors
        report.deleted_files += 1
        report.deleted_chunks += len(removed)

    # Only a COMPLETED run may call a vector an orphan: after a stop, the next run decides.
    orphans = store.ids() - chunk_store.all_ids(conn, project_id)
    if orphans:
        store.delete(list(orphans))
        report.orphans_removed = len(orphans)

    report.seconds = time.monotonic() - started
    return report


def _match_store_to_model(conn, project_id, store, model, dim, report):
    """A vector store is only meaningful for the model that filled it. If it was built for another one (or for none),
    rebuild it. Every step is safe to repeat, so a crash between them just repeats them on the next run."""
    if store.signature() == (model, dim):
        return
    forgotten = chunk_store.forget_embeddings(conn, project_id)   # 1. rows: "never embedded"
    had_vectors = store.count() > 0
    store.clear()                                                 # 2. drop the old model's vectors
    store.set_signature(model, dim)                               # 3. stamp the store for the new model
    report.store_rebuilt = bool(forgotten or had_vectors)


def _sync_file(conn, project_id, rel, file_hash, chunks, version, embedder, store, model, dim, report):
    plan = plan_sync(chunk_store.chunks_for_file(conn, project_id, rel), chunks, model, dim)

    vectors = embed_chunks(embedder, plan.to_embed)          # 1. embed: no transaction is open
    if vectors:
        store.upsert(list(vectors), list(vectors.values()))  # 2. vectors first ...
    tags = classify_file(rel)
    removed = chunk_store.save_file_chunks(                  # 3. ... then the rows, with the model that embedded them
        conn, project_id, rel, file_hash, chunks, embedded={i: (model, dim) for i in vectors}, chunker_version=version,
        language=tags.language, is_test=tags.is_test, is_changelog=tags.is_changelog)
    if removed:
        store.delete(list(removed))                          # 4. stale vectors last

    report.files_written += 1
    report.new += len(plan.new)
    report.changed += len(plan.changed)
    report.needs_vector += len(plan.needs_vector)
    report.moved += len(plan.moved)
    report.unchanged += len(plan.unchanged)
    report.embedded += len(plan.to_embed)
    report.deleted_chunks += len(removed)
