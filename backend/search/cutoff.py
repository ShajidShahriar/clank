"""An OPTIONAL relevance cutoff for search (task I-7.8). Default off: the eval found no floor worth adopting (devlog 74), but the app must be able to
use one later (decisions about `/context` defaults), so the mechanism exists and is tested.

`min_score` drops hits whose RAW cosine is below it; `margin` drops hits more than `margin` below the best raw score of the question; both may be given.
Like the demotion margin, a cutoff is only meaningful for the model TAG it was measured on (`calibrated_for`; other weights of the same tag still apply, with a note: `calibration.py`): another model's scores have another scale
(and the eval showed a repo's content moves the scale too), so for any other embedder search cuts nothing and says so.
"""
import math
from dataclasses import dataclass


def _number(name: str, value, low: float | None = None, high: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or (low is not None and value < low) or (high is not None and value > high):
        bounds = "" if low is None else f" between {low:g} and {high:g}" if high is not None else f" of at least {low:g}"
        raise ValueError(f"{name} must be a number{bounds}, got {value!r}")
    return float(value)


@dataclass(frozen=True)
class RelevanceCutoff:
    min_score: float | None = None          # cosine similarity: hits below it are dropped
    margin: float | None = None             # hits further than this below the best hit are dropped
    calibrated_for: str = ""                # the embedder identity (`tag@digest`) the numbers were measured on

    def __post_init__(self):
        if self.min_score is None and self.margin is None:
            raise ValueError("a cutoff needs a min_score or a margin")
        if self.min_score is not None:
            _number("min_score", self.min_score, -1.0, 1.0)
        if self.margin is not None:
            _number("margin", self.margin, 0.0)
        if not isinstance(self.calibrated_for, str) or not self.calibrated_for:
            raise ValueError("calibrated_for must name the embedder identity the cutoff was measured on, for example 'qwen3-embedding:0.6b@ac6da0dfba84'")

    def limit(self, best: float | None) -> float:
        """The lowest raw score that stays, given the best raw score of the question (None when no hit is visible yet)."""
        floor = -math.inf if self.min_score is None else self.min_score
        relative = -math.inf if self.margin is None or best is None else best - self.margin
        return max(floor, relative)


def passes(score: float, limit: float) -> bool:
    return round(score, 9) >= round(limit, 9)
