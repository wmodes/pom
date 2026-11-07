"""The score chart: the room's scores and the bar riding above them, drawn as a small inline SVG for the monitoring site.

`render_score_chart(points, qualifier_ids, look)` takes the points of a ScoreTrace and returns one `<svg>` element, the width of the text:

  * every attempt is a small dot (black on the page), on a fixed scale from 0 to `score_max` with gridlines at `score_gridlines`, and vertical
    gridlines every `gridline_every_minutes`, so it reads like graph paper;
  * the bar each sentence was judged against is a line (red on the page) that steps from one sentence to the next, so it rises and
    falls with the room;
  * each qualifier is a larger orange dot, and where its record is known (`qualifier_ids`, by the audio time it was heard) the dot is a link
    to that qualifier's page, with the score and the bar as its tooltip.
  * the time axis runs back from the newest point ("now", "-10 min", ...), over the last `window_minutes`.

Design decisions, and why:
  * EVERY NUMBER THAT SHAPES THE CHART IS A SETTING. They are the `[chart]` section of the config, passed in as `look` (anything with the attributes
    of `ChartLook`, which is what `config.chart` is). `ChartLook` here only holds the fallbacks, for a caller with no config (the tests, a playground).
    Colors are not settings: they come from the page's own CSS, which follows its light and dark theme.
  * NUMBERS ONLY. The chart is built from scores, bars and times; no words from the room can reach it, so it is shown whether or not
    hinges are hidden. Every number in the markup is formatted here, never passed through from outside.
  * It is a pure function that returns a string, so it can be tested exactly and the page can draw it on the server with no script and
    no charting library. The colours come from the page's own CSS (classes `grid` and `vgrid`, `dot`, `bar`, `hit`, and `key` for text), so the chart
    follows light and dark mode.
  * The scale is fixed (0 to `score_max`) and not fitted to the data, so the bar's height means the same in every room and
    a quiet stretch looks quiet. A score outside it is drawn at the edge.
  * A gap in the bar (a sentence judged before any bar existed) breaks the line instead of joining across it.
"""
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from pom.monitor.score_trace import TracePoint


@dataclass(frozen=True)
class ChartLook:
    """The numbers that shape the chart, as fallbacks; `config.chart` has the same fields and is what the Printer passes. See `[chart]` in the config."""
    window_minutes: float = 30.0
    label_every_minutes: float = 10.0
    gridline_every_minutes: float = 5.0
    score_max: float = 9.0
    score_gridlines: tuple = (0.0, 3.0, 6.0, 9.0)
    drawing_width: float = 720.0
    drawing_height: float = 150.0
    margin_left: float = 24.0
    margin_right: float = 6.0
    margin_top: float = 8.0
    margin_bottom: float = 18.0
    attempt_dot_radius: float = 1.8
    qualifier_dot_radius: float = 4.0
    bar_line_width: float = 1.6


def _x(at: float, t_end: float, seconds: float, plot_width: float, left: float) -> float:
    return left + plot_width * (1.0 - (t_end - at) / seconds)


def _y(score: float, plot_height: float, top: float, score_max: float) -> float:
    clamped = min(max(score, 0.0), score_max)
    return top + plot_height * (1.0 - clamped / score_max)


def render_score_chart(points: Sequence[TracePoint], qualifier_ids: Optional[Mapping[float, int]] = None, look=None) -> str:
    """The chart for these points as an `<svg>` string.

    Args:
        points: The ScoreTrace's points, oldest first.
        qualifier_ids: The number of each qualifier's record by the audio time it was heard, for linking its dot (a qualifier not in
            it is drawn without a link, for example one whose portent is still being written).
        look: The `[chart]` settings (anything with the fields of `ChartLook`); None means the fallbacks.
    """
    look = look if look is not None else ChartLook()
    qualifier_ids = qualifier_ids or {}
    seconds = look.window_minutes * 60.0
    width, height = look.drawing_width, look.drawing_height
    left, right, top, bottom = look.margin_left, look.margin_right, look.margin_top, look.margin_bottom
    plot_width, plot_height = width - left - right, height - top - bottom
    head = f'<svg class="chart" viewBox="0 0 {width:g} {height:g}" role="img" xmlns="http://www.w3.org/2000/svg">'
    if not points:
        return (f'{head}<title>No scores yet.</title><text class="key" x="{width / 2:.1f}" y="{height / 2:.1f}" '
                f'text-anchor="middle">No scores yet.</text></svg>')
    t_end = points[-1].at
    shown = [point for point in points if point.at >= t_end - seconds]
    qualifiers = [point for point in shown if point.qualified]
    parts = [head, f"<title>Scores and the bar over the last {look.window_minutes:g} minutes: {len(shown)} sentences, "
                   f"{len(qualifiers)} qualifiers.</title>"]
    for value in look.score_gridlines:
        y = _y(value, plot_height, top, look.score_max)
        parts.append(f'<line class="grid" x1="{left:g}" x2="{width - right:g}" y1="{y:.1f}" y2="{y:.1f}"/>'
                     f'<text class="key" x="{left - 4:g}" y="{y + 3:.1f}" text-anchor="end">{value:g}</text>')
    minor_every = look.gridline_every_minutes * 60.0
    minor = 0
    while minor * minor_every <= seconds:
        x = _x(t_end - minor * minor_every, t_end, seconds, plot_width, left)
        parts.append(f'<line class="vgrid" x1="{x:.1f}" x2="{x:.1f}" y1="{top:g}" y2="{top + plot_height:.1f}"/>')
        minor += 1
    label_every = look.label_every_minutes * 60.0
    tick = 0
    while tick * label_every <= seconds:
        x = _x(t_end - tick * label_every, t_end, seconds, plot_width, left)
        label = "now" if tick == 0 else f"-{tick * label_every / 60:g} min"
        parts.append(f'<text class="key" x="{x:.1f}" y="{height - 4:g}" text-anchor="{"end" if tick == 0 else "middle"}">{label}</text>')
        tick += 1
    for point in shown:
        if not point.qualified:
            parts.append(f'<circle class="dot" cx="{_x(point.at, t_end, seconds, plot_width, left):.1f}" '
                         f'cy="{_y(point.score, plot_height, top, look.score_max):.1f}" r="{look.attempt_dot_radius:g}"/>')
    path, previous_bar = [], False
    for point in shown:
        if point.bar is None:
            previous_bar = False
            continue
        x, y = _x(point.at, t_end, seconds, plot_width, left), _y(point.bar, plot_height, top, look.score_max)
        path.append(f"V{y:.1f} H{x:.1f}" if previous_bar else f"M{x:.1f} {y:.1f}")
        previous_bar = True
    if path:
        parts.append(f'<path class="bar" stroke-width="{look.bar_line_width:g}" d="{" ".join(path)}"/>')
    for point in qualifiers:
        circle = (f'<circle class="hit" cx="{_x(point.at, t_end, seconds, plot_width, left):.1f}" '
                  f'cy="{_y(point.score, plot_height, top, look.score_max):.1f}" r="{look.qualifier_dot_radius:g}">'
                  f'<title>score {point.score:.2f}'
                  f'{"" if point.bar is None else f", bar {point.bar:.2f}"}</title></circle>')
        record = qualifier_ids.get(point.at)
        parts.append(f'<a href="/turn/{int(record)}">{circle}</a>' if record is not None else circle)
    parts.append("</svg>")
    return "".join(parts)
