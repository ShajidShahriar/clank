"""Where Clank keeps its data (task I-5.0, fix #6 from the I-4 review).

The old default was `DB_PATH = "app.db"`, relative to wherever the backend happened to start, so starting it from another folder
silently made a NEW database (and a new vector store). Now there is one absolute data folder: ~/.clank, or CLANK_DATA_DIR
(Electron passes its user-data path through that variable). Tests never touch the real one: conftest redirects it for every test.
"""
import os
import stat
from pathlib import Path

import pytest

import datadir
import db


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLANK_DATA_DIR", raising=False)
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_the_default_is_dot_clank_in_the_home_folder_and_private(home):
    path = datadir.data_dir()
    assert path == home / ".clank" and path.is_dir() and mode(path) == 0o700


def test_an_empty_override_means_no_override(home, monkeypatch):
    monkeypatch.setenv("CLANK_DATA_DIR", "")
    assert datadir.data_dir() == home / ".clank"


def test_the_override_is_used_and_created_private_with_its_parents(home, tmp_path, monkeypatch):
    target = tmp_path / "elsewhere" / "deeper" / "clank-data"
    monkeypatch.setenv("CLANK_DATA_DIR", str(target))
    assert datadir.data_dir() == target.resolve() and target.is_dir() and mode(target) == 0o700
    assert not (home / ".clank").exists()


def test_a_tilde_in_the_override_is_expanded(home, monkeypatch):
    monkeypatch.setenv("CLANK_DATA_DIR", "~/my-clank")
    assert datadir.data_dir() == home / "my-clank"


def test_a_relative_override_is_refused_because_it_would_depend_on_the_working_folder(home, monkeypatch):
    monkeypatch.setenv("CLANK_DATA_DIR", "data/here")
    with pytest.raises(ValueError, match=r"CLANK_DATA_DIR.*absolute.*data/here"):
        datadir.data_dir()


def test_an_existing_folder_is_left_exactly_as_it_is(home, tmp_path, monkeypatch):
    existing = tmp_path / "shared"
    existing.mkdir()
    existing.chmod(0o755)
    monkeypatch.setenv("CLANK_DATA_DIR", str(existing))     # it may be somebody else's folder: never change its permissions
    assert datadir.data_dir() == existing.resolve() and mode(existing) == 0o755


def test_the_answer_does_not_depend_on_the_working_folder(home, tmp_path, monkeypatch):
    first = datadir.data_dir()
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert datadir.data_dir() == first and first.is_absolute()


def test_the_database_lives_in_the_data_folder_whatever_folder_the_backend_starts_in(home, tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", None)                # None = use the data folder
    elsewhere = tmp_path / "started-from-here"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    db.init_db()
    assert (home / ".clank" / "app.db").is_file()
    assert not (elsewhere / "app.db").exists()              # the old bug: a second, empty database next to wherever we started


def test_the_helpers_point_inside_the_data_folder(home):
    assert datadir.db_path() == home / ".clank" / "app.db"
    assert datadir.lock_dir() == home / ".clank" / "locks" and datadir.lock_dir().is_dir()


def test_every_test_is_redirected_away_from_the_real_data_folder():
    # conftest sets CLANK_DATA_DIR for every test; if this fails, some test could write into the user's real ~/.clank
    redirected = os.environ.get("CLANK_DATA_DIR")
    assert redirected and Path(redirected).is_absolute()
    assert Path(redirected).resolve() != (Path(os.path.expanduser("~")) / ".clank").resolve()
