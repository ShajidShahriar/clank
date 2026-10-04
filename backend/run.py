"""Start the backend: `python run.py --port N` (task 8.6).

It listens on 127.0.0.1 and nowhere else: there is deliberately no host option, so the address is not a setting that could be set wrong. The security settings
(`CLANK_API_TOKEN`, `CLANK_ALLOWED_ORIGINS`) are checked BEFORE the server starts, so a missing token is a clear message and exit code 2, not a server that
runs but refuses everything. The token is never printed.
"""
import argparse
import os
import sys

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


def main(argv=None, *, environ=None, serve=None) -> int:
    parser = argparse.ArgumentParser(prog="run.py", description="Start the Clank backend on 127.0.0.1.", allow_abbrev=False)
    parser.add_argument("--port", type=_port, required=True, help="the port to listen on (127.0.0.1 only)")
    args = parser.parse_args(argv)
    try:
        Security.from_env(os.environ if environ is None else environ)
    except SecurityConfigError as problem:
        print(f"Clank backend not started: {problem}", file=sys.stderr)
        return 2
    if serve is None:
        import uvicorn
        serve = uvicorn.run
    serve(app="main:app", host=LOOPBACK, port=args.port, proxy_headers=False, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
