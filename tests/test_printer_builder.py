"""Tests for pom.printer_builder: the PrinterBuilder builds each part the first time it is asked for and keeps it, and lets a testbench watch.

Nothing here contacts a server or opens a microphone or a port: building is plain construction. (The wiring of the whole Printer is in test_printer.py.)
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from pom.config import Config
from pom.conductor import TurnOutcome
from pom.library import Circulation
from pom.printer_builder import Options, PrinterBuilder

ROOT = Path(__file__).resolve().parents[1]


def config_from(tmp_path, text=""):
    path = tmp_path / "pom.toml"
    path.write_text(text, encoding="utf-8")
    return Config.load(path, environ={})


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Building must contact nothing."""
    def refuse(*args, **kwargs):
        raise AssertionError("building touched the network")
    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.setattr("requests.sessions.Session.request", refuse)      # every ModelComms request goes through requests


def test_the_no_network_guard_refuses_a_request_to_a_model():
    """The guard above is what lets these tests say "building contacts nothing"; every ModelComms request goes through requests."""
    import requests
    with pytest.raises(AssertionError, match="touched the network"):
        requests.get("http://127.0.0.1:8098/health", timeout=1)


def an_attempt(score=5.0):
    return SimpleNamespace(score=score, bar=4.0, heard_at=12.0, is_hinge=False)


class TestEachPartIsBuiltOnceAndOnlyWhenAsked:
    def test_a_part_asked_for_twice_is_the_same_part(self, tmp_path):
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "none"\n'))
        assert builder.scorer is builder.scorer and builder.bar_keeper is builder.bar_keeper

    def test_the_parts_that_need_a_part_share_it(self, tmp_path):
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "none"\n'))
        printer = builder.printer
        assert printer.parts["scorer"] is builder.scorer and builder.warmer._scorer is builder.scorer
        assert printer.parts["bar_keeper"] is builder.bar_keeper and printer.parts["timekeeper"] is builder.timekeeper

    def test_asking_for_the_scorer_builds_nothing_else(self, tmp_path):
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "console"\n'))
        builder.scorer
        assert set(vars(builder)) & {"terminal", "selector", "writer", "conductor", "printer"} == set()

    def test_a_part_set_before_it_is_asked_for_is_the_one_every_later_part_uses(self, tmp_path):
        """A testbench hands in its own circulation file by setting the part; the Bibliomancer built after it draws by it."""
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "none"\n'), root=ROOT)
        mostly_aphorisms = tmp_path / "circulation.json"
        mostly_aphorisms.write_text(json.dumps({"shelves": {"aphorism": {"share": 100.0, "holdings": {}}}}), encoding="utf-8")
        builder.circulation = Circulation.from_file(mostly_aphorisms)
        shares = builder.selector.shelf_shares()
        assert shares["aphorism"] > 0.8 and max(shares, key=shares.get) == "aphorism"       # every other shelf counts 1

    def test_the_bibliomancer_draws_from_the_librarys_own_circulation_by_default(self, tmp_path):
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "none"\n'), root=ROOT)
        assert len(builder.selector.shelf_shares()) > 1 and builder.catalog.layout()
        assert builder.selector.oracle is builder.config.oracle

    def test_the_scorer_is_built_on_the_builders_own_client_and_tagger(self, tmp_path):
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "none"\n'))
        assert builder.scorer._scorer_model_client is builder.scorer_client and builder.scorer._word_tagger is builder.word_tagger

    def test_typed_lines_are_scored_on_the_rooms_scorer_bar_and_clock(self, tmp_path):
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "serial"\n'))
        typed = builder.typed_line_scorer
        assert typed._scorer is builder.scorer and typed._bar_keeper is builder.bar_keeper and typed._clock == builder.timekeeper.now
        assert builder.keyboard._typed_scorer is typed

    def test_the_boot_slip_finds_addresses_with_the_builders_address_finder(self, tmp_path):
        from pom.conductor import AddressFinder
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "console"\n'))
        assert isinstance(builder.address_finder, AddressFinder) and builder.boot_slipper._find_addresses == builder.address_finder.find

    def test_an_address_finder_set_before_it_is_asked_for_fills_in_the_slip(self, tmp_path):
        """A testbench shows the slip with no network, or a given address, by setting the part."""
        config = config_from(tmp_path, '[terminal]\nmode = "console"\ncharacters_per_second = 1e9\nfinish_wait_seconds = 0\n'
                                       '[conductor]\ncheck_seconds = 0.001\n')
        builder = PrinterBuilder(config)
        builder.address_finder = SimpleNamespace(find=lambda: ["10.41.0.1"])
        assert builder.boot_slipper.run() and "10.41.0.1" in builder.boot_slipper.text

    def test_each_model_has_one_model_comms_built_from_its_address_and_the_health_timeout(self, tmp_path):
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "none"\n[modelcomms]\nhealth_timeout_seconds = 4.5\n'))
        assert builder.scorer_comms.address == builder.config.scorer.url and builder.oracle_comms.address == builder.config.oracle.url
        assert builder.scorer_comms.health_timeout_seconds == 4.5 and builder.oracle_comms.health_timeout_seconds == 4.5
        assert builder.scorer_client.comms is builder.scorer_comms
        assert builder.whisper_comms.address == builder.config.listening.whisper_url.rstrip("/") and builder.whisper_comms.health_timeout_seconds == 4.5
        assert builder.whisper.comms is builder.whisper_comms

    def test_the_oracle_is_asked_through_the_builders_oracle_comms(self, tmp_path):
        """A testbench can hand in its own ModelComms by setting the part; the oracle client built after it talks through it."""
        asked = []
        builder = PrinterBuilder(Config.load(ROOT / "config" / "pom.toml", environ={}), Options(terminal="none"), root=ROOT)   # the shipped oracle prompt
        builder.oracle_comms = SimpleNamespace(post_json=lambda body, timeout_seconds, path="": asked.append(body) or
                                               {"choices": [{"message": {"content": "The lamp keeps the hour."}, "finish_reason": "stop"}]})
        portent = builder.writer.write("the lamp hums")
        assert len(asked) == 1 and portent is not None and portent.text == "The lamp keeps the hour."

    def test_the_whole_printer_is_built_once(self, tmp_path):
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "none"\n'))
        assert builder.printer is builder.printer

    def test_the_options_default_when_none_are_given(self, tmp_path):
        assert PrinterBuilder(config_from(tmp_path)).options == Options()

    def test_the_mode_is_the_option_if_given_else_the_config(self, tmp_path):
        config = config_from(tmp_path, '[terminal]\nmode = "console"\n')
        assert PrinterBuilder(config).mode == "console"
        assert PrinterBuilder(config, Options(terminal="none")).mode == "none"


class TestABuilderWithNoBookkeeperFollowsTheConfigsStrict:
    """A test or a tool that hands no Bookkeeper gets one that is strict if the config's `strict` setting is (Design Audit 04 item 4)."""

    def test_strict_on_means_every_part_raises_on_a_warning(self, tmp_path):
        from pom.qualifying.bar_keeper import BarKeeperError
        builder = PrinterBuilder(config_from(tmp_path, 'strict = true\n[terminal]\nmode = "none"\n'))
        with pytest.raises(BarKeeperError):
            builder.bar_keeper.rule(5.0, float("nan"))         # a stream time that is not a number is a warning

    def test_strict_off_means_a_warning_is_only_logged(self, tmp_path):
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "none"\n'))
        builder.bar_keeper.rule(5.0, float("nan"))
        assert builder.bar_keeper.counted == 1


class TestTheStreamClockStartsWithTheBuilder:
    def test_the_clock_starts_when_the_builder_is_made_not_when_first_asked(self, tmp_path, monkeypatch):
        now = [100.0]
        monkeypatch.setattr("time.monotonic", lambda: now[0])
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "none"\n'))
        now[0] += 9.0
        assert builder.timekeeper.now() == pytest.approx(9.0)


class TestTheReceiversFallBackToTheStreamClock:
    def test_a_bad_time_handed_to_the_bar_keeper_becomes_the_printers_stream_time_now(self, tmp_path, monkeypatch):
        now = [100.0]
        monkeypatch.setattr("time.monotonic", lambda: now[0])
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "none"\n'))
        now[0] += 25.0
        builder.bar_keeper.rule(5.0, float("nan"))
        assert builder.bar_keeper.counted == 1 and builder.bar_keeper._last_time == pytest.approx(25.0)


class TestEveryPartThatChecksStreamTimesIsHandedThePrintersClock:
    """A stream time that is not a number becomes the stream time now: the Printer's, from its one Timekeeper (Design Audit 04 item 3)."""

    def test_the_bar_keeper_the_chart_notebook_and_the_turn_history(self, tmp_path):
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "none"\n'))
        assert builder.bar_keeper.check.stream_now == builder.timekeeper.now
        assert builder.notebook.check.stream_now == builder.timekeeper.now
        assert builder.history.check.stream_now == builder.timekeeper.now

    def test_the_room_its_ears_and_its_hinge_finder(self, tmp_path):
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "none"\n'), Options(wav="a.wav"))
        finder, ears = builder.make_room()
        assert finder.check.stream_now == builder.timekeeper.now and ears.check.stream_now == builder.timekeeper.now


class TestATestbenchCanWatch:
    def test_a_watcher_sees_each_attempt_after_the_printer_has_counted_it(self, tmp_path):
        seen = []
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "none"\n'),
                                 on_attempt=lambda attempt: seen.append((attempt, builder.status_now().attempts_heard)))
        attempt = an_attempt()
        builder.note(attempt)
        assert seen == [(attempt, 1)]                          # the tally already counted it when the watcher was told

    def test_a_watcher_sees_each_turn_after_the_printer_has_logged_it(self, tmp_path, logged):
        seen = []
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "none"\n'), on_turn=lambda attempt, result: seen.append((attempt, result)))
        attempt, result = an_attempt(), SimpleNamespace(outcome=TurnOutcome.PRINTED)
        builder.turned(attempt, result)
        assert seen == [(attempt, result)]
        assert "turn over: printed" in logged.text()

    def test_the_conductor_is_handed_the_builders_own_handlers(self, tmp_path):
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "none"\n'))
        conductor = builder.conductor
        assert conductor._on_attempt == builder.note and conductor._on_turn == builder.turned

    def test_without_a_watcher_the_printer_handles_attempts_and_turns_alone(self, tmp_path):
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "none"\n'))
        builder.note(an_attempt())
        builder.turned(an_attempt(), SimpleNamespace(outcome=TurnOutcome.PRINTED))
        assert builder.status_now().attempts_heard == 1 and builder.status_now().attempts_scored == 1

    def test_an_unscored_attempt_is_heard_but_not_scored_and_not_charted(self, tmp_path):
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "none"\n'))
        builder.note(an_attempt(score=None))
        status = builder.status_now()
        assert (status.attempts_heard, status.attempts_scored) == (1, 0)
        assert builder.notebook.earliest_at is None
