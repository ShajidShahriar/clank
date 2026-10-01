import os
from pathlib import Path
import pathspec

# ---------------------------------------------------------
# load_gitignore(repo_path):
#   look for a .gitignore file at the root of repo_path
#   if it doesn't exist:
#       return an empty rule set (nothing gets ignored by gitignore)
#   else:
#       read its lines and compile them into a matcher
#       return that matcher
#
# discover_files(repo_path):
#   convert repo_path to an absolute, normalized path
#   load the gitignore matcher for this repo
#   matched_files = []
#
#   for every file/folder anywhere under repo_path (recursive):
#       if it's a folder            -> skip it, keep going
#       if any part of its path is in HARD_IGNORE_DIRS
#           (.git, node_modules, venv, dist, build, etc.) -> skip it
#       if the gitignore matcher says this path is ignored -> skip it
#       if its extension is not one we care about
#           (.py, .js, .md, .json, etc.)                   -> skip it
#
#       otherwise -> keep it, add to matched_files
#
#   return matched_files
# ---------------------------------------------------------





# Extensions we actually care about indexing
SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".java", ".c", ".cpp", ".h", ".hpp",
    ".go", ".rs", ".rb", ".php",
    ".md", ".json", ".yaml", ".yml",
}

# Hardcoded ignores — things we never want indexed regardless of .gitignore
HARD_IGNORE_DIRS = {
    ".git", "node_modules", "venv", "env", "__pycache__",
    "dist", "build", ".next", ".cache", "coverage",
}

# Generated files that match an indexed extension but are never worth indexing
IGNORE_FILENAMES = {
    "package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml",
    "yarn.lock", "composer.lock", "poetry.lock", "uv.lock",
}

# Files bigger than this are almost never hand-written source (bundles, data dumps)
MAX_FILE_BYTES = 1_000_000

# Data/config files are only worth indexing when small. Bigger ones (generated schemas, fixtures,
# lockfile-like dumps) are noise, and the lockfiles themselves are already excluded by name.
DATA_EXTENSIONS = {".json", ".yaml", ".yml"}
MAX_DATA_FILE_BYTES = 20_000

# A null byte in the first few KB means binary (the same test git uses)
BINARY_SNIFF_BYTES = 8192

def is_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return b"\0" in f.read(BINARY_SNIFF_BYTES)
    except OSError:
        return True  # unreadable: don't try to index it

def load_gitignore(repo_path: Path) -> pathspec.PathSpec:
    gitignore_path = repo_path / ".gitignore"
    if not gitignore_path.exists():
        return pathspec.PathSpec.from_lines("gitwildmatch", [])
    with open(gitignore_path) as f:
        lines = f.readlines()
    return pathspec.PathSpec.from_lines("gitwildmatch", lines)

def discover_files(repo_path: str) -> list[Path]:
    repo_path = Path(repo_path).resolve()
    spec = load_gitignore(repo_path)
    matched_files = []

    for dirpath, dirnames, filenames in os.walk(repo_path):
        current = Path(dirpath)

        # Pruning dirnames in place stops os.walk from ever entering these folders.
        dirnames[:] = [
            d for d in dirnames
            if d not in HARD_IGNORE_DIRS
            and not spec.match_file(str((current / d).relative_to(repo_path)) + "/")
        ]

        for name in filenames:
            path = current / name

            if name in IGNORE_FILENAMES or name.endswith(".min.js"):
                continue

            # only keep files with extensions we actually want to index
            if path.suffix not in SOURCE_EXTENSIONS:
                continue

            if spec.match_file(str(path.relative_to(repo_path))):
                continue

            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES or (path.suffix in DATA_EXTENSIONS and size > MAX_DATA_FILE_BYTES):
                continue

            if is_binary(path):
                continue

            matched_files.append(path)

    return matched_files
