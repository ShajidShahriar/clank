"""Manual script, not a test: the relevance-floor analysis on saved runs (raw scores, offline).
Run from backend/:  uv run python scripts/floor_report.py --runs eval/results/clank-policy-on.json eval/results/flask-policy-on.json eval/results/express-policy-on.json
See eval/floor.py. The holdout is hidden unless you pass --include-holdout (only at the very end).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.floor import main

sys.exit(main())
