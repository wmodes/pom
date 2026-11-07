"""The TerminalPort: formatted text goes in, the typewriter types it; what a person types on it comes back.

The Wheelwriter, converted with the CadetWriter board, acts as a serial terminal (hence the config section
`[terminal]`). `type(text)` sends a formatted portent to it and returns when it has been typed. It is the last step
after the PortentFormatter. The Conductor pauses listening, calls `transmit`, and resumes listening when it returns:
this module knows nothing about listening. `read_line()` is the other direction: someone at the keyboard can type
back, and each finished line (the C Rtn key) is returned.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A port that is missing, will not open or fails halfway gives
    a PrintResult that says the text was not all typed, and a warning in `report`; never an exception. `strict=True`
    raises TerminalPortError instead, for tests and dev tools. The port is opened on the first call and reopened
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
    for its whole run. Unless `type_during_printing` is on (it is off by default) whatever was typed during it, and any half-typed
    line, is thrown away when it finishes, so nothing typed over a portent appears after it. Turned on, those keys
    are kept and handled by the next `read_line`.
  * STAYING WITH THE PORTENT. A serial printer is fickle: it is switched off, unplugged and plugged back in, held, out of paper. Once a portent
    is under way, a write that fails does not end it. A STALL (the machine stopped accepting characters: a hold, no paper, busy; pyserial
    raises a write timeout when flow control holds the line) is waited out, and the same character is sent again, so nothing is
    repeated. A LOST PORT (unplugged, an I/O error) is closed and opened again every `reconnect_seconds`, and typing goes on from the first
    character not sent (a character or two in flight at the moment of the unplug may be lost or doubled; nothing can know). Either way the
    wait lasts at most `stall_patience_seconds`, after which the text is given up (CRITICAL, with how far it got), and meanwhile the
    caller's `still_alive` function is called every few seconds so the Conductor's watchdog knows this is a wait and not a hang. Pacing
    restarts from now after a wait, so the rest is not flooded out at once. A port that will not open at the START is not waited for here:
    `transmit` returns at once with nothing typed, and the caller (the TurnTaker) decides how long to keep trying. What software cannot see:
    whether the CadetWriter signals a hold or out-of-paper through flow control (if it does not, nothing in the code can tell), and a
    printer that is powered off behind a USB adapter that swallows the bytes.
  * THE PORT NAME MAY BE A PATTERN (`/dev/ttyACM*`, `/dev/serial/by-id/usb-*`). It is looked up each time the port is opened, and the
    first match (in sorted order) is used, so a printer that comes back under another name (ttyACM0, then ttyACM1) is found.
  * A port that cannot be opened is one warning for the whole stretch it stays unavailable, not one per try, and a line in the log when it
    opens again.
  * What is typed in the room is room content: like the portent it is never logged or put in a report.
  * The settings are passed in (the entry point takes them from `config.terminal`); this module never reads Config.
  * The way to open the port, the clock and the sleep are injected, so tests run with a fake and need no machine, and
    the ConsoleTerminal (the stand-in for development) is this same code writing to the terminal.
"""
import collections
import glob
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
DEFAULT_STALL_PATIENCE_SECONDS = 600.0   # how long a portent under way is waited for if the machine stops or goes away
DEFAULT_RECONNECT_SECONDS = 2.0          # how often a lost port is tried again
DEFAULT_REFUSED_CHARACTER_RETRY_SECONDS = 1.0   # the pause before sending a character the machine refused (the write itself waits too)
DEFAULT_WAIT_LOG_REMINDER_SECONDS = 60.0        # a long wait is mentioned in the log this often
DEFAULT_MAX_TYPED_LINE_CHARACTERS = 500         # a typed line this long is ended there, so a stuck key cannot grow it without limit
_READ_SLICE_SECONDS = 0.1   # one look at the port; the lock is free between looks, so a portent can start


class TerminalPortError(ValueError):
    """Raised for any port problem, but only in strict mode (development and tests)."""


@dataclass(frozen=True, slots=True)
class PrintResult:
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


class TerminalPort:
    """Sends formatted text to the typewriter over a serial port, paced.

    Attributes:
        report: Problems met (port will not open, write failed, bad setting). Never holds the text.
    """

    def __init__(self, port: str, baud: int = DEFAULT_BAUD, flow_control: str = DEFAULT_FLOW_CONTROL,
                 end_of_line: str = DEFAULT_END_OF_LINE, characters_per_second: float = DEFAULT_CHARACTERS_PER_SECOND,
                 finish_wait_seconds: float = DEFAULT_FINISH_WAIT_SECONDS,
                 echoplex: bool = True, type_during_printing: bool = False,
                 stall_patience_seconds: float = DEFAULT_STALL_PATIENCE_SECONDS, reconnect_seconds: float = DEFAULT_RECONNECT_SECONDS,
                 max_typed_line_characters: int = DEFAULT_MAX_TYPED_LINE_CHARACTERS,
                 refused_character_retry_seconds: float = DEFAULT_REFUSED_CHARACTER_RETRY_SECONDS,
                 wait_log_reminder_seconds: float = DEFAULT_WAIT_LOG_REMINDER_SECONDS,
                 opener: Optional[Callable[[str, int, str], object]] = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, lister: Callable[[str], list] = glob.glob, strict: bool = False):
        """Args:
            port: The serial device, e.g. /dev/ttyACM0, or a pattern such as /dev/ttyACM* (looked up each time it is opened).
            baud: The speed; only the RS-232 port uses it (USB ignores it). One of BAUD_RATES.
            flow_control: "xon_xoff", "rts_cts" or "none".
            end_of_line: What a newline is sent as: "cr", "crlf", "lf" or "lfcr". Must match the CadetWriter's setting.
            characters_per_second: The fastest the text is sent, above 0.
            finish_wait_seconds: How long to wait after the last byte for the machine to finish, 0 or more.
            echoplex: True to echo each key typed on the typewriter back to it (the CadetWriter must be in full duplex).
            type_during_printing: False to throw away keys typed while a portent is being typed; True keeps them for the next read.
            stall_patience_seconds: How long to wait, once typing has begun, for a machine that stopped or went away, above 0.
            reconnect_seconds: How often to try a lost port again, above 0.
            max_typed_line_characters: A line typed on the keyboard is ended at this many characters, a whole number of at least 1.
            refused_character_retry_seconds: The pause before a character the machine refused is sent again, above 0.
            wait_log_reminder_seconds: A long wait for the machine is mentioned in the log this often, above 0.
            lister: A function pattern -> list of matching device names. Default: glob.glob. Tests give a fake.
            opener: A function (port, baud, flow_control) -> an object with write(bytes), flush(), close(), read(size)
                and reset_input_buffer(). Default: the real serial port.
            clock: Returns seconds, steadily increasing.
            sleep: Waits for that many seconds.
            strict: Raise TerminalPortError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=TerminalPortError, logger=_log)
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
        self._stall_patience = self._valid(
            stall_patience_seconds, "stall_patience_seconds", DEFAULT_STALL_PATIENCE_SECONDS,
            lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0, "a number above 0")
        self._reconnect = self._valid(
            reconnect_seconds, "reconnect_seconds", DEFAULT_RECONNECT_SECONDS,
            lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0, "a number above 0")
        self._max_typed_line = self._valid(
            max_typed_line_characters, "max_typed_line_characters", DEFAULT_MAX_TYPED_LINE_CHARACTERS,
            lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 1, "a whole number of at least 1")
        self._refused_retry = self._valid(
            refused_character_retry_seconds, "refused_character_retry_seconds", DEFAULT_REFUSED_CHARACTER_RETRY_SECONDS,
            lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0, "a number above 0")
        self._wait_reminder = self._valid(
            wait_log_reminder_seconds, "wait_log_reminder_seconds", DEFAULT_WAIT_LOG_REMINDER_SECONDS,
            lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0, "a number above 0")
        self._lister = lister
        self._connect_failing = False            # the port has been unavailable since a warning was given
        self._opener = opener if opener is not None else _open_serial
        self._clock = clock
        self._sleep = sleep
        self._echoplex = bool(echoplex)
        self._type_during_printing = bool(type_during_printing)
        self._serial = None
        self._lock = threading.Lock()            # one use of the line at a time: a portent, or a look for keys
        self._received: collections.deque = collections.deque()   # bytes read but not yet handled
        self._line: list[str] = []               # the line being typed on the keyboard
        self._after_cr = False                   # the last byte handled was a CR, so a following LF is its pair
        self._usable = isinstance(port, str) and bool(port.strip())
        if not self._usable:
            self.report.warn(f"the terminal port must be a device name, got {port!r}; nothing will be typed")

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

    def online(self, wait: bool = True) -> bool:
        """True if the typewriter's port is open, or opens now. Asks quietly (no warning, nothing typed), so it can be asked again and again.

        `wait` says what to do while a portent is being typed (typing holds the port for minutes): True waits for it to finish; False does not
        wait and answers True, since a terminal that is being typed on is there. The monitoring page asks with False so its polling never stalls.
        """
        if not self._lock.acquire(blocking=wait):
            return True
        try:
            return self._usable and self._connect(quiet=True)
        finally:
            self._lock.release()

    def transmit(self, text: str, still_alive: Optional[Callable[[], object]] = None) -> PrintResult:
        """Types the text and returns when the typewriter has had time to finish. Never raises (except in strict mode).

        `completed` is False if any of it was not sent; the reason is in `report`. Text with nothing to type is
        completed at once, without opening the port. A port that will not open returns at once with nothing typed. Once typing has
        begun, a machine that stops or goes away is waited for (see STAYING WITH THE PORTENT), up to `stall_patience_seconds`;
        `still_alive`, if given, is called every few seconds while waiting.
        """
        started = self._clock()
        data = self._bytes(text) if isinstance(text, str) else ""
        if not data:
            return PrintResult(0, 0.0, self._usable)
        with self._lock:
            if not self._usable or not self._connect():
                return PrintResult(0, self._clock() - started, False)
            self._discard_input()
            sent = 0
            pace_from = started                      # when character 0 would be due; moves on after a wait
            trouble = _Trouble()
            while sent < len(data):
                due = pace_from + sent * self._seconds_per_character
                if due > self._clock():
                    self._sleep(due - self._clock())
                try:
                    self._serial.write(data[sent].encode("ascii"))
                except Exception as error:           # a dead or held port must never stop the oracle
                    if not self._wait_to_go_on(error, trouble, sent, len(data), still_alive):
                        return PrintResult(sent, self._clock() - started, False)
                    pace_from = self._clock() - sent * self._seconds_per_character
                    continue
                sent += 1
                if trouble.since is not None:
                    trouble.over(self._clock())
            try:
                self._serial.flush()
            except Exception as error:               # a dead port must never stop the oracle
                self.report.warn(f"the terminal port failed at the end, after all {sent} characters were sent "
                                 f"({type(error).__name__})")
                self.close()
                return PrintResult(sent, self._clock() - started, False)
            if self._finish_wait:
                self._sleep(self._finish_wait)
            self._discard_input()
            seconds = self._clock() - started
            _log.debug("typed %d characters in %.1f s", sent, seconds)
            return PrintResult(sent, seconds, True)

    @staticmethod
    def _is_stall(error: Exception) -> bool:
        """True if the machine stopped accepting characters (a write timeout under flow control), as against the port being lost."""
        return isinstance(error, TimeoutError) or type(error).__name__ == "SerialTimeoutException"

    def _wait_to_go_on(self, error: Exception, trouble: "_Trouble", sent: int, total: int, still_alive) -> bool:
        """A write failed part-way. Waits until typing can go on: True if it can, False if the patience ran out (CRITICAL).

        A stall keeps the port and sends the same character again after a short pause; a lost port is closed and tried again every
        `reconnect_seconds`. The first failure of a stretch is a warning and each minute after is a reminder in the log; the end of the
        trouble is a line in the log. Only counts are ever mentioned, never the text.
        """
        stalled = self._is_stall(error)
        now = self._clock()
        if trouble.since is None:
            trouble.begin(now)
            self.report.warn(f"the terminal {'stopped taking characters (held, out of paper or busy)' if stalled else 'port was lost'} "
                             f"after {sent} of {total} characters ({type(error).__name__}); waiting for it for up to "
                             f"{self._stall_patience:g} s")
        while True:
            now = self._clock()
            if now - trouble.since >= self._stall_patience:
                self.report.critical(f"the terminal did not come back within {self._stall_patience:g} s, after {sent} of {total} characters; "
                                     f"the rest was not typed")
                self.close()
                return False
            if still_alive is not None:
                try:
                    still_alive()
                except Exception:                    # a failing heartbeat must not end the wait
                    pass
            if now - trouble.reminded >= self._wait_reminder:
                trouble.reminded = now
                _log.warning("the terminal is still not typing (%.0f s so far, %d of %d characters sent)", now - trouble.since, sent, total)
            if stalled:
                self._sleep(self._refused_retry)
                return True                          # the port is open: send the same character again
            # the port was lost: close it and open it again, every few seconds
            self.close()
            self._sleep(self._reconnect)
            if self._connect(quiet=True):
                return True

    def read_line(self, timeout: Optional[float] = None) -> Optional[str]:
        """The next line typed on the typewriter's keyboard, or None if none was finished within `timeout` seconds.

        Waits until C Rtn is pressed (or `timeout` passes; None waits for ever). A line typed halfway when the time ran
        out is kept for the next call. An empty line, from C Rtn alone, is returned as ''. With `echoplex` each key is
        echoed to the paper as it arrives. Keys pressed during a portent wait for it (see type_during_printing). Never
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
                    self.report.warn(f"the terminal port failed while listening for the keyboard "
                                     f"({type(error).__name__}); try again later")
                    self.close()
                    self._sleep(1.0 if deadline is None else max(0.0, min(1.0, deadline - self._clock())))   # no hot loop on a dead port
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
            if len(self._line) >= self._max_typed_line:
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
                self.report.warn(f"the terminal port failed while echoing the keyboard ({type(error).__name__})")
                self.close()

    def _discard_input(self) -> None:
        """If keys are not wanted during a portent: forget everything typed and still unread, and any half-typed line."""
        if self._type_during_printing:
            return
        self._received.clear()
        self._line.clear()
        self._after_cr = False
        try:
            self._serial.reset_input_buffer()
        except Exception:                            # a dead port is found by the next write
            pass

    def _resolve(self) -> Optional[str]:
        """The device to open: the port name, or the first device matching it if it is a pattern. None if nothing matches."""
        name = self._port_name
        if not any(wildcard in name for wildcard in "*?["):
            return name
        try:
            matches = sorted(self._lister(name))
        except Exception:                            # a failing lookup is the same as no match
            matches = []
        return matches[0] if matches else None

    def _connect(self, quiet: bool = False) -> bool:
        """Opens the port if it is not open. False if it will not open: one warning for the whole stretch it stays unavailable."""
        if self._serial is not None:
            return True
        name = self._resolve()
        try:
            if name is None:
                raise FileNotFoundError("no device matches the port pattern")
            self._serial = self._opener(name, self._baud, self._flow_control)
        except Exception as error:
            if not self._connect_failing and not quiet:
                self.report.warn(f"the terminal port {self._port_name!r} would not open ({type(error).__name__}); nothing was typed")
            self._connect_failing = self._connect_failing or not quiet
            return False
        if self._connect_failing:
            _log.info("the terminal port %s is open again", name)
        self._connect_failing = False
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


class _Trouble:
    """A stretch in which the machine would not take characters: when it began and when it was last mentioned."""

    def __init__(self):
        self.since = None
        self.reminded = 0.0

    def begin(self, now: float) -> None:
        self.since, self.reminded = now, now

    def over(self, now: float) -> None:
        _log.info("the terminal is typing again after %.0f s", now - self.since)
        self.since = None


class _StreamSink:
    """Looks like a serial port to the TerminalPort but types on a text stream and reads keys from another, for the ConsoleTerminal."""

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


class ConsoleTerminal(TerminalPort):
    """A stand-in for the typewriter that types on the terminal at the same pace, for development.

    It is the TerminalPort writing to a stream instead of a serial port, so the pacing, the safe-byte filter and
    the report are exactly the real ones. Anything that takes a TerminalPort takes this.

    Args:
        stream: Where to transmit; default standard output.
        input_stream: Where the "keyboard" is: what is typed there is what `read_line` returns; default standard input.
        characters_per_second: The pace; 16 like the real one. A big number types at once.
        finish_wait_seconds: Wait after the last character; default none.
        echoplex: Echo the keys back; off by default because the terminal already shows what is typed.
        type_during_printing: False throws away keys typed while it types, like the real one.
    """

    def __init__(self, stream=None, input_stream=None, characters_per_second: float = DEFAULT_CHARACTERS_PER_SECOND,
                 finish_wait_seconds: float = 0.0, echoplex: bool = False, type_during_printing: bool = False,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 strict: bool = False):
        sink = _StreamSink(stream if stream is not None else sys.stdout,
                           input_stream if input_stream is not None else sys.stdin)
        super().__init__("console", end_of_line="lf", characters_per_second=characters_per_second,
                         finish_wait_seconds=finish_wait_seconds, echoplex=echoplex,
                         type_during_printing=type_during_printing, opener=lambda port, baud, flow: sink,
                         clock=clock, sleep=sleep, strict=strict)
