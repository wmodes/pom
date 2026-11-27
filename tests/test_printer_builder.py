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

    def test_the_whole_printer_is_built_once(self, tmp_path):
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "none"\n'))
        assert builder.printer is builder.printer

    def test_the_options_default_when_none_are_given(self, tmp_path):
        assert PrinterBuilder(config_from(tmp_path)).options == Options()

    def test_the_mode_is_the_option_if_given_else_the_config(self, tmp_path):
        config = config_from(tmp_path, '[terminal]\nmode = "console"\n')
        assert PrinterBuilder(config).mode == "console"
        assert PrinterBuilder(config, Options(terminal="none")).mode == "none"


class TestTheStreamClockStartsWithTheBuilder:
    def test_the_clock_starts_when_the_builder_is_made_not_when_first_asked(self, tmp_path, monkeypatch):
        now = [100.0]
        monkeypatch.setattr("time.monotonic", lambda: now[0])
        builder = PrinterBuilder(config_from(tmp_path, '[terminal]\nmode = "none"\n'))
        now[0] += 9.0
        assert builder.timekeeper.now() == pytest.approx(9.0)


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
