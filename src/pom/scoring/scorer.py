"""The Scorer: gives each overheard fragment an interest score, so the Trigger can pick a hinge.

A fragment goes in (normally a whole sentence, from the SegmentFragmenter); a `ScoredFragment` comes out,
the fragment plus a score and where the score came from. Scores run from 0 (nothing to hold on to)
to 9 (extremely interesting), as the probability-weighted value of the single digit the scorer model
answers with, so they fall anywhere in between (6.35). They are relative, not absolute: ordinary talk
scores 1 to 3 and even striking sentences often stop short of 9, so whatever uses them should work
from ranks and gaps, not a fixed high threshold.

THREE KINDS OF RESULT, recorded in `ScoredFragment.scored_by`:
  * MODEL:    the scorer model scored it (through the ScorerModelClient). The normal case.
  * FRAGMENT_FILTER: the fragment has no content words (it is all filler, stopwords or numbers, like
              "Yeah, okay."), so the model is not asked and it gets the fragment_filter's score. This saves a
              model call on talk that cannot be interesting. The word tagger decides.
  * UNSCORED: the scorer model gave no score, so the score is None. Nothing downstream may treat
              it as a number: the ConvoInterestTracker leaves it out. The scorer model
              is local, so this means something larger is wrong, and the Printer stays quiet and
              keeps listening. (A word-counting fallback used to guess a score here. It is shelved
              in fallback_scorer.py, unused.)

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A model that is down, slow or answering
    oddly means unscored fragments and a warning in `report`, never an exception. `strict=True`
    raises ScorerError instead, for tests and dev tools.
  * A circuit breaker keeps a dead server from slowing every sentence: after `failure_limit`
    failures in a row the model is left alone for `retry_seconds`, every fragment goes unscored
    meanwhile, then one fragment is allowed through to see whether the model is back. One success
    closes the breaker. The clock is injected, so tests need no waiting.
  * The word tagger feeds the filter; the model is the judge. They are separate objects passed in,
    so tests use fakes.
  * One fragment at a time, no context, so a fragment scores the same whatever came before it. Any
    memory of what was heard recently (repeats, fading) belongs to the pool the Trigger keeps, not here.
  * Fragment text is never logged or put in a report; logs hold scores, sources and counts.
  * The settings (the fragment_filter's score, how many content words are needed, breaker numbers) are passed in by the
    entry point from Config; this module never reads Config.
"""
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, Optional

from pom.scoring.word_tagger import WordTagger
from pom.listening.segment_fragmenter import Fragment, FragmentLevel
from pom.log_setup import get_logger
from pom.fault_recorder import FaultRecorder

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_FRAGMENT_FILTER_SCORE = 1.0
DEFAULT_MIN_CONTENT_WORDS = 1
DEFAULT_FAILURE_LIMIT = 3
DEFAULT_RETRY_SECONDS = 30.0


class ScorerError(ValueError):
    """Raised for any scorer problem, but only in strict mode (development and tests)."""


class ScoredBy(Enum):
    """What made a fragment's score (or left it without one)."""
    MODEL = "model"
    FRAGMENT_FILTER = "fragment_filter"
    UNSCORED = "unscored"


@dataclass(frozen=True, slots=True)
class ScoredFragment:
    """A fragment with its interest score: a candidate hinge.

    Attributes:
        fragment: The fragment that was scored.
        score: 0 (dull) to 9 (extremely interesting), or None if the fragment could not
            be scored (scored_by UNSCORED). Check for None before using it as a number.
        scored_by: Whether the scorer model made the score, the fragment_filter did, or it is unscored.
    """
    fragment: Fragment
    score: Optional[float]
    scored_by: ScoredBy

    @property
    def text(self) -> str:
        """The fragment's words."""
        return self.fragment.text


class Scorer:
    """Scores fragments with the scorer model, with a filter in front of it.

    Attributes:
        report: Problems met (settings corrected, model unavailable, breaker opened).
    """

    def __init__(self, scorer_model_client, word_tagger: WordTagger, fragment_filter_score: float = DEFAULT_FRAGMENT_FILTER_SCORE,
                 min_content_words: int = DEFAULT_MIN_CONTENT_WORDS,
                 failure_limit: int = DEFAULT_FAILURE_LIMIT,
                 retry_seconds: float = DEFAULT_RETRY_SECONDS,
                 clock: Callable[[], float] = time.monotonic, strict: bool = False):
        """Args:
            scorer_model_client: A ScorerModelClient (anything with score(text), warm_up() and available).
            word_tagger: The WordTagger that tags each word of a fragment.
            fragment_filter_score: The score for a fragment the fragment_filter keeps from the model.
            min_content_words: Fragments with fewer content words skip the model (0 = never skip).
            failure_limit: Model failures in a row that open the circuit breaker.
            retry_seconds: How long the breaker stays open before the model gets another try.
            clock: Returns seconds; injected so tests need not wait.
            strict: Raise ScorerError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=ScorerError, logger=_log)
        self._scorer_model_client, self._word_tagger, self._clock = (
            scorer_model_client, word_tagger, clock)
        self._fragment_filter_score = self._number(fragment_filter_score, "fragment_filter_score",
                                                      DEFAULT_FRAGMENT_FILTER_SCORE, minimum=0.0)
        self._min_content = self._whole(min_content_words, "min_content_words", DEFAULT_MIN_CONTENT_WORDS, minimum=0)
        self._failure_limit = self._whole(failure_limit, "failure_limit", DEFAULT_FAILURE_LIMIT, minimum=1)
        self._retry_seconds = self._number(retry_seconds, "retry_seconds", DEFAULT_RETRY_SECONDS, minimum=0.0)
        self._failures = 0
        self._reopen_at: Optional[float] = None       # None while the breaker is closed
        self._counts = {scored_by: 0 for scored_by in ScoredBy}
        if not scorer_model_client.available:
            self.report.warn("the scorer model is not available (see the client's report); every "
                             "fragment that passes the filter will go unscored")

    # --- Settings -------------------------------------------------------------------

    def _number(self, value, name, default, minimum):
        """A non-negative number, or the default with a warning."""
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= minimum:
            return float(value)
        self.report.warn(f"{name} must be a number of at least {minimum:g}, got {value!r}; using {default:g}")
        return default

    def _whole(self, value, name, default, minimum):
        """A whole number of at least `minimum`, or the default with a warning."""
        if isinstance(value, int) and not isinstance(value, bool) and value >= minimum:
            return value
        self.report.warn(f"{name} must be a whole number of at least {minimum}, got {value!r}; using {default}")
        return default

    # --- Scoring --------------------------------------------------------------------

    def score(self, fragment: Fragment) -> ScoredFragment:
        """Scores one fragment. Always returns a result; see `scored_by` for how it was made."""
        content = [word for word in self._word_tagger.tag(fragment.text) if word.is_content]
        if self._skips_model(fragment.text, content):
            return self._result(fragment, self._fragment_filter_score, ScoredBy.FRAGMENT_FILTER)
        if self._model_allowed():
            value = self._scorer_model_client.score(fragment.text)
            if value is not None:
                self._model_worked()
                return self._result(fragment, value, ScoredBy.MODEL)
            self._model_failed()
        return self._result(fragment, None, ScoredBy.UNSCORED)

    def is_fragment_filtered(self, text: str) -> bool:
        """True if this text would skip the model (empty, or too few content words), without scoring it."""
        return self._skips_model(text, [word for word in self._word_tagger.tag(text) if word.is_content])

    def _skips_model(self, text: str, content: list) -> bool:
        """The filter rule, in one place: nothing to ask about, or fewer content words than the setting needs."""
        return not text.strip() or len(content) < self._min_content

    def score_text(self, text: str, heard_at: float = 0.0) -> ScoredFragment:
        """Scores plain text as a one-sentence fragment (for playgrounds and tests)."""
        return self.score(Fragment(text=text or "", level=FragmentLevel.SENTENCE, heard_at=heard_at))

    def score_many(self, fragments: Iterable[Fragment]) -> list:
        """Scores each fragment on its own, in the order given."""
        return [self.score(fragment) for fragment in fragments]

    def ranked(self, fragments: Iterable[Fragment]) -> list:
        """Scores the fragments and returns them best first (ties keep their order, unscored ones go last)."""
        return sorted(self.score_many(fragments),
                      key=lambda scored: (scored.score is None, -(scored.score or 0.0)))

    def warm_up(self) -> bool:
        """Loads the model before a real sentence needs it. True if it answered."""
        return bool(self._scorer_model_client.available and self._scorer_model_client.warm_up())

    # --- State ----------------------------------------------------------------------

    @property
    def degraded(self) -> bool:
        """True while the model is being left alone (breaker open) or cannot be used at all."""
        return not self._model_allowed()

    def counts(self) -> dict:
        """How many fragments each kind of result has had so far (counts only, never text)."""
        return {scored_by.value: number for scored_by, number in self._counts.items()}

    def _result(self, fragment, value, scored_by) -> ScoredFragment:
        self._counts[scored_by] += 1
        _log.debug("scored a fragment of %d words: %s (%s)", fragment.words,
                   "no score" if value is None else f"{value:.2f}", scored_by.value)
        return ScoredFragment(fragment, None if value is None else float(value), scored_by)

    # --- The circuit breaker --------------------------------------------------------

    def _model_allowed(self) -> bool:
        """False while the client is unusable or the breaker is open and not yet due a retry."""
        if not self._scorer_model_client.available:
            return False
        return self._reopen_at is None or self._clock() >= self._reopen_at

    def _model_failed(self) -> None:
        """Counts a failure and opens the breaker once there have been enough in a row."""
        self._failures += 1
        if self._failures >= self._failure_limit:
            self._reopen_at = self._clock() + self._retry_seconds
            self.report.warn(f"the scorer model failed {self._failures} times in a row; leaving "
                             f"fragments unscored and trying the model again in {self._retry_seconds:g} s")

    def _model_worked(self) -> None:
        """A success closes the breaker and clears the failure count."""
        if self._reopen_at is not None:
            _log.info("the scorer model is answering again")
        self._failures, self._reopen_at = 0, None
