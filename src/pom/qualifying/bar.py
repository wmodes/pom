"""The Bar: what the BarKeeper asks of the part that works out the bar.

The BarKeeper decides whether a score qualifies (it is above the bar, and the cooldown is over). WHERE the bar sits is worked out by a
bar, and there are several ways to do it (a percentile of a window of recent scores, a decaying quantile, a moving average), each its
own class. They all answer the same four questions, so the BarKeeper does not care which one it was given:

  * `level_at(now)`: the score a new score must be above to qualify, as the room stands at `now`. None means there is no bar yet
    (a bar with no data and no starting line), and nothing qualifies.
  * `window_size_at(now)`: how many real scores the bar is built from at `now`, for display (a bar that fades scores counts them by
    their weight). Starting-line scores do not count.
  * `add(score, now, qualified)`: takes in a score that was judged, and whether it qualified. A bar may leave a qualifying score out
    of what it remembers (`exclude_qualifiers`), which is why it is told.
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
    def add(self, score: float, now: float, qualified: bool) -> None:
        """Takes in a judged score at `now`; `qualified` says whether it fired."""

    @abstractmethod
    def describe(self) -> str:
        """One line saying how the bar works and how it is set, for a testbench or a log."""

    def ignores(self, score: float) -> bool:
        """True if this score should be left out of everything (default: no score is)."""
        return False
