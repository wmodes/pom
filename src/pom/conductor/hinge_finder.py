"""The HingeFinder: sound in, hinges out.

It joins the listening side to the trigger. The Eavesdropper says what was heard; the TakeCutter cuts the words into
sentences; the Scorer scores each sentence; the BarKeeper says whether a score stands out enough to answer.
`find()` yields one Attempt for each thing heard, and the one the trigger fired on (`is_hinge`) is the hinge: the
sentence the oracle will be asked about, word for word.

Design decisions, and why:
  * `find()` is a plain generator, like the Eavesdropper's `hear()`, so this module needs no threads. It blocks while it waits
    for sound and while the Scorer works. The Conductor runs it in a thread of its own, and a playground can print every
    Attempt, which is why the ones that did not trigger are yielded too.
  * Time is the Eavesdropper's: counted in audio received, so a recording replayed at any speed triggers exactly as a live run.
  * Only whole sentences are scored; clauses and phrases are not (the same rule the hearing-debug experiment used).
  * ROOM TONE. A caption like "(dog barks)" is yielded either way, so a playground can show it. With
    `score_room_tone` off (the default) it is not scored and the trigger never sees it: `score` is None and
    `effect` is None. With it on, the caption is scored like a sentence and counted by the trigger, so a sound can be the
    hinge. How a caption scores is untried; whisper sometimes invents captions.
  * Nothing here may stop the oracle (see pom/__init__.py). A Scorer that cannot score gives an unscored sentence, which the
    trigger ignores; nothing is raised.
  * What was heard is never logged or put in a report: the sentences live only in the objects this yields.
  * Every part is passed in, already built (the entry point takes their settings from Config); this module never reads Config.
  * Pausing while the oracle writes and the typewriter prints is the Conductor's job: it simply does not take the next
    sentence from `find()` until it is ready to listen again.
"""
from typing import Iterator, Optional

from pom.listening.take_cutter import ClipLevel
from pom.log_setup import get_logger
from pom.qualifying.attempt import Attempt
from pom.qualifying.bar_keeper import Ruling
from pom.scoring.clip_scorer import ScoredBy

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

ROOM_TONE = "room_tone"


class HingeFinder:
    """Finds the sentences worth answering in what the room says."""

    def __init__(self, eavesdropper, take_cutter, scorer, tracker, score_room_tone: bool = False):
        """Args:
            eavesdropper: An Eavesdropper (anything with hear(), close() and report).
            take cutter: A TakeCutter that cuts words into sentences.
            scorer: A Scorer that scores a clip, or plain text with score_text().
            tracker: A BarKeeper that says whether a score is a trigger.
            score_room_tone: True lets captions of sounds be scored and become hinges; False leaves them unscored.
        """
        self._eavesdropper = eavesdropper
        self._take_cutter = take_cutter
        self._scorer = scorer
        self._tracker = tracker
        self._score_room_tone = score_room_tone

    def find(self) -> Iterator[Attempt]:
        """Yields an Attempt for each thing heard, as it is scored. Blocks while waiting for sound.

        Ends when the Eavesdropper's sound ends or the finder is closed.
        """
        for heard in self._eavesdropper.hear():
            for caption in heard.room_tone:
                yield self._sentence(caption, ROOM_TONE, heard.ends_at, score_it=self._score_room_tone)
            if not heard.words:
                continue
            for clip in self._take_cutter.split(heard.words, heard_at=heard.ends_at):
                if clip.level is ClipLevel.SENTENCE:
                    yield self._sentence(clip.text, heard.kind, heard.ends_at, clip=clip)

    def _sentence(self, text: str, kind: str, now: float, clip=None, score_it: bool = True) -> Attempt:
        """Scores the text (unless it is a caption that is not to be considered) and asks the trigger about it."""
        bar, held = self._tracker.bar_at(now), self._tracker.window_size_at(now)
        if not score_it:
            return Attempt(text, kind, None, ScoredBy.UNSCORED, None, now, bar, held, self._tracker.counted)
        result = self._scorer.score(clip) if clip is not None else self._scorer.score_text(text, now)
        effect = self._tracker.rule(result.score, now)
        _log.debug("scored a %s of %d words: %s", kind, len(text.split()), effect.value)
        return Attempt(text, kind, result.score, result.scored_by, effect, now, bar, held, self._tracker.counted)

    def close(self) -> None:
        """Stops listening. Safe to call more than once."""
        self._eavesdropper.close()

    def report_lines(self) -> list[str]:
        """The problems met by the eavesdropper, the take cutter, the scorer and the trigger, as lines to show."""
        return [*self._eavesdropper.report.lines(), *self._take_cutter.report.lines(), *self._scorer.report.lines(),
                *self._tracker.report.lines()]
