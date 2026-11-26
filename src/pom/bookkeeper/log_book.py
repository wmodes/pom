"""The LogBook: holds the faults a module recorded, so they can be read later (the startup summary, a test, `has_critical`).

It has one job: keep the first `limit` faults (and every critical one, which must never be hidden behind a flood of warnings), count the rest, and
answer the readers. It does not log, raise or mute: saying a fault, and raising it in strict mode, belong to the ReportRecorder; muting a repeat
belongs to the log (pom.bookkeeper.log_muter).

It owns its state, so it owns the lock: every method takes it, and what comes back is a copy, never the live list.
"""
import threading
from typing import Iterator

from pom.bookkeeper.fault import Fault, Severity


class LogBook:
    """The kept faults of one module or one operation.

    Args:
        limit: How many faults to keep for reading; further ones (except criticals) are only counted, so a long outage cannot exhaust memory.
    """

    def __init__(self, limit: int):
        self._limit = limit
        self._faults: list[Fault] = []
        self._suppressed = 0
        self._lock = threading.Lock()

    def add(self, fault: Fault) -> None:
        """Keeps the fault, or counts it when the limit is reached. A critical fault is always kept."""
        with self._lock:
            if fault.severity is Severity.CRITICAL or len(self._faults) < self._limit:
                self._faults.append(fault)
            else:
                self._suppressed += 1

    def count_suppressed(self, count: int) -> None:
        """Adds faults that another book counted without keeping."""
        with self._lock:
            self._suppressed += count

    @property
    def faults(self) -> tuple[Fault, ...]:
        """The kept faults, in the order they were found."""
        with self._lock:
            return tuple(self._faults)

    def _messages(self, severity: Severity) -> list[str]:
        """The messages of the kept faults of one severity."""
        return [fault.message for fault in self.faults if fault.severity is severity]

    @property
    def warnings(self) -> list[str]:
        """Messages of the WARNING faults."""
        return self._messages(Severity.WARNING)

    @property
    def errors(self) -> list[str]:
        """Messages of the ERROR faults."""
        return self._messages(Severity.ERROR)

    @property
    def criticals(self) -> list[str]:
        """Messages of the CRITICAL faults."""
        return self._messages(Severity.CRITICAL)

    @property
    def suppressed(self) -> int:
        """How many faults were not kept because the limit was reached."""
        with self._lock:
            return self._suppressed

    @property
    def ok(self) -> bool:
        """True when nothing at all was reported."""
        with self._lock:
            return not self._faults and not self._suppressed

    @property
    def has_critical(self) -> bool:
        """True if any CRITICAL fault was recorded. Criticals are never suppressed."""
        return bool(self.criticals)

    def lines(self) -> list[str]:
        """The faults as printable lines, ending with a count of any not kept."""
        with self._lock:
            out = [str(fault) for fault in self._faults]
            if self._suppressed:
                out.append(f"... and {self._suppressed} more")
            return out

    def __iter__(self) -> Iterator[Fault]:
        return iter(self.faults)

    def __len__(self) -> int:
        with self._lock:
            return len(self._faults) + self._suppressed
