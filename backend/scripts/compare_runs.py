"""Manual script, not a test: compare saved eval runs question by question.
Run from backend/:  uv run python scripts/compare_runs.py --a clank-control.json flask-control.json --b clank-raw.json flask-raw.json --names control raw
See eval/compare.py. The holdout is hidden unless you pass --include-holdout (only at the very end).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.compare import main

sys.exit(main())
