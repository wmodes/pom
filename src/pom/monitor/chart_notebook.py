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
  * A SILENCE IS DRAWN AS ITS FIRST MINUTES, A CUT, AND ITS LAST MINUTES, so only the first and the last `edge_seconds` of it (the `silence_shape`) are ever on the chart
    (see `silence_compressor.SilenceCompressor`). The rule for which ticks that gives is the TickWriter's (`tick_writer.py`); this keeps
    the list and the lock, and hands it where the stretch begins. When the previous attempt is more than the shape's `longest_seconds` back, the ticks of
    the stretch between are not written. Only the first `edge_seconds` after that attempt are, and they were worked out when that attempt was heard (`head_ticks()` holds them
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
  * Nothing here may stop the oracle (see pom/__init__.py). Every value handed to `add` and `ticks_up_to` is checked by the notebook's
    ValueChecker (Design Audit 04 item 3), with a warning for a bad one: a time below 0 is 0 and one that is not a number is the stream
    time now (`stream_now`); a score below 0 is 0; a bar that is not a number is None (no bar yet). An attempt whose score is not a number
    is not kept, and `add` returns False. A bad setting is replaced by its default; a ticks source that fails is a warning and no ticks.
    `strict=True` raises ChartNotebookError instead, for tests and dev tools.
  * This module never reads Config, and never imports the bar: the Printer hands it a function.
"""
import threading
from collections import deque
from typing import Callable, Deque, List, Optional

from pom.bookkeeper.bookkeeper import Bookkeeper
from pom.defaults import ChartSettings, MonitorSettings
from pom.monitor.notebook_entry import ATTEMPT, TICK, NotebookEntry
from pom.monitor.silence_compressor import SilenceCompressor, SilenceShape
from pom.monitor.tick_writer import TickWriter
from pom.value_checker import ValueChecker

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module

# Only the fallbacks for a missing or bad setting (with a warning); the real values are `[monitor] history_seconds`, `[monitor] chart_notebook_max_entries`
# and `[chart] longest_silence_minutes` in config/pom.toml.
DEFAULT_SILENCE_SHAPE = SilenceShape(300.0)            # a silence over 300 s, drawn as nothing but its cut: so no ticks are written for one


class ChartNotebookError(ValueError):
    """Raised for any notebook problem, but only in strict mode (development and tests)."""


class ChartNotebook:
    """Keeps the recent attempts and ticks as numbers, oldest first.

    Attributes:
        logger: Where this class says what it has to say (a LogRecorder from the Bookkeeper).
        chart, monitor: The `[chart]` and `[monitor]` settings it was handed (it reads `window_minutes` and `chart_notebook_max_entries`).
    """

    def __init__(self, chart: ChartSettings, monitor: MonitorSettings, strict: bool = False,
                 ticks_source: Optional[Callable[[float, float], list]] = None, silence_shape: Optional[SilenceShape] = None,
                 began_at: Optional[float] = None, bookkeeper: Optional[Bookkeeper] = None, stream_now: Optional[Callable[[], float]] = None):
        """Args:
            chart: The `[chart]` section: `window_minutes`, how much drawn time is kept (the chart's own width, since nothing older is ever drawn).
            monitor: The `[monitor]` section: `chart_notebook_max_entries`, the most entries kept; the oldest go first.
            strict: Raise ChartNotebookError on any problem instead of logging it.
            bookkeeper: The Bookkeeper the entry point built; it hands this class its logger. None (a test or a tool) gives a plain one.
            ticks_source: A function from (start, end) to the ticks in that stretch, each as (time, anchor score, the bar at that tick), oldest
                first (the bar's `ticks_between`). Without one, no ticks are written.
            silence_shape: How the chart draws a silence (`score_chart.silence_shape(look)`): a stretch between two attempts longer than its `longest_seconds`
                is one, and only its first and last `edge_seconds` have ticks. None means the default shape, which has no true-scale sides, so none.
            began_at: The stream time at which listening began, so the stretch before the first attempt gets its ticks too. None means it does not.
            stream_now: The stream time now (the Printer's Timekeeper's `now`): the fallback value for a time that is not a number. None (a
                test or a tool) gives a Timekeeper of its own, which runs on real seconds from now.
        """
        self.logger = (bookkeeper or Bookkeeper()).logger_for(__name__, ChartNotebookError, DEBUG, strict)
        self.check = ValueChecker(self.logger, "ChartNotebook", stream_now)
        self.chart, self.monitor = chart, monitor
        if silence_shape is None:
            silence_shape = DEFAULT_SILENCE_SHAPE
        elif not isinstance(silence_shape, SilenceShape):
            self.logger.warning("silence_shape must be a SilenceShape, got %s; using the default", type(silence_shape).__name__)
            silence_shape = DEFAULT_SILENCE_SHAPE
        self._head: List[NotebookEntry] = []                    # the ticks for the first minutes after the newest attempt, worked out when it was heard
        self._entries: Deque[NotebookEntry] = deque(maxlen=monitor.chart_notebook_max_entries)
        self._tick_writer = TickWriter(ticks_source, silence_shape, began_at, self.logger, stream_now)
        self._lock = threading.Lock()
        self._total_added = 0

    def add(self, score: float, bar: Optional[float], at: float, qualified: bool = False) -> bool:
        """Adds an attempt, after the ticks since the previous one. True if it was kept; False (the log says why) if the score was not a number."""
        score = self.check.score(score, method="add", may_be_unscored=False)
        if score is None:
            self.logger.info("so that attempt was not kept")                  # the warning, just above, said why
            return False
        at = self.check.stream_time(at, method="add", argument="at")
        bar = self.check.bar(bar, method="add")
        attempt = NotebookEntry(at, score, bar, bool(qualified), ATTEMPT)
        ticks = self._stretch_ticks(attempt.at)                   # asked for outside the lock: the bar has its own
        head = self._tick_writer.first_minutes(attempt.at)
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
        return self.ticks() + self._stretch_ticks(self.check.stream_time(now, method="ticks_up_to", argument="now"))

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

    @property
    def earliest_at(self) -> Optional[float]:
        """The audio time of the oldest attempt held, or None if there are none: how far back the chart can reach, so a turn whose qualifier dot is
        still on the chart can be kept."""
        with self._lock:
            for entry in self._entries:
                if entry.kind == ATTEMPT:
                    return entry.at
        return None

    def _latest_attempt_at(self) -> Optional[float]:
        """The audio time of the newest attempt. The caller holds the lock."""
        for entry in reversed(self._entries):
            if entry.kind == ATTEMPT:
                return entry.at
        return None

    def _stretch_ticks(self, until: float) -> List[NotebookEntry]:
        """The ticks the chart draws between the newest attempt (or the stream's start, before the first) and `until`: the TickWriter's rule, given
        where the stretch begins and the first minutes worked out when that attempt was heard."""
        if self._tick_writer.ticks_source is None:                 # nothing to ask for, so no need to take the lock
            return []
        with self._lock:
            since = self._latest_attempt_at()
            held = list(self._head)
        return self._tick_writer.stretch(since, held, until)

    def _forget_old(self) -> None:
        """Drops entries older than the age limit in drawn time, measured back from the newest. The caller holds the lock."""
        entries = self._entries
        shape = self._tick_writer.shape
        began = self._tick_writer.began_at if self._total_added == len(entries) else None   # the quiet before the first attempt is a silence, if nothing before it was forgotten
        squeeze = SilenceCompressor([entry.at for entry in entries if entry.kind == ATTEMPT], entries[-1].at, shape, began)
        cutoff = squeeze.drawn(entries[-1].at) - self.chart.window_minutes * 60.0
        first = 0
        while first < len(entries) and squeeze.drawn(entries[first].at) < cutoff:
            first += 1
        # the newest attempt before the cutoff stays if the silence after it reaches into the chart: the chart needs it to draw the break
        before = [i for i in range(first) if entries[i].kind == ATTEMPT]
        if before and first < len(entries):
            following = next((entry.at for entry in list(entries)[first:] if entry.kind == ATTEMPT), None)
            if following is not None and following - entries[before[-1]].at > shape.longest_seconds:
                first = before[-1]
        for _ in range(first):
            entries.popleft()
