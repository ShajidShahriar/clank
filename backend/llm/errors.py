"""Errors of the answer model. Every message is written for people and never holds the key, the address or the provider's own words (those can carry an
organization id, a key fragment or an internal URL). The API layer turns each class into a status and a code."""


class LLMError(Exception):
    """Base class."""


class LLMNotConfigured(LLMError):
    """No model is set up (for example the key is missing). `env_var` names the variable to set, never its value."""

    def __init__(self, message: str, env_var: str | None = None):
        super().__init__(message)
        self.env_var = env_var


class LLMAuthError(LLMError):
    """The service refused the key (or the network edge refused the request)."""


class LLMModelNotFound(LLMError):
    """The service does not know this model or this address."""


class LLMContextTooLong(LLMError):
    """The request is bigger than the model or the plan accepts."""


class LLMRateLimited(LLMError):
    """The service said to slow down. `retry_after` is the wait in seconds when the service said how long, else None."""

    def __init__(self, message: str = "the rate limit was reached", retry_after: float | None = None, rate_limit_headers: dict | None = None):
        super().__init__(message)
        self.retry_after = retry_after
        self.rate_limit_headers = dict(rate_limit_headers or {})         # the service's `x-ratelimit-*` numbers at the moment it said no (the most useful ones)


class LLMUnavailable(LLMError):
    """The service could not be reached, or it failed on its side."""


class LLMTimeout(LLMError):
    """The service did not answer in time."""


class LLMBadResponse(LLMError):
    """The service answered, but not with a chat answer we can read (or it rejected a request that was ours to get right)."""
