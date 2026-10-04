"""index_project: discover -> chunk -> plan -> embed -> vector store -> SQLite -> delete stale, one file at a time.

The order is indexing decision 4, and it is what makes a re-run after any crash converge:
  1. embed (no database transaction is open while the model runs)
  2. write the vectors to the vector store
  3. commit the file's rows (with the model that embedded them) to SQLite  <- the chunk officially exists only now
  4. delete the vectors of chunks that left the file
A crash after step 2 leaves an orphan vector with no row: harmless, search reads rows through SQLite, and the end of a completed run removes it.
A crash after step 3 leaves a stale vector that step 4 would have deleted: also an orphan. Nothing ever leaves a row claiming a vector it does not have.

What goes wrong, and what happens (the policy, decided with the user on 2026-10-03):
- A chunk is too long for the model (`EmbeddingTooLong`), or the chunker fails on a file: THAT FILE is skipped. Nothing of it is written (the embed
  step fails before any write), the reason is stored on its file row, and the run goes on. A flagged file's old chunks, if it had any, stay in the
  database; retrieval hides flagged files. If the skips pass max(3, 5% of the files) the run stops: something systematic is wrong.
- Ollama not reachable, model missing, a garbage answer (any other `EmbeddingError`): the run STOPS, because every file would fail the same way.
  `warmup()` runs first, before anything is changed. The report says where it stopped and how many files were done. No exception is raised.
- A stopped run neither deletes vanished files nor sweeps orphans (only a COMPLETED run may), and finished files stay finished: a re-run continues.
- Errors from the vector store or the database are bugs or a broken disk, not conditions to handle: they are raised.
"""
import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

import chunk_store
import datadir
from chunker import chunk_file
from file_discovery import discover_files
from embedding import EmbeddingError, EmbeddingTooLong

from .embed import embed_chunks
from .fingerprint import chunker_fingerprint
from .lock import project_lock
from .sync import plan_sync
from .tags import classify_file


@dataclass
class Stopped:
    """Why a run ended before the end of the file list."""
    at: str | None     # the file being worked on (it was NOT completed), or None if the run never started
    reason: str
    files_done: int    # files handled before this point: indexed, skipped by the shortcut, or skipped with a recorded reason
    kind: str          # "embedder" (Ollama problem), "too_many_skips" or "cancelled" (the caller asked to stop)


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
    vectors_lost: int = 0        # rows that claimed a vector the store no longer had (they are embedded again this run)
    store_rebuilt: bool = False  # the vector store was cleared because it was built for another model, or for none
    skipped: list = field(default_factory=list)   # [(rel_path, reason)] files skipped (a chunk too long, or the chunker failed); also stored on the file row
    skipped_chunk_ids: dict = field(default_factory=dict)   # {rel_path: [chunk ids]} for files skipped because a chunk was too long
    new_skips: int = 0           # skipped files that were NOT already flagged by an earlier run: only these count toward the skip limit
    skip_limit: float = 0.0
    skip_limit_exceeded: bool = False   # new failures passed the limit (the run stops, unless that happened on the very last file)
    stopped: Stopped | None = None
    seconds: float = 0.0

    def describe(self) -> str:
        """The result in one sentence, for the UI or a log."""
        skipped = f"; {len(self.skipped)} skipped: {', '.join(rel for rel, _ in self.skipped)}" if self.skipped else ""
        if self.stopped is None:
            warning = f"; {self.new_skips} new failures, more than the limit of {self.skip_limit:g}: check the model's context size" if self.skip_limit_exceeded else ""
            return f"done: {self.files_seen} files, {self.embedded} chunks embedded{skipped}{warning}"
        if self.stopped.at is None:
            return f"stopped before the first file: {self.stopped.reason}"
        return f"stopped at {self.stopped.at} after {self.stopped.files_done} of {self.files_seen} files: {self.stopped.reason}{skipped}"


def index_project(conn, project_id, repo_path, embedder, store, *, progress=None, lock_dir=None, cancel=None, on_start=None) -> IndexReport:
    """Bring SQLite and the vector store in line with the repo on disk. Safe to run again after any interruption.

    `on_start(total)` is called once, after the files are found and before the first one is handled. `progress(done, total, rel_path)` is called after each file. `cancel()` is asked BEFORE each file: when it says yes the run stops cleanly
    (`report.stopped.kind == "cancelled"`; like any stopped run it deletes and sweeps nothing). Only one run per project at a time: a second call
    raises `IndexAlreadyRunning` at once, before doing any work.
    """
    with project_lock(lock_dir if lock_dir is not None else datadir.lock_dir(), project_id):
        return _index_project(conn, project_id, repo_path, embedder, store, progress, cancel, on_start)


def _index_project(conn, project_id, repo_path, embedder, store, progress, cancel=None, on_start=None) -> IndexReport:
    started = time.monotonic()
    root = Path(repo_path).resolve()
    report = IndexReport()

    try:
        embedder.warmup()   # first, so a model we cannot reach changes NOTHING (not even the vector store's signature)
    except EmbeddingError as problem:
        report.stopped = Stopped(None, _why(problem), 0, "embedder")
        report.seconds = time.monotonic() - started
        return report

    model, dim = embedder.model_name, embedder.dim   # AFTER warmup: only then does the embedder know its full identity (tag + digest)
    _match_store_to_model(conn, project_id, store, model, dim, report)
    # The other direction of drift: a row that says "embedded" while its vector is gone (a damaged or half-deleted store). The end-of-run
    # sweep only finds vectors without rows. Fixed HERE, before the file shortcut looks at the rows, so this same run repairs them.
    report.vectors_lost = chunk_store.forget_embeddings_for(conn, project_id, chunk_store.all_ids(conn, project_id) - store.ids())

    files = sorted(discover_files(str(root)), key=lambda p: p.relative_to(root).as_posix())
    report.files_seen = len(files)
    if on_start:
        on_start(len(files))
    stored_files = chunk_store.file_hashes(conn, project_id)
    states = chunk_store.file_states(conn, project_id)
    unfinished = chunk_store.files_needing_embedding(conn, project_id, model, dim)
    flagged = set(chunk_store.failed_files(conn, project_id))   # a flagged file is always tried again, or its flag could never clear
    version = chunker_fingerprint()
    # A file that failed last time is a KNOWN problem: try it after everything else, so a few permanently bad files can never keep the healthy
    # majority from being indexed. (On first contact nothing is flagged yet, so the first run can still stop early; the next one then completes.)
    files.sort(key=lambda p: (p.relative_to(root).as_posix() in flagged, p.relative_to(root).as_posix()))
    skip_limit = report.skip_limit = max(3, 0.05 * len(files))
    handled = 0

    for path in files:
        rel = path.relative_to(root).as_posix()
        if cancel is not None and cancel():
            report.stopped = Stopped(rel, "cancelled by the user", handled, "cancelled")
            break
        file_hash = ""
        skipped_before = len(report.skipped)
        try:
            file_hash = hashlib.sha1(path.read_bytes()).hexdigest()
            if states.get(rel) == (file_hash, version) and rel not in unfinished and rel not in flagged:
                chunks = None   # nothing to do: same bytes, same chunker, and every chunk of the file already has its vector
            else:
                chunks = chunk_file(str(path), repo_root=str(root))
        except Exception as problem:  # deliberately broad: a bug in the chunker on ONE file must not stop the whole run
            _skip(conn, project_id, rel, file_hash, version, problem, report)
        else:
            if chunks is None:
                report.files_unchanged += 1
            else:
                try:
                    _sync_file(conn, project_id, rel, file_hash, chunks, version, embedder, store, model, dim, report)
                except EmbeddingTooLong as problem:   # raised by the embed step, before anything of this file is written
                    _skip(conn, project_id, rel, file_hash, version, problem, report)
                except EmbeddingError as problem:     # Ollama down, model missing, garbage answer: every file would fail the same way
                    report.stopped = Stopped(rel, _why(problem), handled, "embedder")
                    break
        handled += 1
        if len(report.skipped) > skipped_before and rel not in flagged:
            report.new_skips += 1      # only NEW failures say "something systematic is wrong": a known bad file is not new evidence
        if progress:
            progress(handled, len(files), rel)
        if report.new_skips > skip_limit:
            report.skip_limit_exceeded = True
            if handled < len(files):   # on the very last file there is nothing left to save, so the run is complete (and does its cleanup)
                report.stopped = Stopped(
                    rel, f"{report.new_skips} files skipped (all new failures), more than the limit of {skip_limit:g}; run again to continue: "
                         f"files that failed are tried last next time", handled, "too_many_skips")
                break

    if report.stopped is None:   # only a COMPLETED run may delete vanished files and call a vector an orphan: after a stop, the next run decides
        on_disk = {p.relative_to(root).as_posix() for p in files}
        for rel in sorted(set(stored_files) - on_disk):
            removed = chunk_store.delete_file(conn, project_id, rel)   # rows first ...
            if removed:
                store.delete(list(removed))                             # ... then their vectors
            report.deleted_files += 1
            report.deleted_chunks += len(removed)

        orphans = store.ids() - chunk_store.all_ids(conn, project_id)
        if orphans:
            store.delete(list(orphans))
            report.orphans_removed = len(orphans)

    report.seconds = time.monotonic() - started
    return report


def _why(problem: Exception) -> str:
    return f"{type(problem).__name__}: {problem}"


def _skip(conn, project_id, rel, file_hash, version, problem, report):
    """This file could not be indexed: say why on its row and in the report, and go on."""
    reason = _why(problem)
    tags = classify_file(rel)
    chunk_store.mark_file_failed(conn, project_id, rel, reason, file_hash=file_hash, chunker_version=version,
                                 language=tags.language, is_test=tags.is_test, is_changelog=tags.is_changelog)
    report.skipped.append((rel, reason))
    if isinstance(problem, EmbeddingTooLong):
        report.skipped_chunk_ids[rel] = list(problem.chunk_ids)


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
