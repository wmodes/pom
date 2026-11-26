"""The MovingAverageBar: the bar is a set fraction above a decaying average of the room's scores (the first qualifying rule).

This is the way the Printer first decided, restored beside the newer bars so it can still be chosen:

  * The floor: a score below `average_floor_fraction` of the way up the score range (5% to begin with: 0.45 on the 0 to 9 scale) is
    ignored. It never feeds the average and can never qualify. It is a fixed line, which is exactly what the newer bars avoid, and it is
    why this one is kept as a choice and not as the default.
  * The average: a decaying moving average of the scores at or above the floor. Each new score moves it part of the way toward itself,
    so old talk fades without a clock. How fast is a half-life in scores, `average_half_life_scores`: a score's influence halves after
    that many more scores (10 to begin with, which moves the average about 6.7% of the way toward each new score).
  * The bar: the average times (1 + `average_fraction`), so 0.25 puts it 25% above the average.
  * THE STARTING LINE. The average begins at `starting_bar` divided by (1 + `average_fraction`), so the bar begins at `starting_bar`
    and slides to the room's level as scores arrive. (The first design had no starting line: the first score became the average and
    nothing could qualify until ten had counted.)

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). The settings come checked from the config (`[qualifying]`); a bad score
    range is replaced by the default and logged (`strict=True` raises MovingAverageBarError instead, for tests and dev tools); bad input
    to `add` is ignored.
  * Decay is by score, as it always was, not by time, so silence leaves the average where it was.
  * A qualifying score joins the average like any other (the original did, and there is no opt-out).
  * The score range (0 to 9, the digits the scorer is asked for) is a parameter with that default, not a setting.
  * Only numbers are kept: no words from the room. This module never reads Config; it is handed the `[qualifying]` settings.
"""
import math
from typing import Optional, Sequence

from pom.bookkeeper.bookkeeper import Bookkeeper
from pom.defaults import QualifyingSettings
from pom.qualifying.bar import Bar

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module

DEFAULT_SCORE_RANGE = (0.0, 9.0)       # the digits 0 to 9 the scorer model is asked to answer with


class MovingAverageBarError(ValueError):
    """Raised for any bar problem, but only in strict mode (development and tests)."""


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class MovingAverageBar(Bar):
    """A decaying average of the scores above a floor, with the bar a fraction above it.

    Attributes:
        logger: Where this class says what it has to say (a LogRecorder from the Bookkeeper).
        counted: How many scores have counted (reached the floor) so far.
        qualifying: The `[qualifying]` settings it reads: `average_fraction` (how far above the average the bar sits: 0.25 is 25% above),
            `average_half_life_scores` (how many more scores it takes for a score's influence on the average to halve),
            `average_floor_fraction` (how far up the score range the floor sits) and `starting_bar` (the bar before any score has counted).
    """

    def __init__(self, qualifying: QualifyingSettings, score_range: Sequence[float] = DEFAULT_SCORE_RANGE, strict: bool = False,
                 bookkeeper: Optional[Bookkeeper] = None):
        """Args:
            qualifying: The `[qualifying]` settings.
            score_range: (lowest, highest) possible score; the lowest must be below the highest.
            strict: Raise MovingAverageBarError on any problem instead of logging it.
            bookkeeper: The Bookkeeper the entry point built; it hands this class its logger. None (a test or a tool) gives a plain one.
        """
        self.logger = (bookkeeper or Bookkeeper()).logger_for(__name__, MovingAverageBarError, DEBUG, strict)
        self.qualifying = qualifying
        low, high = self._valid_range(score_range)
        self._floor = low + qualifying.average_floor_fraction * (high - low)
        self._weight = 1.0 - 0.5 ** (1.0 / qualifying.average_half_life_scores)            # how far each score moves the average toward itself
        self._average = qualifying.starting_bar / (1.0 + qualifying.average_fraction)       # so the bar (average x (1 + fraction)) starts at the starting line
        self.counted = 0

    @classmethod
    def from_settings(cls, settings, started_at: float, strict: bool = False, bookkeeper: Optional[Bookkeeper] = None) -> "MovingAverageBar":
        """Builds the bar from the `[qualifying]` settings. `started_at` is the stream time the bar begins; this bar has no clock to start, and
        takes it so the factory can build any bar the same way."""
        return cls(settings, strict=strict, bookkeeper=bookkeeper)

    def _valid_range(self, value) -> tuple:
        """A (lowest, highest) pair with the lowest below the highest, or the default with a warning."""
        try:
            low, high = value
            if _is_number(low) and _is_number(high) and low < high:
                return float(low), float(high)
        except (TypeError, ValueError):
            pass
        self.logger.warning("score_range must be (lowest, highest) with the lowest below the highest, got %r; using %s", value, DEFAULT_SCORE_RANGE)
        return DEFAULT_SCORE_RANGE

    # --- The Bar questions ---------------------------------------------------

    def level_at(self, now: float) -> Optional[float]:
        """The average times (1 + fraction). `now` is not used: this bar decays by score, not by time."""
        return self._average * (1.0 + self.qualifying.average_fraction)

    def window_size_at(self, now: float) -> int:
        """How many scores have been averaged so far (there is no window; this is for display)."""
        return self.counted

    def describe(self) -> str:
        """For example: 25% above a decaying average of scores (half-life 10 scores, ignoring scores under 0.45), starting at 6.8."""
        return (f"{self.qualifying.average_fraction * 100:g}% above a decaying average of scores (half-life {self.qualifying.average_half_life_scores:g} scores, ignoring scores "
                f"under {self._floor:.2f}), starting at {self.qualifying.starting_bar:g}")

    def ignores(self, score: float) -> bool:
        """True for a score below the floor, and for anything that is not a number."""
        return not (_is_number(score) and score >= self._floor)

    def add(self, score: float, now: float) -> None:
        """Moves the average toward a score at or above the floor. Scores below it, and junk, are left out."""
        if self.ignores(score):
            return
        self._average += self._weight * (score - self._average)
        self.counted += 1
        self.logger.debug("average now %.3f after %d counted", self._average, self.counted)
