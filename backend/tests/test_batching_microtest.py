"""Task I-7.12 (part 1): the batching micro-test. A MEASUREMENT, not a pipeline change: does embedding in batches of 16 ACROSS files beat the
per-file batches the indexer uses today? (eval/batching.py; scripts/batching_microtest.py runs it on the real model.)

The rule (from the plan, written before any number): build cross-file batching ONLY if the speedup is at least 2x AND a first full index of a typical repo
takes more than 2 minutes; otherwise write down that it is not worth it. The measurement protocol: one warm-up call, excluded; the two modes alternate
in order across repeats (so heat and caching hit both alike); the median of the repeats is reported.
"""
import pytest

from embedding import FakeEmbedder
from eval.batching import (BATCH_SIZE, FULL_INDEX_SECONDS_THAT_COUNT, SPEEDUP_THAT_COUNTS, cross_file_batches, decide, load_real_chunks, median,
                           per_file_batches, predict_seconds, run_microtest)


def chunk(rel, i):
    return {"rel_path": rel, "embed_text": f"{rel} #{i}"}


def sample():
    return [chunk("a.py", 0), chunk("a.py", 1), chunk("b.py", 0), chunk("c.py", 0), chunk("c.py", 1), chunk("c.py", 2)]


# ---- the two ways of grouping

def test_per_file_batches_keep_each_file_in_its_own_call_in_order():
    assert per_file_batches(sample()) == [["a.py #0", "a.py #1"], ["b.py #0"], ["c.py #0", "c.py #1", "c.py #2"]]


def test_a_file_that_comes_back_later_is_a_new_batch_not_merged():
    chunks = [chunk("a.py", 0), chunk("b.py", 0), chunk("a.py", 1)]
    assert per_file_batches(chunks) == [["a.py #0"], ["b.py #0"], ["a.py #1"]]


def test_cross_file_batches_fill_each_call_up_to_the_batch_size_whatever_the_files():
    chunks = [chunk(f"f{i}.py", 0) for i in range(35)]
    batches = cross_file_batches(chunks)
    assert [len(b) for b in batches] == [16, 16, 3] and BATCH_SIZE == 16
    assert [t for b in batches for t in b] == [c["embed_text"] for c in chunks], "every chunk exactly once, in order"
    assert cross_file_batches(chunks, size=10)[0] == [c["embed_text"] for c in chunks[:10]]


def test_both_groupings_cover_every_chunk_exactly_once():
    chunks = [chunk(f"f{i // 3}.py", i) for i in range(50)]
    for batches in (per_file_batches(chunks), cross_file_batches(chunks)):
        assert sorted(t for b in batches for t in b) == sorted(c["embed_text"] for c in chunks)


def test_nothing_gives_no_batches_and_a_bad_size_is_refused():
    assert per_file_batches([]) == [] and cross_file_batches([]) == []
    with pytest.raises(ValueError, match="size"):
        cross_file_batches(sample(), size=0)


# ---- the numbers

def test_median_of_an_odd_and_an_even_number_of_values_and_of_nothing():
    assert median([3.0, 1.0, 2.0]) == 2.0 and median([4.0, 1.0, 2.0, 3.0]) == 2.5
    with pytest.raises(ValueError, match="median of nothing"):
        median([])


def test_the_predicted_full_index_time_is_ms_per_chunk_times_the_chunks_of_the_repo():
    assert predict_seconds(ms_per_chunk=100.0, total_chunks=1027) == pytest.approx(102.7)


@pytest.mark.parametrize("speedup, seconds, verdict", [
    (2.0, 121.0, "build"), (3.0, 300.0, "build"),
    (1.99, 300.0, "skip"), (5.0, 120.0, "skip"), (5.0, 60.0, "skip"), (1.1, 30.0, "skip"),
])
def test_cross_file_batching_is_built_only_if_it_is_at_least_twice_as_fast_and_a_full_index_takes_over_two_minutes(speedup, seconds, verdict):
    assert decide(speedup, seconds)[0] == verdict
    assert (SPEEDUP_THAT_COUNTS, FULL_INDEX_SECONDS_THAT_COUNT) == (2.0, 120.0)


def test_the_decision_names_both_numbers_in_a_sentence():
    text = decide(1.3, 190.0)[1]
    assert "1.3" in text and "190" in text and "not worth" in text
    assert "build" in decide(2.5, 200.0)[1].lower()


# ---- the protocol, with a fake clock so the test is exact

class ClockedEmbedder(FakeEmbedder):
    """Every embed_documents call costs 10 ms plus 1 ms per text on a fake clock (so many small calls are slower than few big ones)."""

    def __init__(self, clock):
        super().__init__()
        self.clock, self.log = clock, []

    def embed_documents(self, texts, ids=None):
        self.log.append(len(texts))
        self.clock.now += 0.010 + 0.001 * len(texts)
        return super().embed_documents(texts, ids)


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def test_the_microtest_warms_up_once_excludes_it_alternates_the_order_and_reports_medians():
    clock = Clock()
    embedder = ClockedEmbedder(clock)
    chunks = [chunk(f"f{i // 4}.py", i) for i in range(64)]                  # 16 files of 4 chunks
    r = run_microtest(embedder.embed_documents, chunks, repeats=3, clock=clock)
    assert embedder.log[0] == 16, "one warm-up call of one batch, before anything is timed"
    assert r.order == [("per_file", "cross_file"), ("cross_file", "per_file"), ("per_file", "cross_file")]
    per_file_seconds = 16 * (0.010 + 0.004)                                  # 16 calls of 4 chunks
    cross_seconds = 4 * (0.010 + 0.016)                                      # 4 calls of 16 chunks
    assert r.ms_per_chunk["per_file"] == pytest.approx(per_file_seconds / 64 * 1000)
    assert r.ms_per_chunk["cross_file"] == pytest.approx(cross_seconds / 64 * 1000)
    assert r.speedup == pytest.approx(per_file_seconds / cross_seconds)
    assert len(r.runs) == 6 and sum(embedder.log[1:]) == 6 * 64, "the warm-up is not one of the six timed runs"
    assert r.chunks == 64 and r.files == 16


def test_the_median_is_taken_over_the_repeats_so_one_slow_run_does_not_count():
    clock = Clock()

    class Spiky(ClockedEmbedder):
        calls = 0

        def embed_documents(self, texts, ids=None):
            Spiky.calls += 1
            if Spiky.calls == 6:                                              # one run of one mode is hit by a 10 s hiccup
                self.clock.now += 10.0
            return super().embed_documents(texts, ids)

    r = run_microtest(Spiky(clock).embed_documents, [chunk(f"f{i // 4}.py", i) for i in range(64)], repeats=3, clock=clock)
    assert r.ms_per_chunk["per_file"] == pytest.approx(16 * 0.014 / 64 * 1000) and r.ms_per_chunk["cross_file"] == pytest.approx(4 * 0.026 / 64 * 1000)


def test_a_microtest_needs_chunks_and_at_least_one_repeat():
    clock = Clock()
    with pytest.raises(ValueError, match="chunks"):
        run_microtest(ClockedEmbedder(clock).embed_documents, [], clock=clock)
    with pytest.raises(ValueError, match="repeats"):
        run_microtest(ClockedEmbedder(clock).embed_documents, sample(), repeats=0, clock=clock)


def test_the_runs_record_mode_repeat_and_seconds():
    clock = Clock()
    r = run_microtest(ClockedEmbedder(clock).embed_documents, [chunk(f"f{i // 2}.py", i) for i in range(32)], repeats=2, clock=clock)
    assert [(x["repeat"], x["mode"]) for x in r.runs] == [(0, "per_file"), (0, "cross_file"), (1, "cross_file"), (1, "per_file")]
    assert all(x["seconds"] > 0 for x in r.runs)


# ---- real chunks from a repo

def make_repo(tmp_path, files=10, per_file=3):
    for n in range(files):
        (tmp_path / f"m{n:02d}.py").write_text("\n\n".join(f"def f{n}_{i}():\n    return {i}\n" for i in range(per_file)))
    return tmp_path


def test_real_chunks_come_from_the_real_chunker_and_stop_at_the_limit(tmp_path):
    chunks = load_real_chunks(make_repo(tmp_path, files=3, per_file=5), limit=7)
    assert 0 < len(chunks) <= 7 and all(c["embed_text"] and c["rel_path"] for c in chunks), "whole files: one file of 5 chunks is the closest to 7 without going over"
    assert {c["rel_path"] for c in chunks} == {"m00.py"}, "a sample of one file is the FIRST file"
    assert [c["rel_path"] for c in chunks] == sorted(c["rel_path"] for c in chunks), "in file order"
    assert len(load_real_chunks(tmp_path, limit=1000)) == 15


def test_the_limit_is_never_exceeded_even_when_whole_files_would_go_over_it(tmp_path):
    chunks = load_real_chunks(make_repo(tmp_path, files=4, per_file=5), limit=8)     # about 1.6 files: two whole files would be 10 chunks
    assert len(chunks) == 8


def test_the_sample_is_spread_over_the_whole_repo_not_taken_from_its_first_files(tmp_path):
    # the first files of a real repo can be huge (a changelog, a readme): taking the first N chunks would measure ONE kind of file
    chunks = load_real_chunks(make_repo(tmp_path, files=10, per_file=3), limit=9)
    files = sorted({c["rel_path"] for c in chunks})
    assert files[0] == "m00.py" and files[-1] == "m09.py", "the first and the last file are both in the sample"
    assert [c["rel_path"] for c in chunks] == sorted(c["rel_path"] for c in chunks), "and the sample is in file order"
    assert len(chunks) == 9 and 3 <= len(files) <= 4


def test_the_sample_keeps_the_repos_own_number_of_chunks_per_file(tmp_path):
    chunks = load_real_chunks(make_repo(tmp_path, files=40, per_file=4), limit=40)
    per_file = {}
    for c in chunks:
        per_file[c["rel_path"]] = per_file.get(c["rel_path"], 0) + 1
    assert len(per_file) == 10 and set(per_file.values()) == {4}, "10 whole files of 4 chunks, not 40 chunks from one file"


def test_a_repo_smaller_than_the_limit_gives_all_its_chunks(tmp_path):
    assert len(load_real_chunks(make_repo(tmp_path, files=2, per_file=3), limit=100)) == 6


def test_an_empty_repo_gives_no_chunks_and_a_bad_limit_is_refused(tmp_path):
    assert load_real_chunks(tmp_path, limit=10) == []
    with pytest.raises(ValueError, match="limit"):
        load_real_chunks(tmp_path, limit=0)


def test_empty_files_do_not_count_as_files_when_the_sample_is_spread(tmp_path):
    for n in range(10):                                   # a00 empty, b01 with chunks, c02 empty, ... (an empty file has no chunks)
        name = f"{'abcdefghij'[n]}{n:02d}.py"
        (tmp_path / name).write_text("" if n % 2 == 0 else "\n\n".join(f"def f{n}_{i}():\n    return {i}\n" for i in range(4)))
    chunks = load_real_chunks(tmp_path, limit=8)
    assert len(chunks) == 8 and sorted({c["rel_path"] for c in chunks}) == ["b01.py", "j09.py"], "the first and the last file that really has chunks"
