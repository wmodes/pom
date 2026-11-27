"""The PercentileBar: the bar is a high percentile of the scores heard in a recent window of time.

This is the rule the BarKeeper has used since the Tukey fence was dropped, moved into a bar of its own so other bars can sit
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
  * Nothing here may stop the oracle (see pom/__init__.py). The settings come checked from the config (`[qualifying]`); bad input to `add`
    is ignored.
  * Only the scores in the window are kept (never fewer than `min_scores`), so memory is bounded however long the room talks.
  * Only numbers are kept: no words from the room. This module never reads Config; it is handed the `[qualifying]` settings.
"""
import math
from collections import deque
from typing import Callable, Deque, List, Optional, Tuple

from pom.bookkeeper.bookkeeper import Bookkeeper
from pom.defaults import QualifyingSettings
from pom.qualifying.bar import Bar
from pom.value_checker import ValueChecker

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module


class PercentileBarError(ValueError):
    """Raised for any bar problem, but only in strict mode (development and tests)."""


class PercentileBar(Bar):
    """A nearest-rank percentile of the recent window.

    Attributes:
        logger: Where this class says what it has to say (a LogRecorder from the Bookkeeper).
        qualifying: The `[qualifying]` settings it reads: `bar_percentile` (how much of the window the bar sits above), `window_seconds`
            (how many seconds of recent scores the bar is drawn from), `min_scores` (the window always holds at least this many, and until
            this many have been heard the bar is `starting_bar`) and `starting_bar`.
    """

    def __init__(self, qualifying: QualifyingSettings, strict: Optional[bool] = None, bookkeeper: Optional[Bookkeeper] = None,
                 stream_now: Optional[Callable[[], float]] = None):
        """Args:
            qualifying: The `[qualifying]` settings.
            strict: Raise PercentileBarError on any problem instead of logging it; None (the default) follows the project's `strict` setting.
            bookkeeper: The Bookkeeper the entry point built; it hands this class its logger. None (a test or a tool) gives a plain one.
            stream_now: The stream time now (the Printer's Timekeeper's `now`): the fallback value for a time that is not a number. None (a
                test or a tool) gives the bar's ValueChecker a clock of its own.
        """
        self.logger = (bookkeeper or Bookkeeper()).logger_for(__name__, PercentileBarError, DEBUG, strict)
        self.check = ValueChecker(self.logger, "PercentileBar", stream_now)
        self.qualifying = qualifying
        self._recent: Deque[Tuple[float, float]] = deque()        # (time heard, score), oldest first

    @classmethod
    def from_settings(cls, settings, started_at: float, strict: Optional[bool] = None, bookkeeper: Optional[Bookkeeper] = None,
                      stream_now: Optional[Callable[[], float]] = None) -> "PercentileBar":
        """Builds the bar from the `[qualifying]` settings. `started_at` is the stream time the bar begins; this bar has no clock to start, and
        takes it so the factory can build any bar the same way."""
        return cls(settings, strict=strict, bookkeeper=bookkeeper, stream_now=stream_now)

    # --- The Bar questions ---------------------------------------------------

    def level_at(self, now: float) -> Optional[float]:
        """The percentile of the window at `now`, or the starting line while fewer than `min_scores` scores have been heard."""
        now = self.check.stream_time(now, method="level_at", argument="now")
        window = self._window(now)
        if window is None:
            return self.qualifying.starting_bar
        ranked = sorted(window)
        rank = math.ceil(round(self.qualifying.bar_percentile * len(ranked) / 100.0, 9))       # rounded: 64.4% of 250 is 161, not 161.00000000000003
        return ranked[rank - 1]

    def window_size_at(self, now: float) -> int:
        """How many scores the window holds at `now`; while fewer than `min_scores` are held at all, that is all of them."""
        window = self._window(self.check.stream_time(now, method="window_size_at", argument="now"))
        return len(window) if window is not None else len(self._recent)

    def add(self, score: float, now: float) -> None:
        """Takes in a judged score. A score that is not a number is left out (with a warning); a time that is not a number is now."""
        score = self.check.score(score, method="add", may_be_unscored=False)
        now = self.check.stream_time(now, method="add", argument="now")
        if score is None:
            return
        self._recent.append((now, score))
        self._forget_old(now)

    def describe(self) -> str:
        """For example: the 99.5th percentile of the last 300 s of scores (at least the last 20), starting at 6.8."""
        return (f"the {self.qualifying.bar_percentile:g}th percentile of the last {self.qualifying.window_seconds:g} s of scores "
                f"(at least the last {self.qualifying.min_scores}), starting at {self.qualifying.starting_bar:g}")

    # --- The window -----------------------------------------------------------------

    def _window(self, now: float) -> Optional[List[float]]:
        """The scores in the window at `now`, or None if fewer than `min_scores` are held at all."""
        if len(self._recent) < self.qualifying.min_scores:
            return None
        cutoff = now - self.qualifying.window_seconds
        inside = [score for heard, score in self._recent if heard >= cutoff]
        if len(inside) >= self.qualifying.min_scores:
            return inside
        return [score for _, score in list(self._recent)[-self.qualifying.min_scores:]]      # a quiet room: the last few

    def _forget_old(self, now: float) -> None:
        """Drops scores older than the window, but never below `min_scores` held, so a quiet room keeps its floor."""
        cutoff = now - self.qualifying.window_seconds
        while len(self._recent) > self.qualifying.min_scores and self._recent[0][0] < cutoff:
            self._recent.popleft()
