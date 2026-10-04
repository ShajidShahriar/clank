"""Manual script, not a test: is the right answer ranked but dropped by the prefix budget rule? Offline, from saved pools.
Run from backend/:  uv run python scripts/budget_report.py --runs eval/results/clank-pool.json eval/results/flask-pool.json eval/results/express-pool.json
See eval/budget_report.py. The holdout is hidden unless you pass --include-holdout (only at the very end).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.budget_report import main

sys.exit(main())
