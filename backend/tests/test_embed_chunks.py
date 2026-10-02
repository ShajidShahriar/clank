"""Task I-4.3: one helper embeds chunks, and it always embeds `embed_text`, never `text`.

Passing `text` by mistake would not crash. Every `file · symbol` label would just be missing from the vectors,
and search quality would quietly drop. So there is exactly one place that decides what gets embedded.
"""
import re
from pathlib import Path

import pytest

from chunker import chunk_file
from embedding import BadResponse, EmbeddingTooLong, FakeEmbedder
from indexing import embed_chunks
from test_embedder_order import DropsLast

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def chunks(tmp_path):
    (tmp_path / "shop.py").write_text("".join(f"def order_{i}():\n    return {i}\n\n\n" for i in range(5)))
    out = chunk_file(str(tmp_path / "shop.py"), repo_root=str(tmp_path))
    assert len(out) == 5
    assert all(c["embed_text"] != c["text"] for c in out)  # otherwise this whole file could not tell the two apart
    assert all(c["embed_text"].startswith("shop.py · order_") for c in out)
    return out


def test_it_embeds_embed_text_in_order_never_text(chunks):
    e = FakeEmbedder()
    embed_chunks(e, chunks)
    assert e.embedded_texts == [c["embed_text"] for c in chunks]
    assert not set(e.embedded_texts) & {c["text"] for c in chunks}


def test_it_returns_one_vector_per_chunk_id_in_chunk_order(chunks):
    e = FakeEmbedder()
    result = embed_chunks(e, chunks)
    assert list(result) == [c["id"] for c in chunks]
    for c in chunks:
        assert result[c["id"]] == FakeEmbedder().embed_documents([c["embed_text"]])[0]


def test_the_vector_belongs_to_its_chunk_even_for_chunks_in_a_different_order(chunks):
    shuffled = chunks[::-1]
    result = embed_chunks(FakeEmbedder(), shuffled)
    assert list(result) == [c["id"] for c in shuffled]
    assert result[chunks[0]["id"]] == FakeEmbedder().embed_documents([chunks[0]["embed_text"]])[0]


def test_chunk_ids_are_passed_on_so_an_oversized_chunk_is_named(chunks):
    big = dict(chunks[2], embed_text="x" * 500)
    mixed = chunks[:2] + [big] + chunks[3:]
    with pytest.raises(EmbeddingTooLong) as err:
        embed_chunks(FakeEmbedder(max_chars=100), mixed)
    assert err.value.chunk_ids == [big["id"]]


def test_nothing_to_embed_does_not_call_the_embedder():
    e = FakeEmbedder()
    assert embed_chunks(e, []) == {}
    assert e.batch_count == 0


def test_duplicate_ids_are_refused_before_anything_is_embedded(chunks):
    e = FakeEmbedder()
    with pytest.raises(ValueError, match=chunks[0]["id"]):
        embed_chunks(e, chunks + [dict(chunks[0])])
    assert e.text_count == 0


@pytest.mark.parametrize("bad", ["", None, "   "])
def test_a_chunk_without_embed_text_is_refused_and_named(chunks, bad):
    e = FakeEmbedder()
    broken = [dict(c) for c in chunks]
    broken[3]["embed_text"] = bad
    with pytest.raises(ValueError, match=chunks[3]["id"]):
        embed_chunks(e, broken)
    assert e.text_count == 0


def test_a_chunk_dict_with_no_embed_text_key_is_refused_and_named(chunks):
    broken = [dict(c) for c in chunks]
    del broken[1]["embed_text"]
    with pytest.raises(ValueError, match=chunks[1]["id"]):
        embed_chunks(FakeEmbedder(), broken)


def test_a_short_answer_from_the_embedder_is_an_error_not_a_misaligned_result(chunks):
    with pytest.raises(BadResponse):
        embed_chunks(DropsLast(), chunks)


def test_embed_documents_is_called_from_one_place_only():
    # Rule: nothing outside the embedding package and this helper may call embed_documents,
    # so there is no second path that could pass the wrong field.
    allowed = {BACKEND / "indexing" / "embed.py"}
    callers = []
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).parts
        if rel[0] in ("tests", "scripts", "embedding") or path in allowed:
            continue
        if re.search(r"\.embed_documents\(", path.read_text()):
            callers.append(str(path.relative_to(BACKEND)))
    assert callers == [], f"call embed_chunks instead of embed_documents in: {callers}"
