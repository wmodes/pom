"""The ChartNotebook: what the chart keeps so it can draw itself later: every attempt's score and the bar it was judged against, and every tick of the anchor.

The TurnHistory keeps the words of the hinges (the attempts that qualified). This keeps something lighter for all the others: just numbers, one
entry per attempt (when it was heard, its score, the bar it had to beat, and whether it qualified) and one per tick (when it fell, the anchor's score,
and the bar as it stood at that tick), so the page can draw the room's scores and the bar riding above them. No words from the room are kept here,
so it is safe to show even when hinges are hidden.

Design decisions, and why:
  * ONE LIST, TWO KINDS. An attempt and a tick carry the same facts (when, a score, the bar then), so both are a NotebookEntry with a `kind`
    ("attempt" or "tick"), in time order, under one age rule and one size limit. `recent()` gives the attempts, `ticks()` the ticks.
  * THE TICKS ARE WRITTEN WHEN THE NEXT ATTEMPT ARRIVES. A tick falls on the clock whether or not anyone speaks, but the bar's height at that
    tick can only be worked out from the scores the bar still holds (`discard_scores_older_than_minutes`), which are all there at the moment the
    next attempt is heard and gone after a long silence. So `add` asks the bar's `ticks_source` for the ticks since the previous attempt (or since the
    stream began, before the first one) and writes them down first. The ticks since the newest attempt are not here yet: the page works those out live.
  * A SILENCE IS DRAWN AS ITS FIRST MINUTES, A CUT, AND ITS LAST MINUTES, so only the first and the last `head_seconds` of it are ever on the chart
    (see `score_chart.SilenceCompressor`). When the previous attempt is more than `longest_silence_seconds` back, the ticks of the stretch between are not
    written. Only the first `head_seconds` after that attempt are, and they were worked out when that attempt was heard (`head_ticks()` holds them
    until the next one arrives): by the time the next attempt comes the bar may have forgotten the scores they depend on, but at the moment an attempt is
    heard the bar holds them, and a tick's height depends only on scores heard before it, so they are exact. The last minutes of the silence are written
    when the attempt that ends it is heard, and while it is still going `ticks_up_to` works them out fresh for the page. Nothing for the cut is
    written at all: a room quiet for 15 hours would be 900 ticks, none of them on the chart.
  * NUMBERS ONLY, MEMORY ONLY. An entry is a few numbers. Nothing is written to disk or logged, and a restart empties it.
  * The time of an entry is the place in the audio (seconds from the start of listening), not the wall clock, so a recording replayed
    faster than real time draws exactly as a live run would. Retention is measured the same way, and in DRAWN time: entries older than
    `max_age_seconds` (the chart's own width) before the newest are dropped when a new one is added, with a silence counted as the width the chart
    draws it however long it was. So the notebook never holds much more than the chart can show, a long silence does not push the history before it
    out, and no clock is needed; in a long silence nothing is dropped, because nothing is added. The newest attempt before the cutoff stays if a
    silence after it reaches into the chart, so the chart can still draw the break.
  * Bounded twice, by age and by `max_entries`, so a flood of attempts cannot grow the memory.
  * Thread-safe. The pipeline adds from its thread and the web server reads from its own, so every change and read takes one lock, and
    `recent()` and `ticks()` return copies. The bar is asked for its ticks outside the lock.
  * Nothing here may stop the oracle (see pom/__init__.py). An entry that cannot be made (a score or time that is not a number) is
    recorded as a warning without any value and `add` returns False; a bad setting is replaced by its default; a ticks source that fails is a
    warning and no ticks. `strict=True` raises ChartNotebookError instead, for tests and dev tools.
  * This module never reads Config, and never imports the bar: the Printer hands it a function.
"""
import math
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, List, Optional

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger
from pom.monitor.silence_compressor import SilenceCompressor

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

# Only the fallbacks for a missing or bad setting (with a warning); the real values are `[monitor] history_seconds`, `[monitor] chart_notebook_max_entries`
# and `[chart] longest_silence_minutes` in config/pom.toml.
DEFAULT_MAX_AGE_SECONDS = 3600.0
DEFAULT_MAX_ENTRIES = 5000
DEFAULT_LONGEST_SILENCE_SECONDS = 300.0
DEFAULT_HEAD_SECONDS = 0.0

ATTEMPT = "attempt"
TICK = "tick"


class ChartNotebookError(ValueError):
    """Raised for any notebook problem, but only in strict mode (development and tests)."""


@dataclass(frozen=True, slots=True)
class NotebookEntry:
    """One attempt or one tick, as numbers.

    Attributes:
        at: Where in the audio stream it happened, in seconds from the start of listening (for an attempt, where it ended).
        score: An attempt's score, 0 to 9; a tick's is the anchor's score.
        bar: The bar at that moment: for an attempt the score it had to be above, as the bar stood before it joined; for a tick the bar as it
            stood at that tick (None if there was none).
        qualified: True if an attempt qualified (always False for a tick).
        kind: "attempt" (the default) or "tick".
    """
    at: float
    score: float
    bar: Optional[float]
    qualified: bool
    kind: str = ATTEMPT


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class ChartNotebook:
    """Keeps the recent attempts and ticks as numbers, oldest first.

    Attributes:
        report: Problems met (a bad setting, an entry that could not be made). Never holds a value from the room.
    """

    def __init__(self, max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS, max_entries: int = DEFAULT_MAX_ENTRIES, strict: bool = False,
                 ticks_source: Optional[Callable[[float, float], list]] = None, longest_silence_seconds: float = DEFAULT_LONGEST_SILENCE_SECONDS,
                 began_at: Optional[float] = None, head_seconds: float = DEFAULT_HEAD_SECONDS):
        """Args:
            max_age_seconds: How much drawn time is kept, above 0: the chart's own width (`[chart] window_minutes`), since nothing older is ever drawn.
            max_entries: The most entries kept, a whole number of at least 1; the oldest go first.
            strict: Raise ChartNotebookError on any problem instead of recording it.
            ticks_source: A function from (start, end) to the ticks in that stretch, each as (time, anchor score, the bar at that tick), oldest
                first (the bar's `ticks_between`). Without one, no ticks are written.
            longest_silence_seconds: A stretch between two attempts longer than this is a silence on the chart, and no ticks are written for it. Above 0.
            began_at: The stream time at which listening began, so the stretch before the first attempt gets its ticks too. None means it does not.
            head_seconds: How much of the start of a silence the chart draws at true scale (`score_chart.silence_edge_seconds`): those ticks are written
                for it, and nothing after them. 0, the default, writes none.
        """
        self.report = FaultRecorder(strict=strict, error=ChartNotebookError, logger=_log)
        if not (_is_number(max_age_seconds) and max_age_seconds > 0):
            self.report.warn(f"max_age_seconds must be a number above 0, got {max_age_seconds!r}; using {DEFAULT_MAX_AGE_SECONDS:g}")
            max_age_seconds = DEFAULT_MAX_AGE_SECONDS
        if not (isinstance(max_entries, int) and not isinstance(max_entries, bool) and max_entries >= 1):
            self.report.warn(f"max_entries must be a whole number of at least 1, got {max_entries!r}; using {DEFAULT_MAX_ENTRIES}")
            max_entries = DEFAULT_MAX_ENTRIES
        if not (_is_number(longest_silence_seconds) and longest_silence_seconds > 0):
            self.report.warn(f"longest_silence_seconds must be a number above 0, got {longest_silence_seconds!r}; using {DEFAULT_LONGEST_SILENCE_SECONDS:g}")
            longest_silence_seconds = DEFAULT_LONGEST_SILENCE_SECONDS
        if not (_is_number(head_seconds) and head_seconds >= 0):
            self.report.warn(f"head_seconds must be a number of at least 0, got {head_seconds!r}; using {DEFAULT_HEAD_SECONDS:g}")
            head_seconds = DEFAULT_HEAD_SECONDS
        self._head_seconds = float(head_seconds)
        self._head: List[NotebookEntry] = []                    # the ticks for the first minutes after the newest attempt, worked out when it was heard
        self._max_age = float(max_age_seconds)
        self._entries: Deque[NotebookEntry] = deque(maxlen=max_entries)
        self._ticks_source = ticks_source
        self._longest_silence = float(longest_silence_seconds)
        self._began_at = float(began_at) if _is_number(began_at) else None
        self._lock = threading.Lock()
        self._total_added = 0

    def add(self, score: float, bar: Optional[float], at: float, qualified: bool = False) -> bool:
        """Adds an attempt, after the ticks since the previous one. True if it was kept; False (see `report`) if the score or the time was not a number."""
        if not (_is_number(score) and _is_number(at)):
            self.report.warn("an attempt was given without a numeric score and time, so it was not kept")
            return False
        attempt = NotebookEntry(float(at), float(score), float(bar) if _is_number(bar) else None, bool(qualified), ATTEMPT)
        ticks = self._ticks_before(attempt.at)                    # asked for outside the lock: the bar has its own
        head = self._tick_entries(attempt.at, attempt.at + self._head_seconds) if self._head_seconds > 0 else []
        with self._lock:
            for tick in ticks:
                self._entries.append(tick)
            self._entries.append(attempt)
            self._head = head
            self._total_added += len(ticks) + 1
            self._forget_old()
        return True

    def recent(self, seconds: Optional[float] = None) -> List[NotebookEntry]:
        """The attempts, oldest first, as a copy. With `seconds`, only those within that much audio time of the newest attempt."""
        with self._lock:
            attempts = [entry for entry in self._entries if entry.kind == ATTEMPT]
        if seconds is not None and attempts:
            cutoff = attempts[-1].at - seconds
            attempts = [entry for entry in attempts if entry.at >= cutoff]
        return attempts

    def ticks(self) -> List[NotebookEntry]:
        """The ticks written down, oldest first, as a copy. The ones since the newest attempt are not here yet (see the module note)."""
        with self._lock:
            return [entry for entry in self._entries if entry.kind == TICK]

    def ticks_up_to(self, now: float) -> List[NotebookEntry]:
        """Every tick the chart draws, oldest first, as of `now`: the ones written down, then the first minutes of a silence that has begun since, then
        the last minutes before `now`, worked out fresh each time (they are not written: an attempt heard a moment ago may not be in the bar yet, and the
        next attempt writes them properly). While the quiet since the newest attempt is not yet a silence, it is all of it."""
        written = self.ticks()
        if self._ticks_source is None or not _is_number(now):
            return written
        with self._lock:
            since = self._latest_attempt_at()
            held = list(self._head)
        began = since is None
        if began:
            since = self._began_at
        if since is None or not now > since:
            return written
        if now - since <= self._longest_silence:
            return written + self._tick_entries(since, now)
        if began:
            held = self._tick_entries(since, since + self._head_seconds) if self._head_seconds > 0 else []
        tail = self._tick_entries(now - self._head_seconds, now) if self._head_seconds > 0 else []
        return written + held + tail

    def head_ticks(self) -> List[NotebookEntry]:
        """The ticks for the first minutes after the newest attempt, as a copy: what the chart draws at the start of a silence that has begun since."""
        with self._lock:
            return list(self._head)

    @property
    def holds_everything(self) -> bool:
        """True while no entry has been forgotten, for age or for room: the notebook then reaches back to the start of the stream."""
        with self._lock:
            return self._total_added == len(self._entries)

    @property
    def latest_at(self) -> Optional[float]:
        """The audio time of the newest attempt, or None if there are none."""
        with self._lock:
            return self._latest_attempt_at()

    def _latest_attempt_at(self) -> Optional[float]:
        """The audio time of the newest attempt. The caller holds the lock."""
        for entry in reversed(self._entries):
            if entry.kind == ATTEMPT:
                return entry.at
        return None

    def _ticks_before(self, at: float) -> List[NotebookEntry]:
        """The ticks to write down before an attempt heard at `at`: those since the previous attempt (or the stream's start), or, if that stretch is a
        silence, only the ones at its start."""
        if self._ticks_source is None:
            return []
        with self._lock:
            since = self._latest_attempt_at()
            held = list(self._head)
        began = since is None
        if began:
            since = self._began_at
        if since is None or not at > since:
            return []
        if at - since <= self._longest_silence:
            return self._tick_entries(since, at)
        if began:                                                  # nothing was heard before it, so no scores to wait for: worked out now, as exactly
            held = self._tick_entries(since, since + self._head_seconds) if self._head_seconds > 0 else []
        tail = self._tick_entries(at - self._head_seconds, at) if self._head_seconds > 0 else []
        return [tick for tick in held if tick.at <= at] + tail      # the chart cuts the rest of the silence out, so nothing is written for it

    def _tick_entries(self, start: float, end: float) -> List[NotebookEntry]:
        """The bar's ticks after `start` and up to `end`, as entries. The start's own tick belongs to whatever was heard then."""
        if self._ticks_source is None:
            return []
        try:
            found = list(self._ticks_source(start, end))
        except Exception:                                          # nothing here may stop the oracle; say what, never a value from the room
            self.report.warn("the ticks source failed, so no ticks were written for this stretch")
            return []
        ticks = []
        for item in found:
            if not (isinstance(item, (tuple, list)) and len(item) == 3 and _is_number(item[0]) and _is_number(item[1])):
                self.report.warn("a tick was given without a numeric time and score, so it was not kept")
                continue
            if start < item[0] <= end:
                ticks.append(NotebookEntry(float(item[0]), float(item[1]), float(item[2]) if _is_number(item[2]) else None, False, TICK))
        return ticks

    def _forget_old(self) -> None:
        """Drops entries older than the age limit in drawn time, measured back from the newest. The caller holds the lock."""
        entries = self._entries
        began = self._began_at if self._total_added == len(entries) else None          # the quiet before the first attempt is a silence, if nothing before it was forgotten
        squeeze = SilenceCompressor([entry.at for entry in entries if entry.kind == ATTEMPT], entries[-1].at, self._longest_silence, began,
                                    self._longest_silence - 2.0 * self._head_seconds)
        cutoff = squeeze.drawn(entries[-1].at) - self._max_age
        first = 0
        while first < len(entries) and squeeze.drawn(entries[first].at) < cutoff:
            first += 1
        # the newest attempt before the cutoff stays if the silence after it reaches into the chart: the chart needs it to draw the break
        before = [i for i in range(first) if entries[i].kind == ATTEMPT]
        if before and first < len(entries):
            following = next((entry.at for entry in list(entries)[first:] if entry.kind == ATTEMPT), None)
            if following is not None and following - entries[before[-1]].at > self._longest_silence:
                first = before[-1]
        for _ in range(first):
            entries.popleft()
