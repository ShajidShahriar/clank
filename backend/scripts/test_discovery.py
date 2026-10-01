"""Manual script, not a test: list the files discover_files finds in the parent folder.
Run from backend/:  uv run python scripts/test_discovery.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from file_discovery import discover_files

files = discover_files("..")
print(f"Found {len(files)} files:\n")
for f in sorted(files):
    print(f)
