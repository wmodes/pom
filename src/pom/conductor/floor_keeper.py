"""The FloorKeeper: who may speak, the room or the Printer, one at a time.

The Printer is half-duplex. While it takes a turn (the oracle model writes, then the terminal prints) it must not listen, or it
would hear its own printing. The FloorKeeper keeps that rule. The floor is in one of four states, and a turn moves through them
in order:

    listening -> writing -> printing -> recovering -> listening

  * LISTENING: nobody holds the floor. The room and the keyboard may both offer a hinge.
  * WRITING: a hinge qualified and the oracle model is writing the portent.
  * PRINTING: the portent is being formatted and typed on the terminal.
  * RECOVERING: the turn is over, and the ears stay shut a few seconds more so the terminal's last sounds (the carriage
    return, the bell) die away before the Printer listens again.

`take_floor(who)` is first come, first served: the first of the room and the keyboard to ask gets a Turn; the other gets None.
A Turn is a small handle with `start_printing()` and `finish()`, and it is a context manager, so the floor is yielded even
when the work in the `with` fails:

    turn = floor.take_floor("room")
    if turn is not None:
        with turn:
            ...write...
            turn.start_printing()
            ...print...

Design decisions, and why:
  * THE PRINTER MUST FAIL TOWARD SPEAKING. A bug here may cost one missed turn; it must never leave the floor held for good, which
    would silence the Printer. So every way a turn can end (it finished, it raised, or it ran too long) ends in RECOVERING and then
    LISTENING, and nothing here waits on anything.
  * THE WATCHDOG. Each of WRITING and PRINTING has a limit, and a turn that runs past it loses the floor: the keeper moves to
    RECOVERING, records a WARNING (a fault it recovered from) and counts an overrun. The watchdog cannot stop a thread that is stuck; it
    only takes the floor back, so the Printer listens again. Whoever does the work (the TurnTaker) must give each blocking
    call a timeout of its own, so that the watchdog is the last line of defense and not the first.
  * THE LIMITS MUST COVER THE LONGEST LIKELY RUN, NOT THE AVERAGE. Both limits are generous on purpose.
      - Too short and a healthy, slow turn is cut off while it is still working. The floor goes back to listening and, after the
        recovery, the ears open while the terminal is still typing, so the Printer can hear itself and react to its own printing.
        Worse, a limit near the average would cut off exactly the long portents, so the Printer would say only short and medium ones.
      - Too long and a turn that is really stuck (a hung request, a dead serial port) keeps the Printer deaf until the limit runs out.
    So the cost of too short is a wrong answer and the cost of too long is a quiet spell, and the limits err long. The WRITING limit
    must be longer than the oracle client's own timeout (`[oracle] timeout`, which makes a slow request give up first), and the PRINTING
    limit longer than the time it takes to type the longest portent the oracle can write (`max_tokens`) at the terminal's pace.
  * LAZY EXPIRY. Every call first looks at the clock and settles anything that has run out (an overrun, the end of the recovery), so
    no ticker thread has to survive for the watchdog to work: the next call, from anywhere, clears a stuck state. Reading `state` is
    such a call.
  * STALE TURNS ARE HARMLESS. When the watchdog takes the floor from a turn that is merely slow, that turn's thread may wake later and
    call `start_printing()` or `finish()`. Its Turn is no longer the current one, so these calls do nothing (they return False) and
    cannot disturb the next turn. The caller never handles a turn number; the Turn object carries it.
  * Time is the caller's (a clock function, by default `time.monotonic`): the Pi has no battery clock, so its wall clock jumps when the
    network sets it at boot, and a monotonic clock does not. Tests give a clock they move by hand.
  * The lock is in memory only (no file, so nothing can be left stuck by a crash), and a call that cannot get it within
    `LOCK_TIMEOUT_SECONDS` records a CRITICAL fault and answers safely (no turn is given, the state reads as listening) instead of
    waiting for ever. Nothing inside the lock waits on anything, so this should never happen.
  * Nothing here may stop the oracle (see pom/__init__.py). A bad setting is replaced by its default and recorded in `report`.
    `strict=True` raises FloorKeeperError instead, for tests and dev tools.
  * Who holds the floor is a short label ("room", "keyboard"), never anything that was said. Nothing said in the room is seen here.
  * LOGGING, so a problem can be read afterwards in the logs. At INFO, one line for every change of state, saying who holds the floor and
    how long the state before it lasted ("floor: writing -> printing (room) after 41.2 s"), one when a turn is over with its total time,
    one when someone asks for the floor and cannot have it, and one when a turn that lost the floor to the watchdog wakes up later and
    calls in (ignored). An overrun is also a WARNING in `report`, and so reaches the log. A lock that could not be had is CRITICAL.
    Only labels and seconds are logged.
"""
import math
import threading
import time
from contextlib import contextmanager
from enum import Enum
from typing import Callable, Iterator, Optional

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_WRITING_SECONDS = 300.0       # longer than the oracle client's timeout (120 s now; a long portent may need more), with room to spare
DEFAULT_PRINTING_SECONDS = 180.0      # the longest portent (300 tokens, about 1,400 characters) is typed in about 90 s at 16 characters a second
DEFAULT_RECOVERING_SECONDS = 3.0      # for the terminal's last sounds to die away
LOCK_TIMEOUT_SECONDS = 5.0            # the longest a call waits for the lock; the work inside it takes microseconds
UNKNOWN_WHO = "unknown"


class FloorKeeperError(ValueError):
    """Raised for any FloorKeeper problem, but only in strict mode (development and tests)."""


class FloorState(Enum):
    """Where the floor is."""
    LISTENING = "listening"
    WRITING = "writing"
    PRINTING = "printing"
    RECOVERING = "recovering"


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class Turn:
    """One turn at the floor: what `take_floor` hands back. It is a context manager that yields the floor when the `with` ends.

    Once the turn is over, or the watchdog has taken the floor from it, every call on it does nothing and returns False.
    """

    def __init__(self, keeper: "FloorKeeper", holder: str):
        self._keeper = keeper
        self.holder = holder
        self._ended: Optional[str] = None     # None while the turn is going; "finished" or "lost" (to the watchdog) after

    def start_printing(self) -> bool:
        """Writing is done and printing begins. True if that happened; False if this turn no longer holds the floor, or is not writing."""
        return self._keeper._start_printing(self)

    def finish(self) -> bool:
        """The turn is over: the floor goes to RECOVERING. True if that happened; False if it was already over. Safe to repeat."""
        return self._keeper._finish(self)

    def __enter__(self) -> "Turn":
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.finish()
        return False                  # never hides the error that ended the work


class FloorKeeper:
    """Keeps the floor: the state, the one-turn-at-a-time rule and the watchdog.

    Attributes:
        report: Problems met (a bad setting, an overrun that was cut off).
        overruns: How many turns lost the floor to the watchdog.
        turns_taken: How many turns have been given out.
    """

    def __init__(self, writing_seconds: float = DEFAULT_WRITING_SECONDS, printing_seconds: float = DEFAULT_PRINTING_SECONDS,
                 recovering_seconds: float = DEFAULT_RECOVERING_SECONDS, clock: Optional[Callable[[], float]] = None,
                 strict: bool = False):
        """Args:
            writing_seconds: The longest the oracle model may take to write one portent, above 0 (see the limits above).
            printing_seconds: The longest the formatting and typing of one portent may take, above 0.
            recovering_seconds: How long the ears stay shut after a turn, 0 or more (0 means none).
            clock: A function returning seconds that never go backwards. Default: time.monotonic.
            strict: Raise FloorKeeperError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=FloorKeeperError, logger=_log)
        self._writing_seconds = self._valid(writing_seconds, "writing_seconds", DEFAULT_WRITING_SECONDS, lambda v: v > 0, "above 0")
        self._printing_seconds = self._valid(printing_seconds, "printing_seconds", DEFAULT_PRINTING_SECONDS, lambda v: v > 0, "above 0")
        self._recovering_seconds = self._valid(recovering_seconds, "recovering_seconds", DEFAULT_RECOVERING_SECONDS, lambda v: v >= 0,
                                               "at least 0")
        if clock is None:
            clock = time.monotonic
        elif not callable(clock):
            self.report.warn(f"clock must be a function, got {type(clock).__name__}; using time.monotonic")
            clock = time.monotonic
        self._clock = clock
        self._lock = threading.Lock()      # held for the whole of each public call
        self._state = FloorState.LISTENING
        self._since = self._clock()        # when the state was entered
        self._current: Optional[Turn] = None
        self._who = ""                     # the label of whoever holds (or last held) the floor, kept until the floor is listening again
        self._turn_began = 0.0             # when the turn took the floor
        self.overruns = 0
        self.turns_taken = 0

    def _valid(self, value, name: str, default: float, is_good, wanted: str) -> float:
        """A number that passes `is_good`, or the default with a warning."""
        if _is_number(value) and is_good(value):
            return float(value)
        self.report.warn(f"{name} must be a number {wanted}, got {value!r}; using {default:g}")
        return default

    # --- Questions ------------------------------------------------------------------

    @property
    def state(self) -> FloorState:
        """Where the floor is now, after settling anything that has run out. LISTENING if the lock cannot be had."""
        with self._held("state") as got:
            if not got:
                return FloorState.LISTENING
            self._settle()
            return self._state

    @property
    def holder(self) -> Optional[str]:
        """Who holds the floor ("room" or "keyboard"), or None when nobody does (listening, or recovering)."""
        with self._held("holder") as got:
            if not got:
                return None
            self._settle()
            return self._current.holder if self._current is not None else None

    # --- Taking and giving up the floor --------------------------------------------

    def take_floor(self, who: str) -> Optional[Turn]:
        """Asks for the floor for one turn. First come, first served: a Turn if the floor was free, otherwise None.

        The floor is free only while listening: it is not given during a turn, nor during the recovery after one.
        `who` is a short label such as "room" or "keyboard" (anything else is recorded as "unknown").
        """
        who = who if isinstance(who, str) and who else UNKNOWN_WHO
        with self._held("take_floor") as got:
            if not got:
                return None
            self._settle()
            if self._state is not FloorState.LISTENING:
                _log.info("floor: the %s asked for the floor but it is %s (%s)", who, self._state.value,
                          f"held by the {self._who}" if self._current is not None else "nobody's, recovering")
                return None
            turn = Turn(self, who)
            self._current = turn
            self._who = who
            self._turn_began = self._clock()
            self._enter(FloorState.WRITING)
            self.turns_taken += 1
            return turn

    def _start_printing(self, turn: Turn) -> bool:
        with self._held("start_printing") as got:
            if not got:
                return False
            self._settle()
            if self._current is not turn:
                self._note_late(turn, "start_printing")
                return False
            if self._state is not FloorState.WRITING:
                return False
            self._enter(FloorState.PRINTING)
            return True

    def _finish(self, turn: Turn) -> bool:
        with self._held("finish") as got:
            if not got:
                return False
            self._settle()
            if self._current is not turn:
                self._note_late(turn, "finish")
                return False
            self._current = None
            turn._ended = "finished"
            self._enter(FloorState.RECOVERING)
            return True

    # --- The watchdog and the recovery ----------------------------------------------

    def _enter(self, state: FloorState) -> None:
        """Moves to `state`, from now, and logs it. Only called with the lock held."""
        now = self._clock()
        _log.info("floor: %s -> %s (%s) after %.1f s", self._state.value, state.value, self._who or "-", max(0.0, now - self._since))
        if state is FloorState.RECOVERING:
            _log.info("floor: the %s's turn is over after %.1f s in all", self._who or "-", max(0.0, now - self._turn_began))
        self._state = state
        self._since = now

    def _note_late(self, turn: Turn, call: str) -> None:
        """Logs a call from a turn that lost the floor to the watchdog and has woken up. A turn that finished normally is not news."""
        if turn._ended == "lost":
            _log.info("floor: the %s's turn called %s after it lost the floor; ignored", turn.holder, call)

    def _settle(self) -> None:
        """Clears whatever has run out: a turn over its limit loses the floor, and a recovery that is done ends. Lock held."""
        now = self._clock()
        elapsed = max(0.0, now - self._since)
        if self._state is FloorState.WRITING and elapsed > self._writing_seconds:
            self._overrun(f"writing ran more than {self._writing_seconds:g} s")
        elif self._state is FloorState.PRINTING and elapsed > self._printing_seconds:
            self._overrun(f"printing ran more than {self._printing_seconds:g} s")
        elif self._state is FloorState.RECOVERING and elapsed >= self._recovering_seconds:
            self._enter(FloorState.LISTENING)
            self._who = ""

    def _overrun(self, what: str) -> None:
        """The watchdog takes the floor from a turn that ran too long. The turn's thread is not stopped; its Turn just stops counting."""
        who = self._current.holder if self._current is not None else UNKNOWN_WHO
        if self._current is not None:
            self._current._ended = "lost"
        self._current = None
        self.overruns += 1
        self._enter(FloorState.RECOVERING)
        self.report.warn(f"{what}, so the {who}'s turn lost the floor; listening resumes after the recovery")

    @contextmanager
    def _held(self, call: str) -> Iterator[bool]:
        """Takes the lock for the length of the `with`, yielding True; or yields False (and records a fault) if it waited too long."""
        got = self._lock.acquire(timeout=LOCK_TIMEOUT_SECONDS)
        if not got:
            self.report.critical(f"{call} could not get the lock in {LOCK_TIMEOUT_SECONDS:g} s; answering as if the floor were free of turns")
        try:
            yield got
        finally:
            if got:
                self._lock.release()
