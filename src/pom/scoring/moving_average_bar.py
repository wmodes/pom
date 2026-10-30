"""The MovingAverageBar: the bar is a set fraction above a decaying average of the room's scores (the first trigger rule).

This is the way the Printer first decided, restored beside the newer bars so it can still be chosen:

  * The floor: a score below `average_floor_fraction` of the way up the score range (5% to begin with: 0.45 on the 0 to 9 scale) is
    ignored. It never feeds the average and can never trigger. It is a fixed line, which is exactly what the newer bars avoid, and it is
    why this one is kept as a choice and not as the default.
  * The average: a decaying moving average of the scores at or above the floor. Each new score moves it part of the way toward itself,
    so old talk fades without a clock. How fast is a half-life in scores, `average_half_life_scores`: a score's influence halves after
    that many more scores (10 to begin with, which moves the average about 6.7% of the way toward each new score).
  * The bar: the average times (1 + `average_fraction`), so 0.25 puts it 25% above the average.
  * THE STARTING LINE. The average begins at `starting_bar` divided by (1 + `average_fraction`), so the bar begins at `starting_bar`
    and slides to the room's level as scores arrive. (The first design had no starting line: the first score became the average and
    nothing could trigger until ten had counted.)

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A bad setting is replaced by its default and recorded in `report`; bad
    input to `add` is ignored. `strict=True` raises MovingAverageBarError instead, for tests and dev tools.
  * Decay is by score, as it always was, not by time, so silence leaves the average where it was.
  * A triggering score joins the average like any other (the original did, and there is no opt-out).
  * The score range (0 to 9, the digits the scorer is asked for) is a parameter with that default, not a setting.
  * Only numbers are kept: no words from the room. This module never reads Config; `from_settings` reads an object's attributes.
"""
import math
from typing import Optional, Sequence

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger
from pom.scoring.trigger_bar import TriggerBar

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_FRACTION = 0.25
DEFAULT_HALF_LIFE_SCORES = 10.0
DEFAULT_FLOOR_FRACTION = 0.05
DEFAULT_STARTING_BAR = 6.8
DEFAULT_SCORE_RANGE = (0.0, 9.0)       # the digits 0 to 9 the scorer model is asked to answer with


class MovingAverageBarError(ValueError):
    """Raised for any bar problem, but only in strict mode (development and tests)."""


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class MovingAverageBar(TriggerBar):
    """A decaying average of the scores above a floor, with the bar a fraction above it.

    Attributes:
        report: Problems met (a bad setting replaced by its default).
        counted: How many scores have counted (reached the floor) so far.
    """

    def __init__(self, fraction: float = DEFAULT_FRACTION, half_life_scores: float = DEFAULT_HALF_LIFE_SCORES,
                 floor_fraction: float = DEFAULT_FLOOR_FRACTION, starting_bar: float = DEFAULT_STARTING_BAR,
                 score_range: Sequence[float] = DEFAULT_SCORE_RANGE, strict: bool = False):
        """Args:
            fraction: How far above the average the bar sits, 0 or more (0.25 is 25% above).
            half_life_scores: How many more scores it takes for a score's influence on the average to halve, above 0.
            floor_fraction: How far up the score range the floor sits, from 0 up to but not including 1.
            starting_bar: The bar before any score has counted.
            score_range: (lowest, highest) possible score; the lowest must be below the highest.
            strict: Raise MovingAverageBarError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=MovingAverageBarError, logger=_log)
        self._fraction = self._valid(fraction, "average_fraction", DEFAULT_FRACTION, lambda v: _is_number(v) and v >= 0,
                                     "a number of at least 0")
        half_life = self._valid(half_life_scores, "average_half_life_scores", DEFAULT_HALF_LIFE_SCORES,
                                lambda v: _is_number(v) and v > 0, "a number above 0")
        floor = self._valid(floor_fraction, "average_floor_fraction", DEFAULT_FLOOR_FRACTION,
                            lambda v: _is_number(v) and 0 <= v < 1, "a number from 0 up to but not including 1")
        starting = self._valid(starting_bar, "starting_bar", DEFAULT_STARTING_BAR, _is_number, "a number")
        low, high = self._valid_range(score_range)
        self._floor = low + floor * (high - low)
        self._weight = 1.0 - 0.5 ** (1.0 / half_life)            # how far each score moves the average toward itself
        self._average = starting / (1.0 + self._fraction)         # so the bar (average x (1 + fraction)) starts at the starting line
        self.counted = 0

    @classmethod
    def from_settings(cls, settings, strict: bool = False) -> "MovingAverageBar":
        """Builds the bar from an object with `average_fraction`, `average_half_life_scores`, `average_floor_fraction`
        and `starting_bar`."""
        return cls(settings.average_fraction, settings.average_half_life_scores, settings.average_floor_fraction,
                   settings.starting_bar, strict=strict)

    def _valid(self, value, name: str, default, is_good, wanted: str) -> float:
        """The value if it passes `is_good`, otherwise the default with a warning."""
        if is_good(value):
            return float(value)
        self.report.warn(f"{name} must be {wanted}, got {value!r}; using {default:g}")
        return default

    def _valid_range(self, value) -> tuple:
        """A (lowest, highest) pair with the lowest below the highest, or the default with a warning."""
        try:
            low, high = value
            if _is_number(low) and _is_number(high) and low < high:
                return float(low), float(high)
        except (TypeError, ValueError):
            pass
        self.report.warn(f"score_range must be (lowest, highest) with the lowest below the highest, got {value!r}; "
                         f"using {DEFAULT_SCORE_RANGE}")
        return DEFAULT_SCORE_RANGE

    # --- The TriggerBar questions ---------------------------------------------------

    def level_at(self, now: float) -> Optional[float]:
        """The average times (1 + fraction). `now` is not used: this bar decays by score, not by time."""
        return self._average * (1.0 + self._fraction)

    def window_size_at(self, now: float) -> int:
        """How many scores have been averaged so far (there is no window; this is for display)."""
        return self.counted

    def ignores(self, score: float) -> bool:
        """True for a score below the floor, and for anything that is not a number."""
        return not (_is_number(score) and score >= self._floor)

    def add(self, score: float, now: float, triggered: bool) -> None:
        """Moves the average toward a score at or above the floor. Scores below it, and junk, are left out."""
        if self.ignores(score):
            return
        self._average += self._weight * (score - self._average)
        self.counted += 1
        _log.debug("average now %.3f after %d counted", self._average, self.counted)
