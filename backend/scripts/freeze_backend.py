"""Build the frozen backend for the installer. Run from backend/:  uv run python scripts/freeze_backend.py   (see freeze.py)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from freeze import main

sys.exit(main())
