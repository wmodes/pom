"""The ConvoInterestTracker: how interesting the room's talk has been, so the trigger can be relative.

The trigger does not use a fixed score. It asks how interesting this room has been lately and responds
when a fragment stands out from that. This module keeps that picture. It is built in steps; so far it
holds only the floor:

  * avg_convo_floor: the line below which a score is ignored. It never feeds avg_convo_interest and can
    never trigger. It is a fixed fraction of the possible score range, from `floor_fraction` (a setting,
    5% to begin with): on the 0 to 9 scale, 0.05 gives 0.45. A fixed fraction, not a fraction of
    avg_convo_interest, because each would then depend on the other and could send the other off.
  * avg_convo_interest (next): a decaying moving average of the scores at or above the floor.

A score of exactly the floor counts as above it. A fragment that has no score (None) is never above
the floor, so unscored fragments stay out of everything built on it.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A bad setting is replaced by its default and
    recorded in `report`; bad input to a question just answers no. `strict=True` raises
    ConvoInterestTrackerError instead, for tests and dev tools.
  * The score range is passed in (the entry point knows the scale the scorer model is asked to use);
    this module never reads Config, and it never sees any words, only numbers.
"""
import math
from typing import Optional, Sequence

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_FLOOR_FRACTION = 0.05
DEFAULT_SCORE_RANGE = (0.0, 9.0)       # the digits 0 to 9 the scorer model is asked to answer with


class ConvoInterestTrackerError(ValueError):
    """Raised for any tracker problem, but only in strict mode (development and tests)."""


class ConvoInterestTracker:
    """Keeps the floor (and, later, the average) that decide whether a score stands out.

    Attributes:
        report: Problems met (a bad setting replaced by its default).
        avg_convo_floor: The score below which scores are ignored.
    """

    def __init__(self, floor_fraction: float = DEFAULT_FLOOR_FRACTION,
                 score_range: Sequence[float] = DEFAULT_SCORE_RANGE, strict: bool = False):
        """Args:
            floor_fraction: How far up the score range the floor sits, from 0 up to but not including 1.
            score_range: (lowest, highest) possible score. The lowest must be below the highest.
            strict: Raise ConvoInterestTrackerError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=ConvoInterestTrackerError, logger=_log)
        low, high = self._valid_range(score_range)
        fraction = self._valid_fraction(floor_fraction)
        self._floor = low + fraction * (high - low)
        _log.debug("floor set at %.3f of the range %g to %g", self._floor, low, high)

    # --- Settings -------------------------------------------------------------------

    def _valid_fraction(self, value) -> float:
        """A number from 0 up to (not including) 1, or the default with a warning."""
        if self._is_number(value) and 0.0 <= value < 1.0:
            return float(value)
        self.report.warn(f"floor_fraction must be a number from 0 up to but not including 1, got {value!r}; "
                         f"using {DEFAULT_FLOOR_FRACTION:g}")
        return DEFAULT_FLOOR_FRACTION

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

    def is_above_floor(self, score: Optional[float]) -> bool:
        """True if the score is at or above the floor. No score, or anything that is not a number, is False."""
        return self._is_number(score) and score >= self._floor
