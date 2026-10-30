"""The DecayingQuantileBar: the bar is a high quantile of recent scores, each counting for less the older it is (EWMQ).

Every score in memory is weighted by 2 ** (-age / half_life_seconds): it counts fully when it is heard, half as much one half-life later,
a quarter as much after two, and so on. The bar is the `percentile` of the weighted scores. A zinger from a while ago therefore fades by
degrees instead of holding the bar until it falls off the edge of a window, so a good score soon after it can still clear the bar.

The quantile is read by linear interpolation: line the scores up from lowest to highest, give each its share of the total weight, and
read along a straight line from each score's value at the start of its share to its own at the end. That makes the percentile a smooth
dial even when only a few dozen scores are in memory, where picking an actual score would jump (a 99.5th percentile of fewer than 200
equal scores is just their best). A faded score moves the bar only by the gap between its neighbours at most.

STARTING LINE. There is no warm-up period. The bar begins at `starting_bar`, a stand-in for the room's usual high tide. The line counts
for `starting_weight` scores' worth (20, like a minute or two of talk), fading with the same half-life as real scores, and the bar is a
blend of the two: the real scores' quantile times their share of the weight, plus the starting line times its share. So the bar slides
from the starting line to what the room really does as real scores arrive, one early score cannot drag it far, and after a long silence,
when every real score has faded, it returns to the starting line.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A bad setting is replaced by its default and recorded in `report`; bad
    input to `add` is ignored. `strict=True` raises DecayingQuantileBarError instead, for tests and dev tools.
  * Decay is by time, not by number of scores, so a quiet room and a busy one both remember "the last few minutes".
  * `exclude_triggers` leaves a triggering score out of what the bar remembers. Otherwise the zinger that fired becomes the room's best
    score and holds the bar up for a while, shutting out the next good one. With it on, the bar stays at the room's usual high tide.
  * Scores older than 8 half-lives (under 0.4% of their weight) are dropped, so memory stays bounded however long the room talks.
  * The starting line is a blend, not an extra score in the list: a stand-in score with almost no weight would still anchor the
    interpolation between its neighbours, and at a percentile of 100 it would be a permanent floor.
  * Only numbers are kept: no words from the room.
  * This module never reads Config; the entry point passes the settings in (`from_settings` reads them off an object).
"""
import math
from collections import deque
from typing import Deque, Optional, Tuple

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger
from pom.scoring.trigger_bar import TriggerBar

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_PERCENTILE = 99.5
DEFAULT_HALF_LIFE_SECONDS = 300.0
DEFAULT_STARTING_BAR = 6.8
DEFAULT_STARTING_WEIGHT = 20.0
MEMORY_HALF_LIVES = 8


class DecayingQuantileBarError(ValueError):
    """Raised for any bar problem, but only in strict mode (development and tests)."""


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class DecayingQuantileBar(TriggerBar):
    """A weighted, interpolated high quantile of the recent scores.

    Attributes:
        report: Problems met (a bad setting replaced by its default).
    """

    def __init__(self, percentile: float = DEFAULT_PERCENTILE, half_life_seconds: float = DEFAULT_HALF_LIFE_SECONDS,
                 starting_bar: float = DEFAULT_STARTING_BAR, exclude_triggers: bool = False,
                 starting_weight: float = DEFAULT_STARTING_WEIGHT, strict: bool = False):
        """Args:
            percentile: How much of the weighted scores the bar sits above, above 0 and at most 100.
            half_life_seconds: Seconds for a score's weight to halve, above 0.
            starting_bar: The bar before any real score has been heard, and where it returns after a long silence.
            exclude_triggers: True leaves a score that triggered out of what the bar remembers.
            starting_weight: How many scores' worth of weight the starting line begins with, above 0.
            strict: Raise DecayingQuantileBarError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=DecayingQuantileBarError, logger=_log)
        self._percentile = self._valid(percentile, "percentile", DEFAULT_PERCENTILE, lambda v: _is_number(v) and 0 < v <= 100,
                                       "a number above 0 and at most 100")
        self._half_life = self._valid(half_life_seconds, "half_life_seconds", DEFAULT_HALF_LIFE_SECONDS,
                                      lambda v: _is_number(v) and v > 0, "a number above 0")
        self._starting_bar = self._valid(starting_bar, "starting_bar", DEFAULT_STARTING_BAR, _is_number, "a number")
        self._starting_weight = self._valid(starting_weight, "starting_weight", DEFAULT_STARTING_WEIGHT,
                                            lambda v: _is_number(v) and v > 0, "a number above 0")
        self._exclude_triggers = bool(exclude_triggers)
        self._memory: Deque[Tuple[float, float]] = deque()          # (time heard, score), oldest first
        self._started_at: Optional[float] = None                    # when the starting line was "heard"

    @classmethod
    def from_settings(cls, settings, strict: bool = False) -> "DecayingQuantileBar":
        """Builds the bar from an object with `percentile`, `half_life_seconds`, `starting_bar` and `exclude_triggers`."""
        return cls(settings.percentile, settings.half_life_seconds, settings.starting_bar, settings.exclude_triggers,
                   strict=strict)

    def _valid(self, value, name: str, default, is_good, wanted: str):
        """The value if it passes `is_good`, otherwise the default with a warning."""
        if is_good(value):
            return float(value)
        self.report.warn(f"{name} must be {wanted}, got {value!r}; using {default:g}")
        return default

    # --- The TriggerBar questions ---------------------------------------------------

    def level_at(self, now: float) -> Optional[float]:
        """The weighted, interpolated percentile of the real scores and the fading starting line at `now`."""
        if not _is_number(now):
            return None
        self._start(now)
        self._forget_old(now)
        if not self._memory:
            return self._starting_bar
        entries = [(score, self._weight(now - heard)) for heard, score in self._memory]
        line = self._starting_weight * self._weight(now - self._started_at)
        share = line / (line + sum(weight for _, weight in entries))          # how much of the bar is still the starting line
        return share * self._starting_bar + (1.0 - share) * self._quantile(entries)

    def window_size_at(self, now: float) -> int:
        """How many real scores are in memory at `now` (the starting line is not counted)."""
        if _is_number(now):
            self._forget_old(now)
        return len(self._memory)

    def add(self, score: float, now: float, triggered: bool) -> None:
        """Takes in a judged score. A triggering score is left out when `exclude_triggers` is on."""
        if not (_is_number(score) and _is_number(now)):
            return
        self._start(now)
        if triggered and self._exclude_triggers:
            return
        self._memory.append((now, float(score)))
        self._forget_old(now)

    def describe(self) -> str:
        """For example: the 99.5th percentile of scores that count half as much every 300 s, starting at 6.8."""
        return (f"the {self._percentile:g}th percentile of scores that count half as much every {self._half_life:g} s, "
                f"starting at {self._starting_bar:g}" + (", triggering scores left out" if self._exclude_triggers else ""))

    # --- The arithmetic -------------------------------------------------------------

    def _start(self, now: float) -> None:
        """The starting line is 'heard' at the first time the bar is used."""
        if self._started_at is None:
            self._started_at = now

    def _weight(self, age: float) -> float:
        """2 ** (-age / half_life); a negative age (a clock that stepped back) counts as 0."""
        return 2.0 ** (-max(age, 0.0) / self._half_life)

    def _forget_old(self, now: float) -> None:
        """Drops scores older than MEMORY_HALF_LIVES half-lives."""
        cutoff = now - MEMORY_HALF_LIVES * self._half_life
        while self._memory and self._memory[0][0] < cutoff:
            self._memory.popleft()

    def _quantile(self, entries) -> float:
        """The weighted quantile at `percentile`: a straight line through the cumulative weight, each score covering its own share."""
        ranked = sorted(entries)
        target = self._percentile / 100.0 * sum(weight for _, weight in ranked)
        before, previous_score = 0.0, ranked[0][0]
        for score, weight in ranked:
            if target <= before + weight:                    # the target falls inside this score's share of the weight
                return previous_score + (target - before) / weight * (score - previous_score)
            before += weight
            previous_score = score
        return ranked[-1][0]
