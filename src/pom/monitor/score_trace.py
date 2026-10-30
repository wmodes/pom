"""The ScoreTrace: every scored sentence's score and the bar it was judged against, for the chart on the monitoring site.

The TriggerHistory keeps the words of the sentences that triggered. This keeps something lighter for all the others: just numbers, one
point per scored sentence (when it was heard, its score, the bar it had to beat, and whether it triggered), so the page can draw the
room's scores and the bar riding above them. No words from the room are kept here, so it is safe to show even when hinges are hidden.

Design decisions, and why:
  * NUMBERS ONLY, MEMORY ONLY. A point is four numbers. Nothing is written to disk or logged, and a restart empties it.
  * The time of a point is the place in the audio (seconds from the start of listening), not the wall clock, so a recording replayed
    faster than real time draws exactly as a live run would. Retention is measured the same way: points older than `max_age_seconds`
    before the newest point are dropped, so no clock is needed.
  * Bounded twice, by age and by `max_points`, so a flood of sentences cannot grow the memory.
  * Thread-safe. The pipeline adds from its thread and the web server reads from its own, so every change and read takes one lock, and
    `recent()` returns a copy.
  * Nothing here may stop the oracle (see pom/__init__.py). A point that cannot be made (a score or time that is not a number) is
    recorded as a warning without any value and `add` returns False; a bad setting is replaced by its default. `strict=True` raises
    ScoreTraceError instead, for tests and dev tools.
  * This module never reads Config.
"""
import math
import threading
from collections import deque
from dataclasses import dataclass
from typing import Deque, List, Optional

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_MAX_AGE_SECONDS = 3600.0
DEFAULT_MAX_POINTS = 5000


class ScoreTraceError(ValueError):
    """Raised for any trace problem, but only in strict mode (development and tests)."""


@dataclass(frozen=True, slots=True)
class TracePoint:
    """One scored sentence, as numbers.

    Attributes:
        at: Where in the audio stream the sentence ended, in seconds from the start of listening.
        score: Its score, 0 to 9.
        bar: The score it had to be above, as the bar stood before it joined (None if there was none).
        triggered: True if it triggered.
    """
    at: float
    score: float
    bar: Optional[float]
    triggered: bool


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class ScoreTrace:
    """Keeps the recent scored sentences as numbers, oldest first.

    Attributes:
        report: Problems met (a bad setting, a point that could not be made). Never holds a value from the room.
    """

    def __init__(self, max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS, max_points: int = DEFAULT_MAX_POINTS, strict: bool = False):
        """Args:
            max_age_seconds: How much audio time is kept, above 0 (3600 is an hour).
            max_points: The most points kept, a whole number of at least 1; the oldest go first.
            strict: Raise ScoreTraceError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=ScoreTraceError, logger=_log)
        if not (_is_number(max_age_seconds) and max_age_seconds > 0):
            self.report.warn(f"max_age_seconds must be a number above 0, got {max_age_seconds!r}; using {DEFAULT_MAX_AGE_SECONDS:g}")
            max_age_seconds = DEFAULT_MAX_AGE_SECONDS
        if not (isinstance(max_points, int) and not isinstance(max_points, bool) and max_points >= 1):
            self.report.warn(f"max_points must be a whole number of at least 1, got {max_points!r}; using {DEFAULT_MAX_POINTS}")
            max_points = DEFAULT_MAX_POINTS
        self._max_age = float(max_age_seconds)
        self._points: Deque[TracePoint] = deque(maxlen=max_points)
        self._lock = threading.Lock()

    def add(self, score: float, bar: Optional[float], at: float, triggered: bool = False) -> bool:
        """Adds a scored sentence. True if it was kept; False (see `report`) if the score or the time was not a number."""
        if not (_is_number(score) and _is_number(at)):
            self.report.warn("a point was given without a numeric score and time, so it was not kept")
            return False
        point = TracePoint(float(at), float(score), float(bar) if _is_number(bar) else None, bool(triggered))
        with self._lock:
            self._points.append(point)
            self._forget_old()
        return True

    def recent(self, seconds: Optional[float] = None) -> List[TracePoint]:
        """The points, oldest first, as a copy. With `seconds`, only those within that much audio time of the newest point."""
        with self._lock:
            points = list(self._points)
        if seconds is not None and points:
            cutoff = points[-1].at - seconds
            points = [point for point in points if point.at >= cutoff]
        return points

    @property
    def latest_at(self) -> Optional[float]:
        """The audio time of the newest point, or None if there are none."""
        with self._lock:
            return self._points[-1].at if self._points else None

    def _forget_old(self) -> None:
        """Drops points older than the age limit, measured back from the newest. The caller holds the lock."""
        cutoff = self._points[-1].at - self._max_age
        while self._points and self._points[0].at < cutoff:
            self._points.popleft()
