"""The SilenceShape and the SilenceCompressor: how a long silence is drawn, and how a stream time becomes a drawn time because of it.

The shape is one value, worked out once from the chart's settings (`score_chart.silence_shape`), that everything which has to agree about a silence is
handed: the chart (to place things), the chart's notebook (to know what is old enough to forget, in drawn time) and the tick writer (to know which ticks
a silence needs). They live on their own, with no import of any of them.
"""
from dataclasses import dataclass
from typing import Optional, Sequence


@dataclass(frozen=True)
class SilenceShape:
    """How a silence is drawn, like a broken part in an engineering drawing: its first `edge_seconds` at true scale, a cut `gap_seconds` wide, then its
    last `edge_seconds` at true scale.

    Attributes:
        longest_seconds: The longest a silence is drawn, and the length above which a quiet stretch counts as one. Above 0.
        gap_seconds: How much of `longest_seconds` the cut takes, in the chart's drawn seconds. Held between 0 and `longest_seconds`; None (the default),
            or at least `longest_seconds`, means all of it: the silence is then drawn as nothing but the cut, with no true-scale sides.
    """
    longest_seconds: float
    gap_seconds: Optional[float] = None

    def __post_init__(self):
        if isinstance(self.longest_seconds, bool) or not isinstance(self.longest_seconds, (int, float)) or not self.longest_seconds > 0:
            raise ValueError(f"longest_seconds must be a number above 0, got {self.longest_seconds!r}")
        gap = self.longest_seconds if self.gap_seconds is None else min(max(self.gap_seconds, 0.0), self.longest_seconds)
        object.__setattr__(self, "gap_seconds", float(gap))                     # frozen, so the held value is set this way

    @property
    def edge_seconds(self) -> float:
        """How much of a silence is drawn at true scale on each side of its cut: half of what `longest_seconds` has left once the cut has taken its share.
        Also what the notebook records at the start of a silence and the page works out at its end."""
        return (self.longest_seconds - self.gap_seconds) / 2.0


class SilenceCompressor:
    """Draws long silences short: it turns a stream time into a drawn time, in which each silence is `shape.longest_seconds` wide at most.

    A silence is a stretch with no attempt longer than that: between two attempts, between the newest attempt and now, and, when the stream's start is
    known (`began_at`), between the start and the first attempt (or now, if there has been none). It is drawn as the shape says: its first `edge` seconds
    at true scale, then a cut `gap` wide (all of what is left out of the middle is drawn in that), then its last `edge` seconds at true scale.
    Everything after a silence is shifted left by what was cut out.
    """

    def __init__(self, attempt_times: Sequence[float], now: float, shape: SilenceShape, began_at: Optional[float] = None):
        """Args:
            attempt_times: When each attempt was heard.
            now: The stream time at the right edge.
            shape: How a silence is drawn.
            began_at: The stream's start, if known: a quiet start is a silence too.
        """
        edges = ([] if began_at is None else [began_at]) + sorted(attempt_times) + [now]          # the stream's start, if known, begins the first silence
        self.longest = shape.longest_seconds
        self.gap = shape.gap_seconds
        self.edge = shape.edge_seconds
        longest = self.longest
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
