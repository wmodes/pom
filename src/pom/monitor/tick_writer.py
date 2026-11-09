"""The TickWriter: works out which of the anchor's ticks the chart draws for a stretch of the stream, as notebook entries.

The ChartNotebook asks it for the ticks to write down when an attempt is heard, and the page (through the notebook) asks it for the ones since the
newest attempt, live. It keeps no list of its own: it is given where the stretch begins and what was worked out for it earlier, and answers.

The rule it carries (see the ChartNotebook's module note for why):
  * A stretch no longer than the shape's `longest_seconds` is all drawn: every tick in it.
  * A longer one is a silence, drawn as its first minutes, a cut, and its last minutes (see `silence_compressor.SilenceCompressor`). Only the first
    and the last `edge_seconds` of it have ticks; nothing at all is made for the cut, which the chart never draws.
  * The first minutes are worked out when the attempt that begins the silence is heard (`first_minutes`), because the bar may have forgotten the
    scores they depend on by the time anyone asks again. The last minutes are worked out when they are wanted.

Nothing here may stop the oracle (see pom/__init__.py): a ticks source that fails or a tick that is not numbers is a warning on the `report` it is
given (the notebook's), never a value from the room, and no ticks for that stretch. This module never reads Config and never imports the bar: it is
handed a function.
"""
from typing import Callable, List, Optional

from pom.fault_recorder import FaultRecorder
from pom.monitor.notebook_entry import TICK, NotebookEntry, is_number
from pom.monitor.silence_compressor import SilenceShape


class TickWriter:
    """Makes the ticks for a stretch of the stream, from a ticks source, as TICK entries.

    Attributes:
        ticks_source: A function from (start, end) to the ticks in that stretch, each as (time, anchor score, the bar at that tick), oldest first
            (the bar's `ticks_between`); None means there are none to make.
        shape: How the chart draws a silence: a stretch longer than `shape.longest_seconds` is one, and the first and last `shape.edge_seconds` of it have ticks.
        began_at: The stream time at which listening began, or None; the stretch before the first attempt runs from here.
    """

    def __init__(self, ticks_source: Optional[Callable[[float, float], list]], shape: SilenceShape, began_at: Optional[float], report: FaultRecorder):
        """Args:
            ticks_source: See the attribute. Without one, every method answers an empty list.
            shape: How a silence is drawn. An `edge_seconds` of 0 means a silence has no ticks at all.
            began_at: The stream time at which listening began; anything that is not a number means it is not known.
            report: Where problems are recorded (the notebook's own), so they are reported in one place.
        """
        self.ticks_source = ticks_source
        self.shape = shape
        self.began_at = float(began_at) if is_number(began_at) else None
        self._report = report

    def ticks_between(self, start: float, end: float) -> List[NotebookEntry]:
        """The source's ticks after `start` and up to `end`, as entries. The start's own tick belongs to whatever was heard then."""
        if self.ticks_source is None:
            return []
        try:
            found = list(self.ticks_source(start, end))
        except Exception:                                          # nothing here may stop the oracle; say what, never a value from the room
            self._report.warn("the ticks source failed, so no ticks were written for this stretch")
            return []
        ticks = []
        for item in found:
            if not (isinstance(item, (tuple, list)) and len(item) == 3 and is_number(item[0]) and is_number(item[1])):
                self._report.warn("a tick was given without a numeric time and score, so it was not kept")
                continue
            if start < item[0] <= end:
                ticks.append(NotebookEntry(float(item[0]), float(item[1]), float(item[2]) if is_number(item[2]) else None, False, TICK))
        return ticks

    def first_minutes(self, since: float) -> List[NotebookEntry]:
        """The ticks for the first `edge_seconds` after `since`: what the chart draws at the start of a silence that begins there."""
        edge = self.shape.edge_seconds
        return self.ticks_between(since, since + edge) if edge > 0 else []

    def stretch(self, since: Optional[float], first_minutes: List[NotebookEntry], until: float) -> List[NotebookEntry]:
        """The ticks the chart draws from `since` to `until`, oldest first.

        Args:
            since: The time of the newest attempt, or None if there is none yet (the stretch then runs from `began_at`, if that is known).
            first_minutes: What `first_minutes` gave when that attempt was heard (worked out while the bar still held the scores they depend on).
            until: The end of the stretch: the attempt now being heard, or now.

        Returns:
            Every tick in the stretch if it is no longer than the longest silence; otherwise the first and last minutes of it only. Empty if there
            is no source, no start, or the stretch has no length.
        """
        if self.ticks_source is None or not is_number(until):
            return []
        began = since is None
        if began:
            since = self.began_at
        if since is None or not until > since:
            return []
        if until - since <= self.shape.longest_seconds:
            return self.ticks_between(since, until)
        if began:                                                  # nothing was heard before it, so no scores to wait for: worked out now, as exactly
            first_minutes = self.first_minutes(since)
        edge = self.shape.edge_seconds
        last_minutes = self.ticks_between(until - edge, until) if edge > 0 else []
        return first_minutes + last_minutes                         # the rest of the silence is cut out: no ticks for it
