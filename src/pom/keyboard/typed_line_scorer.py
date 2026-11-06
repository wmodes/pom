"""The TypedLineScorer: a line typed at the keyboard in, an Attempt out.

Someone at the terminal's keyboard (whatever output device is attached) can type back. That is not the room being overheard but a person speaking to the Printer
directly, so it does not go through the Eavesdropper or the HingeFinder. This is its own way in: `score(line)` has the Scorer score the
line, multiplies the score by the score multiplier, and has the BarKeeper say whether it stands above the bar. The result is the same
Attempt the HingeFinder yields (with `kind` "typed"), so whatever takes the finder's sentences, the Conductor and the
monitoring site among them, takes these too, and a hinge is a hinge whichever way it came.

Design decisions, and why:
  * THE SCORE MULTIPLIER. A typed score is multiplied by `score_multiplier` (1.5 to begin with) before it meets the bar, because a person typing to
    the Printer is asking for an answer. The product is not capped at 9, so a typed 7 (10.5) beats any spoken 9.
  * THE SAME BAR, NO COOLDOWN. The line is held to the room's own bar, so a dull one ("hello") is simply ignored: it gets no answer
    and nothing is printed. It has no cooldown, and a qualifier it causes does not start one, so the room's rhythm is the room's.
  * THE BAR STAYS THE ROOM'S. By default a typed score has no influence on what the bar is built from (`bar_influence` 0), so the bar
    stays a picture of the room. A number above 0 lets it feed the bar at that share of a score heard in the room (1 is the same share; the number
    is not limited to 0 to 1). Both are in the config (`[qualifying]`).
  * Time is the stream's. `clock` gives seconds on the same timeline as the Eavesdropper's (`heard_at`), so the BarKeeper never sees time
    run backwards. The Conductor passes the clock it uses for listening.
  * Nothing here may stop the oracle (see pom/__init__.py). A line the Scorer cannot score, or that is blank, gives an unscored
    sentence the BarKeeper ignores; a bad score multiplier is replaced by its default with a warning (`strict` raises instead).
  * What was typed is never logged or put in a report: the text lives only in the sentence this returns.
  * The Scorer, the BarKeeper and the clock are passed in, already built (the entry point takes the score multiplier and `bar_influence` from
    `config.qualifying`); this module never reads Config. It adds no lock: the BarKeeper is shared with the HingeFinder's thread and
    has a lock of its own, so the two may call it at once. Which of them gets the floor is the Conductor's job.
"""
import math
from typing import Callable

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger
from pom.qualifying.attempt import Attempt
from pom.scoring.clip_scorer import ScoredBy

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

TYPED = "typed"
DEFAULT_SCORE_MULTIPLIER = 1.5


class TypedLineScorerError(ValueError):
    """Raised for any problem, but only in strict mode (development and tests)."""


class TypedLineScorer:
    """Scores the lines typed at the keyboard and says which of them the Printer should answer."""

    def __init__(self, scorer, bar_keeper, clock: Callable[[], float], score_multiplier: float = DEFAULT_SCORE_MULTIPLIER,
                 bar_influence: float = 0.0, strict: bool = False):
        """Args:
            scorer: A Scorer (anything with score_text(text, heard_at) giving a score and scored_by).
            bar_keeper: The BarKeeper the room's sentences also meet (anything with bar_at, window_size_at,
                rule_typed and counted).
            clock: Gives now, in seconds on the Eavesdropper's timeline.
            score_multiplier: What a score is multiplied by before it meets the bar, 0 or more.
            bar_influence: How much the typed score counts toward the bar, against 1 for a score heard in the room; 0 leaves it out. Any
                number (not limited to 0 to 1).
            strict: Raise TypedLineScorerError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=TypedLineScorerError, logger=_log)
        self._scorer = scorer
        self._bar_keeper = bar_keeper
        self._clock = clock
        self._score_multiplier = self._valid_score_multiplier(score_multiplier)
        self._bar_influence = self._valid_bar_influence(bar_influence)

    def _valid_bar_influence(self, value) -> float:
        """Any finite number (it is not limited to 0 to 1), or 0 with a warning: the bar stays the room's."""
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value)
        self.report.warn(f"bar_influence must be a number, got {value!r}; using 0")
        return 0.0

    def _valid_score_multiplier(self, value) -> float:
        """A number of 0 or more, or the default with a warning."""
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0.0:
            return float(value)
        self.report.warn(f"score_multiplier must be a number of at least 0, got {value!r}; using {DEFAULT_SCORE_MULTIPLIER:g}")
        return DEFAULT_SCORE_MULTIPLIER

    def score(self, line: str) -> Attempt:
        """Scores one typed line and judges it against the bar. Blocks while the Scorer works.

        The returned sentence's `score` is the multiplied one (the number the bar was asked about); `is_hinge` says whether the Printer
        should answer. A blank line, or one the Scorer could not score, comes back unscored with no ruling.
        """
        now = self._clock()
        text = line.strip() if isinstance(line, str) else ""
        bar, held = self._bar_keeper.bar_at(now), self._bar_keeper.window_size_at(now)
        if not text:
            return Attempt(text, TYPED, None, ScoredBy.UNSCORED, None, now, bar, held, self._bar_keeper.counted)
        result = self._scorer.score_text(text, now)
        multiplied = None if result.score is None else result.score * self._score_multiplier
        ruling = self._bar_keeper.rule_typed(multiplied, now, bar_influence=self._bar_influence)
        _log.debug("scored a typed line of %d words: %s", len(text.split()), ruling.value)
        return Attempt(text, TYPED, multiplied, result.scored_by, ruling, now, bar, held, self._bar_keeper.counted)
