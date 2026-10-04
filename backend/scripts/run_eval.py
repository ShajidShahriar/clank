"""Manual script, not a test: index a repo with the real Ollama and ask the eval questions of that repo.
Run from backend/:  CLANK_DATA_DIR=~/.clank-eval uv run python scripts/run_eval.py --repo clank
See eval/cli.py for the options. One repo per sitting (the laptop has no fan).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.cli import main

sys.exit(main())
