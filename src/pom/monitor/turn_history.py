"""The TurnHistory: the recent turns (an hour of them, and any the chart still shows), for the monitoring site.

Each time a hinge qualifies, the Conductor records what happened: the hinge, its score, the bar it had to beat, the
exemplars the oracle was shown and the portent it wrote. The monitoring site reads this list to show the newest
turns and, for each, its details. It is only a memory: the list is for looking at, not for the Printer to act on.

Design decisions, and why:
  * MEMORY ONLY. A hinge is something that was said in the room, and the project keeps nothing that was said in the
    room. So the records live in this object and nowhere else: never written to disk, never logged (the log holds counts only). They are dropped after `max_age_seconds` (an hour), unless the chart still shows the turn's qualifier dot, and
    when the Printer restarts.
  * A TURN STAYS WHILE ITS DOT IS ON THE CHART. After a long silence the chart reaches back hours of clock time, so a dot can outlive its turn and
    link to nothing. So the history is handed a function (`keep_heard_since`) that says the audio time from which turns are kept whatever their age; the
    Printer hands it the chart notebook's oldest attempt. The history does not import the notebook, and asks the function outside its own lock.
  * Bounded twice: by age (with that exception), and by `max_records`, so a flood of turns cannot grow the memory.
  * Thread-safe. The Conductor adds from its thread and the web server reads from its own, so every change and read
    takes one lock, and `recent()` returns a copy.
  * Nothing here may stop the oracle (see pom/__init__.py). A record that cannot be made (a blank hinge, a score that is
    not a number) is recorded as a warning without its text and `add` returns None; a bad setting is replaced by its
    default. `strict=True` raises TurnHistoryError instead, for tests and dev tools.
  * The clock is injected (`clock`, seconds since the epoch), so tests need not wait. A record is stamped with the
    clock when it is added, not with the audio's own time; `heard_at` keeps the place in the audio for reference.
  * Records get a number that only goes up, starting at 1 and never reused, so a page can link to one and a link to a
    record that has since expired simply finds nothing.
  * A record is added as soon as the portent is written, before it is printed, so the monitoring site shows it even while the printer is
    being waited for or has failed; `set_outcome` says how the turn ended afterwards.
  * This module never reads Config.
"""
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Callable, Deque, List, Optional

from pom.bookkeeper.bookkeeper import Bookkeeper
from pom.defaults import MonitorSettings
from pom.writing.portent_writer import Portent
from pom.value_rules import is_number

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module



class TurnHistoryError(ValueError):
    """Raised for any history problem, but only in strict mode (development and tests)."""


@dataclass(frozen=True, slots=True)
class TurnRecord:
    """One turn, and what came of it.

    Attributes:
        id: A number that only goes up, from 1, unique for as long as the history lives.
        at: When it was recorded, in seconds since the epoch.
        heard_at: Where in the audio stream the hinge ended, in seconds from the start of listening.
        hinge: The clip that qualified, word for word.
        score: The hinge's score, 0 to 9.
        bar: The score it had to be above, as it stood before it joined the window (None if there was none yet).
        window_size: How many scores the window held when it arrived.
        portent: What the oracle wrote and was shown, or None if no portent could be had.
        outcome: How the turn ended ("printed", "too late to print" and so on: a short label), or None while it is still going.
    """
    id: int
    at: float
    heard_at: float
    hinge: str
    score: float
    bar: Optional[float]
    window_size: int
    portent: Optional[Portent]
    outcome: Optional[str] = None


class TurnHistory:
    """Keeps the recent turns, newest first.

    Attributes:
        monitor: The `[monitor]` settings it was handed (it reads `history_seconds` and `history_max_turns`).
        logger: Where this class says what it has to say (a LogRecorder from the Bookkeeper).
        total_added: How many records have been accepted since the history was made, expired ones included.
    """

    def __init__(self, monitor: MonitorSettings, clock: Callable[[], float] = time.time, strict: bool = False,
                 keep_heard_since: Optional[Callable[[], Optional[float]]] = None, bookkeeper: Optional[Bookkeeper] = None):
        """Args:
            monitor: The `[monitor]` section: `history_seconds` (how long a record is kept, unless `keep_heard_since` keeps it longer) and
                `history_max_turns` (the most records kept; the oldest go first).
            clock: Returns seconds since the epoch; injected so tests need not wait.
            strict: Raise TurnHistoryError on any problem instead of logging it.
            bookkeeper: The Bookkeeper the entry point built; it hands this class its logger. None (a test or a tool) gives a plain one.
            keep_heard_since: A function returning the audio time (as `heard_at`) from which turns are kept whatever their age, or None for no such
                time: the chart notebook's oldest attempt, so a turn stays while its dot is on the chart. Without one, or if it fails or gives
                something that is not a number, the age limit alone decides.
        """
        self.logger = (bookkeeper or Bookkeeper()).logger_for(__name__, TurnHistoryError, DEBUG, strict)
        self.monitor = monitor
        self._records: Deque[TurnRecord] = deque(maxlen=monitor.history_max_turns)       # oldest first
        self._clock = clock
        self._keep_heard_since = keep_heard_since
        self._lock = threading.Lock()
        self._next_id = 1
        self.total_added = 0

    @property
    def max_age_seconds(self) -> float:
        """How long a record is kept, in seconds."""
        return self.monitor.history_seconds

    def add(self, hinge: str, score: float, bar: Optional[float], window_size: int, heard_at: float,
            portent: Optional[Portent] = None) -> Optional[TurnRecord]:
        """Records a turn and returns its record, or None if it could not be made (the log says why)."""
        if not (isinstance(hinge, str) and hinge.strip()):
            self.logger.warning("a turn was given without a hinge, so it was not recorded")
            return None
        if not is_number(score):
            self.logger.warning("a turn was given without a numeric score, so it was not recorded")
            return None
        keep_from = self._keep_from()                       # asked for outside the lock: the function belongs to another object
        with self._lock:
            record = TurnRecord(self._next_id, self._clock(), heard_at if is_number(heard_at) else 0.0, hinge.strip(),
                                   float(score), float(bar) if is_number(bar) else None,
                                   window_size if isinstance(window_size, int) and not isinstance(window_size, bool) else 0,
                                   portent)
            self._next_id += 1
            self.total_added += 1
            self._records.append(record)
            self._forget_old(keep_from)
        self.logger.debug("recorded turn %d (%d kept)", record.id, len(self._records))
        return record

    def set_outcome(self, record_id: int, outcome: str) -> bool:
        """Says how a turn ended: the record keeps everything else. True if the record was still there and `outcome` is a label.

        A record is added when the portent is written, before it is printed, so the outcome comes later. A record that has already
        expired, or an outcome that is not a non-blank string, changes nothing (the second is also recorded as a warning).
        """
        if not (isinstance(outcome, str) and outcome.strip()):
            self.logger.warning("a turn's outcome must be a short label, so it was not recorded")
            return False
        keep_from = self._keep_from()
        with self._lock:
            self._forget_old(keep_from)
            for index, record in enumerate(self._records):
                if record.id == record_id:
                    self._records[index] = replace(record, outcome=outcome.strip())
                    return True
        return False

    def recent(self) -> List[TurnRecord]:
        """The records still kept (within `max_age_seconds`, or still shown on the chart), newest first. A copy, so it may be changed freely."""
        keep_from = self._keep_from()
        with self._lock:
            self._forget_old(keep_from)
            return list(reversed(self._records))

    def get(self, record_id: int) -> Optional[TurnRecord]:
        """The record with this number, or None if there is none or it has expired."""
        keep_from = self._keep_from()
        with self._lock:
            self._forget_old(keep_from)
            return next((record for record in self._records if record.id == record_id), None)

    def _keep_from(self) -> Optional[float]:
        """The audio time from which records are kept whatever their age, or None. Asked outside the lock; a failing function is a warning, never a stop."""
        if self._keep_heard_since is None:
            return None
        try:
            found = self._keep_heard_since()
        except Exception:                                                    # nothing here may stop the oracle; say what, never a hinge
            self.logger.warning("the function that says which turns the chart still shows failed, so only the age limit applies")
            return None
        return float(found) if is_number(found) else None

    def _forget_old(self, keep_from: Optional[float] = None) -> None:
        """Drops the records older than the age limit, except those heard at or after `keep_from`. The caller holds the lock."""
        cutoff = self._clock() - self.monitor.history_seconds
        kept = [record for record in self._records if record.at >= cutoff or (keep_from is not None and record.heard_at >= keep_from)]
        if len(kept) != len(self._records):
            self._records = deque(kept, maxlen=self._records.maxlen)
