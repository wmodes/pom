"""The RollingCutter: lets whisper's own voice detection decide where to cut the audio stream.

Cutting the stream by loudness (a pause after speech) fails in a room with bursty noise, a hookah, a cat or the street,
which never lets it find a pause, so it ends up cutting by the clock in the middle of a thought. This module hands
whisper a rolling stretch of audio every `step` seconds and keeps the segments whisper itself finished. Whisper decides
what is speech and where its segments end.

What this adds around whisper, and why:
  * The audio after the last kept segment stays in the buffer for the next round, so a thought that was still going is
    not cut in two. A segment is kept once it ends at least `hold` seconds before the end of the buffer.
  * A segment that does not end like a sentence (no . ? or !) is joined to the one after it first, because whisper
    sometimes breaks at a pause in the middle of a sentence.
  * Optional, off by default (`rewind` 0): the next round starts `rewind` seconds before the last kept segment's end, in
    case whisper's segment ends run slightly late and clip the next sentence's first syllables. Segments that end within
    that stretch are the old ones heard again and are dropped. Live, it also re-heard the last word of the previous
    segment as the start of the next ("yourself." twice), which is why it is off.
  * A buffer with no speech in it is trimmed to its last two seconds, so noise does not pile up.
  * A buffer that grows past `max_buffer` seconds without a finished segment is forced out, so one very long thought
    cannot stall everything.

Design decisions, and why:
  * Time is counted in audio received, not in wall-clock time, so a recording replayed at any speed is cut exactly as a
    live stream would be. The one clock reading is how long whisper took, for display (`took`), and it is injected.
  * Nothing here may stop the oracle (see pom/__init__.py). If whisper cannot be reached the round returns nothing and
    the audio stays in the buffer, so the next round tries it again; a client that raises is treated the same way. A bad
    setting is replaced by its default and recorded in `report`. `strict=True` raises RollingCutterError instead, for
    tests and dev tools.
  * Audio is only held in memory, and neither audio nor text is logged.
  * `feed` and `cut` may be called from different threads; a lock guards the buffer.
  * The settings are passed in (the entry point takes them from Config); this module never reads Config.
"""
import math
import re
import threading
import time
from typing import Callable, List, Optional, Protocol

from pom.fault_recorder import FaultRecorder
from pom.listening.audio_format import BYTES_PER_SECOND, SAMPLE_BYTES
from pom.listening.whisper_server_client import Segment
from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_STEP = 5.0
DEFAULT_HOLD = 2.0
DEFAULT_REWIND = 0.0
DEFAULT_MAX_BUFFER = 30.0
_FINISHED = re.compile(r"[.?!][\"')\]]*$")             # ends like a sentence
_LONGEST_JOIN = 25.0                                    # seconds: the longest a joined segment may grow
_NOISE_KEPT = 2.0                                       # seconds of a buffer with no speech that are kept
_SHORTEST_WINDOW = 1.0                                  # not worth asking whisper about less
_REHEARD_SLACK = 0.2                                    # seconds: a segment must end this much past the last kept one


class RollingCutterError(ValueError):
    """Raised for any cutter problem, but only in strict mode (development and tests)."""


class SegmentFinder(Protocol):
    """What the cutter needs from whisper: the WhisperServerClient has it."""
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


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class RollingCutter:
    """Feed it audio; ask it for the segments whisper has finished.

    Attributes:
        report: Problems met (a bad setting, a client that raised). Never holds audio or text.
        took: How long the most recent whisper request took, in seconds.
        forced: How many times a segment had to be forced out at the buffer limit.
    """

    def __init__(self, client: SegmentFinder, step: float = DEFAULT_STEP, hold: float = DEFAULT_HOLD,
                 rewind: float = DEFAULT_REWIND, max_buffer: float = DEFAULT_MAX_BUFFER, vad: bool = True,
                 clock: Optional[Callable[[], float]] = None, strict: bool = False):
        """Args:
            client: Asks whisper for segments (a WhisperServerClient, or anything with its `segments` and `error`).
            step: Seconds of new audio between whisper runs, above 0.
            hold: A segment is kept once it ends this many seconds before the buffer's end, 0 or more.
            rewind: Seconds before the last kept segment's end that the next round starts, 0 or more (0 is off).
            max_buffer: Seconds without a finished segment before one is forced out, above 0.
            vad: Whether whisper's voice detection is on for the rounds.
            clock: A function giving seconds, used only to time whisper. Default: time.perf_counter.
            strict: Raise RollingCutterError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=RollingCutterError, logger=_log)
        self._client = client
        self._step = self._setting(step, "step", DEFAULT_STEP, lambda v: v > 0, "above 0")
        self._hold = self._setting(hold, "hold", DEFAULT_HOLD, lambda v: v >= 0, "at least 0")
        self._rewind = self._setting(rewind, "rewind", DEFAULT_REWIND, lambda v: v >= 0, "at least 0")
        self._max_buffer = self._setting(max_buffer, "max_buffer", DEFAULT_MAX_BUFFER, lambda v: v > 0, "above 0")
        self._vad = bool(vad)
        self._clock = clock if clock is not None else time.perf_counter
        self._lock = threading.Lock()
        self._pcm = bytearray()
        self._base = 0.0               # seconds in the whole stream at which the buffer's first byte lies
        self._start = 0.0              # where the next whisper window begins
        self._last_end = 0.0           # the end of the last kept segment
        self._next = self._step        # the stream time at which the next whisper run is due
        self.took = 0.0
        self.forced = 0

    def _setting(self, value, name: str, default: float, is_good, wanted: str) -> float:
        """The value if it is a number that passes `is_good`, otherwise the default with a warning."""
        if _is_number(value) and is_good(value):
            return float(value)
        self.report.warn(f"{name} must be a number {wanted}, got {value!r}; using {default:g}")
        return default

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

    def due(self) -> bool:
        """True when enough audio has arrived for the next whisper run."""
        return self.total_seconds >= self._next

    def recent(self, seconds: float) -> bytes:
        """The last `seconds` of audio received (less if there is less)."""
        wanted = int(seconds * BYTES_PER_SECOND) if _is_number(seconds) else 0
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
        self._next += self._step
        window = self._buffer_to(end)
        found: List[Segment] = []
        if end - self._start >= _SHORTEST_WINDOW:
            started = self._clock()
            try:
                found = join_unfinished(self._client.segments(window, self._start, vad=self._vad))
            except Exception as problem:             # a misbehaving client must never stop the oracle
                self.report.warn(f"whisper could not be asked for segments ({type(problem).__name__})")
                return []
            self.took = self._clock() - started
            if self._client.error:                   # whisper did not answer: keep the audio and try again
                return []
        if not found:
            self._trim(end - _NOISE_KEPT)                       # only noise in the buffer: keep a short lead-in
            return []
        found = [s for s in found if s.end > self._last_end + _REHEARD_SLACK]    # drop what was already kept (heard again)
        done = [s for s in found if final or s.end <= end - self._hold]
        if not done and found and end - self._start >= self._max_buffer:
            done = found                              # one long thought: do not let the buffer grow forever
            self.forced += 1
        if done:
            self._last_end = done[-1].end
            self._trim(self._last_end - self._rewind)
        return done
