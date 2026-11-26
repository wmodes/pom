"""The TerminalPort: formatted text goes in, the typewriter types it; what a person types on it comes back.

The Wheelwriter, converted with the CadetWriter board, acts as a serial terminal (hence the config section
`[terminal]`). `type(text)` sends a formatted portent to it and returns when it has been typed. It is the last step
after the PortentFormatter. The Conductor pauses listening, calls `transmit`, and resumes listening when it returns:
this module knows nothing about listening. `read_line()` is the other direction: someone at the keyboard can type
back, and each finished line (the C Rtn key) is returned.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A port that is missing, will not open or fails halfway gives
    a PrintResult that says the text was not all typed, and a warning in the log; never an exception. `strict=True`
    raises TerminalPortError instead, for tests and dev tools. The port is opened on the first call and reopened
    after a failure, so a typewriter that is switched on late is found.
  * The portent is never logged: nothing that was said in the room is kept. The log holds counts
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
    `finish_wait_seconds` more before returning. Only then does the Conductor resume listening. Before that it waits until the machine has
    TAKEN every byte (pyserial's `out_waiting` is zero): a write returns once the bytes are in the port's queue, so a short portent can be
    "sent" in full while the machine is held. It never calls `flush()`, which has no time limit and would wait for ever on a held machine.
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
  * THE PORT IS FOUND BY NAME, BY A FINDER (`auto`, or any part of the USB name or path; see TerminalPortFinder and pom/device_finder.py). It is
    asked each time the port is opened, so a printer that comes back under another name (ttyUSB0, then ttyUSB1) is found. With no finder the
    port name is opened as written. The wait between tries for a port that is not there is `reconnect_seconds`, for ever.
  * A port that cannot be opened is one warning for the whole stretch it stays unavailable, not one per try, and a line in the log when it
    opens again.
  * What is typed in the room is room content: like the portent it is never logged.
  * The settings are passed in (the entry point takes them from `config.terminal`); this module never reads Config.
  * The way to open the port, the clock and the sleep are injected, so tests run with a fake and need no machine, and
    the ConsoleTerminal (the stand-in for development) is this same code writing to the terminal.
"""
import collections
import dataclasses
import os
import select
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from pom.bookkeeper.bookkeeper import Bookkeeper
from pom.defaults import TerminalSettings

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module

PARITIES = {"none": "N", "even": "E", "odd": "O"}         # the setting's word -> pyserial's letter
END_OF_LINES = {"cr": "\r", "crlf": "\r\n", "lf": "\n", "lfcr": "\n\r"}
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


def _open_serial(port: str, baud: int, flow_control: str, data_bits: int, parity: str, stop_bits: int):
    """Opens the real serial port (pyserial is imported here, so a missing library is a logged problem, not a crash)."""
    import serial
    return serial.Serial(port, baud, bytesize=data_bits, parity=PARITIES[parity], stopbits=stop_bits, timeout=_READ_SLICE_SECONDS, write_timeout=10,
                         xonxoff=flow_control == "xon_xoff", rtscts=flow_control == "rts_cts")


class TerminalPort:
    """Sends formatted text to the typewriter over a serial port, paced.

    Attributes:
        logger: Where this class says what it has to say (a LogRecorder from the Bookkeeper).
        terminal: The `[terminal]` settings it was handed.
    """

    def __init__(self, terminal: TerminalSettings, opener: Optional[Callable[[str, int, str, int, str, int], object]] = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep, finder: Optional[object] = None,
                 strict: bool = False, bookkeeper: Optional[Bookkeeper] = None):
        """Args:
            terminal: The `[terminal]` section: the `port` (a device name such as /dev/ttyACM0, or `auto` or part of a name for the finder
                to look up each time it is opened), the line's `baud`, `data_bits`, `parity`, `stop_bits`, `flow_control` and `end_of_line`
                (each must match the machine's own setting), the pace (`characters_per_second`, `finish_wait_seconds`), the keyboard
                (`echoplex`, `type_during_printing`, `max_typed_line_characters`) and the waits (`stall_patience_seconds`,
                `reconnect_seconds`, `refused_character_retry_seconds`).
            finder: Anything with `resolve(setting) -> device name` (a TerminalPortFinder), asked each time the port is opened. None opens `port` as written.
            opener: A function (port, baud, flow_control, data_bits, parity, stop_bits) -> an object with write(bytes), close(), read(size)
                and reset_input_buffer(), and optionally `out_waiting` (the bytes accepted but not yet taken). Default: the real serial port.
            clock: Returns seconds, steadily increasing.
            sleep: Waits for that many seconds.
            strict: Raise TerminalPortError on any problem instead of logging it.
            bookkeeper: The Bookkeeper the entry point built; it hands this class its logger. None (a test or a tool) gives a plain one.
        """
        self.logger = (bookkeeper or Bookkeeper()).logger_for(__name__, TerminalPortError, DEBUG, strict)
        self.terminal = terminal
        port = terminal.port
        self._port_name = port
        self._end_of_line = END_OF_LINES[terminal.end_of_line]
        self._seconds_per_character = 1.0 / terminal.characters_per_second
        self._finder = finder
        self._connect_failing = False            # the port has been unavailable since a warning was given
        self._opener = opener if opener is not None else _open_serial
        self._clock = clock
        self._sleep = sleep
        self._serial = None
        self._lock = threading.Lock()            # one use of the line at a time: a portent, or a look for keys
        self._received: collections.deque = collections.deque()   # bytes read but not yet handled
        self._line: list[str] = []               # the line being typed on the keyboard
        self._after_cr = False                   # the last byte handled was a CR, so a following LF is its pair
        self._usable = isinstance(port, str) and bool(port.strip())
        if not self._usable:
            self.logger.warning("the terminal port must be a device name, got %r; nothing will be typed", port)

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

        `completed` is False if any of it was not sent; the reason is in the log. Text with nothing to type is
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
                    self.logger.info("the terminal is typing again after %.0f s", trouble.over(self._clock()))
            try:
                left = self._wait_until_taken(sent, len(data), trouble, still_alive)
            except Exception as error:               # a dead port must never stop the oracle
                self.logger.warning("the terminal port failed at the end, after all %s characters were sent (%s)", sent, type(error).__name__)
                self.close()
                return PrintResult(sent, self._clock() - started, False)
            if left:                                 # the patience ran out (reported, and the port closed, by the wait)
                return PrintResult(max(0, sent - left), self._clock() - started, False)
            if self.terminal.finish_wait_seconds:
                self._sleep(self.terminal.finish_wait_seconds)
            self._discard_input()
            seconds = self._clock() - started
            self.logger.debug("typed %d characters in %.1f s", sent, seconds)
            return PrintResult(sent, seconds, True)

    def _queued(self) -> int:
        """How many bytes the port has accepted that the machine has not yet taken (pyserial's `out_waiting`); 0 for a port that cannot say."""
        return getattr(self._serial, "out_waiting", 0) or 0

    def _wait_until_taken(self, sent: int, total: int, trouble: "_Trouble", still_alive) -> int:
        """Waits until the machine has taken everything the port accepted. 0 once it has; the bytes still waiting if the patience ran out.

        `write` returns when the bytes are in the port's queue, not when the machine has them, so a held machine does not make a write fail
        for a short portent. A queue that is going down is the machine taking its time; one that is not going down is a stall, waited out
        exactly like a stalled write. (The count of characters it logs is bytes, so it is a character or two out where a line ends
        in two bytes.)
        """
        previous = None
        while True:
            queued = self._queued()
            stalled = previous is not None and queued >= previous
            if not stalled and trouble.since is not None:
                self.logger.info("the terminal is typing again after %.0f s", trouble.over(self._clock()))     # it is taking them again, or has taken them all
            if queued == 0:
                return 0
            if stalled:
                if not self._wait_to_go_on(TimeoutError("the machine is not taking what is queued"), trouble, max(0, sent - queued), total,
                                           still_alive):
                    return queued
            else:
                self._sleep(self.terminal.refused_character_retry_seconds)     # going down, or the first look: give it a moment and look again
            previous = queued

    @staticmethod
    def _is_stall(error: Exception) -> bool:
        """True if the machine stopped accepting characters (a write timeout under flow control), as against the port being lost."""
        return isinstance(error, TimeoutError) or type(error).__name__ == "SerialTimeoutException"

    def _wait_to_go_on(self, error: Exception, trouble: "_Trouble", sent: int, total: int, still_alive) -> bool:
        """A write failed part-way. Waits until typing can go on: True if it can, False if the patience ran out (CRITICAL).

        A stall keeps the port and sends the same character again after a short pause; a lost port is closed and tried again every
        `reconnect_seconds`. The first failure of a stretch is a warning and every try after it says the terminal is still not typing (the log
        mutes the repeats); the end of the
        trouble is a line in the log. Only counts are ever mentioned, never the text.
        """
        stalled = self._is_stall(error)
        now = self._clock()
        first = trouble.since is None
        if first:
            trouble.begin(now)
            self.logger.warning("the terminal %s after %s of %s characters (%s); waiting for it for up to %g s",
                                "stopped taking characters (held, out of paper or busy)" if stalled else "port was lost", sent, total,
                                type(error).__name__, self.terminal.stall_patience_seconds)
        while True:
            now = self._clock()
            if now - trouble.since >= self.terminal.stall_patience_seconds:
                self.logger.critical("the terminal did not come back within %g s, after %s of %s characters; the rest was not typed",
                                     self.terminal.stall_patience_seconds, sent, total)
                self.close()
                return False
            if still_alive is not None:
                try:
                    still_alive()
                except Exception as problem:         # a failing heartbeat must not end the wait
                    self.logger.warning("the heartbeat failed (%s); the wait goes on", type(problem).__name__)
            if not first:
                self.logger.warning("the terminal is still not typing (%.0f s so far, %d of %d characters sent)", now - trouble.since, sent, total)
            first = False
            if stalled:
                self._sleep(self.terminal.refused_character_retry_seconds)
                return True                          # the port is open: send the same character again
            # the port was lost: close it and open it again, every few seconds
            self.close()
            self._sleep(self.terminal.reconnect_seconds)
            if self._connect(quiet=True):
                return True

    def read_line(self, timeout: Optional[float] = None) -> Optional[str]:
        """The next line typed on the typewriter's keyboard, or None if none was finished within `timeout` seconds.

        Waits until C Rtn is pressed (or `timeout` passes; None waits for ever). A line typed halfway when the time ran
        out is kept for the next call. An empty line, from C Rtn alone, is returned as ''. With `echoplex` each key is
        echoed to the paper as it arrives. Keys pressed during a portent wait for it (see type_during_printing). Never
        raises (except in strict mode); None can also mean the port would not open, with the reason in the log.
        """
        deadline = None if timeout is None else self._clock() + timeout
        while True:
            while self._received:
                line = self._accept(self._received.popleft())
                if line is not None:
                    return line
            if deadline is not None and self._clock() >= deadline:
                return None
            pause = None                             # set when the port is not there: the wait is done after the lock is let go
            with self._lock:
                if not self._usable or not self._connect():
                    pause = self.terminal.reconnect_seconds if deadline is None else max(0.0, deadline - self._clock())
                else:
                    try:
                        data = self._serial.read(max(1, getattr(self._serial, "in_waiting", 0) or 1))
                    except Exception as error:       # a dead port must never stop the oracle
                        self.logger.warning("the terminal port failed while listening for the keyboard (%s); try again later", type(error).__name__)
                        self.close()
                        pause = self.terminal.reconnect_seconds if deadline is None else max(0.0, min(self.terminal.reconnect_seconds, deadline - self._clock()))   # no hot loop on a dead port
            if pause is not None:
                self._sleep(pause)                   # not under the lock: a held lock reads as "being typed on", so the printer would look online
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
            if len(self._line) >= self.terminal.max_typed_line_characters:
                line, self._line = "".join(self._line), []
                self._echo(self._end_of_line)
                return line
        return None

    def _echo(self, text: str) -> None:
        """Sends the keyboard's echo to the typewriter at once (a person types slowly; no pacing). A failure is recorded."""
        if not self.terminal.echoplex or self._serial is None:      # no echo wanted, or the port just failed (already reported)
            return
        with self._lock:
            try:
                self._serial.write(text.encode("ascii"))
            except Exception as error:               # a dead port must never stop the oracle
                self.logger.warning("the terminal port failed while echoing the keyboard (%s)", type(error).__name__)
                self.close()

    def _discard_input(self) -> None:
        """If keys are not wanted during a portent: forget everything typed and still unread, and any half-typed line."""
        if self.terminal.type_during_printing:
            return
        self._received.clear()
        self._line.clear()
        self._after_cr = False
        try:
            self._serial.reset_input_buffer()
        except Exception:                            # a dead port is found by the next write
            pass

    def _resolve(self) -> str:
        """The device to open: what the finder says the port setting means now, or the port name as written if there is no finder."""
        return self._finder.resolve(self._port_name) if self._finder is not None else self._port_name

    def _connect(self, quiet: bool = False) -> bool:
        """Opens the port if it is not open. False if it will not open: one warning for the whole stretch it stays unavailable."""
        if self._serial is not None:
            return True
        name = self._port_name
        try:
            name = self._resolve()                   # inside the try: a finder that breaks is a port that will not open, not a crash
            self._serial = self._opener(name, self.terminal.baud, self.terminal.flow_control, self.terminal.data_bits, self.terminal.parity, self.terminal.stop_bits)
        except Exception as error:
            if not self._connect_failing and not quiet:
                self.logger.warning("the terminal port %r would not open (%s); nothing was typed", self._port_name, type(error).__name__)
            self._connect_failing = self._connect_failing or not quiet
            return False
        if self._connect_failing:
            self.logger.info("the terminal port %s is open again", name)
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
    """A stretch in which the machine would not take characters: when it began."""

    def __init__(self):
        self.since = None

    def begin(self, now: float) -> None:
        self.since = now

    def over(self, now: float) -> float:
        """Ends the stretch; returns how many seconds it lasted."""
        lasted = now - self.since
        self.since = None
        return lasted


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
    the log lines are exactly the real ones. Anything that takes a TerminalPort takes this.

    Args:
        terminal: The `[terminal]` section: the pace (`characters_per_second`, `finish_wait_seconds`) and `type_during_printing` are
            the real ones; the port is the console and a line ends with a plain line feed.
        stream: Where to transmit; default standard output.
        input_stream: Where the "keyboard" is: what is typed there is what `read_line` returns; default standard input.
        echoplex: Echo the keys back; off by default because a screen already shows what is typed.
    """

    def __init__(self, terminal: TerminalSettings, stream=None, input_stream=None, echoplex: bool = False,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 strict: bool = False, bookkeeper: Optional[Bookkeeper] = None):
        sink = _StreamSink(stream if stream is not None else sys.stdout,
                           input_stream if input_stream is not None else sys.stdin)
        super().__init__(dataclasses.replace(terminal, port="console", end_of_line="lf", echoplex=echoplex), opener=lambda *line: sink,
                         clock=clock, sleep=sleep, strict=strict, bookkeeper=bookkeeper)
