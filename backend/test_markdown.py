import pytest

from chunker import MAX_CHUNK_TOKENS, chunk_file, estimate_tokens

DOC = """# README

Intro text.

## Setup

### Install
Run it.

### Configure
Set it.

## Usage
Use it.
"""


def chunks_of(tmp_path, name, source):
    p = tmp_path / name
    p.write_bytes(source.encode() if isinstance(source, str) else source)
    return chunk_file(str(p))


def pairs(chunks):
    return [(c["kind"], c["symbol"]) for c in chunks]


def test_sections_use_the_heading_path_as_symbol(tmp_path):
    assert pairs(chunks_of(tmp_path, "README.md", DOC)) == [
        ("doc_section", "README"),
        ("doc_section", "README > Setup > Install"),
        ("doc_section", "README > Setup > Configure"),
        ("doc_section", "README > Usage"),
    ]


def test_bare_heading_is_folded_into_the_next_section(tmp_path):
    chunks = chunks_of(tmp_path, "README.md", DOC)
    install = chunks[1]
    assert install["text"] == "## Setup\n\n### Install\nRun it."
    assert (install["start_line"], install["end_line"]) == (5, 8)
    assert install["embed_text"].startswith("README.md · README > Setup > Install\n")


def test_hash_lines_inside_code_fences_are_not_headings(tmp_path):
    src = "# Top\n\n```sh\n# a comment, not a heading\nmake\n```\n\n~~~\n## also not\n~~~\n"
    chunks = chunks_of(tmp_path, "a.md", src)
    assert pairs(chunks) == [("doc_section", "Top")]
    assert "# a comment, not a heading" in chunks[0]["text"]


def test_setext_headings(tmp_path):
    src = "Title\n=====\n\nintro\n\nSub\n---\n\nbody\n\n---\n\nafter a rule\n"
    assert pairs(chunks_of(tmp_path, "a.md", src)) == [("doc_section", "Title"), ("doc_section", "Title > Sub")]


def test_front_matter_and_text_before_the_first_heading(tmp_path):
    src = "---\ntitle: x\n---\n\nbefore any heading\n\n# Real\ntext\n"
    chunks = chunks_of(tmp_path, "a.md", src)
    assert pairs(chunks) == [("doc_intro", None), ("doc_section", "Real")]
    assert chunks[0]["text"].startswith("---\ntitle: x")  # front matter is kept, not parsed as headings


def test_file_without_headings_is_one_intro_chunk(tmp_path):
    assert pairs(chunks_of(tmp_path, "a.md", "just text\n\nmore text\n")) == [("doc_intro", None)]


def test_repeated_heading_paths_get_distinct_ids(tmp_path):
    chunks = chunks_of(tmp_path, "a.md", "# A\n\n## Notes\none\n\n## Notes\ntwo\n")
    assert [c["symbol"] for c in chunks] == ["A > Notes", "A > Notes"]
    assert chunks[0]["id"] != chunks[1]["id"]


def test_long_section_splits_at_paragraphs_and_keeps_fences_whole(tmp_path):
    para = "A paragraph of prose that goes on for a little while, long enough to matter.\n"
    fence = "```python\n" + "x = 1\n" * 5 + "```\n"
    body = ("\n".join([para * 3, fence, para * 3]) + "\n") * 12
    chunks = chunks_of(tmp_path, "big.md", "# Big\n\n" + body)
    assert len(chunks) > 5
    for c in chunks:
        assert estimate_tokens(c["text"]) <= MAX_CHUNK_TOKENS
        assert c["text"].count("```") % 2 == 0  # never cut a code fence in half
    assert [c["symbol"] for c in chunks[:2]] == ["Big_part0", "Big_part1"]


def test_crlf_markdown_has_no_carriage_returns(tmp_path):
    chunks = chunks_of(tmp_path, "a.md", "# A\r\n\r\ntext\r\n\r\n## B\r\nmore\r\n")
    assert pairs(chunks) == [("doc_section", "A"), ("doc_section", "A > B")]
    assert all("\r" not in c["text"] for c in chunks)


def test_blank_markdown_returns_nothing(tmp_path):
    assert chunks_of(tmp_path, "a.md", "\n\n") == []


@pytest.mark.parametrize("name,source", [("a.json", '{"name": "x"}\n'), ("a.yaml", "name: x\n"), ("a.yml", "name: x\n")])
def test_small_config_files_are_indexed_as_plain_text(tmp_path, name, source):
    chunks = chunks_of(tmp_path, name, source)
    assert [(c["kind"], c["start_line"], c["end_line"]) for c in chunks] == [("config", 1, 1)]
    assert chunks[0]["text"] == source.rstrip("\n")


def test_config_file_is_windowed_like_everything_else(tmp_path):
    src = "{\n" + ",\n".join(f'  "key_number_{i}": "some value {i}"' for i in range(300)) + "\n}\n"
    chunks = chunks_of(tmp_path, "a.json", src)
    assert len(chunks) > 1 and all(c["kind"] == "config" for c in chunks)
    assert all(estimate_tokens(c["text"]) <= MAX_CHUNK_TOKENS for c in chunks)
