"""Manual script, not a test: print the chunks of one file (edit the path below).
Run from backend/:  uv run python scripts/test_chunker.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chunker import chunk_file

chunks = chunk_file("dummy_class_body.py")

print(f"Found {len(chunks)} chunks:\n")
for c in chunks:
    print(f"--- {c['kind']} (lines {c['start_line']}-{c['end_line']}) ---")
    print(f"symbol: {c['symbol']}, parent: {c['parent']}")
    print(c["embed_text"])
    print()
