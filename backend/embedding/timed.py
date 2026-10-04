"""A pass-through embedder that counts calls and time: for measuring, never for choosing what is embedded.

It lives in this package on purpose: only the embedding package and `indexing/embed.py` may call `embed_documents` (a second path could
pass the wrong field). This wrapper forwards the texts it is given, untouched.
"""
import time


class TimedEmbedder:
    """Passes everything to the real embedder and counts the time and the calls."""

    def __init__(self, inner):
        self.inner = inner
        self.query_seconds = 0.0
        self.document_seconds = 0.0
        self.document_calls = 0
        self.document_texts = 0

    @property
    def model_name(self):
        return self.inner.model_name

    @property
    def dim(self):
        return self.inner.dim

    def warmup(self):
        return self.inner.warmup()

    def embed_documents(self, texts, ids=None):
        start = time.perf_counter()
        try:
            return self.inner.embed_documents(texts, ids)
        finally:
            self.document_seconds += time.perf_counter() - start
            self.document_calls += 1
            self.document_texts += len(texts)

    def embed_query(self, text):
        start = time.perf_counter()
        try:
            return self.inner.embed_query(text)
        finally:
            self.query_seconds += time.perf_counter() - start
