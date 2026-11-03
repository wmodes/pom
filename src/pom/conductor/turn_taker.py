"""The TurnTaker: takes one hinge through a turn, from the portent being written to it being typed.

The FloorKeeper decides who may speak; the TurnTaker does the speaking. `take_turn(turn, attempt)` is given the Turn the FloorKeeper
handed out and the Attempt that qualified, and it:

    1. has the PortentWriter write a portent for the hinge (trying again a few times if the oracle gives nothing);
    2. records the turn in the TurnHistory at once, so the monitoring site shows it even while the printer is being waited for;
    3. formats the portent for the terminal and types it, waiting patiently if the terminal cannot print yet;
    4. says how the turn ended, on the record and in the TurnResult it returns, and yields the floor.

Printing has priority over listening: the Printer listens only to find something to print, and a portent costs minutes of the oracle's
time, so there is no hurry to get back to listening. A portent that has been written is printed.

Design decisions, and why:
  * THE TURN IS THE TURNTAKER'S TO CLOSE. `take_turn` runs the whole turn inside `with turn:`, so the floor is yielded whatever happens
    in it (a failure, an error nobody expected). The Conductor only says "take this turn" and cannot forget to give the floor back.
  * NOTHING HERE MAY STOP THE ORACLE (see pom/__init__.py). `take_turn` never raises: an unexpected error ends the turn with the outcome
    ERROR and a fault in `report` naming the error type; the floor is still yielded. `strict=True` raises TurnTakerError instead, for tests
    and dev tools.
  * THE ORACLE GIVES NOTHING. The writer returns None when the server is down, slow or answering oddly. The turn tries again, up to
    `write_attempts` times with `write_pause_seconds` between them, but only while a full attempt (`write_attempt_seconds`, the oracle
    client's own timeout) still fits in `write_budget_seconds`. So a quick failure (a refused connection) is retried, and a timeout
    that used up the time is not: no special cases, and the FloorKeeper's writing watchdog, which is longer than the budget, never has
    to act. A turn that wrote nothing ends NO_PORTENT; after three in a row a CRITICAL fault says the oracle looks broken.
  * A PORTENT GROWS STALE. It answers one sentence, and a few minutes later the room has moved on. A portent is worth printing only
    while its hinge is fresh: `freshness_seconds` (6 minutes by default, a little under the usual gap between portents), counted from
    the start of the turn. Past it the portent is dropped, marked TOO_LATE on its record, and the Printer goes back to listening. The
    check is made before each try at printing, never in the middle of typing: a portent that has begun is finished.
  * THE TERMINAL CANNOT PRINT YET (unplugged, held, no paper, the port gone). If nothing was typed, the turn waits `print_pause_seconds`
    and tries again until the portent goes stale, sending a heartbeat (`turn.still_printing()`) every few seconds so the watchdog knows it
    is waiting and not hung, and showing "waiting for the printer" on its record. The ears stay shut meanwhile: with no printer there is
    nothing to listen for. As soon as the terminal works the portent is typed. Once typing has begun, the TerminalPort itself waits
    for a stalled or lost port and goes on where it stopped (it is handed the heartbeat to send meanwhile), so a portent is not typed
    twice. If even its patience runs out with part of the portent on the paper, the turn ends PRINT_FAILED with a CRITICAL fault and is
    not retried, since typing it again would repeat the start.
  * NO PRINTER BY DESIGN. With no terminal (`None`) a turn is write, record, done: the outcome is NOT_PRINTED. The monitoring site shows
    everything it normally does, so the Printer can be watched listening and writing with nothing attached.
  * A portent that finishes writing after the watchdog took the floor is still printed: printing has priority. It is logged as a warning.
  * Time and sleeping are the caller's (`clock`, `sleep`), so tests need not wait.
  * LOGGING, so a problem can be read afterwards: one INFO line when the turn begins (who, the score, the bar), one for each write attempt
    that gave nothing, one when the portent is written (its length and how long it took), the start of a wait for the printer and every sixth
    try after, and one when the turn ends with its outcome and times. Numbers and labels only. What was heard and what was written are never
    logged or put in a report.
  * The parts are passed in, already built; this module never reads Config.
"""
import math
import time
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Tuple

from pom.conductor.floor_keeper import Turn
from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger
from pom.qualifying.attempt import Attempt

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_FRESHNESS_SECONDS = 360.0       # a portent is worth printing for 6 minutes after its hinge
DEFAULT_WRITE_ATTEMPTS = 3
DEFAULT_WRITE_PAUSE_SECONDS = 10.0
DEFAULT_WRITE_ATTEMPT_SECONDS = 240.0   # the longest one attempt can take: the oracle client's own timeout
DEFAULT_WRITE_BUDGET_SECONDS = 270.0    # the FloorKeeper's writing limit (300 s) less a margin
DEFAULT_PRINT_PAUSE_SECONDS = 10.0
BEAT_SECONDS = 5.0                      # how often a wait for the printer sends its heartbeat
WRITE_FAILURES_THAT_ALARM = 3           # turns in a row that wrote nothing
PRINT_TRIES_BETWEEN_LOG_LINES = 6


class TurnTakerError(ValueError):
    """Raised for any TurnTaker problem, but only in strict mode (development and tests)."""


class TurnOutcome(Enum):
    """How a turn ended. The value is the label the monitoring site shows."""
    PRINTED = "printed"
    NOT_PRINTED = "not printed (no printer)"
    NO_PORTENT = "no portent written"
    NOTHING_TO_PRINT = "nothing to print"
    TOO_LATE = "too late to print"
    PRINT_FAILED = "print failed"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class TurnResult:
    """What happened in one turn. Numbers and a label only, never any words.

    Attributes:
        outcome: How the turn ended.
        words: How many words the portent has (0 if none was written).
        characters: How many characters were typed.
        writing_seconds: How long writing took, retries and pauses included.
        printing_seconds: How long the printing stage took, waiting included (0 if it never began).
        write_attempts: How many times the oracle was asked.
        print_tries: How many times the terminal was tried.
        record_id: The TurnHistory record's number, or None if no record was made.
    """
    outcome: TurnOutcome
    words: int = 0
    characters: int = 0
    writing_seconds: float = 0.0
    printing_seconds: float = 0.0
    write_attempts: int = 0
    print_tries: int = 0
    record_id: Optional[int] = None


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class TurnTaker:
    """Takes a hinge through a turn.

    Attributes:
        report: Problems met (a turn that failed, a printer that did not come back).
        outcomes: How many turns have ended each way (a Counter of TurnOutcome).
    """

    def __init__(self, writer, formatter, terminal=None, history=None, freshness_seconds: float = DEFAULT_FRESHNESS_SECONDS,
                 write_attempts: int = DEFAULT_WRITE_ATTEMPTS, write_pause_seconds: float = DEFAULT_WRITE_PAUSE_SECONDS,
                 write_attempt_seconds: float = DEFAULT_WRITE_ATTEMPT_SECONDS, write_budget_seconds: float = DEFAULT_WRITE_BUDGET_SECONDS,
                 print_pause_seconds: float = DEFAULT_PRINT_PAUSE_SECONDS, clock: Optional[Callable[[], float]] = None,
                 sleep: Optional[Callable[[float], None]] = None, strict: bool = False):
        """Args:
            writer: A PortentWriter (anything with write(hinge) returning a Portent or None, and report_lines()).
            formatter: A PortentFormatter (anything with format(text)).
            terminal: Anything with transmit(text) returning a PrintResult, or None for no printer by design.
            history: A TurnHistory (add, set_outcome), or None to keep no record.
            freshness_seconds: How long after the turn begins a portent is still worth printing, above 0.
            write_attempts: The most times to ask the oracle, a whole number of at least 1.
            write_pause_seconds: The wait between two attempts to write, 0 or more.
            write_attempt_seconds: The longest one attempt can take (the oracle client's timeout), above 0.
            write_budget_seconds: The time all the writing may take, above 0; keep it under the FloorKeeper's writing limit.
            print_pause_seconds: The wait between two tries at a terminal that cannot print yet, above 0.
            clock: A function returning seconds that never go backwards. Default: time.monotonic.
            sleep: A function that waits that many seconds. Default: time.sleep.
            strict: Raise TurnTakerError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=TurnTakerError, logger=_log)
        self._writer, self._formatter, self._terminal, self._history = writer, formatter, terminal, history
        self._freshness = self._valid(freshness_seconds, "freshness_seconds", DEFAULT_FRESHNESS_SECONDS, lambda v: v > 0, "above 0")
        self._write_attempts = int(self._valid(write_attempts, "write_attempts", DEFAULT_WRITE_ATTEMPTS,
                                               lambda v: v >= 1 and float(v).is_integer(), "a whole number of at least 1"))
        self._write_pause = self._valid(write_pause_seconds, "write_pause_seconds", DEFAULT_WRITE_PAUSE_SECONDS, lambda v: v >= 0, "at least 0")
        self._write_attempt = self._valid(write_attempt_seconds, "write_attempt_seconds", DEFAULT_WRITE_ATTEMPT_SECONDS, lambda v: v > 0,
                                          "above 0")
        self._write_budget = self._valid(write_budget_seconds, "write_budget_seconds", DEFAULT_WRITE_BUDGET_SECONDS, lambda v: v > 0, "above 0")
        self._print_pause = self._valid(print_pause_seconds, "print_pause_seconds", DEFAULT_PRINT_PAUSE_SECONDS, lambda v: v > 0, "above 0")
        self._clock = clock if callable(clock) else time.monotonic
        if clock is not None and not callable(clock):
            self.report.warn(f"clock must be a function, got {type(clock).__name__}; using time.monotonic")
        self._sleep = sleep if callable(sleep) else time.sleep
        if sleep is not None and not callable(sleep):
            self.report.warn(f"sleep must be a function, got {type(sleep).__name__}; using time.sleep")
        self.outcomes: Counter = Counter()
        self._failed_writes_in_a_row = 0

    def _valid(self, value, name: str, default: float, is_good, wanted: str) -> float:
        """A number that passes `is_good`, or the default with a warning."""
        if _is_number(value) and is_good(value):
            return float(value)
        self.report.warn(f"{name} must be a number {wanted}, got {value!r}; using {default:g}")
        return default

    # --- One turn -------------------------------------------------------------------

    def take_turn(self, turn: Turn, attempt: Attempt) -> TurnResult:
        """Takes the hinge through writing, recording and printing, and yields the floor. Never raises (except in strict mode)."""
        if not isinstance(turn, Turn):
            self.report.warn(f"take_turn was given {type(turn).__name__}, not a Turn; nothing was done")
            return TurnResult(TurnOutcome.ERROR)
        began = self._clock()
        trail = {"record_id": None}                  # what the turn has got as far as, for the error case below
        with turn:                                   # whatever happens below, the floor is yielded at the end
            try:
                _log.info("turn: the %s's hinge scored %s against a bar of %s", turn.holder,
                          f"{attempt.score:.1f}" if _is_number(getattr(attempt, "score", None)) else "none",
                          f"{attempt.bar:.1f}" if _is_number(getattr(attempt, "bar", None)) else "none")
                result = self._take(turn, attempt, began, trail)
            except Exception as problem:             # nothing may stop the oracle
                self.report.warn(f"a turn failed unexpectedly ({type(problem).__name__}); the floor was yielded")
                result = TurnResult(TurnOutcome.ERROR, record_id=trail["record_id"])
                self._set_outcome(trail["record_id"], TurnOutcome.ERROR)
        self.outcomes[result.outcome] += 1
        _log.info("turn: %s (writing %.1f s, printing %.1f s, %d words, %d characters)", result.outcome.value,
                  result.writing_seconds, result.printing_seconds, result.words, result.characters)
        return result

    def _take(self, turn: Turn, attempt: Attempt, began: float, trail: dict) -> TurnResult:
        portent, write_attempts = self._write(attempt.text, began)
        writing_seconds = self._clock() - began
        record = self._record(attempt, portent)
        record_id = record.id if record is not None else None
        trail["record_id"] = record_id
        if portent is None:
            return self._done(TurnOutcome.NO_PORTENT, record_id, writing_seconds=writing_seconds, write_attempts=write_attempts)
        self._failed_writes_in_a_row = 0
        _log.info("turn: a portent of %d words was written in %.1f s (%d attempt%s)", portent.words, writing_seconds, write_attempts,
                  "" if write_attempts == 1 else "s")
        if self._terminal is None:
            return self._done(TurnOutcome.NOT_PRINTED, record_id, words=portent.words, writing_seconds=writing_seconds,
                              write_attempts=write_attempts)
        text = self._formatter.format(portent.text)
        if not isinstance(text, str) or not text.strip():
            return self._done(TurnOutcome.NOTHING_TO_PRINT, record_id, words=portent.words, writing_seconds=writing_seconds,
                              write_attempts=write_attempts)
        if not turn.start_printing():
            self.report.warn("the floor was lost while the portent was being written; printing it anyway, since printing has priority")
        return self._print(turn, text, portent.words, record_id, began, writing_seconds, write_attempts)

    # --- Writing --------------------------------------------------------------------

    def _write(self, hinge: str, began: float) -> Tuple[Optional[object], int]:
        """Asks the oracle for a portent, again after a pause while a whole attempt still fits in the budget."""
        attempts = 0
        while True:
            attempts += 1
            portent = self._writer.write(hinge)
            if portent is not None:
                return portent, attempts
            if attempts >= self._write_attempts:
                break
            if self._clock() - began + self._write_pause + self._write_attempt > self._write_budget:
                _log.info("turn: the oracle gave nothing and another attempt would not fit in the time allowed")
                break
            _log.info("turn: the oracle gave nothing (attempt %d of %d); trying again in %.0f s", attempts, self._write_attempts,
                      self._write_pause)
            self._sleep(self._write_pause)
        self._failed_writes_in_a_row += 1
        if self._failed_writes_in_a_row % WRITE_FAILURES_THAT_ALARM == 0:
            self.report.critical(f"the oracle has written nothing for {self._failed_writes_in_a_row} turns in a row; it looks broken")
        else:
            self.report.warn(f"the oracle wrote nothing after {attempts} attempt{'s' if attempts != 1 else ''}; this turn is dropped")
        return None, attempts

    def _record(self, attempt: Attempt, portent):
        """Adds the turn to the history now, before any printing, so the monitoring site shows it. None if there is no history."""
        if self._history is None:
            return None
        return self._history.add(attempt.text, attempt.score, attempt.bar, attempt.window_size, attempt.heard_at, portent)

    # --- Printing -------------------------------------------------------------------

    def _print(self, turn: Turn, text: str, words: int, record_id: Optional[int], began: float, writing_seconds: float,
               write_attempts: int) -> TurnResult:
        """Types the portent, waiting for a terminal that cannot print yet, for as long as the portent is fresh."""
        printing_began = self._clock()
        deadline = began + self._freshness
        tries = 0
        while True:
            if self._clock() >= deadline:
                break
            tries += 1
            result = self._terminal.transmit(text, still_alive=turn.still_printing)     # an unexpected error goes up to take_turn
            if result.completed:
                return self._done(TurnOutcome.PRINTED, record_id, words=words, characters=result.characters, writing_seconds=writing_seconds,
                                  printing_seconds=self._clock() - printing_began, write_attempts=write_attempts, print_tries=tries)
            if result.characters > 0:
                self.report.critical(f"the terminal failed after {result.characters} characters; it is not typed again, so the start is not "
                                     f"repeated on the paper")
                return self._done(TurnOutcome.PRINT_FAILED, record_id, words=words, characters=result.characters,
                                  writing_seconds=writing_seconds, printing_seconds=self._clock() - printing_began,
                                  write_attempts=write_attempts, print_tries=tries)
            if tries == 1:
                self._set_outcome(record_id, "waiting for the printer")
            if tries % PRINT_TRIES_BETWEEN_LOG_LINES == 1:
                _log.info("turn: the terminal cannot print yet (try %d); waiting for it, for up to %.0f s more", tries,
                          max(0.0, deadline - self._clock()))
            if self._clock() + self._print_pause >= deadline:
                break
            self._wait(turn, self._print_pause)
        if tries == 0:
            self.report.warn(f"the portent was ready more than {self._freshness:g} s after the hinge, so it is dropped unprinted")
        else:
            self.report.critical(f"the printer did not come back within {self._freshness:g} s of the hinge ({tries} tries); the portent is dropped")
        return self._done(TurnOutcome.TOO_LATE, record_id, words=words, writing_seconds=writing_seconds,
                          printing_seconds=self._clock() - printing_began, write_attempts=write_attempts, print_tries=tries)

    def _wait(self, turn: Turn, seconds: float) -> None:
        """Waits `seconds`, sending the heartbeat every few seconds so the watchdog sees a patient wait and not a hang."""
        end = self._clock() + seconds
        while True:
            turn.still_printing()
            left = end - self._clock()
            if left <= 0:
                return
            self._sleep(min(BEAT_SECONDS, left))

    # --- The end --------------------------------------------------------------------

    def _done(self, outcome: TurnOutcome, record_id: Optional[int], **numbers) -> TurnResult:
        """Writes the outcome on the record and makes the result."""
        self._set_outcome(record_id, outcome)
        return TurnResult(outcome, record_id=record_id, **numbers)

    def _set_outcome(self, record_id: Optional[int], outcome) -> None:
        if self._history is not None and record_id is not None:
            self._history.set_outcome(record_id, outcome.value if isinstance(outcome, TurnOutcome) else outcome)

    def report_lines(self) -> list:
        """The problems met by the TurnTaker and by the writer (and the prompt builder and model client under it), as lines to show."""
        return [*self.report.lines(), *self._writer.report_lines()]
