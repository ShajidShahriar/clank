"""Manual script, not a test: the batching micro-test on the REAL model (7.12). One sitting; about 1,200 embeddings (200 chunks x 2 modes x 3 repeats).
Run from backend/:  uv run python scripts/batching_microtest.py --repo /path/to/express
Takes ~200 real chunks of the repo, embeds them as per-file batches (what the indexer does) and as batches of 16 across files, one excluded warm-up call
first, the two modes alternating in order, 3 repeats, and prints the median ms per chunk, the speedup and the verdict of the rule (eval/batching.py).
"""
import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embedding import OllamaEmbedder
from eval.batching import decide, load_real_chunks, per_file_batches, predict_seconds, run_microtest

# Cold full-index times measured in the baselines (devlog 68 to 70), and the chunk count of each repo.
BASELINES = {"clank": (101.5, 957), "flask": (110.4, 898), "express": (189.9, 1027)}

parser = argparse.ArgumentParser()
parser.add_argument("--repo", required=True)
parser.add_argument("--chunks", type=int, default=200)
parser.add_argument("--repeats", type=int, default=3)
args = parser.parse_args()

chunks = load_real_chunks(args.repo, args.chunks)
embedder = OllamaEmbedder()
result = run_microtest(embedder.embed_documents, chunks, repeats=args.repeats)

sizes = sorted(len(batch) for batch in per_file_batches(chunks))
print(f"{result.chunks} real chunks from {result.files} files ({result.chunks / result.files:.1f} chunks per file; per-file calls of {sizes[0]} to {sizes[-1]} chunks), "
      f"{args.repeats} repeats, warm-up excluded")
for repeat, order in enumerate(result.order):
    row = {r["mode"]: r["seconds"] for r in result.runs if r["repeat"] == repeat}
    print(f"  repeat {repeat}: order {' then '.join(order)}   per_file {row['per_file']:.2f} s   cross_file {row['cross_file']:.2f} s")
print(f"median ms per chunk: per_file {result.ms_per_chunk['per_file']:.1f}   cross_file {result.ms_per_chunk['cross_file']:.1f}   speedup {result.speedup:.2f}x")
for repo, (cold, total) in BASELINES.items():
    print(f"  {repo:<8} measured cold index {cold:>6.1f} s ({total} chunks); embedding alone would take {predict_seconds(result.ms_per_chunk['per_file'], total):6.1f} s per-file, "
          f"{predict_seconds(result.ms_per_chunk['cross_file'], total):6.1f} s cross-file")
typical = statistics.median(cold for cold, _ in BASELINES.values())
verdict, sentence = decide(result.speedup, typical)
print(f"typical repo = the median cold index of the three eval repos = {typical:.1f} s")
print(f"verdict: {sentence}")
