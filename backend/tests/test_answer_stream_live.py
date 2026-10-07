"""Task: streaming answers, part 8: the stream route on a REAL server (uvicorn on a real port), with a client that walks away.

This is the test of what the Stop button is for: when the person (or the window) leaves, the model must be told to stop, or it goes on writing, and being paid for, an
answer nobody will read. What is promised:
- pieces reach the client as the model writes them, not all at the end, and a complete stream ends cleanly with `done` as its last line;
- when the client disconnects, the model's stream is closed within a moment, whether the model is silent or talking, NOTHING is saved, and the server goes on serving;
- with the REAL client against a server that plays a model, that server sees the connection close (so a real provider would stop generating);
- while one stream waits on a silent model, the server still answers other requests (the waiting is in worker threads, not in the event loop);
- the generator closes the stream by itself (cancelled, closed, or finished), and the response also carries a background task that closes it (belt and braces).
"""
import http.client
import json
import socket
import threading
import time

import anyio
import uvicorn

import routes_answer
from app_for_tests import create_app
from fake_llm_server import FakeLLMServer, Stream, groq_stream, hang
from jobs import IndexJobs
from llm import FakeLLM, OpenAICompatibleClient, TextPiece
from llm.fake import FakeStream
from llm.profiles import Profile
from services import Services
from test_answer_endpoint import REMOTE, Question2D, indexed, world  # noqa: F401  (fixtures)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Live:
    """The real app on a real port, in a thread."""

    def __init__(self, world, llm, profile=REMOTE):
        self.services = Services(Question2D(), store_factory=lambda pid: world.store, llm_factory=lambda: (profile, llm))
        self.app = create_app(self.services, IndexJobs(self.services), auto_sync=False)
        self.port = free_port()
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert self.server.started, "the server did not start"
        self.services.wait_for_warmup()
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(10)

    def post_stream(self, **body):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        payload = json.dumps({"question": "how does it work", "allow_remote": True, **body})
        conn.request("POST", "/projects/1/answer/stream", body=payload, headers={"Content-Type": "application/json"})
        return conn, conn.getresponse()

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path)
        reply = conn.getresponse()
        body = reply.read()
        conn.close()
        return reply.status, body


def read_event(response):
    line = response.readline()
    return json.loads(line) if line.strip() else None


def wait_for(condition, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


class CountingStream(FakeStream):
    """A talking model that counts how many events were taken from it."""

    def __init__(self, events, **kw):
        super().__init__(events, **kw)
        self.taken = 0

    def __iter__(self):
        for event in super().__iter__():
            self.taken += 1
            yield event


# ---- pieces arrive as they are written

def test_pieces_arrive_over_time_and_the_stream_ends_with_done(world):
    indexed(world)
    with Live(world, FakeLLM(["a b c d e f"], delay=0.2)) as live:
        started = time.monotonic()
        conn, response = live.post_stream()
        assert response.status == 200
        stamps, last = [], None
        while True:
            event = read_event(response)
            if event is None:
                break
            last = event
            if event["type"] == "delta":
                stamps.append(time.monotonic() - started)
        conn.close()
    assert last["type"] == "done" and last["answer"] == "a b c d e f"
    assert len(stamps) == 6 and stamps[-1] - stamps[0] >= 0.8, stamps
    assert stamps[0] < stamps[-1] - 0.7, "the first piece did not wait for the last"


def test_the_start_event_arrives_before_the_model_has_written_anything(world):
    indexed(world)
    with Live(world, FakeLLM([FakeStream([TextPiece("x")], hang_after=0)])) as live:
        conn, response = live.post_stream()
        first = read_event(response)
        conn.close()
    assert first["type"] == "start" and len(first["sources"]) == 2


# ---- the client leaves

def test_leaving_while_the_model_is_silent_closes_the_models_stream_and_saves_nothing(world):
    indexed(world)
    llm = FakeLLM([FakeStream([TextPiece("a"), TextPiece("b")], hang_after=1)])
    with Live(world, llm) as live:
        conn0 = http.client.HTTPConnection("127.0.0.1", live.port)
        conn0.request("POST", "/projects/1/conversations")
        cid = json.loads(conn0.getresponse().read())["id"]
        conn, response = live.post_stream(conversation_id=cid)
        got = [read_event(response)["type"] for _ in range(3)]
        assert got == ["start", "stage", "delta"]
        started = time.monotonic()
        conn.close()                                                    # the person pressed Stop / closed the window
        assert wait_for(lambda: llm.streams[0].closed, 3), "the model's stream was never closed"
        assert time.monotonic() - started < 2
        status, body = live.get(f"/projects/1/conversations/{cid}")
        assert status == 200 and json.loads(body)["messages"] == [], "a stopped answer is not saved"
        assert live.get("/health")[0] == 200, "the server goes on serving"


def test_leaving_while_the_model_is_talking_stops_taking_pieces_from_it(world):
    indexed(world)
    stream = CountingStream([TextPiece(f"w{i} ") for i in range(400)], delay=0.03)
    llm = FakeLLM([stream])
    with Live(world, llm) as live:
        conn, response = live.post_stream()
        for _ in range(4):
            read_event(response)
        conn.close()
        assert wait_for(lambda: stream.closed, 3)
        taken = stream.taken
        time.sleep(0.5)
        assert stream.taken <= taken + 1, "nothing more is taken from the model after the client left"
        assert taken < 100


def test_leaving_before_anything_is_read_still_closes_the_stream(world):
    indexed(world)
    llm = FakeLLM([FakeStream([TextPiece("a")], hang_after=0)])
    with Live(world, llm) as live:
        conn, response = live.post_stream()
        conn.close()
        assert wait_for(lambda: llm.streams and llm.streams[0].closed, 3)


def test_with_the_real_client_the_model_server_sees_the_connection_close(world):
    """The whole chain: browser-side disconnect -> the route is cancelled -> the real client closes its socket -> the (fake) provider sees it and would stop."""
    indexed(world)
    steps = list(groq_stream(pieces=("one ", "two ", "three ", "four "), thinking=("hmm",), delay=0.1).steps[:5]) + [hang(30)]
    with FakeLLMServer(lambda request: (200, {}, Stream(*steps))) as provider:
        profile = Profile(name="local", base_url=provider.base_url, model="m", api_key_env=None, context_tokens=3000, max_output_tokens=500)
        client = OpenAICompatibleClient(provider.base_url, "m", None, timeout=30)
        with Live(world, client, profile=profile) as live:
            conn, response = live.post_stream(allow_remote=False)
            types = []
            while "delta" not in types:
                types.append(read_event(response)["type"])
            assert not provider.client_closed.is_set()
            conn.close()
            assert provider.client_closed.wait(3), "the provider never saw the connection close: it would have gone on generating"
            assert live.get("/health")[0] == 200


def test_the_server_answers_other_requests_while_a_stream_waits_on_a_silent_model(world):
    indexed(world)
    llm = FakeLLM([FakeStream([TextPiece("a")], hang_after=1)])
    with Live(world, llm) as live:
        conn, response = live.post_stream()
        for _ in range(3):
            read_event(response)
        started = time.monotonic()
        for _ in range(5):
            assert live.get("/health")[0] == 200
        assert time.monotonic() - started < 2
        conn.close()


def test_two_streams_at_the_same_time_both_finish(world):
    indexed(world)
    with Live(world, FakeLLM(["a b c"], delay=0.1)) as live:
        results = []

        def one():
            conn, response = live.post_stream()
            last = None
            while True:
                event = read_event(response)
                if event is None:
                    break
                last = event
            conn.close()
            results.append(last["type"])
        threads = [threading.Thread(target=one) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
    assert results == ["done", "done"]


# ---- the generator and the response, on their own

def make_parts(world, conn, llm, **body):
    indexed(world)
    services = Services(Question2D(), store_factory=lambda pid: world.store, llm_factory=lambda: (REMOTE, llm))
    services.ensure_warm()
    request = routes_answer.AnswerRequest(question="q", allow_remote=True, **body)
    prep = routes_answer._prepare(1, request, conn, services)
    return services, request, prep


def test_closing_the_generator_closes_the_models_stream(world, conn):
    stream = FakeStream([TextPiece("a"), TextPiece("b")], delay=0.01)
    services, request, prep = make_parts(world, conn, FakeLLM([stream]))

    async def scenario():
        agen = routes_answer._events(prep, request, services, stream, 0.0)
        await agen.__anext__()                                   # start
        await agen.aclose()
    anyio.run(scenario)
    assert stream.closed


def test_cancelling_the_generator_while_it_waits_on_a_silent_model_closes_the_stream_and_returns_quickly(world, conn):
    stream = FakeStream([TextPiece("a")], hang_after=1)
    services, request, prep = make_parts(world, conn, FakeLLM([stream]))
    outcome = {}

    async def consume(agen):
        try:
            async for _ in agen:
                pass
        finally:
            outcome["ended"] = time.monotonic()

    async def scenario():
        agen = routes_answer._events(prep, request, services, stream, 0.0)
        async with anyio.create_task_group() as group:
            group.start_soon(consume, agen)
            await anyio.sleep(0.3)                               # now it is waiting on the silent model
            outcome["cancel"] = time.monotonic()
            group.cancel_scope.cancel()
    anyio.run(scenario)
    assert stream.closed and outcome["ended"] - outcome["cancel"] < 1.0


def test_a_generator_that_finishes_closes_the_stream_too(world, conn):
    stream = FakeStream([TextPiece("a")])
    services, request, prep = make_parts(world, conn, FakeLLM([stream]))

    async def scenario():
        return [chunk async for chunk in routes_answer._events(prep, request, services, stream, 0.0)]
    chunks = anyio.run(scenario)
    assert json.loads(chunks[-1])["type"] == "done" and stream.closed


def test_the_response_also_closes_the_stream_in_a_background_task(world, conn):
    stream = FakeStream([TextPiece("a")])
    services, request, prep = make_parts(world, conn, FakeLLM([stream]))
    response = routes_answer.answer_stream(1, request, conn, services)
    assert response.background is not None and response.background.func == stream.close


def test_when_the_model_is_not_called_there_is_nothing_to_close_in_the_background(world, conn):
    indexed(world)
    (world.repo / "src/a.py").unlink()
    (world.repo / "src/b.py").unlink()
    services = Services(Question2D(), store_factory=lambda pid: world.store, llm_factory=lambda: (REMOTE, FakeLLM()))
    services.ensure_warm()
    response = routes_answer.answer_stream(1, routes_answer.AnswerRequest(question="q", allow_remote=True), conn, services)
    assert response.background is None
