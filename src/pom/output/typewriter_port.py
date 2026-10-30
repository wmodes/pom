"""The TypewriterPort: formatted text goes in, the typewriter types it; what a person types on it comes back.

The Wheelwriter, converted with the CadetWriter board, acts as a serial terminal (hence the config section
`[terminal]`). `type(text)` sends a formatted portent to it and returns when it has been typed. It is the last step
after the TypewriterFormatter. The Conductor pauses listening, calls `type`, and resumes listening when it returns:
this module knows nothing about listening. `read_line()` is the other direction: someone at the keyboard can type
back, and each finished line (the C Rtn key) is returned.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A port that is missing, will not open or fails halfway gives
    a TypingReport that says the text was not all typed, and a warning in `report`; never an exception. `strict=True`
    raises TypewriterPortError instead, for tests and dev tools. The port is opened on the first call and reopened
    after a failure, so a typewriter that is switched on late is found.
  * The portent is never logged or put in a report: nothing that was said in the room is kept. Reports hold counts
    and timings.
  * LINE ENDINGS. The CadetWriter expects CR+LF by default; a bare LF only feeds the paper and does not return the
    carriage, so every line would step down the page. A newline is sent as `end_of_line` ("crlf" by default), which
    must match the CadetWriter's "receive end-of-line" setting.
  * PACING. The CadetWriter has a small buffer and loses data if flooded, so the text is sent no faster than
    `characters_per_second`, a little under the machine's own speed, which keeps its buffer nearly empty. Software
    flow control (XON/XOFF, or RTS/CTS) is switched on in the port as well, as the real safeguard; the pacing makes
    the end time predictable. A newline takes one slot, like a character. Timing is scheduled from the start, not
    slept per character, so it does not drift.
  * FINISHING. When the last byte has been sent the machine may still be typing the end of the line, so `type` waits
    `finish_wait_seconds` more before returning. Only then does the Conductor resume listening.
  * ONLY SAFE BYTES. Only printable ASCII and newlines are sent. An ESC would start one of the CadetWriter's escape
    sequences and the other control characters have jobs of their own, so any that reach here are dropped.
  * KEYBOARD. In its default full-duplex mode the CadetWriter sends each key to the Pi and prints nothing itself; the Pi
    decides what reaches the paper. With `echoplex` (the old terminal name for the host echoing back what it receives)
    the Pi echoes each key as it arrives, a Backspace moves the carriage left (the line being built loses its last
    character, so a retype overstrikes as on any typewriter) and C Rtn is echoed as the line ending. Without it the
    CadetWriter must be set to half duplex, where it echoes locally (set both or characters print twice, or not at all).
    A line ends at CR or LF (a CR+LF pair counts once). Control characters are ignored and not echoed.
  * NO KEYS DURING A PORTENT. `type` and the echo share one serial line and cannot interleave, so `type` holds a lock
    for its whole run. Unless `type_during_output` is on (it is off by default) whatever was typed during it, and any half-typed
    line, is thrown away when it finishes, so nothing typed over a portent appears after it. Turned on, those keys
    are kept and handled by the next `read_line`.
  * What is typed in the room is room content: like the portent it is never logged or put in a report.
  * The settings are passed in (the entry point takes them from `config.terminal`); this module never reads Config.
  * The way to open the port, the clock and the sleep are injected, so tests run with a fake and need no machine, and
    the ConsoleTypewriter (the stand-in for development) is this same code writing to the terminal.
"""
import collections
import os
import select
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

FLOW_CONTROLS = ("xon_xoff", "rts_cts", "none")
END_OF_LINES = {"cr": "\r", "crlf": "\r\n", "lf": "\n", "lfcr": "\n\r"}
BAUD_RATES = (50, 75, 110, 134, 150, 200, 300, 600, 1200, 1800, 2400, 4800, 9600, 19200, 38400, 57600, 76800,
              115200, 230400)
DEFAULT_BAUD = 9600
DEFAULT_FLOW_CONTROL = "xon_xoff"
DEFAULT_END_OF_LINE = "crlf"
DEFAULT_CHARACTERS_PER_SECOND = 16.0
DEFAULT_FINISH_WAIT_SECONDS = 2.0
MAX_TYPED_LINE = 500        # a typed line this long is ended there, so a stuck key cannot grow it without limit
_READ_SLICE_SECONDS = 0.1   # one look at the port; the lock is free between looks, so a portent can start


class TypewriterPortError(ValueError):
    """Raised for any port problem, but only in strict mode (development and tests)."""


@dataclass(frozen=True, slots=True)
class TypingReport:
    """What happened to one piece of text.

    Attributes:
        characters: How many characters were sent (a newline counts as one).
        seconds: How long `type` took, including the wait at the end.
        completed: True if all of the text was sent. False if the port could not be opened or failed halfway.
    """
    characters: int
    seconds: float
    completed: bool


def _open_serial(port: str, baud: int, flow_control: str):
    """Opens the real serial port (pyserial is imported here, so a missing library is a recorded fault, not a crash)."""
    import serial
    return serial.Serial(port, baud, bytesize=8, parity="N", stopbits=1, timeout=_READ_SLICE_SECONDS, write_timeout=10,
                         xonxoff=flow_control == "xon_xoff", rtscts=flow_control == "rts_cts")


class TypewriterPort:
    """Sends formatted text to the typewriter over a serial port, paced.

    Attributes:
        report: Problems met (port will not open, write failed, bad setting). Never holds the text.
    """

    def __init__(self, port: str, baud: int = DEFAULT_BAUD, flow_control: str = DEFAULT_FLOW_CONTROL,
                 end_of_line: str = DEFAULT_END_OF_LINE, characters_per_second: float = DEFAULT_CHARACTERS_PER_SECOND,
                 finish_wait_seconds: float = DEFAULT_FINISH_WAIT_SECONDS,
                 echoplex: bool = True, type_during_output: bool = False,
                 opener: Optional[Callable[[str, int, str], object]] = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, strict: bool = False):
        """Args:
            port: The serial device, e.g. /dev/ttyACM0.
            baud: The speed; only the RS-232 port uses it (USB ignores it). One of BAUD_RATES.
            flow_control: "xon_xoff", "rts_cts" or "none".
            end_of_line: What a newline is sent as: "cr", "crlf", "lf" or "lfcr". Must match the CadetWriter's setting.
            characters_per_second: The fastest the text is sent, above 0.
            finish_wait_seconds: How long to wait after the last byte for the machine to finish, 0 or more.
            echoplex: True to echo each key typed on the typewriter back to it (the CadetWriter must be in full duplex).
            type_during_output: False to throw away keys typed while a portent is being typed; True keeps them for the next read.
            opener: A function (port, baud, flow_control) -> an object with write(bytes), flush(), close(), read(size)
                and reset_input_buffer(). Default: the real serial port.
            clock: Returns seconds, steadily increasing.
            sleep: Waits for that many seconds.
            strict: Raise TypewriterPortError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=TypewriterPortError, logger=_log)
        self._port_name = port
        self._baud = self._valid(baud, "baud", DEFAULT_BAUD, lambda v: v in BAUD_RATES, "one of the standard baud rates")
        self._flow_control = self._valid(flow_control, "flow_control", DEFAULT_FLOW_CONTROL,
                                         lambda v: v in FLOW_CONTROLS, f"one of {', '.join(FLOW_CONTROLS)}")
        self._end_of_line = END_OF_LINES[self._valid(end_of_line, "end_of_line", DEFAULT_END_OF_LINE,
                                                     lambda v: v in END_OF_LINES, f"one of {', '.join(END_OF_LINES)}")]
        self._seconds_per_character = 1.0 / self._valid(
            characters_per_second, "characters_per_second", DEFAULT_CHARACTERS_PER_SECOND,
            lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0, "a number above 0")
        self._finish_wait = self._valid(
            finish_wait_seconds, "finish_wait_seconds", DEFAULT_FINISH_WAIT_SECONDS,
            lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0, "a number of at least 0")
        self._opener = opener if opener is not None else _open_serial
        self._clock = clock
        self._sleep = sleep
        self._echoplex = bool(echoplex)
        self._type_during_output = bool(type_during_output)
        self._serial = None
        self._lock = threading.Lock()            # one use of the line at a time: a portent, or a look for keys
        self._received: collections.deque = collections.deque()   # bytes read but not yet handled
        self._line: list[str] = []               # the line being typed on the keyboard
        self._after_cr = False                   # the last byte handled was a CR, so a following LF is its pair
        self._usable = isinstance(port, str) and bool(port.strip())
        if not self._usable:
            self.report.warn(f"the typewriter port must be a device name, got {port!r}; nothing will be typed")

    def _valid(self, value, name: str, default, is_good, wanted: str):
        """The value if it passes `is_good`, otherwise the default with a warning."""
        if is_good(value):
            return value
        self.report.warn(f"the typewriter {name} must be {wanted}, got {value!r}; using {default}")
        return default

    @property
    def available(self) -> bool:
        """False if the port name was unusable, so nothing will ever be typed."""
        return self._usable

    def type(self, text: str) -> TypingReport:
        """Types the text and returns when the typewriter has had time to finish. Never raises (except in strict mode).

        `completed` is False if any of it was not sent; the reason is in `report`. Text with nothing to type is
        completed at once, without opening the port.
        """
        started = self._clock()
        data = self._bytes(text) if isinstance(text, str) else ""
        if not data:
            return TypingReport(0, 0.0, self._usable)
        with self._lock:
            if not self._usable or not self._connect():
                return TypingReport(0, self._clock() - started, False)
            self._discard_input()
            sent = 0
            try:
                for index, character in enumerate(data):
                    due = started + index * self._seconds_per_character
                    if due > self._clock():
                        self._sleep(due - self._clock())
                    self._serial.write(character.encode("ascii"))
                    sent += 1
                self._serial.flush()
            except Exception as error:               # a dead port must never stop the oracle
                self.report.warn(f"the typewriter port failed after {sent} of {len(data)} characters "
                                 f"({type(error).__name__}); the rest was not typed")
                self.close()
                return TypingReport(sent, self._clock() - started, False)
            if self._finish_wait:
                self._sleep(self._finish_wait)
            self._discard_input()
            seconds = self._clock() - started
            _log.debug("typed %d characters in %.1f s", sent, seconds)
            return TypingReport(sent, seconds, True)

    def read_line(self, timeout: Optional[float] = None) -> Optional[str]:
        """The next line typed on the typewriter's keyboard, or None if none was finished within `timeout` seconds.

        Waits until C Rtn is pressed (or `timeout` passes; None waits for ever). A line typed halfway when the time ran
        out is kept for the next call. An empty line, from C Rtn alone, is returned as ''. With `echoplex` each key is
        echoed to the paper as it arrives. Keys pressed during a portent wait for it (see type_during_output). Never
        raises (except in strict mode); None can also mean the port would not open, with the reason in `report`.
        """
        deadline = None if timeout is None else self._clock() + timeout
        while True:
            while self._received:
                line = self._accept(self._received.popleft())
                if line is not None:
                    return line
            if deadline is not None and self._clock() >= deadline:
                return None
            with self._lock:
                if not self._usable or not self._connect():
                    self._sleep(1.0 if deadline is None else max(0.0, deadline - self._clock()))
                    return None
                try:
                    data = self._serial.read(max(1, getattr(self._serial, "in_waiting", 0) or 1))
                except Exception as error:           # a dead port must never stop the oracle
                    self.report.warn(f"the typewriter port failed while listening for the keyboard "
                                     f"({type(error).__name__}); try again later")
                    self.close()
                    return None
            if data:
                self._received.extend(data)
            else:
                self._sleep(0.01)

    def close(self) -> None:
        """Closes the port if it is open; the next `type` opens it again."""
        serial, self._serial = self._serial, None
        if serial is not None:
            try:
                serial.close()
            except Exception:                    # closing a dead port can fail; there is nothing more to do
                pass

    def _accept(self, code: int) -> Optional[str]:
        """Handles one byte from the keyboard; returns the finished line if this byte ends one."""
        after_cr, self._after_cr = self._after_cr, code == 13
        if code == 10 and after_cr:
            return None                                 # the LF of a CR+LF pair
        if code in (13, 10):
            line, self._line = "".join(self._line), []
            self._echo(self._end_of_line)
            return line
        if code in (8, 127):                            # Backspace or Delete
            if self._line:
                self._line.pop()
                self._echo("\b")
            return None
        if 32 <= code <= 126:
            self._line.append(chr(code))
            self._echo(chr(code))
            if len(self._line) >= MAX_TYPED_LINE:
                line, self._line = "".join(self._line), []
                self._echo(self._end_of_line)
                return line
        return None

    def _echo(self, text: str) -> None:
        """Sends the keyboard's echo to the typewriter at once (a person types slowly; no pacing). A failure is recorded."""
        if not self._echoplex or self._serial is None:      # no echo wanted, or the port just failed (already reported)
            return
        with self._lock:
            try:
                self._serial.write(text.encode("ascii"))
            except Exception as error:               # a dead port must never stop the oracle
                self.report.warn(f"the typewriter port failed while echoing the keyboard ({type(error).__name__})")
                self.close()

    def _discard_input(self) -> None:
        """If keys are not wanted during a portent: forget everything typed and still unread, and any half-typed line."""
        if self._type_during_output:
            return
        self._received.clear()
        self._line.clear()
        self._after_cr = False
        try:
            self._serial.reset_input_buffer()
        except Exception:                            # a dead port is found by the next write
            pass

    def _connect(self) -> bool:
        """Opens the port if it is not open. False (with a warning) if it will not open."""
        if self._serial is not None:
            return True
        try:
            self._serial = self._opener(self._port_name, self._baud, self._flow_control)
        except Exception as error:
            self.report.warn(f"the typewriter port {self._port_name!r} would not open ({type(error).__name__}); "
                             f"nothing was typed")
            return False
        return True

    def _bytes(self, text: str) -> list[str]:
        """The characters to send, one per slot: safe ones only, newlines as the configured line ending (one slot)."""
        out: list[str] = []
        for character in text.replace("\r\n", "\n").replace("\r", "\n"):
            if character == "\n":
                out.append(self._end_of_line)
            elif " " <= character <= "~":
                out.append(character)
        return out


class _StreamSink:
    """Looks like a serial port to the TypewriterPort but types on a text stream and reads keys from another, for the ConsoleTypewriter."""

    def __init__(self, stream, input_stream):
        self._stream = stream
        self._input = input_stream

    def write(self, data: bytes) -> None:
        self._stream.write(data.decode("ascii"))
        self._stream.flush()

    def flush(self) -> None:
        self._stream.flush()

    def close(self) -> None:
        pass

    def read(self, size: int = 1) -> bytes:
        """Up to `size` bytes typed on the input if there are some within a moment, else b''."""
        if self._input is None:
            return b""
        try:
            descriptor = self._input.fileno()
        except (AttributeError, OSError, ValueError):   # not a real terminal or pipe (a test's StringIO)
            return self._input.read(size).encode("ascii", "replace")
        ready, _, _ = select.select([descriptor], [], [], _READ_SLICE_SECONDS)
        return os.read(descriptor, size) if ready else b""

    def reset_input_buffer(self) -> None:
        """Throws away whatever has been typed and not read."""
        if self._input is None:
            return
        try:
            descriptor = self._input.fileno()
        except (AttributeError, OSError, ValueError):
            return
        while select.select([descriptor], [], [], 0)[0]:
            if not os.read(descriptor, 4096):
                break


class ConsoleTypewriter(TypewriterPort):
    """A stand-in for the typewriter that types on the terminal at the same pace, for development.

    It is the TypewriterPort writing to a stream instead of a serial port, so the pacing, the safe-byte filter and
    the report are exactly the real ones. Anything that takes a TypewriterPort takes this.

    Args:
        stream: Where to type; default standard output.
        input_stream: Where the "keyboard" is: what is typed there is what `read_line` returns; default standard input.
        characters_per_second: The pace; 16 like the real one. A big number types at once.
        finish_wait_seconds: Wait after the last character; default none.
        echoplex: Echo the keys back; off by default because the terminal already shows what is typed.
        type_during_output: False throws away keys typed while it types, like the real one.
    """

    def __init__(self, stream=None, input_stream=None, characters_per_second: float = DEFAULT_CHARACTERS_PER_SECOND,
                 finish_wait_seconds: float = 0.0, echoplex: bool = False, type_during_output: bool = False,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 strict: bool = False):
        sink = _StreamSink(stream if stream is not None else sys.stdout,
                           input_stream if input_stream is not None else sys.stdin)
        super().__init__("console", end_of_line="lf", characters_per_second=characters_per_second,
                         finish_wait_seconds=finish_wait_seconds, echoplex=echoplex,
                         type_during_output=type_during_output, opener=lambda port, baud, flow: sink,
                         clock=clock, sleep=sleep, strict=strict)
