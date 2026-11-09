"""The DecayingQuantileBar: the bar is a high quantile of recent scores, each counting for less the older it is (EWMQ).

Every score in memory is weighted by 2 ** (-age / half_life_seconds): it counts fully when it is heard, half as much one half-life later,
a quarter as much after two, and so on. The bar is the `bar_percentile` of the weighted scores. A zinger from a while ago therefore fades by
degrees instead of holding the bar until it falls off the edge of a window, so a good score soon after it can still clear the bar.

The quantile is read by linear interpolation: line the scores up from lowest to highest, give each its share of the total weight, and
read along a straight line from each score's value at the start of its share to its own at the end. That makes the percentile a smooth
dial even when only a few dozen scores are in memory, where picking an actual score would jump (a 99.5th percentile of fewer than 200
equal scores is just their best). A faded score moves the bar only by the gap between its neighbours at most.

STARTING LINE. There is no warm-up period. The bar begins at `starting_bar`, a stand-in for the room's usual high tide. The line counts
for `starting_bar_weight` scores' worth (20, like a minute or two of talk), fading with the same half-life as real scores, and the bar is a
blend of the two: the real scores' quantile times their share of the weight, plus the starting line times its share. So the bar slides
from the starting line to what the room really does as real scores arrive, one early score cannot drag it far, and after a long silence,
when every real score has faded, it returns to the starting line.

THE ANCHOR (TICKS). Silence has no scores, so by itself it cannot lower the bar: every weight fades by the same factor and their shares do not change.
So a steady tick runs under the room the whole time, one every `anchor_interval_minutes` on a fixed grid from `started_at`, when the stream began. A tick is a stand-in score
of `anchor_score` that sits in the bar's memory like any score and fades on the same half-life. While people talk, the real scores bury the ticks; when the
room goes quiet nothing buries them any more, and the bar settles toward the anchor. There is no silence detection and no cutoff. Ticks only ever shape the
quantile: they are never judged, never qualify, never start a cooldown, and are not counted as scores (`window_size_at` leaves them out).

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A bad setting is replaced by its default and recorded in `report`; bad
    input to `add` is ignored. `strict=True` raises DecayingQuantileBarError instead, for tests and dev tools.
  * Decay is by time, not by number of scores, so a quiet room and a busy one both remember "the last few minutes".
  * Scores, and ticks, older than `discard_scores_older_than_minutes` are dropped, so memory stays bounded however long the room talks. It is a setting
    and not a rule of the module, because it decides what the bar does after a long quiet: once every real score is gone, the bar is made of the
    ticks alone (the anchor), or is the starting bar if no tick has come yet. A weight that has faded to nothing (below what a float can hold)
    is dropped too, so a very long memory can never divide by zero.
  * The quantile and the starting line are separate. `_quantile(entries, percentile)` takes its percentile as an argument and knows nothing of the
    starting line; the blend is applied only in `level_at`, around the reading at `bar_percentile`.
  * Ticks are never stored. They are a pure function of the clock (the grid from `started_at`), so there is no ticker to run and nothing to flush, a read at any
    time gives the same answer, and a recording replayed fast gives what a live run would. `_ticks_at(now)` lists the grid times still within memory, each
    as an entry at the anchor score weighted by its age, exactly as if every tick had been added when its time came.
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
from pom.qualifying.bar import Bar

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_BAR_PERCENTILE = 99.5
DEFAULT_HALF_LIFE_SECONDS = 300.0
DEFAULT_STARTING_BAR = 6.8
DEFAULT_STARTING_BAR_WEIGHT = 20.0
DEFAULT_ANCHOR_SCORE = 5.85
DEFAULT_ANCHOR_INTERVAL_MINUTES = 1.0
DEFAULT_DISCARD_SCORES_OLDER_THAN_MINUTES = 120.0
DEFAULT_STARTED_AT = 0.0


class DecayingQuantileBarError(ValueError):
    """Raised for any bar problem, but only in strict mode (development and tests)."""


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class DecayingQuantileBar(Bar):
    """A weighted, interpolated high quantile of the recent scores.

    Attributes:
        report: Problems met (a bad setting replaced by its default).
    """

    def __init__(self, bar_percentile: float = DEFAULT_BAR_PERCENTILE, half_life_seconds: float = DEFAULT_HALF_LIFE_SECONDS,
                 starting_bar: float = DEFAULT_STARTING_BAR, starting_bar_weight: float = DEFAULT_STARTING_BAR_WEIGHT,
                 anchor_score: float = DEFAULT_ANCHOR_SCORE, anchor_interval_minutes: float = DEFAULT_ANCHOR_INTERVAL_MINUTES,
                 discard_scores_older_than_minutes: float = DEFAULT_DISCARD_SCORES_OLDER_THAN_MINUTES, started_at: float = DEFAULT_STARTED_AT,
                 strict: bool = False):
        """Args:
            bar_percentile: How much of the weighted scores the bar sits above, above 0 and at most 100.
            half_life_seconds: Seconds for a score's weight to halve, above 0.
            starting_bar: The bar before any real score has been heard, and where it returns after a long silence.
            starting_bar_weight: How many scores' worth of weight the starting line begins with, above 0.
            anchor_score: The value of each tick, the level the bar settles toward in silence. Any number.
            anchor_interval_minutes: Minutes between ticks, above 0. Shorter pulls the bar down faster in silence, and a little lower during talk.
            discard_scores_older_than_minutes: Minutes after which a score, or a tick, is dropped from the bar's memory, above 0.
            started_at: The stream time at which the bar begins, in the same seconds as the times it is given: the tick grid and the starting bar's fade both run
                from it. The Printer passes the time at which listening starts (0, for a stream that begins at 0).
            strict: Raise DecayingQuantileBarError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=DecayingQuantileBarError, logger=_log)
        self._bar_percentile = self._valid(bar_percentile, "bar_percentile", DEFAULT_BAR_PERCENTILE, lambda v: _is_number(v) and 0 < v <= 100,
                                       "a number above 0 and at most 100")
        self._half_life = self._valid(half_life_seconds, "half_life_seconds", DEFAULT_HALF_LIFE_SECONDS,
                                      lambda v: _is_number(v) and v > 0, "a number above 0")
        self._starting_bar = self._valid(starting_bar, "starting_bar", DEFAULT_STARTING_BAR, _is_number, "a number")
        self._starting_bar_weight = self._valid(starting_bar_weight, "starting_bar_weight", DEFAULT_STARTING_BAR_WEIGHT,
                                            lambda v: _is_number(v) and v > 0, "a number above 0")
        self._anchor_score = self._valid(anchor_score, "anchor_score", DEFAULT_ANCHOR_SCORE, _is_number, "a number")
        self._anchor_interval = 60.0 * self._valid(anchor_interval_minutes, "anchor_interval_minutes", DEFAULT_ANCHOR_INTERVAL_MINUTES,
                                                   lambda v: _is_number(v) and v > 0, "a number above 0")
        self._discard_seconds = 60.0 * self._valid(discard_scores_older_than_minutes, "discard_scores_older_than_minutes",
                                                   DEFAULT_DISCARD_SCORES_OLDER_THAN_MINUTES, lambda v: _is_number(v) and v > 0, "a number above 0")
        self._memory: Deque[Tuple[float, float]] = deque()          # (time heard, score), oldest first; the real scores only, never the ticks
        self._started_at = self._valid(started_at, "started_at", DEFAULT_STARTED_AT, _is_number, "a number")

    @classmethod
    def from_settings(cls, settings, started_at: float, strict: bool = False) -> "DecayingQuantileBar":
        """Builds the bar from an object with `bar_percentile`, `half_life_seconds`, `starting_bar`, `anchor_score`, `anchor_interval_minutes`,
        `discard_scores_older_than_minutes` and `starting_bar_weight`."""
        return cls(settings.bar_percentile, settings.half_life_seconds, settings.starting_bar, anchor_score=settings.anchor_score,
                   anchor_interval_minutes=settings.anchor_interval_minutes,
                   discard_scores_older_than_minutes=settings.discard_scores_older_than_minutes,
                   starting_bar_weight=settings.starting_bar_weight, started_at=started_at, strict=strict)

    def _valid(self, value, name: str, default, is_good, wanted: str):
        """The value if it passes `is_good`, otherwise the default with a warning."""
        if is_good(value):
            return float(value)
        self.report.warn(f"{name} must be {wanted}, got {value!r}; using {default:g}")
        return default

    # --- The Bar questions ---------------------------------------------------

    def level_at(self, now: float) -> Optional[float]:
        """The weighted, interpolated percentile of the real scores and the ticks, blended with the fading starting line, at `now`."""
        return self._level(now, future=True)

    def level_as_of(self, then: float) -> Optional[float]:
        """The bar as it stood at `then`, a time already past: the same as `level_at`, but a score heard after `then` has no say."""
        return self._level(then, future=False)

    def ticks_between(self, start: float, end: float) -> list:
        """Each tick from `start` to `end`, as (time, anchor score, the bar as it stood at that tick), oldest first. It is how a chart shows the
        bar sliding down through a silence: the ticks fall on the grid whether or not anyone spoke."""
        if not (_is_number(start) and _is_number(end)):
            return []
        first = max(1, math.ceil((start - self._started_at) / self._anchor_interval))
        last = math.floor((end - self._started_at) / self._anchor_interval)
        times = [self._started_at + k * self._anchor_interval for k in range(first, last + 1)]
        return [(at, self._anchor_score, self.level_as_of(at)) for at in times]

    def _level(self, now: float, future: bool) -> Optional[float]:
        """The bar at `now`; `future` says whether a score heard after `now` still counts (as a stepped-back clock would have it) or not."""
        if not _is_number(now):
            return None
        entries = self._weighted_scores(now, future)
        if not entries:
            return self._starting_bar
        line = self._starting_bar_weight * self._weight(now - self._started_at)
        share = line / (line + sum(weight for _, weight in entries))          # how much of the bar is still the starting line
        return share * self._starting_bar + (1.0 - share) * self._quantile(entries, self._bar_percentile)

    def window_size_at(self, now: float) -> int:
        """How many scores' worth of weight the real scores carry at `now`, to the nearest whole score (the starting line is not
        counted, and neither are the ticks). A score counts for 1 when it is heard and for half that a half-life later, so this is the size of the window the
        bar is really built from (about 100 in a busy room at a 5 minute half-life), not all the scores kept in memory (`discard_scores_older_than_minutes`)."""
        if not _is_number(now):
            return 0
        self._forget_old(now)
        return int(sum(self._weight(now - heard) for heard, _ in self._memory) + 0.5)

    def add(self, score: float, now: float) -> None:
        """Takes in a judged score."""
        if not (_is_number(score) and _is_number(now)):
            return
        self._memory.append((now, float(score)))
        self._forget_old(now)

    def describe(self) -> str:
        """For example: the 99.5th percentile of scores that count half as much every 300 s, starting at 6.8."""
        return (f"the {self._bar_percentile:g}th percentile of scores that count half as much every {self._half_life:g} s, "
                f"starting at {self._starting_bar:g}, with a steady tick of {self._anchor_score:g} every {self._anchor_interval / 60.0:g} min")

    # --- The arithmetic -------------------------------------------------------------

    def _weight(self, age: float) -> float:
        """2 ** (-age / half_life); a negative age (a clock that stepped back) counts as 0."""
        return 2.0 ** (-max(age, 0.0) / self._half_life)

    def _forget_old(self, now: float) -> None:
        """Drops scores older than `discard_scores_older_than_minutes`."""
        cutoff = now - self._discard_seconds
        while self._memory and self._memory[0][0] < cutoff:
            self._memory.popleft()

    def _weighted_scores(self, now: float, future: bool = True) -> list:
        """What the bar is read from at `now`, each as (score, weight): the real scores still remembered and the ticks, with no starting line in it."""
        self._forget_old(now)
        entries = [(score, self._weight(now - heard)) for heard, score in self._memory if future or heard <= now] + self._ticks_at(now)
        return [(score, weight) for score, weight in entries if weight > 0.0]          # a weight faded to nothing has no say, and must not be divided by

    def _ticks_at(self, now: float) -> list:
        """The ticks within memory at `now`, each as (anchor score, weight by its age). Tick k is heard at `started_at` plus k intervals (k from 1), and
        is kept while it is no older than a score would be; none exist before the first interval after `started_at` has gone by."""
        last = math.floor((now - self._started_at) / self._anchor_interval)
        first = max(1, math.ceil((now - self._discard_seconds - self._started_at) / self._anchor_interval))
        return [(self._anchor_score, self._weight(now - (self._started_at + k * self._anchor_interval))) for k in range(first, last + 1)]

    @staticmethod
    def _quantile(entries, percentile: float) -> float:
        """The weighted quantile of `entries` (score, weight) at `percentile`, from 0 to 100: a straight line through the cumulative weight,
        each score covering its own share. It takes any percentile and knows nothing of the starting line, which is blended in by `level_at`."""
        ranked = sorted(entries)
        target = percentile / 100.0 * sum(weight for _, weight in ranked)
        before, previous_score = 0.0, ranked[0][0]
        for score, weight in ranked:
            if target <= before + weight:                    # the target falls inside this score's share of the weight
                return previous_score + (target - before) / weight * (score - previous_score)
            before += weight
            previous_score = score
        return ranked[-1][0]
