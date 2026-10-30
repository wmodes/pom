"""The TriggerHistory: the last hour of triggers, for the monitoring site.

Each time the trigger fires, the Conductor records what happened: the hinge, its score, the bar it had to beat, the
exemplars the oracle was shown and the portent it wrote. The monitoring site reads this list to show the newest
triggers and, for each, its details. It is only a memory: the list is for looking at, not for the Printer to act on.

Design decisions, and why:
  * MEMORY ONLY. A hinge is something that was said in the room, and the project keeps nothing that was said in the
    room. So the records live in this object and nowhere else: never written to disk, never logged, never put in a
    report (those hold counts only). They are dropped after `max_age_seconds` (an hour) and when the Printer restarts.
  * Bounded twice: by age, and by `max_records`, so a flood of triggers cannot grow the memory.
  * Thread-safe. The Conductor adds from its thread and the web server reads from its own, so every change and read
    takes one lock, and `recent()` returns a copy.
  * Nothing here may stop the oracle (see pom/__init__.py). A record that cannot be made (a blank hinge, a score that is
    not a number) is recorded as a warning without its text and `add` returns None; a bad setting is replaced by its
    default. `strict=True` raises TriggerHistoryError instead, for tests and dev tools.
  * The clock is injected (`clock`, seconds since the epoch), so tests need not wait. A record is stamped with the
    clock when it is added, not with the audio's own time; `heard_at` keeps the place in the audio for reference.
  * Records get a number that only goes up, starting at 1 and never reused, so a page can link to one and a link to a
    record that has since expired simply finds nothing.
  * This module never reads Config.
"""
import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, List, Optional

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger
from pom.voice.portent_writer import Portent

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_MAX_AGE_SECONDS = 3600.0
DEFAULT_MAX_RECORDS = 500


class TriggerHistoryError(ValueError):
    """Raised for any history problem, but only in strict mode (development and tests)."""


@dataclass(frozen=True, slots=True)
class TriggerRecord:
    """One trigger, and what came of it.

    Attributes:
        id: A number that only goes up, from 1, unique for as long as the history lives.
        at: When it was recorded, in seconds since the epoch.
        heard_at: Where in the audio stream the hinge ended, in seconds from the start of listening.
        hinge: The sentence that triggered, word for word.
        score: The hinge's score, 0 to 9.
        bar: The score it had to be above, as it stood before it joined the window (None if there was none yet).
        window_size: How many scores the window held when it arrived.
        portent: What the oracle wrote and was shown, or None if no portent could be had.
    """
    id: int
    at: float
    heard_at: float
    hinge: str
    score: float
    bar: Optional[float]
    window_size: int
    portent: Optional[Portent]


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class TriggerHistory:
    """Keeps the recent triggers, newest first.

    Attributes:
        report: Problems met (a bad setting, a record that could not be made). Never holds a hinge or a portent.
        total_added: How many records have been accepted since the history was made, expired ones included.
    """

    def __init__(self, max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS, max_records: int = DEFAULT_MAX_RECORDS,
                 clock: Callable[[], float] = time.time, strict: bool = False):
        """Args:
            max_age_seconds: How long a record is kept, above 0 (3600 is an hour).
            max_records: The most records kept, a whole number of at least 1; the oldest go first.
            clock: Returns seconds since the epoch; injected so tests need not wait.
            strict: Raise TriggerHistoryError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=TriggerHistoryError, logger=_log)
        if not (_is_number(max_age_seconds) and max_age_seconds > 0):
            self.report.warn(f"max_age_seconds must be a number above 0, got {max_age_seconds!r}; "
                             f"using {DEFAULT_MAX_AGE_SECONDS:g}")
            max_age_seconds = DEFAULT_MAX_AGE_SECONDS
        if not (isinstance(max_records, int) and not isinstance(max_records, bool) and max_records >= 1):
            self.report.warn(f"max_records must be a whole number of at least 1, got {max_records!r}; "
                             f"using {DEFAULT_MAX_RECORDS}")
            max_records = DEFAULT_MAX_RECORDS
        self._max_age = float(max_age_seconds)
        self._records: Deque[TriggerRecord] = deque(maxlen=max_records)       # oldest first
        self._clock = clock
        self._lock = threading.Lock()
        self._next_id = 1
        self.total_added = 0

    def add(self, hinge: str, score: float, bar: Optional[float], window_size: int, heard_at: float,
            portent: Optional[Portent] = None) -> Optional[TriggerRecord]:
        """Records a trigger and returns its record, or None if it could not be made (see `report`)."""
        if not (isinstance(hinge, str) and hinge.strip()):
            self.report.warn("a trigger was given without a hinge, so it was not recorded")
            return None
        if not _is_number(score):
            self.report.warn("a trigger was given without a numeric score, so it was not recorded")
            return None
        with self._lock:
            record = TriggerRecord(self._next_id, self._clock(), heard_at if _is_number(heard_at) else 0.0, hinge.strip(),
                                   float(score), float(bar) if _is_number(bar) else None,
                                   window_size if isinstance(window_size, int) and not isinstance(window_size, bool) else 0,
                                   portent)
            self._next_id += 1
            self.total_added += 1
            self._records.append(record)
            self._forget_old()
        _log.debug("recorded trigger %d (%d kept)", record.id, len(self._records))
        return record

    def recent(self) -> List[TriggerRecord]:
        """The records still within `max_age_seconds`, newest first. A copy, so it may be changed freely."""
        with self._lock:
            self._forget_old()
            return list(reversed(self._records))

    def get(self, record_id: int) -> Optional[TriggerRecord]:
        """The record with this number, or None if there is none or it has expired."""
        with self._lock:
            self._forget_old()
            return next((record for record in self._records if record.id == record_id), None)

    def _forget_old(self) -> None:
        """Drops the records older than the age limit. The caller holds the lock."""
        cutoff = self._clock() - self._max_age
        while self._records and self._records[0].at < cutoff:
            self._records.popleft()
