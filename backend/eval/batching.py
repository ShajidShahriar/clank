"""The batching micro-test (task I-7.12, part 1): is embedding in batches of 16 ACROSS files faster than the per-file batches the indexer uses today?

A MEASUREMENT, not a pipeline change. The rule (from the plan, written before any number): build cross-file batching only if the speedup is at least 2x
AND a first full index of a typical repo takes more than 2 minutes; otherwise write down that it is not worth it. Protocol: one warm-up call, excluded; the
two modes alternate in order across the repeats (heat and caching hit both alike); the median of the repeats is reported.

`run_microtest` takes the embed function as a parameter (the script passes `embedder.embed_documents`): only the embedding package, `indexing/embed.py`,
the tests and the scripts may call it.
"""
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from chunker import chunk_file
from file_discovery import discover_files

BATCH_SIZE = 16
SPEEDUP_THAT_COUNTS = 2.0
FULL_INDEX_SECONDS_THAT_COUNT = 120.0
MODES = ("per_file", "cross_file")


def per_file_batches(chunks: list[dict]) -> list[list[str]]:
    """What the indexer does today: one call per file (consecutive chunks of one file), in order."""
    batches, last = [], None
    for c in chunks:
        if c["rel_path"] != last:
            batches.append([])
            last = c["rel_path"]
        batches[-1].append(c["embed_text"])
    return batches


def cross_file_batches(chunks: list[dict], size: int = BATCH_SIZE) -> list[list[str]]:
    """The alternative: calls of `size` chunks whatever the files, in order."""
    if not isinstance(size, int) or isinstance(size, bool) or size < 1:
        raise ValueError(f"size must be a positive integer, got {size!r}")
    texts = [c["embed_text"] for c in chunks]
    return [texts[i:i + size] for i in range(0, len(texts), size)]


def median(values: list[float]) -> float:
    if not values:
        raise ValueError("the median of nothing")
    return statistics.median(values)


def predict_seconds(ms_per_chunk: float, total_chunks: int) -> float:
    return ms_per_chunk * total_chunks / 1000


def decide(speedup: float, full_index_seconds: float) -> tuple[str, str]:
    """("build" | "skip", a sentence). Both conditions must hold: a big enough speedup AND an index slow enough to matter."""
    facts = f"cross-file batching is {speedup:.1f}x faster and a full index takes {full_index_seconds:.0f} s"
    rule = f"the rule needs at least {SPEEDUP_THAT_COUNTS:g}x AND more than {FULL_INDEX_SECONDS_THAT_COUNT:.0f} s"
    if speedup >= SPEEDUP_THAT_COUNTS and full_index_seconds > FULL_INDEX_SECONDS_THAT_COUNT:
        return "build", f"build it: {facts} ({rule}); re-run the crash matrix and the real-kill tests first, they depend on per-file commits"
    return "skip", f"not worth it: {facts}; {rule}"


@dataclass
class MicroResult:
    ms_per_chunk: dict = field(default_factory=dict)    # {mode: median over the repeats}
    speedup: float = 0.0                                # per_file / cross_file (above 1: cross-file is faster)
    runs: list = field(default_factory=list)            # [{"repeat", "mode", "seconds"}] in the order they ran
    order: list = field(default_factory=list)           # the mode order of each repeat
    chunks: int = 0
    files: int = 0


def run_microtest(embed_batch, chunks: list[dict], repeats: int = 3, clock=time.perf_counter, warmup_size: int = BATCH_SIZE) -> MicroResult:
    """Time both modes over the same chunks. `embed_batch(texts)` embeds one call's worth of texts."""
    if not chunks:
        raise ValueError("a micro-test needs chunks")
    if not isinstance(repeats, int) or isinstance(repeats, bool) or repeats < 1:
        raise ValueError(f"repeats must be a positive integer, got {repeats!r}")
    plans = {"per_file": per_file_batches(chunks), "cross_file": cross_file_batches(chunks)}
    embed_batch([c["embed_text"] for c in chunks[:warmup_size]])          # the warm-up call: loads the model, is not timed
    result = MicroResult(chunks=len(chunks), files=len({c["rel_path"] for c in chunks}))
    seconds: dict[str, list[float]] = {mode: [] for mode in MODES}
    for repeat in range(repeats):
        order = MODES if repeat % 2 == 0 else tuple(reversed(MODES))      # alternate which mode goes first
        result.order.append(order)
        for mode in order:
            start = clock()
            for batch in plans[mode]:
                embed_batch(batch)
            elapsed = clock() - start
            seconds[mode].append(elapsed)
            result.runs.append({"repeat": repeat, "mode": mode, "seconds": elapsed})
    result.ms_per_chunk = {mode: median(values) / len(chunks) * 1000 for mode, values in seconds.items()}
    result.speedup = median(seconds["per_file"]) / median(seconds["cross_file"])
    return result


def load_real_chunks(repo_path, limit: int) -> list[dict]:
    """About `limit` real chunks (never more), in file order, taken from files SPREAD EVENLY over the whole repo with all of each file's chunks.

    The first files of a repo can be unlike the rest (a changelog and a readme are most of Express's first 200 chunks), and the whole point of the
    micro-test is the many-small-files case, so the sample keeps the repo's own number of chunks per file.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError(f"limit must be a positive integer, got {limit!r}")
    root = Path(repo_path).resolve()
    per_file = [chunk_file(str(path), repo_root=str(root))
                for path in sorted(discover_files(str(root)), key=lambda p: p.relative_to(root).as_posix())]
    per_file = [chunks for chunks in per_file if chunks]
    total = sum(len(chunks) for chunks in per_file)
    if total <= limit:
        return [c for chunks in per_file for c in chunks]
    wanted = min(len(per_file), max(1, round(limit / (total / len(per_file)))))
    picked = [0] if wanted == 1 else [int(i * (len(per_file) - 1) / (wanted - 1) + 0.5) for i in range(wanted)]
    return [c for index in picked for c in per_file[index]][:limit]
