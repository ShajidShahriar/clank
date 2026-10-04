"""Task I-7.6, step 4: the test/changelog demotion inside `search`.

Hand-made 2-d vectors give EXACT scores: a vector [s, sqrt(1 - s^2)] has cosine s with the question [1, 0]. What is promised:
- the policy ranks over a WIDER pool (max(3k, 30)) and then cuts to k, so a right answer far down the raw list can come up;
- hidden files and orphan vectors never use up pool slots (the existing widening still applies);
- the raw cosine score of every hit is untouched; only the ORDER changes;
- tags are read with ONE batched query, never one per hit;
- a margin only means something for the model it was calibrated on: for another model nothing is demoted and the result says why;
- `test_policy=None` is exactly the old behavior; rank first, expand second, budget third still holds.
"""
import hashlib
import math

import pytest

import chunk_store
from chunker import chunk_file
from embedding import FakeEmbedder
from search import DEFAULT_DEMOTION, DemotionPolicy, build_context, retrieve, search
from vectorstore import InMemoryVectorStore

MODEL = "fake-hash-2"


class Question2D(FakeEmbedder):
    def __init__(self):
        super().__init__(dim=2)

    def embed_query(self, text):
        return [1.0, 0.0]


def vector(score):
    return [score, math.sqrt(1 - score * score)]


@pytest.fixture
def world(conn, tmp_path):
    """build(spec) saves files of hand-made chunks. spec: {rel_path: dict(scores=[...], test=False, log=False, hidden=False)}; chunk ids are `<path>#<i>`."""
    template = tmp_path / "t.py"
    template.write_text("def f():\n    return 1\n")
    base = chunk_file(str(template), repo_root=str(tmp_path))[0]
    store = InMemoryVectorStore()
    store.set_signature(MODEL, 2)
    repo = tmp_path / "repo"
    repo.mkdir()

    def build(spec, orphans=()):
        for rel, info in spec.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(rel)                                          # a real file, so the freshness check finds it unchanged
            file_hash = hashlib.sha1((repo / rel).read_bytes()).hexdigest()
            chunks = [dict(base, id=f"{rel}#{i}", rel_path=rel, symbol=f"s{i}", names=[f"s{i}"], start_line=i + 1, end_line=i + 1,
                           text=f"line {i}", embed_text=f"{rel} · s{i}\nline {i}", content_hash=f"h-{rel}-{i}") for i in range(len(info["scores"]))]
            chunk_store.save_file_chunks(conn, 1, rel, file_hash, chunks, is_test=info.get("test", False), is_changelog=info.get("log", False),
                                         embedded={c["id"]: (MODEL, 2) for c in chunks})
            store.upsert([c["id"] for c in chunks], [vector(s) for s in info["scores"]])
            if info.get("hidden"):
                chunk_store.mark_file_failed(conn, 1, rel, "EmbeddingTooLong: x", file_hash=file_hash, chunker_version="v")
        if orphans:
            store.upsert([f"orphan-{i}" for i in range(len(orphans))], [vector(s) for s in orphans])
        return store
    build.repo = repo
    return build


def policy(margin=0.06, **kw):
    return DemotionPolicy(margin, calibrated_for=kw.pop("calibrated_for", MODEL), **kw)


def ids(result):
    return [h.chunk["id"] for h in result.hits]


# ---- the batched tag lookup

def test_file_tags_returns_the_tags_of_the_files_asked_for(conn, world):
    world({"a.py": dict(scores=[0.5]), "tests/t.py": dict(scores=[0.5], test=True), "CHANGELOG.md": dict(scores=[0.5], log=True)})
    tags = chunk_store.file_tags(conn, 1, ["a.py", "tests/t.py", "CHANGELOG.md", "unknown.py"])
    assert tags == {"a.py": (False, False), "tests/t.py": (True, False), "CHANGELOG.md": (False, True)}
    assert chunk_store.file_tags(conn, 1, []) == {}


def test_file_tags_handles_more_paths_than_one_sql_statement_can_hold(conn, world):
    spec = {f"f{i}.py": dict(scores=[0.5], test=(i % 2 == 0)) for i in range(620)}
    world(spec)
    tags = chunk_store.file_tags(conn, 1, list(spec))
    assert len(tags) == 620 and tags["f0.py"] == (True, False) and tags["f1.py"] == (False, False)


def test_file_tags_asks_in_batches_and_returns_real_booleans(conn, world, monkeypatch):
    spec = {f"f{i}.py": dict(scores=[0.5], test=(i % 2 == 0)) for i in range(250)}
    world(spec)
    monkeypatch.setattr(chunk_store, "_SQL_BATCH", 100)
    statements = []
    conn.set_trace_callback(statements.append)
    tags = chunk_store.file_tags(conn, 1, list(spec))
    conn.set_trace_callback(None)
    assert len([s for s in statements if "is_test" in s]) == 3 and len(tags) == 250, "250 paths in batches of 100: three queries"
    assert tags["f0.py"][0] is True and tags["f0.py"][1] is False and tags["f1.py"][0] is False


def test_other_projects_tags_are_not_mixed_in(conn, world):
    world({"a.py": dict(scores=[0.5], test=True)})
    assert chunk_store.file_tags(conn, 2, ["a.py"]) == {}


def test_search_reads_the_tags_with_one_query_whatever_the_number_of_files(conn, world):
    store = world({f"f{i}.py": dict(scores=[0.9 - 0.005 * i], test=(i % 2 == 0)) for i in range(40)})
    statements = []
    conn.set_trace_callback(statements.append)
    search(conn, 1, Question2D(), store, "q", k=5, test_policy=policy())
    conn.set_trace_callback(None)
    assert len([s for s in statements if "is_test" in s]) == 1, "one tag query for all the files, however many hits"


# ---- the rule, through search

def test_the_demotion_changes_the_order_but_never_the_scores(conn, world):
    store = world({"tests/t.py": dict(scores=[0.70], test=True), "src/a.py": dict(scores=[0.66])})
    result = search(conn, 1, Question2D(), store, "q", k=2, test_policy=policy(0.06))
    assert ids(result) == ["src/a.py#0", "tests/t.py#0"]
    assert [round(h.score, 6) for h in result.hits] == [0.66, 0.70]
    assert result.ranking == "demoted" and result.ranking_note is None


def test_a_test_that_wins_by_more_than_the_margin_stays_first(conn, world):
    store = world({"tests/t.py": dict(scores=[0.80], test=True), "src/a.py": dict(scores=[0.66])})
    assert ids(search(conn, 1, Question2D(), store, "q", k=2, test_policy=policy(0.06))) == ["tests/t.py#0", "src/a.py#0"]


def test_a_changelog_takes_twice_the_margin(conn, world):
    store = world({"CHANGELOG.md": dict(scores=[0.75], log=True), "src/a.py": dict(scores=[0.66])})
    assert ids(search(conn, 1, Question2D(), store, "q", k=2, test_policy=policy(0.06))) == ["src/a.py#0", "CHANGELOG.md#0"]


def test_a_question_that_asks_for_tests_is_not_demoted_for_them(conn, world):
    store = world({"tests/t.py": dict(scores=[0.70], test=True), "src/a.py": dict(scores=[0.66])})
    result = search(conn, 1, Question2D(), store, "where is it tested?", k=2, test_policy=policy(0.06))
    assert ids(result) == ["tests/t.py#0", "src/a.py#0"]


def test_the_same_number_of_hits_comes_out_and_never_more_than_k(conn, world):
    store = world({"tests/t.py": dict(scores=[0.9 - 0.01 * i for i in range(8)], test=True), "src/a.py": dict(scores=[0.5, 0.4])})
    assert len(search(conn, 1, Question2D(), store, "q", k=4, test_policy=policy()).hits) == 4
    assert len(search(conn, 1, Question2D(), store, "q", k=50, test_policy=policy()).hits) == 10


# ---- the wider pool

def crowded(extra_hidden=0):
    spec = {"tests/t.py": dict(scores=[0.80 - 0.005 * i for i in range(24)], test=True), "src/answer.py": dict(scores=[0.68])}
    if extra_hidden:
        spec["bad.py"] = dict(scores=[0.95 - 0.001 * i for i in range(extra_hidden)], hidden=True)
    return spec


def test_an_answer_far_down_the_raw_list_comes_up_because_the_pool_is_wider_than_k(conn, world):
    store = world(crowded())                                  # the answer is raw rank 25; k is 3
    raw = search(conn, 1, Question2D(), store, "q", k=3, test_policy=None)
    assert "src/answer.py#0" not in ids(raw)
    assert ids(search(conn, 1, Question2D(), store, "q", k=3, test_policy=policy(0.15)))[0] == "src/answer.py#0"


def test_hidden_files_and_orphans_do_not_use_up_pool_slots(conn, world):
    store = world(crowded(extra_hidden=40), orphans=[0.99 - 0.001 * i for i in range(40)])
    result = search(conn, 1, Question2D(), store, "q", k=3, test_policy=policy(0.15))
    assert ids(result)[0] == "src/answer.py#0"
    assert all(h.chunk["rel_path"] != "bad.py" for h in result.hits) and set(result.hidden_files) == {"bad.py"}


def test_the_widening_continues_until_the_whole_pool_is_visible_not_just_k(conn, world):
    # 45 hidden chunks are the nearest, so the first fetch (60) holds 45 hidden and only 15 visible tests: that is more than k (3) but less than the pool (30)
    store = world(crowded(extra_hidden=45))
    result = search(conn, 1, Question2D(), store, "q", k=3, test_policy=policy(0.15))
    assert ids(result)[0] == "src/answer.py#0", "the answer sits behind the first 15 visible tests: stopping at k would never see it"


def test_the_pool_is_three_times_k_when_that_is_more_than_thirty(conn, world):
    tests = dict(scores=[0.90 - 0.001 * i for i in range(44)], test=True)         # 44 tests, then the answer at raw rank 45
    store = world({"tests/t.py": tests, "src/answer.py": dict(scores=[0.80])})
    result = search(conn, 1, Question2D(), store, "q", k=15, test_policy=policy(0.15))    # pool = max(45, 30) = 45: the answer is the 45th and is reached
    assert "src/answer.py#0" in ids(result)
    result = search(conn, 1, Question2D(), store, "q", k=5, test_policy=policy(0.15))     # pool = 30: the 45th is out of reach
    assert "src/answer.py#0" not in ids(result)


# ---- when the policy is off, or does not apply

def test_no_policy_is_exactly_the_raw_order(conn, world):
    store = world({"tests/t.py": dict(scores=[0.70], test=True), "src/a.py": dict(scores=[0.66])})
    result = search(conn, 1, Question2D(), store, "q", k=2, test_policy=None)
    assert ids(result) == ["tests/t.py#0", "src/a.py#0"] and result.ranking == "raw" and result.ranking_note is None


def test_a_margin_calibrated_for_another_model_demotes_nothing_and_says_why(conn, world):
    store = world({"tests/t.py": dict(scores=[0.70], test=True), "src/a.py": dict(scores=[0.66])})
    result = search(conn, 1, Question2D(), store, "q", k=2, test_policy=policy(0.06, calibrated_for="some-other-model@abc"))
    assert ids(result) == ["tests/t.py#0", "src/a.py#0"] and result.ranking == "raw"
    assert "some-other-model@abc" in result.ranking_note and MODEL in result.ranking_note and "not demoted" in result.ranking_note


def test_a_bad_policy_argument_is_refused(conn, world):
    store = world({"src/a.py": dict(scores=[0.66])})
    with pytest.raises(ValueError, match="test_policy"):
        search(conn, 1, Question2D(), store, "q", k=2, test_policy="demote")


def test_the_default_is_the_margin_found_in_the_eval_and_the_model_it_was_found_for():
    assert (DEFAULT_DEMOTION.margin, DEFAULT_DEMOTION.changelog_margin) == (0.15, None)
    assert DEFAULT_DEMOTION.calibrated_for == "qwen3-embedding:0.6b@ac6da0dfba84"


def test_the_default_applies_to_no_other_model_so_every_fake_embedder_test_is_unchanged(conn, world):
    store = world({"tests/t.py": dict(scores=[0.70], test=True), "src/a.py": dict(scores=[0.66])})
    result = search(conn, 1, Question2D(), store, "q", k=2)               # default policy, a fake model
    assert ids(result) == ["tests/t.py#0", "src/a.py#0"] and result.ranking == "raw" and result.ranking_note


def test_a_policy_cannot_be_changed_after_it_is_made():
    import dataclasses
    p = DemotionPolicy(0.1, calibrated_for="m")
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.margin = 0.0


def test_a_margin_must_not_be_negative_even_through_the_policy_object():
    with pytest.raises(ValueError, match="margin"):
        DemotionPolicy(-0.1, calibrated_for="m")
    with pytest.raises(ValueError, match="changelog_margin"):
        DemotionPolicy(0.1, changelog_margin=-0.5, calibrated_for="m")
    with pytest.raises(ValueError, match="calibrated_for"):
        DemotionPolicy(0.1, calibrated_for="")


# ---- rank first, expand second, budget third

def test_retrieve_follows_the_demoted_order_and_the_budget_cuts_from_the_end(conn, world):
    store = world({"tests/t.py": dict(scores=[0.70], test=True), "src/a.py": dict(scores=[0.66])})
    r = retrieve(conn, 1, world.repo, Question2D(), store, "q", k=2, max_tokens=6000, test_policy=policy(0.06))
    assert [p.rel_path for p in r.passages] == ["src/a.py", "tests/t.py"] and r.ranking_note is None
    r = retrieve(conn, 1, world.repo, Question2D(), store, "q", k=2, max_tokens=6000, test_policy=None)
    assert [p.rel_path for p in r.passages] == ["tests/t.py", "src/a.py"]
    other = retrieve(conn, 1, world.repo, Question2D(), store, "q", k=2, max_tokens=6000, test_policy=policy(0.06, calibrated_for="x@1"))
    assert "x@1" in other.ranking_note and [p.rel_path for p in other.passages] == ["tests/t.py", "src/a.py"]
    tight = retrieve(conn, 1, world.repo, Question2D(), store, "q", k=2, max_tokens=1, test_policy=policy(0.06))
    assert [p.rel_path for p in tight.passages] == ["src/a.py"] and [p.rel_path for p in tight.dropped] == ["tests/t.py"], "the budget cuts from the END of the demoted order"


def test_build_context_puts_the_code_first_and_carries_the_ranking_note(conn, world):
    store = world({"tests/t.py": dict(scores=[0.70], test=True), "src/a.py": dict(scores=[0.66])})
    ctx = build_context(conn, 1, world.repo, Question2D(), store, "q", k=2, max_tokens=6000, test_policy=policy(0.06))
    assert [p.rel_path for p in ctx.passages] == ["src/a.py", "tests/t.py"] and ctx.ranking_note is None
    assert ctx.text.index("src/a.py") < ctx.text.index("tests/t.py")
    other = build_context(conn, 1, world.repo, Question2D(), store, "q", k=2, max_tokens=6000, test_policy=policy(0.06, calibrated_for="x@1"))
    assert "x@1" in other.ranking_note and "x@1" not in other.text, "the note is for the caller; the LLM is not told about it"
