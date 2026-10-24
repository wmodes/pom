"""The Scorer: gives each overheard fragment an interest score, so the Trigger can pick a hinge.

A fragment goes in (normally a whole sentence, from the Fragmenter); a `ScoredFragment` comes out,
the fragment plus a score and where the score came from. Scores run from about 1 (nothing to hold
on to) to about 9.99 (extremely interesting). They are relative, not absolute: on the experiment's
sample the best sentence scored only about 8, and ordinary talk 2 to 3, so whatever uses them
should work from ranks and gaps, not a fixed high threshold.

THREE WAYS A SCORE IS MADE, recorded in `ScoredFragment.source`:
  * MODEL:    the language model scored it (through the InterestClient). The normal case.
  * FILTER:   the fragment has no content words (it is all filler, stopwords or numbers, like
              "Yeah, okay."), so the model is not asked and it gets the floor score. This saves a
              model call on talk that cannot be interesting. The word tools decide.
  * FALLBACK: the model gave no score, so a crude stand-in from the word tools is used instead.
              It leans on length and on names, numbers and questions, is capped below the model's
              usual top scores, and exists only so the Printer never stops. Do not rank a
              fallback score against a model score as if they meant the same thing; the source says
              which is which.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A model that is down, slow or answering
    oddly means fallback scores and a warning in `report`, never an exception. `strict=True` raises
    ScorerError instead, for tests and dev tools.
  * A circuit breaker keeps a dead server from slowing every sentence: after `failure_limit`
    failures in a row the model is left alone for `retry_seconds`, every fragment gets a fallback
    score meanwhile, then one fragment is allowed through to see whether the model is back. One
    success closes the breaker. The clock is injected, so tests need no waiting.
  * The word tools are the pre-filter and the fallback; the model is the judge. They are separate
    objects passed in, so tests use fakes.
  * One fragment at a time, no context, so a fragment scores the same whatever came before it. Any
    memory of what was heard recently (repeats, fading) belongs to the pool the Trigger keeps, not here.
  * Fragment text is never logged or put in a report; logs hold scores, sources and counts.
  * The settings (floor, how many content words are needed, breaker numbers) are passed in by the
    entry point from Config; this module never reads Config.
"""
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, Optional

from pom.hinge.words import WordTools
from pom.listening.fragmenter import Fragment, Kind
from pom.logs import get_logger
from pom.fault_recorder import FaultRecorder

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_FLOOR = 1.0
DEFAULT_MIN_CONTENT_WORDS = 1
DEFAULT_FAILURE_LIMIT = 3
DEFAULT_RETRY_SECONDS = 30.0

# The stand-in score: a base plus a little per content word, name, number and question, capped
# below where the model's best scores sit so a fallback score never looks like a standout.
FALLBACK_PER_CONTENT_WORD = 0.35
FALLBACK_CONTENT_WORDS_COUNTED = 10
FALLBACK_PER_NAME = 0.8
FALLBACK_NAMES_COUNTED = 2
FALLBACK_PER_NUMBER = 0.4
FALLBACK_NUMBERS_COUNTED = 2
FALLBACK_QUESTION_BONUS = 0.4
FALLBACK_CEILING = 5.0


class ScorerError(ValueError):
    """Raised for any scorer problem, but only in strict mode (development and tests)."""


class Source(Enum):
    """Where a score came from."""
    MODEL = "model"
    FILTER = "filter"
    FALLBACK = "fallback"


@dataclass(frozen=True, slots=True)
class ScoredFragment:
    """A fragment with its interest score: a candidate hinge.

    Attributes:
        fragment: The fragment that was scored.
        score: About 1 (dull) to 9.99 (extremely interesting).
        source: Whether the model, the filter or the fallback made the score.
    """
    fragment: Fragment
    score: float
    source: Source

    @property
    def text(self) -> str:
        """The fragment's words."""
        return self.fragment.text


class Scorer:
    """Scores fragments with a language model, with a filter in front and a fallback behind.

    Attributes:
        report: Problems met (settings corrected, model unavailable, breaker opened).
    """

    def __init__(self, client, words: WordTools, floor: float = DEFAULT_FLOOR,
                 min_content_words: int = DEFAULT_MIN_CONTENT_WORDS,
                 failure_limit: int = DEFAULT_FAILURE_LIMIT,
                 retry_seconds: float = DEFAULT_RETRY_SECONDS,
                 clock: Callable[[], float] = time.monotonic, strict: bool = False):
        """Args:
            client: An InterestClient (anything with score(text), warm_up() and available).
            words: The WordTools that read a fragment as words.
            floor: The score for a fragment the filter keeps from the model.
            min_content_words: Fragments with fewer content words skip the model (0 = never skip).
            failure_limit: Model failures in a row that open the circuit breaker.
            retry_seconds: How long the breaker stays open before the model gets another try.
            clock: Returns seconds; injected so tests need not wait.
            strict: Raise ScorerError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=ScorerError, logger=_log)
        self._client, self._words, self._clock = client, words, clock
        self._floor = self._number(floor, "floor", DEFAULT_FLOOR, minimum=0.0)
        self._min_content = self._whole(min_content_words, "min_content_words", DEFAULT_MIN_CONTENT_WORDS, minimum=0)
        self._failure_limit = self._whole(failure_limit, "failure_limit", DEFAULT_FAILURE_LIMIT, minimum=1)
        self._retry_seconds = self._number(retry_seconds, "retry_seconds", DEFAULT_RETRY_SECONDS, minimum=0.0)
        self._failures = 0
        self._reopen_at: Optional[float] = None       # None while the breaker is closed
        self._counts = {source: 0 for source in Source}
        if not client.available:
            self.report.warn("the scorer model is not available (see the client's report); every "
                             "fragment that passes the filter will get a fallback score")

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
        """Scores one fragment. Always returns a result; see `source` for how it was made."""
        words = self._words.analyze(fragment.text)
        content = [word for word in words if word.content]
        if self._skips_model(fragment.text, content):
            return self._result(fragment, self._floor, Source.FILTER)
        if self._model_allowed():
            value = self._client.score(fragment.text)
            if value is not None:
                self._model_worked()
                return self._result(fragment, value, Source.MODEL)
            self._model_failed()
        return self._result(fragment, self._fallback_score(fragment.text, words, content), Source.FALLBACK)

    def is_filtered(self, text: str) -> bool:
        """True if this text would skip the model (empty, or too few content words), without scoring it."""
        return self._skips_model(text, [word for word in self._words.analyze(text) if word.content])

    def _skips_model(self, text: str, content: list) -> bool:
        """The filter rule, in one place: nothing to ask about, or fewer content words than the setting needs."""
        return not text.strip() or len(content) < self._min_content

    def score_text(self, text: str, at: float = 0.0) -> ScoredFragment:
        """Scores plain text as a one-sentence fragment (for playgrounds and tests)."""
        return self.score(Fragment(text=text or "", kind=Kind.SENTENCE, at=at))

    def score_many(self, fragments: Iterable[Fragment]) -> list:
        """Scores each fragment on its own, in the order given."""
        return [self.score(fragment) for fragment in fragments]

    def ranked(self, fragments: Iterable[Fragment]) -> list:
        """Scores the fragments and returns them best first (ties keep their order)."""
        return sorted(self.score_many(fragments), key=lambda scored: -scored.score)

    def warm_up(self) -> bool:
        """Loads the model before a real sentence needs it. True if it answered."""
        return bool(self._client.available and self._client.warm_up())

    # --- State ----------------------------------------------------------------------

    @property
    def degraded(self) -> bool:
        """True while the model is being left alone (breaker open) or cannot be used at all."""
        return not self._model_allowed()

    def counts(self) -> dict:
        """How many fragments each source has scored so far (counts only, never text)."""
        return {source.value: number for source, number in self._counts.items()}

    def _result(self, fragment, value, source) -> ScoredFragment:
        self._counts[source] += 1
        _log.debug("scored a fragment of %d words: %.2f (%s)", fragment.words, value, source.value)
        return ScoredFragment(fragment, float(value), source)

    # --- The circuit breaker --------------------------------------------------------

    def _model_allowed(self) -> bool:
        """False while the client is unusable or the breaker is open and not yet due a retry."""
        if not self._client.available:
            return False
        return self._reopen_at is None or self._clock() >= self._reopen_at

    def _model_failed(self) -> None:
        """Counts a failure and opens the breaker once there have been enough in a row."""
        self._failures += 1
        if self._failures >= self._failure_limit:
            self._reopen_at = self._clock() + self._retry_seconds
            self.report.warn(f"the scorer model failed {self._failures} times in a row; using the "
                             f"word-tool fallback and trying the model again in {self._retry_seconds:g} s")

    def _model_worked(self) -> None:
        """A success closes the breaker and clears the failure count."""
        if self._reopen_at is not None:
            _log.info("the scorer model is answering again")
        self._failures, self._reopen_at = 0, None

    # --- The fallback ---------------------------------------------------------------

    def _fallback_score(self, text, words, content) -> float:
        """A crude stand-in score from the word tools, capped below the model's best scores."""
        names = sum(1 for word in words if word.is_name)
        numbers = sum(1 for word in words if word.is_number)
        value = (self._floor
                 + FALLBACK_PER_CONTENT_WORD * min(len(content), FALLBACK_CONTENT_WORDS_COUNTED)
                 + FALLBACK_PER_NAME * min(names, FALLBACK_NAMES_COUNTED)
                 + FALLBACK_PER_NUMBER * min(numbers, FALLBACK_NUMBERS_COUNTED)
                 + (FALLBACK_QUESTION_BONUS if "?" in text else 0.0))
        return min(value, FALLBACK_CEILING)
