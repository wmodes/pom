"""The LogMuter: lets a message that keeps repeating through a few times, then mutes it, and says so in the log.

A fault that goes on (a microphone that is gone, a port that will not open) is logged again every time it is looked at, and one weekend of that
filled the log with the same line thousands of times and hid everything else. Counting the repeats in each module (the microphone, the terminal,
whisper, the oracle, ...) would mean writing the same counter again and again. This does it once, for every line any module logs.

THE RULE (the three numbers are settings, `logmute_after_repeats`, `logmute_remind_every_repeats` and `logmute_quiet_seconds` in config/pom.toml):
  * Two lines are THE SAME MESSAGE when they come from the same logger at the same level and say the same thing once every run of digits is
    read as `#` (so "not found after 49s" and "not found after 50s" are the same). The digits only decide what counts as the same: a line that
    goes through prints its own real numbers.
  * The first `logmute_after_repeats` lines of a message go through untouched. The last of them has a second line under it, `LOGMUTE: Last
    message repeating and muted`, and after it the message is muted: its lines are dropped.
  * After every `logmute_remind_every_repeats` dropped lines one goes through, with `LOGMUTE: Last message repeated N times so far, still
    muted` under it.
  * A message is quiet when `logmute_quiet_seconds` pass without it (a dropped line counts as it coming, so a message that keeps coming
    stays muted). Its next line goes through as an ordinary line, with `LOGMUTE: Last message repeated N times` under it if lines were dropped,
    and the counting starts again.
  * DEBUG lines are never muted: whoever turned DEBUG on wants all of it.

The LOGMUTE line is written into the same record as the line it is about, so the two can never be separated, even when two threads log at once.
A message that never comes back never gets its closing count; that is the price of attaching the count to the next line.

It is attached to every handler of the `pom` logger (see log_setup.configure_logging). A filter on the logger itself would not see the lines of
the modules' own child loggers. A record that passes through several handlers is decided once; the verdict is kept on the record.

A log filter must never stop the main work: if anything in here fails the line goes through, and one warning (naming the kind of error, never the
text of a line) says so. That warning is written straight to stderr, as Python's own logging reports its own failures (`Handler.handleError`), not
through the log: the log passes through this muter, the part that failed. Under systemd stderr reaches the same journal.
"""
import logging
import re
import sys
import threading
import time
from typing import Callable, Dict, Tuple

TAG = "LOGMUTE"
_DIGITS = re.compile(r"[0-9]+")
_VERDICT = "_logmute_verdict"     # where a record keeps the answer, so a second handler does not count it again


class _Run:
    """What is known about one message: how many lines went through, how many were dropped, when it was last seen."""
    __slots__ = ("passed", "repeats", "last_seen")

    def __init__(self, now: float):
        self.passed = 1          # lines that went through (counting this first one)
        self.repeats = 0         # lines after the muting began, dropped or not
        self.last_seen = now


class LogMuter(logging.Filter):
    """Mutes a message that repeats, and says so under the lines it lets through.

    Args:
        after_repeats: How many lines of one message go through before it is muted, a whole number of at least 1 (validated by the config).
        remind_every_repeats: Once muted, one line in this many goes through as a reminder, a whole number of at least 1 (validated by the config).
        quiet_seconds: How long a message must be silent before it is let through again, above 0 (validated by the config).
        clock: Gives the time in seconds; only differences are used. Injected so a test does not wait.
    """

    def __init__(self, after_repeats: int, remind_every_repeats: int, quiet_seconds: float,
                 clock: Callable[[], float] = time.monotonic):
        super().__init__()
        self._after_repeats = after_repeats
        self._remind_every_repeats = remind_every_repeats
        self._quiet_seconds = quiet_seconds
        self._clock = clock
        self._runs: Dict[Tuple[str, int, str], _Run] = {}
        self._last_sweep = clock()
        self._lock = threading.Lock()
        self._warned = False

    @property
    def tracked(self) -> int:
        """How many different messages are being counted at the moment."""
        with self._lock:
            return len(self._runs)

    def filter(self, record: logging.LogRecord) -> bool:
        """True if the line goes through (with a LOGMUTE line added to it when it has something to say), False if it is dropped."""
        try:
            verdict = getattr(record, _VERDICT, None)
            if verdict is None:
                verdict = self._decide(record)
                setattr(record, _VERDICT, verdict)
            return verdict
        except Exception as error:            # a filter must not stop the main work
            self._warn_once(error)
            return True

    def _decide(self, record: logging.LogRecord) -> bool:
        """Counts the line and answers; adds the LOGMUTE lines to the record when it goes through."""
        if record.levelno < logging.INFO:
            return True
        text = record.getMessage()
        key = (record.name, record.levelno, _DIGITS.sub("#", text))
        notes = []
        with self._lock:
            now = self._clock()
            self._forget_the_quiet(now)
            run = self._runs.get(key)
            if run is not None and now - run.last_seen >= self._quiet_seconds:
                if run.repeats:
                    notes.append(f"Last message repeated {run.repeats} times")
                run = None                                # the muted run is over
            if run is None:
                run = self._runs[key] = _Run(now)
                goes_through = True
                if run.passed == self._after_repeats:
                    notes.append("Last message repeating and muted")
            else:
                run.last_seen = now
                if run.passed < self._after_repeats:
                    run.passed += 1
                    goes_through = True
                    if run.passed == self._after_repeats:
                        notes.append("Last message repeating and muted")
                else:
                    run.repeats += 1
                    goes_through = run.repeats % self._remind_every_repeats == 0
                    if goes_through:
                        notes.append(f"Last message repeated {run.repeats} times so far, still muted")
        if goes_through and notes:
            record.msg = text + "".join(f"\n{TAG}: {note}" for note in notes)
            record.args = None
        return goes_through

    def _forget_the_quiet(self, now: float) -> None:
        """Drops the messages that went quiet with nothing muted, so the table cannot grow without limit. Runs at most once a quiet spell.

        A muted message is kept until it comes back, because it holds the count its closing line will give.
        """
        if now - self._last_sweep < self._quiet_seconds:
            return
        self._last_sweep = now
        for key in [k for k, run in self._runs.items()
                    if run.repeats == 0 and now - run.last_seen >= self._quiet_seconds]:
            del self._runs[key]

    def _warn_once(self, error: Exception) -> None:
        """Says once on stderr, naming only the kind of error, that the muter failed and lines are going through (as Python's logging does)."""
        if not self._warned and sys.stderr is not None:
            self._warned = True
            try:
                sys.stderr.write(f"WARNING {__name__}: the log muter failed ({type(error).__name__}); lines go through unmuted\n")
            except OSError:                    # nowhere left to say it; the line still goes through
                pass
