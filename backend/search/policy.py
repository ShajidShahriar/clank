"""A ranking policy for tests and changelogs (task I-7.6). A pure function: it is NOT wired into `search` yet.

Tests repeat the words of the question (their names are whole sentences) and changelogs repeat feature names, so they crowd real code out of the top
results. The policy demotes them by a small ADDITIVE margin when hits are ORDERED. Nothing is hidden: a test that beats the best code by more than the
margin stays first. A question that asks for tests (or a changelog) is not demoted for it.

The raw cosine score is never changed (the relevance-floor study needs it): every hit comes back with a separate `adjusted` value that only decides the order.
"""
import math
import re
from dataclasses import dataclass
from typing import NamedTuple

from .core import Hit

_TEST_INTENT = re.compile(r"\b(?:tests?|tested|testing|specs?|fixtures?|pytest)\b", re.IGNORECASE)
_CHANGELOG_INTENT = re.compile(r"\b(?:changelog|change log|release notes|what changed|what['’]?s new|release history)\b", re.IGNORECASE)


class Intent(NamedTuple):
    test: bool
    changelog: bool


@dataclass(frozen=True)
class Adjusted:
    hit: Hit            # the hit as search returned it: the raw score is untouched
    adjusted: float     # the score used to ORDER (raw score minus a margin for tests and changelogs)


def intent_of(question: str) -> Intent:
    """Does the question ask for tests, or for a changelog? Whole words only: "latest version" and "contest" are not about tests."""
    return Intent(bool(_TEST_INTENT.search(question)), bool(_CHANGELOG_INTENT.search(question)))


def _check_margin(name: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a number that is not negative, got {value!r}")
    return float(value)


def apply_test_policy(hits: list[Hit], question: str, tags: dict, *, margin: float, changelog_margin: float | None = None) -> list[Adjusted]:
    """Order `hits` (best first, as search returns them) with tests and changelogs demoted.

    `tags` is `{rel_path: (is_test, is_changelog)}`; a file not in it counts as code. A test loses `margin`, a changelog loses `changelog_margin`
    (default: twice the margin), a file that is both loses the larger one. The sort is stable and compares values rounded to 9 places, so an exact
    tie in the adjusted score keeps the original order even where floating point would say otherwise.
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("the question must be a non-empty string")
    margin = _check_margin("margin", margin)
    changelog_margin = 2 * margin if changelog_margin is None else _check_margin("changelog_margin", changelog_margin)
    intent = intent_of(question)
    adjusted = []
    for hit in hits:
        is_test, is_changelog = tags.get(hit.chunk["rel_path"], (False, False))
        loss = max(margin if is_test and not intent.test else 0.0, changelog_margin if is_changelog and not intent.changelog else 0.0)
        adjusted.append(Adjusted(hit, hit.score - loss))
    return sorted(adjusted, key=lambda a: -round(a.adjusted, 9))
