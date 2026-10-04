"""Which model a margin or a cutoff was measured on, and whether it still applies (task 7.14).

An embedder identity is `tag@digest`. The TAG names the model; the digest names the exact weights. `ollama pull` on the same tag can bring new weights and
so a new digest. Scores from the same model family with slightly different weights are close (devlog 72: margins from 0.15 to 0.50 gave the same
result), so a number measured for a tag still applies to a new digest of it. The digest is kept as provenance: the caller gets a note, not silence.
A different TAG is a different model with a different score scale: the number does not apply.
"""


def _tag(identity: str) -> str:
    return identity.split("@", 1)[0]


def _digest(identity: str) -> str:
    return identity.split("@", 1)[1] if "@" in identity else "no digest"


def calibration_match(measured_on: str, current: str) -> tuple[bool, str | None]:
    """(applies, note). Same identity: (True, None). Same tag, other digest: (True, a note). Other tag: (False, None): the caller says why."""
    if measured_on == current:
        return True, None
    if _tag(measured_on) != _tag(current):
        return False, None
    return True, (f"measured on {_digest(measured_on)}, this index uses {_digest(current)}; "
                  f"results should be close, re-run the eval to confirm")
