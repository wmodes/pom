"""The SilenceCompressor: turns a stream time into a drawn time, so a long silence takes a fixed width on the chart.

Both the chart (to place things) and the chart's notebook (to know what is old enough to forget, in drawn time) need it, so it lives on its own, with
no import of either.
"""
from typing import Optional, Sequence


class SilenceCompressor:
    """Draws long silences short: it turns a stream time into a drawn time, in which each silence is `longest` wide at most.

    A silence is a stretch with no attempt longer than `longest`: between two attempts, between the newest attempt and now, and, when the stream's
    start is known (`began_at`), between the start and the first attempt (or now, if there has been none). It is drawn like a broken part in an
    engineering drawing: its first `edge` seconds at true scale, then a cut `gap` wide (all of what is left out of the middle is drawn in that),
    then its last `edge` seconds at true scale, where `edge` is half of what `longest` has left after the gap. Everything after a silence is
    shifted left by what was cut out.
    """

    def __init__(self, attempt_times: Sequence[float], now: float, longest: float, began_at: Optional[float] = None, gap: Optional[float] = None):
        """Args:
            attempt_times: When each attempt was heard.
            now: The stream time at the right edge.
            longest: The longest a silence is drawn, and the length above which a quiet stretch counts as one.
            began_at: The stream's start, if known: a quiet start is a silence too.
            gap: How much of `longest` the cut takes, in the chart's drawn seconds. None, or at least `longest`, means all of it: the silence is
                then drawn as nothing but the cut, squeezed evenly.
        """
        edges = ([] if began_at is None else [began_at]) + sorted(attempt_times) + [now]          # the stream's start, if known, begins the first silence
        self.longest = longest
        self.gap = longest if gap is None else min(max(gap, 0.0), longest)
        self.edge = (longest - self.gap) / 2.0
        self.silences = [(a, b) for a, b in zip(edges, edges[1:]) if b - a > longest]

    def drawn(self, at: float) -> float:
        """`at` as a drawn time: the stream time with what every silence before it cut out taken away."""
        cut = 0.0
        for start, end in self.silences:
            cut_length = end - start - 2.0 * self.edge                      # the real stretch in the middle that the gap stands for
            inside = min(max(at - (start + self.edge), 0.0), cut_length)       # how far `at` is into it
            cut += inside * (1.0 - self.gap / cut_length)
        return at - cut

    def gap_drawn(self, start: float) -> tuple:
        """Where the cut of the silence that begins at `start` is, as (from, to) drawn times."""
        begins = self.drawn(start) + self.edge
        return begins, begins + self.gap

    def within(self, drawn: float) -> bool:
        """True if this drawn time falls inside a silence as drawn (its two sides and its cut)."""
        return any(self.drawn(start) < drawn < self.drawn(start) + self.longest for start, _ in self.silences)
