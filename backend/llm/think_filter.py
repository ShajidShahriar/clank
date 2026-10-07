"""Tell thinking from answer in models that write their thinking INLINE, as `<think>...</think>` at the very start of the answer (some local models do).

Fed the answer's text piece by piece, it gives back ("thinking", text) and ("text", text) pieces, never an empty one, never the tags themselves. The tags are only
recognised at the very START of the answer (after spaces or blank lines): anywhere else `<think>` is ordinary text. A tag cut in pieces ("<thi" + "nk>") is found; text
that only looks like a tag is returned unchanged and in order. After the closing tag the blank lines are dropped once. What it holds back is at most the length of a tag
(so a hostile stream of "<<<<" cannot make it grow), and `finish()` gives back whatever is still held. Pure: no I/O, no clock.
"""
import re

OPEN, CLOSE = "<think>", "</think>"
_BLANK_LINES = re.compile(r"(?:[ \t]*\r?\n)*")      # the blank lines a model leaves between its thinking and its answer
MAX_HELD_SPACE = 64                                     # blank space waiting to see whether a line follows: never more than this

_START, _INSIDE, _AFTER, _TEXT = "start", "inside", "after", "text"


class ThinkTagFilter:
    def __init__(self):
        self._state = _START
        self._held = ""                     # text not yet decided: a possible tag start (or, after the closing tag, blank space)

    def feed(self, text: str) -> list[tuple[str, str]]:
        if self._state == _TEXT:
            return [("text", text)] if text else []
        self._held += text
        return self._step()

    def finish(self) -> list[tuple[str, str]]:
        held, self._held = self._held, ""
        if not held:
            return []
        if self._state == _INSIDE:
            return [("thinking", held)]
        if self._state == _START:
            return [("text", held)]                     # it never became a tag
        return []                                       # blank space after the closing tag

    def _step(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        while True:
            if self._state == _START:
                stripped = self._held.lstrip()
                if stripped.startswith(OPEN):
                    self._held = stripped[len(OPEN):]
                    self._state = _INSIDE
                    continue
                if OPEN.startswith(stripped):           # empty or a prefix of the tag so far: wait for more
                    return out
                self._state = _TEXT                     # not a tag: the held text (with its leading spaces) is plain answer text
                held, self._held = self._held, ""
                out.append(("text", held))
                return out
            if self._state == _INSIDE:
                end = self._held.find(CLOSE)
                if end != -1:
                    if end:
                        out.append(("thinking", self._held[:end]))
                    self._held = self._held[end + len(CLOSE):]
                    self._state = _AFTER
                    continue
                keep = _partial_suffix(self._held, CLOSE)       # a closing tag may be cut: hold only what could still become one
                emit = self._held[:len(self._held) - keep]
                if emit:
                    out.append(("thinking", emit))
                self._held = self._held[len(self._held) - keep:]
                return out
            if self._state == _AFTER:
                rest = self._held[_BLANK_LINES.match(self._held).end():]       # blank lines go; the indentation of the first real line stays
                if not rest.strip() and len(rest) <= MAX_HELD_SPACE:
                    self._held = rest                   # only spaces so far: they may be indentation or the end of a blank line: wait
                    return out
                self._state = _TEXT
                self._held = ""
                out.append(("text", rest))
                return out
            return out


def _partial_suffix(text: str, tag: str) -> int:
    """How many characters at the end of `text` could be the start of `tag` (the longest such suffix, shorter than the whole tag)."""
    for size in range(min(len(tag) - 1, len(text)), 0, -1):
        if tag.startswith(text[len(text) - size:]):
            return size
    return 0
