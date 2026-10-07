"""Task: first run, part 1: download the embedding model through Ollama (`model_pull.py`), so a person without the model does not need a terminal.

What is promised:
- ONE model is ever pulled: the one the puller was made for. The caller cannot name another (nothing in the API takes a model name);
- the pull runs in the background; `status()` can be read at any time and is a COPY (a caller cannot change the puller by changing it);
- progress is read from Ollama's lines (several layers, each with `completed` and `total`) and added up; it never goes backwards and never passes 100;
- the pull is DONE only when Ollama says `success`; a stream that just stops, an `error` line, an HTTP error and a refused connection are all FAILED, each
  with a sentence written for people (never a traceback, never a URL);
- asking again while a pull runs does NOT start a second download; asking again after a failure or a success starts a new one;
- `on_done` runs exactly once per successful pull, after the state says done, and never after a failure; an error inside `on_done` does not turn the pull into a failure.
"""
import io
import json
import threading
import urllib.error


from model_pull import ModelPuller

MODEL = "qwen3-embedding:0.6b"


def lines(*items):
    """Ollama's answer: one JSON object per line."""
    return [(json.dumps(item) + "\n").encode() for item in items]


class Reply:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return iter(self.body)

    def __exit__(self, *exc):
        return False


class Opener:
    """Stands in for urllib.request.urlopen: records the request, plays a script (a list of lines, or an exception to raise)."""

    def __init__(self, *scripts):
        self.scripts, self.requests, self.timeouts = list(scripts), [], []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        self.timeouts.append(timeout)
        script = self.scripts[min(len(self.requests), len(self.scripts)) - 1]
        if isinstance(script, BaseException):
            raise script
        return Reply(script)


def run(puller):
    """Start and wait for the background thread to finish."""
    puller.start()
    puller.wait(5)
    return puller.status()


GOOD = lines(
    {"status": "pulling manifest"},
    {"status": "pulling aaa", "digest": "sha256:aaa", "total": 1000, "completed": 250},
    {"status": "pulling aaa", "digest": "sha256:aaa", "total": 1000, "completed": 1000},
    {"status": "pulling bbb", "digest": "sha256:bbb", "total": 100, "completed": 100},
    {"status": "verifying sha256 digest"},
    {"status": "writing manifest"},
    {"status": "success"},
)


# ---- what is asked of Ollama

def test_it_asks_ollama_to_pull_exactly_its_own_model_and_nothing_else():
    opener = Opener(GOOD)
    run(ModelPuller(MODEL, "http://localhost:11434", opener=opener))
    [request] = opener.requests
    assert request.full_url == "http://localhost:11434/api/pull" and request.get_method() == "POST"
    assert json.loads(request.data) == {"model": MODEL, "stream": True}


def test_a_trailing_slash_in_the_address_is_fine():
    opener = Opener(GOOD)
    run(ModelPuller(MODEL, "http://localhost:11434/", opener=opener))
    assert opener.requests[0].full_url == "http://localhost:11434/api/pull"


def test_a_read_timeout_is_set_so_a_dead_connection_cannot_hang_forever():
    opener = Opener(GOOD)
    run(ModelPuller(MODEL, opener=opener))
    assert isinstance(opener.timeouts[0], (int, float)) and 0 < opener.timeouts[0] <= 600


# ---- the status

def test_before_anything_the_state_is_idle():
    status = ModelPuller(MODEL, opener=Opener(GOOD)).status()
    assert status == {"state": "idle", "model": MODEL, "message": None, "percent": None, "completed": None, "total": None, "error": None}


def test_a_good_pull_ends_done_at_100_percent():
    status = run(ModelPuller(MODEL, opener=Opener(GOOD)))
    assert status["state"] == "done" and status["percent"] == 100 and status["error"] is None
    assert (status["completed"], status["total"]) == (1100, 1100)
    assert status["message"] == "success"


def test_progress_adds_up_the_layers():
    script = lines({"status": "pulling a", "digest": "sha256:a", "total": 1000, "completed": 250}, {"status": "pulling b", "digest": "sha256:b", "total": 1000, "completed": 750})
    status = run(ModelPuller(MODEL, opener=Opener(script)))
    assert (status["completed"], status["total"], status["percent"]) == (1000, 2000, 50)
    assert status["state"] == "failed", "the stream stopped before `success`"


def test_the_percent_never_goes_backwards_when_a_new_layer_appears():
    script = lines(
        {"status": "pulling a", "digest": "sha256:a", "total": 100, "completed": 100},      # 100 of 100
        {"status": "pulling b", "digest": "sha256:b", "total": 900, "completed": 0},        # a bigger layer starts: 100 of 1000
        {"status": "pulling b", "digest": "sha256:b", "total": 900, "completed": 100},      # 200 of 1000
        {"status": "success"},
    )
    percents = []
    puller = ModelPuller(MODEL, opener=Opener(script))
    real = puller._apply

    def record(item):
        stop = real(item)
        percents.append(puller.status()["percent"])
        return stop
    puller._apply = record
    run(puller)
    assert percents[0] == 100 and all(b >= a for a, b in zip(percents, percents[1:])) and max(percents) <= 100


def test_the_percent_is_unknown_until_a_total_is_known():
    puller = ModelPuller(MODEL, opener=Opener(lines({"status": "pulling manifest"})))
    status = run(puller)
    assert status["percent"] is None and status["total"] is None


def test_the_status_is_a_copy():
    puller = ModelPuller(MODEL, opener=Opener(GOOD))
    status = run(puller)
    status["state"] = "hacked"
    assert puller.status()["state"] == "done"


def test_a_line_that_is_not_json_is_skipped_and_the_pull_still_finishes():
    script = [b"\n", b"not json at all\n", b"[1, 2]\n"] + lines({"status": "success"})
    assert run(ModelPuller(MODEL, opener=Opener(script)))["state"] == "done"


# ---- what goes wrong

def test_an_error_line_fails_the_pull_with_ollamas_own_words():
    script = lines({"status": "pulling manifest"}, {"error": "pull model manifest: file does not exist"})
    status = run(ModelPuller(MODEL, opener=Opener(script)))
    assert status["state"] == "failed" and "file does not exist" in status["error"]


def test_a_stream_that_stops_without_success_is_a_failure():
    status = run(ModelPuller(MODEL, opener=Opener(lines({"status": "pulling manifest"}))))
    assert status["state"] == "failed" and "stopped" in status["error"].lower()


def test_ollama_not_running_is_a_failure_with_a_plain_sentence_and_no_address():
    refused = urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
    status = run(ModelPuller(MODEL, "http://localhost:11434", opener=Opener(refused)))
    assert status["state"] == "failed"
    assert "Ollama" in status["error"] and "running" in status["error"]
    assert "11434" not in status["error"] and "refused" not in status["error"].lower() and "Traceback" not in status["error"]


def test_a_dropped_connection_in_the_middle_is_a_failure():
    class Breaks:
        def __enter__(self):
            def gen():
                yield from lines({"status": "pulling a", "digest": "sha256:a", "total": 10, "completed": 5})
                raise ConnectionResetError("reset by peer at 127.0.0.1")
            return gen()

        def __exit__(self, *exc):
            return False
    status = run(ModelPuller(MODEL, opener=lambda request, timeout=None: Breaks()))
    assert status["state"] == "failed" and "127.0.0.1" not in status["error"] and "Ollama" in status["error"]


def test_an_http_error_is_a_failure_with_ollamas_message_when_it_has_one():
    body = io.BytesIO(json.dumps({"error": "disk is full"}).encode())
    error = urllib.error.HTTPError("http://localhost:11434/api/pull", 500, "Internal Server Error", {}, body)
    status = run(ModelPuller(MODEL, opener=Opener(error)))
    assert status["state"] == "failed" and "disk is full" in status["error"]


def test_an_http_error_without_a_readable_body_still_says_something_useful():
    error = urllib.error.HTTPError("http://localhost:11434/api/pull", 502, "Bad Gateway", {}, io.BytesIO(b"<html>"))
    status = run(ModelPuller(MODEL, opener=Opener(error)))
    assert status["state"] == "failed" and status["error"] and "<html>" not in status["error"]


def test_a_very_long_error_is_cut():
    status = run(ModelPuller(MODEL, opener=Opener(lines({"error": "x" * 5000}))))
    assert len(status["error"]) <= 400


# ---- asking again

def test_asking_while_a_pull_runs_does_not_start_a_second_download():
    gate = threading.Event()

    class Slow:
        def __enter__(self):
            def gen():
                yield from lines({"status": "pulling manifest"})
                assert gate.wait(5)
                yield from lines({"status": "success"})
            return gen()

        def __exit__(self, *exc):
            return False
    calls = []

    def opener(request, timeout=None):
        calls.append(request)
        return Slow()
    puller = ModelPuller(MODEL, opener=opener)
    first = puller.start()
    second = puller.start()
    assert first["state"] == "pulling" and second["state"] == "pulling"
    gate.set()
    puller.wait(5)
    assert len(calls) == 1 and puller.status()["state"] == "done"


def test_asking_again_after_a_failure_tries_again():
    opener = Opener(lines({"error": "network down"}), GOOD)
    puller = ModelPuller(MODEL, opener=opener)
    assert run(puller)["state"] == "failed"
    status = run(puller)
    assert status["state"] == "done" and status["error"] is None and len(opener.requests) == 2


def test_asking_again_after_a_success_pulls_again_and_resets_the_progress():
    opener = Opener(GOOD, lines({"status": "pulling manifest"}, {"status": "success"}))
    puller = ModelPuller(MODEL, opener=opener)
    run(puller)
    status = run(puller)
    assert status["state"] == "done" and len(opener.requests) == 2 and status["total"] is None


# ---- on_done

def test_on_done_runs_once_after_a_success_and_sees_the_done_state():
    seen = []
    puller = ModelPuller(MODEL, opener=Opener(GOOD), on_done=lambda: seen.append(puller.status()["state"]))
    run(puller)
    assert seen == ["done"]


def test_on_done_never_runs_after_a_failure():
    seen = []
    run(ModelPuller(MODEL, opener=Opener(lines({"error": "no"})), on_done=lambda: seen.append(1)))
    assert seen == []


def test_a_crash_in_on_done_does_not_turn_the_pull_into_a_failure():
    def boom():
        raise RuntimeError("warm-up broke")
    status = run(ModelPuller(MODEL, opener=Opener(GOOD), on_done=boom))
    assert status["state"] == "done" and status["error"] is None


def test_the_background_thread_is_a_daemon_so_it_never_keeps_the_app_alive():
    puller = ModelPuller(MODEL, opener=Opener(GOOD))
    puller.start()
    assert puller._thread.daemon is True
    puller.wait(5)


# ---- gaps the mutation checks found

def test_what_start_returns_is_a_copy_too():
    puller = ModelPuller(MODEL, opener=Opener(GOOD))
    returned = puller.start()
    puller.wait(5)
    returned["state"] = "hacked"
    assert puller.status()["state"] == "done"


def test_a_second_pull_counts_only_its_own_layers():
    second = lines({"status": "pulling c", "digest": "sha256:ccc", "total": 50, "completed": 50}, {"status": "success"})
    puller = ModelPuller(MODEL, opener=Opener(GOOD, second))
    run(puller)
    status = run(puller)
    assert (status["completed"], status["total"]) == (50, 50)


def test_a_long_http_error_message_is_cut_too():
    body = io.BytesIO(json.dumps({"error": "y" * 5000}).encode())
    error = urllib.error.HTTPError("http://localhost:11434/api/pull", 500, "Internal Server Error", {}, body)
    assert len(run(ModelPuller(MODEL, opener=Opener(error)))["error"]) <= 400


def test_success_means_100_percent_even_if_the_last_layer_never_reported_its_end():
    script = lines({"status": "pulling a", "digest": "sha256:a", "total": 1000, "completed": 500}, {"status": "success"})
    status = run(ModelPuller(MODEL, opener=Opener(script)))
    assert status["state"] == "done" and status["percent"] == 100


def test_a_crash_in_on_done_leaves_no_unhandled_error_in_the_thread(monkeypatch):
    problems = []
    monkeypatch.setattr(threading, "excepthook", lambda args: problems.append(args))

    def boom():
        raise RuntimeError("warm-up broke")
    run(ModelPuller(MODEL, opener=Opener(GOOD), on_done=boom))
    assert problems == []
