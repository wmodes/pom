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
from pom.conductor import Conductor, FloorKeeper, KeyboardWatcher, TurnTaker
from pom.listening import MicDeck
from pom.monitor import MonitorSite
from pom.printer import Options, Printer, ServiceWatch, answers, build_printer, parse_options, reachable
from pom.printing import ConsoleTerminal, TerminalPort

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
        for name in ("whisper", "scorer", "bar_keeper", "selector", "writer", "formatter", "trace", "monitor"):
            assert p.parts[name] is not None

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

    def test_the_turn_taker_prints_on_that_terminal(self, tmp_path):
        p = build(tmp_path, '[terminal]\nmode = "console"\n')
        assert p.parts["turn_taker"]._terminal is p.terminal


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
        assert len(p.parts["trace"].recent()) == 1                  # only the scored one is a point on the chart
        assert p.parts["trace"].report.ok                           # and the unscored one was never offered to it


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


def assembled(site=None, terminal=None, conductor=None, services=None):
    monitor = type("M", (), {"host": "0.0.0.0", "port": 8080})()
    return Printer(conductor or FakeConductor(), None, site, terminal, None, {"monitor": monitor, "mode": "none"},
                   services or (lambda: {"whisper": True, "scorer": False}))


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
