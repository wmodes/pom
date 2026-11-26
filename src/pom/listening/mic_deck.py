"""The MicDeck: the microphone, through the Pi's `arecord`.

It starts `arecord` for the capture device and hands out the sound in 30 ms frames. The Printer's whole job is to
listen, so this is where its hearing starts.

Design decisions, and why:
  * `arecord` is read by a thread of its own into a buffer, and `frames()` takes from the buffer. Whisper takes a
    second or more to answer, and the loop that waits for it must not make the microphone's pipe fill up and lose
    sound. The buffer holds `buffer_seconds` of audio (a `[listening]` setting). If the eavesdropper falls so far behind that the
    buffer fills, the OLDEST frames are dropped (the room has moved on), counted in `dropped_frames`, and a warning is
    recorded once for each stretch of dropping.
  * Nothing here may stop the oracle (see pom/__init__.py). No `arecord`, a microphone that cannot be opened, or a
    microphone that stops sending (unplugged) is a CRITICAL line in the log and `frames()` simply ends; it never
    raises. The Conductor decides what to do about it. `strict=True` raises MicDeckError instead, for tests and dev
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
from typing import Callable, Iterator, Optional

from pom.bookkeeper.bookkeeper import Bookkeeper
from pom.defaults import ListeningSettings
from pom.listening.audio_format import FRAME_BYTES, FRAME_SECONDS, SAMPLE_RATE

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module

DEFAULT_DEVICE = "plughw:0,0"
_STOP_WAIT = 2.0                      # seconds to wait for arecord and its reader to finish when closing


class MicDeckError(ValueError):
    """Raised for any microphone problem, but only in strict mode (development and tests)."""



class MicDeck:
    """Frames from the microphone.

    Attributes:
        logger: Where this class says what it has to say (a LogRecorder from the Bookkeeper).
        listening: The `[listening]` settings it was handed (it reads `buffer_seconds`).
        dropped_frames: How many frames were thrown away because the eavesdropper fell behind.
    """

    # What differs on another machine: the capture program, the device a bad setting falls back to, and the hint
    # given when the program is missing. MacMicDeck overrides these and `_command`.
    _TOOL = "arecord"
    _FALLBACK_DEVICE = DEFAULT_DEVICE
    _MISSING_HINT = "this is meant to run on the Pi"

    def __init__(self, device: str = DEFAULT_DEVICE, arecord: str = "arecord", *, listening: ListeningSettings, strict: bool = False,
                 heard: Optional[Callable[[], None]] = None, bookkeeper: Optional[Bookkeeper] = None):
        """Args:
            device: The ALSA capture device, e.g. plughw:0,0 (`arecord -l` lists them).
            arecord: The program to run (a path or a name on the PATH). Tests give a stand-in.
            listening: The `[listening]` section: `buffer_seconds`, how much sound to hold before the oldest is dropped.
            strict: Raise MicDeckError on any problem instead of logging it.
            bookkeeper: The Bookkeeper the entry point built; it hands this class its logger. None (a test or a tool) gives a plain one.
            heard: Called, with no arguments, as each frame comes from the microphone (by the thread that reads it), so the status board can
                tell that sound is arriving. A callback that raises is ignored: it must never stop the sound.
        """
        self.logger = (bookkeeper or Bookkeeper()).logger_for(__name__, MicDeckError, DEBUG, strict)
        self.listening = listening
        self.dropped_frames = 0
        self._heard = heard
        if not (isinstance(device, str) and device.strip()):
            self.logger.warning("the capture device must be a name such as %s, got %r; using %s",
                                self._FALLBACK_DEVICE, device, self._FALLBACK_DEVICE)
            device = self._FALLBACK_DEVICE
        self._queue: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=max(1, round(listening.buffer_seconds / FRAME_SECONDS)))
        self._closing = threading.Event()
        self._finished = False
        self._dropping = False
        self._thread_error: Optional[MicDeckError] = None
        self._process: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._start(device, arecord)

    def _start(self, device: str, arecord: str) -> None:
        program = shutil.which(arecord) if isinstance(arecord, str) else None
        if program is None:
            self.logger.critical("%s was not found; the Printer cannot hear (%s)", self._TOOL, self._MISSING_HINT)
            return
        try:
            self._process = subprocess.Popen(self._command(program, device), stdout=subprocess.PIPE,
                                             stderr=subprocess.DEVNULL)
        except OSError as problem:
            self.logger.critical("%s could not be started (%s); the Printer cannot hear", self._TOOL, type(problem).__name__)
            return
        self._reader = threading.Thread(target=self._read, name="mic-reader", daemon=True)
        self._reader.start()

    def _command(self, program: str, device: str) -> list[str]:
        """The command that writes the microphone's sound to its standard output as raw 16 kHz mono 16-bit samples."""
        return [program, "-q", "-D", device, "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1", "-t", "raw"]

    def _read(self) -> None:
        """Runs in its own thread: moves frames from arecord into the buffer until arecord stops."""
        stream = self._process.stdout
        try:
            while True:
                data = stream.read(FRAME_BYTES)
                if len(data) < FRAME_BYTES:
                    break
                self._put(data)
                self._tell_sound_arrived()
        except (OSError, ValueError):                       # the pipe was closed under us
            pass
        try:
            if not self._closing.is_set():
                self._fault("critical", "the microphone stopped sending sound (%s ended); the Printer can no longer hear", self._TOOL)
        finally:
            self._put(None)                                 # tells frames() the sound has ended

    def _tell_sound_arrived(self) -> None:
        """Calls `heard`, if there is one. Whatever it does, the sound goes on; a failure is a warning naming only the kind of error."""
        if self._heard is not None:
            try:
                self._heard()
            except Exception as problem:                    # the board must never stop the sound
                self._fault("warning", "telling that sound arrived failed (%s); the sound goes on", type(problem).__name__)

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
            self._fault("warning", "sound is arriving faster than it is used; dropping the oldest")
        self._dropping = dropped_now

    def _fault(self, mode: str, message: str, *args) -> None:
        """Says a problem found in the reader thread, in its mode ("warning" or "critical"). In strict mode the error cannot be raised here, where nobody is
        waiting for it, so it is kept and raised by frames() when the sound ends."""
        try:
            getattr(self.logger, mode)(message, *args)
        except MicDeckError as problem:
            self._thread_error = problem

    def frames(self) -> Iterator[bytes]:
        """Yields frames as they are heard, until the microphone stops or the deck is closed."""
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
