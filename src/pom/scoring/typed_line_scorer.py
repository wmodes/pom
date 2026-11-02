"""The TypedLineScorer: a line typed at the keyboard in, a ScoredSentence out.

Someone at the terminal's keyboard (whatever output device is attached) can type back. That is not the room being overheard but a person speaking to the Printer
directly, so it does not go through the Listener or the HingeFinder. This is its own way in: `score(line)` has the Scorer score the
line, multiplies the score by the typed score multiplier, and has the ConvoInterestTracker say whether it stands above the bar. The result is the same
ScoredSentence the HingeFinder yields (with `kind` "typed"), so whatever takes the finder's sentences, the Conductor and the
monitoring site among them, takes these too, and a hinge is a hinge whichever way it came.

Design decisions, and why:
  * THE TYPED SCORE MULTIPLIER. A typed score is multiplied by `multiplier` (1.5 to begin with) before it meets the bar, because a person typing to
    the Printer is asking for an answer. The product is not capped at 9, so a typed 7 (10.5) beats any spoken 9.
  * THE SAME BAR, NO COOLDOWN. The line is held to the room's own bar, so a dull one ("hello") is simply ignored: it gets no answer
    and nothing is printed. It has no cooldown, and a trigger it causes does not start one, so the room's rhythm is the room's.
  * THE BAR STAYS THE ROOM'S. By default a typed score is kept out of what the bar is built from (`affects_bar` False), so the bar
    stays a picture of the room. True lets it feed the bar like any other score. Both are in the config (`[trigger]`).
  * Time is the stream's. `clock` gives seconds on the same timeline as the Listener's (`heard_at`), so the tracker never sees time
    run backwards. The Conductor passes the clock it uses for listening.
  * Nothing here may stop the oracle (see pom/__init__.py). A line the Scorer cannot score, or that is blank, gives an unscored
    sentence the tracker ignores; a bad multiplier is replaced by its default with a warning (`strict` raises instead).
  * What was typed is never logged or put in a report: the text lives only in the sentence this returns.
  * The Scorer, the tracker and the clock are passed in, already built (the entry point takes the multiplier and `affects_bar` from
    `config.trigger`); this module never reads Config. It adds no lock: the tracker is shared with the HingeFinder's thread, and
    keeping the two from calling it at once is the Conductor's job.
"""
import math
from typing import Callable

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger
from pom.scoring.hinge_finder import ScoredSentence
from pom.scoring.clip_scorer import ScoredBy

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

TYPED = "typed"
DEFAULT_MULTIPLIER = 1.5


class TypedLineScorerError(ValueError):
    """Raised for any problem, but only in strict mode (development and tests)."""


class TypedLineScorer:
    """Scores the lines typed at the keyboard and says which of them the Printer should answer."""

    def __init__(self, scorer, tracker, clock: Callable[[], float], multiplier: float = DEFAULT_MULTIPLIER,
                 affects_bar: bool = False, strict: bool = False):
        """Args:
            scorer: A Scorer (anything with score_text(text, heard_at) giving a score and scored_by).
            tracker: The ConvoInterestTracker the room's sentences also meet (anything with trigger_level_at, window_size_at,
                record_direct and counted).
            clock: Gives now, in seconds on the Listener's timeline.
            multiplier: What a score is multiplied by before it meets the bar, 0 or more.
            affects_bar: True lets the typed score affect the bar (feed what it is built from).
            strict: Raise TypedLineScorerError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=TypedLineScorerError, logger=_log)
        self._scorer = scorer
        self._tracker = tracker
        self._clock = clock
        self._multiplier = self._valid_multiplier(multiplier)
        self._affects_bar = bool(affects_bar)

    def _valid_multiplier(self, value) -> float:
        """A number of 0 or more, or the default with a warning."""
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0.0:
            return float(value)
        self.report.warn(f"multiplier must be a number of at least 0, got {value!r}; using {DEFAULT_MULTIPLIER:g}")
        return DEFAULT_MULTIPLIER

    def score(self, line: str) -> ScoredSentence:
        """Scores one typed line and judges it against the bar. Blocks while the Scorer works.

        The returned sentence's `score` is the multiplied one (the number the bar was asked about); `is_hinge` says whether the Printer
        should answer. A blank line, or one the Scorer could not score, comes back unscored with no effect.
        """
        now = self._clock()
        text = line.strip() if isinstance(line, str) else ""
        bar, held = self._tracker.trigger_level_at(now), self._tracker.window_size_at(now)
        if not text:
            return ScoredSentence(text, TYPED, None, ScoredBy.UNSCORED, None, now, bar, held, self._tracker.counted)
        result = self._scorer.score_text(text, now)
        multiplied = None if result.score is None else result.score * self._multiplier
        effect = self._tracker.record_direct(multiplied, now, affects_bar=self._affects_bar)
        _log.debug("scored a typed line of %d words: %s", len(text.split()), effect.value)
        return ScoredSentence(text, TYPED, multiplied, result.scored_by, effect, now, bar, held, self._tracker.counted)
