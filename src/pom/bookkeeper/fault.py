"""The small shared types of the bookkeeper: how bad a fault is, and one recorded fault.

They live apart so the LogRecorder (which says things) and the LogBook (which holds the faults) can both use them without importing each other.
`pom.bookkeeper.log_recorder` re-exports both under their old home.
"""
import logging
from dataclasses import dataclass
from enum import Enum


class Severity(Enum):
    """How bad a fault is."""
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"

    @property
    def level(self) -> int:
        """The logging level a fault of this severity is logged at."""
        return _LEVELS[self]


_LEVELS = {Severity.WARNING: logging.WARNING, Severity.ERROR: logging.ERROR, Severity.CRITICAL: logging.CRITICAL}


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
