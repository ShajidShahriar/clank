"""The one place that decides WHAT gets embedded: a chunk's `embed_text`, never its `text`.

`embed_text` is `"<rel_path> · <Class.symbol>"` plus the source. Embedding `text` by mistake would not crash, it
would drop every label and quietly lower search quality. Nothing else may call `embed_documents` (a test enforces it).
"""
from embedding import BadResponse, Embedder, Vector


def embed_chunks(embedder: Embedder, chunks: list[dict]) -> dict[str, Vector]:
    """Embed every chunk's `embed_text`. Returns {chunk id: vector}, in chunk order. Embeds nothing if any chunk is unusable."""
    ids = [c.get("id") for c in chunks]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"duplicate chunk ids, vectors would be mixed up: {dupes}")
    for chunk in chunks:
        embed_text = chunk.get("embed_text")
        if not isinstance(embed_text, str) or not embed_text.strip():
            raise ValueError(f"chunk {chunk.get('id')} has no embed_text")
    if not chunks:
        return {}
    vectors = embedder.embed_documents([c["embed_text"] for c in chunks], ids=ids)
    if len(vectors) != len(chunks):  # embedders check this too; a second check costs nothing and a misaligned dict is silent
        raise BadResponse(f"embedder returned {len(vectors)} vectors for {len(chunks)} chunks")
    return dict(zip(ids, vectors))
