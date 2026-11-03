"""The KeyboardWatcher: lines typed at the terminal's keyboard in, attempts out.

It is the typed-line twin of the HingeFinder. Someone can type a line on the typewriter's keyboard, and the Printer answers it like
something said in the room: the TerminalPort hands over each finished line (the C Rtn key), the TypedLineScorer scores it and has the
BarKeeper rule on it, and `watch()` yields the Attempt. The one that qualified (`is_hinge`) is the hinge, exactly as for the room.

Design decisions, and why:
  * `watch()` is a plain generator, like the HingeFinder's `find()`, so this module needs no threads of its own. It blocks while it waits
    for keys. The Conductor runs it in a thread of its own, beside the HingeFinder's.
  * The same two calls as the Eavesdropper, so the Conductor treats both ears alike: `pause()` when a turn takes the floor and
    `skip_ahead()` when it yields it. While paused, a finished line is read (so the terminal's buffer is kept empty) but thrown away
    unscored: nothing is sent to the scorer model during a turn, and a line typed over a portent is not an answer to it. A line typed during a
    turn is counted in `dropped_lines`, never kept. Both calls only set a flag and return at once, from any thread.
  * An empty line (C Rtn alone) is not an attempt and is skipped.
  * NOTHING HERE MAY STOP THE ORACLE (see pom/__init__.py). A terminal that raises, or a scorer that raises, is a warning in `report`
    naming the error type, a short pause, and the watcher goes on listening; only `close()` ends `watch()`. A terminal that cannot be
    opened makes `read_line` return None at once, and the TerminalPort itself pauses between tries, so there is no hot loop here.
    `strict=True` raises KeyboardWatcherError instead, for tests and dev tools.
  * What is typed is room content: it is never logged or put in a report. Only counts are.
  * The parts are passed in, already built; this module never reads Config.
"""
import math
import threading
import time
from typing import Callable, Iterator, Optional

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger
from pom.qualifying.attempt import Attempt

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_POLL_SECONDS = 1.0           # how long one look at the keyboard waits; it is also how soon `close()` takes effect
DEFAULT_ERROR_PAUSE_SECONDS = 1.0    # the pause after an error, so a broken part cannot make a hot loop


class KeyboardWatcherError(ValueError):
    """Raised for any KeyboardWatcher problem, but only in strict mode (development and tests)."""


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class KeyboardWatcher:
    """Turns the lines typed at the terminal into attempts.

    Attributes:
        report: Problems met (a terminal or a scorer that raised, a bad setting). Never holds what was typed.
        dropped_lines: How many finished lines arrived during a turn and were thrown away unscored.
    """

    def __init__(self, terminal, typed_scorer, poll_seconds: float = DEFAULT_POLL_SECONDS,
                 error_pause_seconds: float = DEFAULT_ERROR_PAUSE_SECONDS, sleep: Optional[Callable[[float], None]] = None,
                 strict: bool = False):
        """Args:
            terminal: Anything with read_line(timeout) returning a finished line or None (a TerminalPort).
            typed_scorer: A TypedLineScorer (anything with score(line) returning an Attempt).
            poll_seconds: How long one look at the keyboard waits for a line, above 0.
            error_pause_seconds: How long to pause after an error, 0 or more.
            sleep: A function that waits that many seconds. Default: time.sleep.
            strict: Raise KeyboardWatcherError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=KeyboardWatcherError, logger=_log)
        self._terminal, self._typed_scorer = terminal, typed_scorer
        self._poll = self._valid(poll_seconds, "poll_seconds", DEFAULT_POLL_SECONDS, lambda v: v > 0, "above 0")
        self._error_pause = self._valid(error_pause_seconds, "error_pause_seconds", DEFAULT_ERROR_PAUSE_SECONDS, lambda v: v >= 0,
                                        "at least 0")
        self._sleep = sleep if callable(sleep) else time.sleep
        if sleep is not None and not callable(sleep):
            self.report.warn(f"sleep must be a function, got {type(sleep).__name__}; using time.sleep")
        self._paused = threading.Event()
        self._closed = threading.Event()
        self.dropped_lines = 0

    def _valid(self, value, name: str, default: float, is_good, wanted: str) -> float:
        """A number that passes `is_good`, or the default with a warning."""
        if _is_number(value) and is_good(value):
            return float(value)
        self.report.warn(f"{name} must be a number {wanted}, got {value!r}; using {default:g}")
        return default

    @property
    def paused(self) -> bool:
        """True from `pause()` until `skip_ahead()`: finished lines are being thrown away unscored."""
        return self._paused.is_set()

    def pause(self) -> None:
        """Stops answering typed lines (for a turn). Returns at once; safe from any thread and safe to repeat."""
        self._paused.set()

    def skip_ahead(self) -> None:
        """Ends a pause: lines typed from now on are answered again. Returns at once; safe from any thread."""
        self._paused.clear()

    def close(self) -> None:
        """Ends `watch()` within one poll. Safe to call more than once, from any thread."""
        self._closed.set()

    def watch(self) -> Iterator[Attempt]:
        """Yields an Attempt for each line typed, as it is scored. Blocks while waiting for keys. Ends only when `close` is called."""
        while not self._closed.is_set():
            try:
                line = self._terminal.read_line(self._poll)
            except Exception as problem:             # nothing may stop the oracle
                self.report.warn(f"the keyboard could not be read ({type(problem).__name__}); trying again")
                self._sleep(self._error_pause)
                continue
            if line is None or not isinstance(line, str):
                continue
            if self._paused.is_set():
                self.dropped_lines += 1
                _log.info("keyboard: a line typed during a turn was not answered (%d so far)", self.dropped_lines)
                continue
            if not line.strip():
                continue                             # C Rtn alone
            try:
                attempt = self._typed_scorer.score(line)
            except Exception as problem:             # nothing may stop the oracle
                self.report.warn(f"a typed line could not be scored ({type(problem).__name__}); it was dropped")
                self._sleep(self._error_pause)
                continue
            yield attempt

    def report_lines(self) -> list:
        """The problems met by the watcher and by the typed-line scorer under it, as lines to show."""
        under = getattr(self._typed_scorer, "report", None)
        return [*self.report.lines(), *(under.lines() if under is not None else [])]
