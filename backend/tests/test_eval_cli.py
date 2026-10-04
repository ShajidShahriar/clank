"""Task I-7.3, the command line around the runner (eval/cli.py; scripts/run_eval.py only calls it). Fake embedder, tiny repo, no Ollama.

What it must protect: the user's real data (it refuses to run without an explicit CLANK_DATA_DIR), the pinned commits (it refuses a repo that is
not at the pinned commit), and the holdout split (it does not print holdout numbers unless asked: "look at the holdout once, at the very end").
"""
import json
import subprocess

import pytest
import yaml

import db
from embedding import FakeEmbedder
from eval.cli import main
from vectorstore import InMemoryVectorStore

FILES = {
    "shop.py": "def order_total(items):\n    return sum(i.price for i in items)\n",
    "net.py": "def retry_request(url, attempts=3):\n    for _ in range(attempts):\n        pass\n    return None\n",
}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=a@b.c", "-c", "user.name=t", *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def tiny(tmp_path):
    root = tmp_path / "tiny"
    root.mkdir()
    for rel, text in FILES.items():
        (root / rel).write_text(text)
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "x")
    return root, git(root, "rev-parse", "HEAD")


def write_questions(tmp_path, rev, texts=None):
    texts = texts or {}
    qs = [
        {"id": "q01", "repo": "tiny", "kind": "name", "split": "tune", "question": texts.get("q01", "order total"), "expect": [{"path": "shop.py", "symbol": "order_total"}]},
        {"id": "q02", "repo": "tiny", "kind": "name", "split": "tune", "question": texts.get("q02", "retry"), "expect": [{"path": "net.py", "symbol": "retry_request"}]},
        {"id": "q03", "repo": "tiny", "kind": "concept", "split": "holdout", "question": texts.get("q03", "totals"), "expect": [{"path": "shop.py", "symbol": "order_total"}]},
        {"id": "q04", "repo": "tiny", "kind": "negative", "split": "tune", "question": "weather", "expect": []},
    ]
    path = tmp_path / "questions.yaml"
    path.write_text(yaml.safe_dump({"repos": {"tiny": {"path": ".", "rev": rev}}, "questions": qs}))
    return path


@pytest.fixture
def run(tmp_path, tiny):
    root, rev = tiny
    questions = write_questions(tmp_path, rev)
    shared = {"store": InMemoryVectorStore(), "embedder": FakeEmbedder()}
    lines = []

    def call(*extra, repo="tiny", path=root, out=None, questions_file=questions, store=None):
        argv = ["--repo", repo, "--questions", str(questions_file), "--out", str(out or tmp_path / "out.json"), *extra]
        if path is not None:
            argv += ["--path", str(path)]
        code = main(argv, embedder=shared["embedder"], store_factory=lambda project_id: store or shared["store"], out=lines.append)
        return code, "\n".join(lines)
    call.lines, call.shared, call.out = lines, shared, tmp_path / "out.json"
    return call


def saved(call):
    return json.loads(call.out.read_text())


def test_a_run_writes_the_results_file_and_prints_a_summary(run):
    code, text = run()
    assert code == 0
    data = saved(run)
    assert data["meta"]["repo"] == "tiny" and [q["id"] for q in data["questions"]] == ["q01", "q02", "q03", "q04"]
    assert data["meta"]["questions_sha"] and data["index"]["embedded"] > 0 and data["meta"]["rev_matches"] is True
    assert "top-1" in text and "top-3" in text and "top-10" in text and "index" in text


def test_it_refuses_to_run_without_an_explicit_data_folder(run, monkeypatch, tmp_path):
    monkeypatch.delenv("CLANK_DATA_DIR")
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))      # if the refusal ever breaks, the default folder is THIS one, never the real ~/.clank
    code, text = run()
    assert code == 2 and "CLANK_DATA_DIR" in text
    assert not run.out.exists()
    assert not (tmp_path / "fake-home" / ".clank").exists(), "a refused run must not even create the default data folder"


def test_it_refuses_a_repo_that_is_not_at_the_pinned_commit_unless_told_to(tmp_path, tiny, run):
    root, _ = tiny
    other = write_questions(tmp_path, "b" * 40)
    code, text = run(questions_file=other)
    assert code == 2 and "pinned" in text and not run.out.exists()
    code, text = run("--allow-rev-mismatch", questions_file=other)
    assert code == 0 and saved(run)["meta"]["rev_matches"] is False


def test_the_clank_repo_itself_only_gets_a_warning_because_it_moves_with_every_commit(tmp_path, tiny, run):
    root, _ = tiny
    qs = yaml.safe_load(write_questions(tmp_path, "c" * 40).read_text())
    qs["repos"] = {"clank": {"path": ".", "rev": "c" * 40}}
    for q in qs["questions"]:
        q["repo"] = "clank"
    path = tmp_path / "clank-questions.yaml"
    path.write_text(yaml.safe_dump(qs))
    code, text = run(repo="clank", questions_file=path)
    assert code == 0 and "not at the pinned commit" in text


def test_a_repo_other_than_clank_needs_a_path(run):
    code, text = run(path=None)
    assert code == 2 and "--path" in text


def test_a_missing_folder_is_refused(run, tmp_path):
    code, text = run(path=tmp_path / "nope")
    assert code == 2 and "not a folder" in text


def test_an_unknown_repo_is_refused(run):
    code, text = run(repo="django")
    assert code == 2 and "django" in text


def test_the_holdout_numbers_stay_hidden_unless_asked_for(run):
    code, text = run()
    assert "q01" in text and "q02" in text
    assert "q03" not in text, "holdout questions are not printed one by one"
    assert "holdout" in text and "hidden" in text
    assert [q["id"] for q in saved(run)["questions"]] == ["q01", "q02", "q03", "q04"], "but they ARE saved, so the end-of-project look needs no re-run"
    run.lines.clear()
    code, text = run("--include-holdout")
    assert "q03" in text


def test_a_negative_is_not_part_of_the_printed_hit_rate(run):
    code, text = run()
    assert "tune: 2 questions" in text


def test_it_reuses_one_project_row_for_the_same_repo_and_keeps_the_index(run):
    run()
    again_calls = run.shared["embedder"].batch_count
    code, text = run()
    assert code == 0
    assert run.shared["embedder"].batch_count == again_calls, "nothing changed, so the second run embeds nothing"
    rows = db.get_connection().execute("SELECT name, repo_path FROM projects WHERE name LIKE 'eval-%'").fetchall()
    assert [r["name"] for r in rows] == ["eval-tiny"]


def test_index_only_stops_before_asking_and_no_index_asks_without_indexing(run):
    code, _ = run("--index-only")
    assert code == 0 and saved(run)["questions"] == [] and saved(run)["index"]["embedded"] > 0
    calls = run.shared["embedder"].batch_count
    code, _ = run("--no-index")
    assert code == 0 and "index" not in saved(run) and len(saved(run)["questions"]) == 4
    assert run.shared["embedder"].batch_count == calls


def test_index_only_and_no_index_together_make_no_sense(run):
    code, text = run("--index-only", "--no-index")
    assert code == 2 and "together" in text


def test_only_asks_just_the_named_questions(run):
    code, _ = run("--only", "q02,q04")
    assert code == 0 and [q["id"] for q in saved(run)["questions"]] == ["q02", "q04"]
    code, text = run("--only", "q99")
    assert code == 2 and "q99" in text


def test_reindex_records_the_second_pass(run):
    run("--reindex")
    assert saved(run)["index_again"]["embedded"] == 0


def test_k_and_the_budget_are_recorded(run):
    run("--k", "2", "--max-tokens", "500")
    meta = saved(run)["meta"]
    assert (meta["k"], meta["max_tokens"]) == (2, 500)
    assert all(len(q["top10"]) <= 2 for q in saved(run)["questions"])


def test_a_stopped_index_exits_with_the_reason(run):
    from embedding.errors import OllamaUnavailable
    from failing_embedders import Raising
    run.shared["embedder"] = Raising(error=OllamaUnavailable("not running"), when=lambda texts: True)
    code, text = run()
    assert code == 1 and "stopped" in text and "not running" in text
    assert saved(run)["aborted"]


def test_when_the_database_schema_was_reset_the_vector_store_is_wiped_too(run):
    import sqlite3
    import datadir

    class Spy(InMemoryVectorStore):
        cleared = 0

        def clear(self):
            Spy.cleared += 1
            super().clear()
    spy = Spy()
    run(store=spy)
    baseline = Spy.cleared                                  # the index itself clears a store that was built for no model yet
    run(store=spy)
    assert Spy.cleared == baseline, "nothing was reset, so nothing is wiped"
    raw = sqlite3.connect(datadir.db_path())
    raw.execute("PRAGMA user_version = 1")
    raw.commit()
    raw.close()
    code, _ = run(store=spy)
    assert code == 0 and Spy.cleared > baseline, "init_db dropped the chunk tables, so every stored vector id points at nothing"


def test_a_query_variant_is_applied_to_the_questions_and_recorded(run):
    run("--index-only")
    code, text = run("--no-index", "--query-variant", "raw")
    assert code == 0 and saved(run)["meta"]["query_variant"] == "raw"
    code, _ = run("--no-index")
    assert saved(run)["meta"]["query_variant"] is None


def test_the_variant_really_changes_the_question_that_is_embedded(run):
    embedder = run.shared["embedder"]
    run("--index-only")
    before = list(embedder.embedded_texts)
    run("--no-index", "--query-variant", "alt", "--only", "q01")
    sent = embedder.embedded_texts[len(before):]
    assert sent == ["Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: order total"]


def test_an_unknown_query_variant_is_refused(run, capsys):
    with pytest.raises(SystemExit):
        run("--query-variant", "nope")


def test_keep_saves_a_pool_and_is_recorded(run):
    run("--index-only")
    code, _ = run("--no-index", "--keep", "10")
    assert code == 0 and saved(run)["meta"]["keep"] == 10 and all("pool" in q for q in saved(run)["questions"])
    run("--no-index")
    assert saved(run)["meta"]["keep"] is None and all("pool" not in q for q in saved(run)["questions"])


def test_a_keep_below_ten_is_refused_with_a_reason(run):
    code, text = run("--keep", "5")
    assert code == 2 and "at least 10" in text


def test_test_policy_defaults_to_the_product_default_and_can_be_switched_off(run):
    run("--index-only")
    run("--no-index")
    assert saved(run)["meta"]["test_policy"]["margin"] == 0.15
    run("--no-index", "--test-policy", "off")
    assert saved(run)["meta"]["test_policy"] is None
    run("--no-index", "--test-policy", "default")
    assert saved(run)["meta"]["test_policy"]["calibrated_for"].startswith("qwen3-embedding")


def test_an_unknown_test_policy_is_refused(run):
    with pytest.raises(SystemExit):
        run("--test-policy", "hide")
