"""The Eavesdropper: sound in, what was heard out.

It joins the parts of the listening side. Sound comes from an AudioDeck; the RollingCutter feeds it to whisper
every few seconds and keeps the segments whisper finished; the TrackSplitter sorts each segment into words, room tone
captions and nothing; and `hear()` yields one Take for each segment that holds something new. What happens next
(cutting, scoring, qualifying) is for the modules after it.

Two things beyond the cutter, both from the hearing-debug experiment:
  * REPEATS. Words identical to the last words are skipped, and captions identical to the last captions are not
    reported again, because whisper sometimes says the same thing twice across a boundary or keeps hearing the same
    sound. The captions are also remembered (the last `room_tone_memory` of them, `recent_room_tone`), to give the oracle
    model as context later.
  * THE ROOM-TONE PASS. The voice detector treats singing and most sounds as not speech. The Printer reacts to what it hears,
    so after `room_tone_pass_every` rounds in a row that found no speech, whisper is asked about the last `room_tone_pass_seconds` of
    audio WITHOUT the voice detector, and only lyrics and captions are kept (plain text from that pass is where whisper
    makes things up, so it is thrown away). `room_tone_pass_every` 0 turns the pass off.
    KNOWN LIMIT, inherited from the experiment: the pass can only hear what the cutter still holds, and a stretch with no
    speech is trimmed to its last two seconds before the pass runs, so in practice it listens to about two seconds, not
    `room_tone_pass_seconds`. Hearing the whole stretch would need the Eavesdropper to keep its own recent audio.

Design decisions, and why:
  * `hear()` is a plain generator, so this module needs no threads and no event loop. It blocks while it waits for sound
    and while whisper works. The Conductor runs it in a thread of its own. A MicDeck reads the microphone in a
    thread of its own, so sound is not lost while the Eavesdropper waits for whisper.
  * Time is counted in audio received, not wall-clock time, so a recording replayed at any speed is heard exactly as a
    live stream would be. `ends_at` is the place in the stream where the segment ended, in seconds; it is the time the
    BarKeeper is given. (In a fast replay the room-tone pass looks at the audio received when the round ran, which is also what a
    live run would have; the hearing-debug experiment, whose audio thread ran ahead, looked further on.)
  * Nothing here may stop the oracle (see pom/__init__.py). Whisper being down is a warning in `report`, once for each
    outage, and the audio is kept and tried again. A bad setting is replaced by its default and recorded.
    `strict=True` raises EavesdropperError instead, for tests and dev tools.
  * Neither what was said nor the audio is logged or put in a report.
  * The settings are passed in (the entry point takes them from Config); this module never reads Config.
"""
import collections
import math
from dataclasses import dataclass
from typing import Iterator, Optional, Tuple

from pom.fault_recorder import FaultRecorder
from pom.listening.audio_deck import AudioDeck
from pom.listening.rolling_cutter import (DEFAULT_HOLD_BACK_SECONDS, DEFAULT_MAX_CUT_SECONDS, DEFAULT_REWIND_SECONDS, DEFAULT_CUT_EVERY_SECONDS, RollingCutter)
from pom.listening.track_splitter import TrackSplitter
from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_ROOM_TONE_PASS_EVERY = 3
DEFAULT_ROOM_TONE_PASS_SECONDS = 10.0
DEFAULT_ROOM_TONE_MEMORY = 6


class EavesdropperError(ValueError):
    """Raised for any eavesdropper problem, but only in strict mode (development and tests)."""


@dataclass(frozen=True)
class Take:
    """Something new that was heard.

    Attributes:
        words: What was said or sung, ready to be cut into clips and scored. "" if this segment held only captions.
        kind: "speech" or "lyrics" for the words; "" if there are none.
        room_tone: Captions of sounds that are new since the last segment, such as "(dog barks)". Never scored.
        ends_at: Where in the audio stream the segment ended, in seconds from the start of listening.
    """
    words: str
    kind: str
    room_tone: Tuple[str, ...]
    ends_at: float


def _is_whole(value, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class Eavesdropper:
    """Turns a deck into the things that were said, sung or sounded in the room.

    Attributes:
        report: Problems met (a bad setting, whisper not answering). Includes the cutter's.
    """

    def __init__(self, deck: AudioDeck, client, cut_every_seconds: float = DEFAULT_CUT_EVERY_SECONDS, hold_back_seconds: float = DEFAULT_HOLD_BACK_SECONDS,
                 rewind_seconds: float = DEFAULT_REWIND_SECONDS, max_cut_seconds: float = DEFAULT_MAX_CUT_SECONDS,
                 room_tone_pass_every: int = DEFAULT_ROOM_TONE_PASS_EVERY, room_tone_pass_seconds: float = DEFAULT_ROOM_TONE_PASS_SECONDS,
                 room_tone_memory: int = DEFAULT_ROOM_TONE_MEMORY, splitter: Optional[TrackSplitter] = None,
                 cutter: Optional[RollingCutter] = None, strict: bool = False):
        """Args:
            deck: Where the sound comes from (a MicDeck, or a WavDeck for a replay).
            client: Asks whisper (a WhisperModelClient, or anything with its `segments`, `text` and `error`).
            step, hold, rewind, max_cut_seconds: The RollingCutter's settings (see there).
            room_tone_pass_every: After this many rounds in a row with no speech, listen once for lyrics and captions.
                A whole number of at least 0; 0 turns the room-tone pass off.
            room_tone_pass_seconds: How much recent audio the room-tone pass listens to, above 0.
            room_tone_memory: How many recent captions to remember, a whole number of at least 1.
            splitter: The TrackSplitter to use. Default: a new one.
            cutter: A RollingCutter to use instead of making one (for tests).
            strict: Raise EavesdropperError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=EavesdropperError, logger=_log)
        self._deck, self._client = deck, client
        self._room_tone_every = self._setting(room_tone_pass_every, "room_tone_pass_every", DEFAULT_ROOM_TONE_PASS_EVERY,
                                          lambda v: _is_whole(v, 0), "a whole number of at least 0")
        self._room_tone_pass_seconds = self._setting(room_tone_pass_seconds, "room_tone_pass_seconds", DEFAULT_ROOM_TONE_PASS_SECONDS,
                                            lambda v: _is_number(v) and v > 0, "a number above 0")
        memory = self._setting(room_tone_memory, "room_tone_memory", DEFAULT_ROOM_TONE_MEMORY,
                               lambda v: _is_whole(v, 1), "a whole number of at least 1")
        self._splitter = splitter if splitter is not None else TrackSplitter()
        self._cutter = cutter if cutter is not None else RollingCutter(client, cut_every_seconds, hold_back_seconds, rewind_seconds, max_cut_seconds, strict=strict)
        if cutter is None:
            self.report.extend(self._cutter.report)
        self._recent_room_tone = collections.deque(maxlen=memory)
        self._last_words = ""
        self._last_room_tone: Tuple[str, ...] = ()
        self._quiet_steps = 0
        self._whisper_down = False

    def _setting(self, value, name: str, default, is_good, wanted: str):
        """The value if it passes `is_good`, otherwise the default with a warning."""
        if is_good(value):
            return value
        self.report.warn(f"{name} must be {wanted}, got {value!r}; using {default:g}")
        return default

    @property
    def recent_room_tone(self) -> Tuple[str, ...]:
        """The last few captions of sounds in the room, oldest first, for the oracle model's context."""
        return tuple(self._recent_room_tone)

    def hear(self) -> Iterator[Take]:
        """Listens until the sound ends (a recording runs out, the microphone stops, or `close` is called).

        Yields each Take as whisper finishes it, a few seconds after it was said. It blocks while waiting.
        Whatever whisper had not yet finished when the sound ended is yielded last.
        """
        try:
            for frame in self._deck.frames():
                self._cutter.feed(frame)
                if self._cutter.due():
                    yield from self._round(final=False)
            yield from self._round(final=True)
        finally:
            self._deck.close()

    def close(self) -> None:
        """Stops listening: closes the deck, so `hear` finishes. Safe to call more than once, from another thread."""
        self._deck.close()

    def _round(self, final: bool) -> Iterator[Take]:
        """One whisper round: yields what is new in the segments it finished, or tries the room-tone pass."""
        segments = self._cutter.cut(final=final)
        self._note_whisper()
        for segment in segments:
            item = self._news(self._splitter.split(segment.text), segment.end)
            if item is not None:
                yield item
        if segments:
            self._quiet_steps = 0
        elif self._room_tone_every and not self._whisper_down:
            self._quiet_steps += 1
            if self._quiet_steps % self._room_tone_every == 0:
                text = self._client.text(self._cutter.recent(self._room_tone_pass_seconds), vad=False)
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
            self.report.warn(f"whisper is not answering ({error}); the audio is kept and tried again")
        elif not error and self._whisper_down:
            self._whisper_down = False
            _log.info("whisper is answering again")
