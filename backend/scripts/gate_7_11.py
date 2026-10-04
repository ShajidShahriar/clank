"""Manual script, not a test: read the 7.11 gate (is a second embedding model worth trying?) from stored results. No embedding.
Run from backend/:  uv run python scripts/gate_7_11.py --runs eval/results/clank-policy-on.json eval/results/flask-rst-plain.json eval/results/express-policy-on.json \
                    --pools eval/results/clank-pool.json eval/results/flask-rst-plain.json eval/results/express-pool.json
See eval/gate.py. The holdout is hidden unless you pass --include-holdout (only at the very end).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.gate import main

sys.exit(main())
