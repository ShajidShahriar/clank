"""Manual script, not a test: regenerate dummy_oversized.py and show how its one big function is split.
Run from backend/:  uv run python scripts/test_split.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chunker import chunk_file

lines = "\n".join(f"    x{i} = {i}" for i in range(200))
source = f"def big_function():\n{lines}\n    return x0\n"

with open("dummy_oversized.py", "w") as f:
    f.write(source)

chunks = chunk_file("dummy_oversized.py")
print(f"Found {len(chunks)} chunks\n")
for c in chunks:
    print(f"--- {c['kind']} {c['symbol']} part {c['part']}/{c['part_count']} (lines {c['start_line']}-{c['end_line']}) ---")
    print(f"  starts with: {c['text'][:40]!r}")
    print(f"  ends with:   {c['text'][-40:]!r}")
    print()
