"""Task I-7.12: reStructuredText docs as plain line windows (the existing plain-text chunker), kind `doc_text`.

Flask's docs are `.rst` and were never indexed, so two docs questions could not be answered. Plain windows were adopted over a heading-aware chunker
because the rule written beforehand (devlog 79) said a tie keeps the simpler one (devlog 80 and 81 have the numbers). The pointer-truth, no-empty-chunk and
full-line-coverage invariants run on real Flask doc excerpts (pinned d73fa1c) in test_invariants.py.
"""
from chunker import chunk_file
from chunker.core import MAX_CHUNK_TOKENS, estimate_tokens


def chunks_of(tmp_path, text, name="doc.rst", raw=None):
    path = tmp_path / name
    path.write_bytes(raw if raw is not None else text.encode())
    return chunk_file(str(path), repo_root=str(tmp_path))


def test_a_short_rst_file_is_one_doc_text_chunk_holding_exactly_its_lines(tmp_path):
    text = "Handling Errors\n===============\n\nSome words.\n\n.. code-block:: python\n\n    x = 1\n"
    (chunk,) = chunks_of(tmp_path, text)
    assert chunk["kind"] == "doc_text" and chunk["symbol"] is None and chunk["rel_path"] == "doc.rst"
    assert (chunk["start_line"], chunk["end_line"]) == (1, 8) and chunk["text"] == text.rstrip("\n")
    assert "doc.rst" in chunk["embed_text"] and "Handling Errors" in chunk["embed_text"]


def test_a_long_rst_file_is_cut_into_line_windows_under_the_cap_that_cover_every_line(tmp_path):
    text = "\n".join(f"Line {i} of a long reference page that goes on and on." for i in range(400)) + "\n"
    chunks = chunks_of(tmp_path, text)
    assert len(chunks) > 1 and all(c["kind"] == "doc_text" for c in chunks)
    assert all(estimate_tokens(c["text"]) <= MAX_CHUNK_TOKENS for c in chunks)
    covered = set()
    for c in chunks:
        covered.update(range(c["start_line"], c["end_line"] + 1))
    assert covered == set(range(1, 401))


def test_empty_and_whitespace_only_rst_files_make_no_chunks(tmp_path):
    assert chunks_of(tmp_path, "") == [] and chunks_of(tmp_path, "  \n\n", name="b.rst") == []


def test_crlf_line_breaks_are_read_as_one_break(tmp_path):
    (chunk,) = chunks_of(tmp_path, "", raw=b"Title\r\n=====\r\n\r\ntext\r\n")
    assert chunk["text"] == "Title\n=====\n\ntext" and chunk["end_line"] == 4


def test_headings_are_not_special_to_the_plain_window_chunker(tmp_path):
    chunks = chunks_of(tmp_path, "Title\n=====\n\nbody\n\nSection\n-------\n\nmore\n")
    assert len(chunks) == 1 and chunks[0]["symbol"] is None, "one window holding the whole short page, no heading path"


def test_the_extension_is_matched_in_lower_case_only_like_the_other_extensions(tmp_path):
    assert chunks_of(tmp_path, "word\n", name="a.rst")
