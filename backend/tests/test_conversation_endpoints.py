"""Task: saved conversations, the endpoints and the memory of earlier turns.

What is promised:
- `POST/GET /projects/{id}/conversations`, `GET/DELETE /projects/{id}/conversations/{cid}`; a conversation of another project is a 404, like one that does
  not exist (`conversation_not_found`);
- `/answer` takes an optional `conversation_id`. The exchange is saved ONLY when an answer came back; every refusal and every model failure saves nothing;
  without an id nothing is saved and the call behaves as before;
- a bad conversation id is refused BEFORE anything is embedded or sent (nothing leaves the machine for a conversation that does not exist);
- the saved answer keeps its sources and notes (`meta`), so the window can draw an old answer like a new one;
- follow-up questions REMEMBER: the earlier questions and answers go to the model as earlier turns, WITHOUT the old code excerpts; the new question gets
  its own fresh excerpts; a turn where the model was not called is not remembered; history comes from this conversation only;
- history has a fixed budget: the newest turns are kept, the oldest are dropped, a long old answer is cut, and the newest turn is always kept.
"""
import answer
from answer import HISTORY_ANSWER_CHARS, HISTORY_TOKENS
from routes_context import MAX_QUESTION_CHARS
from llm import FakeLLM, LLMTimeout
from test_answer_endpoint import ask, clients, code, indexed, make_client, world  # noqa: F401  (fixtures)


def new_conversation(client, project=1):
    r = client.post(f"/projects/{project}/conversations")
    assert r.status_code == 201, r.text
    return r.json()["id"]


def saved(client, cid, project=1):
    return client.get(f"/projects/{project}/conversations/{cid}").json()


# ---- the endpoints

def test_create_list_read_delete(world, clients):
    indexed(world)
    client = make_client(world, clients)
    first = client.post("/projects/1/conversations")
    assert first.status_code == 201
    body = first.json()
    assert set(body) == {"id", "title", "created_at", "updated_at", "message_count"}
    assert body["title"] is None and body["message_count"] == 0
    second = new_conversation(client)
    listed = client.get("/projects/1/conversations").json()
    assert [c["id"] for c in listed] == [second, body["id"]], "the newest first"
    assert client.get(f"/projects/1/conversations/{body['id']}").json()["messages"] == []
    assert client.delete(f"/projects/1/conversations/{body['id']}").status_code == 204
    assert [c["id"] for c in client.get("/projects/1/conversations").json()] == [second]
    assert client.get(f"/projects/1/conversations/{body['id']}").status_code == 404


def test_unknown_project_and_unknown_conversation_are_404(world, clients):
    indexed(world)
    client = make_client(world, clients)
    for method, path in (("post", "/projects/9/conversations"), ("get", "/projects/9/conversations")):
        r = getattr(client, method)(path)
        assert r.status_code == 404 and code(r) == "project_not_found", path
    for method in ("get", "delete"):
        r = getattr(client, method)("/projects/1/conversations/99")
        assert r.status_code == 404 and code(r) == "conversation_not_found", method


def test_another_projects_conversation_is_a_404_everywhere(world, clients, conn):
    indexed(world)
    conn.execute("INSERT INTO projects (name, repo_path, created_at) VALUES ('other', '/o', 'now')")
    conn.commit()
    client = make_client(world, clients)
    theirs = new_conversation(client, project=2)
    assert client.get(f"/projects/1/conversations/{theirs}").status_code == 404
    assert client.delete(f"/projects/1/conversations/{theirs}").status_code == 404
    assert [c["id"] for c in client.get("/projects/2/conversations").json()] == [theirs], "the failed delete removed nothing"
    r = ask(client, conversation_id=theirs)
    assert r.status_code == 404 and code(r) == "conversation_not_found"
    assert client.llm.calls == [] and client.embedder.query_count == 0


# ---- saving through /answer

def test_an_answer_in_a_conversation_is_saved_with_its_sources(world, clients):
    indexed(world)
    client = make_client(world, clients, FakeLLM(["It calls a (src/a.py:1-1)."]))
    cid = new_conversation(client)
    r = ask(client, question="what does a do?", conversation_id=cid)
    assert r.status_code == 200 and r.json()["conversation_id"] == cid
    convo = saved(client, cid)
    assert convo["title"] == "what does a do?"
    user, assistant = convo["messages"]
    assert (user["role"], user["content"]) == ("user", "what does a do?")
    assert (assistant["role"], assistant["content"]) == ("assistant", "It calls a (src/a.py:1-1).")
    assert assistant["meta"]["sources"] == r.json()["sources"] and assistant["meta"]["llm_called"] is True
    assert assistant["meta"]["model"] == "fake-llm" and "answer" not in assistant["meta"], "the text is stored once, as the message"
    assert client.get("/projects/1/conversations").json()[0]["message_count"] == 2


def test_without_a_conversation_nothing_is_saved_and_the_call_is_as_before(world, clients):
    indexed(world)
    client = make_client(world, clients)
    r = ask(client)
    assert r.status_code == 200 and r.json()["conversation_id"] is None
    assert len(client.llm.calls[0]["messages"]) == 2
    assert client.get("/projects/1/conversations").json() == []


def test_a_bad_conversation_id_is_refused_before_anything_is_sent(world, clients):
    indexed(world)
    client = make_client(world, clients)
    for bad in ("1", 1.5, True, 0, -1):
        r = ask(client, conversation_id=bad)
        assert r.status_code == 422 and code(r) == "invalid_request", bad
    r = ask(client, conversation_id=12345)
    assert r.status_code == 404 and code(r) == "conversation_not_found"
    assert client.llm.calls == [] and client.embedder.query_count == 0


def test_a_missing_conversation_is_found_before_consent_and_before_the_model_is_set_up(world, clients):
    indexed(world)
    client = make_client(world, clients)
    r = client.post("/projects/1/answer", json={"question": "q", "conversation_id": 777})          # no allow_remote: still the 404 first
    assert r.status_code == 404 and code(r) == "conversation_not_found"


def test_nothing_is_saved_when_the_answer_fails(world, clients):
    indexed(world)
    client = make_client(world, clients, FakeLLM([LLMTimeout("slow")]))
    cid = new_conversation(client)
    assert ask(client, conversation_id=cid).status_code == 504
    no_consent = client.post("/projects/1/answer", json={"question": "q", "conversation_id": cid})
    assert no_consent.status_code == 403
    assert saved(client, cid)["messages"] == [] and saved(client, cid)["title"] is None


def test_a_turn_with_no_matching_code_is_saved_without_calling_the_model(world, clients):
    indexed(world)
    (world.repo / "src/a.py").unlink()
    (world.repo / "src/b.py").unlink()
    client = make_client(world, clients)
    cid = new_conversation(client)
    ask(client, conversation_id=cid)
    assistant = saved(client, cid)["messages"][1]
    assert assistant["content"] == answer.NO_MATCH_TEXT and assistant["meta"]["llm_called"] is False and client.llm.calls == []


def test_deleting_a_conversation_does_not_touch_the_others_messages(world, clients):
    indexed(world)
    client = make_client(world, clients)
    a, b = new_conversation(client), new_conversation(client)
    ask(client, conversation_id=a)
    ask(client, conversation_id=b)
    client.delete(f"/projects/1/conversations/{a}")
    assert len(saved(client, b)["messages"]) == 2


# ---- memory of earlier turns

def test_a_follow_up_sends_the_earlier_turns_without_their_code(world, clients):
    indexed(world)
    client = make_client(world, clients, FakeLLM(["first answer", "second answer"]))
    cid = new_conversation(client)
    ask(client, question="first question", conversation_id=cid)
    ask(client, question="and the second?", conversation_id=cid)
    sent = client.llm.calls[1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[1]["content"] == "first question" and sent[2]["content"] == "first answer"
    assert "src/a.py" not in sent[1]["content"] + sent[2]["content"], "no old code excerpts in the history"
    assert "src/a.py" in sent[3]["content"] and "and the second?" in sent[3]["content"], "the new question has its own fresh excerpts"
    assert len(client.llm.calls[0]["messages"]) == 2, "the first question has no history"


def test_history_comes_from_this_conversation_only(world, clients):
    indexed(world)
    client = make_client(world, clients, FakeLLM(["answer one", "answer two"]))
    a, b = new_conversation(client), new_conversation(client)
    ask(client, question="in a", conversation_id=a)
    ask(client, question="in b", conversation_id=b)
    assert len(client.llm.calls[1]["messages"]) == 2


def test_a_turn_where_the_model_was_not_called_is_not_remembered(world, clients):
    indexed(world)
    client = make_client(world, clients)
    cid = new_conversation(client)
    (world.repo / "src/a.py").unlink()
    (world.repo / "src/b.py").unlink()
    ask(client, question="nothing here", conversation_id=cid)                    # saved, model not called
    (world.repo / "src/a.py").write_text("src/a.py")
    (world.repo / "src/b.py").write_text("src/b.py")
    r = ask(client, question="again", conversation_id=cid)
    assert r.status_code == 200
    # the files came back with the same bytes, so the index is fresh again and the model is called: with no history
    assert client.llm.calls and len(client.llm.calls[-1]["messages"]) == 2


def test_the_oldest_turns_are_dropped_first_and_the_newest_is_kept(world, clients):
    indexed(world)
    long_answer = "w" * 1500
    client = make_client(world, clients, FakeLLM([long_answer] * 8))
    cid = new_conversation(client)
    for i in range(6):
        ask(client, question=f"question number {i}", conversation_id=cid)
    ask(client, question="last", conversation_id=cid)
    sent = client.llm.calls[-1]["messages"][1:-1]
    questions = [m["content"] for m in sent if m["role"] == "user"]
    assert questions and questions[-1] == "question number 5", "the newest earlier turn is kept"
    assert "question number 0" not in questions, "the oldest is dropped"
    assert len(questions) < 6
    assert sum(len(m["content"]) for m in sent) // 3 <= HISTORY_TOKENS, "the history stays inside its budget"
    assert [m["role"] for m in sent] == ["user", "assistant"] * len(questions), "whole turns only, in order"


def test_a_very_long_old_answer_is_cut_but_the_newest_turn_is_still_kept(world, clients):
    indexed(world)
    huge = "z" * (HISTORY_ANSWER_CHARS * 5)
    client = make_client(world, clients, FakeLLM([huge, "next"]))
    cid = new_conversation(client)
    ask(client, question="long one", conversation_id=cid)
    ask(client, question="follow up", conversation_id=cid)
    sent = client.llm.calls[1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert 0 < len(sent[2]["content"]) <= HISTORY_ANSWER_CHARS + 20 and sent[2]["content"].startswith("z" * 100)
    assert saved(client, cid)["messages"][1]["content"] == huge, "what is SAVED is never cut"


def test_the_instructions_tell_the_model_what_the_earlier_turns_are():
    text = answer.SYSTEM_PROMPT.lower()
    assert "earlier" in text and "context" in text


def test_build_messages_without_history_is_what_it_was():
    assert [m["role"] for m in answer.build_messages("q", "ctx")] == ["system", "user"]
    assert [m["role"] for m in answer.build_messages("q", "ctx", [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}])] == [
        "system", "user", "assistant", "user"]


def test_history_turns_pick_whole_pairs_newest_first():
    turns = [(f"q{i}", "a" * 600) for i in range(10)]               # each turn about 200 estimated tokens
    picked = answer.history_messages(turns)
    assert [m["content"] for m in picked if m["role"] == "user"] == [f"q{i}" for i in range(10 - len(picked) // 2, 10)]
    assert answer.history_messages([]) == []


def test_the_biggest_possible_turn_fits_the_budget_alone_so_the_newest_turn_is_always_kept():
    biggest = ("q" * MAX_QUESTION_CHARS, "a" * (HISTORY_ANSWER_CHARS * 3))
    assert len(answer.history_messages([biggest])) == 2


def test_one_turn_too_big_for_the_budget_ends_the_walk_and_older_small_ones_are_not_brought_back():
    big = ("q" * MAX_QUESTION_CHARS, "a" * HISTORY_ANSWER_CHARS)           # about 1,300 estimated tokens: two of them cannot fit
    picked = answer.history_messages([("tiny old question", "a"), big, big, ("new", "b")])
    assert [m["content"] for m in picked][-2:] == ["new", "b"]
    assert len(picked) == 4 and all(m["content"] != "tiny old question" for m in picked), "a run of the latest turns, with no gap"
