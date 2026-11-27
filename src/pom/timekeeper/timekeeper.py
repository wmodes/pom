"""The Timekeeper: the stream's one clock, which every part of the Printer reads.

Stream time is the Printer's timeline: seconds of sound, counted by the listening side, going on through silence and through turns. The bar,
the cooldown, the chart and the typed lines all stamp their moments on it, so they must all read the same clock, or a typed line lands ahead
of the room and the BarKeeper (which never lets time run backwards) holds still until the room catches up. The Timekeeper is that clock. The
entry point builds one, has it follow the room that is listening now, and hands every part that needs "now" the same `timekeeper.now`.

Design decisions, and why:
  * BEFORE THE FIRST ROOM IT RUNS ON REAL SECONDS. There is no sound to count until a room is built (the microphone can take a while to come
    up), so the clock starts at `started_at` when it is made and goes on by the real seconds since. A typed line before the microphone is up
    is then on the same timeline as the room after it.
  * A NEW ROOM BEGINS WHERE THE CLOCK STANDS. The entry point gives each new room `now()` as its start and then `follow`s it, so the clock
    carries on without a jump. Between a room that stopped and the next one, the old room's clock already runs on by real seconds
    (`Eavesdropper.stream_time_at`), so nothing else is needed for that gap.
  * Time while a room is followed is the room's, counted in audio received, so a recording replayed fast runs on the recording's time.
  * One lock guards which room is followed, and the room is asked the time outside it, so a slow or re-entrant room never blocks a reader.
"""
import threading
import time
from typing import Callable, Optional


class Timekeeper:
    """Keeps the stream's time: `now()` for every part, `follow(room)` for the entry point when a room starts listening."""

    def __init__(self, started_at: float = 0.0, wall: Callable[[], float] = time.monotonic):
        """Starts the clock.

        Args:
            started_at: The stream time the clock stands at when it is made (the stream's start, the same one the bar is given).
            wall: Real time in seconds, the same clock the rooms are given as their `wall`.
        """
        self._wall = wall
        self._lock = threading.Lock()
        self._room: Optional[object] = None                # the room whose clock this is, once one is listening
        self._started_at, self._made_at = started_at, wall()

    def now(self) -> float:
        """The stream time right now: the followed room's clock, or, before any room, the start plus the real seconds since."""
        with self._lock:
            room = self._room
        wall_now = self._wall()
        if room is None:
            return self._started_at + (wall_now - self._made_at)
        return room.stream_time_at(wall_now)

    def follow(self, room) -> None:
        """From now on the clock is this room's (an Eavesdropper, begun at `now()` so the time carries on)."""
        with self._lock:
            self._room = room
