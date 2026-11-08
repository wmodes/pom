"""Tests for pom.printer: the entry point builds every part from one Config, and runs the Conductor.

Nothing here contacts a server or opens a microphone or a port: `build_printer` is plain construction, and what runs is faked.
"""
import logging
import signal
import urllib.error
from pathlib import Path

import pytest

from pom import printer as printer_module
from pom.config import Config
from pom.conductor import BootSlipper, Conductor, FloorKeeper, KeyboardWatcher, TurnTaker
from pom.listening import MicDeck
from pom.monitor import MonitorSite
from pom.printer import Options, Printer, ServiceWatch, answers, build_printer, parse_options, reachable
from pom.printing import ConsoleTerminal, TerminalPort
from pom.scoring import ScorerWarmer

ROOT = Path(__file__).resolve().parents[1]


def config_from(tmp_path, text=""):
    path = tmp_path / "pom.toml"
    path.write_text(text, encoding="utf-8")
    return Config.load(path, environ={})


def build(tmp_path, text="", **options):
    return build_printer(config_from(tmp_path, text), Options(**options))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Building must contact nothing."""
    def refuse(*args, **kwargs):
        raise AssertionError("building the Printer touched the network")
    monkeypatch.setattr("urllib.request.urlopen", refuse)


class TestTheWiring:
    def test_every_part_is_built_and_joined_in_a_conductor(self, tmp_path):
        p = build(tmp_path, '[terminal]\nmode = "none"\n')
        assert isinstance(p, Printer) and isinstance(p.conductor, Conductor) and isinstance(p.floor, FloorKeeper)
        assert isinstance(p.parts["turn_taker"], TurnTaker)
        for name in ("whisper", "scorer", "bar_keeper", "selector", "writer", "formatter", "notebook", "monitor"):
            assert p.parts[name] is not None

    def test_the_scorers_warm_up_is_built_on_the_printers_own_scorer_and_asks_the_scorers_address(self, tmp_path, monkeypatch):
        asked = []
        monkeypatch.setattr(printer_module, "answers", lambda url: asked.append(url) or True)
        p = build(tmp_path, '[scorer]\nurl = "http://127.0.0.1:9123/v1/chat/completions"\n')
        warmer = p.parts["warmer"]
        assert isinstance(warmer, ScorerWarmer) and warmer._scorer is p.parts["scorer"] and p._warmer is warmer
        assert warmer._server_answers() is True and asked == ["http://127.0.0.1:9123/v1/chat/completions"]
        assert warmer.warmed is False                      # building contacts no server: nothing has been started

    def test_the_conductor_settings_reach_the_floor_and_the_turn_taker(self, tmp_path):
        p = build(tmp_path, "[conductor]\nwriting_seconds = 500\nprinting_seconds = 400\nrecovering_seconds = 7\nfreshness_seconds = 99\n"
                            "write_attempts = 2\nwrite_pause_seconds = 4\nwrite_budget_seconds = 450\nprint_pause_seconds = 3\n"
                            "restart_pause_seconds = 8\ncheck_seconds = 0.5\n")
        floor, taker, conductor = p.floor, p.parts["turn_taker"], p.conductor
        assert (floor._writing_seconds, floor._printing_seconds, floor._recovering_seconds) == (500.0, 400.0, 7.0)
        assert (taker._freshness, taker._write_attempts, taker._write_pause, taker._write_budget, taker._print_pause) == (99.0, 2, 4.0, 450.0, 3.0)
        assert (conductor._restart_pause, conductor._check) == (8.0, 0.5)

    def test_the_oracles_own_timeout_is_what_one_write_attempt_is_allowed(self, tmp_path):
        assert build(tmp_path, "[oracle]\ntimeout = 123\n[conductor]\nwriting_seconds = 999\n").parts["turn_taker"]._write_attempt == 123.0

    def test_the_bar_keeper_and_scorer_are_shared_by_every_room_that_is_built(self, tmp_path):
        p = build(tmp_path, '[terminal]\nmode = "none"\n')
        finder1, ears1 = p.conductor._room_factory()
        finder2, ears2 = p.conductor._room_factory()
        assert finder1 is not finder2 and ears1 is not ears2                              # a fresh room each time
        assert finder1._bar_keeper is finder2._bar_keeper is p.parts["bar_keeper"]          # the same memory of the room's scores
        assert finder1._scorer is finder2._scorer is p.parts["scorer"]
        assert ears1._deck is not ears2._deck and isinstance(ears1._deck, MicDeck)

    def test_the_writer_and_the_selector_share_one_random_generator(self, tmp_path):
        p = build(tmp_path)
        assert p.parts["writer"]._rng is p.parts["selector"]._rng

    def test_the_library_is_the_real_one(self, tmp_path):
        assert p_ready(build(tmp_path))

    def test_the_selector_redraws_unfinished_exemplars_as_often_as_the_config_says(self, tmp_path):
        assert build(tmp_path, "[oracle]\nexemplar_redraws = 7\n").parts["selector"]._redraw_limit == 7
        assert build(tmp_path).parts["selector"]._redraw_limit == 50


def p_ready(p):
    return p.parts["selector"].ready


class TestWhereSoundComesFrom:
    def test_a_recording_ends_the_run_and_a_microphone_does_not(self, tmp_path):
        assert build(tmp_path, wav="x.wav").conductor._restart_room is False
        assert build(tmp_path).conductor._restart_room is True

    def test_a_recording_is_played_at_the_asked_speed(self, tmp_path):
        p = build(tmp_path, wav="x.wav", speed=0)
        _, ears = p.conductor._room_factory()
        assert type(ears._deck).__name__ == "WavDeck"

    def test_the_mac_microphone_is_used_when_asked(self, tmp_path):
        _, ears = build(tmp_path, mac_mic="default").conductor._room_factory()
        assert type(ears._deck).__name__ == "MacMicDeck"


class TestWhatItPrintsOn:
    def test_serial_is_the_default_and_comes_with_a_keyboard_watcher(self, tmp_path):
        p = build(tmp_path)
        assert isinstance(p.terminal, TerminalPort) and not isinstance(p.terminal, ConsoleTerminal)
        assert isinstance(p.parts["keyboard"], KeyboardWatcher) and p.parts["mode"] == "serial"

    def test_the_serial_port_gets_every_terminal_setting(self, tmp_path):
        p = build(tmp_path, '[terminal]\nport = "/dev/ttyACM*"\nbaud = 19200\nflow_control = "rts_cts"\nend_of_line = "lf"\n'
                            "characters_per_second = 12\nfinish_wait_seconds = 1\nechoplex = false\ntype_during_printing = true\n"
                            "stall_patience_seconds = 90\nreconnect_seconds = 0.5\n")
        t = p.terminal
        assert (t._port_name, t._baud, t._flow_control, t._end_of_line) == ("/dev/ttyACM*", 19200, "rts_cts", "\n")
        assert (t._seconds_per_character, t._finish_wait, t._echoplex, t._type_during_printing) == (1 / 12, 1, False, True)
        assert (t._stall_patience, t._reconnect) == (90.0, 0.5)

    def test_console_mode_types_on_the_screen_and_has_no_keyboard_watcher(self, tmp_path):
        p = build(tmp_path, '[terminal]\nmode = "console"\n')
        assert isinstance(p.terminal, ConsoleTerminal) and p.parts["keyboard"] is None

    def test_none_mode_has_no_terminal_and_no_keyboard(self, tmp_path):
        p = build(tmp_path, '[terminal]\nmode = "none"\n')
        assert p.terminal is None and p.parts["keyboard"] is None

    def test_a_command_line_choice_beats_the_configs_mode(self, tmp_path):
        assert build(tmp_path, '[terminal]\nmode = "serial"\n', terminal="none").terminal is None
        assert isinstance(build(tmp_path, '[terminal]\nmode = "none"\n', terminal="console").terminal, ConsoleTerminal)

    def test_the_keyboard_can_be_left_unwatched(self, tmp_path):
        assert build(tmp_path, keyboard=False).parts["keyboard"] is None

    def test_the_typed_line_scorer_gets_the_score_multiplier_and_the_bar_influence_from_the_config(self, tmp_path):
        p = build(tmp_path, '[terminal]\nmode = "serial"\n[qualifying]\ntyped_lines_score_multiplier = 2.5\ntyped_lines_bar_influence = 0.4\n')
        typed = p.parts["keyboard"]._typed_scorer
        assert (typed._score_multiplier, typed._bar_influence) == (2.5, 0.4)

    def test_the_bar_keeper_gets_the_qualifier_bar_influence_from_the_config(self, tmp_path):
        p = build(tmp_path, '[terminal]\nmode = "none"\n[qualifying]\nqualifier_bar_influence = 0.3\n')
        assert p.parts["bar_keeper"]._qualifier_bar_influence == 0.3

    def test_the_turn_taker_prints_on_that_terminal(self, tmp_path):
        p = build(tmp_path, '[terminal]\nmode = "console"\n')
        assert p.parts["turn_taker"]._terminal is p.terminal


class TestTheMovedSettingsReachTheirParts:
    """Each of these was a constant in a module; now the config says it and the entry point passes it in."""

    def test_the_starting_bar_weight(self, tmp_path):
        p = build(tmp_path, '[terminal]\nmode = "none"\n[qualifying]\nalgorithm = "decaying_quantile"\nstarting_bar_weight = 9\n')
        assert p.parts["bar_keeper"]._bar._starting_bar_weight == 9.0

    def test_the_longest_clip(self, tmp_path):
        assert build(tmp_path, "[listening]\nmax_clip_words = 12\n").parts["take_cutter"].max_words == 12

    def test_the_scorer_warm_up(self, tmp_path):
        p = build(tmp_path, "[scorer]\nwarmup_poll_seconds = 2.5\nwarmup_attempts = 7\n")
        assert (p._warmer._poll_seconds, p._warmer._attempts) == (2.5, 7)

    def test_the_service_check_interval(self, tmp_path):
        assert build(tmp_path, "[monitor]\nservice_check_seconds = 40\n")._services._every == 40.0

    def test_the_terminal_limits(self, tmp_path):
        p = build(tmp_path, '[terminal]\nmode = "serial"\nmax_typed_line_characters = 80\nrefused_character_retry_seconds = 0.5\n'
                            "wait_log_reminder_seconds = 30\n")
        assert (p.terminal._max_typed_line, p.terminal._refused_retry, p.terminal._wait_reminder) == (80, 0.5, 30.0)

    def test_the_chart(self, tmp_path):
        p = build(tmp_path, '[terminal]\nmode = "none"\n[monitor]\nenabled = true\npassword = "x"\n[chart]\nwindow_minutes = 12\nbar_line_width = 2\n')
        assert (p.site._look.window_minutes, p.site._look.bar_line_width) == (12.0, 2.0)


class TestTheMonitorMovesWithTheStream:
    """The page's beat, its chart's right edge, its ticks and its tiles all come from the stream's clock and the bar, not from the last attempt."""

    CONFIG = '[terminal]\nmode = "none"\n[monitor]\nenabled = true\npassword = "pw"\n[qualifying]\nalgorithm = "decaying_quantile"\n'

    @staticmethod
    def clock(monkeypatch, start=100.0):
        now = [start]
        monkeypatch.setattr(printer_module.time, "monotonic", lambda: now[0])
        return now

    def test_the_poll_beat_is_the_setting(self, tmp_path):
        p = build(tmp_path, '[terminal]\nmode = "none"\n[monitor]\nenabled = true\npassword = "pw"\npoll_seconds = 2.5\n')
        assert p.site._poll_seconds == 2.5

    def test_the_password_on_localhost_setting_reaches_the_site(self, tmp_path):
        off = build(tmp_path, '[monitor]\nenabled = true\npassword = "pw"\npassword_on_localhost = false\n')
        assert off.site._password_on_localhost is False
        assert build(tmp_path, '[monitor]\nenabled = true\npassword = "pw"\n').site._password_on_localhost is True

    def test_the_chart_is_given_the_bars_ticks(self, tmp_path):
        p = build(tmp_path, self.CONFIG)
        assert p.site._ticks_source == p.parts["bar_keeper"].ticks_between

    def test_before_any_room_the_stream_time_is_the_streams_start(self, tmp_path, monkeypatch):
        self.clock(monkeypatch)
        assert build(tmp_path, self.CONFIG).site._stream_now() == 0.0

    def test_once_a_room_is_listening_the_stream_time_runs_with_the_wall_clock_through_silence(self, tmp_path, monkeypatch):
        now = self.clock(monkeypatch)
        p = build(tmp_path, self.CONFIG)
        p.conductor._room_factory()
        assert p.site._stream_now() == pytest.approx(0.0)
        now[0] += 120.0
        assert p.site._stream_now() == pytest.approx(120.0)

    def test_the_stream_time_goes_on_through_a_room_built_again(self, tmp_path, monkeypatch):
        now = self.clock(monkeypatch)
        p = build(tmp_path, self.CONFIG)
        p.conductor._room_factory()
        now[0] += 300.0
        p.conductor._room_factory()
        now[0] += 30.0
        assert p.site._stream_now() == pytest.approx(330.0, abs=0.5)

    def test_the_tiles_read_the_live_bar_so_it_falls_in_a_silence_with_no_attempt(self, tmp_path, monkeypatch):
        now = self.clock(monkeypatch)
        p = build(tmp_path, self.CONFIG)
        p.conductor._room_factory()
        start = p.site._status_source().bar
        now[0] += 300.0
        later = p.site._status_source().bar
        assert start == pytest.approx(6.8) and later == pytest.approx(6.535, abs=0.01) and p.parts["bar_keeper"].counted == 0

    def test_the_window_size_is_read_live_too(self, tmp_path, monkeypatch):
        now = self.clock(monkeypatch)
        p = build(tmp_path, self.CONFIG)
        p.conductor._room_factory()
        keeper = p.parts["bar_keeper"]
        keeper.rule(7.0, 0.0)
        assert p.site._status_source().window_size == 1
        now[0] += 300.0 * 8
        assert p.site._status_source().window_size == 0                                   # eight half-lives on, its weight is under half a score

    def test_the_counts_come_from_the_attempts_noted(self, tmp_path):
        from pom.qualifying import Attempt, Ruling
        from pom.scoring import ScoredBy
        p = build(tmp_path, self.CONFIG)
        status = p.site._status_source()
        assert (status.attempts_heard, status.attempts_scored) == (0, 0)
        p.conductor._on_attempt(Attempt("Hello.", "speech", 6.0, ScoredBy.MODEL, Ruling.DID_NOT_QUALIFY, 12.0, 5.0, 3, 1))
        p.conductor._on_attempt(Attempt("Room tone.", "room_tone", None, ScoredBy.MODEL, None, 13.0, None, 3, 1))
        status = p.site._status_source()
        assert (status.attempts_heard, status.attempts_scored) == (2, 1)

    def test_the_status_also_carries_the_services_and_the_bar_description(self, tmp_path):
        status = build(tmp_path, self.CONFIG).site._status_source()
        assert status.algorithm == "decaying_quantile" and "steady tick" in status.bar_description and set(status.services) == {"whisper", "scorer", "oracle", "terminal"}


class TestTheTerminalLight:
    """`terminal` among the service lights: up if the terminal answers, DOWN if it does not, none if there is no terminal."""

    @staticmethod
    def light(tmp_path, mode_text):
        p = build(tmp_path, f'[monitor]\nenabled = true\npassword = "pw"\n[terminal]\n{mode_text}\n')
        return p, p.site._status_source().services["terminal"]

    def test_no_terminal_is_none(self, tmp_path):
        assert self.light(tmp_path, 'mode = "none"')[1] is None

    def test_the_console_stand_in_is_up(self, tmp_path):
        assert self.light(tmp_path, 'mode = "console"')[1] is True

    def test_a_serial_port_that_is_not_there_is_down(self, tmp_path):
        assert self.light(tmp_path, 'mode = "serial"\nport = "/dev/no-such-terminal-device"')[1] is False

    def test_a_terminal_being_typed_on_is_up_and_the_page_does_not_wait(self, tmp_path):
        import threading
        import time
        p = build(tmp_path, '[monitor]\nenabled = true\npassword = "pw"\n[terminal]\nmode = "serial"\nport = "/dev/no-such-terminal-device"\n')
        held, release = threading.Event(), threading.Event()

        def hold():
            with p.terminal._lock:
                held.set()
                release.wait(5)
        thread = threading.Thread(target=hold)
        thread.start()
        assert held.wait(2)
        try:
            started = time.monotonic()
            assert p.site._status_source().services["terminal"] is True and time.monotonic() - started < 1.0
        finally:
            release.set()
            thread.join(2)

    def test_the_lights_are_the_servers_and_the_terminal(self, tmp_path):
        assert set(self.light(tmp_path, 'mode = "none"')[0].site._status_source().services) == {"whisper", "scorer", "oracle", "terminal"}

    def test_the_page_says_none(self, tmp_path):
        import base64
        p, _ = self.light(tmp_path, 'mode = "none"')
        headers = {"Authorization": "Basic " + base64.b64encode(b"x:pw").decode()}
        assert 'terminal: <b class="muted">none</b>' in p.site.app.test_client().get("/", headers=headers).get_data(as_text=True)

    def test_the_site_is_told_when_listening_began(self, tmp_path):
        assert build(tmp_path, '[monitor]\nenabled = true\npassword = "pw"\n').site._stream_began_at == 0.0


class TestAServiceLightMayBeNone:
    def test_a_check_that_says_none_stays_none(self):
        assert ServiceWatch({"a": lambda: None})() == {"a": None}

    def test_the_others_are_still_up_or_down(self):
        lights = ServiceWatch({"a": lambda: 1, "b": lambda: 0, "c": lambda: True, "d": lambda: False, "e": lambda: "yes"})()
        assert lights == {"a": True, "b": False, "c": True, "d": False, "e": True}
        assert all(light is True or light is False for light in lights.values())          # real booleans, not 1 and 0

    def test_a_check_that_fails_is_down_not_none(self):
        def boom():
            raise RuntimeError("x")
        assert ServiceWatch({"a": boom})() == {"a": False}


class TestTheBarStartsWithTheStream:
    """The bar's clock runs from the stream's start, not from the first attempt: a Printer that has heard nothing yet already has a moving bar."""

    @staticmethod
    def fresh_bar(tmp_path):
        p = build(tmp_path, '[terminal]\nmode = "none"\n[qualifying]\nalgorithm = "decaying_quantile"\n')
        return p.parts["bar_keeper"]

    def test_a_fresh_printers_bar_has_moved_before_any_attempt_arrives(self, tmp_path):
        keeper = self.fresh_bar(tmp_path)
        assert keeper.counted == 0                                           # no attempt has been ruled on
        assert keeper.bar_at(0.0) == pytest.approx(6.8)                      # it starts at the starting bar
        assert keeper.bar_at(60.0) == pytest.approx(6.748, abs=0.005)        # a minute in, the first tick has come
        assert keeper.bar_at(300.0) == pytest.approx(6.535, abs=0.005)       # five minutes in, sliding toward the anchor
        assert keeper.bar_at(1800.0) < 5.95                                  # half an hour in, nearly there

    def test_the_first_attempt_after_a_long_quiet_start_faces_a_bar_that_has_already_settled(self, tmp_path):
        keeper = self.fresh_bar(tmp_path)
        assert keeper.bar_at(7200.0) == pytest.approx(5.85, abs=0.01)        # two hours of quiet: the anchor, not the starting bar

    def test_the_bar_and_the_first_room_begin_at_the_same_stream_time(self, tmp_path, monkeypatch):
        monkeypatch.setattr(printer_module.time, "monotonic", lambda: 100.0)
        p = build(tmp_path, '[terminal]\nmode = "none"\n[qualifying]\nalgorithm = "decaying_quantile"\n')
        _, first = p.conductor._room_factory()
        assert first.stream_time_at(100.0) == 0.0 and p.parts["bar_keeper"]._bar._started_at == 0.0


class TestARoomBuiltAgain:
    """The Conductor builds the listening side again if it fails; the new one's stream clock carries on from the old one's."""

    def test_the_new_rooms_clock_carries_on_from_the_old_one_plus_the_real_seconds_between(self, tmp_path, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(printer_module.time, "monotonic", lambda: now[0])
        make_room = build(tmp_path).conductor._room_factory
        _, first = make_room()
        now[0] = 1100.0                                              # the first room fails and, a hundred real seconds later, another is built
        _, second = make_room()
        assert first.stream_time_at(1000.0) == 0.0 and second.stream_time_at(1100.0) == pytest.approx(100.0)
        now[0] = 1160.0
        _, third = make_room()
        assert third.stream_time_at(1160.0) == pytest.approx(160.0)  # and through a second restart

    def test_a_first_room_begins_at_zero(self, tmp_path, monkeypatch):
        monkeypatch.setattr(printer_module.time, "monotonic", lambda: 5000.0)
        _, first = build(tmp_path).conductor._room_factory()
        assert first.stream_time_at(5000.0) == 0.0


class TestTheBootSlip:
    def test_a_real_terminal_gets_a_boot_slipper_on_the_floor_and_the_terminal(self, tmp_path):
        p = build(tmp_path, '[terminal]\nmode = "console"\n')
        slipper = p.parts["boot_slipper"]
        assert isinstance(slipper, BootSlipper) and p._slipper is slipper
        assert slipper._floor is p.floor and slipper._terminal is p.terminal and slipper._formatter is p.parts["formatter"]

    def test_the_serial_default_gets_one_too(self, tmp_path):
        assert isinstance(build(tmp_path).parts["boot_slipper"], BootSlipper)

    def test_no_terminal_means_no_slip(self, tmp_path):
        p = build(tmp_path, '[terminal]\nmode = "none"\n')
        assert p.parts["boot_slipper"] is None and p._slipper is None

    def test_a_command_line_none_means_no_slip(self, tmp_path):
        assert build(tmp_path, terminal="none").parts["boot_slipper"] is None

    def test_it_can_be_switched_off(self, tmp_path):
        assert build(tmp_path, '[terminal]\nmode = "console"\n[boot_slip]\nenabled = false\n').parts["boot_slipper"] is None

    def test_the_settings_reach_the_slipper(self, tmp_path):
        p = build(tmp_path, '[terminal]\nmode = "console"\n[conductor]\ncheck_seconds = 2\n'
                            '[boot_slip]\ntext = "HELLO {address}"\naddress_grace_seconds = 7\nretry_seconds = 3\n')
        s = p.parts["boot_slipper"]
        assert (s._template, s._grace, s._retry) == ("HELLO {address}", 7.0, 3.0)
        assert s._ears_shut == 3.0                                  # the Conductor's check interval (2 s) plus a second

    def test_the_slip_is_found_with_the_address_finder(self, tmp_path):
        from pom.conductor import AddressFinder
        s = build(tmp_path, '[terminal]\nmode = "console"\n').parts["boot_slipper"]
        assert s._find_addresses.__self__.__class__ is AddressFinder


class TestTheMonitoringSite:
    def test_it_is_off_by_default_and_says_nothing_about_a_password(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="pom.printer"):
            assert build(tmp_path).site is None
        assert [r for r in caplog.records if r.name == "pom.printer"] == []        # (the empty test config rightly makes other parts complain)

    def test_a_password_alone_does_not_switch_it_on(self, tmp_path):
        assert build(tmp_path, '[monitor]\nenabled = false\npassword = "pw"\n').site is None

    def test_it_is_built_when_enabled_with_a_password(self, tmp_path):
        p = build(tmp_path, '[monitor]\nenabled = true\npassword = "pw"\n')
        assert isinstance(p.site, MonitorSite)

    def test_enabled_without_a_password_is_a_warning_and_no_site_not_a_stop(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="pom.printer"):
            p = build(tmp_path, "[monitor]\nenabled = true\n")
        assert p.site is None and any("no password" in r.getMessage() for r in caplog.records)

    def test_the_turn_taker_records_into_the_history_the_site_reads(self, tmp_path):
        p = build(tmp_path, '[monitor]\nenabled = true\npassword = "pw"\n')
        assert p.parts["turn_taker"]._history is p.history and p.site._history is p.history

    def test_every_attempt_is_noted_for_the_chart_as_numbers(self, tmp_path):
        from pom.qualifying import Attempt, Ruling
        from pom.scoring import ScoredBy
        p = build(tmp_path)
        p.conductor._on_attempt(Attempt("A sentence.", "speech", 6.0, ScoredBy.MODEL, Ruling.DID_NOT_QUALIFY, 12.0, 5.0, 3, 1))
        p.conductor._on_attempt(Attempt("Room tone.", "room_tone", None, ScoredBy.NONE if hasattr(ScoredBy, "NONE") else ScoredBy.MODEL,
                                        None, 13.0, None, 3, 1))
        assert len(p.parts["notebook"].recent()) == 1                  # only the scored one is a point on the chart
        assert p.parts["notebook"].report.ok                           # and the unscored one was never offered to it


class TestOptions:
    def test_the_defaults(self):
        o = parse_options([])
        assert (o.wav, o.speed, o.mac_mic, o.terminal, o.keyboard) == (None, 1.0, None, None, True)
        assert o.config_path.endswith("config/pom.toml")

    def test_every_option(self):
        o = parse_options(["--config", "c.toml", "--wav", "m.wav", "--speed", "0", "--terminal", "console", "--no-keyboard"])
        assert (o.config_path, o.wav, o.speed, o.terminal, o.keyboard) == ("c.toml", "m.wav", 0.0, "console", False)

    def test_the_mac_microphone_with_and_without_a_name(self):
        assert parse_options(["--mac-mic"]).mac_mic == "default"
        assert parse_options(["--mac-mic", "MacBook Pro Microphone"]).mac_mic == "MacBook Pro Microphone"

    def test_a_recording_and_a_microphone_together_are_refused(self):
        with pytest.raises(SystemExit):
            parse_options(["--wav", "m.wav", "--mac-mic"])

    def test_an_unknown_terminal_is_refused(self):
        with pytest.raises(SystemExit):
            parse_options(["--terminal", "paper"])


class FakeSite:
    def __init__(self, starts=True):
        self.started, self.stopped, self.starts, self.port = False, False, starts, 8080
        self.report = type("R", (), {"lines": lambda self: ["site problem"]})()

    def start(self, host, port):
        self.started = True
        return self.starts

    def stop(self):
        self.stopped = True


class FakeConductor:
    def __init__(self, raises=None):
        self.ran, self.stopped, self.raises = False, 0, raises

    def run(self):
        self.ran = True
        if self.raises:
            raise self.raises

    def stop(self):
        self.stopped += 1

    def report_lines(self):
        return ["a problem"]


class FakeTerminalClose:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


def assembled(site=None, terminal=None, conductor=None, services=None, warmer=None, slipper=None):
    monitor = type("M", (), {"host": "0.0.0.0", "port": 8080})()
    return Printer(conductor or FakeConductor(), None, site, terminal, None, {"monitor": monitor, "mode": "none"},
                   services or (lambda: {"whisper": True, "scorer": False}), warmer, slipper)


class FakeWarmer:
    """Records when it was started and stopped, in the same list the conductor writes to."""

    def __init__(self, events):
        self.events = events

    def start(self):
        self.events.append("warmer started")

    def stop(self):
        self.events.append("warmer stopped")


class FakeSlipper:
    """Records when the boot slip was started and stopped, in the same list the conductor writes to."""

    def __init__(self, events):
        self.events = events

    def start(self):
        self.events.append("slipper started")

    def stop(self):
        self.events.append("slipper stopped")


class RecordingConductor(FakeConductor):
    def __init__(self, events, raises=None):
        super().__init__(raises)
        self.events = events

    def run(self):
        self.events.append("conductor ran")
        super().run()


class TestRunningAndStopping:
    def test_it_starts_the_site_runs_the_conductor_and_closes_everything(self):
        site, terminal, conductor = FakeSite(), FakeTerminalClose(), FakeConductor()
        assembled(site, terminal, conductor).run()
        assert site.started and conductor.ran and site.stopped and terminal.closed == 1

    def test_everything_is_closed_even_when_the_conductor_raises(self):
        site, terminal = FakeSite(), FakeTerminalClose()
        with pytest.raises(RuntimeError):
            assembled(site, terminal, FakeConductor(raises=RuntimeError("boom"))).run()
        assert site.stopped and terminal.closed == 1

    def test_a_site_that_will_not_start_is_dropped_and_the_run_goes_on(self, caplog):
        site, conductor = FakeSite(starts=False), FakeConductor()
        printer = assembled(site, None, conductor)
        with caplog.at_level(logging.WARNING, logger="pom.printer"):
            printer.run()
        assert conductor.ran and printer.site is None and any("did not start" in r.getMessage() for r in caplog.records)

    def test_no_site_and_no_terminal_is_fine(self):
        conductor = FakeConductor()
        assembled(None, None, conductor).run()
        assert conductor.ran

    def test_the_services_that_do_not_answer_are_in_the_log_but_do_not_stop_it(self, caplog):
        conductor = FakeConductor()
        with caplog.at_level(logging.INFO, logger="pom.printer"):
            assembled(None, None, conductor).run()
        assert conductor.ran and any("whisper up, scorer NOT answering" in r.getMessage() for r in caplog.records)

    def test_the_scorers_warm_up_starts_before_the_conductor_and_stops_after_it(self):
        events = []
        assembled(None, None, RecordingConductor(events), warmer=FakeWarmer(events)).run()
        assert events == ["warmer started", "conductor ran", "warmer stopped"]

    def test_the_warm_up_is_stopped_even_when_the_conductor_raises(self):
        events = []
        with pytest.raises(RuntimeError):
            assembled(None, None, RecordingConductor(events, RuntimeError("boom")), warmer=FakeWarmer(events)).run()
        assert events == ["warmer started", "conductor ran", "warmer stopped"]

    def test_the_boot_slip_starts_before_the_conductor_and_stops_after_it(self):
        events = []
        assembled(None, None, RecordingConductor(events), warmer=FakeWarmer(events), slipper=FakeSlipper(events)).run()
        assert events == ["warmer started", "slipper started", "conductor ran", "slipper stopped", "warmer stopped"]

    def test_the_boot_slip_is_stopped_even_when_the_conductor_raises(self):
        events = []
        with pytest.raises(RuntimeError):
            assembled(None, None, RecordingConductor(events, RuntimeError("boom")), slipper=FakeSlipper(events)).run()
        assert events == ["slipper started", "conductor ran", "slipper stopped"]

    def test_stop_stops_the_conductor_and_may_be_repeated(self):
        conductor = FakeConductor()
        printer = assembled(None, None, conductor)
        printer.stop()
        printer.stop()
        assert conductor.stopped == 2

    def test_report_lines_come_from_the_conductor(self):
        assert assembled().report_lines() == ["a problem"]


class TestMain:
    def test_it_builds_installs_the_signal_handlers_runs_and_reports(self, monkeypatch, tmp_path, caplog):
        conductor = FakeConductor()
        fake = assembled(None, None, conductor)
        fake.parts["mode"] = "none"
        built = []
        monkeypatch.setattr(printer_module, "build_printer", lambda config, options: built.append(options) or fake)
        monkeypatch.setattr(printer_module, "configure_logging", lambda *a, **k: None)
        handlers = {}
        monkeypatch.setattr(signal, "signal", lambda number, handler: handlers.update({number: handler}))
        path = tmp_path / "pom.toml"
        path.write_text("", encoding="utf-8")
        with caplog.at_level(logging.INFO):
            assert printer_module.main(["--config", str(path), "--terminal", "none"]) == 0
        assert conductor.ran and built[0].terminal == "none"
        assert set(handlers) == {signal.SIGTERM, signal.SIGINT}
        handlers[signal.SIGTERM](signal.SIGTERM, None)                     # systemd stopping the service
        assert conductor.stopped == 1
        assert any("report: a problem" in r.getMessage() for r in caplog.records)

    def test_config_problems_are_in_the_log(self, monkeypatch, tmp_path, caplog):
        fake = assembled(None, None, FakeConductor())
        monkeypatch.setattr(printer_module, "build_printer", lambda config, options: fake)
        monkeypatch.setattr(printer_module, "configure_logging", lambda *a, **k: None)
        monkeypatch.setattr(signal, "signal", lambda *a: None)
        path = tmp_path / "pom.toml"
        path.write_text("[conductor]\nwriting_seconds = -1\n", encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            printer_module.main(["--config", str(path)])
        assert any("config:" in r.getMessage() and "conductor.writing_seconds" in r.getMessage() for r in caplog.records)


class TestTheServiceChecks:
    def test_answers_and_reachable_say_yes_when_the_server_answers(self, monkeypatch):
        class Open:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Open())
        assert answers("http://127.0.0.1:8099/v1/chat/completions") and reachable("http://127.0.0.1:8110")

    @pytest.mark.parametrize("error", [urllib.error.URLError("down"), OSError("down"), ValueError("bad url")])
    def test_and_no_when_it_does_not_or_the_address_is_bad(self, monkeypatch, error):
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(error))
        assert not answers("http://127.0.0.1:8099/x") and not reachable("http://127.0.0.1:8110")

    def test_answers_asks_the_servers_health_address(self, monkeypatch):
        seen = []

        class Open:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=None: seen.append(url) or Open())
        answers("http://127.0.0.1:8099/v1/chat/completions")
        assert seen == ["http://127.0.0.1:8099/health"]

    def test_the_checks_are_made_at_most_every_so_often(self):
        now, calls = [100.0], []
        watch = ServiceWatch({"a": lambda: calls.append("a") or True}, every=15.0, clock=lambda: now[0])
        assert watch() == {"a": True} and watch() == {"a": True}
        assert calls == ["a"]
        now[0] = 116.0
        watch()
        assert calls == ["a", "a"]

    def test_a_check_that_raises_is_a_server_that_does_not_answer(self):
        watch = ServiceWatch({"a": lambda: (_ for _ in ()).throw(RuntimeError("boom")), "b": lambda: True})
        assert watch() == {"a": False, "b": True}

    def test_a_copy_is_handed_out(self):
        watch = ServiceWatch({"a": lambda: True})
        watch()["a"] = False
        assert watch() == {"a": True}
