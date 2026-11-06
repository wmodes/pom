"""The Bar: what the BarKeeper asks of the part that works out the bar.

The BarKeeper decides whether a score qualifies (it is above the bar, and the cooldown is over). WHERE the bar sits is worked out by a
bar, and there are several ways to do it (a percentile of a window of recent scores, a decaying quantile, a moving average), each its
own class. They all answer the same four questions, so the BarKeeper does not care which one it was given:

  * `level_at(now)`: the score a new score must be above to qualify, as the room stands at `now`. None means there is no bar yet
    (a bar with no data and no starting line), and nothing qualifies.
  * `window_size_at(now)`: how many real scores the bar is built from at `now`, for display (a bar that fades scores counts them by
    their weight). Starting-line scores do not count.
  * `add(score, now, weight)`: takes in a score that was judged, and how much it counts toward the bar. A normal score counts 1; the
    BarKeeper hands a score that stood above the bar the qualifier's share (`qualifier_bar_influence`), and a typed line its own
    (`typed_lines_bar_influence`). A bar that can weight scores (the decaying quantile) remembers the score at that weight; a bar that cannot
    remembers it whole if the weight is above 0, and not at all if it is 0 or less (a quantile has no use for a negative weight).
  * `ignores(score)`: True for a score the bar wants nothing to do with (a score under a floor). Most bars ignore nothing.
  * `describe()`: one line saying how this bar works and how it is set, for a testbench or a log (numbers only).

`now` is seconds, from the same clock for every call (live it is a clock reading; in a replay it is the place in the recording).
The BarKeeper never lets it run backwards.

Each bar is built from the plain settings object the config produces (`from_settings`), and reads only the attributes it needs; this
package never reads Config.
"""
from abc import ABC, abstractmethod
from typing import Optional


class Bar(ABC):
    """Works out where the bar sits for the BarKeeper."""

    @abstractmethod
    def level_at(self, now: float) -> Optional[float]:
        """The score a new score must be above to qualify at `now`, or None if there is no bar yet."""

    @abstractmethod
    def window_size_at(self, now: float) -> int:
        """How many real scores the bar is built from at `now`."""

    @abstractmethod
    def add(self, score: float, now: float, weight: float = 1.0) -> None:
        """Takes in a judged score at `now`, counting `weight` toward the bar (1 is a normal score; 0 or less leaves it out)."""

    @abstractmethod
    def describe(self) -> str:
        """One line saying how the bar works and how it is set, for a testbench or a log."""

    def ignores(self, score: float) -> bool:
        """True if this score should be left out of everything (default: no score is)."""
        return False
