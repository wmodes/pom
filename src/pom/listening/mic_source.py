"""The MicSource: the microphone, through the Pi's `arecord`.

It starts `arecord` for the capture device and hands out the sound in 30 ms frames. The Printer's whole job is to
listen, so this is where its hearing starts.

Design decisions, and why:
  * `arecord` is read by a thread of its own into a buffer, and `frames()` takes from the buffer. Whisper takes a
    second or more to answer, and the loop that waits for it must not make the microphone's pipe fill up and lose
    sound. The buffer holds `buffer_seconds` of audio (10 by default). If the listener falls so far behind that the
    buffer fills, the OLDEST frames are dropped (the room has moved on), counted in `dropped_frames`, and a warning is
    recorded once for each stretch of dropping.
  * Nothing here may stop the oracle (see pom/__init__.py). No `arecord`, a microphone that cannot be opened, or a
    microphone that stops sending (unplugged) is a CRITICAL fault in `report` and `frames()` simply ends; it never
    raises. The Conductor decides what to do about it. `strict=True` raises MicSourceError instead, for tests and dev
    tools.
  * The program that is run is a setting (`arecord`), so tests can use a stand-in; the capture format is fixed at
    what whisper takes (see audio_format).
  * Nothing that was heard is ever kept or logged: the audio lives in the buffer until it is taken.
  * The capture gain is not set here. It resets on every reboot, so setting it is a startup duty.
"""
import math
import queue
import shutil
import subprocess
import threading
from typing import Iterator, Optional

from pom.fault_recorder import FaultRecorder
from pom.listening.audio_format import FRAME_BYTES, FRAME_SECONDS, SAMPLE_RATE
from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_DEVICE = "plughw:0,0"
DEFAULT_BUFFER_SECONDS = 10.0
_STOP_WAIT = 2.0                      # seconds to wait for arecord and its reader to finish when closing


class MicSourceError(ValueError):
    """Raised for any microphone problem, but only in strict mode (development and tests)."""


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class MicSource:
    """Frames from the microphone.

    Attributes:
        report: Problems met (no arecord, the microphone stopped, frames dropped). CRITICAL means no sound will come.
        dropped_frames: How many frames were thrown away because the listener fell behind.
    """

    def __init__(self, device: str = DEFAULT_DEVICE, arecord: str = "arecord",
                 buffer_seconds: float = DEFAULT_BUFFER_SECONDS, strict: bool = False):
        """Args:
            device: The ALSA capture device, e.g. plughw:0,0 (`arecord -l` lists them).
            arecord: The program to run (a path or a name on the PATH). Tests give a stand-in.
            buffer_seconds: How much sound to hold before the oldest is dropped, above 0.
            strict: Raise MicSourceError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=MicSourceError, logger=_log)
        self.dropped_frames = 0
        if not (isinstance(device, str) and device.strip()):
            self.report.warn(f"the capture device must be a name such as plughw:0,0, got {device!r}; using {DEFAULT_DEVICE}")
            device = DEFAULT_DEVICE
        if not (_is_number(buffer_seconds) and buffer_seconds > 0):
            self.report.warn(f"buffer_seconds must be a number above 0, got {buffer_seconds!r}; using {DEFAULT_BUFFER_SECONDS:g}")
            buffer_seconds = DEFAULT_BUFFER_SECONDS
        self._queue: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=max(1, round(buffer_seconds / FRAME_SECONDS)))
        self._closing = threading.Event()
        self._finished = False
        self._dropping = False
        self._thread_error: Optional[MicSourceError] = None
        self._process: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._start(device, arecord)

    def _start(self, device: str, arecord: str) -> None:
        program = shutil.which(arecord) if isinstance(arecord, str) else None
        if program is None:
            self.report.critical("arecord was not found; the Printer cannot hear (this is meant to run on the Pi)")
            return
        try:
            self._process = subprocess.Popen([program, "-q", "-D", device, "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1",
                                              "-t", "raw"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as problem:
            self.report.critical(f"arecord could not be started ({type(problem).__name__}); the Printer cannot hear")
            return
        self._reader = threading.Thread(target=self._read, name="mic-reader", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        """Runs in its own thread: moves frames from arecord into the buffer until arecord stops."""
        stream = self._process.stdout
        try:
            while True:
                data = stream.read(FRAME_BYTES)
                if len(data) < FRAME_BYTES:
                    break
                self._put(data)
        except (OSError, ValueError):                       # the pipe was closed under us
            pass
        try:
            if not self._closing.is_set():
                self._fault("critical", "the microphone stopped sending sound (arecord ended); the Printer can no longer hear")
        finally:
            self._put(None)                                 # tells frames() the sound has ended

    def _put(self, item: Optional[bytes]) -> None:
        """Adds to the buffer, throwing away the oldest frame if it is full (the end marker is never lost)."""
        dropped_now = False
        while True:
            try:
                self._queue.put_nowait(item)
                break
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    self.dropped_frames += 1
                    dropped_now = True
                except queue.Empty:
                    pass
        if dropped_now and not self._dropping:
            self._fault("warn", "sound is arriving faster than it is used; dropping the oldest")
        self._dropping = dropped_now

    def _fault(self, severity: str, message: str) -> None:
        """Records a fault found in the reader thread. In strict mode the error cannot be raised here, where nobody is
        waiting for it, so it is kept and raised by frames() when the sound ends."""
        try:
            getattr(self.report, severity)(message)
        except MicSourceError as problem:
            self._thread_error = problem

    def frames(self) -> Iterator[bytes]:
        """Yields frames as they are heard, until the microphone stops or the source is closed."""
        if self._process is None:
            return
        while not self._finished:
            frame = self._queue.get()
            if frame is None:
                self._finished = True
                if self._thread_error is not None:
                    raise self._thread_error
                return
            yield frame

    def close(self) -> None:
        """Stops the microphone. Safe to call more than once."""
        self._closing.set()
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=_STOP_WAIT)
        except subprocess.TimeoutExpired:
            process.kill()
        if self._reader is not None:
            self._reader.join(timeout=_STOP_WAIT)
        try:
            process.stdout.close()
        except (OSError, ValueError):
            pass
