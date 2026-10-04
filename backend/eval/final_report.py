"""The write-up generator (task I-7.13): the numbers of the results come FROM the result files, not from anyone's typing.

One text with: the frozen configuration, a table per stage (baseline, after the demotion, the final configuration with the demotion off and on) over the
tune split, the holdout and all counted questions, the verdict on the holdout by the rule of devlog 83, a rank grid with a row per counted question and
every miss labelled by cause, and the timings. It reads the final files only if they were made with `--final`.
"""
import argparse
import json
import statistics
from pathlib import Path

from .gate import label_miss
from .holdout import require_final

REPOS = ("clank", "flask", "express")
LOOKS_BAD_BY = 3        # the demotion-on finalist has this many FEWER top-3 hits than demotion-off (devlog 83)


def _counted(run, split=None):
    return [q for q in run["questions"] if q["expect"] and (split is None or q["split"] == split)]


def summarize_stage(runs: list[dict], split: str | None) -> dict:
    qs = [q for run in runs for q in _counted(run, split)]
    return {"n": len(qs), "top1": sum(1 for q in qs if q["rank"] == 1), "top3": sum(1 for q in qs if q["rank"] and q["rank"] <= 3),
            "top10": sum(1 for q in qs if q["rank"]), "context": sum(1 for q in qs if q["found_in_context"])}


def _by_repo(runs):
    return {run["meta"].get("repo"): run for run in runs}


def grid_rows(baseline, demotion, final_on, final_off, pools) -> list[dict]:
    """A row per counted question of the final run (repo order, then id): the rank at every stage ("-" where the stage never asked it) and, for a question
    whose answer is not in the real context, the cause (from the saved pool)."""
    stages = {"baseline": _by_repo(baseline), "demotion": _by_repo(demotion), "final_off": _by_repo(final_off), "pools": _by_repo(pools)}
    rows = []
    for repo in REPOS:
        run = _by_repo(final_on).get(repo)
        if run is None:
            continue
        for q in sorted(_counted(run), key=lambda x: x["id"]):
            row = {"repo": repo, "id": q["id"], "kind": q["kind"], "split": q["split"], "final_on": q["rank"], "final_on_found": q["found_in_context"], "cause": None}
            for name in ("baseline", "demotion", "final_off"):
                other = next((x for x in stages[name].get(repo, {"questions": []})["questions"] if x["id"] == q["id"]), None)
                row[name] = "-" if other is None else other["rank"]
                row[name + "_found"] = False if other is None else other["found_in_context"]
            if not q["found_in_context"]:
                pooled = next((x for x in stages["pools"].get(repo, {"questions": []})["questions"] if x["id"] == q["id"]), None)
                row["cause"] = label_miss(dict(q, pool=pooled["pool"]))[0] if pooled else "no pool saved"
            rows.append(row)
    return rows


def _cell(rank, found) -> str:
    if rank == "-":
        return "-"
    return ("miss" if rank is None else str(rank)) + ("*" if found else "")


def render_grid(rows: list[dict]) -> str:
    lines = [f"{'id':<4} {'repo':<8} {'kind':<9} {'split':<8} {'baseline':>8} {'+demotion':>9} {'final off':>9} {'final on':>8}  cause (final, if the answer is not in the context)"]
    for r in rows:
        lines.append(f"{r['id']:<4} {r['repo']:<8} {r['kind']:<9} {r['split']:<8} {_cell(r['baseline'], r['baseline_found']):>8} {_cell(r['demotion'], r['demotion_found']):>9} "
                     f"{_cell(r['final_off'], r['final_off_found']):>9} {_cell(r['final_on'], r['final_on_found']):>8}  {r['cause'] or ''}")
    lines.append("(rank of the answer in the top 10; * = the answer is in the real context text; miss = not in the top 10)")
    return "\n".join(lines)


def timing_lines(baselines, finals) -> list[str]:
    lines = []
    base, fin = _by_repo(baselines), _by_repo(finals)
    for repo in REPOS:
        parts = []
        index = base.get(repo, {}).get("index")
        if index:
            parts.append(f"cold index {index['seconds']} s ({index['chunks']} chunks, {index['embed_calls']} embed calls)")
        if base.get(repo, {}).get("index_again"):
            parts.append(f"nothing changed {base[repo]['index_again']['seconds']} s")
        times = [q["timings_ms"]["total"] for q in fin.get(repo, {"questions": []})["questions"]]
        if times:
            parts.append(f"query median {statistics.median(times):.1f} ms, slowest {max(times):.1f} ms ({len(times)} questions)")
        lines.append(f"  {repo}: " + "; ".join(parts))
    return lines


def _verdict(final_on, final_off) -> list[str]:
    on, off = summarize_stage(final_on, "holdout"), summarize_stage(final_off, "holdout")
    delta = on["top3"] - off["top3"]
    off_by_id = {(run["meta"].get("repo"), q["id"]): q for run in final_off for q in _counted(run, "holdout")}
    worse_tests = []
    for run in final_on:
        for q in _counted(run, "holdout"):
            if q["kind"] != "tests":
                continue
            other = off_by_id.get((run["meta"].get("repo"), q["id"]))
            rank_on, rank_off = q["rank"] or 99, (other["rank"] or 99) if other else 99
            if rank_on > rank_off:
                worse_tests.append(q["id"])
    lines = [f"holdout, top-3 with the demotion on minus off: {delta:+d} (on {on['top3']} of {on['n']}, off {off['top3']} of {off['n']})"]
    if delta <= -LOOKS_BAD_BY or worse_tests:
        lines.append("verdict: LOOKS BAD (the demotion-on finalist has at least 3 fewer top-3 hits, or ranks a tests question lower"
                     + (f": {', '.join(worse_tests)}" if worse_tests else "") + "): report it as a limitation; nothing is retuned on the holdout")
    else:
        lines.append("verdict: it does not look bad (not 3 or more top-3 hits fewer, no tests question ranked lower). Six questions cannot confirm the margin")
    return lines


def render_report(baseline, demotion, final_on, final_off, pools) -> str:
    config = final_on[0]["meta"].get("config", {}) if final_on else {}
    lines = ["CLANK RETRIEVAL EVAL: FINAL RESULTS (task I-7.13)", "", "frozen configuration (written into every final file):"]
    lines += [f"  {key}: {value}" for key, value in config.items()]
    lines += ["", "stages (counted questions with an answer; top-1 / top-3 / top-10 / answer in the real context):"]
    for name, runs in (("baseline (no policy, no .rst)", baseline), ("after 7.6: demotion on (before .rst)", demotion), ("final, demotion OFF", final_off), ("final, demotion ON", final_on)):
        row = []
        for label, split in (("tune", "tune"), ("holdout", "holdout"), ("all", None)):
            s = summarize_stage(runs, split)
            row.append(f"{label} {s['top1']}/{s['top3']}/{s['top10']}/{s['context']} of {s['n']}")
        lines.append(f"  {name:<40} " + "   ".join(row))
    lines += [""] + _verdict(final_on, final_off) + ["", "rank grid:", render_grid(grid_rows(baseline, demotion, final_on, final_off, pools)), "", "timings (Air, real model):"]
    lines += timing_lines(baseline, final_on)
    return "\n".join(lines) + "\n"


def main(argv=None, out=print) -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(prog="final_report", description="Write the final results text from the result files.")
    parser.add_argument("--results", default=str(here / "results"), help="folder with the baseline, policy-on and pool files")
    parser.add_argument("--final", default=str(here / "final"), help="folder with the --final runs")
    parser.add_argument("--out", default=str(here / "final" / "RESULTS.txt"))
    parser.add_argument("--pool", action="append", default=[], metavar="REPO=FILE", help="the pool file of a repo inside --results (default {repo}-pool.json)")
    args = parser.parse_args(argv)
    try:
        results, final = Path(args.results), Path(args.final)
        named = dict(item.split("=", 1) for item in args.pool)
        load = lambda folder, name: json.loads((folder / name).read_text())
        baseline = [load(results, f"{r}-baseline.json") for r in REPOS]
        demotion = [load(results, f"{r}-policy-on.json") for r in REPOS]
        pools = [load(results, named.get(r, f"{r}-pool.json")) for r in REPOS]
        final_on = [load(final, f"{r}-final-on.json") for r in REPOS]
        final_off = [load(final, f"{r}-final-off.json") for r in REPOS]
        require_final(final_on + final_off)
        text = render_report(baseline, demotion, final_on, final_off, pools)
    except (ValueError, OSError, KeyError) as problem:
        out(f"error: {problem}")
        return 2
    Path(args.out).write_text(text)
    out(f"wrote {args.out}")
    return 0
