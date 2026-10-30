"""The score chart: the room's scores and the bar riding above them, drawn as a small inline SVG for the monitoring site.

`render_score_chart(points, trigger_ids)` takes the points of a ScoreTrace and returns one `<svg>` element, the width of the text:

  * every scored sentence is a small dot (black on the page), on a fixed 0 to 9 scale with gridlines at 0, 3, 6 and 9, and vertical
    gridlines every five minutes, so it reads like graph paper;
  * the bar each sentence was judged against is a line (red on the page) that steps from one sentence to the next, so it rises and
    falls with the room;
  * each trigger is a larger orange dot, and where its record is known (`trigger_ids`, by the audio time it was heard) the dot is a link
    to that trigger's page, with the score and the bar as its tooltip.
  * the time axis runs back from the newest point ("now", "-10 min", ...), over the last `seconds` (30 minutes to begin with).

Design decisions, and why:
  * NUMBERS ONLY. The chart is built from scores, bars and times; no words from the room can reach it, so it is shown whether or not
    hinges are hidden. Every number in the markup is formatted here, never passed through from outside.
  * It is a pure function that returns a string, so it can be tested exactly and the page can draw it on the server with no script and
    no charting library. The colours come from the page's own CSS (classes `grid` and `vgrid`, `dot`, `bar`, `hit`, and `key` for text), so the chart
    follows light and dark mode.
  * The scale is fixed (0 to 9, what the scorer answers) and not fitted to the data, so the bar's height means the same in every room and
    a quiet stretch looks quiet. A score outside it is drawn at the edge.
  * A gap in the bar (a sentence judged before any bar existed) breaks the line instead of joining across it.
"""
from typing import Mapping, Optional, Sequence

from pom.monitor.score_trace import TracePoint

DEFAULT_SECONDS = 1800.0
WIDTH = 720
HEIGHT = 150
LEFT, RIGHT, TOP, BOTTOM = 24, 6, 8, 18
SCORE_MAX = 9.0
GRID_AT = (0, 3, 6, 9)
TICK_EVERY = 600.0                       # a label on the time axis every ten minutes
MINOR_EVERY = 300.0                      # and a faint vertical line every five


def _x(at: float, t_end: float, seconds: float, plot_width: float) -> float:
    return LEFT + plot_width * (1.0 - (t_end - at) / seconds)


def _y(score: float, plot_height: float) -> float:
    clamped = min(max(score, 0.0), SCORE_MAX)
    return TOP + plot_height * (1.0 - clamped / SCORE_MAX)


def render_score_chart(points: Sequence[TracePoint], trigger_ids: Optional[Mapping[float, int]] = None,
                       seconds: float = DEFAULT_SECONDS, width: int = WIDTH, height: int = HEIGHT) -> str:
    """The chart for these points as an `<svg>` string.

    Args:
        points: The ScoreTrace's points, oldest first.
        trigger_ids: The number of each trigger's record by the audio time it was heard, for linking its dot (a trigger not in
            it is drawn without a link, for example one whose portent is still being written).
        seconds: How much audio time to show, back from the newest point.
        width, height: The size of the drawing's own coordinate space (it scales to the width of the page).
    """
    trigger_ids = trigger_ids or {}
    plot_width, plot_height = width - LEFT - RIGHT, height - TOP - BOTTOM
    head = f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" xmlns="http://www.w3.org/2000/svg">'
    if not points:
        return (f'{head}<title>No scores yet.</title><text class="key" x="{width / 2:.1f}" y="{height / 2:.1f}" '
                f'text-anchor="middle">No scores yet.</text></svg>')
    t_end = points[-1].at
    shown = [point for point in points if point.at >= t_end - seconds]
    triggers = [point for point in shown if point.triggered]
    parts = [head, f"<title>Scores and the bar over the last {seconds / 60:g} minutes: {len(shown)} sentences, "
                   f"{len(triggers)} triggers.</title>"]
    for value in GRID_AT:
        y = _y(value, plot_height)
        parts.append(f'<line class="grid" x1="{LEFT}" x2="{width - RIGHT}" y1="{y:.1f}" y2="{y:.1f}"/>'
                     f'<text class="key" x="{LEFT - 4}" y="{y + 3:.1f}" text-anchor="end">{value}</text>')
    minor = 0
    while minor * MINOR_EVERY <= seconds:
        x = _x(t_end - minor * MINOR_EVERY, t_end, seconds, plot_width)
        parts.append(f'<line class="vgrid" x1="{x:.1f}" x2="{x:.1f}" y1="{TOP}" y2="{TOP + plot_height:.1f}"/>')
        minor += 1
    tick = 0
    while tick * TICK_EVERY <= seconds:
        x = _x(t_end - tick * TICK_EVERY, t_end, seconds, plot_width)
        label = "now" if tick == 0 else f"-{tick * TICK_EVERY / 60:g} min"
        parts.append(f'<text class="key" x="{x:.1f}" y="{height - 4}" text-anchor="{"end" if tick == 0 else "middle"}">{label}</text>')
        tick += 1
    for point in shown:
        if not point.triggered:
            parts.append(f'<circle class="dot" cx="{_x(point.at, t_end, seconds, plot_width):.1f}" cy="{_y(point.score, plot_height):.1f}" r="1.8"/>')
    path, previous_bar = [], False
    for point in shown:
        if point.bar is None:
            previous_bar = False
            continue
        x, y = _x(point.at, t_end, seconds, plot_width), _y(point.bar, plot_height)
        path.append(f"V{y:.1f} H{x:.1f}" if previous_bar else f"M{x:.1f} {y:.1f}")
        previous_bar = True
    if path:
        parts.append(f'<path class="bar" d="{" ".join(path)}"/>')
    for point in triggers:
        circle = (f'<circle class="hit" cx="{_x(point.at, t_end, seconds, plot_width):.1f}" cy="{_y(point.score, plot_height):.1f}" r="4">'
                  f'<title>score {point.score:.2f}'
                  f'{"" if point.bar is None else f", bar {point.bar:.2f}"}</title></circle>')
        record = trigger_ids.get(point.at)
        parts.append(f'<a href="/trigger/{int(record)}">{circle}</a>' if record is not None else circle)
    parts.append("</svg>")
    return "".join(parts)
