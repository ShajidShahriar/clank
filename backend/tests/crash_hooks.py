"""Tools to kill the indexing pipeline at an exact step, for the crash tests.

Every step that changes something (an embed call, a vector write, a row commit, a delete, a signature change) calls `hooks.tick(label)` just
before and just after it. `Hooks(crash_at=k)` dies at the k-th tick:
- soft: raises `Crash`, a BaseException, so no `except Exception` in the code under test can swallow it (a real kill cannot be caught either);
- hard: `os._exit(137)`, no cleanup at all, like `kill -9` (used by the subprocess test).
Running once without a crash gives the number of ticks N; the matrix then crashes at each of 0..N-1.
"""
import os

import chunk_store
from embedding import FakeEmbedder


class Crash(BaseException):
    """A simulated kill."""


class Hooks:
    def __init__(self, crash_at=None, hard=False):
        self.crash_at, self.hard, self.labels = crash_at, hard, []

    def tick(self, label):
        index = len(self.labels)
        self.labels.append(label)
        if index == self.crash_at:
            if self.hard:
                os._exit(137)
            raise Crash(f"killed at step {index}: {label}")


class HookedEmbedder(FakeEmbedder):
    def __init__(self, hooks, **kw):
        super().__init__(**kw)
        self.hooks = hooks

    def warmup(self):
        self.hooks.tick("embedder.warmup:before")
        super().warmup()
        self.hooks.tick("embedder.warmup:after")

    def embed_documents(self, texts, ids=None):
        self.hooks.tick("embedder.embed:before")
        out = super().embed_documents(texts, ids)
        self.hooks.tick("embedder.embed:after")
        return out


class HookedStore:
    """Wraps any vector store; every call that changes it is a step."""

    def __init__(self, inner, hooks):
        self.inner, self.hooks = inner, hooks

    def _step(self, name, *args):
        self.hooks.tick(f"store.{name}:before")
        result = getattr(self.inner, name)(*args)
        self.hooks.tick(f"store.{name}:after")
        return result

    def upsert(self, ids, vectors):
        return self._step("upsert", ids, vectors)

    def delete(self, ids):
        return self._step("delete", ids)

    def clear(self):
        return self._step("clear")

    def set_signature(self, model, dim):
        return self._step("set_signature", model, dim)

    def ids(self):
        return self.inner.ids()

    def count(self):
        return self.inner.count()

    def query(self, vector, k):
        return self.inner.query(vector, k)

    def signature(self):
        return self.inner.signature()


STORE_FUNCTIONS = ("save_file_chunks", "delete_file", "forget_embeddings", "forget_embeddings_for", "mark_file_failed")


def install(hooks):
    """Make every database-changing chunk_store call a step. Returns a function that undoes it."""
    originals = {name: getattr(chunk_store, name) for name in STORE_FUNCTIONS}

    def wrap(name, real):
        def hooked(*args, **kwargs):
            hooks.tick(f"chunk_store.{name}:before")
            result = real(*args, **kwargs)
            hooks.tick(f"chunk_store.{name}:after")
            return result
        return hooked

    for name, real in originals.items():
        setattr(chunk_store, name, wrap(name, real))

    def uninstall():
        for name, real in originals.items():
            setattr(chunk_store, name, real)
    return uninstall
