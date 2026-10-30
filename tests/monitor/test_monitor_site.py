"""Tests for pom.monitor.monitor_site: a password-protected page of the last hour of triggers.

The pages are read with Flask's test client; one test starts the real server on a free port and reads it over HTTP.
"""
import base64
import json
import logging
import urllib.error
import urllib.request

import pytest

from pom.corpus import Chunk
from pom.monitor import MonitorSite, MonitorSiteError, MonitorStatus, TriggerHistory
from pom.voice import Portent

PASSWORD = "open sesame"


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def basic(user="anyone", password=PASSWORD):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def portent(text="The ghosts keep the hours.", cut_off=False):
    return Portent(text, (Chunk("dreambook#7", "dreambook", "divination", "To dream of a sea is a sign of travel.", 9),
                          Chunk("horoscope#2", "horoscope", "horoscope", "The Moon in Cancer favours the home.", 6)), 2.5, cut_off)


def make(clock=None, show_hinges=True, status=None, history=None, **options):
    clock = clock or Clock()
    history = history if history is not None else TriggerHistory(clock=clock)
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

    @pytest.mark.parametrize("path", ["/", "/api/triggers", "/trigger/1", "/trigger/99", "/nothing-here"])
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
        history = TriggerHistory()
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
        site = MonitorSite(TriggerHistory(), bad)
        assert site.app is None
        assert site.start("127.0.0.1", 0) is False and site.port is None
        assert len(site.report.criticals) == 1 and "password" in site.report.criticals[0]

    def test_strict_mode_raises_for_a_missing_password(self):
        with pytest.raises(MonitorSiteError, match="password"):
            MonitorSite(TriggerHistory(), "", strict=True)

    def test_the_password_is_never_in_a_report_or_a_log(self, caplog):
        caplog.set_level(logging.DEBUG)
        site = MonitorSite(TriggerHistory(), "")
        _, _, client, _ = make()
        client.get("/", headers=basic(password="wrong-guess-1234"))
        assert PASSWORD not in caplog.text and "wrong-guess-1234" not in caplog.text
        assert "wrong-guess-1234" not in "\n".join(site.report.lines())


class TestTheList:
    def test_an_empty_history_says_so(self):
        _, _, client, _ = make()
        page = get(client, "/").get_data(as_text=True)
        assert "No triggers in the last 60 minutes." in page and "<table" not in page

    def test_the_newest_trigger_is_first(self):
        _, history, client, _ = make()
        for text in ("first hinge", "second hinge", "third hinge"):
            add(history, text)
        page = get(client, "/").get_data(as_text=True)
        assert page.index("third hinge") < page.index("second hinge") < page.index("first hinge")

    def test_each_row_shows_time_score_bar_window_hinge_and_a_link(self):
        _, history, client, _ = make()
        record = add(history, "A line about lighthouses.", score=7.256, bar=6.5, window_size=120)
        page = get(client, "/").get_data(as_text=True)
        assert f'href="/trigger/{record.id}"' in page
        for shown in ("7.26", "6.50", "120", "A line about lighthouses."):
            assert shown in page

    def test_a_long_hinge_is_cut_in_the_list_but_whole_on_its_page(self):
        _, history, client, _ = make()
        long_hinge = "word " * 60
        record = add(history, long_hinge)
        assert long_hinge.strip() not in get(client, "/").get_data(as_text=True)
        assert "…" in get(client, "/").get_data(as_text=True)
        assert long_hinge.strip() in get(client, f"/trigger/{record.id}").get_data(as_text=True)

    def test_a_trigger_with_no_bar_yet_says_so(self):
        _, history, client, _ = make()
        add(history, bar=None)
        assert "none yet" in get(client, "/").get_data(as_text=True)

    def test_times_are_shown_as_how_long_ago(self):
        clock = Clock()
        _, history, client, _ = make(clock)
        add(history)
        clock.now += 420
        assert "7 min ago" in get(client, "/").get_data(as_text=True)

    def test_old_triggers_leave_the_list(self):
        clock = Clock()
        _, history, client, _ = make(clock)
        add(history, "an old one")
        clock.now += 3601
        assert "an old one" not in get(client, "/").get_data(as_text=True)

    def test_the_page_polls_for_news_and_knows_the_newest_trigger(self):
        _, history, client, _ = make()
        record = add(history)
        page = get(client, "/").get_data(as_text=True)
        assert "/api/triggers" in page and f"var newest = {record.id};" in page and "5 * 1000" in page

    def test_an_empty_page_polls_with_no_newest_trigger(self):
        _, _, client, _ = make()
        assert "var newest = null;" in get(client, "/").get_data(as_text=True)


class TestTheStats:
    def test_trigger_counts_and_the_pace(self):
        _, history, client, _ = make()
        for _ in range(6):
            add(history)
        stats = get(client, "/api/triggers").get_json()["stats"]
        assert stats["triggers_in_history"] == 6 and stats["triggers_per_hour"] == "6.0"
        assert stats["triggers_since_start"] == 6 and stats["history_minutes"] == "60"

    def test_the_pace_scales_when_the_history_is_shorter_than_an_hour(self):
        clock = Clock()
        history = TriggerHistory(max_age_seconds=1800, clock=clock)
        _, _, client, _ = make(clock, history=history)
        for _ in range(3):
            add(history)
        stats = get(client, "/api/triggers").get_json()["stats"]
        assert stats["triggers_per_hour"] == "6.0" and stats["history_minutes"] == "30"

    def test_triggers_since_start_include_the_forgotten_ones(self):
        clock = Clock()
        _, history, client, _ = make(clock)
        add(history)
        clock.now += 4000
        stats = get(client, "/api/triggers").get_json()["stats"]
        assert stats["triggers_in_history"] == 0 and stats["triggers_since_start"] == 1

    def test_with_no_triggers_the_last_one_is_none_yet(self):
        _, _, client, _ = make()
        assert get(client, "/api/triggers").get_json()["stats"]["last_trigger_ago"] == "none yet"

    def test_the_last_trigger_and_the_uptime_follow_the_clock(self):
        clock = Clock()
        _, history, client, _ = make(clock)
        clock.now += 600
        add(history)
        clock.now += 125
        stats = get(client, "/api/triggers").get_json()["stats"]
        assert stats["last_trigger_ago"] == "2 min ago" and stats["uptime"] == "12 min"

    def test_a_site_just_started_says_so(self):
        _, _, client, _ = make()
        assert get(client, "/api/triggers").get_json()["stats"]["uptime"] == "just started"

    def test_the_state_comes_from_the_status_function(self):
        status = MonitorStatus(bar=6.789, window_size=84, sentences_heard=900, sentences_scored=870,
                               services={"whisper": True, "oracle": False})
        _, _, client, _ = make(status=lambda: status)
        stats = get(client, "/api/triggers").get_json()["stats"]
        assert (stats["bar"], stats["bar_text"], stats["window_size"]) == (6.789, "6.79", 84)
        assert (stats["sentences_heard"], stats["sentences_scored"]) == (900, 870)
        assert stats["services"] == {"whisper": True, "oracle": False}
        page = get(client, "/").get_data(as_text=True)
        assert "6.79" in page and "900" in page and "870" in page

    def test_a_server_that_is_down_is_marked_down_on_the_page(self):
        _, _, client, _ = make(status=lambda: MonitorStatus(services={"whisper": True, "oracle": False}))
        page = get(client, "/").get_data(as_text=True)
        assert 'whisper: <b class="up">up</b>' in page and 'oracle: <b class="down">DOWN</b>' in page

    def test_without_a_status_function_the_page_says_none_yet(self):
        _, _, client, _ = make()
        stats = get(client, "/api/triggers").get_json()["stats"]
        assert stats["bar"] is None and stats["bar_text"] == "none yet" and stats["services"] == {}
        assert get(client, "/").status_code == 200

    def test_a_status_function_that_fails_does_not_take_the_page_down(self):
        def broken():
            raise RuntimeError("secret detail")
        site, _, client, _ = make(status=broken)
        assert get(client, "/").status_code == 200 and get(client, "/api/triggers").status_code == 200
        assert len(site.report.warnings) >= 1 and "RuntimeError" in site.report.warnings[0]
        assert "secret detail" not in "\n".join(site.report.lines())

    def test_a_status_function_that_returns_the_wrong_thing_is_ignored(self):
        site, _, client, _ = make(status=lambda: {"bar": 5})
        assert get(client, "/").status_code == 200 and "MonitorStatus" in site.report.warnings[0]


class TestTheTriggerRule:
    RULE = MonitorStatus(algorithm="decaying_quantile",
                         trigger_description="the 99.5th percentile of scores that count half as much every 300 s, starting at 6.8; at most one trigger every 60 s")

    def test_the_index_names_the_algorithm_and_says_how_it_works(self):
        _, _, client, _ = make(status=lambda: self.RULE)
        page = get(client, "/").get_data(as_text=True)
        assert "Trigger rule:" in page and "<b>decaying_quantile</b>" in page
        assert "a score above the 99.5th percentile of scores that count half as much every 300 s" in page

    def test_the_feed_carries_both(self):
        _, _, client, _ = make(status=lambda: self.RULE)
        stats = get(client, "/api/triggers").get_json()["stats"]
        assert stats["algorithm"] == "decaying_quantile" and stats["trigger_description"].startswith("the 99.5th percentile")

    def test_a_trigger_page_says_which_rule_was_in_force(self):
        _, history, client, _ = make(status=lambda: self.RULE)
        record = add(history)
        page = get(client, f"/trigger/{record.id}").get_data(as_text=True)
        assert "Trigger rule" in page and "decaying_quantile - the 99.5th percentile" in page

    def test_with_only_the_algorithm_known_that_is_shown_alone(self):
        _, history, client, _ = make(status=lambda: MonitorStatus(algorithm="percentile"))
        record = add(history)
        page = get(client, "/").get_data(as_text=True)
        assert "<b>percentile</b>" in page and "a score above" not in page
        assert "<td>percentile</td>" in get(client, f"/trigger/{record.id}").get_data(as_text=True)

    def test_with_only_a_description_known_that_is_shown(self):
        _, _, client, _ = make(status=lambda: MonitorStatus(trigger_description="a fake bar"))
        assert "a score above a fake bar" in get(client, "/").get_data(as_text=True)

    def test_with_neither_known_nothing_is_said(self):
        _, history, client, _ = make()
        record = add(history)
        assert "Trigger rule" not in get(client, "/").get_data(as_text=True)
        assert "Trigger rule" not in get(client, f"/trigger/{record.id}").get_data(as_text=True)
        stats = get(client, "/api/triggers").get_json()["stats"]
        assert stats["algorithm"] == "" and stats["trigger_description"] == ""

    def test_it_is_escaped(self):
        _, history, client, _ = make(status=lambda: MonitorStatus(algorithm="<b>x</b>", trigger_description="<script>alert(1)</script>"))
        record = add(history)
        for path in ("/", f"/trigger/{record.id}"):
            page = get(client, path).get_data(as_text=True)
            assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
            assert "<b>x</b>" not in page.replace("<b>decaying", "")

    def test_none_is_nothing_not_the_word_none(self):
        _, history, client, _ = make(status=lambda: MonitorStatus(algorithm=None, trigger_description=None))
        record = add(history)
        stats = get(client, "/api/triggers").get_json()["stats"]
        assert stats["algorithm"] == "" and stats["trigger_description"] == ""
        assert "None" not in get(client, "/").get_data(as_text=True) and "None" not in get(client, f"/trigger/{record.id}").get_data(as_text=True)

    @pytest.mark.parametrize("junk", [None, 5, ["x"]])
    def test_a_value_that_is_not_text_is_made_text_and_never_breaks_the_page(self, junk):
        _, history, client, _ = make(status=lambda: MonitorStatus(algorithm=junk, trigger_description=junk))
        record = add(history)
        assert get(client, "/").status_code == 200 and get(client, f"/trigger/{record.id}").status_code == 200

    def test_the_status_is_asked_for_a_trigger_page_only_once(self):
        calls = []
        _, history, client, _ = make(status=lambda: calls.append(1) or self.RULE)
        record = add(history)
        get(client, f"/trigger/{record.id}")
        assert len(calls) == 1


class TestTheDetailPage:
    def test_it_shows_the_hinge_exemplars_portent_score_bar_and_window(self):
        _, history, client, _ = make()
        record = add(history, "Nooooooo.", score=7.32, bar=6.9, window_size=140, heard_at=3725.0)
        page = get(client, f"/trigger/{record.id}").get_data(as_text=True)
        for shown in ("Nooooooo.", "The ghosts keep the hours.", "7.32", "6.90", "140", "62 min 05 s",
                      "dreambook#7", "divination", "To dream of a sea is a sign of travel.",
                      "horoscope#2", "The Moon in Cancer favours the home.", "2.5 s", "5 words"):
            assert shown in page

    def test_a_portent_cut_off_is_marked(self):
        _, history, client, _ = make()
        record = add(history, portent=portent(cut_off=True))
        assert "cut off" in get(client, f"/trigger/{record.id}").get_data(as_text=True)

    def test_a_portent_that_finished_is_not_marked_cut_off(self):
        _, history, client, _ = make()
        record = add(history)
        assert "cut off" not in get(client, f"/trigger/{record.id}").get_data(as_text=True)

    def test_a_trigger_with_no_portent_says_so(self):
        _, history, client, _ = make()
        record = add(history, portent=None)
        page = get(client, f"/trigger/{record.id}").get_data(as_text=True)
        assert "No portent could be had" in page and "Exemplars" not in page

    def test_a_missing_bar_says_none_yet(self):
        _, history, client, _ = make()
        record = add(history, bar=None)
        assert "none yet" in get(client, f"/trigger/{record.id}").get_data(as_text=True)

    @pytest.mark.parametrize("path", ["/trigger/99", "/trigger/0", "/trigger/-1", "/trigger/abc", "/trigger/",
                                      "/trigger/99999999999999999999999"])
    def test_an_unknown_trigger_is_not_found(self, path):
        _, history, client, _ = make()
        add(history)
        assert get(client, path).status_code == 404

    def test_an_expired_trigger_is_not_found(self):
        clock = Clock()
        _, history, client, _ = make(clock)
        record = add(history)
        clock.now += 3601
        assert get(client, f"/trigger/{record.id}").status_code == 404

    def test_it_links_back_to_the_list(self):
        _, history, client, _ = make()
        record = add(history)
        assert 'href="/"' in get(client, f"/trigger/{record.id}").get_data(as_text=True)


class TestMovingBetweenTriggers:
    @staticmethod
    def links(client, record_id):
        """The numbers behind the Newer and Older links of a trigger's page (None for a link that is not there)."""
        import re
        page = get(client, f"/trigger/{record_id}").get_data(as_text=True)
        newer = re.search(r'href="/trigger/(\d+)" rel="prev"', page)
        older = re.search(r'href="/trigger/(\d+)" rel="next"', page)
        return (int(newer[1]) if newer else None), (int(older[1]) if older else None)

    def test_a_trigger_in_the_middle_links_both_ways(self):
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

    def test_a_lone_trigger_links_nowhere(self):
        _, history, client, _ = make()
        add(history)
        assert self.links(client, 1) == (None, None)

    def test_the_labels_are_always_shown_even_without_a_link(self):
        _, history, client, _ = make()
        add(history)
        page = get(client, "/trigger/1").get_data(as_text=True)
        assert "Newer" in page and "Older" in page

    def test_a_neighbour_that_has_expired_is_not_linked(self):
        clock = Clock()
        _, history, client, _ = make(clock)
        add(history, "old")
        clock.now += 3000
        add(history, "new")
        clock.now += 700                                       # the first is now 3700 s old and gone
        assert self.links(client, 2) == (None, None)

    def test_a_trigger_that_expires_while_its_page_is_made_has_no_neighbours_and_no_error(self):
        site, history, _, _ = make()
        add(history)
        assert site._neighbours(99) == (None, None)

    def test_the_numbers_are_the_triggers_not_their_places(self):
        clock = Clock()
        history = TriggerHistory(max_records=2, clock=clock)
        _, _, client, _ = make(clock, history=history)
        for text in ("one", "two", "three"):
            add(history, text)
        assert self.links(client, 3) == (None, 2) and self.links(client, 2) == (3, None)


class TestTheJsonFeed:
    def test_it_lists_the_triggers_newest_first_with_their_details(self):
        _, history, client, _ = make()
        add(history, "first")
        record = add(history, "second", score=8.5, bar=7.0, window_size=99, heard_at=60.0)
        data = get(client, "/api/triggers").get_json()
        assert [t["id"] for t in data["triggers"]] == [2, 1]
        top = data["triggers"][0]
        assert (top["hinge"], top["score"], top["bar"], top["window_size"], top["heard_at"]) == ("second", 8.5, 7.0, 99, 60.0)
        assert top["has_portent"] is True and top["portent"] == "The ghosts keep the hours."
        assert top["words"] == 5 and top["seconds"] == 2.5 and top["cut_off"] is False
        assert top["exemplars"] == ["dreambook#7", "horoscope#2"] and top["at"] == record.at

    def test_a_trigger_without_a_portent_has_no_portent_fields(self):
        _, history, client, _ = make()
        add(history, portent=None)
        top = get(client, "/api/triggers").get_json()["triggers"][0]
        assert top["has_portent"] is False and "portent" not in top and "exemplars" not in top

    def test_it_is_json(self):
        _, _, client, _ = make()
        assert get(client, "/api/triggers").mimetype == "application/json"


class TestHidingTheHinges:
    HINGE = "My neighbour owes me forty dollars."

    def pages(self, show_hinges):
        _, history, client, _ = make(show_hinges=show_hinges)
        record = add(history, self.HINGE)
        return (get(client, "/").get_data(as_text=True), get(client, f"/trigger/{record.id}").get_data(as_text=True),
                get(client, "/api/triggers").get_data(as_text=True))

    def test_shown_by_default_everywhere(self):
        assert all(self.HINGE in page for page in self.pages(True))

    def test_hidden_means_it_appears_on_no_page_and_not_in_the_feed(self):
        index, detail, feed = self.pages(False)
        assert not any("neighbour" in page for page in (index, detail, feed))
        assert "(hidden)" in index and "(hidden)" in detail and "hinge" not in json.loads(feed)["triggers"][0]

    def test_hidden_still_shows_the_numbers(self):
        index, detail, feed = self.pages(False)
        assert "7.25" in index and "6.50" in index and "7.25" in detail
        assert json.loads(feed)["triggers"][0]["score"] == 7.25


class TestTextIsEscaped:
    ATTACK = '<script>alert("x")</script> & <img src=x onerror=alert(1)>'

    def test_a_hinge_cannot_inject_markup(self):
        _, history, client, _ = make()
        record = add(history, self.ATTACK)
        for path in ("/", f"/trigger/{record.id}"):
            page = get(client, path).get_data(as_text=True)
            assert "<script>alert" not in page and "<img src=x" not in page
            assert "&lt;script&gt;" in page and "&amp;" in page

    def test_a_portent_and_an_exemplar_cannot_inject_markup(self):
        _, history, client, _ = make()
        bad = Portent("<b onclick=evil()>boom</b>", (Chunk("x#1", "x", "<i>reg</i>", "<u>text</u>", 1),), 1.0, False)
        record = add(history, portent=bad)
        page = get(client, f"/trigger/{record.id}").get_data(as_text=True)
        assert "<b onclick" not in page and "<u>text</u>" not in page and "<i>reg</i>" not in page
        assert "&lt;b onclick=evil()&gt;boom&lt;/b&gt;" in page

    def test_a_service_name_is_escaped(self):
        _, _, client, _ = make(status=lambda: MonitorStatus(services={"<b>x</b>": True}))
        page = get(client, "/").get_data(as_text=True)
        assert "<b>x</b>:" not in page and "&lt;b&gt;x&lt;/b&gt;" in page


class TestTheHeaders:
    @pytest.mark.parametrize("path", ["/", "/api/triggers"])
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
        for path in ("/", "/api/triggers", "/trigger/1"):
            assert client.post(path, headers=basic()).status_code == 405


class TestRunningForReal:
    def test_it_serves_over_http_and_stops(self):
        history = TriggerHistory()
        add(history, "A hinge over the wire.")
        site = MonitorSite(history, PASSWORD)
        assert site.start("127.0.0.1", 0) is True
        try:
            port = site.port
            assert isinstance(port, int) and port > 0
            request = urllib.request.Request(f"http://127.0.0.1:{port}/api/triggers", headers=basic())
            with urllib.request.urlopen(request, timeout=5) as response:
                assert response.status == 200 and json.load(response)["triggers"][0]["hinge"] == "A hinge over the wire."
            with pytest.raises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
            assert refused.value.code == 401
        finally:
            site.stop()
        assert site.port is None
        with pytest.raises(urllib.error.URLError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)

    def test_starting_twice_keeps_one_server(self):
        site = MonitorSite(TriggerHistory(), PASSWORD)
        try:
            assert site.start("127.0.0.1", 0) is True
            port = site.port
            assert site.start("127.0.0.1", 0) is True and site.port == port
        finally:
            site.stop()

    def test_stopping_twice_or_before_starting_is_safe(self):
        site = MonitorSite(TriggerHistory(), PASSWORD)
        site.stop()
        site.start("127.0.0.1", 0)
        site.stop()
        site.stop()

    def test_a_port_that_is_taken_is_reported_not_raised(self):
        first = MonitorSite(TriggerHistory(), PASSWORD)
        first.start("127.0.0.1", 0)
        try:
            second = MonitorSite(TriggerHistory(), PASSWORD)
            assert second.start("127.0.0.1", first.port) is False
            assert second.port is None and len(second.report.criticals) == 1
            assert str(first.port) in second.report.criticals[0]
        finally:
            first.stop()

    @pytest.mark.parametrize("port", [-1, 70000, "x"])
    def test_a_bad_port_is_reported_not_raised(self, port):
        site = MonitorSite(TriggerHistory(), PASSWORD)
        assert site.start("127.0.0.1", port) is False and len(site.report.criticals) == 1

    def test_the_site_does_not_keep_the_process_alive(self):
        site = MonitorSite(TriggerHistory(), PASSWORD)
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
        get(client, f"/trigger/{record.id}")
        get(client, "/api/triggers")
        text = caplog.text + "\n".join(site.report.lines())
        assert "secret" not in text and "neighbour" not in text
