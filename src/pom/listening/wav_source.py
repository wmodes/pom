"""The WavSource: a recording played into the listening side as if it were the microphone.

It hands out a 16 kHz mono 16-bit WAV file in 30 ms frames, paced like real time (speed 1), faster, or flat out
(speed 0). This is how we try a change on a meeting we already have, and how the listening side is tested without a
microphone. The Printer itself uses a MicSource and never constructs this.

After the last frame it adds a short stretch of silence (1.2 s), so whatever was being said when the recording ended
is followed by a pause, as it would be in a room. A last piece of less than one frame is dropped.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A missing file, or a file in the wrong format, gives a
    CRITICAL fault in `report` and a source that yields nothing, never an exception. `strict=True` raises
    WavSourceError instead, for tests and dev tools. The message for a wrong format says how to convert it.
  * Pacing is only a pause after each frame, and the pause function is injected (`sleep`), so tests run instantly.
    The listening side counts time in the audio it has received, not in wall-clock time, so a recording replayed at
    any speed is heard exactly as a live stream would be.
  * Nothing in a recording is ever logged.
"""
import math
import time
import wave
from pathlib import Path
from typing import Callable, Iterator, Optional

from pom.fault_recorder import FaultRecorder
from pom.listening.audio_format import FRAME_BYTES, FRAME_SECONDS, SAMPLE_BYTES, SAMPLE_RATE
from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_SPEED = 1.0
DEFAULT_END_PAUSE_SECONDS = 1.2


class WavSourceError(ValueError):
    """Raised for any source problem, but only in strict mode (development and tests)."""


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class WavSource:
    """Frames from a recording.

    Attributes:
        report: Problems met (file missing or in the wrong format, bad setting). A recording that cannot be played is
            a CRITICAL fault and the source yields nothing.
        seconds: The length of the recording in seconds, or 0.0 if it could not be opened.
    """

    def __init__(self, path, speed: float = DEFAULT_SPEED, end_pause_seconds: float = DEFAULT_END_PAUSE_SECONDS,
                 sleep: Optional[Callable[[float], None]] = None, strict: bool = False):
        """Args:
            path: The WAV file: 16 kHz, mono, 16-bit. Convert another with: ffmpeg -i in -ar 16000 -ac 1 out.wav
            speed: 1 plays at real time, 2 twice as fast, 0 as fast as possible. 0 or more.
            end_pause_seconds: Silence added after the last frame. 0 or more.
            sleep: A function that waits that many seconds. Default: time.sleep.
            strict: Raise WavSourceError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=WavSourceError, logger=_log)
        self.seconds = 0.0
        self._speed = self._valid(speed, "speed", DEFAULT_SPEED)
        self._end_frames = round(self._valid(end_pause_seconds, "end_pause_seconds", DEFAULT_END_PAUSE_SECONDS) / FRAME_SECONDS)
        self._sleep = sleep if sleep is not None else time.sleep
        self._wav: Optional[wave.Wave_read] = None
        self._open(path)

    def _valid(self, value, name: str, default: float) -> float:
        """The value if it is a number of 0 or more, otherwise the default with a warning."""
        if _is_number(value) and value >= 0:
            return float(value)
        self.report.warn(f"{name} must be a number of at least 0, got {value!r}; using {default:g}")
        return default

    def _open(self, path) -> None:
        try:
            wav = wave.open(str(Path(path)), "rb")
        except (OSError, EOFError, wave.Error, TypeError, ValueError) as problem:
            self.report.critical(f"cannot open the recording ({type(problem).__name__}); nothing will be played")
            return
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (SAMPLE_RATE, 1, SAMPLE_BYTES):
            wav.close()
            self.report.critical(f"the recording must be 16 kHz mono 16-bit; convert it with: "
                                 f"ffmpeg -i in -ar {SAMPLE_RATE} -ac 1 out.wav")
            return
        self._wav = wav
        self.seconds = wav.getnframes() / SAMPLE_RATE

    def frames(self) -> Iterator[bytes]:
        """Yields the recording a frame at a time, then a pause of silence. Yields nothing if it could not be opened."""
        wav = self._wav
        if wav is None:
            return
        samples_per_frame = FRAME_BYTES // SAMPLE_BYTES
        pause = FRAME_SECONDS / self._speed if self._speed > 0 else 0.0
        while self._wav is not None:
            try:
                data = wav.readframes(samples_per_frame)
            except Exception:                                    # closed from another thread while playing
                break
            if len(data) < FRAME_BYTES:
                break
            yield data
            if pause:
                self._sleep(pause)
        if self._wav is None:
            return
        for _ in range(self._end_frames):
            yield b"\x00" * FRAME_BYTES

    def close(self) -> None:
        """Closes the file. Safe to call more than once."""
        wav, self._wav = self._wav, None
        if wav is not None:
            wav.close()
