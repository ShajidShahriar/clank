"""Manual script, not a test: replay ranking policies (tests and changelogs demoted, hidden, scaled) on saved pools, offline.
Run from backend/:  uv run python scripts/replay_policies.py --runs eval/results/clank-pool.json eval/results/flask-pool.json eval/results/express-pool.json
The pools come from `run_eval.py --keep 30 --no-index`. See eval/replay_report.py. The holdout is hidden unless you pass --include-holdout.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.replay_report import main

sys.exit(main())
