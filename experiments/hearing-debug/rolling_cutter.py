"""The RollingCutter: lets whisper's own voice detection decide where to cut the audio stream.

Part of experiments/hearing-debug. The energy cutter in probe_hearing_pipeline.py cuts the stream by
loudness, which in a room with bursty noise (a hookah, a cat, the street) never finds a pause and cuts by the
clock, in the middle of a thought. This one hands whisper a rolling stretch of audio every `step` seconds and
keeps the segments whisper itself finished. Whisper decides what is speech and where its segments end.

What this adds around whisper, and why:
  * The audio after the last kept segment stays in the buffer for the next round, so a thought that was still
    going is not cut in two. A segment is kept once it ends at least `hold` seconds before the end of the
    buffer.
  * A segment that does not end like a sentence (no . ? or !) is joined to the one after it first, because
    whisper sometimes breaks at a pause in the middle of a sentence.
  * Optional, off by default (`rewind` 0): the next round starts `rewind` seconds before the last kept segment's
    end, in case whisper's segment ends run slightly late and clip the next sentence's first syllables. Segments
    that end within that stretch are the old ones heard again and are dropped. Live, it also re-heard the last word
    of the previous segment as the start of the next ("yourself." twice), which is why it is off.
  * A buffer with no speech in it is trimmed to its last two seconds, so noise does not pile up.
  * A buffer that grows past `max_buffer` seconds without a finished segment is forced out, so one very long
    thought cannot stall everything.

Time is counted in audio received, not in wall-clock time, so a recording replayed at any speed is cut exactly
as a live stream would be. Audio is only held in memory.
"""
import re
import threading
import time
from typing import List, Optional, Tuple

from whisper_server_client import SAMPLE_RATE, Segment, WhisperServerClient

BYTES_PER_SECOND = SAMPLE_RATE * 2                         # 16-bit mono
FINISHED = re.compile(r"[.?!][\"')\]]*$")                  # ends like a sentence


def join_unfinished(found: List[Segment], longest: float = 25.0) -> List[Segment]:
    """Joins each segment that does not end like a sentence to the one after it (up to `longest` seconds)."""
    out: List[Segment] = []
    for begin, finish, text in found:
        if out and not FINISHED.search(out[-1][2]) and finish - out[-1][0] <= longest:
            out[-1] = (out[-1][0], finish, out[-1][2] + " " + text)
        else:
            out.append((begin, finish, text))
    return out


class RollingCutter:
    """Feed it audio; ask it for the segments whisper has finished.

    Attributes:
        took: How long the most recent whisper request took, in seconds.
        forced: How many times a segment had to be forced out at the buffer limit.
    """

    def __init__(self, client: WhisperServerClient, step: float = 5.0, hold: float = 2.0, rewind: float = 0.0,
                 max_buffer: float = 30.0, vad: bool = True):
        self._client, self._step, self._hold, self._rewind = client, step, hold, rewind
        self._max_buffer, self._vad = max_buffer, vad
        self._lock = threading.Lock()
        self._pcm = bytearray()
        self._base = 0.0               # seconds in the whole stream at which the buffer's first byte lies
        self._start = 0.0              # where the next whisper window begins
        self._last_end = 0.0           # the end of the last kept segment
        self._next = step              # the stream time at which the next whisper run is due
        self.took = 0.0
        self.forced = 0

    @property
    def total_seconds(self) -> float:
        """Seconds of audio received so far."""
        with self._lock:
            return self._base + len(self._pcm) / BYTES_PER_SECOND

    def feed(self, pcm: bytes) -> None:
        with self._lock:
            self._pcm.extend(pcm)

    def due(self) -> bool:
        """True when enough audio has arrived for the next whisper run."""
        return self.total_seconds >= self._next

    def recent(self, seconds: float) -> bytes:
        """The last `seconds` of audio received (less if there is less)."""
        with self._lock:
            return bytes(self._pcm[max(0, len(self._pcm) - int(seconds * BYTES_PER_SECOND)) // 2 * 2:])

    def _slice(self, begin: float, end: float) -> bytes:
        with self._lock:
            low = max(0, int((begin - self._base) * BYTES_PER_SECOND)) // 2 * 2
            high = max(0, int((end - self._base) * BYTES_PER_SECOND)) // 2 * 2
            return bytes(self._pcm[low:high])

    def _trim(self, new_start: float) -> None:
        """Forgets audio before `new_start` (it will never be needed again)."""
        with self._lock:
            drop = max(0, int((new_start - self._base) * BYTES_PER_SECOND)) // 2 * 2
            del self._pcm[:drop]
            self._base += drop / BYTES_PER_SECOND
        self._start = max(self._start, new_start)

    def cut(self, final: bool = False) -> List[Segment]:
        """Runs whisper on the buffer up to the current step and returns the segments it has finished.

        With `final` the end of the audio has been reached, so everything whisper found is kept. A failed request
        returns [] and leaves the buffer as it was, so the audio is tried again next round.
        """
        total = self.total_seconds
        end = total if final else min(self._next, total)
        self._next += self._step
        window = self._slice(self._start, end)
        found: List[Segment] = []
        if end - self._start >= 1.0:
            started = time.perf_counter()
            found = join_unfinished(self._client.segments(window, self._start, vad=self._vad))
            self.took = time.perf_counter() - started
            if self._client.error:                       # whisper did not answer: keep the audio and try again
                return []
        if not found:
            self._trim(max(self._start, end - 2.0))      # only noise in the buffer: keep a short lead-in
            return []
        found = [s for s in found if s[1] > self._last_end + 0.2]     # drop what was already kept (heard again in the rewind)
        done = [s for s in found if final or s[1] <= end - self._hold]
        if not done and found and end - self._start >= self._max_buffer:
            done = found                                  # one long thought: do not let the buffer grow forever
            self.forced += 1
        if done:
            self._last_end = done[-1][1]
            self._trim(self._last_end - self._rewind)
        return done
