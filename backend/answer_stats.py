"""The numbers behind an answer's summary line: when each stage began and ended, and how many tokens went into thinking and into the answer. Pure arithmetic.

Moments are readings of a monotonic clock in seconds (or None when that moment never came); results are whole milliseconds, never negative, and None for a stage that
did not happen: a model that does not think has NO thinking time (never 0, never invented).
"""
from chunker.core import estimate_tokens


def _ms(start, end):
    if start is None or end is None:
        return None
    return max(0, round((end - start) * 1000))


def build_timings(*, begin, searched, requested, first_piece, first_thinking, first_text, finished) -> dict:
    """search: begin -> searched. wait: request sent -> first piece of any kind (or the end when none came). thinking: first thinking piece -> first answer piece (or the
    end when no answer came). writing: first answer piece -> end. total: begin -> end. A model that was never called (`requested` is None) has only search and total."""
    called = requested is not None
    return {
        "search_ms": _ms(begin, searched),
        "wait_ms": _ms(requested, first_piece if first_piece is not None else finished) if called else None,
        "thinking_ms": _ms(first_thinking, first_text if first_text is not None else finished) if called else None,
        "writing_ms": _ms(first_text, finished) if called else None,
        "total_ms": _ms(begin, finished),
    }


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def token_summary(*, reported_thinking, reported_completion, thinking_pieces: int, text: str) -> dict:
    """Thinking and answer tokens. The service's own numbers when it gives them (`completion` INCLUDES the thinking); otherwise the thinking pieces counted (a piece is
    about one token on the services seen) and the answer's text estimated at len // 3; `estimated` says whether anything here is not the service's own number."""
    thinking, completion = _count(reported_thinking), _count(reported_completion)
    estimated = False
    if thinking is None and thinking_pieces > 0:
        thinking, estimated = thinking_pieces, True
    if completion is not None:
        answer = max(0, completion - (thinking or 0))
    else:
        answer, estimated = estimate_tokens(text), True
    return {"thinking": thinking, "answer": answer, "estimated": estimated}
