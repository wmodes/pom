"""The ConvoInterestTracker: whether a score stands out from how the room has been scoring lately.

The trigger does not use a fixed score. It asks what this room's recent scores look like and responds when a new one stands well above
them. In a dull room a 5 can stand out; in a lively one it may take an 8. Each score goes in through `record`, with the time it was
heard, and the tracker answers what it did with it.

WHERE the bar sits is not decided here. It is worked out by a TriggerBar, and there are several (a percentile of a window of recent
scores, a decaying quantile, a moving average), each its own class, so the way of deciding can be chosen in the config. This class does the
parts every way shares:

  * The trigger: a score triggers when it is ABOVE the bar, as the bar stood BEFORE that score was added, so a sentence is judged
    against the room as it was before it was said. A triggering score is then handed to the bar like any other (the bar may leave it out).
  * The cooldown: after a trigger, a score above the bar is not marked until `cooldown_seconds` have passed since that trigger (60 to
    begin with; 0 turns it off). It is COOLING_DOWN: it still goes to the bar, and it does not restart the cooldown. A burst of strong
    sentences, such as a run of jokes, gives one trigger and not six, and the next one comes from the room once the cooldown is over.
  * Time: the caller passes it. Live it is a clock reading; replaying a recording it is the place in the recording, which is why a
    recording can be replayed faster than real time and give the same answers. If a time ever goes backwards (a clock stepping back) it
    is held at the latest time seen, so the bar cannot be asked about the past.

A fragment that has no score (None), or one the bar ignores (a score under a floor), is IGNORED and stays out of everything here.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A bad setting is replaced by its default and recorded in `report`; bad input to
    `record` is simply ignored. `strict=True` raises ConvoInterestTrackerError instead, for tests and dev tools.
  * The bar is passed in, already built (`tracker_from_settings` builds the one the config chose), so a new way of deciding is a new
    TriggerBar and nothing here changes.
  * This module never reads Config, and it never sees any words, only numbers.
"""
import math
from enum import Enum
from typing import Optional

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger
from pom.scoring.percentile_bar import PercentileBar
from pom.scoring.trigger_bar import TriggerBar

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_COOLDOWN_SECONDS = 60.0


class ConvoInterestTrackerError(ValueError):
    """Raised for any tracker problem, but only in strict mode (development and tests)."""


class ScoreEffect(Enum):
    """What the tracker did with a score."""
    IGNORED = "ignored"        # no score, not a number, no usable time, or a score the bar ignores
    COUNTED = "counted"        # handed to the bar
    TRIGGERED = "triggered"    # stood above the bar (and was handed to the bar too)
    COOLING_DOWN = "cooling down"    # stood above the bar, but a trigger was too recent (handed to the bar too)


class ConvoInterestTracker:
    """Decides whether a new score stands out from the room's recent scores, using a bar and a cooldown.

    Attributes:
        report: Problems met (a bad setting replaced by its default).
        counted: How many scores have counted so far.
    """

    def __init__(self, bar: Optional[TriggerBar] = None, cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
                 strict: bool = False):
        """Args:
            bar: The TriggerBar that works out where the bar sits. None means a PercentileBar with its defaults.
            cooldown_seconds: How many seconds after a trigger before another score may trigger, 0 or more (0 means no cooldown).
            strict: Raise ConvoInterestTrackerError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=ConvoInterestTrackerError, logger=_log)
        if bar is None:
            bar = PercentileBar()
        elif not isinstance(bar, TriggerBar):
            self.report.warn(f"bar must be a TriggerBar, got {type(bar).__name__}; using the default PercentileBar")
            bar = PercentileBar()
        self._bar = bar
        self._cooldown_seconds = self._valid_cooldown_seconds(cooldown_seconds)
        self._last_time: Optional[float] = None
        self._last_trigger: Optional[float] = None
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
        """One line saying how the trigger works and how it is set."""
        return f"{self._bar.describe()}; at most one trigger every {self._cooldown_seconds:g} s"

    def trigger_level_at(self, now: float) -> Optional[float]:
        """The score a new score must be above to trigger at time `now`, or None if `now` is not a number or the bar has none."""
        return self._bar.level_at(self._steady(now)) if self._is_number(now) else None

    def window_size_at(self, now: float) -> int:
        """How many scores the bar is built from at time `now` (what that means depends on the bar; it is for display)."""
        return self._bar.window_size_at(self._steady(now) if self._is_number(now) else (self._last_time or 0.0))

    def _steady(self, now: float) -> float:
        """`now`, held at the latest time seen so a clock stepping back cannot reorder things."""
        return now if self._last_time is None or now >= self._last_time else self._last_time

    # --- Taking in scores -----------------------------------------------------------

    def record(self, score: Optional[float], now: float) -> ScoreEffect:
        """Takes in one score heard at time `now` (seconds) and says what happened to it.

        A score that is missing or not a number, one the bar ignores, or a time that is not a number, is IGNORED and changes nothing.
        Otherwise the score is compared with the bar as it stands now, then handed to the bar, whatever happens to it. It is TRIGGERED if it
        was above the bar and at least `cooldown_seconds` have passed since the last trigger; COOLING_DOWN if it was above the bar but
        that trigger was too recent; COUNTED otherwise.
        """
        if not (self._is_number(score) and self._is_number(now)) or self._bar.ignores(score):
            return ScoreEffect.IGNORED
        now = self._steady(now)
        level = self._bar.level_at(now)
        above = level is not None and score > level
        cooling = above and self._last_trigger is not None and now - self._last_trigger < self._cooldown_seconds
        triggered = above and not cooling
        if triggered:
            self._last_trigger = now
        self._bar.add(float(score), now, triggered)
        self._last_time = now
        self.counted += 1
        effect = ScoreEffect.TRIGGERED if triggered else ScoreEffect.COOLING_DOWN if cooling else ScoreEffect.COUNTED
        _log.debug("score %s: bar %s, %d counted", effect.value, "none" if level is None else f"{level:.3f}", self.counted)
        return effect
