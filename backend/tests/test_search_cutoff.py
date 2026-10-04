"""Task I-7.8: an OPTIONAL relevance cutoff in `search`, default off. The eval did not find a floor worth adopting (devlog 74), but the app must be able
to use one later, so the mechanism exists and is tested with hand-made hits.

- `min_score`: drop hits whose RAW cosine is below it. `margin`: drop hits more than `margin` below the best raw score of the question. Both can be given.
- It looks at RAW scores (the demotion never changes them) and is applied to the pool BEFORE the demotion orders it and before the cut to k.
- A cutoff is only meaningful for the model it was measured on: for any other embedder nothing is cut and the result says why.
- When it cuts everything the answer is "no hits", never an error. Searching must not scan the whole store just because the cutoff keeps removing hits.
"""
import dataclasses

import pytest

from hand_made import MODEL, CountingStore, Question2D, make_world
from search import DemotionPolicy, RelevanceCutoff, build_context, retrieve, search


@pytest.fixture
def world(conn, tmp_path):
    return make_world(conn, tmp_path)


def cutoff(**kw):
    return RelevanceCutoff(calibrated_for=kw.pop("calibrated_for", MODEL), **kw)


def ids(result):
    return [h.chunk["id"] for h in result.hits]


SPREAD = {"a.py": dict(scores=[0.80, 0.70, 0.62, 0.55, 0.40, 0.30])}


def test_min_score_drops_the_hits_below_it_and_keeps_the_order(conn, world):
    result = search(conn, 1, Question2D(), world(SPREAD), "q", k=10, test_policy=None, cutoff=cutoff(min_score=0.60))
    assert ids(result) == ["a.py#0", "a.py#1", "a.py#2"] and result.below_cutoff == 3


def test_a_hit_exactly_at_the_floor_is_kept(conn, world):
    result = search(conn, 1, Question2D(), world(SPREAD), "q", k=10, test_policy=None, cutoff=cutoff(min_score=0.70))
    assert ids(result) == ["a.py#0", "a.py#1"]


def test_the_cutoff_is_applied_before_the_cut_to_k(conn, world):
    store = world({"a.py": dict(scores=[0.9 - 0.01 * i for i in range(10)] + [0.2])})
    assert len(search(conn, 1, Question2D(), store, "q", k=3, test_policy=None, cutoff=cutoff(min_score=0.5)).hits) == 3
    assert len(search(conn, 1, Question2D(), store, "q", k=30, test_policy=None, cutoff=cutoff(min_score=0.5)).hits) == 10


def test_a_margin_is_measured_from_the_best_raw_score(conn, world):
    result = search(conn, 1, Question2D(), world(SPREAD), "q", k=10, test_policy=None, cutoff=cutoff(margin=0.15))
    assert ids(result) == ["a.py#0", "a.py#1"]                    # best 0.80: keep 0.65 and above (0.62 is out)
    assert ids(search(conn, 1, Question2D(), world.store, "q", k=10, test_policy=None, cutoff=cutoff(margin=0.5))) == [f"a.py#{i}" for i in range(6)]


def test_a_margin_alone_never_returns_nothing_because_the_best_hit_always_survives(conn, world):
    result = search(conn, 1, Question2D(), world({"a.py": dict(scores=[0.30, 0.29])}), "q", k=5, test_policy=None, cutoff=cutoff(margin=0.0))
    assert ids(result) == ["a.py#0"]


def test_min_score_and_margin_together_apply_the_stricter_one(conn, world):
    store = world(SPREAD)                                           # 0.80, 0.70, 0.62, 0.55, 0.40, 0.30
    margin_stricter = search(conn, 1, Question2D(), store, "q", k=10, test_policy=None, cutoff=cutoff(min_score=0.45, margin=0.20))
    assert len(margin_stricter.hits) == 3, "limit = max(0.45, 0.80 - 0.20) = 0.60"
    floor_stricter = search(conn, 1, Question2D(), store, "q", k=10, test_policy=None, cutoff=cutoff(min_score=0.65, margin=0.40))
    assert len(floor_stricter.hits) == 2, "limit = max(0.65, 0.80 - 0.40) = 0.65"


def test_when_everything_is_below_the_floor_the_answer_is_no_hits_not_an_error(conn, world):
    result = search(conn, 1, Question2D(), world(SPREAD), "q", k=10, test_policy=None, cutoff=cutoff(min_score=0.95))
    assert result.hits == [] and result.below_cutoff == 6 and result.hidden_files == {}


def test_the_cutoff_uses_raw_scores_even_when_the_demotion_reordered_the_hits(conn, world):
    store = world({"tests/t.py": dict(scores=[0.70], test=True), "src/a.py": dict(scores=[0.66]), "src/b.py": dict(scores=[0.50])})
    result = search(conn, 1, Question2D(), store, "q", k=5, cutoff=cutoff(min_score=0.60), test_policy=DemotionPolicy(0.06, calibrated_for=MODEL))
    assert ids(result) == ["src/a.py#0", "tests/t.py#0"], "the demoted test (adjusted 0.64, raw 0.70) stays; the code below the floor (0.50) goes"
    assert [round(h.score, 6) for h in result.hits] == [0.66, 0.70]


def test_the_demotion_pool_is_made_of_hits_that_passed_the_cutoff(conn, world):
    # without a cutoff the code (raw 0.70, adjusted 0.70) would come first and the tests (adjusted 0.65 and below) follow
    store = world({"tests/t.py": dict(scores=[0.80 - 0.01 * i for i in range(10)], test=True), "src/a.py": dict(scores=[0.70])})
    policy = DemotionPolicy(0.15, calibrated_for=MODEL)
    assert ids(search(conn, 1, Question2D(), store, "q", k=3, test_policy=policy))[0] == "src/a.py#0"
    result = search(conn, 1, Question2D(), store, "q", k=3, cutoff=cutoff(min_score=0.75), test_policy=policy)
    assert "src/a.py#0" not in ids(result) and len(result.hits) == 3, "the cut happens before the cut to k, so three hits above the floor remain"
    assert result.below_cutoff == 5 and result.ranking == "demoted", "4 tests (0.74 to 0.71) and the code (0.70) are below 0.75: the count is reported on the demoted path too"
    other = search(conn, 1, Question2D(), store, "q", k=3, cutoff=cutoff(min_score=0.75, calibrated_for="o@1"), test_policy=policy)
    assert other.cutoff_note and "o@1" in other.cutoff_note and "src/a.py#0" in ids(other), "and so is the note when the cutoff does not apply"


def test_a_cutoff_measured_for_another_model_cuts_nothing_and_says_why(conn, world):
    result = search(conn, 1, Question2D(), world(SPREAD), "q", k=10, test_policy=None, cutoff=cutoff(min_score=0.60, calibrated_for="other@1"))
    assert len(result.hits) == 6 and result.below_cutoff == 0
    assert "other@1" in result.cutoff_note and MODEL in result.cutoff_note and "not applied" in result.cutoff_note


def test_no_cutoff_is_the_old_behavior_and_leaves_no_note(conn, world):
    result = search(conn, 1, Question2D(), world(SPREAD), "q", k=10, test_policy=None)
    assert len(result.hits) == 6 and result.below_cutoff == 0 and result.cutoff_note is None


def test_a_bad_cutoff_argument_is_refused(conn, world):
    with pytest.raises(ValueError, match="cutoff"):
        search(conn, 1, Question2D(), world(SPREAD), "q", k=5, cutoff=0.6)


@pytest.mark.parametrize("kw, words", [
    (dict(), "min_score or a margin"),
    (dict(min_score=float("nan")), "min_score"),
    (dict(min_score="0.5"), "min_score"),
    (dict(min_score=True), "min_score"),
    (dict(margin=True), "margin"),
    (dict(min_score=1.5), "min_score"),
    (dict(min_score=-1.5), "min_score"),
    (dict(margin=-0.1), "margin"),
    (dict(margin=float("inf")), "margin"),
    (dict(min_score=0.5, calibrated_for=""), "calibrated_for"),
])
def test_a_cutoff_object_refuses_nonsense(kw, words):
    with pytest.raises(ValueError, match=words):
        RelevanceCutoff(**{"calibrated_for": "m@1", **kw})


def test_a_cutoff_cannot_be_changed_after_it_is_made():
    c = RelevanceCutoff(min_score=0.5, calibrated_for="m")
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.min_score = 0.0


def test_searching_does_not_scan_the_whole_store_just_because_the_cutoff_keeps_removing_hits(conn, tmp_path):
    counting = CountingStore()
    build = make_world(conn, tmp_path, store=counting)
    build({"a.py": dict(scores=[0.9, 0.8]), "far.py": dict(scores=[0.3 - 0.0005 * i for i in range(400)])})
    result = search(conn, 1, Question2D(), counting, "q", k=3, test_policy=None, cutoff=cutoff(min_score=0.6))
    assert len(result.hits) == 2 and result.below_cutoff >= 1
    assert counting.asked == [20], "one query: the hits it got back already ended below the floor, so nothing nearer could still be missing"


def test_a_cutoff_still_looks_past_hidden_files_to_the_hits_that_pass_it(conn, world):
    # 45 hidden chunks are the nearest, so the first fetch holds no visible hit at all: the search must go on, not stop at the first fetch
    store = world({"bad.py": dict(scores=[0.99 - 0.001 * i for i in range(45)], hidden=True), "a.py": dict(scores=[0.90 - 0.01 * i for i in range(10)])})
    result = search(conn, 1, Question2D(), store, "q", k=5, test_policy=None, cutoff=cutoff(min_score=0.6))
    assert len(result.hits) == 5 and all(h.chunk["rel_path"] == "a.py" for h in result.hits) and set(result.hidden_files) == {"bad.py"}


def test_with_no_cutoff_the_widening_still_runs_until_k_hits_are_visible(conn, tmp_path):
    counting = CountingStore()
    build = make_world(conn, tmp_path, store=counting)
    build({"a.py": dict(scores=[0.9 - 0.001 * i for i in range(60)])})
    assert len(search(conn, 1, Question2D(), counting, "q", k=50, test_policy=None).hits) == 50


def test_retrieve_and_build_context_pass_the_cutoff_on_and_report_what_was_cut(conn, world):
    store = world({"a.py": dict(scores=[0.80, 0.30])})
    r = retrieve(conn, 1, world.repo, Question2D(), store, "q", k=5, max_tokens=6000, test_policy=None, cutoff=cutoff(min_score=0.5))
    assert len(r.passages) == 1 and r.below_cutoff == 1
    ctx = build_context(conn, 1, world.repo, Question2D(), store, "q", k=5, max_tokens=6000, test_policy=None, cutoff=cutoff(min_score=0.95))
    assert ctx.passages == [] and ctx.below_cutoff == 2 and "No matching code was found" in ctx.text
    other = build_context(conn, 1, world.repo, Question2D(), store, "q", k=5, max_tokens=6000, test_policy=None, cutoff=cutoff(min_score=0.95, calibrated_for="x@1"))
    assert "x@1" in other.cutoff_note and "x@1" not in other.text and len(other.passages) == 2
