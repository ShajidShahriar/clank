"""A reusable check that any embedder keeps vectors in the same order as its texts (task I-3c).

Not a test file (no test_ prefix). test_embedder_order.py runs it on the fake and on deliberately
broken embedders; test_ollama_embedder.py will run it on the real one.

How it works: the ground truth for text i is the vector you get by embedding text i ALONE.
A batch result must match that, position by position. A batch that came back reordered, short,
or with a duplicate fails, and the message says which position got which text's vector.
"""
import math


def distinct_texts(n):
    # Different lengths on purpose: real models pad a batch to its longest text, which is where order bugs hide.
    return [f"text {i}: " + "word " * (i * 7 % 23) for i in range(n)]


def close(a, b, tol):
    return len(a) == len(b) and all(math.isclose(x, y, abs_tol=tol) for x, y in zip(a, b))


def check_order_preserved(embedder, n=40, tol=0.0, batch_sizes=(None, 16, 7)):
    """Raises AssertionError if vector i is not the vector of text i. `tol` is for real models, whose
    batch results can differ from single results in the last decimals."""
    texts = distinct_texts(n)
    alone = [embedder.embed_documents([t])[0] for t in texts]
    for i in range(n):
        for j in range(i):
            assert not close(alone[i], alone[j], 1e-9), (
                f"setup problem: texts {i} and {j} got the same vector, so order cannot be checked")

    for size in batch_sizes:  # None = everything in one call
        got = []
        for start in range(0, n, size or n):
            part = texts[start:start + (size or n)]
            result = embedder.embed_documents(part)
            assert len(result) == len(part), (
                f"batch of {len(part)} texts returned {len(result)} vectors (batch size {size})")
            got.extend(result)
        for i in range(n):
            if not close(got[i], alone[i], tol):
                other = next((j for j in range(n) if close(got[i], alone[j], tol)), None)
                where = f"it matches text {other}" if other is not None else "it matches no text"
                raise AssertionError(f"position {i} holds the wrong vector: {where} (batch size {size})")
