"""Task I-5.4: kill the pipeline at EVERY step, run it again, and the result must equal a clean run. This is the proof behind indexing decision 4.

For each start state (a first run, an update, a file that lost a function, a model switch, a damaged store, a skipped file):
  1. run it cleanly once, recording the final state (every row, every file row, every vector, the store's signature) and counting the steps N;
  2. for each k in 0..N-1: build the same start state, kill the run at step k, run it again, and compare with the clean state;
  3. run once more: nothing may change, nothing may be embedded (a recovered index is a settled index).
A step is an embed call, a vector write, a row commit, a delete or a signature change, each counted just before and just after it happens.
"""
import pytest

import chunk_store
import db
from crash_hooks import Crash, HookedEmbedder, HookedStore, Hooks, install
from indexing import index_project
from vectorstore import InMemoryVectorStore

BIG = "def big():\n    return '" + "x" * 400 + "'\n"

FILES = {
    "a.py": "import os\n\n\ndef first():\n    return 1\n\n\ndef second():\n    return 2\n",
    "b.py": "def helper():\n    return 'b'\n",
    "c.js": "function render() {\n  return 1;\n}\n",
    "README.md": "# Shop\n\nA tiny shop.\n\n## Usage\n\nRun it.\n",
}


class World:
    """One project: a repo folder, a SQLite file and a vector store, in a given start state."""

    def __init__(self, base, monkeypatch, scenario):
        self.scenario, self.base = scenario, base
        self.repo = base / "repo"
        self.repo.mkdir(parents=True)
        for name, text in FILES.items():
            (self.repo / name).write_text(text)
        monkeypatch.setattr(db, "DB_PATH", str(base / "app.db"))
        db.init_db()
        conn = db.get_connection()
        conn.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('p', ?, 'now')", (str(self.repo),))
        conn.commit()
        conn.close()
        self.store = InMemoryVectorStore()
        self.embedder_args = {}
        getattr(self, f"prepare_{scenario}")()

    def index(self, hooks=None, **embedder_args):
        hooks = hooks or Hooks()
        conn = db.get_connection()                       # a fresh connection each time, like a new process
        undo = install(hooks)
        try:
            embedder = HookedEmbedder(hooks, **{**self.embedder_args, **embedder_args})
            return index_project(conn, 1, self.repo, embedder, HookedStore(self.store, hooks)), embedder
        finally:
            undo()
            conn.close()

    # --- start states (prepared WITHOUT crashes, the action is what gets killed) ---
    def prepare_first_run(self):
        pass

    def prepare_update(self):
        self.index()
        (self.repo / "a.py").write_text(FILES["a.py"].replace("return 2", "return 22"))      # an edit
        (self.repo / "d.py").write_text("def added():\n    return 'd'\n")                      # a new file
        (self.repo / "b.py").unlink()                                                         # a vanished file
        (self.repo / "README.md").write_text("\n\n" + FILES["README.md"])                     # lines moved, nothing to embed

    def prepare_shrunk_file(self):
        self.index()
        (self.repo / "a.py").write_text("import os\n\n\ndef first():\n    return 1\n")    # a surviving file LOSES a function: its vector must go, the file stays

    def prepare_model_switch(self):
        self.index(digest="weights-1")
        self.embedder_args = {"digest": "weights-2"}                                          # the action runs with replaced weights

    def prepare_damaged_store(self):
        self.index()
        ids = sorted(self.store.ids())
        self.store.delete(ids[:2])                                                            # two vectors lost
        self.store.upsert(["stray-vector"], [[1.0] * 8])                                      # one with no row
        (self.repo / "c.js").write_text("function render() {\n  return 2;\n}\n")

    def prepare_skipped_file(self):
        self.index()
        (self.repo / "b.py").write_text(BIG)                                                  # a good file becomes too long
        (self.repo / "e.py").write_text(BIG)                                                  # and a new file is too long
        self.embedder_args = {"max_chars": 300}


SCENARIOS = ["first_run", "update", "shrunk_file", "model_switch", "damaged_store", "skipped_file"]


def snapshot(store):
    """Everything that should be identical after a crash and a re-run as after a clean run."""
    conn = db.get_connection()
    try:
        rows = [tuple(r) for r in conn.execute(
            "SELECT id, rel_path, content_hash, kind, symbol, parent, part, part_count, start_line, end_line, text, embed_text, "
            "embed_model, embed_dim FROM chunks ORDER BY rel_path, id")]
        files = [tuple(r) for r in conn.execute(
            "SELECT rel_path, hash, is_test, is_changelog, language, chunker_version, index_error FROM files ORDER BY rel_path")]
        ids = chunk_store.all_ids(conn, 1)
        claimed = {r[0] for r in conn.execute("SELECT id FROM chunks WHERE embed_model IS NOT NULL")}   # rows that say "my vector is in the store"
    finally:
        conn.close()
    inner = store.inner if isinstance(store, HookedStore) else store
    return {"rows": rows, "files": files, "vectors": {i: tuple(v) for i, v in inner._vectors.items()},
            "signature": inner.signature(), "row_ids": ids, "claimed": claimed}


def assert_consistent(state):
    assert set(state["vectors"]) == state["row_ids"], "vector ids and row ids differ"


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_killed_at_every_step_then_run_again_equals_a_clean_run(scenario, tmp_path, monkeypatch):
    clean_world = World(tmp_path / "clean", monkeypatch, scenario)
    # (the damaged-store start state has claims without vectors on purpose; a kill must never ADD to them)
    hooks = Hooks()
    clean_report, _ = clean_world.index(hooks)
    reference, steps = snapshot(clean_world.store), len(hooks.labels)
    assert steps >= 4 and clean_report.stopped is None
    assert_consistent(reference)

    for k in range(steps):
        world = World(tmp_path / f"k{k}", monkeypatch, scenario)
        start = snapshot(world.store)
        with pytest.raises(Crash):
            world.index(Hooks(crash_at=k))
        mid = snapshot(world.store)                     # what a restarted program would find on disk
        # The dangerous direction: a row that CLAIMS a vector (it has a model record) must have one. A kill may leave an orphan vector,
        # or rows that honestly say "never embedded", but it must never create a new claim without a vector.
        new_lies = (mid["claimed"] - set(mid["vectors"])) - (start["claimed"] - set(start["vectors"]))
        assert not new_lies, f"after a kill at step {k} ({hooks.labels[k]}) rows claim vectors that are not there: {sorted(new_lies)}"

        recovered, _ = world.index()
        state = snapshot(world.store)
        assert state == reference, f"scenario {scenario}: kill at step {k} ({hooks.labels[k]}), then a re-run, differs from a clean run"
        assert recovered.stopped is None

        again, embedder = world.index()
        assert embedder.text_count == 0 and again.embedded == 0 and snapshot(world.store) == state, \
            f"scenario {scenario}: the re-run after a kill at step {k} ({hooks.labels[k]}) was not settled"


def test_the_matrix_really_covers_every_kind_of_step(tmp_path, monkeypatch):
    # If a future change stops reporting a kind of step, the matrix would quietly test less. These are the kinds it must reach.
    seen = set()
    for scenario in SCENARIOS:
        hooks = Hooks()
        World(tmp_path / scenario, monkeypatch, scenario).index(hooks)
        seen |= {label.split(":")[0] for label in hooks.labels}
    assert seen >= {"embedder.warmup", "embedder.embed", "store.upsert", "store.delete", "store.clear", "store.set_signature",
                    "chunk_store.save_file_chunks", "chunk_store.delete_file", "chunk_store.forget_embeddings",
                    "chunk_store.forget_embeddings_for", "chunk_store.mark_file_failed"}


def test_the_hook_that_kills_cannot_be_swallowed_by_a_broad_except(tmp_path, monkeypatch):
    world = World(tmp_path / "w", monkeypatch, "first_run")
    with pytest.raises(Crash):
        world.index(Hooks(crash_at=5))
