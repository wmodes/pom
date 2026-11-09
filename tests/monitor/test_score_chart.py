"""Tests for pom.monitor.score_chart: the scores and the bar as a small inline SVG, from numbers only."""
import re
import xml.etree.ElementTree as ET

import pytest

from pom.monitor.score_chart import ChartLook, SilenceCompressor, SilenceShape, _silence_lines, render_score_chart, silence_shape
from pom.monitor.chart_notebook import NotebookEntry

NS = "{http://www.w3.org/2000/svg}"
LOOK = ChartLook()                                       # the fallbacks, which are also what the shipped config says
WIDTH, HEIGHT = LOOK.drawing_width, LOOK.drawing_height
LEFT, RIGHT, TOP, BOTTOM = LOOK.margin_left, LOOK.margin_right, LOOK.margin_top, LOOK.margin_bottom
DEFAULT_SECONDS = LOOK.window_minutes * 60.0
PLOT_W, PLOT_H = WIDTH - LEFT - RIGHT, HEIGHT - TOP - BOTTOM
NEVER = 1e9                                              # a silence this long (in minutes) never happens: the chart is drawn in plain time


def render(points, qualifier_ids=None, seconds=None, width=None, height=None, ticks=(), now=None, bar_now=None, began_at=None, **look):
    """The chart with the default look, changed by keyword: `seconds` is the window in seconds, `width` and `height` the drawing's size, `ticks`,
    `now` and `bar_now` are what the monitor gives the chart, and any other keyword is a `[chart]` setting. Unless a test says otherwise no silence is
    long enough to be drawn short, so the first tests are about plain time."""
    look.setdefault("longest_silence_minutes", NEVER)
    if seconds is not None:
        look["window_minutes"] = seconds / 60.0
    if width is not None:
        look["drawing_width"] = width
    if height is not None:
        look["drawing_height"] = height
    return render_score_chart(points, qualifier_ids, ChartLook(**look), ticks=ticks, now=now, bar_now=bar_now, began_at=began_at)


def parse(svg):
    return ET.fromstring(svg)


def find(root, tag, css):
    return [e for e in root.iter(NS + tag) if e.get("class") == css]


def point(at, score=3.0, bar=5.0, qualified=False):
    return NotebookEntry(float(at), float(score), None if bar is None else float(bar), qualified)


def x_of(at, t_end, seconds=DEFAULT_SECONDS):
    return LEFT + PLOT_W * (1.0 - (t_end - at) / seconds)


def y_of(value):
    return TOP + PLOT_H * (1.0 - min(max(value, 0.0), 9.0) / 9.0)


class TestTheFrame:
    def test_it_is_one_svg_with_a_viewbox_and_a_role(self):
        root = parse(render([point(0)]))
        assert root.tag == NS + "svg" and root.get("viewBox") == f"0 0 {WIDTH:g} {HEIGHT:g}" and root.get("role") == "img"
        assert root.get("class") == "chart"

    def test_it_is_well_formed_xml_in_every_case(self):
        for pts in ([], [point(0)], [point(0, qualified=True), point(5, bar=None)]):
            parse(render(pts, {0.0: 4}))

    def test_a_size_can_be_given(self):
        assert parse(render([point(0)], width=400, height=100)).get("viewBox") == "0 0 400 100"

    def test_there_is_a_title_for_screen_readers_with_the_counts(self):
        title = parse(render([point(0), point(10, qualified=True), point(20)])).find(NS + "title").text
        assert title == "Scores and the bar over the last 30 minutes: 3 attempts, 1 qualifiers."

    def test_the_gridlines_are_at_zero_three_six_and_nine(self):
        root = parse(render([point(0)]))
        lines = find(root, "line", "grid")
        assert sorted(float(line.get("y1")) for line in lines) == pytest.approx(sorted(y_of(v) for v in (0, 3, 6, 9)), abs=0.06)
        labels = [t.text for t in find(root, "text", "key") if t.text in {"0", "3", "6", "9"}]
        assert sorted(labels) == ["0", "3", "6", "9"]

    def test_the_gridlines_span_the_plot(self):
        line = find(parse(render([point(0)])), "line", "grid")[0]
        assert (float(line.get("x1")), float(line.get("x2"))) == (LEFT, WIDTH - RIGHT)


class TestNoScoresYet:
    def test_it_says_so(self):
        root = parse(render([]))
        assert root.find(NS + "title").text == "No scores yet."
        assert [t.text for t in root.iter(NS + "text")] == ["No scores yet."]

    def test_it_draws_no_dots_or_lines(self):
        root = parse(render([]))
        assert not list(root.iter(NS + "circle")) and not list(root.iter(NS + "path")) and not list(root.iter(NS + "line"))


class TestTheVerticalGrid:
    def lines(self, seconds=1800, **kw):
        return find(parse(render([point(0), point(seconds)], seconds=seconds, **kw)), "line", "vgrid")

    def test_there_is_a_vertical_line_every_five_minutes_back_from_now(self):
        lines = self.lines(1800)
        xs = sorted(float(line.get("x1")) for line in lines)
        assert len(lines) == 7
        assert xs == pytest.approx([x_of(1800 - k * 300, 1800) for k in range(6, -1, -1)], abs=0.06)

    def test_the_lines_are_vertical_and_span_the_plot(self):
        for line in self.lines(1800):
            assert line.get("x1") == line.get("x2")
            assert float(line.get("y1")) == TOP and float(line.get("y2")) == pytest.approx(TOP + PLOT_H, abs=0.06)

    def test_the_first_is_at_now_and_the_last_at_the_start_of_the_window(self):
        xs = sorted(float(line.get("x1")) for line in self.lines(1800))
        assert xs[-1] == pytest.approx(WIDTH - RIGHT, abs=0.06) and xs[0] == pytest.approx(LEFT, abs=0.06)

    def test_a_shorter_window_has_fewer_lines(self):
        assert len(self.lines(900)) == 4 and len(self.lines(300)) == 2

    def test_the_time_axis_does_not_label_each_line(self):
        root = parse(render([point(0), point(1800)]))
        labels = [t.text for t in find(root, "text", "key") if t.text and ("min" in t.text or t.text == "now")]
        assert len(find(root, "line", "vgrid")) == 7 and labels == ["Intervals at approximately 5 min", "now"]

    def test_the_horizontal_grid_is_still_the_four_lines(self):
        assert len(find(parse(render([point(0), point(1800)])), "line", "grid")) == 4

    def test_an_empty_chart_has_no_lines(self):
        assert not find(parse(render([])), "line", "vgrid")


class TestTheDots:
    def test_each_attempt_is_a_dot_at_its_score_and_time(self):
        root = parse(render([point(0, score=6.0), point(900, score=2.0)]))
        dots = find(root, "circle", "dot")
        assert len(dots) == 2
        assert [float(d.get("cx")) for d in dots] == pytest.approx([x_of(0, 900), x_of(900, 900)], abs=0.06)
        assert [float(d.get("cy")) for d in dots] == pytest.approx([y_of(6.0), y_of(2.0)], abs=0.06)

    def test_the_newest_point_is_at_the_right_edge_and_the_window_start_at_the_left(self):
        dots = find(parse(render([point(0), point(1800)])), "circle", "dot")
        assert float(dots[0].get("cx")) == pytest.approx(LEFT, abs=0.06) and float(dots[1].get("cx")) == pytest.approx(WIDTH - RIGHT, abs=0.06)

    def test_a_score_of_nine_is_at_the_top_and_zero_at_the_bottom(self):
        dots = find(parse(render([point(0, score=9.0), point(1, score=0.0)])), "circle", "dot")
        assert float(dots[0].get("cy")) == pytest.approx(TOP, abs=0.06) and float(dots[1].get("cy")) == pytest.approx(TOP + PLOT_H, abs=0.06)

    def test_a_score_off_the_scale_is_drawn_at_the_edge(self):
        dots = find(parse(render([point(0, score=12.0), point(1, score=-3.0)])), "circle", "dot")
        assert float(dots[0].get("cy")) == pytest.approx(TOP, abs=0.06) and float(dots[1].get("cy")) == pytest.approx(TOP + PLOT_H, abs=0.06)

    def test_points_older_than_the_window_are_left_out(self):
        root = parse(render([point(0), point(2000), point(2100)], seconds=1800))
        assert len(find(root, "circle", "dot")) == 2

    def test_a_shorter_window_stretches_the_points_out(self):
        pts = [point(0), point(300)]
        wide = parse(render(pts, seconds=1800))
        narrow = parse(render(pts, seconds=300))
        gap = lambda r: float(find(r, "circle", "dot")[1].get("cx")) - float(find(r, "circle", "dot")[0].get("cx"))
        assert gap(narrow) > gap(wide) * 5

    def test_a_plain_dot_is_small_and_a_qualifier_dot_is_clearly_bigger(self):
        root = parse(render([point(0), point(1, qualified=True)]))
        dot, hit = float(find(root, "circle", "dot")[0].get("r")), float(find(root, "circle", "hit")[0].get("r"))
        assert dot < 3 and hit >= 2 * dot

    def test_a_qualifier_is_not_also_drawn_as_a_plain_dot(self):
        root = parse(render([point(0), point(1, qualified=True)]))
        assert len(find(root, "circle", "dot")) == 1 and len(find(root, "circle", "hit")) == 1


class TestTheBar:
    def path(self, pts):
        paths = find(parse(render(pts)), "path", "bar")
        return paths[0].get("d") if paths else None

    def test_it_starts_at_the_first_bar_and_runs_straight_to_each_next_one(self):
        d = self.path([point(0, bar=5.0), point(900, bar=7.0), point(1800, bar=6.0)])
        x0, x1, x2 = x_of(0, 1800), x_of(900, 1800), x_of(1800, 1800)
        expected = f"M{x0:.1f} {y_of(5.0):.1f} L{x1:.1f} {y_of(7.0):.1f} L{x2:.1f} {y_of(6.0):.1f}"
        assert d == expected

    def test_a_single_point_gives_a_line_start_only(self):
        d = self.path([point(0, bar=5.0)])
        assert d == f"M{x_of(0, 0):.1f} {y_of(5.0):.1f}"

    def test_a_gap_with_no_bar_breaks_the_line(self):
        d = self.path([point(0, bar=5.0), point(100, bar=None), point(200, bar=6.0), point(300, bar=6.5)])
        assert d.count("M") == 2

    def test_points_with_no_bar_at_all_draw_no_line(self):
        assert self.path([point(0, bar=None), point(10, bar=None)]) is None

    def test_a_bar_off_the_scale_is_drawn_at_the_edge(self):
        d = self.path([point(0, bar=20.0)])
        assert f" {y_of(9.0):.1f}" in d

    def test_the_line_has_the_bar_class_and_no_fill(self):
        assert len(find(parse(render([point(0)])), "path", "bar")) == 1


class TestTheQualifiers:
    def test_a_qualifier_is_a_larger_dot_at_its_score(self):
        hit = find(parse(render([point(0), point(900, score=7.5, qualified=True)])), "circle", "hit")[0]
        assert float(hit.get("r")) > 3 and float(hit.get("cy")) == pytest.approx(y_of(7.5), abs=0.06)

    def test_it_links_to_its_page_when_the_record_is_known(self):
        svg = render([point(0), point(900, qualified=True)], {900.0: 17})
        links = [a for a in parse(svg).iter(NS + "a")]
        assert len(links) == 1 and links[0].get("href") == "/turn/17" and links[0].find(NS + "circle").get("class") == "hit"

    def test_it_has_no_link_when_the_record_is_not_known_yet(self):
        svg = render([point(0), point(900, qualified=True)], {})
        root = parse(svg)
        assert not list(root.iter(NS + "a")) and len(find(root, "circle", "hit")) == 1

    def test_only_the_qualifier_with_a_record_is_linked(self):
        pts = [point(0, qualified=True), point(100, qualified=True)]
        root = parse(render(pts, {100.0: 5}))
        assert [a.get("href") for a in root.iter(NS + "a")] == ["/turn/5"] and len(find(root, "circle", "hit")) == 2

    def test_the_tooltip_has_the_score_and_the_bar(self):
        hit = find(parse(render([point(0, score=6.987, bar=6.5, qualified=True)])), "circle", "hit")[0]
        assert hit.find(NS + "title").text == "score 6.99, bar 6.50"

    def test_the_tooltip_without_a_bar_has_only_the_score(self):
        hit = find(parse(render([point(0, score=6.987, bar=None, qualified=True)])), "circle", "hit")[0]
        assert hit.find(NS + "title").text == "score 6.99"

    def test_a_qualifier_outside_the_window_is_left_out(self):
        root = parse(render([point(0, qualified=True), point(5000)], {0.0: 1}))
        assert not find(root, "circle", "hit") and not list(root.iter(NS + "a"))


class TestTheTimeAxis:
    """One note at the left says what the vertical lines are, and "now" sits at the right edge."""

    def texts(self, **kw):
        return {t.text: t for t in find(parse(render([point(0), point(1800)], **kw)), "text", "key") if t.text and ("min" in t.text or t.text == "now")}

    def test_it_is_one_note_and_now(self):
        assert list(self.texts()) == ["Intervals at approximately 5 min", "now"]

    def test_the_old_minute_labels_are_gone(self):
        assert not [t for t in self.texts() if re.fullmatch(r"-\d+ min", t)]

    def test_the_note_is_left_justified_at_the_plot(self):
        note = self.texts()["Intervals at approximately 5 min"]
        assert note.get("text-anchor") == "start" and float(note.get("x")) == LEFT

    def test_now_is_right_aligned_at_the_right_edge(self):
        now = self.texts()["now"]
        assert now.get("text-anchor") == "end" and float(now.get("x")) == WIDTH - RIGHT

    def test_both_sit_in_the_bottom_margin(self):
        for text in self.texts().values():
            assert float(text.get("y")) > TOP + PLOT_H

    @pytest.mark.parametrize("every, said", [(10, "10"), (2.5, "2.5"), (1, "1")])
    def test_the_note_says_the_gridline_spacing_from_the_config(self, every, said):
        assert f"Intervals at approximately {said} min" in self.texts(gridline_every_minutes=every)

    def test_the_note_is_there_with_no_scores_yet_only_when_the_chart_moves(self):
        assert "Intervals at approximately 5 min" not in [t.text for t in parse(render([])).iter(NS + "text")]
        assert "Intervals at approximately 5 min" in [t.text for t in parse(render([], now=100)).iter(NS + "text")]


class TestTheTicks:
    """The anchor's ticks are grey dots at the anchor score, and the bar line passes through the bar each one carries."""

    def ticked(self, **kw):
        return parse(render([point(0, bar=6.5)], ticks=[(60.0, 5.85, 6.4), (120.0, 5.85, 6.3)], now=180.0, bar_now=6.2, **kw))

    def test_each_tick_is_a_dot_of_its_own_class_at_the_anchor_score_and_its_time(self):
        ticks = find(self.ticked(), "circle", "tick")
        assert [float(t.get("cx")) for t in ticks] == pytest.approx([x_of(60, 180), x_of(120, 180)], abs=0.06)
        assert [float(t.get("cy")) for t in ticks] == pytest.approx([y_of(5.85)] * 2, abs=0.06)

    def test_a_tick_is_not_an_attempt(self):
        root = self.ticked()
        assert len(find(root, "circle", "dot")) == 1 and "1 attempts" in root.find(NS + "title").text

    def test_a_tick_is_drawn_the_size_of_an_attempt_dot(self):
        assert all(t.get("r") == "2.5" for t in find(self.ticked(attempt_dot_radius=2.5), "circle", "tick"))

    def test_the_bar_line_runs_through_the_bar_at_each_tick_to_the_bar_now(self):
        d = find(self.ticked(), "path", "bar")[0].get("d")
        xs = [x_of(t, 180) for t in (0, 60, 120, 180)]
        expected = f"M{xs[0]:.1f} {y_of(6.5):.1f} L{xs[1]:.1f} {y_of(6.4):.1f} L{xs[2]:.1f} {y_of(6.3):.1f} L{xs[3]:.1f} {y_of(6.2):.1f}"
        assert d == expected

    def test_without_a_bar_now_the_line_ends_at_the_last_sample(self):
        d = find(parse(render([point(0, bar=6.5)], ticks=[(60.0, 5.85, 6.4)], now=180.0)), "path", "bar")[0].get("d")
        assert d == f"M{x_of(0, 180):.1f} {y_of(6.5):.1f} L{x_of(60, 180):.1f} {y_of(6.4):.1f}"

    def test_an_attempt_and_a_tick_are_joined_in_time_order(self):
        d = find(parse(render([point(30, bar=6.0)], ticks=[(60.0, 5.85, 5.9), (0.0, 5.85, 6.1)], now=60.0)), "path", "bar")[0].get("d")
        assert [float(part[1:]) for part in d.split() if part[0] == "L"] == pytest.approx([x_of(30, 60), x_of(60, 60)], abs=0.06)

    def test_a_bar_now_with_no_now_is_ignored(self):
        with_it = render([point(0, bar=6.5)], bar_now=3.0)
        assert with_it == render([point(0, bar=6.5)])

    def test_a_now_with_no_bar_now_leaves_the_line_at_the_last_sample(self):
        d = find(parse(render([point(0, bar=6.5)], now=100.0)), "path", "bar")[0].get("d")
        assert d == f"M{x_of(0, 100):.1f} {y_of(6.5):.1f}"

    def test_a_tick_with_no_bar_breaks_the_line(self):
        d = find(parse(render([point(0, bar=6.5)], ticks=[(60.0, 5.85, None), (120.0, 5.85, 6.3)], now=180.0)), "path", "bar")[0].get("d")
        assert d.count("M") == 2

    def test_ticks_outside_the_window_are_left_out(self):
        root = parse(render([point(1900)], ticks=[(100.0, 5.85, 6.0), (1900.0, 5.85, 6.0), (2000.0, 5.85, 6.0)], now=2000.0))
        assert len(find(root, "circle", "tick")) == 2

    def test_a_tick_after_now_is_left_out(self):
        assert not find(parse(render([point(0)], ticks=[(500.0, 5.85, 6.0)], now=100.0)), "circle", "tick")

    def test_with_no_ticks_given_there_are_none(self):
        assert not find(parse(render([point(0), point(60)])), "circle", "tick")


class TestTheChartMovesInSilence:
    """`now` is the right edge, so the picture slides left as time passes with no attempt."""

    def test_the_right_edge_is_now_and_not_the_newest_attempt(self):
        dot = find(parse(render([point(100)], now=400.0)), "circle", "dot")[0]
        assert float(dot.get("cx")) == pytest.approx(x_of(100, 400), abs=0.06) and float(dot.get("cx")) < WIDTH - RIGHT

    def test_the_same_attempt_is_further_left_a_minute_later(self):
        before = float(find(parse(render([point(100)], now=400.0)), "circle", "dot")[0].get("cx"))
        after = float(find(parse(render([point(100)], now=460.0)), "circle", "dot")[0].get("cx"))
        assert after < before

    def test_an_attempt_slides_off_the_left_once_it_is_older_than_the_window(self):
        assert not find(parse(render([point(100)], now=100.0 + 1801.0)), "circle", "dot")

    def test_the_vertical_lines_slide_with_now(self):
        xs = lambda now: sorted(float(l.get("x1")) for l in find(parse(render([point(0)], now=now)), "line", "vgrid"))
        assert xs(1800.0)[-1] == pytest.approx(WIDTH - RIGHT, abs=0.06) and xs(1800.0) == xs(1800.0 + 300)      # a whole interval later it is the same grid

    def test_with_no_attempts_at_all_it_still_draws_the_ticks_and_the_bar(self):
        root = parse(render([], ticks=[(60.0, 5.85, 6.7), (120.0, 5.85, 6.6)], now=150.0, bar_now=6.55))
        assert root.find(NS + "title").text != "No scores yet." and len(find(root, "circle", "tick")) == 2 and find(root, "path", "bar")

    def test_with_nothing_given_and_no_now_it_is_still_the_empty_chart(self):
        assert parse(render([])).find(NS + "title").text == "No scores yet."


class TestTheSilenceCompressor:
    def test_a_gap_between_attempts_longer_than_the_limit_is_a_silence(self):
        assert SilenceCompressor([0, 3000], 3000, SilenceShape(300)).silences == [(0, 3000)]

    def test_a_gap_exactly_the_limit_is_not(self):
        assert SilenceCompressor([0, 300], 300, SilenceShape(300)).silences == []

    def test_the_stretch_up_to_now_counts(self):
        assert SilenceCompressor([0, 60], 3660, SilenceShape(300)).silences == [(60, 3660)]

    def test_no_attempts_means_no_silence(self):
        assert SilenceCompressor([], 5000, SilenceShape(300)).silences == []

    def test_time_inside_a_silence_is_squeezed_evenly(self):
        c = SilenceCompressor([0, 3000], 3000, SilenceShape(300))
        assert (c.drawn(0), c.drawn(1500), c.drawn(3000)) == pytest.approx((0, 150, 300))

    def test_time_after_a_silence_is_shifted_by_what_was_cut(self):
        c = SilenceCompressor([0, 3000, 3100], 3100, SilenceShape(300))
        assert c.drawn(3100) == pytest.approx(400)

    def test_each_silence_is_cut_in_turn(self):
        c = SilenceCompressor([0, 1000, 3000], 3000, SilenceShape(300))
        assert (c.drawn(1000), c.drawn(3000)) == pytest.approx((300, 600))

    def test_time_before_a_silence_is_not_moved(self):
        c = SilenceCompressor([0, 100, 3100], 3100, SilenceShape(300))
        assert (c.drawn(0), c.drawn(50), c.drawn(100)) == (0, 50, 100)

    def test_within_is_true_only_strictly_inside_a_drawn_silence(self):
        c = SilenceCompressor([0, 3000], 3000, SilenceShape(300))
        assert c.within(150) and not c.within(0) and not c.within(300) and not c.within(450)

    def test_with_a_cut_the_first_and_last_minutes_are_at_true_scale(self):
        c = SilenceCompressor([0, 3000], 3000, SilenceShape(300, 100))                         # drawn: 100 of silence, a cut 100 wide, 100 of silence
        assert (c.drawn(0), c.drawn(50), c.drawn(100)) == pytest.approx((0, 50, 100))
        assert (c.drawn(2900), c.drawn(2950), c.drawn(3000)) == pytest.approx((200, 250, 300))

    def test_with_a_cut_what_is_between_is_squeezed_into_the_cut(self):
        c = SilenceCompressor([0, 3000], 3000, SilenceShape(300, 100))
        assert c.drawn(1550) == pytest.approx(100 + 1450 * 100 / 2800)
        assert 100 < c.drawn(101) < c.drawn(2899) < 200

    def test_the_cut_is_where_the_first_minutes_end_and_as_wide_as_asked(self):
        c = SilenceCompressor([0, 3000], 3000, SilenceShape(300, 100))
        assert c.gap_drawn(0) == pytest.approx((100, 200)) and c.edge == pytest.approx(100)

    def test_the_cut_of_a_later_silence_is_after_what_the_earlier_one_cut(self):
        c = SilenceCompressor([0, 3000, 3100, 7000], 7000, SilenceShape(300, 100))
        assert c.gap_drawn(3100) == pytest.approx((c.drawn(3100) + 100, c.drawn(3100) + 200))

    def test_time_after_a_silence_with_a_cut_is_shifted_by_what_was_cut(self):
        c = SilenceCompressor([0, 3000, 3100], 3100, SilenceShape(300, 100))
        assert c.drawn(3100) == pytest.approx(400)

    def test_with_no_gap_given_the_whole_silence_is_the_cut(self):
        c = SilenceCompressor([0, 3000], 3000, SilenceShape(300))
        assert c.gap == 300 and c.edge == 0

    @pytest.mark.parametrize("gap", [300, 500, 1e9])
    def test_a_gap_as_wide_as_the_limit_or_wider_leaves_no_true_scale_sides(self, gap):
        c = SilenceCompressor([0, 3000], 3000, SilenceShape(300, gap))
        assert c.edge == 0 and c.drawn(1500) == pytest.approx(150)

    def test_a_negative_gap_is_none_at_all(self):
        c = SilenceCompressor([0, 3000], 3000, SilenceShape(300, -5))
        assert c.gap == 0 and c.edge == 150 and c.drawn(3000) == pytest.approx(300)

    def test_a_silence_just_over_the_limit_has_a_small_cut(self):
        c = SilenceCompressor([0, 310], 310, SilenceShape(300, 100))
        assert c.drawn(310) == pytest.approx(300) and c.drawn(100) == pytest.approx(100) and c.drawn(210) == pytest.approx(200)

    def test_a_clock_before_the_newest_attempt_makes_no_silence_of_it(self):
        assert SilenceCompressor([0, 100], 50, SilenceShape(300)).silences == []


class TestAQuietStartIsASilenceToo:
    """With the stream's start known, the stretch from the start to the first attempt (or to now, if none has come) is a silence."""

    def test_the_start_is_an_edge_of_the_first_silence(self):
        assert SilenceCompressor([3000], 3000, SilenceShape(300), began_at=0.0).silences == [(0.0, 3000)]

    def test_without_a_start_the_first_attempt_has_no_silence_before_it(self):
        assert SilenceCompressor([3000], 3000, SilenceShape(300)).silences == []

    def test_with_no_attempts_the_whole_stretch_to_now_is_one(self):
        assert SilenceCompressor([], 3000, SilenceShape(300), began_at=0.0).silences == [(0.0, 3000)]

    def test_a_quiet_start_no_longer_than_the_limit_is_none(self):
        assert SilenceCompressor([300], 300, SilenceShape(300), began_at=0.0).silences == []

    def test_a_start_after_which_attempts_come_at_once_has_none(self):
        assert SilenceCompressor([10, 20], 30, SilenceShape(300), began_at=0.0).silences == []

    def test_it_is_squeezed_like_any_other(self):
        c = SilenceCompressor([3000], 3000, SilenceShape(300), began_at=0.0)
        assert (c.drawn(0), c.drawn(3000)) == pytest.approx((0, 300))

    def test_a_quiet_start_is_drawn_with_the_gap_and_its_words(self):
        root = parse(render([point(3000)], longest_silence_minutes=5, began_at=0.0))
        assert len(find(root, "rect", "gap")) == 1
        assert [s.text for s in find(root, "text", "key silence")[0].iter(NS + "tspan")] == ["50 min", "silence"]

    def test_with_no_attempt_at_all_the_quiet_since_the_start_is_drawn(self):
        root = parse(render([], longest_silence_minutes=5, began_at=0.0, now=3000.0, ticks=[(60.0, 5.85, 6.7), (2940.0, 5.85, 6.0)], bar_now=5.9))
        assert len(find(root, "rect", "gap")) == 1 and len(find(root, "path", "break")) == 2
        assert [s.text for s in find(root, "text", "key silence")[0].iter(NS + "tspan")] == ["50 min", "silence"]

    def test_the_ticks_and_the_bar_show_on_either_side_of_the_gap(self):
        root = parse(render([], longest_silence_minutes=5, began_at=0.0, now=3000.0, ticks=[(60.0, 5.85, 6.7), (2940.0, 5.85, 6.0)], bar_now=5.9))
        assert len(find(root, "circle", "tick")) == 2 and find(root, "path", "bar")

    def test_a_start_that_is_not_given_leaves_the_chart_as_it_was(self):
        assert render([point(3000)], longest_silence_minutes=5) == render([point(3000)], longest_silence_minutes=5, began_at=None)
        assert not find(parse(render([point(3000)], longest_silence_minutes=5)), "rect", "gap")

    def test_a_short_quiet_start_draws_no_gap(self):
        assert not find(parse(render([point(200)], longest_silence_minutes=5, began_at=0.0)), "rect", "gap")


class TestHowLongASilenceWas:
    @pytest.mark.parametrize("seconds, lines", [
        (601, ("10 min", "silence")), (330, ("6 min", "silence")), (3569, ("59 min", "silence")), (3570, ("1 hr 0 min", "silence")),
        (3600, ("1 hr 0 min", "silence")), (3900, ("1 hr 5 min", "silence")), (7500, ("2 hrs 5 min", "silence")), (36000, ("10 hrs 0 min", "silence"))])
    def test_it_reads_in_minutes_then_hours_and_minutes(self, seconds, lines):
        assert _silence_lines(seconds) == lines


LOOK_5 = ChartLook(longest_silence_minutes=5)


class TestASilenceIsDrawnShort:
    """A silence longer than the limit is five minutes wide, with a clear gap between two break marks and its length written in the gap."""

    def drawn(self, points=None, **kw):
        kw.setdefault("longest_silence_minutes", 5)
        return parse(render(points if points is not None else [point(0), point(3000)], **kw))

    def test_the_silence_is_drawn_the_limit_wide(self):
        dots = find(self.drawn(), "circle", "dot")
        assert [float(d.get("cx")) for d in dots] == pytest.approx([x_of(0, 300), x_of(300, 300)], abs=0.06)

    def test_a_gap_that_is_not_longer_than_the_limit_is_left_alone(self):
        root = self.drawn([point(0), point(300)])
        assert not find(root, "rect", "gap") and not find(root, "path", "break")

    def test_the_gap_is_a_page_colored_rectangle_the_height_of_the_plot_centred_in_the_silence(self):
        gap = find(self.drawn(), "rect", "gap")[0]
        silence_left, silence_right = x_of(0, 300), x_of(300, 300)
        assert float(gap.get("y")) == TOP and float(gap.get("height")) == pytest.approx(PLOT_H, abs=0.06)
        assert float(gap.get("x")) + float(gap.get("width")) / 2 == pytest.approx((silence_left + silence_right) / 2, abs=0.06)
        assert float(gap.get("width")) == pytest.approx(LOOK.break_gap_width, abs=0.06)

    def test_the_gap_comes_after_the_bar_so_it_breaks_it(self):
        svg = render([point(0), point(3000)], longest_silence_minutes=5)
        assert svg.index('class="bar"') < svg.index('class="gap"') < svg.index('class="break"')

    def test_a_silence_narrower_than_the_gap_is_all_gap(self):
        gap = find(self.drawn(seconds=36000), "rect", "gap")[0]
        assert float(gap.get("width")) == pytest.approx(PLOT_W * 300 / 36000, abs=0.1)
        assert float(gap.get("width")) < LOOK.break_gap_width

    def test_there_is_a_break_mark_on_each_edge_of_the_gap_the_height_of_the_plot(self):
        root = self.drawn()
        gap = find(root, "rect", "gap")[0]
        marks = find(root, "path", "break")
        assert len(marks) == 2
        left, right = float(gap.get("x")), float(gap.get("x")) + float(gap.get("width"))
        assert [float(m.get("d").split()[0][1:]) for m in marks] == pytest.approx([left, right], abs=0.06)
        for mark in marks:
            d = mark.get("d")
            assert d.startswith(f"M{float(d.split()[0][1:]):.1f} {TOP:g} V") and d.endswith(f"V{TOP + PLOT_H:.1f}")

    def test_the_break_mark_zigzags_across_its_line(self):
        d = find(self.drawn(), "path", "break")[0].get("d")
        xs = [float(part[1:]) for part in d.replace("L", " L").split() if part[0] in "ML"]
        assert xs[1] > xs[0] > xs[2] and xs[3] == pytest.approx(xs[0], abs=0.06)

    def test_the_zigzag_is_exactly_the_shape_of_the_break_mark(self):
        root = self.drawn()
        gap = find(root, "rect", "gap")[0]
        left, size, middle = float(gap.get("x")), LOOK.break_mark_size, TOP + PLOT_H / 2
        expected = (f"M{left:.1f} {TOP:g} V{middle - size:.1f} L{left + size / 2:.1f} {middle - size / 2:.1f} "
                    f"L{left - size / 2:.1f} {middle + size / 2:.1f} L{left:.1f} {middle + size:.1f} V{TOP + PLOT_H:.1f}")
        assert find(root, "path", "break")[0].get("d") == expected

    def test_the_length_is_written_in_two_centred_lines_in_the_gap(self):
        root = self.drawn()
        text = find(root, "text", "key silence")[0]
        gap = find(root, "rect", "gap")[0]
        spans = list(text.iter(NS + "tspan"))
        assert [s.text for s in spans] == ["50 min", "silence"] and text.get("text-anchor") == "middle"
        centre = float(gap.get("x")) + float(gap.get("width")) / 2
        assert [float(s.get("x")) for s in spans] == pytest.approx([centre, centre], abs=0.06)
        assert float(spans[0].get("y")) == float(spans[1].get("y")) == pytest.approx(TOP + PLOT_H / 2, abs=0.06)
        assert [s.get("dy") for s in spans] == ["-0.2em", "1.0em"]                  # the first line just above the middle, the second below it

    def test_a_long_silence_says_hours_and_minutes(self):
        texts = [s.text for s in find(self.drawn([point(0), point(3900 + 300)]), "text", "key silence")[0].iter(NS + "tspan")]
        assert texts == ["1 hr 10 min", "silence"]

    def test_the_bar_line_runs_on_through_the_silence_on_both_sides_of_the_gap(self):
        d = find(self.drawn([point(0, bar=6.5), point(3000, bar=5.0)]), "path", "bar")[0].get("d")
        assert d == f"M{x_of(0, 300):.1f} {y_of(6.5):.1f} L{x_of(300, 300):.1f} {y_of(5.0):.1f}"

    def test_the_silence_up_to_now_is_drawn_too(self):
        root = self.drawn([point(0), point(60)], now=3660.0)
        assert [s.text for s in find(root, "text", "key silence")[0].iter(NS + "tspan")] == ["1 hr 0 min", "silence"]
        gap = find(root, "rect", "gap")[0]
        assert float(gap.get("x")) + float(gap.get("width")) < WIDTH - RIGHT

    def test_ticks_in_the_first_and_last_minutes_of_a_silence_are_drawn_at_true_scale(self):
        root = self.drawn([point(0), point(3000)], ticks=[(30.0, 5.85, 6.0), (2970.0, 5.85, 5.9)])
        xs = [float(t.get("cx")) for t in find(root, "circle", "tick")]
        assert xs == pytest.approx([x_of(30, 300), x_of(270, 300)], abs=0.06)              # 30 s after the start, and 30 s before the end

    def test_a_tick_in_the_cut_is_drawn_under_the_gap(self):
        root = self.drawn([point(0), point(3000)], ticks=[(1500.0, 5.85, 6.0)])
        gap = find(root, "rect", "gap")[0]
        x = float(find(root, "circle", "tick")[0].get("cx"))
        assert float(gap.get("x")) <= x <= float(gap.get("x")) + float(gap.get("width"))

    def test_the_gap_sits_where_the_first_minutes_end(self):
        gap = find(self.drawn([point(0), point(3000)]), "rect", "gap")[0]
        assert float(gap.get("x")) == pytest.approx(x_of(silence_shape(LOOK_5).edge_seconds, 300), abs=0.06)

    def test_a_long_silence_draws_the_same_width_of_it_whatever_its_length(self):
        short = self.drawn([point(0), point(3000)], ticks=[(30.0, 5.85, 6.0)])
        long = self.drawn([point(0), point(30000)], ticks=[(30.0, 5.85, 6.0)])
        assert float(find(short, "circle", "tick")[0].get("cx")) == pytest.approx(float(find(long, "circle", "tick")[0].get("cx")), abs=0.06)

    def test_no_vertical_line_falls_inside_a_silence(self):
        root = self.drawn(longest_silence_minutes=10)                                       # drawn 600 s wide: the line 300 s back from the edge is inside
        xs = [float(l.get("x1")) for l in find(root, "line", "vgrid")]
        assert len(xs) == 6 and not any(abs(x - x_of(300, 600)) < 0.06 for x in xs)
        assert sorted(xs) == pytest.approx(sorted(x_of(d, 600) for d in (600, 0, -300, -600, -900, -1200)), abs=0.06)     # counted back from the drawn edge

    def test_a_chart_with_a_silence_reaches_back_further_than_the_window(self):
        pts = [point(0), point(120), point(240), point(3840)]                               # 64 minutes in all, in a 30 minute window
        assert len(find(self.drawn(pts), "circle", "dot")) == 4
        assert len(find(parse(render(pts)), "circle", "dot")) == 1                          # in plain time only the newest is within 30 minutes

    def test_a_silence_cut_by_the_window_keeps_its_gap_with_a_mark_on_each_side(self):
        root = self.drawn(seconds=350, points=[point(0), point(3000), point(3100)])             # the silence is drawn 0 to 300, the window starts at 50
        assert len(find(root, "rect", "gap")) == 1 and len(find(root, "path", "break")) == 2

    def test_a_window_that_shows_only_the_end_of_a_silence_has_no_gap_because_the_cut_is_off_the_chart(self):
        root = self.drawn(seconds=55, points=[point(0), point(3000), point(3050)])
        assert not find(root, "rect", "gap") and not find(root, "path", "break")

    def test_a_window_that_cuts_through_the_gap_keeps_only_its_right_mark(self):
        root = self.drawn(seconds=200, points=[point(0), point(3000), point(3050)])             # the window starts inside the cut, which is drawn 141 to 159
        assert len(find(root, "rect", "gap")) == 1 and len(find(root, "path", "break")) == 1

    def test_a_silence_wholly_before_the_window_is_not_drawn(self):
        root = self.drawn(seconds=40, points=[point(0), point(3000), point(3050)])
        assert not find(root, "rect", "gap") and not find(root, "text", "key silence")

    def test_a_window_that_starts_just_where_a_silence_ends_shows_none_of_it(self):
        root = self.drawn(seconds=50, points=[point(0), point(3000), point(3050)])             # the silence is drawn 0 to 300, the window starts at 300
        assert not find(root, "rect", "gap")

    def test_two_silences_are_each_written_up(self):
        root = self.drawn([point(0), point(1000), point(2000)])
        assert [[s.text for s in t.iter(NS + "tspan")][0] for t in find(root, "text", "key silence")] == ["17 min", "17 min"]

    def test_the_words_and_numbers_are_the_chart_s_own(self):
        root = self.drawn([point(0, qualified=True), point(3000), point(3100)], qualifier_ids={0.0: 1})
        words = [t.text for t in root.iter(NS + "tspan")]
        assert words and all(re.fullmatch(r"\d+ (hrs?|min)( \d+ min)?|silence", word) for word in words)


class TestItIsNumbersOnly:
    def test_the_only_words_in_it_are_the_chart_s_own(self):
        root = parse(render([point(0, qualified=True), point(5), point(1500)], {0.0: 3}))
        texts = [t.text for t in root.iter(NS + "text")]
        assert texts and all(re.fullmatch(r"\d|now|Intervals at approximately [\d.]+ min", text) for text in texts)
        titles = [t.text for t in root.iter(NS + "title")]
        assert all(re.fullmatch(r"(Scores and the bar over the last \d+ minutes: \d+ attempts, \d+ qualifiers\.|score [\d.]+(, bar [\d.]+)?)", title)
                   for title in titles)

    def test_the_output_is_stable(self):
        pts = [point(i * 7, score=(i % 9), bar=5 + (i % 3) / 2, qualified=(i % 11 == 0)) for i in range(60)]
        assert render(pts, {0.0: 1}) == render(pts, {0.0: 1})

    def test_a_busy_half_hour_draws_in_a_reasonable_size(self):
        pts = [point(i * 4, score=(i % 9), bar=6.0) for i in range(450)]
        assert len(render(pts)) < 90_000


class TestEveryNumberIsASetting:
    """What the `[chart]` section sets is what the drawing does; the colors are the page's."""

    def test_the_window_sets_how_much_time_is_shown_and_what_the_title_says(self):
        svg = render([point(0), point(1200), point(1800)], window_minutes=10)
        root = parse(svg)
        assert len(find(root, "circle", "dot")) == 2 and "last 10 minutes" in root.find(NS + "title").text

    def test_the_gridline_spacing_sets_the_vertical_lines(self):
        assert len(find(parse(render([point(0), point(1800)], gridline_every_minutes=10)), "line", "vgrid")) == 4

    def test_the_score_max_sets_the_top_of_the_scale(self):
        dots = find(parse(render([point(0, score=10.0), point(1, score=0.0)], score_max=10)), "circle", "dot")
        assert float(dots[0].get("cy")) == pytest.approx(TOP, abs=0.06)

    def test_the_gridlines_are_the_ones_asked_for_and_labelled(self):
        root = parse(render([point(0)], score_gridlines=(0, 4.5, 9)))
        lines = find(root, "line", "grid")
        labels = [t.text for t in find(root, "text", "key") if t.text in ("0", "4.5", "9")]
        assert len(lines) == 3 and labels == ["0", "4.5", "9"]

    def test_the_gridlines_sit_where_the_score_max_puts_them(self):
        """With a scale to 10, the line for 5 is half way up the plot."""
        lines = find(parse(render([point(0)], score_max=10, score_gridlines=(0, 5, 10))), "line", "grid")
        ys = sorted(float(line.get("y1")) for line in lines)
        assert ys == pytest.approx([TOP, TOP + PLOT_H / 2, TOP + PLOT_H], abs=0.06)

    def test_the_drawing_size_and_margins_set_the_plot(self):
        root = parse(render([point(0), point(1800)], drawing_width=500, drawing_height=100, margin_left=40, margin_right=10, margin_top=5, margin_bottom=20))
        assert root.get("viewBox") == "0 0 500 100"
        xs = sorted(float(line.get("x1")) for line in find(root, "line", "vgrid"))
        assert xs[0] == pytest.approx(40, abs=0.06) and xs[-1] == pytest.approx(490, abs=0.06)

    def test_the_dot_radii_and_the_bar_width_are_in_the_drawing_not_the_page(self):
        root = parse(render([point(0), point(1, qualified=True)], attempt_dot_radius=2.5, qualifier_dot_radius=6, bar_line_width=3))
        assert find(root, "circle", "hit")[0].get("r") == "6" and find(root, "path", "bar")[0].get("stroke-width") == "3"
        dot = parse(render([point(0), point(1800, qualified=True)], attempt_dot_radius=2.5))
        assert find(dot, "circle", "dot")[0].get("r") == "2.5"

    def test_with_no_look_the_fallbacks_are_the_shipped_numbers(self):
        assert render_score_chart([point(0)]) == render_score_chart([point(0)], None, ChartLook())

    def test_the_longest_silence_sets_which_gaps_are_drawn_short(self):
        pts = [point(0), point(500)]                                                        # 500 s: a silence at 5 minutes, not at 10
        assert find(parse(render(pts, longest_silence_minutes=5)), "rect", "gap") and not find(parse(render(pts, longest_silence_minutes=10)), "rect", "gap")

    def test_the_longest_silence_sets_how_wide_one_is_drawn(self):
        pts = [point(0), point(3000)]
        for minutes in (5, 10):
            dots = find(parse(render(pts, longest_silence_minutes=minutes)), "circle", "dot")
            assert float(dots[1].get("cx")) - float(dots[0].get("cx")) == pytest.approx(PLOT_W * minutes * 60 / 1800, abs=0.1)

    def test_the_break_mark_size_sets_the_zigzag_height(self):
        for size in (6, 12):
            path = find(parse(render([point(0), point(3000)], longest_silence_minutes=5, break_mark_size=size)), "path", "break")[0].get("d")
            middle = TOP + PLOT_H / 2
            assert f"V{middle - size:.1f} " in path and f" {middle + size:.1f} V{TOP + PLOT_H:.1f}" in path

    def test_the_break_gap_width_sets_the_clear_gap(self):
        for width in (30, 64, 100):
            gap = find(parse(render([point(0), point(3000)], longest_silence_minutes=5, break_gap_width=width)), "rect", "gap")[0]
            assert float(gap.get("width")) == pytest.approx(width, abs=0.1)


class TestTheShapeOfASilence:
    """`silence_shape(look)`: the one place the chart's settings become the sizes of a silence."""

    def test_the_longest_silence_is_longest_silence_minutes(self):
        assert silence_shape(ChartLook(longest_silence_minutes=7)).longest_seconds == 420.0

    def test_the_cut_is_the_break_gap_width_at_the_charts_scale(self):
        assert silence_shape(LOOK).gap_seconds == pytest.approx(64.0 / PLOT_W * 1800.0)

    def test_the_cut_is_no_wider_than_a_whole_silence(self):
        assert silence_shape(ChartLook(break_gap_width=10_000.0)).gap_seconds == 300.0

    def test_the_true_scale_sides_share_what_the_cut_leaves(self):
        shape = silence_shape(ChartLook(longest_silence_minutes=10))
        assert shape.edge_seconds == pytest.approx((600.0 - shape.gap_seconds) / 2.0)

    def test_a_cut_as_wide_as_the_silence_leaves_no_sides(self):
        assert silence_shape(ChartLook(break_gap_width=10_000.0)).edge_seconds == 0.0

    def test_a_longer_window_makes_the_cut_cover_more_time(self):
        assert (silence_shape(ChartLook(window_minutes=60, longest_silence_minutes=30)).gap_seconds
                == pytest.approx(2 * silence_shape(ChartLook(window_minutes=30, longest_silence_minutes=30)).gap_seconds))


class TestSilenceShape:
    def test_with_no_gap_given_the_whole_silence_is_the_cut(self):
        shape = SilenceShape(300)
        assert shape.gap_seconds == 300 and shape.edge_seconds == 0

    def test_the_sides_share_what_the_cut_leaves(self):
        assert SilenceShape(300, 100).edge_seconds == 100

    @pytest.mark.parametrize("gap", [300, 500, 1e9])
    def test_a_gap_as_wide_as_the_limit_or_wider_is_held_at_the_limit(self, gap):
        assert SilenceShape(300, gap).gap_seconds == 300 and SilenceShape(300, gap).edge_seconds == 0

    def test_a_negative_gap_is_none_at_all(self):
        assert SilenceShape(300, -5).gap_seconds == 0 and SilenceShape(300, -5).edge_seconds == 150

    @pytest.mark.parametrize("bad", [0, -1, None, "300", float("nan"), True])
    def test_a_longest_that_is_not_a_number_above_zero_is_refused(self, bad):
        with pytest.raises(ValueError):
            SilenceShape(bad)

    def test_it_cannot_be_changed(self):
        with pytest.raises(Exception):
            SilenceShape(300).longest_seconds = 5
