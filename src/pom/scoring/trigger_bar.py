"""The TriggerBar: what the ConvoInterestTracker asks of the part that works out the bar.

The tracker decides whether a score triggers (it is above the bar, and the cooldown is over). WHERE the bar sits is worked out by a
bar, and there are several ways to do it (a percentile of a window of recent scores, a decaying quantile, a moving average), each its
own class. They all answer the same four questions, so the tracker does not care which one it was given:

  * `level_at(now)`: the score a new score must be above to trigger, as the room stands at `now`. None means there is no bar yet
    (a bar with no data and no starting line), and nothing triggers.
  * `window_size_at(now)`: how many real scores the bar is built from at `now`, for display. Starting-line scores do not count.
  * `add(score, now, triggered)`: takes in a score that was judged, and whether it triggered. A bar may leave a triggering score out
    of what it remembers (`exclude_triggers`), which is why it is told.
  * `ignores(score)`: True for a score the bar wants nothing to do with (a score under a floor). Most bars ignore nothing.

`now` is seconds, from the same clock for every call (live it is a clock reading; in a replay it is the place in the recording).
The tracker never lets it run backwards.

Each bar is built from the plain settings object the config produces (`from_settings`), and reads only the attributes it needs; this
package never reads Config.
"""
from abc import ABC, abstractmethod
from typing import Optional


class TriggerBar(ABC):
    """Works out where the bar sits for the ConvoInterestTracker."""

    @abstractmethod
    def level_at(self, now: float) -> Optional[float]:
        """The score a new score must be above to trigger at `now`, or None if there is no bar yet."""

    @abstractmethod
    def window_size_at(self, now: float) -> int:
        """How many real scores the bar is built from at `now`."""

    @abstractmethod
    def add(self, score: float, now: float, triggered: bool) -> None:
        """Takes in a judged score at `now`; `triggered` says whether it fired."""

    def ignores(self, score: float) -> bool:
        """True if this score should be left out of everything (default: no score is)."""
        return False
