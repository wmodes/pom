"""Tests for pom.monitor.monitor_site: a password-protected page of the last hour of turns.

The pages are read with Flask's test client; one test starts the real server on a free port and reads it over HTTP.
"""
import base64
import json
import logging
import urllib.error
import urllib.request

import pytest

from pom.library import Passage
from pom.monitor import MonitorSite, MonitorSiteError, MonitorStatus, ChartNotebook, SilenceShape, TurnHistory
from pom.writing import Portent

PASSWORD = "open sesame"


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def basic(user="anyone", password=PASSWORD):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def portent(text="The ghosts keep the hours.", cut_off=False):
    return Portent(text, (Passage("dreambook#7", "dreambook", "divination", "To dream of a sea is a sign of travel.", 9),
                          Passage("horoscope#2", "horoscope", "horoscope", "The Moon in Cancer favours the home.", 6)), 2.5, cut_off)


def make(clock=None, show_hinges=True, status=None, history=None, **options):
    clock = clock or Clock()
    history = history if history is not None else TurnHistory(clock=clock)
    site = MonitorSite(history, PASSWORD, show_hinges=show_hinges, status_source=status, clock=clock, **options)
    return site, history, site.app.test_client(), clock


def add(history, hinge="The dog barks at ghosts.", **extra):
    values = dict(score=7.25, bar=6.5, window_size=120, heard_at=305.0, portent=portent())
    values.update(extra)
    return history.add(hinge, **values)


def get(client, path, **kwargs):
    return client.get(path, headers=basic(), **kwargs)


class TestThePassword:
    def test_a_request_without_a_password_is_refused_and_asked_for_one(self):
        _, _, client, _ = make()
        response = client.get("/")
        assert response.status_code == 401 and "Basic" in response.headers["WWW-Authenticate"]

    @pytest.mark.parametrize("path", ["/", "/api/turns", "/turn/1", "/turn/99", "/nothing-here"])
    def test_every_page_is_behind_the_password(self, path):
        site, history, client, _ = make()
        add(history)
        assert client.get(path).status_code == 401

    def test_the_right_password_gets_in_with_any_user_name(self):
        _, _, client, _ = make()
        assert client.get("/", headers=basic("alice")).status_code == 200
        assert client.get("/", headers=basic("")).status_code == 200

    @pytest.mark.parametrize("wrong", ["", "open", "open sesame!", "OPEN SESAME", " open sesame", "open sesameopen sesame", "x"])
    def test_a_wrong_password_is_refused(self, wrong):
        _, _, client, _ = make()
        assert client.get("/", headers=basic(password=wrong)).status_code == 401

    def test_a_password_with_other_characters_works(self):
        history = TurnHistory()
        site = MonitorSite(history, "pässwörd: ñ=1")
        client = site.app.test_client()
        assert client.get("/", headers=basic(password="pässwörd: ñ=1")).status_code == 200
        assert client.get("/", headers=basic(password="passwoerd")).status_code == 401

    def test_other_kinds_of_authorization_are_refused(self):
        _, _, client, _ = make()
        assert client.get("/", headers={"Authorization": f"Bearer {PASSWORD}"}).status_code == 401
        assert client.get("/", headers={"Authorization": "Basic not-base64!!"}).status_code == 401

    def test_the_password_is_compared_in_constant_time(self, monkeypatch):
        import hmac
        calls, real = [], hmac.compare_digest
        monkeypatch.setattr("pom.monitor.monitor_site.hmac.compare_digest", lambda a, b: calls.append((a, b)) or real(a, b))
        _, _, client, _ = make()
        client.get("/", headers=basic())
        assert calls == [(PASSWORD.encode(), PASSWORD.encode())]

    @pytest.mark.parametrize("bad", ["", None, 5, b"bytes", ["pw"]])
    def test_without_a_usable_password_the_site_does_not_exist(self, bad):
        site = MonitorSite(TurnHistory(), bad)
        assert site.app is None
        assert site.start("127.0.0.1", 0) is False and site.port is None
        assert len(site.report.criticals) == 1 and "password" in site.report.criticals[0]

    def test_strict_mode_raises_for_a_missing_password(self):
        with pytest.raises(MonitorSiteError, match="password"):
            MonitorSite(TurnHistory(), "", strict=True)

    def test_the_password_is_never_in_a_report_or_a_log(self, caplog):
        caplog.set_level(logging.DEBUG)
        site = MonitorSite(TurnHistory(), "")
        _, _, client, _ = make()
        client.get("/", headers=basic(password="wrong-guess-1234"))
        assert PASSWORD not in caplog.text and "wrong-guess-1234" not in caplog.text
        assert "wrong-guess-1234" not in "\n".join(site.report.lines())


class TestTheList:
    def test_an_empty_history_says_so(self):
        _, _, client, _ = make()
        page = get(client, "/").get_data(as_text=True)
        assert "No turns in the last 60 minutes." in page and "<table" not in page

    def test_the_newest_turn_is_first(self):
        _, history, client, _ = make()
        for text in ("first hinge", "second hinge", "third hinge"):
            add(history, text)
        page = get(client, "/").get_data(as_text=True)
        assert page.index("third hinge") < page.index("second hinge") < page.index("first hinge")

    def test_each_row_shows_time_score_bar_window_hinge_and_a_link(self):
        _, history, client, _ = make()
        record = add(history, "A line about lighthouses.", score=7.256, bar=6.5, window_size=120)
        page = get(client, "/").get_data(as_text=True)
        assert f'href="/turn/{record.id}"' in page
        for shown in ("7.26", "6.50", "120", "A line about lighthouses."):
            assert shown in page

    def test_a_long_hinge_is_cut_in_the_list_but_whole_on_its_page(self):
        _, history, client, _ = make()
        long_hinge = "word " * 60
        record = add(history, long_hinge)
        assert long_hinge.strip() not in get(client, "/").get_data(as_text=True)
        assert "…" in get(client, "/").get_data(as_text=True)
        assert long_hinge.strip() in get(client, f"/turn/{record.id}").get_data(as_text=True)

    def test_a_turn_with_no_bar_yet_says_so(self):
        _, history, client, _ = make()
        add(history, bar=None)
        assert "none yet" in get(client, "/").get_data(as_text=True)

    def test_times_are_shown_as_how_long_ago(self):
        clock = Clock()
        _, history, client, _ = make(clock)
        add(history)
        clock.now += 420
        assert "7 min ago" in get(client, "/").get_data(as_text=True)

    def test_old_turns_leave_the_list(self):
        clock = Clock()
        _, history, client, _ = make(clock)
        add(history, "an old one")
        clock.now += 3601
        assert "an old one" not in get(client, "/").get_data(as_text=True)

    def test_the_page_polls_for_news_and_knows_the_newest_turn(self):
        _, history, client, _ = make()
        record = add(history)
        page = get(client, "/").get_data(as_text=True)
        assert "/api/turns" in page and f"var newest = {record.id};" in page and "5 * 1000" in page

    def test_an_empty_page_polls_with_no_newest_turn(self):
        _, _, client, _ = make()
        assert "var newest = null;" in get(client, "/").get_data(as_text=True)


class TestTheStats:
    def test_turn_counts_and_the_pace(self):
        _, history, client, _ = make()
        for _ in range(6):
            add(history)
        stats = get(client, "/api/turns").get_json()["stats"]
        assert stats["turns_in_history"] == 6 and stats["turns_per_hour"] == "6.0"
        assert stats["turns_since_start"] == 6 and stats["history_minutes"] == "60"

    def test_the_pace_scales_when_the_history_is_shorter_than_an_hour(self):
        clock = Clock()
        history = TurnHistory(max_age_seconds=1800, clock=clock)
        _, _, client, _ = make(clock, history=history)
        for _ in range(3):
            add(history)
        stats = get(client, "/api/turns").get_json()["stats"]
        assert stats["turns_per_hour"] == "6.0" and stats["history_minutes"] == "30"

    def test_turns_since_start_include_the_forgotten_ones(self):
        clock = Clock()
        _, history, client, _ = make(clock)
        add(history)
        clock.now += 4000
        stats = get(client, "/api/turns").get_json()["stats"]
        assert stats["turns_in_history"] == 0 and stats["turns_since_start"] == 1

    def test_with_no_turns_the_last_one_is_none_yet(self):
        _, _, client, _ = make()
        assert get(client, "/api/turns").get_json()["stats"]["last_turn_ago"] == "none yet"

    def test_the_last_turn_and_the_uptime_follow_the_clock(self):
        clock = Clock()
        _, history, client, _ = make(clock)
        clock.now += 600
        add(history)
        clock.now += 125
        stats = get(client, "/api/turns").get_json()["stats"]
        assert stats["last_turn_ago"] == "2 min ago" and stats["uptime"] == "12 min"

    def test_a_site_just_started_says_so(self):
        _, _, client, _ = make()
        assert get(client, "/api/turns").get_json()["stats"]["uptime"] == "just started"

    def test_the_state_comes_from_the_status_function(self):
        status = MonitorStatus(bar=6.789, window_size=84, attempts_heard=900, attempts_scored=870,
                               services={"whisper": True, "oracle": False})
        _, _, client, _ = make(status=lambda: status)
        stats = get(client, "/api/turns").get_json()["stats"]
        assert (stats["bar"], stats["bar_text"], stats["window_size"]) == (6.789, "6.79", 84)
        assert (stats["attempts_heard"], stats["attempts_scored"]) == (900, 870)
        assert stats["services"] == {"whisper": True, "oracle": False}
        page = get(client, "/").get_data(as_text=True)
        assert "6.79" in page and "900" in page and "870" in page

    def test_a_server_that_is_down_is_marked_down_on_the_page(self):
        _, _, client, _ = make(status=lambda: MonitorStatus(services={"whisper": True, "oracle": False}))
        page = get(client, "/").get_data(as_text=True)
        assert 'whisper: <b class="up">up</b>' in page and 'oracle: <b class="down">DOWN</b>' in page

    def test_without_a_status_function_the_page_says_none_yet(self):
        _, _, client, _ = make()
        stats = get(client, "/api/turns").get_json()["stats"]
        assert stats["bar"] is None and stats["bar_text"] == "none yet" and stats["services"] == {}
        assert get(client, "/").status_code == 200

    def test_a_status_function_that_fails_does_not_take_the_page_down(self):
        def broken():
            raise RuntimeError("secret detail")
        site, _, client, _ = make(status=broken)
        assert get(client, "/").status_code == 200 and get(client, "/api/turns").status_code == 200
        assert len(site.report.warnings) >= 1 and "RuntimeError" in site.report.warnings[0]
        assert "secret detail" not in "\n".join(site.report.lines())

    def test_a_status_function_that_returns_the_wrong_thing_is_ignored(self):
        site, _, client, _ = make(status=lambda: {"bar": 5})
        assert get(client, "/").status_code == 200 and "MonitorStatus" in site.report.warnings[0]


class TestTheTurnRule:
    RULE = MonitorStatus(algorithm="decaying_quantile",
                         bar_description="the 99.5th percentile of scores that count half as much every 300 s, starting at 6.8; at most one turn every 60 s")

    def test_the_index_names_the_algorithm_and_says_how_it_works(self):
        _, _, client, _ = make(status=lambda: self.RULE)
        page = get(client, "/").get_data(as_text=True)
        assert "Bar type:" in page and "<b>decaying_quantile</b>" in page
        assert "a score above the 99.5th percentile of scores that count half as much every 300 s" in page

    def test_the_feed_carries_both(self):
        _, _, client, _ = make(status=lambda: self.RULE)
        stats = get(client, "/api/turns").get_json()["stats"]
        assert stats["algorithm"] == "decaying_quantile" and stats["bar_description"].startswith("the 99.5th percentile")

    def test_a_turn_page_says_which_rule_was_in_force(self):
        _, history, client, _ = make(status=lambda: self.RULE)
        record = add(history)
        page = get(client, f"/turn/{record.id}").get_data(as_text=True)
        assert "Bar type" in page and "decaying_quantile - the 99.5th percentile" in page

    def test_with_only_the_algorithm_known_that_is_shown_alone(self):
        _, history, client, _ = make(status=lambda: MonitorStatus(algorithm="percentile"))
        record = add(history)
        page = get(client, "/").get_data(as_text=True)
        assert "<b>percentile</b>" in page and "a score above" not in page
        assert "<td>percentile</td>" in get(client, f"/turn/{record.id}").get_data(as_text=True)

    def test_with_only_a_description_known_that_is_shown(self):
        _, _, client, _ = make(status=lambda: MonitorStatus(bar_description="a fake bar"))
        assert "a score above a fake bar" in get(client, "/").get_data(as_text=True)

    def test_with_neither_known_nothing_is_said(self):
        _, history, client, _ = make()
        record = add(history)
        assert "Bar type" not in get(client, "/").get_data(as_text=True)
        assert "Bar type" not in get(client, f"/turn/{record.id}").get_data(as_text=True)
        stats = get(client, "/api/turns").get_json()["stats"]
        assert stats["algorithm"] == "" and stats["bar_description"] == ""

    def test_it_is_escaped(self):
        _, history, client, _ = make(status=lambda: MonitorStatus(algorithm="<b>x</b>", bar_description="<script>alert(1)</script>"))
        record = add(history)
        for path in ("/", f"/turn/{record.id}"):
            page = get(client, path).get_data(as_text=True)
            assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
            assert "<b>x</b>" not in page.replace("<b>decaying", "")

    def test_none_is_nothing_not_the_word_none(self):
        _, history, client, _ = make(status=lambda: MonitorStatus(algorithm=None, bar_description=None))
        record = add(history)
        stats = get(client, "/api/turns").get_json()["stats"]
        assert stats["algorithm"] == "" and stats["bar_description"] == ""
        assert "None" not in get(client, "/").get_data(as_text=True) and "None" not in get(client, f"/turn/{record.id}").get_data(as_text=True)

    @pytest.mark.parametrize("junk", [None, 5, ["x"]])
    def test_a_value_that_is_not_text_is_made_text_and_never_breaks_the_page(self, junk):
        _, history, client, _ = make(status=lambda: MonitorStatus(algorithm=junk, bar_description=junk))
        record = add(history)
        assert get(client, "/").status_code == 200 and get(client, f"/turn/{record.id}").status_code == 200

    def test_the_status_is_asked_for_a_turn_page_only_once(self):
        calls = []
        _, history, client, _ = make(status=lambda: calls.append(1) or self.RULE)
        record = add(history)
        get(client, f"/turn/{record.id}")
        assert len(calls) == 1


class TestTheChart:
    @staticmethod
    def with_notebook(**options):
        notebook = ChartNotebook()
        site, history, client, clock = make(notebook=notebook, **options)
        return notebook, history, client

    def test_the_index_has_the_chart_above_the_list(self):
        notebook, history, client = self.with_notebook()
        notebook.add(3.0, 5.0, 10.0)
        page = get(client, "/").get_data(as_text=True)
        assert '<svg class="chart"' in page and 'id="chart"' in page
        assert page.index('id="chart"') < page.index("Turns, newest first")

    def test_the_chart_is_black_dots_and_a_red_bar_on_solid_grid_paper(self):
        notebook, _, client = self.with_notebook()
        page = get(client, "/").get_data(as_text=True)
        assert "--dot:#000;" in page and "--bar:#d62828;" in page
        assert ".chart .dot { fill:var(--dot); }" in page and ".chart .bar { stroke:var(--bar);" in page
        assert ".chart .grid, .chart .vgrid { stroke:var(--line); stroke-width:1; }" in page and "dasharray" not in page

    def test_in_dark_mode_the_dots_and_the_bar_stay_visible(self):
        notebook, _, client = self.with_notebook()
        page = get(client, "/").get_data(as_text=True)
        dark = page[page.index("prefers-color-scheme: dark"):]
        assert "--dot:#f2efe6;" in dark and "--bar:#ff6b6b;" in dark

    def test_the_chart_appears_once(self):
        notebook, _, client = self.with_notebook()
        notebook.add(3.0, 5.0, 1.0)
        assert get(client, "/").get_data(as_text=True).count('<svg class="chart"') == 1

    def test_there_is_a_caption_that_says_what_the_marks_are(self):
        notebook, _, client = self.with_notebook()
        page = get(client, "/").get_data(as_text=True)
        assert "Black dots: attempts." in page and "Red line: the bar each one had to beat." in page
        assert "Orange: qualifiers (click one)." in page and "The last 30 minutes." in page

    def test_the_caption_and_the_chart_follow_the_configured_look(self):
        from pom.monitor import ChartLook
        notebook, _, client = self.with_notebook(chart=ChartLook(window_minutes=12, bar_line_width=2.5, qualifier_dot_radius=7))
        notebook.add(3.0, 5.0, 10.0, True)
        page = get(client, "/").get_data(as_text=True)
        assert "The last 12 minutes." in page and "The last 30 minutes." not in page
        assert 'stroke-width="2.5"' in page and 'r="7"' in page
        assert "last 12 minutes" in get(client, "/chart.svg").get_data(as_text=True)

    def test_the_chart_window_also_picks_which_points_are_drawn(self):
        from pom.monitor import ChartLook
        notebook, _, client = self.with_notebook(chart=ChartLook(window_minutes=5))
        notebook.add(3.0, 5.0, 0.0)
        notebook.add(3.0, 5.0, 1000.0)                      # 1000 s after the first: outside a five-minute window
        assert get(client, "/chart.svg").get_data(as_text=True).count('class="dot"') == 1

    def test_a_window_longer_than_the_old_thirty_minutes_draws_all_of_it(self):
        from pom.monitor import ChartLook
        notebook, _, client = self.with_notebook(chart=ChartLook(window_minutes=60))
        notebook.add(3.0, 5.0, 0.0)
        notebook.add(3.0, 5.0, 2400.0)                      # forty minutes after the first: inside an hour, outside half an hour
        assert get(client, "/chart.svg").get_data(as_text=True).count('class="dot"') == 2

    def test_without_a_notebook_there_is_no_chart_and_no_route(self):
        _, history, client, _ = make()
        page = get(client, "/").get_data(as_text=True)
        assert 'id="chart"' not in page and "<svg" not in page
        assert get(client, "/chart.svg").status_code == 404

    def test_an_empty_notebook_says_no_scores_yet(self):
        _, _, client = self.with_notebook()
        assert "No scores yet." in get(client, "/").get_data(as_text=True)

    def test_the_scores_and_the_bar_are_drawn(self):
        notebook, _, client = self.with_notebook()
        for i in range(5):
            notebook.add(2.0 + i, 6.0, float(i))
        svg = get(client, "/chart.svg").get_data(as_text=True)
        assert svg.count('class="dot"') == 5 and 'class="bar"' in svg

    def test_the_chart_route_is_an_svg_behind_the_password(self):
        notebook, _, client = self.with_notebook()
        notebook.add(3.0, 5.0, 1.0)
        response = get(client, "/chart.svg")
        assert response.status_code == 200 and response.mimetype == "image/svg+xml" and response.get_data(as_text=True).startswith("<svg")
        assert client.get("/chart.svg").status_code == 401

    def test_a_turn_dot_links_to_the_turn_page_by_its_heard_time(self):
        notebook, history, client = self.with_notebook()
        notebook.add(3.0, 5.0, 100.0)
        notebook.add(7.0, 5.0, 130.0, True)
        record = add(history, "a hinge", heard_at=130.0)
        assert f'href="/turn/{record.id}"' in get(client, "/chart.svg").get_data(as_text=True)

    def test_a_turn_still_being_written_is_drawn_without_a_link(self):
        notebook, history, client = self.with_notebook()
        notebook.add(7.0, 5.0, 130.0, True)
        svg = get(client, "/chart.svg").get_data(as_text=True)
        assert 'class="hit"' in svg and "/turn/" not in svg

    def test_only_the_last_half_hour_is_drawn(self):
        notebook, _, client = self.with_notebook()
        notebook.add(3.0, 5.0, 0.0)
        notebook.add(3.0, 5.0, 2500.0)
        notebook.add(3.0, 5.0, 2600.0)
        assert get(client, "/chart.svg").get_data(as_text=True).count('class="dot"') == 2

    def test_the_chart_changes_as_scores_arrive(self):
        notebook, _, client = self.with_notebook()
        notebook.add(3.0, 5.0, 1.0)
        before = get(client, "/chart.svg").get_data(as_text=True)
        notebook.add(4.0, 5.0, 2.0)
        assert get(client, "/chart.svg").get_data(as_text=True) != before

    def test_the_chart_is_drawn_even_when_the_hinges_are_hidden(self):
        notebook, history, client = self.with_notebook(show_hinges=False)
        notebook.add(3.0, 5.0, 1.0)
        assert '<svg class="chart"' in get(client, "/").get_data(as_text=True)

    def test_no_hinge_text_can_reach_the_chart(self):
        notebook, history, client = self.with_notebook()
        notebook.add(7.0, 5.0, 130.0, True)
        add(history, "My neighbour owes me forty dollars.", heard_at=130.0)
        assert "neighbour" not in get(client, "/chart.svg").get_data(as_text=True)

    def test_the_page_script_refreshes_the_chart_from_the_route(self):
        notebook, _, client = self.with_notebook()
        page = get(client, "/").get_data(as_text=True)
        assert "fetch('/chart.svg'" in page and "getElementById('chart')" in page
        assert page.count("drawChart()") >= 2                  # defined, and called by the poll

    def test_the_security_headers_still_allow_the_page_to_fetch_the_chart(self):
        notebook, _, client = self.with_notebook()
        csp = get(client, "/").headers["Content-Security-Policy"]
        assert "connect-src 'self'" in csp and "default-src 'none'" in csp


class TestTheDetailPage:
    def test_it_shows_the_hinge_exemplars_portent_score_bar_and_window(self):
        _, history, client, _ = make()
        record = add(history, "Nooooooo.", score=7.32, bar=6.9, window_size=140, heard_at=3725.0)
        page = get(client, f"/turn/{record.id}").get_data(as_text=True)
        for shown in ("Nooooooo.", "The ghosts keep the hours.", "7.32", "6.90", "140", "62 min 05 s",
                      "dreambook#7", "divination", "To dream of a sea is a sign of travel.",
                      "horoscope#2", "The Moon in Cancer favours the home.", "2.5 s", "5 words"):
            assert shown in page

    def test_a_portent_cut_off_is_marked(self):
        _, history, client, _ = make()
        record = add(history, portent=portent(cut_off=True))
        assert "cut off" in get(client, f"/turn/{record.id}").get_data(as_text=True)

    def test_a_portent_that_finished_is_not_marked_cut_off(self):
        _, history, client, _ = make()
        record = add(history)
        assert "cut off" not in get(client, f"/turn/{record.id}").get_data(as_text=True)

    def test_a_turn_with_no_portent_says_so(self):
        _, history, client, _ = make()
        record = add(history, portent=None)
        page = get(client, f"/turn/{record.id}").get_data(as_text=True)
        assert "No portent could be had" in page and "Exemplars" not in page

    def test_a_missing_bar_says_none_yet(self):
        _, history, client, _ = make()
        record = add(history, bar=None)
        assert "none yet" in get(client, f"/turn/{record.id}").get_data(as_text=True)

    @pytest.mark.parametrize("path", ["/turn/99", "/turn/0", "/turn/-1", "/turn/abc", "/turn/",
                                      "/turn/99999999999999999999999"])
    def test_an_unknown_turn_is_not_found(self, path):
        _, history, client, _ = make()
        add(history)
        assert get(client, path).status_code == 404

    def test_an_expired_turn_is_not_found(self):
        clock = Clock()
        _, history, client, _ = make(clock)
        record = add(history)
        clock.now += 3601
        assert get(client, f"/turn/{record.id}").status_code == 404

    def test_it_links_back_to_the_list(self):
        _, history, client, _ = make()
        record = add(history)
        assert 'href="/"' in get(client, f"/turn/{record.id}").get_data(as_text=True)


class TestMovingBetweenTurns:
    @staticmethod
    def links(client, record_id):
        """The numbers behind the Newer and Older links of a turn's page (None for a link that is not there)."""
        import re
        page = get(client, f"/turn/{record_id}").get_data(as_text=True)
        newer = re.search(r'href="/turn/(\d+)" rel="prev"', page)
        older = re.search(r'href="/turn/(\d+)" rel="next"', page)
        return (int(newer[1]) if newer else None), (int(older[1]) if older else None)

    def test_a_turn_in_the_middle_links_both_ways(self):
        _, history, client, _ = make()
        for text in ("one", "two", "three"):
            add(history, text)
        assert self.links(client, 2) == (3, 1)

    def test_the_newest_has_no_newer_and_the_oldest_no_older(self):
        _, history, client, _ = make()
        for text in ("one", "two", "three"):
            add(history, text)
        assert self.links(client, 3) == (None, 2)
        assert self.links(client, 1) == (2, None)

    def test_a_lone_turn_links_nowhere(self):
        _, history, client, _ = make()
        add(history)
        assert self.links(client, 1) == (None, None)

    def test_the_labels_are_always_shown_even_without_a_link(self):
        _, history, client, _ = make()
        add(history)
        page = get(client, "/turn/1").get_data(as_text=True)
        assert "Newer" in page and "Older" in page

    def test_a_neighbour_that_has_expired_is_not_linked(self):
        clock = Clock()
        _, history, client, _ = make(clock)
        add(history, "old")
        clock.now += 3000
        add(history, "new")
        clock.now += 700                                       # the first is now 3700 s old and gone
        assert self.links(client, 2) == (None, None)

    def test_a_turn_that_expires_while_its_page_is_made_has_no_neighbours_and_no_error(self):
        site, history, _, _ = make()
        add(history)
        assert site._neighbours(99) == (None, None)

    def test_the_numbers_are_the_turns_not_their_places(self):
        clock = Clock()
        history = TurnHistory(max_records=2, clock=clock)
        _, _, client, _ = make(clock, history=history)
        for text in ("one", "two", "three"):
            add(history, text)
        assert self.links(client, 3) == (None, 2) and self.links(client, 2) == (3, None)


class TestTheJsonFeed:
    def test_it_lists_the_turns_newest_first_with_their_details(self):
        _, history, client, _ = make()
        add(history, "first")
        record = add(history, "second", score=8.5, bar=7.0, window_size=99, heard_at=60.0)
        data = get(client, "/api/turns").get_json()
        assert [t["id"] for t in data["turns"]] == [2, 1]
        top = data["turns"][0]
        assert (top["hinge"], top["score"], top["bar"], top["window_size"], top["heard_at"]) == ("second", 8.5, 7.0, 99, 60.0)
        assert top["has_portent"] is True and top["portent"] == "The ghosts keep the hours."
        assert top["words"] == 5 and top["seconds"] == 2.5 and top["cut_off"] is False
        assert top["exemplars"] == ["dreambook#7", "horoscope#2"] and top["at"] == record.at

    def test_a_turn_without_a_portent_has_no_portent_fields(self):
        _, history, client, _ = make()
        add(history, portent=None)
        top = get(client, "/api/turns").get_json()["turns"][0]
        assert top["has_portent"] is False and "portent" not in top and "exemplars" not in top

    def test_it_is_json(self):
        _, _, client, _ = make()
        assert get(client, "/api/turns").mimetype == "application/json"


class TestHidingTheHinges:
    HINGE = "My neighbour owes me forty dollars."

    def pages(self, show_hinges):
        _, history, client, _ = make(show_hinges=show_hinges)
        record = add(history, self.HINGE)
        return (get(client, "/").get_data(as_text=True), get(client, f"/turn/{record.id}").get_data(as_text=True),
                get(client, "/api/turns").get_data(as_text=True))

    def test_shown_by_default_everywhere(self):
        assert all(self.HINGE in page for page in self.pages(True))

    def test_hidden_means_it_appears_on_no_page_and_not_in_the_feed(self):
        index, detail, feed = self.pages(False)
        assert not any("neighbour" in page for page in (index, detail, feed))
        assert "(hidden)" in index and "(hidden)" in detail and "hinge" not in json.loads(feed)["turns"][0]

    def test_hidden_still_shows_the_numbers(self):
        index, detail, feed = self.pages(False)
        assert "7.25" in index and "6.50" in index and "7.25" in detail
        assert json.loads(feed)["turns"][0]["score"] == 7.25


class TestTextIsEscaped:
    ATTACK = '<script>alert("x")</script> & <img src=x onerror=alert(1)>'

    def test_a_hinge_cannot_inject_markup(self):
        _, history, client, _ = make()
        record = add(history, self.ATTACK)
        for path in ("/", f"/turn/{record.id}"):
            page = get(client, path).get_data(as_text=True)
            assert "<script>alert" not in page and "<img src=x" not in page
            assert "&lt;script&gt;" in page and "&amp;" in page

    def test_a_portent_and_an_exemplar_cannot_inject_markup(self):
        _, history, client, _ = make()
        bad = Portent("<b onclick=evil()>boom</b>", (Passage("x#1", "x", "<i>reg</i>", "<u>text</u>", 1),), 1.0, False)
        record = add(history, portent=bad)
        page = get(client, f"/turn/{record.id}").get_data(as_text=True)
        assert "<b onclick" not in page and "<u>text</u>" not in page and "<i>reg</i>" not in page
        assert "&lt;b onclick=evil()&gt;boom&lt;/b&gt;" in page

    def test_a_service_name_is_escaped(self):
        _, _, client, _ = make(status=lambda: MonitorStatus(services={"<b>x</b>": True}))
        page = get(client, "/").get_data(as_text=True)
        assert "<b>x</b>:" not in page and "&lt;b&gt;x&lt;/b&gt;" in page


class TestTheHeaders:
    @pytest.mark.parametrize("path", ["/", "/api/turns"])
    def test_pages_are_not_cached_and_are_locked_down(self, path):
        _, _, client, _ = make()
        headers = get(client, path).headers
        assert headers["Cache-Control"] == "no-store" and headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY" and headers["Referrer-Policy"] == "no-referrer"
        assert "default-src 'none'" in headers["Content-Security-Policy"] and "frame" not in headers["Content-Security-Policy"].replace("frame-", "")

    def test_the_refusal_is_not_cached_either(self):
        _, _, client, _ = make()
        assert client.get("/").headers["Cache-Control"] == "no-store"

    def test_a_page_can_only_be_read_not_posted_to(self):
        _, _, client, _ = make()
        for path in ("/", "/api/turns", "/turn/1"):
            assert client.post(path, headers=basic()).status_code == 405


class TestRunningForReal:
    def test_it_serves_over_http_and_stops(self):
        history = TurnHistory()
        add(history, "A hinge over the wire.")
        site = MonitorSite(history, PASSWORD)
        assert site.start("127.0.0.1", 0) is True
        try:
            port = site.port
            assert isinstance(port, int) and port > 0
            request = urllib.request.Request(f"http://127.0.0.1:{port}/api/turns", headers=basic())
            with urllib.request.urlopen(request, timeout=5) as response:
                assert response.status == 200 and json.load(response)["turns"][0]["hinge"] == "A hinge over the wire."
            with pytest.raises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
            assert refused.value.code == 401
        finally:
            site.stop()
        assert site.port is None
        with pytest.raises(urllib.error.URLError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)

    def poll_once(self):
        site = MonitorSite(TurnHistory(), PASSWORD)
        assert site.start("127.0.0.1", 0) is True
        try:
            request = urllib.request.Request(f"http://127.0.0.1:{site.port}/api/turns", headers=basic())
            with urllib.request.urlopen(request, timeout=5) as response:
                assert response.status == 200
        finally:
            site.stop()

    def test_the_pages_polling_leaves_no_line_in_the_log_or_on_the_console_at_info(self, caplog, capfd):
        with caplog.at_level(logging.INFO, logger="pom.monitor.monitor_site"):
            self.poll_once()
        seen = capfd.readouterr()
        assert "/api/turns" not in seen.out + seen.err                          # not werkzeug's own line on the console either
        assert not [r for r in caplog.records if "/api/turns" in r.getMessage()]

    def test_at_debug_each_request_is_one_debug_line_from_this_module_and_nothing_else(self, caplog, capfd):
        with caplog.at_level(logging.DEBUG, logger="pom.monitor.monitor_site"):
            self.poll_once()
        lines = [r for r in caplog.records if r.name == "pom.monitor.monitor_site" and "/api/turns" in r.getMessage()]
        assert len(lines) == 1 and lines[0].levelno == logging.DEBUG
        assert '"GET /api/turns HTTP/1.1" 200' in lines[0].getMessage()
        seen = capfd.readouterr()
        assert "/api/turns" not in seen.out + seen.err                          # not also werkzeug's own line

    def test_starting_twice_keeps_one_server(self):
        site = MonitorSite(TurnHistory(), PASSWORD)
        try:
            assert site.start("127.0.0.1", 0) is True
            port = site.port
            assert site.start("127.0.0.1", 0) is True and site.port == port
        finally:
            site.stop()

    def test_stopping_twice_or_before_starting_is_safe(self):
        site = MonitorSite(TurnHistory(), PASSWORD)
        site.stop()
        site.start("127.0.0.1", 0)
        site.stop()
        site.stop()

    def test_a_port_that_is_taken_is_reported_not_raised(self):
        first = MonitorSite(TurnHistory(), PASSWORD)
        first.start("127.0.0.1", 0)
        try:
            second = MonitorSite(TurnHistory(), PASSWORD)
            assert second.start("127.0.0.1", first.port) is False
            assert second.port is None and len(second.report.criticals) == 1
            assert str(first.port) in second.report.criticals[0]
        finally:
            first.stop()

    @pytest.mark.parametrize("port", [-1, 70000, "x"])
    def test_a_bad_port_is_reported_not_raised(self, port):
        site = MonitorSite(TurnHistory(), PASSWORD)
        assert site.start("127.0.0.1", port) is False and len(site.report.criticals) == 1

    def test_the_site_does_not_keep_the_process_alive(self):
        site = MonitorSite(TurnHistory(), PASSWORD)
        site.start("127.0.0.1", 0)
        try:
            assert site._thread.daemon is True
        finally:
            site.stop()


class TestNothingSaidInTheRoomIsLogged:
    def test_hinges_and_portents_reach_neither_the_log_nor_the_report(self, caplog):
        caplog.set_level(logging.DEBUG)
        site, history, client, _ = make(status=lambda: 1 / 0)
        record = add(history, "My secret is the neighbour.", portent=portent("the neighbour keeps secret hours"))
        get(client, "/")
        get(client, f"/turn/{record.id}")
        get(client, "/api/turns")
        text = caplog.text + "\n".join(site.report.lines())
        assert "secret" not in text and "neighbour" not in text


class TestTheChartMovesWithTheStream:
    """With the stream's clock the chart is drawn up to now, so it moves in a quiet room. The page asks only the notebook for its ticks."""

    @staticmethod
    def moving(now=1000.0, ticks=None, status=None, notebook=None, began_at=None, head_seconds=0.0, **options):
        stream = [now]
        notebook = notebook if notebook is not None else ChartNotebook(ticks_source=lambda start, end: list(ticks or []), began_at=began_at,
                                                                      silence_shape=SilenceShape(300.0, 300.0 - 2.0 * head_seconds))
        site, history, client, clock = make(notebook=notebook, stream_now=lambda: stream[0], status=status, **options)
        return notebook, client, stream

    def test_the_chart_is_drawn_up_to_the_stream_time_not_the_newest_attempt(self):
        notebook, client, stream = self.moving(now=1000.0)
        notebook.add(3.0, 5.0, 900.0)
        near = get(client, "/chart.svg").get_data(as_text=True)
        stream[0] = 1200.0
        later = get(client, "/chart.svg").get_data(as_text=True)
        assert near != later and later.count('class="dot"') == 1                    # the same attempt, drawn further left

    def test_the_chart_moves_with_no_attempt_at_all(self):
        _, client, stream = self.moving(now=200.0, ticks=[(160.0, 5.85, 6.5)], began_at=0.0)
        first = get(client, "/chart.svg").get_data(as_text=True)
        stream[0] = 230.0
        assert first.count('class="tick"') == 1 and get(client, "/chart.svg").get_data(as_text=True) != first

    def test_the_ticks_the_notebook_gives_are_drawn(self):
        notebook, client, _ = self.moving(now=1000.0, ticks=[(940.0, 5.85, 6.0), (960.0, 5.85, 5.9)])
        notebook.add(3.0, 5.0, 900.0)
        assert get(client, "/chart.svg").get_data(as_text=True).count('class="tick"') == 2

    def test_the_ticks_the_notebook_wrote_and_the_live_ones_are_both_drawn(self):
        notebook = ChartNotebook(ticks_source=lambda a, b: [(t, 5.85, 6.0) for t in (60.0, 120.0, 240.0, 300.0) if a < t <= b], began_at=0.0)
        notebook.add(3.0, 6.0, 150.0)                                               # writes ticks 60 and 120
        _, client, _ = self.moving(now=330.0, notebook=notebook)                    # and 240 and 300 are live
        assert get(client, "/chart.svg").get_data(as_text=True).count('class="tick"') == 4

    def test_a_tick_from_before_a_long_silence_is_still_drawn(self):
        notebook = ChartNotebook(ticks_source=lambda a, b: [(t, 5.85, 6.0) for t in (60.0, 120.0) if a < t <= b], began_at=0.0)
        notebook.add(3.0, 6.0, 150.0)
        _, client, _ = self.moving(now=150.0 + 14 * 3600.0, notebook=notebook)              # fifteen hours of nothing, and the ticks are still in the notebook
        svg = get(client, "/chart.svg").get_data(as_text=True)
        assert svg.count('class="tick"') == 2 and ">silence<" in svg

    def test_the_first_and_last_minutes_of_a_silence_are_drawn_and_nothing_between(self):
        grid = [(float(t), 5.85, 6.0) for t in range(60, 40_000, 60)]
        notebook = ChartNotebook(ticks_source=lambda a, b: [tick for tick in grid if a < tick[0] <= b], silence_shape=SilenceShape(300.0, 100.0))
        notebook.add(3.0, 6.0, 30.0)                                                # its first 100 s run to 130: the ticks at 60 and 120, and not the rest
        _, client, _ = self.moving(now=30.0 + 10_000.0, notebook=notebook)
        svg = get(client, "/chart.svg").get_data(as_text=True)
        assert svg.count('class="tick"') == 2 + 2 and ">2 hrs 47 min<" in svg                  # two in the first 100 s, two in the last 100 s before now (9960 and 10020)

    def test_the_page_never_asks_the_notebook_for_anything_but_the_stream_time(self):
        asked = []
        notebook = ChartNotebook()
        notebook.ticks_up_to = lambda now: asked.append(now) or []
        _, client, _ = self.moving(now=1234.0, notebook=notebook)
        get(client, "/chart.svg")
        assert asked == [1234.0]

    def test_the_bar_line_runs_to_the_right_edge_at_the_bar_now(self):
        notebook, client, _ = self.moving(now=1000.0, status=lambda: MonitorStatus(bar=6.0))
        notebook.add(3.0, 5.0, 900.0)
        svg = get(client, "/chart.svg").get_data(as_text=True)
        import re
        assert re.search(r'class="bar"[^>]* d="[^"]*H714\.0"', svg)                    # the right edge is 720 - 6

    def test_the_bar_line_is_where_the_bar_now_is(self):
        notebook, client, _ = self.moving(now=1000.0, status=lambda: MonitorStatus(bar=6.0))
        notebook.add(3.0, 5.0, 900.0)
        high = get(client, "/chart.svg").get_data(as_text=True)
        _, client2, _ = self.moving(now=1000.0, status=lambda: MonitorStatus(bar=2.0))
        assert high != get(client2, "/chart.svg").get_data(as_text=True)

    def test_with_a_notebook_that_has_no_ticks_the_chart_still_moves(self):
        notebook = ChartNotebook()
        site, _, client, _ = make(notebook=notebook, stream_now=lambda: 1000.0)
        notebook.add(3.0, 5.0, 900.0)
        svg = get(client, "/chart.svg").get_data(as_text=True)
        assert svg.count('class="dot"') == 1 and 'class="tick"' not in svg

    def test_without_the_stream_clock_the_chart_stands_still_at_the_newest_attempt(self):
        notebook = ChartNotebook()
        _, _, client, _ = make(notebook=notebook)
        notebook.add(3.0, 5.0, 900.0)
        assert 'cx="714.0"' in get(client, "/chart.svg").get_data(as_text=True)

    def test_a_silence_is_drawn_short_through_the_page(self):
        from pom.monitor import ChartLook
        notebook, client, _ = self.moving(now=4000.0, chart=ChartLook(longest_silence_minutes=5))
        notebook.add(3.0, 5.0, 100.0)
        svg = get(client, "/chart.svg").get_data(as_text=True)
        assert 'class="gap"' in svg and ">silence<" in svg and ">1 hr 5 min<" in svg

    def test_the_page_draws_the_moving_chart_too(self):
        notebook, client, _ = self.moving(ticks=[(940.0, 5.85, 6.0)])
        notebook.add(3.0, 5.0, 900.0)
        assert 'class="tick"' in get(client, "/").get_data(as_text=True)

    def test_the_stream_clock_is_read_afresh_for_every_drawing(self):
        reads = []
        _, _, client, _ = make(notebook=ChartNotebook(), stream_now=lambda: reads.append(1) or 1000.0)
        get(client, "/chart.svg")
        get(client, "/chart.svg")
        assert len(reads) == 2


class TestTheQuietStart:
    """The page tells the chart when listening began, but only while the notebook still holds every attempt since then."""

    @staticmethod
    def site(began_at=0.0, now=3960.0, notebook=None, **options):
        notebook = notebook if notebook is not None else ChartNotebook()
        _, _, client, _ = make(notebook=notebook, stream_now=lambda: now, stream_began_at=began_at, **options)
        return notebook, client

    def test_a_room_quiet_since_the_start_has_its_silence_drawn(self):
        _, client = self.site()
        svg = get(client, "/chart.svg").get_data(as_text=True)
        assert 'class="gap"' in svg and ">1 hr 6 min<" in svg

    def test_so_does_a_quiet_start_before_the_first_attempt(self):
        notebook, client = self.site(now=1000.0)
        notebook.add(3.0, 5.0, 900.0)
        assert ">15 min<" in get(client, "/chart.svg").get_data(as_text=True)

    def test_with_no_start_given_it_is_not(self):
        _, client = self.site(began_at=None)
        assert 'class="gap"' not in get(client, "/chart.svg").get_data(as_text=True)

    def test_once_the_notebook_has_forgotten_an_attempt_the_start_is_not_used(self):
        notebook = ChartNotebook(max_entries=2)
        for at in (3000.0, 3010.0, 3020.0):
            notebook.add(3.0, 5.0, at)
        notebook, client = self.site(now=3030.0, notebook=notebook)
        assert 'class="gap"' not in get(client, "/chart.svg").get_data(as_text=True)

    def test_a_start_inside_the_longest_silence_draws_nothing(self):
        _, client = self.site(began_at=0.0, now=200.0)
        assert 'class="gap"' not in get(client, "/chart.svg").get_data(as_text=True)

    def test_the_start_can_be_any_stream_time(self):
        _, client = self.site(began_at=3000.0, now=3100.0)
        assert 'class="gap"' not in get(client, "/chart.svg").get_data(as_text=True)


class TestTheServiceLights:
    """Each light says up, DOWN or none: True, False, or None (there is nothing to check)."""

    @staticmethod
    def lights(**services):
        _, _, client, _ = make(status=lambda: MonitorStatus(services=services))
        page = get(client, "/").get_data(as_text=True)
        return page, get(client, "/api/turns").get_json()["stats"]["services"]

    def test_true_is_up_in_green(self):
        page, _ = self.lights(terminal=True)
        assert 'terminal: <b class="up">up</b>' in page

    def test_false_is_down_in_red(self):
        page, _ = self.lights(terminal=False)
        assert 'terminal: <b class="down">DOWN</b>' in page

    def test_none_is_none_in_grey(self):
        page, _ = self.lights(terminal=None)
        assert 'terminal: <b class="muted">none</b>' in page and 'terminal: <b class="down"' not in page

    def test_the_feed_keeps_the_three_apart(self):
        _, feed = self.lights(a=True, b=False, c=None)
        assert feed == {"a": True, "b": False, "c": None}

    @pytest.mark.parametrize("value", [1, 0, "yes", ""])
    def test_anything_else_is_up_or_down_as_it_would_be_as_a_bool(self, value):
        _, feed = self.lights(x=value)
        assert feed == {"x": bool(value)}

    def test_each_light_is_its_own_span_in_order(self):
        page, _ = self.lights(whisper=True, scorer=True, oracle=False, terminal=None)
        assert page.index("whisper:") < page.index("scorer:") < page.index("oracle:") < page.index("terminal:")

    def test_no_lights_no_line(self):
        _, _, client, _ = make(status=lambda: MonitorStatus())
        assert 'class="services"' not in get(client, "/").get_data(as_text=True)


class TestTheStyleOfTheNewMarks:
    def test_ticks_are_the_grid_s_grey_and_the_gap_is_the_card_s_color(self):
        page = get(make(notebook=ChartNotebook())[2], "/").get_data(as_text=True)
        assert ".chart .tick { fill:var(--line); }" in page and ".chart .gap { fill:var(--card); }" in page
        assert ".chart .break { stroke:var(--muted); fill:none; stroke-width:1; }" in page

    def test_the_grid_and_the_ticks_use_the_same_variable(self):
        page = get(make(notebook=ChartNotebook())[2], "/").get_data(as_text=True)
        assert ".chart .grid, .chart .vgrid { stroke:var(--line); stroke-width:1; }" in page and "fill:var(--line)" in page


class TestThePollBeat:
    """How often the open page asks for news is `[monitor] poll_seconds`."""

    def beat(self, **options):
        _, history, client, _ = make(**options)
        return get(client, "/").get_data(as_text=True)

    def test_the_default_is_five_seconds(self):
        assert "setInterval(poll, 5 * 1000)" in self.beat()

    def test_the_setting_is_the_beat(self):
        assert "setInterval(poll, 2 * 1000)" in self.beat(poll_seconds=2) and "setInterval(poll, 30 * 1000)" in self.beat(poll_seconds=30)

    def test_a_part_of_a_second_is_kept(self):
        assert "setInterval(poll, 2.5 * 1000)" in self.beat(poll_seconds=2.5)

    @pytest.mark.parametrize("bad", [0, -1, "5", None, True, float("inf"), float("nan")])
    def test_a_bad_beat_is_the_fallback_with_a_warning(self, bad):
        site, _, client, _ = make(poll_seconds=bad)
        assert "setInterval(poll, 5 * 1000)" in get(client, "/").get_data(as_text=True)
        assert any("poll_seconds" in w for w in site.report.warnings)

    def test_a_good_beat_says_nothing(self):
        site, _, _, _ = make(poll_seconds=7)
        assert not site.report.warnings

    def test_the_beat_is_the_same_for_everything_the_page_asks_for(self):
        page = self.beat(poll_seconds=9)
        assert page.count("setInterval") == 1 and "drawChart();" in page and "fetch('/api/turns'" in page


class TestTheTilesAreReadOnEveryPoll:
    """The status tiles come from the status function on each request, so they move on the page's beat, with no attempt needed."""

    def test_the_bar_on_the_feed_follows_the_status_function(self):
        bar = [6.80]
        _, _, client, _ = make(status=lambda: MonitorStatus(bar=bar[0], window_size=10))
        first = get(client, "/api/turns").get_json()["stats"]
        bar[0] = 6.75
        second = get(client, "/api/turns").get_json()["stats"]
        assert (first["bar_text"], second["bar_text"]) == ("6.80", "6.75")

    def test_the_window_size_follows_it_too(self):
        size = [10]
        _, _, client, _ = make(status=lambda: MonitorStatus(bar=6.0, window_size=size[0]))
        first = get(client, "/api/turns").get_json()["stats"]["window_size"]
        size[0] = 9
        assert (first, get(client, "/api/turns").get_json()["stats"]["window_size"]) == (10, 9)

    def test_the_page_sets_the_tiles_from_every_poll(self):
        page = get(make()[2], "/").get_data(as_text=True)
        assert "set('bar', s.bar_text)" in page and "set('window', s.window_size)" in page


class TestThePasswordOnLocalhost:
    """With `password_on_localhost` off, a request from the Printer's own machine (its display) needs no password; everyone else still does."""

    PATHS = ["/", "/chart.svg", "/api/turns", "/turn/1"]

    @staticmethod
    def site(**options):
        history = TurnHistory()
        add(history)
        notebook = ChartNotebook()
        notebook.add(3.0, 5.0, 10.0)
        _, _, client, _ = make(history=history, notebook=notebook, **options)
        return client

    @staticmethod
    def ask(client, path="/", address="127.0.0.1", headers=None):
        return client.get(path, headers=headers or {}, environ_overrides={"REMOTE_ADDR": address})

    @pytest.mark.parametrize("path", PATHS)
    def test_this_machine_gets_every_page_without_a_password(self, path):
        assert self.ask(self.site(password_on_localhost=False), path).status_code == 200

    @pytest.mark.parametrize("address", ["127.0.0.1", "127.0.0.2", "127.255.255.254", "::1", "::ffff:127.0.0.1"])
    def test_every_form_of_this_machine_counts(self, address):
        assert self.ask(self.site(password_on_localhost=False), address=address).status_code == 200

    def test_an_ipv4_address_in_ipv6_dress_counts_even_where_python_does_not_say_so(self, monkeypatch):
        """Older Pythons (the Pi's 3.11.2) do not call ::ffff:127.0.0.1 loopback; the site looks through the dress itself."""
        import ipaddress
        monkeypatch.setattr(ipaddress.IPv6Address, "is_loopback", property(lambda self: self._ip == 1))
        client = self.site(password_on_localhost=False)
        assert self.ask(client, address="::ffff:127.0.0.1").status_code == 200 and self.ask(client, address="::1").status_code == 200
        assert self.ask(client, address="::ffff:192.168.1.5").status_code == 401

    @pytest.mark.parametrize("address", ["192.168.1.5", "10.0.0.2", "100.64.12.7", "172.16.0.9", "8.8.8.8", "0.0.0.0", "fe80::1", "2001:db8::1",
                                         "::ffff:192.168.1.5", "128.0.0.1"])
    def test_everyone_else_still_needs_the_password(self, address):
        response = self.ask(self.site(password_on_localhost=False), address=address)
        assert response.status_code == 401 and "Basic" in response.headers["WWW-Authenticate"]

    @pytest.mark.parametrize("address", ["192.168.1.5", "100.64.12.7", "::ffff:192.168.1.5"])
    def test_and_the_password_still_works_for_them(self, address):
        assert self.ask(self.site(password_on_localhost=False), address=address, headers=basic()).status_code == 200

    @pytest.mark.parametrize("address", ["192.168.1.5", "100.64.12.7"])
    def test_and_a_wrong_password_still_does_not(self, address):
        assert self.ask(self.site(password_on_localhost=False), address=address, headers=basic(password="wrong")).status_code == 401

    @pytest.mark.parametrize("header", ["X-Forwarded-For", "Forwarded", "X-Real-IP", "x-forwarded-for"])
    def test_a_request_that_names_a_proxy_is_not_treated_as_this_machine(self, header):
        assert self.ask(self.site(password_on_localhost=False), headers={header: "203.0.113.9"}).status_code == 401

    def test_such_a_request_still_gets_in_with_the_password(self):
        assert self.ask(self.site(password_on_localhost=False), headers={**basic(), "X-Forwarded-For": "203.0.113.9"}).status_code == 200

    @pytest.mark.parametrize("address", ["", "not-an-address", "localhost", "127.0.0.1.5", "1"])
    def test_an_address_that_is_not_one_is_not_this_machine(self, address):
        assert self.ask(self.site(password_on_localhost=False), address=address).status_code == 401

    def test_by_default_this_machine_needs_the_password_too(self):
        assert self.ask(self.site()).status_code == 401 and self.ask(self.site(), headers=basic()).status_code == 200

    def test_asking_for_the_password_on_localhost_asks_everyone(self):
        assert self.ask(self.site(password_on_localhost=True)).status_code == 401

    @pytest.mark.parametrize("bad", ["no", "false", None, 0, 1, "", [False]])
    def test_a_setting_that_is_not_a_bool_asks_everyone_with_a_warning(self, bad):
        site, _, client, _ = make(password_on_localhost=bad)
        assert self.ask(client).status_code == 401 and any("password_on_localhost" in w for w in site.report.warnings)

    def test_a_good_setting_says_nothing(self):
        site, _, _, _ = make(password_on_localhost=False)
        assert not site.report.warnings

    def test_the_refusal_is_unchanged(self):
        response = self.ask(self.site(password_on_localhost=False), address="192.168.1.5")
        assert response.get_data(as_text=True) == "A password is needed." and response.headers["WWW-Authenticate"].startswith('Basic realm="Printer of Mysteries"')

    def test_over_a_real_socket_the_machines_own_connection_is_let_in(self):
        site = MonitorSite(TurnHistory(), PASSWORD, password_on_localhost=False)
        assert site.start("127.0.0.1", 0) is True
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{site.port}/api/turns", timeout=5) as response:
                assert response.status == 200
        finally:
            site.stop()

    def test_over_a_real_socket_by_default_it_is_refused(self):
        site = MonitorSite(TurnHistory(), PASSWORD)
        assert site.start("127.0.0.1", 0) is True
        try:
            with pytest.raises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(f"http://127.0.0.1:{site.port}/api/turns", timeout=5)
            assert refused.value.code == 401
        finally:
            site.stop()
