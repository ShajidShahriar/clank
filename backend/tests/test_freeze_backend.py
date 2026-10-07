"""Task: packaging, part 1: freeze the backend into one folder with PyInstaller (`freeze.py`, run by `scripts/freeze_backend.py`).

A frozen app has no `.py` files and no package metadata unless they are shipped on purpose, and the backend needs both: the chunker fingerprint hashes the
chunker's SOURCE files and the versions of the tree-sitter packages. If either is missing the index silently misbehaves (a crash on the first index, or a
fingerprint that no longer notices a grammar update). What is promised:
- the list of source files and package names shipped is DERIVED from the fingerprint code itself, never copied by hand: a file added to the fingerprint is
  shipped by the next build;
- every shipped source file lands where the fingerprint will look for it in the frozen app (`<bundle>/<same relative path>`): proved by pointing the real
  fingerprint at a copy of the bundle layout and getting the SAME fingerprint as in development;
- `main` (started by name by uvicorn, so invisible to the analysis) and all of Chroma (which loads its parts by name) are included;
- the output is one folder, `clank-backend/`, holding the program `clank-backend` that the desktop app starts, and an old output never lingers;
- the build is started with the interpreter that runs it, from the backend folder, and its exit code is passed on.
"""
import os
import shutil
import sys
from pathlib import Path

import pytest

import freeze
from indexing import fingerprint


@pytest.fixture(autouse=True)
def tool_present(monkeypatch):
    """These tests are about the command, not about whether PyInstaller is installed here."""
    monkeypatch.setattr(freeze, "pyinstaller_available", lambda: True)


def args(**kw):
    base = dict(dist_dir=Path("/out/dist"), work_dir=Path("/out/work"), spec_dir=Path("/out/spec"))
    return freeze.pyinstaller_args(**{**base, **kw})


def data_pairs(arguments):
    pairs = []
    for a in arguments:
        if a.startswith("--add-data="):
            source, dest = a[len("--add-data="):].rsplit(os.pathsep, 1)
            pairs.append((Path(source), dest))
    return pairs


# ---- what goes in

def test_every_source_file_of_the_fingerprint_is_shipped_to_the_same_relative_place():
    pairs = data_pairs(args())
    wanted = fingerprint.chunker_source_files()
    assert wanted, "the fingerprint lists no files: the test would prove nothing"
    assert sorted(p for p, _ in pairs) == sorted(wanted)
    for source, dest in pairs:
        assert (Path("BUNDLE") / dest / source.name) == Path("BUNDLE") / source.relative_to(fingerprint.BACKEND), source


def test_a_file_added_to_the_fingerprint_is_shipped_without_touching_the_build(monkeypatch, tmp_path):
    extra = tmp_path / "newrules.py"
    extra.write_text("x = 1\n")
    monkeypatch.setattr(fingerprint, "BACKEND", tmp_path)
    monkeypatch.setattr(fingerprint, "chunker_source_files", lambda: [extra])
    assert data_pairs(args()) == [(extra, ".")]


def test_every_package_the_fingerprint_reads_a_version_from_ships_its_metadata():
    shipped = {a[len("--copy-metadata="):] for a in args() if a.startswith("--copy-metadata=")}
    versions = fingerprint.grammar_versions()
    assert versions and shipped == set(versions)


def test_the_hidden_parts_are_named():
    a = args()
    assert "--hidden-import=main" in a
    assert "--collect-all=chromadb" in a


def test_the_bundle_gives_the_same_fingerprint_as_development(monkeypatch, tmp_path):
    """The frozen app has the shipped files at <bundle>/<relative path>. Point the real fingerprint code at such a folder: nothing may differ."""
    bundle = tmp_path / "_internal"
    for source, dest in data_pairs(args()):
        (bundle / dest).mkdir(parents=True, exist_ok=True)
        shutil.copy(source, bundle / dest / source.name)
    in_development = fingerprint.chunker_fingerprint()
    monkeypatch.setattr(fingerprint, "BACKEND", bundle)
    assert fingerprint.chunker_fingerprint() == in_development


def test_a_bundle_missing_one_file_would_give_another_fingerprint_or_fail(monkeypatch, tmp_path):
    """The proof above can fail: leave one file out and it must."""
    bundle = tmp_path / "_internal"
    for source, dest in data_pairs(args())[:-1]:
        (bundle / dest).mkdir(parents=True, exist_ok=True)
        shutil.copy(source, bundle / dest / source.name)
    monkeypatch.setattr(fingerprint, "BACKEND", bundle)
    with pytest.raises(FileNotFoundError):
        fingerprint.chunker_fingerprint()


# ---- the command

def test_the_command_is_a_folder_build_named_for_the_program_the_app_starts():
    a = args()
    assert "--onedir" in a and "--noconfirm" in a and "--clean" in a
    assert f"--name={freeze.PROGRAM_NAME}" in a and freeze.PROGRAM_NAME == "clank-backend"
    assert a[-1] == "run.py", "the entry point is last"
    assert len(a) == len(set(a)), "no argument twice"


def test_the_three_work_folders_are_the_ones_given():
    a = args(dist_dir=Path("/x/dist"), work_dir=Path("/x/work"), spec_dir=Path("/x/spec"))
    assert {"--distpath=/x/dist", "--workpath=/x/work", "--specpath=/x/spec"} <= set(a)


def test_the_data_separator_is_the_one_of_this_system():
    assert all(os.pathsep in a for a in args() if a.startswith("--add-data="))


# ---- running it

class Recorder:
    def __init__(self, code=0):
        self.code, self.calls = code, []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))

        class Done:
            returncode = self.code
        return Done()


def test_main_runs_pyinstaller_with_this_interpreter_from_the_backend_folder(tmp_path):
    run = Recorder()
    assert freeze.main(["--out", str(tmp_path / "out")], run=run) == 0
    [(command, kwargs)] = run.calls
    assert command[:3] == [sys.executable, "-m", "PyInstaller"]
    assert Path(kwargs["cwd"]) == Path(freeze.__file__).resolve().parent
    assert f"--distpath={tmp_path / 'out'}" in command, "the program ends up in <out>/clank-backend"


def test_main_passes_on_a_failed_build(tmp_path):
    assert freeze.main(["--out", str(tmp_path / "out")], run=Recorder(code=3)) == 3


def test_an_old_output_is_removed_before_a_build_so_no_stale_file_lingers(tmp_path):
    out = tmp_path / "out"
    stale = out / freeze.PROGRAM_NAME / "_internal" / "left_over_from_an_old_build.py"
    stale.parent.mkdir(parents=True)
    stale.write_text("old")
    freeze.main(["--out", str(out)], run=Recorder())
    assert not stale.exists()


def test_main_never_removes_anything_but_its_own_output_folder(tmp_path):
    out = tmp_path / "out"
    (out / "someone_elses_file.txt").parent.mkdir(parents=True)
    (out / "someone_elses_file.txt").write_text("keep")
    freeze.main(["--out", str(out)], run=Recorder())
    assert (out / "someone_elses_file.txt").read_text() == "keep"


def test_the_default_output_is_build_backend_at_the_top_of_the_repository():
    assert freeze.DEFAULT_OUT == Path(freeze.__file__).resolve().parent.parent / "build" / "backend"


def test_the_missing_tool_is_a_clear_message_not_a_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(freeze, "pyinstaller_available", lambda: False)
    assert freeze.main(["--out", str(tmp_path / "out")], run=Recorder()) == 2
    assert "PyInstaller" in capsys.readouterr().err
