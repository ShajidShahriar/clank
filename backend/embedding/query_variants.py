"""Ways of wording the question for the embedder: an experiment tool for the eval (task I-7.5), not part of the product.

A variant changes ONLY how the question becomes a vector. Documents are untouched, the identity is the wrapped embedder's (the stored index belongs
to that model), and the question goes out as plain text through `embed_documents` (no second wrapper). It lives in this package because only the
embedding package and `indexing/embed.py` may call `embed_documents`.

`instruct` is the CONTROL: it sends exactly what `OllamaEmbedder.embed_query` sends, so running it must reproduce the baseline. If it does not,
the experiment is broken, not the model.
"""
from .ollama import QUERY_TASK

QUERY_VARIANTS = {
    "instruct": f"Instruct: {QUERY_TASK}\nQuery: {{q}}",     # what production sends today
    "raw": "{q}",                                            # the bare question, no instruction
    # the default retrieval instruction of the Qwen3-Embedding model card: does tailoring the wording to code help at all?
    "alt": "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: {q}",
}


class VariantQueryEmbedder:
    def __init__(self, inner, variant: str):
        if variant not in QUERY_VARIANTS:
            raise ValueError(f"unknown query variant {variant!r}; choose from {sorted(QUERY_VARIANTS)}")
        self.inner, self.variant, self._template = inner, variant, QUERY_VARIANTS[variant]

    @property
    def model_name(self):
        return self.inner.model_name

    @property
    def dim(self):
        return self.inner.dim

    def warmup(self):
        return self.inner.warmup()

    def embed_documents(self, texts, ids=None):
        return self.inner.embed_documents(texts, ids)

    def embed_query(self, text):
        return self.inner.embed_documents([self._template.format(q=text)])[0]


def variant_embedder(inner, variant: str) -> VariantQueryEmbedder:
    return VariantQueryEmbedder(inner, variant)
