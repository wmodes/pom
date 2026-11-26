"""The FaultRecorder: a module's problems found while loading or running, kept as data, not raised.

DESIGN PRINCIPLE (project-wide): the Printer must never stop because of a typo. A hand-edited
file with a bad number, one corrupt line in a library file, a missing optional file: none of
these may crash the oracle in a gallery. A module that meets a problem it can recover from
recovers (it falls back to a default, skips the bad entry) and records what it did here, so a
person can fix it later. Nothing is raised at the point of the problem.

LOGGING: a FaultRecorder can be given a logger, and then everything said through it is logged the moment it is
said. It is the ONE way a module says anything: five methods, each at its own level, all going through the
same logger and so all subject to the log's muting (pom.bookkeeper.log_muter), except DEBUG, which is never muted:
  * debug, info:  not problems. They are only logged: never kept, never counted, never raised in strict mode.
  * warn, error, critical:  faults (below). They are logged, kept for reading, and raised in strict mode.
Every method takes a message and optional %-arguments, like a logger does.
The service's stdout goes to journald, so `journalctl -u pom` shows these without any caller having to
remember to print them. Modules never configure logging themselves; the entry point does that once
(pom.bookkeeper.log_setup).

Three severities of fault:
  * WARNING:  recovered. The module carried on with a default or skipped something.
  * ERROR:    something failed and was skipped or tried again, but the module still does its job.
  * CRITICAL: the module cannot do its job at all (for example, there are no passages to draw
              from). It still does not raise; it returns an empty result and says so, and the
              Conductor decides how to degrade (stay quiet and keep listening, say so in the log).

STRICT MODE exists for development: with strict=True every fault is raised immediately as the
module's own error type. Tests and dev tools run strict so a typo is loud while you work; the
runtime never does.

A FaultRecorder logs EVERY fault, and keeps the first `limit` of them (and every critical one) for reading, counting the rest, so
a long outage cannot exhaust memory. It does no muting of its own: a fault that keeps repeating is muted by the log itself, in one
place (pom.bookkeeper.log_muter), not by each module's recorder.
"""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Optional


class Severity(Enum):
    """How bad a fault is."""
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


_LEVELS = {Severity.WARNING: logging.WARNING, Severity.ERROR: logging.ERROR, Severity.CRITICAL: logging.CRITICAL}


def _text(message: str, args: tuple) -> str:
    """The message with its %-arguments filled in. A message that does not fit its arguments is kept as written: saying it must never raise."""
    if not args:
        return message
    try:
        return message % args
    except Exception:
        return message


@dataclass(frozen=True)
class Fault:
    """One recorded problem.

    Attributes:
        severity: WARNING (recovered), ERROR (failed and was skipped or retried; still does its job) or CRITICAL (cannot do its job).
        message: A short human-readable description, naming the file or entry if there is one.
    """
    severity: Severity
    message: str

    def __str__(self) -> str:
        return f"{self.severity.value}: {self.message}"


class FaultRecorder:
    """A collection of faults from one module or one operation.

    Args:
        strict: If true, every fault is raised at once as `error` (development only).
        error: The exception type raised in strict mode; defaults to ValueError.
        limit: How many faults to keep for reading (`warnings`, `lines()`); further ones are only counted. Every fault is logged either way.
        logger: If given, every fault is logged as it is recorded.
    """

    def __init__(self, strict: bool = False, error: type = ValueError, limit: int = 50,
                 logger: Optional[logging.Logger] = None):
        self._strict = strict
        self._error = error
        self._limit = limit
        self._logger = logger
        self._faults: list[Fault] = []
        self._suppressed = 0

    # --- Recording ------------------------------------------------------------------

    def debug(self, message: str, *args) -> None:
        """Logs a line of detail. Not a fault: never kept, counted or raised, and never muted."""
        self._say(logging.DEBUG, message, args)

    def info(self, message: str, *args) -> None:
        """Logs a normal event. Not a fault: never kept, counted or raised."""
        self._say(logging.INFO, message, args)

    def warn(self, message: str, *args) -> None:
        """Records a recovered problem."""
        self._add(Fault(Severity.WARNING, _text(message, args)))

    def error(self, message: str, *args) -> None:
        """Records a failure that was skipped or tried again, while the module still does its job."""
        self._add(Fault(Severity.ERROR, _text(message, args)))

    def critical(self, message: str, *args) -> None:
        """Records a problem the module could not recover from."""
        self._add(Fault(Severity.CRITICAL, _text(message, args)))

    def _say(self, level: int, message: str, args: tuple) -> None:
        """Logs a line that is not a fault, if a logger was given."""
        if self._logger is not None:
            self._logger.log(level, _text(message, args))

    def extend(self, other: "FaultRecorder") -> None:
        """Adds every fault from another report (for example a sub-module's)."""
        for fault in other.faults:
            self._add(fault)
        self._suppressed += other.suppressed

    def _add(self, fault: Fault) -> None:
        """Raises in strict mode; otherwise keeps the fault (or counts it past the limit) and logs it."""
        if self._strict:
            raise self._error(fault.message)
        # A critical fault is always kept: it is rare, and it must never be hidden behind
        # a flood of warnings. Every fault is logged; only the list kept for reading is limited.
        if fault.severity is Severity.CRITICAL or len(self._faults) < self._limit:
            self._faults.append(fault)
        else:
            self._suppressed += 1
        self._log(fault)

    def _log(self, fault: Fault) -> None:
        """Logs a fault at the matching level, if a logger was given."""
        if self._logger is not None:
            self._logger.log(_LEVELS[fault.severity], fault.message)

    # --- Reading --------------------------------------------------------------------

    @property
    def faults(self) -> tuple[Fault, ...]:
        """The kept faults, in the order they were found."""
        return tuple(self._faults)

    @property
    def warnings(self) -> list[str]:
        """Messages of the WARNING faults."""
        return [i.message for i in self._faults if i.severity is Severity.WARNING]

    @property
    def errors(self) -> list[str]:
        """Messages of the ERROR faults."""
        return [i.message for i in self._faults if i.severity is Severity.ERROR]

    @property
    def criticals(self) -> list[str]:
        """Messages of the CRITICAL faults."""
        return [i.message for i in self._faults if i.severity is Severity.CRITICAL]

    @property
    def suppressed(self) -> int:
        """How many faults were not kept because the limit was reached."""
        return self._suppressed

    @property
    def ok(self) -> bool:
        """True when nothing at all was reported."""
        return not self._faults and not self._suppressed

    @property
    def has_critical(self) -> bool:
        """True if any CRITICAL fault was recorded. Criticals are never suppressed."""
        return any(i.severity is Severity.CRITICAL for i in self._faults)

    def lines(self) -> list[str]:
        """The report as printable lines, ending with a count of any suppressed faults."""
        out = [str(fault) for fault in self._faults]
        if self._suppressed:
            out.append(f"... and {self._suppressed} more")
        return out

    def __iter__(self) -> Iterator[Fault]:
        return iter(self._faults)

    def __len__(self) -> int:
        return len(self._faults) + self._suppressed

    def __repr__(self) -> str:
        return f"FaultRecorder({len(self)} fault(s){', critical' if self.has_critical else ''})"


def strict_fault_recorder(error: type, strict: bool, limit: Optional[int] = None,
                  logger: Optional[logging.Logger] = None) -> FaultRecorder:
    """A FaultRecorder for a module: strict raises `error`, lenient collects (and logs, if a logger is
    given). Small convenience so every module builds its report the same way."""
    return FaultRecorder(strict=strict, error=error, logger=logger,
                  **({} if limit is None else {"limit": limit}))
