"""The holdout is looked at ONCE, with the final configuration (task I-7.13).

`run_eval.py --final` is the only way to print holdout numbers, and it writes `meta.final = true` into the results file. Every results file made
before that contains the holdout answers too (the runner saves everything), so the analysis tools call `require_final` before they show the holdout:
a run that was not made with `--final` is refused, however it is asked for.
"""


def require_final(runs: list[dict]) -> None:
    """ValueError unless every run was made with `--final` (`meta.final` is exactly True)."""
    if not runs:
        raise ValueError("there are no runs to check: the holdout can only be shown from runs made with --final")
    not_final = [run["meta"].get("repo", "?") for run in runs if run["meta"].get("final") is not True]
    if not_final:
        raise ValueError(f"the holdout can only be shown from runs made with --final; these runs were not: {', '.join(not_final)}")
