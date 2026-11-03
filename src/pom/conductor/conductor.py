"""The Conductor: runs the whole Printer, listening for a hinge and taking it through a turn, one turn at a time.

It joins the parts the other modules built. The room and the keyboard both offer attempts; when one qualifies, the Printer takes the
floor for one turn (writing and printing) and does not listen; then it yields the floor and listens again:

    attempts (room, keyboard) -> the floor -> turn: write, print -> yield -> listening

Four threads, all plain and all ordinary Python:
  * the ROOM thread runs the HingeFinder (sound in, attempts out) and offers each attempt;
  * the KEYBOARD thread runs the KeyboardWatcher and offers each attempt (there may be no keyboard);
  * the TURN thread takes each hinge through a turn with the TurnTaker, one at a time;
  * the thread that called `run()` keeps watch every `check_seconds` and is the only one that opens the ears again.

Design decisions, and why:
  * FIRST COME, FIRST SERVED. An attempt that qualified asks the FloorKeeper for the floor. The first to ask gets a Turn; a hinge from
    the other that arrives meanwhile is dropped, with a line in the log. The turn is handed to the TURN thread, never run on the one
    that found it: the room thread must go on reading sound (which the paused Eavesdropper throws away) or the microphone's buffer would
    overflow while the Printer is busy.
  * THE EARS SHUT THE MOMENT THE FLOOR IS TAKEN: the thread that took it pauses the Eavesdropper and the KeyboardWatcher at once, before
    handing the turn over, so nothing more is sent to whisper or the scorer model.
  * THE EARS FOLLOW THE FLOOR, LEVEL-TRIGGERED. Opening them again is not an event someone has to remember to send. Every `check_seconds`
    the watching thread looks at the floor: if it is listening and the ears are shut, it calls `skip_ahead()`; if the floor is held and the
    ears are open, it shuts them. So a turn that ends any way at all (finished, failed, cut off by the watchdog), or an event that was
    missed, cannot leave the Printer deaf. (Pausing needs no such care: it is done by the thread that took the floor.)
  * THE PRINTER KEEPS GOING. Every thread catches what its part throws, records it and goes on. A microphone that is unplugged ends the
    HingeFinder; the room thread builds a fresh one (`room_factory`) after `restart_pause_seconds`, and again and again for as long as it
    takes, so plugging the microphone back in is all it needs. (A recording that ends is the end of the run: `restart_room=False`.) A
    thread that dies in a way nobody expected is started again by the watching thread. Nothing here may stop the oracle.
  * NOTHING SAID IS KEPT. Attempts and portents pass through; nothing is logged or put in a report but counts, labels and seconds.
  * SHUTTING DOWN. `stop()`, from any thread (a signal handler, a test), shuts the ears for good and closes the keyboard, and `run()` returns
    after waiting at most `JOIN_SECONDS` (5) for each thread. So a turn that is only a moment from done finishes, but a portent being typed
    for another half minute is cut off when the process exits (a burn test showed this): restart the service between portents if that
    matters. A graceful "finish the print first" stop would have to wait up to the printing limit, longer than systemd waits by default.
  * The parts are passed in, already built; this module never reads Config. `on_attempt(attempt)` is called for every attempt (the
    entry point uses it to feed the monitoring site's chart and to print a testbench line) and `on_turn(attempt, result)` for every turn;
    a callback that raises is recorded and ignored.
"""
import math
import queue
import threading
from typing import Callable, Optional

from pom.conductor.floor_keeper import FloorKeeper, FloorState
from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger
from pom.qualifying.attempt import Attempt

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_CHECK_SECONDS = 1.0            # how often the watching thread looks at the floor and the threads
DEFAULT_RESTART_PAUSE_SECONDS = 5.0    # the wait before the room (or the keyboard) is started again after it ended or failed
JOIN_SECONDS = 5.0                     # how long stopping waits for a thread


class ConductorError(ValueError):
    """Raised for any Conductor problem, but only in strict mode (development and tests)."""


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class Conductor:
    """Runs the Printer: listens, takes the floor, has a turn taken, and listens again.

    Attributes:
        report: Problems met (a thread that failed or ended, a callback that raised). Never holds anything said.
        hinges_dropped: How many hinges arrived while the floor was held and were dropped.
        turns_taken: How many turns have been handed to the turn thread.
    """

    def __init__(self, floor: FloorKeeper, turn_taker, room_factory: Callable[[], tuple], keyboard=None,
                 on_attempt: Optional[Callable[[Attempt], object]] = None, on_turn: Optional[Callable[..., object]] = None,
                 restart_room: bool = True, restart_pause_seconds: float = DEFAULT_RESTART_PAUSE_SECONDS,
                 check_seconds: float = DEFAULT_CHECK_SECONDS, strict: bool = False):
        """Args:
            floor: The FloorKeeper.
            turn_taker: A TurnTaker (anything with take_turn(turn, attempt) returning a result).
            room_factory: A function returning (finder, eavesdropper): a HingeFinder and the Eavesdropper under it, freshly built. It is
                called again each time the room ends, so a microphone that comes back is heard.
            keyboard: A KeyboardWatcher, or None when there is no keyboard to watch.
            on_attempt: Called with every Attempt, from the thread that found it.
            on_turn: Called with (attempt, result) after every turn, from the turn thread.
            restart_room: True to build the room again after it ends (a microphone); False when its ending is the end of the run (a recording).
            restart_pause_seconds: The wait before a part is started again, 0 or more.
            check_seconds: How often the watching thread looks at the floor and the threads, above 0.
            strict: Raise ConductorError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=ConductorError, logger=_log)
        self._floor, self._turn_taker, self._room_factory, self._keyboard = floor, turn_taker, room_factory, keyboard
        self._on_attempt, self._on_turn = on_attempt, on_turn
        self._restart_room = bool(restart_room)
        self._restart_pause = self._valid(restart_pause_seconds, "restart_pause_seconds", DEFAULT_RESTART_PAUSE_SECONDS, lambda v: v >= 0,
                                          "at least 0")
        self._check = self._valid(check_seconds, "check_seconds", DEFAULT_CHECK_SECONDS, lambda v: v > 0, "above 0")
        self._stop = threading.Event()
        self._lock = threading.Lock()              # guards `_ears`, `_shut` and the thread table
        self._ears = None                          # the Eavesdropper of the room that is running now
        self._shut = False                         # True while the ears are shut (the floor is held, or the Printer is stopping)
        self._turns: "queue.Queue" = queue.Queue()
        self._threads: dict = {}
        self.hinges_dropped = 0
        self.turns_taken = 0

    def _valid(self, value, name: str, default: float, is_good, wanted: str) -> float:
        """A number that passes `is_good`, or the default with a warning."""
        if _is_number(value) and is_good(value):
            return float(value)
        self.report.warn(f"{name} must be a number {wanted}, got {value!r}; using {default:g}")
        return default

    # --- Running --------------------------------------------------------------------

    def run(self) -> None:
        """Runs the Printer until `stop()` is called (or, without `restart_room`, until the room ends). Blocks."""
        _log.info("conductor: starting")
        self._stop.clear()
        self._start_threads()
        try:
            while not self._stop.wait(self._check):
                self._watch()
                if not self._restart_room and not self._running("room") and self._floor.state is FloorState.LISTENING:
                    _log.info("conductor: the room ended and is not to be started again, so the run is over")
                    break
        finally:
            self.stop()
            self._join_threads()
            _log.info("conductor: stopped (%d turns, %d hinges dropped)", self.turns_taken, self.hinges_dropped)

    def stop(self) -> None:
        """Stops the Printer: the ears shut for good, the keyboard closed, a turn in progress allowed to finish. Safe from any thread, safe to repeat."""
        self._stop.set()
        with self._lock:
            ears, self._ears, self._shut = self._ears, None, True
        for part in (ears, self._keyboard):
            self._call(part, "close")
        self._turns.put(None)

    def _start_threads(self) -> None:
        with self._lock:
            for name, target in (("room", self._room_loop), ("keyboard", self._keyboard_loop), ("turn", self._turn_loop)):
                if name == "keyboard" and self._keyboard is None:
                    continue
                thread = threading.Thread(target=target, name=f"conductor-{name}", daemon=True)
                self._threads[name] = thread
                thread.start()

    def _running(self, name: str) -> bool:
        thread = self._threads.get(name)
        return thread is not None and thread.is_alive()

    def _join_threads(self) -> None:
        for thread in list(self._threads.values()):
            thread.join(timeout=JOIN_SECONDS)

    # --- The watching thread --------------------------------------------------------

    def _watch(self) -> None:
        """One look: the ears follow the floor, and a thread that died unexpectedly is started again."""
        with self._lock:
            state = self._floor.state              # read inside the lock, so a turn taking the floor right now cannot be missed
            if state is FloorState.LISTENING and self._shut:
                self._shut, action = False, "open"
            elif state is not FloorState.LISTENING and not self._shut:
                self._shut, action = True, "shut"
            else:
                action = None
        if action == "open":
            _log.info("conductor: the floor is free, so the ears open")
            self._open_ears()
        elif action == "shut":
            _log.info("conductor: the floor is %s, so the ears shut", state.value)
            self._shut_ears()
        if self._stop.is_set():
            return
        for name, target in (("room", self._room_loop), ("keyboard", self._keyboard_loop), ("turn", self._turn_loop)):
            if name == "keyboard" and self._keyboard is None:
                continue
            if name == "room" and not self._restart_room:
                continue
            if not self._running(name):
                self.report.warn(f"the {name} thread had stopped; it is started again")
                with self._lock:
                    thread = threading.Thread(target=target, name=f"conductor-{name}", daemon=True)
                    self._threads[name] = thread
                    thread.start()

    def _shut_ears(self) -> None:
        with self._lock:
            ears = self._ears
        self._call(ears, "pause")
        self._call(self._keyboard, "pause")

    def _open_ears(self) -> None:
        with self._lock:
            ears = self._ears
        self._call(ears, "skip_ahead")
        self._call(self._keyboard, "skip_ahead")

    def _call(self, part, method: str) -> None:
        """Calls `part.method()`, recording a failure; a missing part is fine."""
        if part is None:
            return
        try:
            getattr(part, method)()
        except Exception as problem:               # nothing may stop the oracle
            self.report.warn(f"{method} failed on a part ({type(problem).__name__})")

    # --- The room and the keyboard --------------------------------------------------

    def _room_loop(self) -> None:
        while not self._stop.is_set():
            try:
                finder, ears = self._room_factory()
            except Exception as problem:           # nothing may stop the oracle
                self.report.warn(f"the room could not be built ({type(problem).__name__}); trying again")
                if self._stop.wait(self._restart_pause):
                    return
                continue
            with self._lock:
                self._ears = ears
                shut = self._shut
            if shut:
                self._call(ears, "pause")
            try:
                for attempt in finder.find():
                    self._offer(attempt, "room")
            except Exception as problem:           # nothing may stop the oracle
                self.report.warn(f"the room failed ({type(problem).__name__})")
            finally:
                self._call(finder, "close")
                with self._lock:
                    if self._ears is ears:
                        self._ears = None
            if self._stop.is_set() or not self._restart_room:
                return
            self.report.warn("the room ended (a microphone that stopped?); it is started again")
            if self._stop.wait(self._restart_pause):
                return

    def _keyboard_loop(self) -> None:
        while not self._stop.is_set():
            try:
                for attempt in self._keyboard.watch():
                    self._offer(attempt, "keyboard")
            except Exception as problem:           # nothing may stop the oracle
                self.report.warn(f"the keyboard failed ({type(problem).__name__})")
            if self._stop.is_set():
                return
            if self._stop.wait(self._restart_pause):
                return

    def _offer(self, attempt: Attempt, who: str) -> None:
        """Every attempt is shown; one that qualified asks for the floor, and if it gets it the ears shut at once and the turn is handed on."""
        self._call_back(self._on_attempt, attempt)
        if not getattr(attempt, "is_hinge", False):
            return
        turn = self._floor.take_floor(who)
        if turn is None:
            self.hinges_dropped += 1
            _log.info("conductor: a hinge from the %s was dropped; the floor is %s (%d dropped so far)", who, self._floor.state.value,
                      self.hinges_dropped)
            return
        with self._lock:
            self._shut = True
        self._shut_ears()
        self.turns_taken += 1
        _log.info("conductor: the %s's hinge takes the floor (turn %d)", who, self.turns_taken)
        self._turns.put((turn, attempt))

    # --- The turn thread ------------------------------------------------------------

    def _turn_loop(self) -> None:
        while True:
            item = self._turns.get()
            if item is None:
                if self._stop.is_set():
                    return
                continue
            turn, attempt = item
            try:
                result = self._turn_taker.take_turn(turn, attempt)
            except Exception as problem:           # take_turn should never raise; if it does the floor must still be given back
                self.report.warn(f"a turn raised ({type(problem).__name__}); the floor was yielded")
                turn.finish()
                continue
            self._call_back(self._on_turn, attempt, result)

    def _call_back(self, callback, *args) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as problem:               # a bad callback must not stop the Printer
            self.report.warn(f"a callback raised ({type(problem).__name__}); it was ignored")

    def report_lines(self) -> list:
        """The problems met by the Conductor and the parts under it, as lines to show."""
        lines = [*self.report.lines(), *self._floor.report.lines()]
        for part in (self._turn_taker, self._keyboard):
            lines.extend(part.report_lines() if hasattr(part, "report_lines") else [])
        return lines
