"""The Eavesdropper: sound in, what was heard out.

It joins the parts of the listening side. Sound comes from an AudioDeck; the RollingCutter feeds it to whisper
every few seconds and keeps the segments whisper finished; the TrackSplitter splits each segment into words, room tone
and blanks; and `hear()` yields one Take for each segment that holds something new. What happens next
(cutting, scoring, qualifying) is for the modules after it.

Two things beyond the cutter, both from the hearing-debug experiment:
  * REPEATS. Words identical to the last words are skipped, and room tone identical to the last room tone is not
    reported again, because whisper sometimes says the same thing twice across a boundary or keeps hearing the same
    sound. The room tone is also remembered (the last `room_tone_memory` of them, `recent_room_tone`), to give the oracle
    model as context later.
  * THE ROOM-TONE PASS. The voice detector treats singing and most sounds as not speech. The Printer reacts to what it hears,
    so after `room_tone_pass_every` rounds in a row that found no speech, whisper is asked about the last `room_tone_pass_seconds` of
    audio WITHOUT the voice detector, and only lyrics and room tone are kept (plain text from that pass is where whisper
    makes things up, so it is thrown away). `room_tone_pass_every` 0 turns the pass off.
    KNOWN LIMIT, inherited from the experiment: the pass can only hear what the cutter still holds, and a stretch with no
    speech is trimmed to its last two seconds before the pass runs, so in practice it listens to about two seconds, not
    `room_tone_pass_seconds`. Hearing the whole stretch would need the Eavesdropper to keep its own recent audio.
  * PAUSE AND SKIP AHEAD, like a tape deck. While the Printer takes a turn (writing and printing) it must not call whisper and must
    not later hear its own printing. `pause()` stops the listening: the thread in `hear()` keeps reading frames from the deck,
    so the deck's buffer never overflows, but throws them away and asks whisper nothing; whatever whisper had already
    transcribed and not yet handed on is dropped too, because it was said before the turn. `skip_ahead()` ends the pause with a
    clean slate: the cutter forgets its audio and any half-finished segment, the repeat memory is cleared, and the cutter's clock moves
    forward by the sound that went by unheard, so audio time keeps matching real time and the BarKeeper's cooldown and the bar's
    memory count the turn. Both calls only set a flag and return at once, from any thread; the thread in `hear()` does the dropping
    itself, so no state is touched by two threads. (A whisper request already in flight finishes first, and its result is dropped.)

Design decisions, and why:
  * `hear()` is a plain generator, so this module needs no threads and no event loop. It blocks while it waits for sound
    and while whisper works. The Conductor runs it in a thread of its own. A MicDeck reads the microphone in a
    thread of its own, so sound is not lost while the Eavesdropper waits for whisper.
  * THE STREAM CLOCK CARRIES ON ACROSS A RESTART. If the room has to be heard again (this Eavesdropper is replaced after a failure), the new one is given
    `start_seconds`, where the stream clock stands now: the Timekeeper (pom.timekeeper), which follows the room that is listening and reads its
    `stream_time_at` (the old clock, the sound that went by unheard, and the real seconds since sound last arrived), so stream time keeps matching real
    time, the same idea as `skip_ahead` after a turn. A clock that began again at 0 would run behind the BarKeeper's,
    which holds time at the latest it has seen, so its cooldown and the bar's fading would stand still until the new clock caught up (two hours in, about two hours).
  * Time is counted in audio received, not wall-clock time, so a recording replayed at any speed is heard exactly as a
    live stream would be. `ends_at` is the place in the stream where the segment ended, in seconds; it is the time the
    BarKeeper is given. (In a fast replay the room-tone pass looks at the audio received when the round ran, which is also what a
    live run would have; the hearing-debug experiment, whose audio thread ran ahead, looked further on.)
  * Nothing here may stop the oracle (see pom/__init__.py). Whisper being down is a warning in the log, once for each
    outage, and the audio is kept and tried again. A bad setting is replaced by its default and recorded.
    `strict=True` raises EavesdropperError instead, for tests and dev tools.
  * Neither what was said nor the audio is logged.
  * The settings are passed in (the entry point takes them from Config); this module never reads Config.
"""
import collections
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterator, Optional, Tuple

from pom.bookkeeper.bookkeeper import Bookkeeper
from pom.defaults import ListeningSettings
from pom.listening.audio_deck import AudioDeck
from pom.listening.audio_format import FRAME_SECONDS
from pom.listening.rolling_cutter import RollingCutter
from pom.listening.track_splitter import TrackSplitter
from pom.value_checker import ValueChecker

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module



class EavesdropperError(ValueError):
    """Raised for any eavesdropper problem, but only in strict mode (development and tests)."""


@dataclass(frozen=True)
class Take:
    """Something new that was heard.

    Attributes:
        words: What was said or sung, ready to be cut into clips and scored. "" if this segment held only room tone.
        kind: "speech" or "lyrics" for the words; "" if there are none.
        room_tone: Room tone that is new since the last segment, such as "(dog barks)". Never scored.
        ends_at: Where in the audio stream the segment ended, in seconds from the start of listening.
    """
    words: str
    kind: str
    room_tone: Tuple[str, ...]
    ends_at: float


class Eavesdropper:
    """Turns a deck into the things that were said, sung or sounded in the room.

    Attributes:
        logger: Where this class says what it has to say (a LogRecorder from the Bookkeeper).
        listening: The `[listening]` settings it was handed (it reads the room-tone settings and hands it to its RollingCutter).
    """

    def __init__(self, deck: AudioDeck, client, listening: ListeningSettings, splitter: Optional[TrackSplitter] = None,
                 cutter: Optional[RollingCutter] = None, start_seconds: float = 0.0, wall: Callable[[], float] = time.monotonic,
                 strict: Optional[bool] = None, bookkeeper: Optional[Bookkeeper] = None, stream_now: Optional[Callable[[], float]] = None):
        """Args:
            deck: Where the sound comes from (a MicDeck, or a WavDeck for a replay).
            client: Asks whisper (a WhisperModelClient, or anything with its `segments`, `text` and `error`).
            listening: The `[listening]` section: `room_tone_pass_every` (after this many rounds in a row with no speech, listen once for
                lyrics and room tone; 0 turns the pass off), `room_tone_pass_seconds` (how much recent audio that pass listens to),
                `room_tone_memory` (how many recent bits of room tone to remember), and the RollingCutter's own settings (see there).
            splitter: The TrackSplitter to use. Default: a new one.
            cutter: A RollingCutter to use instead of making one (for tests).
            start_seconds: Where the stream clock begins, a stream time: 0 for a first room, and for one that replaces a room that stopped, what the
                old one's `stream_time_at` gave, so the clock carries on. Checked like every stream time (Design Audit 04 item 3): below 0 is 0,
                and one that is not a number is the stream time now (`stream_now`), with a warning.
            wall: Returns seconds that steadily increase, for noting when sound last arrived (real time, not stream time). Injected so tests need not wait.
            strict: Raise EavesdropperError on any problem instead of logging it; None (the default) follows the project's `strict` setting.
            bookkeeper: The Bookkeeper the entry point built; it hands this class its logger. None (a test or a tool) gives a plain one.
            stream_now: The stream time now (the Printer's Timekeeper's `now`, which has not begun to follow this room yet): the fallback value
                for a `start_seconds` that is not a number. None (a test or a tool) gives a clock of its own, which begins at 0.
        """
        self.logger = (bookkeeper or Bookkeeper()).logger_for(__name__, EavesdropperError, DEBUG, strict)
        self.check = ValueChecker(self.logger, "Eavesdropper", stream_now)
        self._deck, self._client = deck, client
        self.listening = listening
        self._splitter = splitter if splitter is not None else TrackSplitter()
        self._cutter = cutter if cutter is not None else RollingCutter(client, listening, strict=strict, bookkeeper=bookkeeper)
        self._recent_room_tone = collections.deque(maxlen=listening.room_tone_memory)
        self._last_words = ""
        self._last_room_tone: Tuple[str, ...] = ()
        self._quiet_steps = 0
        self._whisper_down = False
        self._paused = threading.Event()          # set by pause(), cleared by skip_ahead(); read by the thread in hear()
        self._skip_wanted = threading.Event()     # set by skip_ahead(); cleared by the thread in hear() when it has done the skipping
        self._unheard_frames = 0                  # frames thrown away while paused (only the thread in hear() touches this)
        self._wall = wall
        self._last_sound_at = wall()              # the real time sound last arrived (or this was made, if none has): it is how long a restart took that is wanted
        start = self.check.stream_time(start_seconds, method="__init__", argument="start_seconds")
        if start > 0:
            self._cutter.skip_ahead(start)        # a fresh cutter moved on by `start`: the clock begins there

    @property
    def recent_room_tone(self) -> Tuple[str, ...]:
        """The last few bits of room tone, oldest first, for the oracle model's context."""
        return tuple(self._recent_room_tone)

    @property
    def paused(self) -> bool:
        """True from `pause()` until `skip_ahead()`: nothing is being sent to whisper, and the sound is being thrown away."""
        return self._paused.is_set()

    def pause(self) -> None:
        """Stops listening (for a turn): see PAUSE AND SKIP AHEAD above. Returns at once; safe from any thread and safe to repeat."""
        self._paused.set()

    def skip_ahead(self) -> None:
        """Ends a pause with a clean slate, forgetting the sound that went by (see above). Returns at once; safe from any thread.

        Without a `pause()` before it, it forgets what is buffered and carries on.
        """
        self._skip_wanted.set()       # first, so a thread that sees the pause cleared also sees the request
        self._paused.clear()

    def stream_time_at(self, wall_now: float) -> float:
        """Where the stream clock stands at real time `wall_now` (the same clock as `wall`), as if the sound had gone on.

        That is the audio received, plus the sound that went by unheard while paused, plus the real seconds since sound last arrived. A room that has
        stopped is asked this when it is replaced, and the answer is what the new one's clock begins at.
        """
        return self._cutter.total_seconds + self._unheard_frames * FRAME_SECONDS + max(0.0, wall_now - self._last_sound_at)

    def hear(self) -> Iterator[Take]:
        """Listens until the sound ends (a recording runs out, the microphone stops, or `close` is called).

        Yields each Take as whisper finishes it, a few seconds after it was said. It blocks while waiting.
        Whatever whisper had not yet finished when the sound ended is yielded last (unless paused).
        """
        try:
            for frame in self._deck.frames():
                self._last_sound_at = self._wall()
                if self._paused.is_set():
                    self._unheard_frames += 1         # the room goes on while the Printer is busy
                    continue
                if self._skip_wanted.is_set():
                    self._skip_wanted.clear()         # before skipping, so a request made meanwhile is not lost
                    self._forget_what_went_by()
                self._cutter.feed(frame)
                if self._cutter.due():
                    yield from self._round(final=False)
            if not self._paused.is_set():
                yield from self._round(final=True)
        finally:
            self._deck.close()

    def _forget_what_went_by(self) -> None:
        """Does a skip_ahead: the cutter forgets its audio and moves its clock on by the unheard sound; the repeat memory is cleared."""
        self._cutter.skip_ahead(self._unheard_frames * FRAME_SECONDS)
        self._unheard_frames = 0
        self._last_words, self._last_room_tone = "", ()
        self._quiet_steps = 0

    def close(self) -> None:
        """Stops listening: closes the deck, so `hear` finishes. Safe to call more than once, from another thread."""
        self._deck.close()

    def _round(self, final: bool) -> Iterator[Take]:
        """One whisper round: yields what is new in the segments it finished, or tries the room-tone pass."""
        segments = self._cutter.cut(final=final)
        self._note_whisper()
        for segment in segments:
            if self._paused.is_set() or self._skip_wanted.is_set():
                break                    # a turn began: what was transcribed but not yet handed on was said before it
            item = self._news(self._splitter.split(segment.text), segment.end)
            if item is not None:
                yield item
        if segments:
            self._quiet_steps = 0
        elif self.listening.room_tone_pass_every and not self._whisper_down and not self._paused.is_set():
            self._quiet_steps += 1
            if self._quiet_steps % self.listening.room_tone_pass_every == 0:
                text = self._client.text(self._cutter.recent(self.listening.room_tone_pass_seconds), vad=False)
                item = self._news(self._splitter.split(text, plain_text=False), self._cutter.total_seconds)
                if item is not None:
                    yield item

    def _news(self, heard, ends_at: float) -> Optional[Take]:
        """The part of `heard` that is new since the last segment, or None if there is nothing new."""
        room_tone = ()
        if heard.room_tone and heard.room_tone != self._last_room_tone:
            self._last_room_tone = heard.room_tone
            self._recent_room_tone.extend(heard.room_tone)
            room_tone = heard.room_tone
        words = heard.words if heard.words and heard.words != self._last_words else ""
        if words:
            self._last_words = words
        if not words and not room_tone:
            return None
        return Take(words, heard.kind if words else "", room_tone, ends_at)

    def _note_whisper(self) -> None:
        """Says so, once, when whisper stops answering; and notes when it is back."""
        error = self._client.error
        if error and not self._whisper_down:
            self._whisper_down = True
            self.logger.warning("whisper is not answering (%s); the audio is kept and tried again", error)
        elif not error and self._whisper_down:
            self._whisper_down = False
            self.logger.info("whisper is answering again")
