"""`create_app` for the tests of the endpoints: the same app, with the security guard switched off (those tests are about what the endpoints do).
Security itself is tested in test_api_security.py with a real token. The real app has no such switch: with no `security=` argument it reads the
environment and refuses to start without a token."""
import main
from security import NO_SECURITY_FOR_TESTS


def create_app(*args, **kwargs):
    kwargs.setdefault("security", NO_SECURITY_FOR_TESTS)
    return main.create_app(*args, **kwargs)
