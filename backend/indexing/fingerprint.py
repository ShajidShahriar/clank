"""A fingerprint of everything that decides what chunks a file turns into: the chunker code, the language rules,
the grammar packages and the grouping switch.

Why it exists: the file-hash shortcut skips re-chunking a file whose bytes did not change. That is only right if the
CHUNKER did not change either. A hand-written version number would depend on someone remembering to bump it, and
forgetting is silent (files keep their old chunks forever). So the version is computed from the code itself.
Any edit to the chunker, even a comment, changes it. That costs one re-chunk of every file (cheap, and nothing is
re-embedded when the chunks come out the same), never a wrong skip.
"""
import hashlib
from importlib import metadata
from pathlib import Path

from chunker import grouping

BACKEND = Path(__file__).resolve().parents[1]


def chunker_source_files() -> list[Path]:
    return sorted((BACKEND / "chunker").glob("*.py")) + [BACKEND / "languages.py"]


def grammar_versions() -> dict[str, str]:
    return {d.metadata["Name"]: d.version for d in metadata.distributions() if d.metadata["Name"].lower().startswith("tree-sitter")}


def compute_fingerprint(source_files: list[Path], dependencies: dict[str, str], grouping_on: bool) -> str:
    """A 16-character hash. Pure: the same inputs give the same answer, whatever order the files come in."""
    digest = hashlib.sha1()
    for path in sorted(source_files, key=lambda p: p.name):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    for name, version in sorted(dependencies.items()):
        digest.update(f"{name}=={version}\0".encode())
    digest.update(f"grouping={grouping_on}".encode())
    return digest.hexdigest()[:16]


def chunker_fingerprint() -> str:
    # grouping is read at call time: the tests (and any future setting) switch it while the program runs
    return compute_fingerprint(chunker_source_files(), grammar_versions(), grouping.GROUP_SMALL_CHUNKS)
