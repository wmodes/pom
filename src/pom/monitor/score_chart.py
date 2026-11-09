"""The score chart: the room's scores and the bar riding above them, drawn as a small inline SVG for the monitoring site.

`render_score_chart(points, qualifier_ids, look)` takes the points of a ChartNotebook and returns one `<svg>` element, the width of the text:

  * every attempt is a small dot (black on the page), on a fixed scale from 0 to `score_max` with gridlines at `score_gridlines`, and vertical
    gridlines every `gridline_every_minutes`, so it reads like graph paper;
  * the bar each attempt was judged against is a line (red on the page) that steps from one attempt to the next, so it rises and
    falls with the room;
  * each qualifier is a larger orange dot, and where its record is known (`qualifier_ids`, by the audio time it was heard) the dot is a link
    to that qualifier's page, with the score and the bar as its tooltip.
  * the time axis runs back from now over the last `window_minutes`: "now" at the right, and on the left one note, "Intervals at approximately
    5 min" (the `gridline_every_minutes`), for the vertical lines;
  * a silence longer than `longest_silence_minutes` is drawn that wide, like a broken part in an engineering drawing: its first minutes at true
    scale, then a cut (the white gap, with its real length written in it: "12 min silence"), then its latest minutes at true scale. What is cut
    out is not drawn at all, so a quiet hour does not push everything else off the chart, and what is drawn on either side is real, never a squeeze.
    The `window_minutes` are drawn minutes, silences already shortened.

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
  * A gap in the bar (an attempt judged before any bar existed) breaks the line instead of joining across it.
"""
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from pom.monitor.chart_notebook import NotebookEntry
from pom.monitor.silence_compressor import SilenceCompressor, SilenceShape


@dataclass(frozen=True)
class ChartLook:
    """The numbers that shape the chart, as fallbacks; `config.chart` has the same fields and is what the Printer passes. See `[chart]` in the config."""
    window_minutes: float = 30.0
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
    longest_silence_minutes: float = 5.0
    break_mark_size: float = 6.0
    break_gap_width: float = 64.0


def silence_shape(look) -> SilenceShape:
    """How the chart draws a silence, from its settings: the longest one, `longest_silence_minutes`, with a cut `break_gap_width` (drawing units) wide at the
    chart's scale, and no wider than a whole silence. The one place that is worked out: the chart, the notebook and the tick writer are all given this."""
    plot_width = look.drawing_width - look.margin_left - look.margin_right
    longest = look.longest_silence_minutes * 60.0
    return SilenceShape(longest, look.break_gap_width / plot_width * look.window_minutes * 60.0)                  # the shape holds the cut to a whole silence


def _silence_lines(seconds: float) -> tuple:
    """How long a silence was, as the two lines written in its gap: ("12 min", "silence"), ("1 hr 5 min", "silence"), ("2 hrs 0 min", "silence")."""
    hours, minutes = divmod(int(round(seconds / 60.0)), 60)
    return (f"{minutes} min" if hours == 0 else f"{hours} {'hr' if hours == 1 else 'hrs'} {minutes} min"), "silence"


def _x(at: float, t_end: float, seconds: float, plot_width: float, left: float) -> float:
    return left + plot_width * (1.0 - (t_end - at) / seconds)


def _y(score: float, plot_height: float, top: float, score_max: float) -> float:
    clamped = min(max(score, 0.0), score_max)
    return top + plot_height * (1.0 - clamped / score_max)


def render_score_chart(points: Sequence[NotebookEntry], qualifier_ids: Optional[Mapping[float, int]] = None, look=None,
                       ticks: Sequence = (), now: Optional[float] = None, bar_now: Optional[float] = None, began_at: Optional[float] = None) -> str:
    """The chart for these points as an `<svg>` string.

    Args:
        points: The ChartNotebook's points, oldest first.
        qualifier_ids: The number of each qualifier's record by the audio time it was heard, for linking its dot (a qualifier not in
            it is drawn without a link, for example one whose portent is still being written).
        look: The `[chart]` settings (anything with the fields of `ChartLook`); None means the fallbacks.
        ticks: The anchor's ticks to draw, each as (time, anchor score, the bar as it stood then). They are grey dots, and the bar line
            passes through the bar they carry, so it slides down through a silence.
        now: The stream time at the chart's right edge. Without it the edge is the newest attempt, and the chart stands still in silence.
        bar_now: The bar at `now`, so the line reaches the right edge.
        began_at: The stream time at which listening began, if the points reach back that far: a quiet start is a silence too. None (the default)
            means silences are counted only between attempts and from the newest one to now.
    """
    look = look if look is not None else ChartLook()
    qualifier_ids = qualifier_ids or {}
    seconds = look.window_minutes * 60.0
    width, height = look.drawing_width, look.drawing_height
    left, right, top, bottom = look.margin_left, look.margin_right, look.margin_top, look.margin_bottom
    plot_width, plot_height = width - left - right, height - top - bottom
    head = f'<svg class="chart" viewBox="0 0 {width:g} {height:g}" role="img" xmlns="http://www.w3.org/2000/svg">'
    if not points and not ticks and now is None:
        return (f'{head}<title>No scores yet.</title><text class="key" x="{width / 2:.1f}" y="{height / 2:.1f}" '
                f'text-anchor="middle">No scores yet.</text></svg>')
    t_end = now if now is not None else points[-1].at
    squeeze = SilenceCompressor([point.at for point in points], t_end, silence_shape(look), began_at)
    edge = squeeze.drawn(t_end)                                                    # the right edge, in drawn time
    def drawn_x(at: float) -> float:
        return _x(squeeze.drawn(at), edge, seconds, plot_width, left)
    def on_chart(at: float) -> bool:
        return edge - seconds <= squeeze.drawn(at) <= edge
    shown = [point for point in points if on_chart(point.at)]
    shown_ticks = [tick for tick in ticks if on_chart(tick[0])]
    qualifiers = [point for point in shown if point.qualified]
    parts = [head, f"<title>Scores and the bar over the last {look.window_minutes:g} minutes: {len(shown)} attempts, "
                   f"{len(qualifiers)} qualifiers.</title>"]
    for value in look.score_gridlines:
        y = _y(value, plot_height, top, look.score_max)
        parts.append(f'<line class="grid" x1="{left:g}" x2="{width - right:g}" y1="{y:.1f}" y2="{y:.1f}"/>'
                     f'<text class="key" x="{left - 4:g}" y="{y + 3:.1f}" text-anchor="end">{value:g}</text>')
    minor_every = look.gridline_every_minutes * 60.0
    minor = 0
    while minor * minor_every <= seconds:
        x = _x(edge - minor * minor_every, edge, seconds, plot_width, left)
        if not squeeze.within(edge - minor * minor_every):
            parts.append(f'<line class="vgrid" x1="{x:.1f}" x2="{x:.1f}" y1="{top:g}" y2="{top + plot_height:.1f}"/>')
        minor += 1
    # the time axis: one note on the left saying what the vertical lines are, and "now" at the right edge
    parts.append(f'<text class="key" x="{left:g}" y="{height - 4:g}" text-anchor="start">Intervals at approximately {look.gridline_every_minutes:g} min</text>')
    parts.append(f'<text class="key" x="{width - right:g}" y="{height - 4:g}" text-anchor="end">now</text>')
    for at, score, _ in shown_ticks:
        parts.append(f'<circle class="tick" cx="{drawn_x(at):.1f}" '
                     f'cy="{_y(score, plot_height, top, look.score_max):.1f}" r="{look.attempt_dot_radius:g}"/>')
    for point in shown:
        if not point.qualified:
            parts.append(f'<circle class="dot" cx="{drawn_x(point.at):.1f}" '
                         f'cy="{_y(point.score, plot_height, top, look.score_max):.1f}" r="{look.attempt_dot_radius:g}"/>')
    path, previous_bar = [], False
    levels = [(point.at, point.bar) for point in shown] + [(tick[0], tick[2]) for tick in shown_ticks]
    if now is not None and bar_now is not None:
        levels.append((now, bar_now))
    for at, bar in sorted(levels, key=lambda level: level[0]):
        if bar is None:
            previous_bar = False
            continue
        x, y = drawn_x(at), _y(bar, plot_height, top, look.score_max)
        path.append(f"V{y:.1f} H{x:.1f}" if previous_bar else f"M{x:.1f} {y:.1f}")
        previous_bar = True
    if path:
        parts.append(f'<path class="bar" stroke-width="{look.bar_line_width:g}" d="{" ".join(path)}"/>')
    for point in qualifiers:
        circle = (f'<circle class="hit" cx="{drawn_x(point.at):.1f}" '
                  f'cy="{_y(point.score, plot_height, top, look.score_max):.1f}" r="{look.qualifier_dot_radius:g}">'
                  f'<title>score {point.score:.2f}'
                  f'{"" if point.bar is None else f", bar {point.bar:.2f}"}</title></circle>')
        record = qualifier_ids.get(point.at)
        parts.append(f'<a href="/turn/{int(record)}">{circle}</a>' if record is not None else circle)
    for start, end in squeeze.silences:
        gap_from, gap_to = squeeze.gap_drawn(start)                                # the cut: what is left of the silence is drawn at true scale either side
        first, last = max(gap_from, edge - seconds), min(gap_to, edge)
        if first >= last:
            continue                                                               # a cut wholly off the chart
        x0, x1 = _x(first, edge, seconds, plot_width, left), _x(last, edge, seconds, plot_width, left)
        middle, size = top + plot_height / 2.0, look.break_mark_size
        centre = (x0 + x1) / 2.0
        # the gap is the page's own color, so it really breaks the bar line and anything else under it
        parts.append(f'<rect class="gap" x="{x0:.1f}" y="{top:g}" width="{x1 - x0:.1f}" height="{plot_height:.1f}"/>')
        for x, on_chart in ((x0, gap_from >= edge - seconds), (x1, gap_to <= edge)):          # a mark only on an edge of the cut that the window has not cut off
            if on_chart:
                parts.append(f'<path class="break" d="M{x:.1f} {top:g} V{middle - size:.1f} L{x + size / 2:.1f} {middle - size / 2:.1f} '
                             f'L{x - size / 2:.1f} {middle + size / 2:.1f} L{x:.1f} {middle + size:.1f} V{top + plot_height:.1f}"/>')
        first_line, second_line = _silence_lines(end - start)                      # centred in the part of the cut that is on the chart
        parts.append(f'<text class="key silence" text-anchor="middle"><tspan x="{centre:.1f}" y="{middle:.1f}" dy="-0.2em">{first_line}</tspan>'
                     f'<tspan x="{centre:.1f}" y="{middle:.1f}" dy="1.0em">{second_line}</tspan></text>')
    parts.append("</svg>")
    return "".join(parts)
