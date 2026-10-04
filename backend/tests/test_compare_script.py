"""Task I-7.5: the command line that prints a comparison of saved runs (eval/compare.py `main`; scripts/compare_runs.py only calls it)."""
import json

from eval.compare import main


def q(id, rank, split="tune"):
    return {"id": id, "kind": "name", "split": split, "rank": rank, "top1": rank == 1, "top3": rank is not None and rank <= 3,
            "top10_hit": rank is not None and rank <= 10, "found_in_context": rank is not None, "expect": [{"path": "a.py", "symbol": "f"}]}


def save(tmp_path, name, repo, *questions, model="m@1", final=False):
    path = tmp_path / name
    path.write_text(json.dumps({"meta": {"repo": repo, "model": model, "final": final}, "questions": list(questions)}))
    return str(path)


def call(*argv):
    lines = []
    code = main(list(argv), out=lines.append)
    return code, "\n".join(lines)


def test_it_prints_the_table_for_one_repo(tmp_path):
    a = save(tmp_path, "a.json", "clank", q("q01", None), q("q02", 1))
    b = save(tmp_path, "b.json", "clank", q("q01", 2), q("q02", 1))
    code, text = call("--a", a, "--b", b, "--names", "control", "raw")
    assert code == 0 and "q01" in text and "better" in text and "control" in text and "raw" in text


def test_it_adds_several_repos_in_the_order_given(tmp_path):
    a1, b1 = save(tmp_path, "a1.json", "clank", q("q01", 1)), save(tmp_path, "b1.json", "clank", q("q01", 4))
    a2, b2 = save(tmp_path, "a2.json", "flask", q("q02", 1)), save(tmp_path, "b2.json", "flask", q("q02", 1))
    code, text = call("--a", a1, a2, "--b", b1, b2)
    assert code == 0 and "2 questions" in text and "clank" in text and "flask" in text


def test_a_different_number_of_files_is_refused(tmp_path):
    a = save(tmp_path, "a.json", "clank", q("q01", 1))
    code, text = call("--a", a, a, "--b", a)
    assert code == 2 and "same number" in text


def test_a_mismatch_between_runs_is_an_error_message_not_a_crash(tmp_path):
    a = save(tmp_path, "a.json", "clank", q("q01", 1), model="m@1")
    b = save(tmp_path, "b.json", "clank", q("q01", 1), model="m@2")
    code, text = call("--a", a, "--b", b)
    assert code == 2 and "model" in text


def test_the_holdout_only_appears_when_asked_for(tmp_path):
    a = save(tmp_path, "a.json", "clank", q("q01", 1), q("q03", 1, split="holdout"), final=True)
    b = save(tmp_path, "b.json", "clank", q("q01", 1), q("q03", None, split="holdout"), final=True)
    assert "q03" not in call("--a", a, "--b", b)[1]
    assert "q03" in call("--a", a, "--b", b, "--include-holdout")[1]


def test_the_verdict_names_the_better_run(tmp_path):
    a = save(tmp_path, "a.json", "clank", *[q(f"q{i:02d}", None) for i in range(5)])
    b = save(tmp_path, "b.json", "clank", *[q(f"q{i:02d}", 1) for i in range(5)])
    code, text = call("--a", a, "--b", b, "--names", "control", "raw")
    assert "raw is better" in text and "b is better" not in text


def test_the_verdict_names_run_a_too_when_a_is_the_better_one(tmp_path):
    a = save(tmp_path, "a.json", "clank", *[q(f"q{i:02d}", 1) for i in range(5)])
    b = save(tmp_path, "b.json", "clank", *[q(f"q{i:02d}", None) for i in range(5)])
    code, text = call("--a", a, "--b", b, "--names", "control", "raw")
    assert "control is better" in text and "a is better" not in text
