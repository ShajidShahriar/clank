from pathlib import Path
import pathspec

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

        # skip hardcoded ignored directories anywhere in the path
        if any(part in HARD_IGNORE_DIRS for part in path.parts):
            continue

        # skip files matched by .gitignore (paths must be relative for pathspec)
        relative_path = path.relative_to(repo_path)
        if spec.match_file(str(relative_path)):
            continue

        # only keep files with extensions we actually want to index
        if path.suffix not in SOURCE_EXTENSIONS:
            continue

        matched_files.append(path)

    return matched_files