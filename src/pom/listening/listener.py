"""The Listener: sound in, what was heard out.

It joins the parts of the listening side. Sound comes from an AudioSource; the RollingCutter feeds it to whisper
every few seconds and keeps the segments whisper finished; the WhisperTextSorter sorts each segment into words, ambient
captions and nothing; and `hear()` yields one HeardSegment for each segment that holds something new. What happens next
(fragmenting, scoring, the trigger) is for the modules after it.

Two things beyond the cutter, both from the hearing-debug experiment:
  * REPEATS. Words identical to the last words are skipped, and captions identical to the last captions are not
    reported again, because whisper sometimes says the same thing twice across a boundary or keeps hearing the same
    sound. The captions are also remembered (the last `ambient_memory` of them, `recent_ambient`), to give the oracle
    model as context later.
  * THE SOUND PASS. The voice detector treats singing and most sounds as not speech. The Printer reacts to what it hears,
    so after `sound_every_steps` rounds in a row that found no speech, whisper is asked about the last `sound_seconds` of
    audio WITHOUT the voice detector, and only lyrics and captions are kept (plain text from that pass is where whisper
    makes things up, so it is thrown away). `sound_every_steps` 0 turns the pass off.
    KNOWN LIMIT, inherited from the experiment: the pass can only hear what the cutter still holds, and a stretch with no
    speech is trimmed to its last two seconds before the pass runs, so in practice it listens to about two seconds, not
    `sound_seconds`. Hearing the whole stretch would need the Listener to keep its own recent audio.

Design decisions, and why:
  * `hear()` is a plain generator, so this module needs no threads and no event loop. It blocks while it waits for sound
    and while whisper works. The Conductor runs it in a thread of its own. A MicSource reads the microphone in a
    thread of its own, so sound is not lost while the Listener waits for whisper.
  * Time is counted in audio received, not wall-clock time, so a recording replayed at any speed is heard exactly as a
    live stream would be. `ends_at` is the place in the stream where the segment ended, in seconds; it is the time the
    trigger is given. (In a fast replay the sound pass looks at the audio received when the round ran, which is also what a
    live run would have; the hearing-debug experiment, whose audio thread ran ahead, looked further on.)
  * Nothing here may stop the oracle (see pom/__init__.py). Whisper being down is a warning in `report`, once for each
    outage, and the audio is kept and tried again. A bad setting is replaced by its default and recorded.
    `strict=True` raises ListenerError instead, for tests and dev tools.
  * Neither what was said nor the audio is logged or put in a report.
  * The settings are passed in (the entry point takes them from Config); this module never reads Config.
"""
import collections
import math
from dataclasses import dataclass
from typing import Iterator, Optional, Tuple

from pom.fault_recorder import FaultRecorder
from pom.listening.audio_source import AudioSource
from pom.listening.rolling_cutter import (DEFAULT_HOLD, DEFAULT_MAX_BUFFER, DEFAULT_REWIND, DEFAULT_STEP, RollingCutter)
from pom.listening.whisper_text_sorter import WhisperTextSorter
from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_SOUND_EVERY_STEPS = 3
DEFAULT_SOUND_SECONDS = 10.0
DEFAULT_AMBIENT_MEMORY = 6


class ListenerError(ValueError):
    """Raised for any listener problem, but only in strict mode (development and tests)."""


@dataclass(frozen=True)
class HeardSegment:
    """Something new that was heard.

    Attributes:
        words: What was said or sung, ready to be fragmented and scored. "" if this segment held only captions.
        kind: "speech" or "lyrics" for the words; "" if there are none.
        ambient: Captions of sounds that are new since the last segment, such as "(dog barks)". Never scored.
        ends_at: Where in the audio stream the segment ended, in seconds from the start of listening.
    """
    words: str
    kind: str
    ambient: Tuple[str, ...]
    ends_at: float


def _is_whole(value, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class Listener:
    """Turns an audio source into the things that were said, sung or sounded in the room.

    Attributes:
        report: Problems met (a bad setting, whisper not answering). Includes the cutter's.
    """

    def __init__(self, source: AudioSource, client, step: float = DEFAULT_STEP, hold: float = DEFAULT_HOLD,
                 rewind: float = DEFAULT_REWIND, max_buffer: float = DEFAULT_MAX_BUFFER,
                 sound_every_steps: int = DEFAULT_SOUND_EVERY_STEPS, sound_seconds: float = DEFAULT_SOUND_SECONDS,
                 ambient_memory: int = DEFAULT_AMBIENT_MEMORY, sorter: Optional[WhisperTextSorter] = None,
                 cutter: Optional[RollingCutter] = None, strict: bool = False):
        """Args:
            source: Where the sound comes from (a MicSource, or a WavSource for a replay).
            client: Asks whisper (a WhisperServerClient, or anything with its `segments`, `text` and `error`).
            step, hold, rewind, max_buffer: The RollingCutter's settings (see there).
            sound_every_steps: After this many rounds in a row with no speech, listen once for lyrics and captions.
                A whole number of at least 0; 0 turns the sound pass off.
            sound_seconds: How much recent audio the sound pass listens to, above 0.
            ambient_memory: How many recent captions to remember, a whole number of at least 1.
            sorter: The WhisperTextSorter to use. Default: a new one.
            cutter: A RollingCutter to use instead of making one (for tests).
            strict: Raise ListenerError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=ListenerError, logger=_log)
        self._source, self._client = source, client
        self._sound_every = self._setting(sound_every_steps, "sound_every_steps", DEFAULT_SOUND_EVERY_STEPS,
                                          lambda v: _is_whole(v, 0), "a whole number of at least 0")
        self._sound_seconds = self._setting(sound_seconds, "sound_seconds", DEFAULT_SOUND_SECONDS,
                                            lambda v: _is_number(v) and v > 0, "a number above 0")
        memory = self._setting(ambient_memory, "ambient_memory", DEFAULT_AMBIENT_MEMORY,
                               lambda v: _is_whole(v, 1), "a whole number of at least 1")
        self._sorter = sorter if sorter is not None else WhisperTextSorter()
        self._cutter = cutter if cutter is not None else RollingCutter(client, step, hold, rewind, max_buffer, strict=strict)
        if cutter is None:
            self.report.extend(self._cutter.report)
        self._recent_ambient = collections.deque(maxlen=memory)
        self._last_words = ""
        self._last_ambient: Tuple[str, ...] = ()
        self._quiet_steps = 0
        self._whisper_down = False

    def _setting(self, value, name: str, default, is_good, wanted: str):
        """The value if it passes `is_good`, otherwise the default with a warning."""
        if is_good(value):
            return value
        self.report.warn(f"{name} must be {wanted}, got {value!r}; using {default:g}")
        return default

    @property
    def recent_ambient(self) -> Tuple[str, ...]:
        """The last few captions of sounds in the room, oldest first, for the oracle model's context."""
        return tuple(self._recent_ambient)

    def hear(self) -> Iterator[HeardSegment]:
        """Listens until the sound ends (a recording runs out, the microphone stops, or `close` is called).

        Yields each HeardSegment as whisper finishes it, a few seconds after it was said. It blocks while waiting.
        Whatever whisper had not yet finished when the sound ended is yielded last.
        """
        try:
            for frame in self._source.frames():
                self._cutter.feed(frame)
                if self._cutter.due():
                    yield from self._round(final=False)
            yield from self._round(final=True)
        finally:
            self._source.close()

    def close(self) -> None:
        """Stops listening: closes the source, so `hear` finishes. Safe to call more than once, from another thread."""
        self._source.close()

    def _round(self, final: bool) -> Iterator[HeardSegment]:
        """One whisper round: yields what is new in the segments it finished, or tries the sound pass."""
        segments = self._cutter.cut(final=final)
        self._note_whisper()
        for segment in segments:
            item = self._news(self._sorter.sort(segment.text), segment.end)
            if item is not None:
                yield item
        if segments:
            self._quiet_steps = 0
        elif self._sound_every and not self._whisper_down:
            self._quiet_steps += 1
            if self._quiet_steps % self._sound_every == 0:
                text = self._client.text(self._cutter.recent(self._sound_seconds), vad=False)
                item = self._news(self._sorter.sort(text, plain_text=False), self._cutter.total_seconds)
                if item is not None:
                    yield item

    def _news(self, heard, ends_at: float) -> Optional[HeardSegment]:
        """The part of `heard` that is new since the last segment, or None if there is nothing new."""
        ambient = ()
        if heard.ambient and heard.ambient != self._last_ambient:
            self._last_ambient = heard.ambient
            self._recent_ambient.extend(heard.ambient)
            ambient = heard.ambient
        words = heard.words if heard.words and heard.words != self._last_words else ""
        if words:
            self._last_words = words
        if not words and not ambient:
            return None
        return HeardSegment(words, heard.kind if words else "", ambient, ends_at)

    def _note_whisper(self) -> None:
        """Says so, once, when whisper stops answering; and notes when it is back."""
        error = self._client.error
        if error and not self._whisper_down:
            self._whisper_down = True
            self.report.warn(f"whisper is not answering ({error}); the audio is kept and tried again")
        elif not error and self._whisper_down:
            self._whisper_down = False
            _log.info("whisper is answering again")
