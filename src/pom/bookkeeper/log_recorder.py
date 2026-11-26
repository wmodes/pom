"""The LogRecorder: a module's problems found while loading or running, kept as data, not raised.

DESIGN PRINCIPLE (project-wide): the Printer must never stop because of a typo. A hand-edited
file with a bad number, one corrupt line in a library file, a missing optional file: none of
these may crash the oracle in a gallery. A module that meets a problem it can recover from
recovers (it falls back to a default, skips the bad entry) and records what it did here, so a
person can fix it later. Nothing is raised at the point of the problem.

LOGGING: a LogRecorder can be given a logger, and then everything said through it is logged the moment it is
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

A LogRecorder logs EVERY fault, and hands each to its LogBook (pom.bookkeeper.log_book), which keeps the first `limit` of them (and
every critical one) for reading and counts the rest, so a long outage cannot exhaust memory. The recorder SAYS (five methods, one per mode, all
through the same helpers); the book HOLDS. Neither mutes: a fault that keeps repeating is muted by the log itself, in one place
(pom.bookkeeper.log_muter), not by each module's recorder.
"""
import logging
from typing import Iterator, Optional

from pom.bookkeeper.fault import Fault, Severity
from pom.bookkeeper.log_book import LogBook

__all__ = ["Fault", "LogRecorder", "Severity", "strict_log_recorder"]      # Fault and Severity are re-exported from their own module


def _text(message: str, args: tuple) -> str:
    """The message with its %-arguments filled in. A message that does not fit its arguments is kept as written: saying it must never raise."""
    if not args:
        return message
    try:
        return message % args
    except Exception:
        return message


class LogRecorder:
    """What a module says: five modes (debug, info, warn, error, critical), each logged at its own level through one logger.

    `debug` and `info` are only logged. `warn`, `error` and `critical` are faults: they are also handed to the book (kept or counted) and, in
    strict mode, raised instead.

    Args:
        strict: If true, every fault is raised at once as `error` (development only).
        error: The exception type raised in strict mode; defaults to ValueError.
        limit: How many faults the book keeps for reading (`warnings`, `lines()`); further ones are only counted. Every fault is logged either way.
        logger: If given, everything said is logged as it is said.
    """

    def __init__(self, strict: bool = False, error: type = ValueError, limit: int = 50,
                 logger: Optional[logging.Logger] = None):
        self._strict = strict
        self._error = error
        self._logger = logger
        self._book = LogBook(limit)

    # --- Saying: one method for each mode, all alike --------------------------------

    def debug(self, message: str, *args) -> None:
        """Logs a line of detail. Not a fault: never kept, counted or raised, and never muted."""
        self._say(logging.DEBUG, _text(message, args))

    def info(self, message: str, *args) -> None:
        """Logs a normal event. Not a fault: never kept, counted or raised."""
        self._say(logging.INFO, _text(message, args))

    def warn(self, message: str, *args) -> None:
        """Records a recovered problem."""
        self._record(Fault(Severity.WARNING, _text(message, args)))

    def error(self, message: str, *args) -> None:
        """Records a failure that was skipped or tried again, while the module still does its job."""
        self._record(Fault(Severity.ERROR, _text(message, args)))

    def critical(self, message: str, *args) -> None:
        """Records a problem the module could not recover from."""
        self._record(Fault(Severity.CRITICAL, _text(message, args)))

    def extend(self, other: "LogRecorder") -> None:
        """Adds every fault from another report (for example a sub-module's)."""
        for fault in other.faults:
            self._record(fault)
        self._book.count_suppressed(other.suppressed)

    # --- The helpers the modes share --------------------------------------------------

    def _record(self, fault: Fault) -> None:
        """A fault: raised in strict mode; otherwise handed to the book and logged at its severity's level."""
        if self._strict:
            raise self._error(fault.message)
        self._book.add(fault)
        self._say(fault.severity.level, fault.message)

    def _say(self, level: int, text: str) -> None:
        """Logs a line at a level, if a logger was given."""
        if self._logger is not None:
            self._logger.log(level, text)

    # --- Reading: the book answers ----------------------------------------------------

    @property
    def faults(self) -> tuple[Fault, ...]:
        """The kept faults, in the order they were found."""
        return self._book.faults

    @property
    def warnings(self) -> list[str]:
        """Messages of the WARNING faults."""
        return self._book.warnings

    @property
    def errors(self) -> list[str]:
        """Messages of the ERROR faults."""
        return self._book.errors

    @property
    def criticals(self) -> list[str]:
        """Messages of the CRITICAL faults."""
        return self._book.criticals

    @property
    def suppressed(self) -> int:
        """How many faults were not kept because the limit was reached."""
        return self._book.suppressed

    @property
    def ok(self) -> bool:
        """True when nothing at all was reported."""
        return self._book.ok

    @property
    def has_critical(self) -> bool:
        """True if any CRITICAL fault was recorded. Criticals are never suppressed."""
        return self._book.has_critical

    def lines(self) -> list[str]:
        """The report as printable lines, ending with a count of any suppressed faults."""
        return self._book.lines()

    def __iter__(self) -> Iterator[Fault]:
        return iter(self._book)

    def __len__(self) -> int:
        return len(self._book)

    def __repr__(self) -> str:
        return f"LogRecorder({len(self)} fault(s){', critical' if self.has_critical else ''})"


def strict_log_recorder(error: type, strict: bool, limit: Optional[int] = None,
                  logger: Optional[logging.Logger] = None) -> LogRecorder:
    """A LogRecorder for a module: strict raises `error`, lenient collects (and logs, if a logger is
    given). Small convenience so every module builds its report the same way."""
    return LogRecorder(strict=strict, error=error, logger=logger,
                  **({} if limit is None else {"limit": limit}))
