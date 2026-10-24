"""The ConvoInterestTracker: how interesting the room's talk has been, so the trigger can be relative.

The trigger does not use a fixed score. It asks how interesting this room has been lately and responds
when a fragment stands out from that. In a dull room a 5 can stand out; in a lively one it may take an 8.
Each score goes in through `record`, and the tracker answers what it did with it.

  * avg_convo_floor: the line below which a score is ignored. It never feeds avg_convo_interest and can
    never trigger. It is a fixed fraction of the possible score range, from `floor_ignore_fraction` (5% to begin
    with): on the 0 to 9 scale, 0.05 gives 0.45. A fixed fraction, not a fraction of avg_convo_interest,
    because each would then depend on the other and could send the other off.
  * avg_convo_interest: a decaying moving average of the scores at or above the floor. The first such
    score becomes the average; after that each new one moves it part of the way toward itself, so old
    talk fades without a clock. How fast is set as a half-life, `interest_half_life`, in scores: a score's
    influence halves after that many more scores (10, to begin with, which makes each new score move
    the average about 6.7% of the way). It is None until a score has counted.
  * The trigger: a score triggers when it is at least `trigger_fraction` above the current
    avg_convo_interest (0.25 means 25% above), once `min_scores` scores have counted. The score is
    compared with the average as it stood BEFORE that score was added, so a fragment is judged against
    the room as it was before it was said. A triggering score is then added to the average like any other.

A score of exactly the floor counts as above it. A fragment that has no score (None) is ignored, so
unscored fragments stay out of everything here.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A bad setting is replaced by its default and
    recorded in `report`; bad input to `record` is simply ignored. `strict=True` raises
    ConvoInterestTrackerError instead, for tests and dev tools.
  * Decay is by score, not by time, so there is no clock to inject and silence leaves the average where
    it was.
  * The score range is passed in (the entry point knows the scale the scorer model is asked to use);
    this module never reads Config, and it never sees any words, only numbers.
"""
import math
from enum import Enum
from typing import Optional, Sequence

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_FLOOR_IGNORE_FRACTION = 0.05
DEFAULT_TRIGGER_FRACTION = 0.25
DEFAULT_INTEREST_HALF_LIFE = 10.0     # in scores
DEFAULT_MIN_SCORES = 10
DEFAULT_SCORE_RANGE = (0.0, 9.0)       # the digits 0 to 9 the scorer model is asked to answer with


class ConvoInterestTrackerError(ValueError):
    """Raised for any tracker problem, but only in strict mode (development and tests)."""


class ScoreEffect(Enum):
    """What the tracker did with a score."""
    IGNORED = "ignored"        # no score, not a number, or below the floor
    COUNTED = "counted"        # added to avg_convo_interest
    TRIGGERED = "triggered"    # stood out from avg_convo_interest (and was added to it too)


class ConvoInterestTracker:
    """Keeps the floor and the average that decide whether a score stands out.

    Attributes:
        report: Problems met (a bad setting replaced by its default).
        avg_convo_floor: The score below which scores are ignored.
        avg_convo_interest: The decaying average of the scores at or above the floor, or None.
        interest_weight: How far each counted score moves the average toward itself (worked out from
            the half-life).
        trigger_level: The score a new score must reach to trigger, or None before any score counted.
        counted: How many scores have counted so far.
    """

    def __init__(self, floor_ignore_fraction: float = DEFAULT_FLOOR_IGNORE_FRACTION,
                 trigger_fraction: float = DEFAULT_TRIGGER_FRACTION,
                 interest_half_life: float = DEFAULT_INTEREST_HALF_LIFE, min_scores: int = DEFAULT_MIN_SCORES,
                 score_range: Sequence[float] = DEFAULT_SCORE_RANGE, strict: bool = False):
        """Args:
            floor_ignore_fraction: How far up the score range the floor sits, from 0 up to but not including 1.
            trigger_fraction: How far above avg_convo_interest a score must be to trigger, 0 or more
                (0.25 is 25% above).
            interest_half_life: How many more scores it takes for a score's influence on
                avg_convo_interest to halve, above 0 (10 means about 10 scores of memory).
            min_scores: How many scores must have counted before the trigger may fire, a whole number
                of at least 1.
            score_range: (lowest, highest) possible score. The lowest must be below the highest.
            strict: Raise ConvoInterestTrackerError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=ConvoInterestTrackerError, logger=_log)
        low, high = self._valid_range(score_range)
        fraction = self._valid_fraction(floor_ignore_fraction)
        self._floor = low + fraction * (high - low)
        self._trigger_fraction = self._valid_trigger_fraction(trigger_fraction)
        self._weight = 1.0 - 0.5 ** (1.0 / self._valid_half_life(interest_half_life))
        self._min_scores = self._valid_min_scores(min_scores)
        self._interest: Optional[float] = None
        self._counted = 0
        _log.debug("floor set at %.3f of the range %g to %g", self._floor, low, high)

    # --- Settings -------------------------------------------------------------------

    def _valid_fraction(self, value) -> float:
        """A number from 0 up to (not including) 1, or the default with a warning."""
        if self._is_number(value) and 0.0 <= value < 1.0:
            return float(value)
        self.report.warn(f"floor_ignore_fraction must be a number from 0 up to but not including 1, got {value!r}; "
                         f"using {DEFAULT_FLOOR_IGNORE_FRACTION:g}")
        return DEFAULT_FLOOR_IGNORE_FRACTION

    def _valid_trigger_fraction(self, value) -> float:
        """A number of 0 or more, or the default with a warning."""
        if self._is_number(value) and value >= 0.0:
            return float(value)
        self.report.warn(f"trigger_fraction must be a number of at least 0, got {value!r}; "
                         f"using {DEFAULT_TRIGGER_FRACTION:g}")
        return DEFAULT_TRIGGER_FRACTION

    def _valid_half_life(self, value) -> float:
        """A number above 0, or the default with a warning."""
        if self._is_number(value) and value > 0.0:
            return float(value)
        self.report.warn(f"interest_half_life must be a number above 0, got {value!r}; "
                         f"using {DEFAULT_INTEREST_HALF_LIFE:g}")
        return DEFAULT_INTEREST_HALF_LIFE

    def _valid_min_scores(self, value) -> int:
        """A whole number of at least 1, or the default with a warning."""
        if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
            return value
        self.report.warn(f"min_scores must be a whole number of at least 1, got {value!r}; "
                         f"using {DEFAULT_MIN_SCORES}")
        return DEFAULT_MIN_SCORES

    def _valid_range(self, value) -> tuple:
        """A (lowest, highest) pair of numbers with lowest below highest, or the default with a warning."""
        try:
            low, high = value
            if self._is_number(low) and self._is_number(high) and low < high:
                return float(low), float(high)
        except (TypeError, ValueError):
            pass
        self.report.warn(f"score_range must be two numbers with the lowest below the highest, got {value!r}; "
                         f"using {DEFAULT_SCORE_RANGE}")
        return DEFAULT_SCORE_RANGE

    @staticmethod
    def _is_number(value) -> bool:
        """True for a real int or float (not a bool, and not NaN or infinity)."""
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

    # --- Questions ------------------------------------------------------------------

    @property
    def avg_convo_floor(self) -> float:
        """The score below which scores are ignored."""
        return self._floor

    @property
    def avg_convo_interest(self) -> Optional[float]:
        """The decaying average of the scores at or above the floor, or None before any has counted."""
        return self._interest

    @property
    def interest_weight(self) -> float:
        """How far each counted score moves the average toward itself, worked out from the half-life."""
        return self._weight

    @property
    def trigger_level(self) -> Optional[float]:
        """The score a new score must reach to trigger, or None before any score has counted."""
        return None if self._interest is None else self._interest * (1.0 + self._trigger_fraction)

    @property
    def counted(self) -> int:
        """How many scores have counted so far."""
        return self._counted

    def is_above_floor(self, score: Optional[float]) -> bool:
        """True if the score is at or above the floor. No score, or anything that is not a number, is False."""
        return self._is_number(score) and score >= self._floor

    # --- Taking in scores -----------------------------------------------------------

    def record(self, score: Optional[float]) -> ScoreEffect:
        """Takes in one score and says what happened to it.

        A score that is missing, is not a number, or is below the floor is IGNORED and changes nothing.
        Otherwise it is compared with the trigger level as it stands now, then added to the average. It
        is TRIGGERED if it reached that level and at least `min_scores` scores had already counted; if
        not, it is COUNTED.
        """
        if not self.is_above_floor(score):
            return ScoreEffect.IGNORED
        triggered = self._counted >= self._min_scores and score >= self.trigger_level
        if self._interest is None:
            self._interest = float(score)
        else:
            self._interest += self._weight * (score - self._interest)
        self._counted += 1
        _log.debug("score %s: average now %.3f after %d counted", "triggered" if triggered else "counted",
                   self._interest, self._counted)
        return ScoreEffect.TRIGGERED if triggered else ScoreEffect.COUNTED
