"""The RollingCutter: lets whisper's own voice detection decide where to cut the audio stream.

Cutting the stream by loudness (a pause after speech) fails in a room with bursty noise, a hookah, a cat or the street,
which never lets it find a pause, so it ends up cutting by the clock in the middle of a thought. This module hands
whisper a rolling stretch of audio every `cut_every_seconds` and keeps the segments whisper itself finished. Whisper decides
what is speech and where its segments end.

What this adds around whisper, and why:
  * The audio after the last kept segment stays in the buffer for the next round, so a thought that was still going is
    not cut in two. A segment is kept once it ends at least `hold_back_seconds` before the end of the buffer.
  * A segment that does not end like a sentence (no . ? or !) is joined to the one after it first, because whisper
    sometimes breaks at a pause in the middle of a sentence.
  * Optional, off by default (`rewind_seconds` 0): the next round starts `rewind_seconds` before the last kept segment's end, in
    case whisper's segment ends run slightly late and clip the next sentence's first syllables. Segments that end within
    that stretch are the old ones heard again and are dropped. Live, it also re-heard the last word of the previous
    segment as the start of the next ("yourself." twice), which is why it is off.
  * A buffer with no speech in it is trimmed to its last two seconds, so noise does not pile up.
  * A buffer that grows past `max_cut_seconds` without a finished segment is forced out, so one very long thought
    cannot stall everything.

Design decisions, and why:
  * Time is counted in audio received, not in wall-clock time, so a recording replayed at any speed is cut exactly as a
    live stream would be. The one clock reading is how long whisper took, for display (`took`), and it is injected.
  * Nothing here may stop the oracle (see pom/__init__.py). If whisper cannot be reached the round returns nothing and
    the audio stays in the buffer, so the next round tries it again; a client that raises is treated the same way. A bad
    setting is replaced by its default and logged. `strict=True` raises RollingCutterError instead, for
    tests and dev tools.
  * Audio is only held in memory, and neither audio nor text is logged.
  * `feed` and `cut` may be called from different threads; a lock guards the buffer.
  * The settings are passed in (the entry point takes them from Config); this module never reads Config.
"""
import re
import threading
import time
from typing import Callable, List, Optional, Protocol

from pom.bookkeeper.bookkeeper import Bookkeeper
from pom.defaults import ListeningSettings
from pom.listening.audio_format import BYTES_PER_SECOND, SAMPLE_BYTES
from pom.listening.whisper_model_client import Segment
from pom.value_rules import is_number

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module

_FINISHED = re.compile(r"[.?!][\"')\]]*$")             # ends like a sentence
_LONGEST_JOIN = 25.0                                    # seconds: the longest a joined segment may grow
_NOISE_KEPT = 2.0                                       # seconds of a buffer with no speech that are kept
_SHORTEST_WINDOW = 1.0                                  # not worth asking whisper about less
_REHEARD_SLACK = 0.2                                    # seconds: a segment must end this much past the last kept one


class RollingCutterError(ValueError):
    """Raised for any cutter problem, but only in strict mode (development and tests)."""


class SegmentFinder(Protocol):
    """What the cutter needs from whisper: the WhisperModelClient has it."""
    error: Optional[str]

    def segments(self, pcm: bytes, offset_s: float = 0.0, vad: bool = True) -> List[Segment]:
        """Whisper's segments for this audio, shifted by `offset_s`; [] if none or the request failed."""


def join_unfinished(found: List[Segment], longest: float = _LONGEST_JOIN) -> List[Segment]:
    """Joins each segment that does not end like a sentence to the one after it (up to `longest` seconds)."""
    out: List[Segment] = []
    for begin, finish, text in found:
        if out and not _FINISHED.search(out[-1].text) and finish - out[-1].start <= longest:
            out[-1] = Segment(out[-1].start, finish, out[-1].text + " " + text)
        else:
            out.append(Segment(begin, finish, text))
    return out


class RollingCutter:
    """Feed it audio; ask it for the segments whisper has finished.

    Attributes:
        logger: Where this class says what it has to say (a LogRecorder from the Bookkeeper).
        listening: The `[listening]` settings it was handed (it reads `cut_every_seconds`, `hold_back_seconds`, `rewind_seconds` and
            `max_cut_seconds`).
        took: How long the most recent whisper request took, in seconds.
        forced: How many times a segment had to be forced out at the buffer limit.
    """

    def __init__(self, client: SegmentFinder, listening: ListeningSettings, vad: bool = True,
                 clock: Optional[Callable[[], float]] = None, strict: Optional[bool] = None, bookkeeper: Optional[Bookkeeper] = None):
        """Args:
            client: Asks whisper for segments (a WhisperModelClient, or anything with its `segments` and `error`).
            listening: The `[listening]` section: `cut_every_seconds` (seconds of new audio between whisper runs), `hold_back_seconds` (a
                segment is kept once it ends this many seconds before the buffer's end), `rewind_seconds` (seconds before the last kept
                segment's end that the next round starts; 0 is off) and `max_cut_seconds` (seconds without a finished segment before one
                is forced out).
            vad: Whether whisper's voice detection is on for the rounds.
            clock: A function giving seconds, used only to time whisper. Default: time.perf_counter.
            strict: Raise RollingCutterError on any problem instead of logging it; None (the default) follows the project's `strict` setting.
            bookkeeper: The Bookkeeper the entry point built; it hands this class its logger. None (a test or a tool) gives a plain one.
        """
        self.logger = (bookkeeper or Bookkeeper()).logger_for(__name__, RollingCutterError, DEBUG, strict)
        self._client = client
        self.listening = listening
        self._vad = bool(vad)
        self._clock = clock if clock is not None else time.perf_counter
        self._lock = threading.Lock()
        self._pcm = bytearray()
        self._base = 0.0               # seconds in the whole stream at which the buffer's first byte lies
        self._start = 0.0              # where the next whisper window begins
        self._last_end = 0.0           # the end of the last kept segment
        self._next = self.listening.cut_every_seconds        # the stream time at which the next whisper run is due
        self.took = 0.0
        self.forced = 0

    @property
    def total_seconds(self) -> float:
        """Seconds of audio received so far, trimmed audio included."""
        with self._lock:
            return self._base + len(self._pcm) / BYTES_PER_SECOND

    def feed(self, pcm: bytes) -> None:
        """Adds audio to the end of the buffer. Anything that is not bytes is ignored."""
        if not isinstance(pcm, (bytes, bytearray, memoryview)):
            return
        with self._lock:
            self._pcm.extend(pcm)

    def skip_ahead(self, seconds: float = 0.0) -> None:
        """Forgets all the audio held and any half-finished segment, as if the sound up to now had not been wanted.

        `seconds` is sound that went by unheard (while the Printer was busy with a turn). The stream clock moves forward
        by that much, so stream time keeps matching real time and the cooldown and the bar's memory count the turn too.
        The next whisper run is due `cut_every_seconds` after the new start. A `seconds` that is not a number, or negative,
        counts as 0.
        """
        gap = float(seconds) if is_number(seconds) and seconds > 0 else 0.0
        with self._lock:
            now = self._base + len(self._pcm) / BYTES_PER_SECOND + gap
            self._pcm.clear()
            self._base = now
        self._start = now
        self._next = now + self.listening.cut_every_seconds

    def due(self) -> bool:
        """True when enough audio has arrived for the next whisper run."""
        return self.total_seconds >= self._next

    def recent(self, seconds: float) -> bytes:
        """The last `seconds` of audio received (less if there is less)."""
        wanted = int(seconds * BYTES_PER_SECOND) if is_number(seconds) else 0
        if wanted <= 0:
            return b""
        with self._lock:
            return bytes(self._pcm[max(0, len(self._pcm) - wanted) // SAMPLE_BYTES * SAMPLE_BYTES:])

    def _buffer_to(self, end: float) -> bytes:
        """The audio held, from the start of the buffer (which is where the next whisper window begins) up to `end`."""
        with self._lock:
            high = max(0, int((end - self._base) * BYTES_PER_SECOND)) // SAMPLE_BYTES * SAMPLE_BYTES
            return bytes(self._pcm[:high])

    def _trim(self, new_start: float) -> None:
        """Forgets audio before `new_start` (it will never be needed again)."""
        with self._lock:
            drop = max(0, int((new_start - self._base) * BYTES_PER_SECOND)) // SAMPLE_BYTES * SAMPLE_BYTES
            del self._pcm[:drop]
            self._base += drop / BYTES_PER_SECOND
        self._start = max(self._start, new_start)

    def cut(self, final: bool = False) -> List[Segment]:
        """Runs whisper on the buffer up to the current step and returns the segments whisper has finished.

        With `final` the end of the audio has been reached, so everything whisper found is kept. A failed request
        returns [] and leaves the buffer as it was, so the audio is tried again next round.
        """
        total = self.total_seconds
        end = total if final else min(self._next, total)
        self._next += self.listening.cut_every_seconds
        window = self._buffer_to(end)
        found: List[Segment] = []
        if end - self._start >= _SHORTEST_WINDOW:
            started = self._clock()
            try:
                found = join_unfinished(self._client.segments(window, self._start, vad=self._vad))
            except Exception as problem:             # a misbehaving client must never stop the oracle
                self.logger.warning("whisper could not be asked for segments (%s)", type(problem).__name__)
                return []
            self.took = self._clock() - started
            if self._client.error:                   # whisper did not answer: keep the audio and try again
                return []
        if not found:
            self._trim(end - _NOISE_KEPT)                       # only noise in the buffer: keep a short lead-in
            return []
        found = [s for s in found if s.end > self._last_end + _REHEARD_SLACK]    # drop what was already kept (heard again)
        done = [s for s in found if final or s.end <= end - self.listening.hold_back_seconds]
        if not done and found and end - self._start >= self.listening.max_cut_seconds:
            done = found                              # one long thought: do not let the buffer grow forever
            self.forced += 1
        if done:
            self._last_end = done[-1].end
            self._trim(self._last_end - self.listening.rewind_seconds)
        return done
