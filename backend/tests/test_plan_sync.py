"""Task I-4.4: plan_sync sorts chunks into new / changed / needs_vector / moved / unchanged / gone. Pure function: no files, no database, no model.

The rule it carries (from the I-2 review): "unchanged" means the same content_hash AND a vector record for the CURRENT model.
Same hash alone must never mean done, or a chunk that was saved but never embedded is skipped forever.
"""
import pytest

import chunk_store
from chunker import chunk_file
from indexing import plan_sync

M, D = "m1", 8


def chunk(i, **kw):
    base = dict(id=f"id{i}", content_hash=f"hash{i}", ordinal=0, kind="function", symbol=f"f{i}", parent=None,
                names=[f"f{i}"], file_path="/abs/a.py", rel_path="a.py", start_line=10 * i + 1, end_line=10 * i + 5,
                text=f"def f{i}(): ...", embed_text=f"a.py · f{i}\ndef f{i}(): ...", part=None, part_count=None,
                synthetic=False, parse_error=False)
    return {**base, **kw}


def stored(c, model=M, dim=D):
    """What the store hands back for a chunk that was saved and embedded."""
    return {**c, "embed_model": model, "embed_dim": dim}


def plan(old, new, model=M, dim=D):
    return plan_sync(old, new, model, dim)


def ids(chunks):
    return [c["id"] for c in chunks]


def test_a_first_run_everything_is_new():
    new = [chunk(i) for i in range(3)]
    p = plan([], new)
    assert ids(p.new) == ids(new) and ids(p.to_embed) == ids(new)
    assert p.gone == [] and p.changed == p.unchanged == p.moved == p.needs_vector == []


def test_a_second_run_with_nothing_edited_embeds_nothing():
    new = [chunk(i) for i in range(3)]
    p = plan([stored(c) for c in new], new)
    assert ids(p.unchanged) == ids(new)
    assert p.to_embed == [] and p.new == p.changed == p.moved == p.needs_vector == [] and p.gone == []


def test_editing_one_function_embeds_exactly_that_one():
    new = [chunk(i) for i in range(4)]
    old = [stored(c) for c in new]
    new[2] = chunk(2, content_hash="hash2-edited", text="def f2(): return 2", embed_text="a.py · f2\ndef f2(): return 2")
    p = plan(old, new)
    assert ids(p.changed) == ["id2"] and ids(p.to_embed) == ["id2"]
    assert ids(p.unchanged) == ["id0", "id1", "id3"]


def test_code_moving_down_the_file_updates_the_row_but_embeds_nothing():
    new = [chunk(i) for i in range(3)]
    old = [stored(c) for c in new]
    new = [chunk(i, start_line=c["start_line"] + 40, end_line=c["end_line"] + 40) for i, c in enumerate(new)]
    p = plan(old, new)
    assert ids(p.moved) == ids(new) and p.to_embed == []   # pointer rows must be rewritten, vectors stay
    assert p.unchanged == [] and p.changed == []


def test_adding_a_function_embeds_only_the_new_one():
    old = [stored(chunk(i)) for i in range(2)]
    new = [chunk(0), chunk(1), chunk(2)]
    p = plan(old, new)
    assert ids(p.new) == ["id2"] and ids(p.to_embed) == ["id2"] and ids(p.unchanged) == ["id0", "id1"]


def test_deleting_a_function_lists_its_id_as_gone():
    old = [stored(chunk(i)) for i in range(3)]
    new = [chunk(0), chunk(2)]
    p = plan(old, new)
    assert p.gone == ["id1"] and p.to_embed == [] and ids(p.unchanged) == ["id0", "id2"]


def test_a_moved_file_is_delete_plus_add_never_changed():
    old = [stored(chunk(i, id=f"a-{i}", rel_path="a.py")) for i in range(3)]
    new = [chunk(i, id=f"b-{i}", rel_path="b.py") for i in range(3)]   # ids hash the path, so a rename changes every id
    p = plan(old, new)
    assert p.gone == ["a-0", "a-1", "a-2"] and ids(p.new) == ["b-0", "b-1", "b-2"]
    assert p.changed == [] and ids(p.to_embed) == ids(new)


@pytest.mark.parametrize("old_model,old_dim", [("other-model", D), (M, 1024), (None, None), (M, None), (None, D)],
                         ids=["other-model", "other-dim", "never-embedded", "no-dim", "no-model"])
def test_same_hash_but_no_vector_record_for_this_model_means_embed_again(old_model, old_dim):
    new = [chunk(i) for i in range(3)]
    p = plan([stored(c, old_model, old_dim) for c in new], new)
    assert ids(p.needs_vector) == ids(new) and ids(p.to_embed) == ids(new)
    assert p.unchanged == []


def test_only_the_chunks_without_a_record_are_embedded_after_a_half_finished_run():
    new = [chunk(i) for i in range(5)]
    old = [stored(c) if i < 2 else stored(c, None, None) for i, c in enumerate(new)]   # crashed after two
    p = plan(old, new)
    assert ids(p.unchanged) == ["id0", "id1"] and ids(p.needs_vector) == ["id2", "id3", "id4"]


def test_a_changed_chunk_is_embedded_once_even_if_its_model_also_differs():
    old = [stored(chunk(0), "other-model", D)]
    new = [chunk(0, content_hash="edited")]
    p = plan(old, new)
    assert ids(p.changed) == ["id0"] and p.needs_vector == [] and ids(p.to_embed) == ["id0"]


def test_every_new_chunk_lands_in_exactly_one_bucket_and_to_embed_keeps_chunk_order():
    new = [chunk(i) for i in range(8)]
    old = [stored(new[0]), stored(new[1], "other", D), stored(new[2], None, None), stored(chunk(3, content_hash="old")),
           stored(chunk(4, start_line=99, end_line=100)), stored(chunk(90))]
    p = plan(old, new)
    buckets = [p.new, p.changed, p.needs_vector, p.moved, p.unchanged]
    seen = [c["id"] for b in buckets for c in b]
    assert sorted(seen) == sorted(ids(new)) and len(seen) == len(set(seen))
    assert ids(p.to_embed) == [i for i in ids(new) if i in set(ids(p.new + p.changed + p.needs_vector))]
    assert p.gone == ["id90"]
    assert {"id1", "id2", "id3", "id5", "id6", "id7"} == set(ids(p.to_embed))


def test_the_absolute_path_alone_does_not_count_as_a_change():
    new = [chunk(0)]
    old = [stored(chunk(0, file_path="/old/location/a.py"))]   # the repo folder was moved
    assert plan(old, new).unchanged == new


@pytest.mark.parametrize("field,value", [("start_line", 77), ("end_line", 78), ("symbol", "renamed"), ("parent", "K"),
                                         ("kind", "method"), ("names", ["x"]), ("part", 0), ("synthetic", True)])
def test_any_row_field_that_differs_with_the_same_hash_is_a_move(field, value):
    c = chunk(0, part_count=2) if field == "part" else chunk(0)
    p = plan([stored(c)], [{**c, field: value}])
    assert len(p.moved) == 1 and p.to_embed == []


def test_bad_input_is_refused():
    with pytest.raises(ValueError, match="id0"):
        plan([], [chunk(0), chunk(0)])
    with pytest.raises(ValueError):
        plan([], [chunk(0)], model="", dim=D)
    with pytest.raises(ValueError):
        plan([], [chunk(0)], model=M, dim=0)
    with pytest.raises(ValueError, match="id0"):
        plan([stored(chunk(0)), stored(chunk(0))], [chunk(0)])


# ---- with the real chunker and the real store ----

V1 = "import os\n\n\ndef alpha():\n    return 1\n\n\ndef beta():\n    return 2\n\n\ndef gamma():\n    return 3\n"


def chunks_of(tmp_path, source, name="shop.py"):
    (tmp_path / name).write_text(source)
    return chunk_file(str(tmp_path / name), repo_root=str(tmp_path))


def stored_rows(conn, chunks, model=M, dim=D):
    chunk_store.save_file_chunks(conn, 1, chunks[0]["rel_path"], "h", chunks, embedded={c["id"]: (model, dim) for c in chunks})
    return chunk_store.get_chunks(conn, 1, sorted(chunk_store.all_ids(conn, 1)))


def test_real_chunks_unedited_embed_nothing(conn, tmp_path):
    v1 = chunks_of(tmp_path, V1)
    p = plan(stored_rows(conn, v1), chunks_of(tmp_path, V1))
    assert p.to_embed == [] and len(p.unchanged) == len(v1)


def test_real_chunks_blank_lines_inserted_above_embed_nothing_but_move_the_pointers(conn, tmp_path):
    v1 = chunks_of(tmp_path, V1)
    rows = stored_rows(conn, v1)
    p = plan(rows, chunks_of(tmp_path, "\n\n\n" + V1))
    assert p.to_embed == [] and p.gone == []
    assert {c["symbol"] for c in p.moved} >= {"alpha", "beta", "gamma"}


def test_real_chunks_edit_one_function_embeds_one(conn, tmp_path):
    rows = stored_rows(conn, chunks_of(tmp_path, V1))
    p = plan(rows, chunks_of(tmp_path, V1.replace("return 2", "return 22")))
    assert [c["symbol"] for c in p.to_embed] == ["beta"] and [c["symbol"] for c in p.changed] == ["beta"]
    assert p.gone == []


def test_real_chunks_a_renamed_file_is_gone_plus_new(conn, tmp_path):
    v1 = chunks_of(tmp_path, V1, "shop.py")
    rows = stored_rows(conn, v1)
    p = plan(rows, chunks_of(tmp_path, V1, "store.py"))
    assert sorted(p.gone) == sorted(c["id"] for c in v1)
    assert len(p.new) == len(v1) and p.changed == []
