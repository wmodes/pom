"""The FaultRecorder: a module's problems found while loading or running, kept as data, not raised.

DESIGN PRINCIPLE (project-wide): the Printer must never stop because of a typo. A hand-edited
file with a bad number, one corrupt line in a corpus file, a missing optional file: none of
these may crash the oracle in a gallery. A module that meets a problem it can recover from
recovers (it falls back to a default, skips the bad entry) and records what it did here, so a
person can fix it later. Nothing is raised at the point of the problem.

LOGGING: a FaultRecorder can be given a logger, and then every fault is also logged the moment it is
recorded (WARNING for warnings, CRITICAL for criticals). The service's stdout goes to journald,
so `journalctl -u pom` shows these without any caller having to remember to print them.
Modules never configure logging themselves; the entry point does that once (pom.log_setup).

Two severities:
  * WARNING:  recovered. The module carried on with a default or skipped something.
  * CRITICAL: the module cannot do its job at all (for example, there are no chunks to draw
              from). It still does not raise; it returns an empty result and says so, and the
              Conductor decides how to degrade (stay quiet and keep listening, say so in the log).

STRICT MODE exists for development: with strict=True every fault is raised immediately as the
module's own error type. Tests and dev tools run strict so a typo is loud while you work; the
runtime never does.

A FaultRecorder keeps the first `limit` faults and counts the rest, so one corrupt 70,000-line file
cannot flood the log or exhaust memory; past the limit it logs a single line saying so. Critical
faults are always kept and always logged.
"""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Optional


class Severity(Enum):
    """How bad a fault is."""
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Fault:
    """One recorded problem.

    Attributes:
        severity: WARNING (recovered) or CRITICAL (cannot do its job).
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
        limit: How many faults to keep; further ones are only counted.
        logger: If given, every kept fault is logged as it is recorded.
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

    def warn(self, message: str) -> None:
        """Records a recovered problem."""
        self._add(Fault(Severity.WARNING, message))

    def critical(self, message: str) -> None:
        """Records a problem the module could not recover from."""
        self._add(Fault(Severity.CRITICAL, message))

    def extend(self, other: "FaultRecorder") -> None:
        """Adds every fault from another report (for example a sub-module's)."""
        for fault in other.faults:
            self._add(fault)
        self._suppressed += other.suppressed

    def _add(self, fault: Fault) -> None:
        """Raises in strict mode; otherwise keeps the fault, or counts it past the limit."""
        if self._strict:
            raise self._error(fault.message)
        # A critical fault is always kept: it is rare, and it must never be hidden behind
        # a flood of warnings.
        if fault.severity is Severity.CRITICAL or len(self._faults) < self._limit:
            self._faults.append(fault)
            self._log(fault)
        else:
            if self._suppressed == 0:
                self._log(Fault(Severity.WARNING, f"more than {self._limit} faults; the rest are "
                                                   f"counted but not logged"))
            self._suppressed += 1

    def _log(self, fault: Fault) -> None:
        """Logs a fault at the matching level, if a logger was given."""
        if self._logger is not None:
            level = logging.CRITICAL if fault.severity is Severity.CRITICAL else logging.WARNING
            self._logger.log(level, fault.message)

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
