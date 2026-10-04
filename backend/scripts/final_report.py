"""Manual script: write the final results text (stages, holdout verdict, rank grid with causes, timings) from the result files (task I-7.13).
Run from backend/:  uv run python scripts/final_report.py --pool flask=flask-rst-plain.json
See eval/final_report.py. It reads the final files only if they were made with `run_eval.py --final`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval.final_report import main

sys.exit(main())
