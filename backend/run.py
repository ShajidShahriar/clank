"""Start the backend: `python run.py --port N` (task 8.6).

It listens on 127.0.0.1 and nowhere else: there is deliberately no host option, so the address is not a setting that could be set wrong. The security settings
(`CLANK_API_TOKEN`, `CLANK_ALLOWED_ORIGINS`) are checked BEFORE the server starts, so a missing token is a clear message and exit code 2, not a server that
runs but refuses everything. The token is never printed.
"""
import argparse
import os
import signal
import sys
import threading

from security import Security, SecurityConfigError

LOOPBACK = "127.0.0.1"


def _port(text: str) -> int:
    try:
        port = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a port number: {text!r}") from None
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"the port must be between 1 and 65535, got {port}")
    return port


def watch_stdin(stream, on_closed) -> threading.Thread:
    """Call `on_closed()` when `stream` reaches its end. The app that starts the backend keeps the backend's stdin pipe open; when that app dies for ANY
    reason (a crash, a kill -9) the operating system closes the pipe, and this is how the backend finds out and stops, instead of living on as an orphan."""
    def watch():
        while stream.read(4096):
            pass                                                   # whatever the parent writes is ignored; only the end matters
        on_closed()
    thread = threading.Thread(target=watch, name="clank-parent-watch", daemon=True)
    thread.start()
    return thread


def _stop_this_process() -> None:
    os.kill(os.getpid(), signal.SIGTERM)                           # uvicorn turns this into a clean shutdown: running jobs are cancelled between files


def main(argv=None, *, environ=None, serve=None, watch=None) -> int:
    parser = argparse.ArgumentParser(prog="run.py", description="Start the Clank backend on 127.0.0.1.", allow_abbrev=False)
    parser.add_argument("--port", type=_port, required=True, help="the port to listen on (127.0.0.1 only)")
    parser.add_argument("--exit-when-stdin-closes", action="store_true",
                        help="stop when the parent process closes this process's stdin (the app that started it has died); off by default so a run from a terminal is not affected")
    args = parser.parse_args(argv)
    try:
        Security.from_env(os.environ if environ is None else environ)
    except SecurityConfigError as problem:
        print(f"Clank backend not started: {problem}", file=sys.stderr)
        return 2
    if args.exit_when_stdin_closes:
        (watch or (lambda: watch_stdin(sys.stdin.buffer, _stop_this_process)))()
    if serve is None:
        import uvicorn
        serve = uvicorn.run
    serve(app="main:app", host=LOOPBACK, port=args.port, proxy_headers=False, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
