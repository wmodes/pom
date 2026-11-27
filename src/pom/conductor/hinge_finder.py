"""The HingeFinder: sound in, hinges out.

It joins the listening side to the BarKeeper. The Eavesdropper says what was heard; the TakeCutter cuts the words into
sentences; the Scorer scores each sentence; the BarKeeper says whether a score stands out enough to answer.
`find()` yields one Attempt for each thing heard, and the one that qualified (`is_hinge`) is the hinge: the
sentence the oracle will be asked about, word for word.

Design decisions, and why:
  * `find()` is a plain generator, like the Eavesdropper's `hear()`, so this module needs no threads. It blocks while it waits
    for sound and while the Scorer works. The Conductor runs it in a thread of its own, and a testbench can print every
    Attempt, which is why the ones that did not qualify are yielded too.
  * Time is the Eavesdropper's: counted in audio received, so a recording replayed at any speed qualifies exactly as a live run.
  * Only whole sentences are scored; clauses and phrases are not (the same rule the hearing-debug experiment used).
  * ROOM TONE. Room tone like "(dog barks)" is yielded either way, so a testbench can show it. With
    `[listening] score_room_tone` off (the default) it is not scored and the BarKeeper never sees it: `score` is None and
    `ruling` is None. With it on, the room tone is scored like a sentence and counted by the BarKeeper, so a sound can be the
    hinge. How room tone scores is untried; whisper sometimes invents it.
  * Nothing here may stop the oracle (see pom/__init__.py). A Scorer that cannot score gives an unscored sentence, which the
    BarKeeper ignores; nothing is raised.
  * AN ATTEMPT CARRIES CHECKED VALUES. The time a take ended (from the Eavesdropper) and the score (from the Scorer) are checked by the
    finder's ValueChecker before they go to the BarKeeper or into the Attempt (Design Audit 04 item 3), so every part downstream sees the
    same values: a time below 0 is 0 and one that is not a number is the stream time now (`stream_now`); a score below 0 is 0 and one that
    is not a number is None (unscored). Each with a warning.
  * What was heard is never logged: the sentences live only in the objects this yields.
  * Every part is passed in, already built (the entry point takes their settings from Config); this module never reads Config.
  * Pausing while the oracle writes and the typewriter prints is the Conductor's job: it calls the Eavesdropper's `pause()`
    when a turn begins and `skip_ahead()` when it ends. The consumer keeps taking from `find()` the whole time, and while the
    Eavesdropper is paused the rest of the take being cut is dropped unscored, so nothing is sent to the Scorer during a turn.
"""
from typing import Callable, Iterator, Optional

from pom.listening.take_cutter import ClipLevel
from pom.bookkeeper.bookkeeper import Bookkeeper
from pom.defaults import ListeningSettings
from pom.qualifying.attempt import Attempt
from pom.scoring.clip_scorer import ScoredBy
from pom.value_checker import ValueChecker

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module

ROOM_TONE = "room_tone"


class HingeFinder:
    """Finds the sentences worth answering in what the room says.

    Attributes:
        listening: The `[listening]` settings it was handed (it reads `score_room_tone`).
    """

    def __init__(self, eavesdropper, take_cutter, scorer, bar_keeper, listening: ListeningSettings, bookkeeper: Optional[Bookkeeper] = None,
                 stream_now: Optional[Callable[[], float]] = None):
        """Args:
            eavesdropper: An Eavesdropper (anything with hear(), paused and close()).
            take cutter: A TakeCutter that cuts words into sentences.
            scorer: A Scorer that scores a clip, or plain text with score_text().
            bar_keeper: A BarKeeper that says whether a score is a qualifier.
            listening: The `[listening]` section: `score_room_tone`, True lets room tone be scored and become hinges, False leaves it unscored.
            bookkeeper: The Bookkeeper the entry point built; it hands this class its logger. None (a test or a tool) gives a plain one.
            stream_now: The stream time now (the Printer's Timekeeper's `now`): the fallback value for a take's time that is not a number.
                None (a test or a tool) gives a Timekeeper of its own, which runs on real seconds from now.
        """
        self.logger = (bookkeeper or Bookkeeper()).logger_for(__name__, debug=DEBUG)
        self.check = ValueChecker(self.logger, "HingeFinder", stream_now)
        self._eavesdropper = eavesdropper
        self._take_cutter = take_cutter
        self._scorer = scorer
        self._bar_keeper = bar_keeper
        self.listening = listening

    def find(self) -> Iterator[Attempt]:
        """Yields an Attempt for each thing heard, as it is scored. Blocks while waiting for sound.

        Ends when the Eavesdropper's sound ends or the finder is closed.
        """
        for heard in self._eavesdropper.hear():
            for tone in heard.room_tone:
                if self._eavesdropper.paused:
                    break
                yield self._sentence(tone, ROOM_TONE, heard.ends_at, score_it=self.listening.score_room_tone)
            if not heard.words:
                continue
            for clip in self._take_cutter.split(heard.words, heard_at=heard.ends_at):
                if self._eavesdropper.paused:
                    break                  # a turn began: the rest of this take was said before it
                if clip.level is ClipLevel.SENTENCE:
                    yield self._sentence(clip.text, heard.kind, heard.ends_at, clip=clip)

    def _sentence(self, text: str, kind: str, now: float, clip=None, score_it: bool = True) -> Attempt:
        """Scores the text (unless it is room tone that is not to be considered) and asks the BarKeeper about it."""
        now = self.check.stream_time(now, method="find", argument="heard_at")
        bar, held = self._bar_keeper.bar_at(now), self._bar_keeper.window_size_at(now)
        if not score_it:
            return Attempt(text, kind, None, ScoredBy.UNSCORED, None, now, bar, held, self._bar_keeper.counted)
        result = self._scorer.score(clip) if clip is not None else self._scorer.score_text(text, now)
        score = self.check.score(result.score, method="find")
        ruling = self._bar_keeper.rule(score, now)
        self.logger.debug("scored a %s of %d words: %s", kind, len(text.split()), ruling.value)
        return Attempt(text, kind, score, result.scored_by, ruling, now, bar, held, self._bar_keeper.counted)

    def close(self) -> None:
        """Stops listening. Safe to call more than once."""
        self._eavesdropper.close()
