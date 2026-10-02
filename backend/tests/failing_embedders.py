"""A fake embedder that fails on purpose: for the tests of what index_project does when the embedder misbehaves."""
from embedding import FakeEmbedder


class Raising(FakeEmbedder):
    """Raises `error` from embed_documents whenever `when(texts)` is true, and from warmup() if `on_warmup`."""

    def __init__(self, error=None, when=None, on_warmup=False, **kw):
        super().__init__(**kw)
        self.error, self.when, self.on_warmup = error, when, on_warmup
        self.events = []                       # ("warmup",) and ("embed", n) in the order they happened

    def warmup(self):
        self.events.append(("warmup",))
        super().warmup()
        if self.on_warmup:
            raise self.error

    def embed_documents(self, texts, ids=None):
        self.events.append(("embed", len(texts)))
        if self.when is not None and self.when(texts):
            raise self.error
        return super().embed_documents(texts, ids)


def mentions(name):
    """A `when` rule: true for a batch that contains a chunk of the file called `name`."""
    return lambda texts: any(name in t for t in texts)
