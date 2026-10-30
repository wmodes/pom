"""The PercentileBar: the bar is a high percentile of the scores heard in a recent window of time.

This is the rule the ConvoInterestTracker has used since the Tukey fence was dropped, moved into a bar of its own so other bars can sit
beside it:

  * The window: the scores heard in the last `window_seconds`, or the last `min_scores` scores if the room is so quiet that fewer than
    that were heard in the time. So the window is the larger of the two. It is a length of time, so a busy room and a quiet one both
    get "the last five minutes"; the count is only a floor under it.
  * The bar: the `percentile` of the window's scores, read by nearest rank. Line the scores up from lowest to highest; the bar is the
    score that has at least that share of the scores at or below it, so it is always a score the room really earned. 100 puts the bar at
    the best score in the window; 99.5 does not sit below the best score until the window holds 200 scores.
  * THE STARTING LINE. Until `min_scores` scores have been heard there is too little to take a percentile of, so the bar is
    `starting_bar` (a stand-in for the room's usual high tide) instead of nothing. There is no silent warm-up; once the room has said
    `min_scores` things, the bar is the room's own.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A bad setting is replaced by its default and recorded in `report`; bad
    input to `add` is ignored. `strict=True` raises PercentileBarError instead, for tests and dev tools.
  * `exclude_triggers` leaves a triggering score out of what the bar remembers. Otherwise the zinger that fired is the room's best score
    and holds the bar up for the rest of the window, shutting out the next good one.
  * Only the scores in the window are kept (never fewer than `min_scores`), so memory is bounded however long the room talks.
  * Only numbers are kept: no words from the room. This module never reads Config; `from_settings` reads an object's attributes.
"""
import math
from collections import deque
from typing import Deque, List, Optional, Tuple

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger
from pom.scoring.trigger_bar import TriggerBar

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_PERCENTILE = 99.5
DEFAULT_WINDOW_SECONDS = 300.0
DEFAULT_MIN_SCORES = 20
DEFAULT_STARTING_BAR = 6.8
MINIMUM_MIN_SCORES = 2                 # a bar needs at least two scores to stand above


class PercentileBarError(ValueError):
    """Raised for any bar problem, but only in strict mode (development and tests)."""


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class PercentileBar(TriggerBar):
    """A nearest-rank percentile of the recent window.

    Attributes:
        report: Problems met (a bad setting replaced by its default).
    """

    def __init__(self, percentile: float = DEFAULT_PERCENTILE, window_seconds: float = DEFAULT_WINDOW_SECONDS,
                 min_scores: int = DEFAULT_MIN_SCORES, starting_bar: float = DEFAULT_STARTING_BAR,
                 exclude_triggers: bool = False, strict: bool = False):
        """Args:
            percentile: How much of the window the bar sits above, above 0 and at most 100.
            window_seconds: How many seconds of recent scores the bar is drawn from, above 0.
            min_scores: The window always holds at least this many scores, and until this many have been heard the bar is
                `starting_bar`; a whole number of at least 2.
            starting_bar: The bar until `min_scores` scores have been heard.
            exclude_triggers: True leaves a score that triggered out of what the bar remembers.
            strict: Raise PercentileBarError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=PercentileBarError, logger=_log)
        self._percentile = self._valid(percentile, "percentile", DEFAULT_PERCENTILE,
                                       lambda v: _is_number(v) and 0 < v <= 100, "a number above 0 and at most 100", float)
        self._window_seconds = self._valid(window_seconds, "window_seconds", DEFAULT_WINDOW_SECONDS,
                                           lambda v: _is_number(v) and v > 0, "a number above 0", float)
        self._min_scores = self._valid(min_scores, "min_scores", DEFAULT_MIN_SCORES,
                                       lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= MINIMUM_MIN_SCORES,
                                       f"a whole number of at least {MINIMUM_MIN_SCORES}", int)
        self._starting_bar = self._valid(starting_bar, "starting_bar", DEFAULT_STARTING_BAR, _is_number, "a number", float)
        self._exclude_triggers = bool(exclude_triggers)
        self._recent: Deque[Tuple[float, float]] = deque()        # (time heard, score), oldest first

    @classmethod
    def from_settings(cls, settings, strict: bool = False) -> "PercentileBar":
        """Builds the bar from an object with `percentile`, `window_seconds`, `min_scores`, `starting_bar` and `exclude_triggers`."""
        return cls(settings.percentile, settings.window_seconds, settings.min_scores, settings.starting_bar,
                   settings.exclude_triggers, strict=strict)

    def _valid(self, value, name: str, default, is_good, wanted: str, convert):
        """The value if it passes `is_good`, otherwise the default with a warning."""
        if is_good(value):
            return convert(value)
        self.report.warn(f"{name} must be {wanted}, got {value!r}; using {default:g}")
        return default

    # --- The TriggerBar questions ---------------------------------------------------

    def level_at(self, now: float) -> Optional[float]:
        """The percentile of the window at `now`, or the starting line while fewer than `min_scores` scores have been heard."""
        if not _is_number(now):
            return None
        window = self._window(now)
        if window is None:
            return self._starting_bar
        ranked = sorted(window)
        rank = math.ceil(round(self._percentile * len(ranked) / 100.0, 9))       # rounded: 64.4% of 250 is 161, not 161.00000000000003
        return ranked[rank - 1]

    def window_size_at(self, now: float) -> int:
        """How many scores the window holds at `now`; while fewer than `min_scores` are held at all, that is all of them."""
        window = self._window(now) if _is_number(now) else None
        return len(window) if window is not None else len(self._recent)

    def add(self, score: float, now: float, triggered: bool) -> None:
        """Takes in a judged score. A triggering score is left out when `exclude_triggers` is on."""
        if not (_is_number(score) and _is_number(now)):
            return
        if triggered and self._exclude_triggers:
            return
        self._recent.append((now, float(score)))
        self._forget_old(now)

    # --- The window -----------------------------------------------------------------

    def _window(self, now: float) -> Optional[List[float]]:
        """The scores in the window at `now`, or None if fewer than `min_scores` are held at all."""
        if len(self._recent) < self._min_scores:
            return None
        cutoff = now - self._window_seconds
        inside = [score for heard, score in self._recent if heard >= cutoff]
        if len(inside) >= self._min_scores:
            return inside
        return [score for _, score in list(self._recent)[-self._min_scores:]]      # a quiet room: the last few

    def _forget_old(self, now: float) -> None:
        """Drops scores older than the window, but never below `min_scores` held, so a quiet room keeps its floor."""
        cutoff = now - self._window_seconds
        while len(self._recent) > self._min_scores and self._recent[0][0] < cutoff:
            self._recent.popleft()
