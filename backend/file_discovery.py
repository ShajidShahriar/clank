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

    for path in repo_path.rglob("*"):
        if path.is_dir():
            continue

        relative_path = path.relative_to(repo_path)

        # judge folders by the path *inside* the repo, so a parent like ~/build/ can't hide everything
        if any(part in HARD_IGNORE_DIRS for part in relative_path.parts):
            continue

        if path.name in IGNORE_FILENAMES:
            continue

        if spec.match_file(str(relative_path)):
            continue

        # only keep files with extensions we actually want to index
        if path.suffix not in SOURCE_EXTENSIONS:
            continue

        matched_files.append(path)

    return matched_files