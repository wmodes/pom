"""Tests for pom.monitor.score_chart: the scores and the bar as a small inline SVG, from numbers only."""
import re
import xml.etree.ElementTree as ET

import pytest

from pom.monitor.score_chart import (DEFAULT_SECONDS, HEIGHT, LEFT, RIGHT, TOP, BOTTOM, WIDTH, render_score_chart)
from pom.monitor.score_trace import TracePoint

NS = "{http://www.w3.org/2000/svg}"
PLOT_W, PLOT_H = WIDTH - LEFT - RIGHT, HEIGHT - TOP - BOTTOM


def parse(svg):
    return ET.fromstring(svg)


def find(root, tag, css):
    return [e for e in root.iter(NS + tag) if e.get("class") == css]


def point(at, score=3.0, bar=5.0, triggered=False):
    return TracePoint(float(at), float(score), None if bar is None else float(bar), triggered)


def x_of(at, t_end, seconds=DEFAULT_SECONDS):
    return LEFT + PLOT_W * (1.0 - (t_end - at) / seconds)


def y_of(value):
    return TOP + PLOT_H * (1.0 - min(max(value, 0.0), 9.0) / 9.0)


class TestTheFrame:
    def test_it_is_one_svg_with_a_viewbox_and_a_role(self):
        root = parse(render_score_chart([point(0)]))
        assert root.tag == NS + "svg" and root.get("viewBox") == f"0 0 {WIDTH} {HEIGHT}" and root.get("role") == "img"
        assert root.get("class") == "chart"

    def test_it_is_well_formed_xml_in_every_case(self):
        for pts in ([], [point(0)], [point(0, triggered=True), point(5, bar=None)]):
            parse(render_score_chart(pts, {0.0: 4}))

    def test_a_size_can_be_given(self):
        assert parse(render_score_chart([point(0)], width=400, height=100)).get("viewBox") == "0 0 400 100"

    def test_there_is_a_title_for_screen_readers_with_the_counts(self):
        title = parse(render_score_chart([point(0), point(10, triggered=True), point(20)])).find(NS + "title").text
        assert title == "Scores and the bar over the last 30 minutes: 3 sentences, 1 triggers."

    def test_the_gridlines_are_at_zero_three_six_and_nine(self):
        root = parse(render_score_chart([point(0)]))
        lines = find(root, "line", "grid")
        assert sorted(float(line.get("y1")) for line in lines) == pytest.approx(sorted(y_of(v) for v in (0, 3, 6, 9)), abs=0.06)
        labels = [t.text for t in find(root, "text", "key") if t.text in {"0", "3", "6", "9"}]
        assert sorted(labels) == ["0", "3", "6", "9"]

    def test_the_gridlines_span_the_plot(self):
        line = find(parse(render_score_chart([point(0)])), "line", "grid")[0]
        assert (float(line.get("x1")), float(line.get("x2"))) == (LEFT, WIDTH - RIGHT)


class TestNoScoresYet:
    def test_it_says_so(self):
        root = parse(render_score_chart([]))
        assert root.find(NS + "title").text == "No scores yet."
        assert [t.text for t in root.iter(NS + "text")] == ["No scores yet."]

    def test_it_draws_no_dots_or_lines(self):
        root = parse(render_score_chart([]))
        assert not list(root.iter(NS + "circle")) and not list(root.iter(NS + "path")) and not list(root.iter(NS + "line"))


class TestTheDots:
    def test_each_sentence_is_a_dot_at_its_score_and_time(self):
        root = parse(render_score_chart([point(0, score=6.0), point(900, score=2.0)]))
        dots = find(root, "circle", "dot")
        assert len(dots) == 2
        assert [float(d.get("cx")) for d in dots] == pytest.approx([x_of(0, 900), x_of(900, 900)], abs=0.06)
        assert [float(d.get("cy")) for d in dots] == pytest.approx([y_of(6.0), y_of(2.0)], abs=0.06)

    def test_the_newest_point_is_at_the_right_edge_and_the_window_start_at_the_left(self):
        dots = find(parse(render_score_chart([point(0), point(1800)])), "circle", "dot")
        assert float(dots[0].get("cx")) == pytest.approx(LEFT, abs=0.06) and float(dots[1].get("cx")) == pytest.approx(WIDTH - RIGHT, abs=0.06)

    def test_a_score_of_nine_is_at_the_top_and_zero_at_the_bottom(self):
        dots = find(parse(render_score_chart([point(0, score=9.0), point(1, score=0.0)])), "circle", "dot")
        assert float(dots[0].get("cy")) == pytest.approx(TOP, abs=0.06) and float(dots[1].get("cy")) == pytest.approx(TOP + PLOT_H, abs=0.06)

    def test_a_score_off_the_scale_is_drawn_at_the_edge(self):
        dots = find(parse(render_score_chart([point(0, score=12.0), point(1, score=-3.0)])), "circle", "dot")
        assert float(dots[0].get("cy")) == pytest.approx(TOP, abs=0.06) and float(dots[1].get("cy")) == pytest.approx(TOP + PLOT_H, abs=0.06)

    def test_points_older_than_the_window_are_left_out(self):
        root = parse(render_score_chart([point(0), point(2000), point(2100)], seconds=1800))
        assert len(find(root, "circle", "dot")) == 2

    def test_a_shorter_window_stretches_the_points_out(self):
        pts = [point(0), point(300)]
        wide = parse(render_score_chart(pts, seconds=1800))
        narrow = parse(render_score_chart(pts, seconds=300))
        gap = lambda r: float(find(r, "circle", "dot")[1].get("cx")) - float(find(r, "circle", "dot")[0].get("cx"))
        assert gap(narrow) > gap(wide) * 5

    def test_a_plain_dot_is_small_and_a_trigger_dot_is_clearly_bigger(self):
        root = parse(render_score_chart([point(0), point(1, triggered=True)]))
        dot, hit = float(find(root, "circle", "dot")[0].get("r")), float(find(root, "circle", "hit")[0].get("r"))
        assert dot < 3 and hit >= 2 * dot

    def test_a_trigger_is_not_also_drawn_as_a_plain_dot(self):
        root = parse(render_score_chart([point(0), point(1, triggered=True)]))
        assert len(find(root, "circle", "dot")) == 1 and len(find(root, "circle", "hit")) == 1


class TestTheBar:
    def path(self, pts):
        paths = find(parse(render_score_chart(pts)), "path", "bar")
        return paths[0].get("d") if paths else None

    def test_it_starts_at_the_first_bar_and_steps_to_each_next_one(self):
        d = self.path([point(0, bar=5.0), point(900, bar=7.0), point(1800, bar=6.0)])
        x0, x1, x2 = x_of(0, 1800), x_of(900, 1800), x_of(1800, 1800)
        expected = f"M{x0:.1f} {y_of(5.0):.1f} V{y_of(7.0):.1f} H{x1:.1f} V{y_of(6.0):.1f} H{x2:.1f}"
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
        assert len(find(parse(render_score_chart([point(0)])), "path", "bar")) == 1


class TestTheTriggers:
    def test_a_trigger_is_a_larger_dot_at_its_score(self):
        hit = find(parse(render_score_chart([point(0), point(900, score=7.5, triggered=True)])), "circle", "hit")[0]
        assert float(hit.get("r")) > 3 and float(hit.get("cy")) == pytest.approx(y_of(7.5), abs=0.06)

    def test_it_links_to_its_page_when_the_record_is_known(self):
        svg = render_score_chart([point(0), point(900, triggered=True)], {900.0: 17})
        links = [a for a in parse(svg).iter(NS + "a")]
        assert len(links) == 1 and links[0].get("href") == "/trigger/17" and links[0].find(NS + "circle").get("class") == "hit"

    def test_it_has_no_link_when_the_record_is_not_known_yet(self):
        svg = render_score_chart([point(0), point(900, triggered=True)], {})
        root = parse(svg)
        assert not list(root.iter(NS + "a")) and len(find(root, "circle", "hit")) == 1

    def test_only_the_trigger_with_a_record_is_linked(self):
        pts = [point(0, triggered=True), point(100, triggered=True)]
        root = parse(render_score_chart(pts, {100.0: 5}))
        assert [a.get("href") for a in root.iter(NS + "a")] == ["/trigger/5"] and len(find(root, "circle", "hit")) == 2

    def test_the_tooltip_has_the_score_and_the_bar(self):
        hit = find(parse(render_score_chart([point(0, score=6.987, bar=6.5, triggered=True)])), "circle", "hit")[0]
        assert hit.find(NS + "title").text == "score 6.99, bar 6.50"

    def test_the_tooltip_without_a_bar_has_only_the_score(self):
        hit = find(parse(render_score_chart([point(0, score=6.987, bar=None, triggered=True)])), "circle", "hit")[0]
        assert hit.find(NS + "title").text == "score 6.99"

    def test_a_trigger_outside_the_window_is_left_out(self):
        root = parse(render_score_chart([point(0, triggered=True), point(5000)], {0.0: 1}))
        assert not find(root, "circle", "hit") and not list(root.iter(NS + "a"))


class TestTheTimeAxis:
    def labels(self, seconds=1800):
        root = parse(render_score_chart([point(0), point(seconds)], seconds=seconds))
        return [t.text for t in find(root, "text", "key") if t.text and ("min" in t.text or t.text == "now")]

    def test_it_runs_back_from_now_every_ten_minutes(self):
        assert self.labels(1800) == ["now", "-10 min", "-20 min", "-30 min"]

    def test_a_shorter_window_has_fewer_labels(self):
        assert self.labels(900) == ["now", "-10 min"]

    def test_the_labels_sit_where_their_times_are(self):
        root = parse(render_score_chart([point(0), point(1800)]))
        texts = {t.text: float(t.get("x")) for t in find(root, "text", "key") if t.text in {"now", "-30 min"}}
        assert texts["now"] == pytest.approx(WIDTH - RIGHT, abs=0.06) and texts["-30 min"] == pytest.approx(LEFT, abs=0.06)

    def test_now_is_right_aligned_and_the_others_centred(self):
        root = parse(render_score_chart([point(0), point(1800)]))
        anchors = {t.text: t.get("text-anchor") for t in find(root, "text", "key") if t.text in {"now", "-10 min"}}
        assert anchors == {"now": "end", "-10 min": "middle"}


class TestItIsNumbersOnly:
    def test_the_only_words_in_it_are_the_chart_s_own(self):
        root = parse(render_score_chart([point(0, triggered=True), point(5), point(1500)], {0.0: 3}))
        texts = [t.text for t in root.iter(NS + "text")]
        assert texts and all(re.fullmatch(r"\d|now|-\d+ min", text) for text in texts)
        titles = [t.text for t in root.iter(NS + "title")]
        assert all(re.fullmatch(r"(Scores and the bar over the last \d+ minutes: \d+ sentences, \d+ triggers\.|score [\d.]+(, bar [\d.]+)?)", title)
                   for title in titles)

    def test_the_output_is_stable(self):
        pts = [point(i * 7, score=(i % 9), bar=5 + (i % 3) / 2, triggered=(i % 11 == 0)) for i in range(60)]
        assert render_score_chart(pts, {0.0: 1}) == render_score_chart(pts, {0.0: 1})

    def test_a_busy_half_hour_draws_in_a_reasonable_size(self):
        pts = [point(i * 4, score=(i % 9), bar=6.0) for i in range(450)]
        assert len(render_score_chart(pts)) < 90_000
