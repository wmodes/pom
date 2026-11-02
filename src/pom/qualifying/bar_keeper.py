"""The BarKeeper: whether a score stands out from how the room has been scoring lately.

The BarKeeper does not use a fixed score. It asks what this room's recent scores look like and responds when a new one stands well above
them. In a dull room a 5 can stand out; in a lively one it may take an 8. Each score goes in through `record`, with the time it was
heard, and the BarKeeper answers what it did with it.

WHERE the bar sits is not decided here. It is worked out by a Bar, and there are several (a percentile of a window of recent
scores, a decaying quantile, a moving average), each its own class, so the way of deciding can be chosen in the config. This class does the
parts every way shares:

  * Qualifying: a score qualifies when it is ABOVE the bar, as the bar stood BEFORE that score was added, so a sentence is judged
    against the room as it was before it was said. A qualifying score is then handed to the bar like any other (the bar may leave it out).
  * The cooldown: after a qualifier, a score above the bar is not marked until `cooldown_seconds` have passed since that qualifier (60 to
    begin with; 0 turns it off). It is HELD: it still goes to the bar, and it does not restart the cooldown. A burst of strong
    sentences, such as a run of jokes, gives one qualifier and not six, and the next one comes from the room once the cooldown is over.
  * Time: the caller passes it. Live it is a clock reading; replaying a recording it is the place in the recording, which is why a
    recording can be replayed faster than real time and give the same answers. If a time ever goes backwards (a clock stepping back) it
    is held at the latest time seen, so the bar cannot be asked about the past.

A clip that has no score (None), or one the bar ignores (a score under a floor), is NO_MARK and stays out of everything here.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A bad setting is replaced by its default and recorded in `report`; bad input to
    `record` is simply ignored. `strict=True` raises BarKeeperError instead, for tests and dev tools.
  * The bar is passed in, already built (`bar_keeper_from_settings` builds the one the config chose), so a new way of deciding is a new
    Bar and nothing here changes.
  * This module never reads Config, and it never sees any words, only numbers.
"""
import math
from enum import Enum
from typing import Optional

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger
from pom.qualifying.percentile_bar import PercentileBar
from pom.qualifying.bar import Bar

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_COOLDOWN_SECONDS = 60.0


class BarKeeperError(ValueError):
    """Raised for any BarKeeper problem, but only in strict mode (development and tests)."""


class Ruling(Enum):
    """What the BarKeeper did with a score."""
    NO_MARK = "no mark"        # no score, not a number, no usable time, or a score the bar ignores
    DID_NOT_QUALIFY = "did not qualify"        # handed to the bar
    QUALIFIED = "qualified"    # stood above the bar (and was handed to the bar too)
    HELD = "held"    # stood above the bar, but a qualifier was too recent (handed to the bar too)
    TYPED_DNQ = "typed DNQ"          # a direct score (typed) that did not stand above the bar and was kept out of it


class BarKeeper:
    """Decides whether a new score stands out from the room's recent scores, using a bar and a cooldown.

    Attributes:
        report: Problems met (a bad setting replaced by its default).
        counted: How many scores have counted so far.
    """

    def __init__(self, bar: Optional[Bar] = None, cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
                 strict: bool = False):
        """Args:
            bar: The Bar that works out where the bar sits. None means a PercentileBar with its defaults.
            cooldown_seconds: How many seconds after a qualifier before another score may qualify, 0 or more (0 means no cooldown).
            strict: Raise BarKeeperError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=BarKeeperError, logger=_log)
        if bar is None:
            bar = PercentileBar()
        elif not isinstance(bar, Bar):
            self.report.warn(f"bar must be a Bar, got {type(bar).__name__}; using the default PercentileBar")
            bar = PercentileBar()
        self._bar = bar
        self._cooldown_seconds = self._valid_cooldown_seconds(cooldown_seconds)
        self._last_time: Optional[float] = None
        self._last_qualifier: Optional[float] = None
        self.counted = 0

    def _valid_cooldown_seconds(self, value) -> float:
        """A number of 0 or more, or the default with a warning."""
        if self._is_number(value) and value >= 0.0:
            return float(value)
        self.report.warn(f"cooldown_seconds must be a number of at least 0, got {value!r}; using {DEFAULT_COOLDOWN_SECONDS:g}")
        return DEFAULT_COOLDOWN_SECONDS

    @staticmethod
    def _is_number(value) -> bool:
        """True for a real int or float (not a bool, and not NaN or infinity)."""
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    # --- Questions ------------------------------------------------------------------

    def describe(self) -> str:
        """One line saying how the BarKeeper works and how it is set."""
        return f"{self._bar.describe()}; at most one qualifier every {self._cooldown_seconds:g} s"

    def bar_at(self, now: float) -> Optional[float]:
        """The score a new score must be above to qualify at time `now`, or None if `now` is not a number or the bar has none."""
        return self._bar.level_at(self._steady(now)) if self._is_number(now) else None

    def window_size_at(self, now: float) -> int:
        """How many scores the bar is built from at time `now` (what that means depends on the bar; it is for display)."""
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
        if not (self._is_number(score) and self._is_number(now)) or self._bar.ignores(score):
            return Ruling.NO_MARK
        now = self._steady(now)
        level = self._bar.level_at(now)
        above = level is not None and score > level
        cooling = above and self._last_qualifier is not None and now - self._last_qualifier < self._cooldown_seconds
        qualified = above and not cooling
        if qualified:
            self._last_qualifier = now
        self._bar.add(float(score), now, qualified)
        self._last_time = now
        self.counted += 1
        ruling = Ruling.QUALIFIED if qualified else Ruling.HELD if cooling else Ruling.DID_NOT_QUALIFY
        _log.debug("score %s: bar %s, %d counted", ruling.value, "none" if level is None else f"{level:.3f}", self.counted)
        return ruling

    def rule_typed(self, score: Optional[float], now: float, affects_bar: bool = False) -> Ruling:
        """Judges a score that was not heard in the room but handed to the Printer directly (a typed line).

        Such a score is held to the same bar as the room's, but it takes no part in the room's rhythm: no cooldown applies to it,
        and a qualifier it causes does not start one. Whether it also affects the bar (feeds what it is built from) is `affects_bar`: False (the
        default) keeps the bar a picture of the room alone, so the score changes nothing here.

        A score or time that is not a number, or one the bar ignores, is NO_MARK. Otherwise it is QUALIFIED if above the bar. If not, it is
        DID_NOT_QUALIFY when it affected the bar and TYPED_DNQ when it did not.
        """
        if not (self._is_number(score) and self._is_number(now)) or self._bar.ignores(score):
            return Ruling.NO_MARK
        now = self._steady(now)
        level = self._bar.level_at(now)
        qualified = level is not None and score > level
        if affects_bar:
            self._bar.add(float(score), now, qualified)
            self._last_time = now
            self.counted += 1
        ruling = Ruling.QUALIFIED if qualified else Ruling.DID_NOT_QUALIFY if affects_bar else Ruling.TYPED_DNQ
        _log.debug("direct score %s: bar %s", ruling.value, "none" if level is None else f"{level:.3f}")
        return ruling
