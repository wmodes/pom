"""The NotebookEntry: one attempt or one tick, as numbers, which is all the chart notebook holds.

It lives in its own module so that the ChartNotebook (which keeps entries) and the TickWriter (which makes the tick ones) can both use it without
importing each other. `pom.monitor.chart_notebook` still offers it under the same names.
"""
from dataclasses import dataclass
from typing import Optional

ATTEMPT = "attempt"
TICK = "tick"


@dataclass(frozen=True, slots=True)
class NotebookEntry:
    """One attempt or one tick, as numbers.

    Attributes:
        at: Where in the audio stream it happened, in seconds from the start of listening (for an attempt, where it ended).
        score: An attempt's score, 0 to 9; a tick's is the anchor's score.
        bar: The bar at that moment: for an attempt the score it had to be above, as the bar stood before it joined; for a tick the bar as it
            stood at that tick (None if there was none).
        qualified: True if an attempt qualified (always False for a tick).
        kind: "attempt" (the default) or "tick".
    """
    at: float
    score: float
    bar: Optional[float]
    qualified: bool
    kind: str = ATTEMPT

