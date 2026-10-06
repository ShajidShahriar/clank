"""Task: project endpoints (the UI cannot use the backend without a way to add, list and remove a project).

- `POST /projects` {path, name?} adds a project: the folder must exist, be a folder, not be the filesystem root or the home folder (too broad: it would index a
  whole disk), and not already be a project (judged on the RESOLVED path, so a trailing slash, a `..` or a symlink does not make a second one);
- `GET /projects`, `GET /projects/{id}` show what the UI needs: name, path, whether it is indexed, how many files and chunks, the state of its index job;
- `DELETE /projects/{id}` removes the project and EVERYTHING of it (rows, conversations, messages, vectors, the job's status) and nothing of any other project;
  while an index job runs it is refused (409 `project_busy`);
- no message repeats the folder path the caller sent; every refusal uses the one JSON error shape.
"""
import inspect
import os
import threading

import pytest
from fastapi.testclient import TestClient

import db
from app_for_tests import create_app
from embedding import FakeEmbedder
from jobs import IndexJobs
from services import Services
from vectorstore import InMemoryVectorStore


class Gate(FakeEmbedder):
    def __init__(self):
        super().__init__(dim=4, digest="d1")
        self.entered = threading.Event()
        self.release = threading.Event()

    def embed_documents(self, texts, ids=None):
        self.entered.set()
        assert self.release.wait(10), "the test never released the gate"
        return super().embed_documents(texts, ids)


def folder(root, name, files=("a.py", "b.py")):
    path = root / name
    path.mkdir(parents=True, exist_ok=True)
    for f in files:
        (path / f).write_text(f"def {f[:-3]}_fn():\n    return '{f}'\n")
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "proj.db"))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    clients, stores = [], {}

    def make(embedder=None):
        services = Services(embedder or FakeEmbedder(dim=4, digest="d1"), store_factory=lambda pid: stores.setdefault(pid, InMemoryVectorStore()))
        jobs = IndexJobs(services, lock_dir=tmp_path / "locks")
        client = TestClient(create_app(services, jobs, auto_sync=False))
        client.__enter__()
        clients.append(client)
        services.wait_for_warmup()
        return client, services, jobs
    make.tmp, make.home, make.stores = tmp_path, home, stores
    yield make
    for c in clients:
        c.__exit__(None, None, None)


def add(client, path, **extra):
    return client.post("/projects", json={"path": str(path), **extra})


def code(response):
    return response.json()["error"]["code"]


# ---- create

def test_adding_a_project_returns_it_with_a_new_id(env):
    client, _, _ = env()
    repo = folder(env.tmp, "myrepo")
    r = add(client, repo)
    assert r.status_code == 201
    body = r.json()
    assert body["id"] >= 1 and body["name"] == "myrepo" and body["path"] == str(repo.resolve())
    assert body["indexed"] is False and body["files"] == 0 and body["chunks"] == 0 and body["flagged_files"] == 0 and body["index_state"] == "idle"
    assert body["created_at"]
    other = add(client, folder(env.tmp, "second")).json()
    assert other["id"] == body["id"] + 1


def test_the_name_is_optional_and_stripped(env):
    client, _, _ = env()
    assert add(client, folder(env.tmp, "x"), name="  My Project \n").json()["name"] == "My Project"


def test_the_path_is_resolved_before_it_is_stored(env):
    client, _, _ = env()
    repo = folder(env.tmp, "real")
    sneaky = f"{env.tmp}/real/../real/"
    assert client.post("/projects", json={"path": sneaky}).json()["path"] == str(repo.resolve())


def test_a_leading_tilde_means_the_home_folder(env):
    client, _, _ = env()
    folder(env.home, "proj")
    r = client.post("/projects", json={"path": "~/proj"})
    assert r.status_code == 201 and r.json()["path"] == str((env.home / "proj").resolve())


@pytest.mark.parametrize("how", ["missing", "file", "relative", "empty", "blank", "nul", "root", "home", "too_long"])
def test_a_path_that_cannot_be_a_project_is_refused_without_repeating_it(env, how):
    client, _, _ = env()
    (env.tmp / "afile.txt").write_text("x")
    path = {"missing": str(env.tmp / "nope" / "deeper"), "file": str(env.tmp / "afile.txt"), "relative": "some/relative/folder", "empty": "", "blank": "   ",
            "nul": str(env.tmp) + "\x00", "root": "/", "home": str(env.home), "too_long": "/" + "a" * 5000}[how]
    r = client.post("/projects", json={"path": path})
    assert r.status_code == 422 and code(r) in ("invalid_path", "invalid_request"), r.text
    for piece in ("nope", "afile.txt", "some/relative", str(env.tmp)):
        assert piece not in r.text, "the path the caller sent must not come back"
    assert client.get("/projects").json() == [], "nothing was added"


def test_bad_fields_are_a_422_in_the_usual_shape(env):
    client, _, _ = env()
    repo = folder(env.tmp, "r")
    for body in ({}, {"path": 5}, {"path": str(repo), "name": ""}, {"path": str(repo), "name": "   "}, {"path": str(repo), "name": "x" * 101},
                 {"path": str(repo), "name": 5}, {"path": str(repo), "extra": 1}):
        r = client.post("/projects", json=body)
        assert r.status_code == 422 and code(r) == "invalid_request", body
    assert client.get("/projects").json() == []


def test_the_same_folder_cannot_be_added_twice_however_it_is_spelled(env):
    client, _, _ = env()
    repo = folder(env.tmp, "dup")
    first = add(client, repo).json()
    link = env.tmp / "link"
    os.symlink(repo, link)
    for spelling in (str(repo), str(repo) + "/", f"{env.tmp}/dup/../dup", str(link)):
        r = client.post("/projects", json={"path": spelling})
        assert r.status_code == 409 and code(r) == "project_exists", spelling
    assert [p["id"] for p in client.get("/projects").json()] == [first["id"]]


# ---- list and get

def test_the_list_is_empty_at_first_and_then_in_id_order(env):
    client, _, _ = env()
    assert client.get("/projects").json() == []
    a = add(client, folder(env.tmp, "a")).json()
    b = add(client, folder(env.tmp, "b")).json()
    assert [p["id"] for p in client.get("/projects").json()] == [a["id"], b["id"]]


def test_a_project_shows_whether_it_is_indexed_and_its_counts(env):
    client, services, jobs = env()
    p = add(client, folder(env.tmp, "idx")).json()
    q = add(client, folder(env.tmp, "plain")).json()
    client.post(f"/projects/{p['id']}/index")
    jobs.wait(p["id"])
    shown = {x["id"]: x for x in client.get("/projects").json()}
    assert shown[p["id"]]["indexed"] is True and shown[p["id"]]["files"] == 2 and shown[p["id"]]["chunks"] >= 2
    assert shown[p["id"]]["index_state"] == "done" and shown[p["id"]]["flagged_files"] == 0
    assert shown[q["id"]]["indexed"] is False and shown[q["id"]]["chunks"] == 0 and shown[q["id"]]["index_state"] == "idle"
    assert client.get(f"/projects/{p['id']}").json() == shown[p["id"]], "one project and the list agree"


def test_files_that_failed_to_index_are_counted(env):
    class Picky(FakeEmbedder):
        def __init__(self):
            super().__init__(dim=4, digest="d1", max_chars=150)
    client, _, jobs = env(Picky())
    repo = folder(env.tmp, "fl")
    (repo / "big.py").write_text("def big():\n    return '" + "x" * 500 + "'\n")
    p = add(client, repo).json()
    client.post(f"/projects/{p['id']}/index")
    jobs.wait(p["id"])
    assert client.get(f"/projects/{p['id']}").json()["flagged_files"] == 1


def test_an_unknown_project_is_404(env):
    client, _, _ = env()
    r = client.get("/projects/999")
    assert r.status_code == 404 and code(r) == "project_not_found"


# ---- delete

def test_deleting_removes_the_project_and_everything_of_it_and_nothing_of_another(env):
    client, services, jobs = env()
    a = add(client, folder(env.tmp, "a")).json()
    b = add(client, folder(env.tmp, "b")).json()
    for p in (a, b):
        client.post(f"/projects/{p['id']}/index")
        jobs.wait(p["id"])
    conn = db.get_connection()
    conv = conn.execute("INSERT INTO conversations (project_id, title, created_at, updated_at) VALUES (?, 't', 'n', 'n')", (a["id"],)).lastrowid
    conn.execute("INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, 'user', 'hi', 'n')", (conv,))
    conn.commit()
    b_before = (client.get(f"/projects/{b['id']}").json(), services.store_for(b["id"]).count())
    r = client.delete(f"/projects/{a['id']}")
    assert r.status_code == 204 and r.content == b""
    assert client.get(f"/projects/{a['id']}").status_code == 404
    assert [p["id"] for p in client.get("/projects").json()] == [b["id"]]
    for table, column in (("files", "project_id"), ("chunks", "project_id"), ("conversations", "project_id")):
        assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (a["id"],)).fetchone()[0] == 0, table
    assert conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (conv,)).fetchone()[0] == 0
    assert services.store_for(a["id"]).count() == 0, "its vectors are gone"
    assert (client.get(f"/projects/{b['id']}").json(), services.store_for(b["id"]).count()) == b_before, "the other project is untouched"
    assert jobs.status(a["id"]) == {"state": "idle", "project_id": a["id"]}, "its job status is forgotten"
    conn.close()


def test_a_deleted_folder_can_be_added_again_as_a_new_project(env):
    client, _, _ = env()
    repo = folder(env.tmp, "again")
    first = add(client, repo).json()
    client.delete(f"/projects/{first['id']}")
    second = add(client, repo)
    assert second.status_code == 201 and second.json()["id"] != first["id"]


def test_deleting_a_project_that_is_being_indexed_is_refused_and_changes_nothing(env):
    gate = Gate()
    client, services, jobs = env(gate)
    p = add(client, folder(env.tmp, "busy")).json()
    client.post(f"/projects/{p['id']}/index")
    assert gate.entered.wait(5)
    r = client.delete(f"/projects/{p['id']}")
    assert r.status_code == 409 and code(r) == "project_busy"
    assert client.get(f"/projects/{p['id']}").status_code == 200 and client.get(f"/projects/{p['id']}").json()["index_state"] == "running"
    gate.release.set()
    jobs.wait(p["id"])
    assert client.delete(f"/projects/{p['id']}").status_code == 204, "allowed once the job is over"


def test_deleting_an_unknown_project_is_404(env):
    client, _, _ = env()
    r = client.delete("/projects/999")
    assert r.status_code == 404 and code(r) == "project_not_found"


def test_the_endpoints_are_plain_def_functions():
    from routes_projects import router
    routes = [r for r in router.routes if r.path.startswith("/projects")]
    assert len(routes) == 4 and not any(inspect.iscoroutinefunction(r.endpoint) for r in routes)


# ---- security: the token is needed here too

def test_the_project_endpoints_need_the_token(env, monkeypatch):
    from main import create_app as real_create_app
    from security import Security
    token = "k" * 40
    services = Services(FakeEmbedder(dim=4, digest="d1"), store_factory=lambda pid: InMemoryVectorStore())
    app = real_create_app(services, auto_sync=False, security=Security(token=token))
    with TestClient(app, base_url="http://127.0.0.1:8123") as client:
        for method, path in (("GET", "/projects"), ("POST", "/projects"), ("GET", "/projects/1"), ("DELETE", "/projects/1")):
            assert client.request(method, path).status_code == 401, (method, path)
        assert client.get("/projects", headers={"x-clank-token": token}).status_code == 200


# ---- behaviours of the pieces underneath (found by mutation checks)

def test_the_duplicate_check_and_the_insert_happen_under_one_write_lock(env, monkeypatch):
    """Two requests adding the same folder at once must not both pass the check: while the check runs, another connection must NOT be able to write."""
    import sqlite3
    import projects
    client, _, _ = env()
    repo = folder(env.tmp, "race")
    seen = []
    real = projects._same_folder

    def spy(stored, resolved):
        other = sqlite3.connect(db.DB_PATH, timeout=0.2)
        try:
            other.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('x', '/x', 'n')")
            other.commit()
            seen.append("another connection could write")
        except sqlite3.OperationalError as problem:
            seen.append(str(problem))
        finally:
            other.close()
        return real(stored, resolved)
    first = db.get_connection()
    first.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('seed', '/seed-folder-that-is-not-real', 'n')")
    first.commit()
    monkeypatch.setattr(projects, "_same_folder", spy)
    conn = db.get_connection()
    projects.add_project(conn, str(repo))
    assert seen and all("locked" in s for s in seen), seen
    conn.close()
    first.close()


def test_a_refused_add_leaves_no_transaction_open(env):
    import projects
    env()                                                           # starts an app, which creates the tables
    folder(env.tmp, "twice")
    conn = db.get_connection()
    projects.add_project(conn, str(env.tmp / "twice"))
    with pytest.raises(projects.ProjectExists):
        projects.add_project(conn, str(env.tmp / "twice"))
    assert conn.in_transaction is False, "the write lock was released"
    with pytest.raises(projects.InvalidProjectPath):
        projects.add_project(conn, str(env.tmp / "nope"))
    assert conn.in_transaction is False
    conn.close()


def test_deleting_drops_the_cached_vector_store(env):
    made = []
    services = Services(FakeEmbedder(dim=4, digest="d1"), store_factory=lambda pid: made.append(pid) or InMemoryVectorStore())
    first = services.store_for(5)
    services.forget_store(5)
    assert services.store_for(5) is not first and made == [5, 5], "a store for a deleted project is not kept in memory"


def test_forgetting_a_running_job_keeps_it(env):
    gate = Gate()
    client, _, jobs = env(gate)
    p = add(client, folder(env.tmp, "keep")).json()
    client.post(f"/projects/{p['id']}/index")
    assert gate.entered.wait(5)
    jobs.forget(p["id"])
    assert jobs.status(p["id"])["state"] == "running" and jobs.is_running(p["id"])
    gate.release.set()
    jobs.wait(p["id"])
    jobs.forget(p["id"])
    assert jobs.status(p["id"]) == {"state": "idle", "project_id": p["id"]}


def test_a_relative_path_is_refused_even_when_it_exists_relative_to_the_working_folder(env, monkeypatch):
    client, _, _ = env()
    folder(env.tmp, "exists_here")
    monkeypatch.chdir(env.tmp)
    for relative in ("exists_here", "./exists_here", "../" + env.tmp.name + "/exists_here"):
        r = client.post("/projects", json={"path": relative})
        assert r.status_code == 422 and code(r) == "invalid_path", relative


def test_a_project_stored_in_another_spelling_still_counts_as_a_duplicate(env):
    """Rows added before this check existed (or by hand) may hold a path with a trailing slash or a symlink: comparing text would let a second copy in."""
    client, _, _ = env()
    repo = folder(env.tmp, "olddata")
    link = env.tmp / "oldlink"
    os.symlink(repo, link)
    conn = db.get_connection()
    conn.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('old', ?, 'n')", (str(link) + "/",))
    conn.commit()
    conn.close()
    r = client.post("/projects", json={"path": str(repo)})
    assert r.status_code == 409 and code(r) == "project_exists"


def test_the_name_is_stripped_by_the_function_itself(env):
    import projects
    env()
    conn = db.get_connection()
    pid = projects.add_project(conn, str(folder(env.tmp, "n1")), "  Padded  ")
    assert projects.get_project_summary(conn, pid, lambda _: "idle")["name"] == "Padded"
    pid2 = projects.add_project(conn, str(folder(env.tmp, "n2")), "   ")
    assert projects.get_project_summary(conn, pid2, lambda _: "idle")["name"] == "n2", "a blank name falls back to the folder name"
    conn.close()
