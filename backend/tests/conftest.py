"""Most tests look at individual small functions, so they run with grouping off.
test_grouping.py turns it on and tests it directly."""
import pytest

import chunker
import db


@pytest.fixture(autouse=True)
def no_grouping_by_default(monkeypatch):
    monkeypatch.setattr(chunker.grouping, "GROUP_SMALL_CHUNKS", False)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """A fresh database in tmp_path with one project (id 1). Never touches app.db."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    c = db.get_connection()
    c.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('p', '/r', 'now')")
    c.commit()
    yield c
    c.close()
