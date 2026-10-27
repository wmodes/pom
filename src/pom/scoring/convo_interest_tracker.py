"""The ConvoInterestTracker: whether a score stands out from how the room has been scoring lately.

The trigger does not use a fixed score. It asks what this room's recent scores look like and responds when
a new one stands well above them. In a dull room a 5 can stand out; in a lively one it may take an 8.
Each score goes in through `record`, with the time it was heard, and the tracker answers what it did with it.

The rule is Tukey's fence, the standard test for a value that stands out from a set of values:

  * The window: the scores heard in the last `window_seconds` (300, the last five minutes), or the last
    `min_scores` scores if the room is so quiet that fewer than that were heard in the time. So the window is
    the larger of the two. It is a length of time, so a busy room and a quiet one both get "the last five
    minutes"; the count is only a floor under it, so the quartiles are never drawn from a handful of scores.
  * The quartiles: lined up from lowest to highest, Q1 is the score a quarter of the way up the window's
    scores and Q3 three quarters of the way up. The IQR (interquartile range) is Q3 minus Q1, the width of the
    middle half of the scores.
  * The bar: Q3 + `fence_multiple` x IQR. 0 puts the bar at Q3, so about the top quarter of scores clear it;
    1.5 is the textbook outlier line, and 1.0 (to begin with) is a bar a lively room reaches rarely.
  * The trigger: a score triggers when it is ABOVE the bar, once the window holds `min_scores` scores. The
    score is compared with the bar as it stood BEFORE that score joined the window, so a sentence is judged
    against the room as it was before it was said. A triggering score then joins the window like any other.
  * The cooldown: after a trigger, a score above the bar is not marked until `cooldown_seconds` have passed
    since that trigger (60 to begin with; 0 turns it off). It is COOLING_DOWN: it still joins the window, and it
    does not restart the cooldown. A burst of strong sentences, such as a run of jokes, gives one trigger and
    not six, and the next one comes from the room once the cooldown is over.

Every score that is a number joins the window, the lowest included: nothing is thrown away by a fixed line,
so the fence works from what the room really scored. A fragment that has no score (None) is IGNORED and
stays out of everything here.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A bad setting is replaced by its default and
    recorded in `report`; bad input to `record` is simply ignored. `strict=True` raises
    ConvoInterestTrackerError instead, for tests and dev tools.
  * The caller passes the time. Live it is a clock reading; replaying a recording it is the place in the
    recording, which is why a recording can be replayed faster than real time and give the same answers. If a
    time ever goes backwards (a clock stepping back) it is held at the latest time seen, so the window cannot
    be reordered.
  * Only the scores in the window are kept, so the memory is bounded however long the room talks.
  * This module never reads Config, and it never sees any words, only numbers.
"""
import math
import statistics
from collections import deque
from enum import Enum
from typing import Deque, List, Optional, Tuple

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_FENCE_MULTIPLE = 1.0
DEFAULT_WINDOW_SECONDS = 300.0
DEFAULT_MIN_SCORES = 20
DEFAULT_COOLDOWN_SECONDS = 60.0
MINIMUM_MIN_SCORES = 2                 # quartiles need at least two scores


class ConvoInterestTrackerError(ValueError):
    """Raised for any tracker problem, but only in strict mode (development and tests)."""


class ScoreEffect(Enum):
    """What the tracker did with a score."""
    IGNORED = "ignored"        # no score, not a number, or no usable time
    COUNTED = "counted"        # joined the window
    TRIGGERED = "triggered"    # stood above the bar (and joined the window too)
    COOLING_DOWN = "cooling down"    # stood above the bar, but a trigger was too recent (joined the window too)


class ConvoInterestTracker:
    """Keeps the room's recent scores and decides whether a new one stands out from them.

    Attributes:
        report: Problems met (a bad setting replaced by its default).
        counted: How many scores have counted so far.
    """

    def __init__(self, fence_multiple: float = DEFAULT_FENCE_MULTIPLE, window_seconds: float = DEFAULT_WINDOW_SECONDS,
                 min_scores: int = DEFAULT_MIN_SCORES, cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
                 strict: bool = False):
        """Args:
            fence_multiple: How many IQRs above Q3 a score must be to trigger, 0 or more.
            window_seconds: How many seconds of recent scores the quartiles are drawn from, above 0.
            min_scores: The window always holds at least this many scores, and nothing triggers until it does;
                a whole number of at least 2.
            cooldown_seconds: How many seconds after a trigger before another score may trigger, 0 or more
                (0 means no cooldown).
            strict: Raise ConvoInterestTrackerError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=ConvoInterestTrackerError, logger=_log)
        self._fence_multiple = self._valid_fence_multiple(fence_multiple)
        self._window_seconds = self._valid_window_seconds(window_seconds)
        self._min_scores = self._valid_min_scores(min_scores)
        self._cooldown_seconds = self._valid_cooldown_seconds(cooldown_seconds)
        self._recent: Deque[Tuple[float, float]] = deque()      # (time heard, score), oldest first
        self._last_time: Optional[float] = None
        self._last_trigger: Optional[float] = None
        self._counted = 0

    # --- Settings -------------------------------------------------------------------

    def _valid_fence_multiple(self, value) -> float:
        """A number of 0 or more, or the default with a warning."""
        if self._is_number(value) and value >= 0.0:
            return float(value)
        self.report.warn(f"fence_multiple must be a number of at least 0, got {value!r}; "
                         f"using {DEFAULT_FENCE_MULTIPLE:g}")
        return DEFAULT_FENCE_MULTIPLE

    def _valid_window_seconds(self, value) -> float:
        """A number above 0, or the default with a warning."""
        if self._is_number(value) and value > 0.0:
            return float(value)
        self.report.warn(f"window_seconds must be a number above 0, got {value!r}; "
                         f"using {DEFAULT_WINDOW_SECONDS:g}")
        return DEFAULT_WINDOW_SECONDS

    def _valid_min_scores(self, value) -> int:
        """A whole number of at least 2, or the default with a warning."""
        if isinstance(value, int) and not isinstance(value, bool) and value >= MINIMUM_MIN_SCORES:
            return value
        self.report.warn(f"min_scores must be a whole number of at least {MINIMUM_MIN_SCORES}, got {value!r}; "
                         f"using {DEFAULT_MIN_SCORES}")
        return DEFAULT_MIN_SCORES

    def _valid_cooldown_seconds(self, value) -> float:
        """A number of 0 or more, or the default with a warning."""
        if self._is_number(value) and value >= 0.0:
            return float(value)
        self.report.warn(f"cooldown_seconds must be a number of at least 0, got {value!r}; "
                         f"using {DEFAULT_COOLDOWN_SECONDS:g}")
        return DEFAULT_COOLDOWN_SECONDS

    @staticmethod
    def _is_number(value) -> bool:
        """True for a real int or float (not a bool, and not NaN or infinity)."""
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    # --- Questions ------------------------------------------------------------------

    @property
    def counted(self) -> int:
        """How many scores have counted so far."""
        return self._counted

    def window_size_at(self, now: float) -> int:
        """How many scores the window holds at time `now`. While fewer than `min_scores` are held at all (the
        warm-up), that is all of them."""
        window = self._window(self._steady(now)) if self._is_number(now) else None
        return len(window) if window is not None else len(self._recent)

    def trigger_level_at(self, now: float) -> Optional[float]:
        """The score a new score must be above to trigger at time `now`, or None while the window holds fewer
        than `min_scores` scores (or `now` is not a number)."""
        window = self._window(self._steady(now)) if self._is_number(now) else None
        if window is None:
            return None
        q1, _, q3 = statistics.quantiles(window, n=4, method="inclusive")
        return q3 + self._fence_multiple * (q3 - q1)

    def _steady(self, now: float) -> float:
        """`now`, held at the latest time seen so a clock stepping back cannot reorder the window."""
        return now if self._last_time is None or now >= self._last_time else self._last_time

    def _window(self, now: float) -> Optional[List[float]]:
        """The scores in the window at `now`, or None if fewer than `min_scores` are held at all."""
        if len(self._recent) < self._min_scores:
            return None
        cutoff = now - self._window_seconds
        inside = [score for heard, score in self._recent if heard >= cutoff]
        if len(inside) >= self._min_scores:
            return inside
        return [score for _, score in list(self._recent)[-self._min_scores:]]      # a quiet room: the last few

    # --- Taking in scores -----------------------------------------------------------

    def record(self, score: Optional[float], now: float) -> ScoreEffect:
        """Takes in one score heard at time `now` (seconds) and says what happened to it.

        A score that is missing or not a number, or a time that is not a number, is IGNORED and changes nothing.
        Otherwise the score is compared with the bar as it stands now, then joins the window, whatever happens
        to it. It is TRIGGERED if it was above the bar and at least `cooldown_seconds` have passed since the last
        trigger; COOLING_DOWN if it was above the bar but that trigger was too recent; COUNTED otherwise,
        including when the window held too few scores.
        """
        if not (self._is_number(score) and self._is_number(now)):
            return ScoreEffect.IGNORED
        now = self._steady(now)
        level = self.trigger_level_at(now)
        above = level is not None and score > level
        cooling = above and self._last_trigger is not None and now - self._last_trigger < self._cooldown_seconds
        triggered = above and not cooling
        if triggered:
            self._last_trigger = now
        self._recent.append((now, float(score)))
        self._last_time = now
        self._counted += 1
        self._forget_old(now)
        effect = ScoreEffect.TRIGGERED if triggered else ScoreEffect.COOLING_DOWN if cooling else ScoreEffect.COUNTED
        _log.debug("score %s: bar %s, %d counted", effect.value, "none yet" if level is None else f"{level:.3f}",
                   self._counted)
        return effect

    def _forget_old(self, now: float) -> None:
        """Drops scores older than the window, but never below `min_scores` held, so a quiet room keeps its floor."""
        cutoff = now - self._window_seconds
        while len(self._recent) > self._min_scores and self._recent[0][0] < cutoff:
            self._recent.popleft()
