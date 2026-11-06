"""The BarKeeper: whether a score stands out from how the room has been scoring lately.

The BarKeeper does not use a fixed score. It asks what this room's recent scores look like and responds when a new one stands well above
them. In a dull room a 5 can stand out; in a lively one it may take an 8. Each score goes in through `rule`, with the time it was
heard, and the BarKeeper answers what it did with it.

WHERE the bar sits is not decided here. It is worked out by a Bar, and there are several (a percentile of a window of recent
scores, a decaying quantile, a moving average), each its own class, so the way of deciding can be chosen in the config. This class does the
parts every way shares:

  * Qualifying: a score qualifies when it is ABOVE the bar, as the bar stood BEFORE that score was added, so a sentence is judged
    against the room as it was before it was said. A score that stood above the bar, whether it qualified or was held by the cooldown, goes
    to the bar as the bar plus `qualifier_bar_influence` of the way up to the score, so the zinger that fired does not hold the bar up as if it
    were an ordinary score (1 goes in as the score itself, 0 as the bar itself, 0.5 half way between).
    A typed line goes in by the same rule with its own dial (`typed_lines_bar_influence`), whether or not it stood above the bar.
  * The cooldown: after a qualifier, a score above the bar is not marked until `cooldown_seconds` have passed since that qualifier (60 to
    begin with; 0 turns it off). It is HELD: it still goes to the bar, and it does not restart the cooldown. A burst of strong
    sentences, such as a run of jokes, gives one qualifier and not six, and the next one comes from the room once the cooldown is over.
  * Time: the caller passes it. Live it is a clock reading; replaying a recording it is the place in the recording, which is why a
    recording can be replayed faster than real time and give the same answers. If a time ever goes backwards (a clock stepping back) it
    is held at the latest time seen, so the bar cannot be asked about the past.

A clip that has no score (None), or one the bar ignores (a score under a floor), is NO_MARK and stays out of everything here.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A bad setting is replaced by its default and recorded in `report`; bad input to
    `rule` is simply ignored. `strict=True` raises BarKeeperError instead, for tests and dev tools.
  * THE QUALIFIER'S INFLUENCE IS THE BARKEEPER'S, NOT EACH BAR'S. How much a score that stood above the bar influences the bar is one
    dial, `qualifier_bar_influence`, a plain number that is not limited to 0 to 1 (an experiment may want 1.1 or -0.1): the BarKeeper changes
    the score's value on its way in (bar + influence * (score - bar)) and hands it to whichever bar it has, so every bar gets the dial and none
    needs to know about it. Typed lines (`rule_typed`) use the same rule with their own dial.
  * The bar is passed in, already built (`bar_keeper_from_settings` builds the one the config chose), so a new way of deciding is a new
    Bar and nothing here changes.
  * This module never reads Config, and it never sees any words, only numbers.
  * Two threads share one BarKeeper: the room's (the HingeFinder) and the keyboard's (the TypedLineScorer). Each public call takes the
    BarKeeper's own lock for its whole length, so a ruling never sees the bar or the window half-updated by the other thread. The lock
    is per call, not per sequence: a caller that asks `bar_at` and then `rule` may find the other thread's score came between the two,
    so the bar it reports can differ slightly from the one the ruling used. The ruling itself is always consistent.
  * The lock is in memory only, never a file, so a crash or a power cut cannot leave it stuck. And no call can wait on it forever: after
    `LOCK_TIMEOUT_SECONDS` a call gives up, records a CRITICAL fault, and answers as if it had nothing (NO_MARK, no bar, a window of 0)
    rather than freeze the thread that is listening. Nothing inside the lock waits on anything, so this should never happen; it is there
    so that a bug somewhere cannot deafen the Printer.
"""
import math
import threading
from contextlib import contextmanager
from enum import Enum
from typing import Iterator, Optional

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger
from pom.qualifying.percentile_bar import PercentileBar
from pom.qualifying.bar import Bar

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_COOLDOWN_SECONDS = 60.0
DEFAULT_QUALIFIER_BAR_INFLUENCE = 0.5     # a score that stood above the bar goes into the bar half way up from the bar to the score
LOCK_TIMEOUT_SECONDS = 5.0   # the longest a call waits for the lock; the work inside it takes microseconds


class BarKeeperError(ValueError):
    """Raised for any BarKeeper problem, but only in strict mode (development and tests)."""


class Ruling(Enum):
    """What the BarKeeper did with a score."""
    NO_MARK = "no mark"        # no score, not a number, no usable time, or a score the bar ignores
    DID_NOT_QUALIFY = "did not qualify"        # handed to the bar
    QUALIFIED = "qualified"    # stood above the bar (and was handed to the bar too)
    HELD = "held"    # stood above the bar, but a qualifier was too recent (handed to the bar like a qualifier)


class BarKeeper:
    """Decides whether a new score stands out from the room's recent scores, using a bar and a cooldown.

    Attributes:
        report: Problems met (a bad setting replaced by its default).
        counted: How many scores have counted so far.
    """

    def __init__(self, bar: Optional[Bar] = None, cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
                 qualifier_bar_influence: float = DEFAULT_QUALIFIER_BAR_INFLUENCE, strict: bool = False):
        """Args:
            bar: The Bar that works out where the bar sits. None means a PercentileBar with its defaults.
            cooldown_seconds: How many seconds after a qualifier before another score may qualify, 0 or more (0 means no cooldown).
            qualifier_bar_influence: How far up from the bar to the score a score that stood above the bar goes into the bar: 1 is the score
                itself, 0 is the bar itself. Any number (not limited to 0 to 1).
            strict: Raise BarKeeperError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=BarKeeperError, logger=_log)
        if bar is None:
            bar = PercentileBar()
        elif not isinstance(bar, Bar):
            self.report.warn(f"bar must be a Bar, got {type(bar).__name__}; using the default PercentileBar")
            bar = PercentileBar()
        self._bar = bar
        self._lock = threading.Lock()      # held for the whole of each public call that reads or changes the bar or the clock
        self._cooldown_seconds = self._valid_cooldown_seconds(cooldown_seconds)
        self._qualifier_bar_influence = self._valid_influence(qualifier_bar_influence)
        self._last_time: Optional[float] = None
        self._last_qualifier: Optional[float] = None
        self.counted = 0

    def _valid_cooldown_seconds(self, value) -> float:
        """A number of 0 or more, or the default with a warning."""
        if self._is_number(value) and value >= 0.0:
            return float(value)
        self.report.warn(f"cooldown_seconds must be a number of at least 0, got {value!r}; using {DEFAULT_COOLDOWN_SECONDS:g}")
        return DEFAULT_COOLDOWN_SECONDS

    def _valid_influence(self, value) -> float:
        """Any finite number, or the default with a warning."""
        if self._is_number(value):
            return float(value)
        self.report.warn(f"qualifier_bar_influence must be a number, got {value!r}; using {DEFAULT_QUALIFIER_BAR_INFLUENCE:g}")
        return DEFAULT_QUALIFIER_BAR_INFLUENCE

    @staticmethod
    def _value_for_bar(score: float, level: Optional[float], influence: float) -> float:
        """The value a score goes into the bar as: the bar plus `influence` of the way up to the score.

        With 1 it is the score itself, with 0 it is the bar, and with 0.5 it is half way between. This is the one rule, for a qualifier and for a
        typed line alike (each with its own influence). With no bar there is nothing to be relative to, so the score goes in as it is.
        """
        return float(score) if level is None else level + influence * (score - level)

    @staticmethod
    def _is_number(value) -> bool:
        """True for a real int or float (not a bool, and not NaN or infinity)."""
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    @contextmanager
    def _held(self, call: str) -> Iterator[bool]:
        """Takes the lock for the length of the `with`, yielding True; or yields False (and records a fault) if it waited too long."""
        got = self._lock.acquire(timeout=LOCK_TIMEOUT_SECONDS)
        if not got:
            self.report.critical(f"{call} could not get the lock in {LOCK_TIMEOUT_SECONDS:g} s; answering as if it had nothing")
        try:
            yield got
        finally:
            if got:
                self._lock.release()

    # --- Questions ------------------------------------------------------------------

    def describe(self) -> str:
        """One line saying how the BarKeeper works and how it is set."""
        return (f"{self._bar.describe()}; at most one qualifier every {self._cooldown_seconds:g} s; "
                f"a qualifying score goes into the bar {self._qualifier_bar_influence * 100:g}% of the way up from the bar")

    def bar_at(self, now: float) -> Optional[float]:
        """The score a new score must be above to qualify at time `now`, or None if `now` is not a number or the bar has none."""
        with self._held("bar_at") as got:
            if not got:
                return None
            return self._bar.level_at(self._steady(now)) if self._is_number(now) else None

    def window_size_at(self, now: float) -> int:
        """How many scores the bar is built from at time `now` (what that means depends on the bar; it is for display)."""
        with self._held("window_size_at") as got:
            if not got:
                return 0
            return self._bar.window_size_at(self._steady(now) if self._is_number(now) else (self._last_time or 0.0))

    def _steady(self, now: float) -> float:
        """`now`, held at the latest time seen so a clock stepping back cannot reorder things."""
        return now if self._last_time is None or now >= self._last_time else self._last_time

    # --- Taking in scores -----------------------------------------------------------

    def rule(self, score: Optional[float], now: float) -> Ruling:
        """Takes in one score heard at time `now` (seconds) and says what happened to it.

        A score that is missing or not a number, one the bar ignores, or a time that is not a number, is NO_MARK and changes nothing.
        Otherwise the score is compared with the bar as it stands now, then handed to the bar, whatever happens to it. It is QUALIFIED if it
        was above the bar and at least `cooldown_seconds` have passed since the last qualifier; HELD if it was above the bar but
        that qualifier was too recent; DID_NOT_QUALIFY otherwise.
        """
        with self._held("rule") as got:
            if not got or not (self._is_number(score) and self._is_number(now)) or self._bar.ignores(score):
                return Ruling.NO_MARK
            now = self._steady(now)
            level = self._bar.level_at(now)
            above = level is not None and score > level
            cooling = above and self._last_qualifier is not None and now - self._last_qualifier < self._cooldown_seconds
            qualified = above and not cooling
            if qualified:
                self._last_qualifier = now
            self._bar.add(self._value_for_bar(score, level, self._qualifier_bar_influence) if above else float(score), now)    # a held score stood above the bar too: it goes in as the qualifier it would have been
            self._last_time = now
            self.counted += 1
            ruling = Ruling.QUALIFIED if qualified else Ruling.HELD if cooling else Ruling.DID_NOT_QUALIFY
            _log.debug("score %s: bar %s, %d counted", ruling.value, "none" if level is None else f"{level:.3f}", self.counted)
            return ruling

    def rule_typed(self, score: Optional[float], now: float, bar_influence: float = 0.0) -> Ruling:
        """Judges a score that was not heard in the room but handed to the Printer directly (a typed line).

        Such a score is held to the same bar as the room's, but it takes no part in the room's rhythm: no cooldown applies to it,
        and a qualifier it causes does not start one. Every typed line then goes to the bar by the same rule as a qualifier, with its own
        dial: as the bar plus `bar_influence` of the way up to the score (1 is the score itself, 0 the bar itself).

        A score or time that is not a number, or one the bar ignores, is NO_MARK. Otherwise it is QUALIFIED if above the bar, and
        DID_NOT_QUALIFY if not.
        """
        with self._held("rule_typed") as got:
            if not got or not (self._is_number(score) and self._is_number(now)) or self._bar.ignores(score):
                return Ruling.NO_MARK
            now = self._steady(now)
            level = self._bar.level_at(now)
            qualified = level is not None and score > level
            influence = bar_influence if self._is_number(bar_influence) else 0.0
            self._bar.add(self._value_for_bar(score, level, influence), now)
            self._last_time = now
            self.counted += 1
            ruling = Ruling.QUALIFIED if qualified else Ruling.DID_NOT_QUALIFY
            _log.debug("direct score %s: bar %s", ruling.value, "none" if level is None else f"{level:.3f}")
            return ruling
