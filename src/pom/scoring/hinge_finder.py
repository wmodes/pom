"""The HingeFinder: sound in, hinges out.

It joins the listening side to the trigger. The Listener says what was heard; the SegmentFragmenter cuts the words into
sentences; the Scorer scores each sentence; the ConvoInterestTracker says whether a score stands out enough to answer.
`find()` yields one ScoredSentence for each thing heard, and the one the trigger fired on (`is_hinge`) is the hinge: the
sentence the oracle will be asked about, word for word.

Design decisions, and why:
  * `find()` is a plain generator, like the Listener's `hear()`, so this module needs no threads. It blocks while it waits
    for sound and while the Scorer works. The Conductor runs it in a thread of its own, and a playground can print every
    ScoredSentence, which is why the ones that did not trigger are yielded too.
  * Time is the Listener's: counted in audio received, so a recording replayed at any speed triggers exactly as a live run.
  * Only whole sentences are scored; clauses and phrases are not (the same rule the hearing-debug experiment used).
  * AMBIENT SOUNDS. A caption like "(dog barks)" is yielded either way, so a playground can show it. With
    `consider_ambient_sounds` off (the default) it is not scored and the trigger never sees it: `score` is None and
    `effect` is None. With it on, the caption is scored like a sentence and counted by the trigger, so a sound can be the
    hinge. How a caption scores is untried; whisper sometimes invents captions.
  * Nothing here may stop the oracle (see pom/__init__.py). A Scorer that cannot score gives an unscored sentence, which the
    trigger ignores; nothing is raised.
  * What was heard is never logged or put in a report: the sentences live only in the objects this yields.
  * Every part is passed in, already built (the entry point takes their settings from Config); this module never reads Config.
  * Pausing while the oracle writes and the typewriter prints is the Conductor's job: it simply does not take the next
    sentence from `find()` until it is ready to listen again.
"""
from dataclasses import dataclass
from typing import Iterator, Optional

from pom.listening.segment_fragmenter import FragmentLevel
from pom.log_setup import get_logger
from pom.scoring.convo_interest_tracker import ScoreEffect
from pom.scoring.scorer import ScoredBy

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

AMBIENT = "ambient"


@dataclass(frozen=True, slots=True)
class ScoredSentence:
    """One thing heard, with what the Scorer and the trigger made of it.

    Attributes:
        text: The sentence (or the caption of a sound), word for word.
        kind: "speech" or "lyrics" for words, "ambient" for the caption of a sound.
        score: 0 (dull) to 9 (extremely interesting), or None if it was not scored (a caption while ambient sounds are
            not considered, or the Scorer could not).
        scored_by: What made the score (the model, the fragment filter) or left it without one.
        effect: What the trigger did with the score, or None if the trigger never saw it.
        heard_at: Where in the audio stream the words ended, in seconds from the start of listening.
        trigger_level: The bar a score had to clear, as it stood before this one joined the window (None until it exists).
        window_size: How many scores were in the window when this one arrived.
        counted: How many scores the trigger has counted so far, this one included.
    """
    text: str
    kind: str
    score: Optional[float]
    scored_by: ScoredBy
    effect: Optional[ScoreEffect]
    heard_at: float
    trigger_level: Optional[float]
    window_size: int
    counted: int

    @property
    def is_hinge(self) -> bool:
        """True if the trigger fired on this: the sentence to answer."""
        return self.effect is ScoreEffect.TRIGGERED


class HingeFinder:
    """Finds the sentences worth answering in what the room says."""

    def __init__(self, listener, fragmenter, scorer, tracker, consider_ambient_sounds: bool = False):
        """Args:
            listener: A Listener (anything with hear(), close() and report).
            fragmenter: A SegmentFragmenter that cuts words into sentences.
            scorer: A Scorer that scores a fragment, or plain text with score_text().
            tracker: A ConvoInterestTracker that says whether a score is a trigger.
            consider_ambient_sounds: True lets captions of sounds be scored and become hinges; False leaves them unscored.
        """
        self._listener = listener
        self._fragmenter = fragmenter
        self._scorer = scorer
        self._tracker = tracker
        self._consider_ambient = consider_ambient_sounds

    def find(self) -> Iterator[ScoredSentence]:
        """Yields a ScoredSentence for each thing heard, as it is scored. Blocks while waiting for sound.

        Ends when the Listener's sound ends or the finder is closed.
        """
        for heard in self._listener.hear():
            for caption in heard.ambient:
                yield self._sentence(caption, AMBIENT, heard.ends_at, score_it=self._consider_ambient)
            if not heard.words:
                continue
            for fragment in self._fragmenter.split(heard.words, heard_at=heard.ends_at):
                if fragment.level is FragmentLevel.SENTENCE:
                    yield self._sentence(fragment.text, heard.kind, heard.ends_at, fragment=fragment)

    def _sentence(self, text: str, kind: str, now: float, fragment=None, score_it: bool = True) -> ScoredSentence:
        """Scores the text (unless it is a caption that is not to be considered) and asks the trigger about it."""
        bar, held = self._tracker.trigger_level_at(now), self._tracker.window_size_at(now)
        if not score_it:
            return ScoredSentence(text, kind, None, ScoredBy.UNSCORED, None, now, bar, held, self._tracker.counted)
        result = self._scorer.score(fragment) if fragment is not None else self._scorer.score_text(text, now)
        effect = self._tracker.record(result.score, now)
        _log.debug("scored a %s of %d words: %s", kind, len(text.split()), effect.value)
        return ScoredSentence(text, kind, result.score, result.scored_by, effect, now, bar, held, self._tracker.counted)

    def close(self) -> None:
        """Stops listening. Safe to call more than once."""
        self._listener.close()

    def report_lines(self) -> list[str]:
        """The problems met by the listener, the fragmenter, the scorer and the trigger, as lines to show."""
        return [*self._listener.report.lines(), *self._fragmenter.report.lines(), *self._scorer.report.lines(),
                *self._tracker.report.lines()]
