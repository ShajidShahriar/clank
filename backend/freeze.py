"""Freeze the backend into one folder with PyInstaller, for the desktop installer: `uv run python scripts/freeze_backend.py` (from backend/).

The result is `<out>/clank-backend/`: the program `clank-backend` (what the desktop app starts, see electron/backend/env.ts) and an `_internal/` folder with
Python and every library. A folder, not one file: a one-file build unpacks itself at every start.

What PyInstaller cannot see by itself, and is given here on purpose:
- `main`: uvicorn starts the app from the text "main:app", which no import statement mentions;
- all of Chroma: it loads its parts by name at run time;
- the SOURCE files and the package METADATA the chunker fingerprint reads (indexing/fingerprint.py hashes the chunker's .py files and the versions of the
  tree-sitter packages). They are taken from the fingerprint code itself, never listed twice, so a file added to the fingerprint is shipped by the next build.
  In the frozen app `BACKEND` is the `_internal` folder, so each file goes to `_internal/<its path relative to backend/>`.
"""
import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

from indexing import fingerprint

PROGRAM_NAME = "clank-backend"        # electron/backend/env.ts starts <resources>/backend/clank-backend (.exe on Windows)
HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE.parent / "build" / "backend"


def pyinstaller_args(dist_dir: Path, work_dir: Path, spec_dir: Path) -> list[str]:
    args = ["--noconfirm", "--clean", "--onedir", f"--name={PROGRAM_NAME}", "--hidden-import=main", "--collect-all=chromadb"]
    for source in fingerprint.chunker_source_files():
        destination = source.relative_to(fingerprint.BACKEND).parent
        args.append(f"--add-data={source}{os.pathsep}{destination}")
    for package in fingerprint.grammar_versions():
        args.append(f"--copy-metadata={package}")
    args += [f"--distpath={dist_dir}", f"--workpath={work_dir}", f"--specpath={spec_dir}", "run.py"]
    return args


def pyinstaller_available() -> bool:
    return importlib.util.find_spec("PyInstaller") is not None


def main(argv=None, *, run=subprocess.run) -> int:
    parser = argparse.ArgumentParser(prog="freeze_backend.py", description="Freeze the Clank backend with PyInstaller.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"the folder that will hold {PROGRAM_NAME}/ (default: %(default)s)")
    options = parser.parse_args(argv)
    if not pyinstaller_available():
        print("PyInstaller is not installed here. Run this with: uv run python scripts/freeze_backend.py (from backend/), after `uv sync`.", file=sys.stderr)
        return 2
    out = options.out.resolve()
    shutil.rmtree(out / PROGRAM_NAME, ignore_errors=True)                      # only its own output: a file left from an old build must not ship
    scratch = out.parent / "pyinstaller"
    command = [sys.executable, "-m", "PyInstaller", *pyinstaller_args(out, scratch / "work", scratch / "spec")]
    result = run(command, cwd=HERE)
    if result.returncode == 0:
        print(f"Frozen backend: {out / PROGRAM_NAME / PROGRAM_NAME}")
    return result.returncode
