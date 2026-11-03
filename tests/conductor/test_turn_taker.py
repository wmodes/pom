"""Tests for pom.conductor.turn_taker: one hinge taken through writing, recording and printing.

The writer and the terminal are small fakes the test scripts; the formatter, the TurnHistory and the FloorKeeper are the real ones,
because how the TurnTaker joins them is the point. One clock moves everything, and "sleeping" just moves it.
"""
import logging
from types import SimpleNamespace

import pytest

from pom.conductor import FloorKeeper, FloorState, TurnOutcome, TurnResult, TurnTaker, TurnTakerError
from pom.library import Passage
from pom.monitor import TurnHistory
from pom.printing import PortentFormatter, PrintResult
from pom.qualifying import Attempt, Ruling
from pom.scoring import ScoredBy
from pom.writing import Portent

SECRET_HINGE = "My neighbor hides a secret."
SECRET_PORTENT = "The cellar remembers every footstep."


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def attempt(text=SECRET_HINGE, score=7.5, bar=6.8):
    return Attempt(text, "speech", score, ScoredBy.MODEL, Ruling.QUALIFIED, 300.0, bar, 120, 50)


def portent(text=SECRET_PORTENT):
    return Portent(text, (Passage("book#1", "book", "verse", "an exemplar", 2),), 1.5, False)


class FakeWriter:
    """Answers each write with the next scripted reply: a Portent, None, or an exception to raise. `takes` seconds pass in each call."""

    def __init__(self, *replies, clock=None, takes=0.0):
        self.replies, self.calls, self.clock, self.takes = list(replies), [], clock, takes

    def write(self, hinge):
        self.calls.append(hinge)
        if self.clock is not None:
            self.clock.advance(self.takes)
        reply = self.replies.pop(0) if self.replies else portent()
        if isinstance(reply, Exception):
            raise reply
        return reply

    def report_lines(self):
        return ["writer: a problem the writer met"]


class FakeTerminal:
    """Answers each transmit with the next scripted reply (a PrintResult, or an exception), then with a good print. Keeps what it was given."""

    def __init__(self, *replies, on_call=None, clock=None, takes=0.0):
        self.replies, self.texts, self.on_call, self.clock, self.takes = list(replies), [], on_call, clock, takes

    def transmit(self, text, still_alive=None):
        self.texts.append(text)
        self.beats = still_alive
        if self.on_call:
            self.on_call(len(self.texts))
        if self.clock is not None:
            self.clock.advance(self.takes)
        reply = self.replies.pop(0) if self.replies else PrintResult(len(text), 12.0, True)
        if isinstance(reply, Exception):
            raise reply
        return reply


NOTHING_TYPED = PrintResult(0, 0.0, False)


class EmptyFormatter:
    def format(self, text):
        return "   \n"


def build(writer_replies=(), terminal=..., writer_takes=0.0, terminal_takes=0.0, writing=300.0, printing=50.0, recovering=3.0,
          history=True, formatter=None, terminal_replies=(), on_print=None, **options):
    clock = Clock()
    keeper = FloorKeeper(writing, printing, recovering, clock=clock)
    writer = FakeWriter(*writer_replies, clock=clock, takes=writer_takes)
    the_terminal = FakeTerminal(*terminal_replies, on_call=on_print, clock=clock, takes=terminal_takes) if terminal is ... else terminal
    the_history = TurnHistory(clock=clock) if history else None
    taker = TurnTaker(writer, formatter or PortentFormatter(), the_terminal, the_history, clock=clock, sleep=clock.advance, **options)
    return SimpleNamespace(taker=taker, keeper=keeper, clock=clock, writer=writer, terminal=the_terminal, history=the_history)


def take(rig, hinge=None):
    """Takes the floor and runs one turn."""
    turn = rig.keeper.take_floor("room")
    assert turn is not None
    return rig.taker.take_turn(turn, hinge or attempt())


class TestAGoodTurn:
    def test_the_portent_is_written_recorded_formatted_and_typed(self):
        rig = build()
        result = take(rig)
        assert result.outcome is TurnOutcome.PRINTED and rig.writer.calls == [SECRET_HINGE]
        assert len(rig.terminal.texts) == 1 and SECRET_PORTENT in rig.terminal.texts[0]
        assert rig.terminal.texts[0] == PortentFormatter().format(SECRET_PORTENT)          # what is typed is the formatted portent
        assert (result.words, result.characters, result.write_attempts, result.print_tries) == (5, len(rig.terminal.texts[0]), 1, 1)

    def test_the_turn_is_in_the_history_with_its_outcome(self):
        rig = build()
        result = take(rig)
        record = rig.history.get(result.record_id)
        assert record.hinge == SECRET_HINGE and record.portent.text == SECRET_PORTENT and record.outcome == "printed"
        assert (record.score, record.bar, record.window_size, record.heard_at) == (7.5, 6.8, 120, 300.0)

    def test_the_record_exists_before_the_typing_begins(self):
        seen = {}
        rig = build(on_print=lambda n: seen.setdefault("outcome_while_typing", rig.history.recent()[0].outcome if rig.history.recent() else "none"))
        take(rig)
        assert seen["outcome_while_typing"] is None             # there, with no outcome yet

    def test_the_floor_is_in_the_printing_state_while_typing_and_yielded_after(self):
        seen = []
        rig = build(on_print=lambda n: seen.append(rig.keeper.state))
        take(rig)
        assert seen == [FloorState.PRINTING] and rig.keeper.state is FloorState.RECOVERING

    def test_the_times_are_counted(self):
        rig = build(writer_takes=42.0, terminal_takes=17.0)
        result = take(rig)
        assert result.writing_seconds == pytest.approx(42.0) and result.printing_seconds == pytest.approx(17.0)

    def test_a_result_holds_numbers_and_a_label_only(self):
        result = take(build())
        assert isinstance(result, TurnResult)
        assert SECRET_PORTENT not in repr(result) and SECRET_HINGE not in repr(result)

    def test_outcomes_are_counted(self):
        rig = build()
        take(rig)
        rig.clock.advance(10)
        take(rig)
        assert rig.taker.outcomes[TurnOutcome.PRINTED] == 2


class TestNoPrinterByDesign:
    def test_the_turn_is_write_record_done(self):
        rig = build(terminal=None)
        result = take(rig)
        assert result.outcome is TurnOutcome.NOT_PRINTED and result.words == 5 and result.characters == 0
        record = rig.history.get(result.record_id)
        assert record.portent.text == SECRET_PORTENT and record.outcome == "not printed (no printer)"

    def test_the_floor_is_yielded_without_a_printing_state(self):
        rig = build(terminal=None)
        take(rig)
        assert rig.keeper.state is FloorState.RECOVERING and rig.keeper.report.ok

    def test_it_is_no_fault(self):
        rig = build(terminal=None)
        take(rig)
        assert rig.taker.report.ok


class TestWhenTheOracleGivesNothing:
    def test_a_quick_failure_is_tried_again_after_a_pause(self):
        rig = build(writer_replies=[None, portent()])
        result = take(rig)
        assert result.outcome is TurnOutcome.PRINTED and result.write_attempts == 2 and len(rig.writer.calls) == 2
        assert result.writing_seconds == pytest.approx(10.0)             # the one pause

    def test_it_gives_up_after_the_attempts(self):
        rig = build(writer_replies=[None, None, None, portent()])
        result = take(rig)
        assert result.outcome is TurnOutcome.NO_PORTENT and result.write_attempts == 3 and len(rig.writer.calls) == 3
        assert rig.terminal.texts == []

    def test_the_number_of_attempts_is_a_setting(self):
        rig = build(writer_replies=[None, None, None, None, portent()], write_attempts=5, write_attempt_seconds=10.0)
        assert take(rig).write_attempts == 5

    def test_with_the_usual_timings_the_budget_lets_only_four_quick_attempts_fit(self):
        rig = build(writer_replies=[None, None, None, None, portent()], write_attempts=5)
        assert take(rig).write_attempts == 4              # the fifth would not fit: 30 + 10 + 240 is over the 270 s budget

    def test_a_slow_failure_is_not_tried_again_because_a_whole_attempt_will_not_fit(self):
        rig = build(writer_replies=[None, portent()], writer_takes=240.0)       # the first attempt used up the oracle's whole timeout
        result = take(rig)
        assert result.outcome is TurnOutcome.NO_PORTENT and result.write_attempts == 1

    def test_a_failure_that_leaves_room_for_a_whole_attempt_is_tried_again(self):
        rig = build(writer_replies=[None, portent()], writer_takes=20.0)        # 20 + 10 pause + 240 = 270, exactly the budget
        assert take(rig).write_attempts == 2

    def test_one_second_over_the_budget_is_not(self):
        rig = build(writer_replies=[None, portent()], writer_takes=20.5)
        assert take(rig).write_attempts == 1

    def test_the_turn_is_recorded_with_no_portent_and_says_so(self):
        rig = build(writer_replies=[None, None, None])
        result = take(rig)
        record = rig.history.get(result.record_id)
        assert record.portent is None and record.outcome == "no portent written"

    def test_the_floor_is_yielded(self):
        rig = build(writer_replies=[None, None, None])
        take(rig)
        assert rig.keeper.state is FloorState.RECOVERING

    def test_one_failed_turn_is_a_warning_and_three_in_a_row_are_a_critical_fault(self):
        rig = build(writer_replies=[None] * 9, write_attempts=1)
        for number in range(1, 4):
            take(rig)
            rig.clock.advance(10)
            assert len(rig.taker.report.criticals) == (1 if number == 3 else 0)
        assert len(rig.taker.report.warnings) == 2 and "3 turns in a row" in rig.taker.report.criticals[0]

    def test_a_good_turn_starts_the_count_again(self):
        rig = build(writer_replies=[None, None, portent(), None, None], write_attempts=1)
        for _ in range(5):
            take(rig)
            rig.clock.advance(10)
        assert rig.taker.report.criticals == []

    def test_the_pause_and_the_budget_are_settings(self):
        rig = build(writer_replies=[None, portent()], write_pause_seconds=3.0)
        assert take(rig).writing_seconds == pytest.approx(3.0)


class TestWhenThePrinterCannotPrintYet:
    def test_it_waits_and_prints_as_soon_as_the_printer_works(self):
        rig = build(terminal_replies=[NOTHING_TYPED, NOTHING_TYPED, NOTHING_TYPED])
        result = take(rig)
        assert result.outcome is TurnOutcome.PRINTED and result.print_tries == 4 and len(rig.terminal.texts) == 4
        assert result.printing_seconds == pytest.approx(30.0)           # three pauses of 10 s

    def test_the_same_text_is_tried_each_time(self):
        rig = build(terminal_replies=[NOTHING_TYPED, NOTHING_TYPED])
        take(rig)
        assert len(set(rig.terminal.texts)) == 1

    def test_the_record_says_it_is_waiting_while_it_waits_and_printed_at_the_end(self):
        seen = []
        rig = build(terminal_replies=[NOTHING_TYPED, NOTHING_TYPED],
                    on_print=lambda n: seen.append(rig.history.recent()[0].outcome))
        take(rig)
        assert seen == [None, "waiting for the printer", "waiting for the printer"]
        assert rig.history.recent()[0].outcome == "printed"

    def test_a_long_wait_is_no_hang_because_the_heartbeat_keeps_the_watchdog_off(self):
        # five minutes of waiting against a printing limit of 50 s
        rig = build(terminal_replies=[NOTHING_TYPED] * 29, printing=50.0)
        result = take(rig)
        assert result.outcome is TurnOutcome.PRINTED and rig.keeper.overruns == 0 and rig.keeper.report.ok

    def test_the_heartbeat_is_sent_every_few_seconds_even_through_a_long_pause(self):
        # a pause of two minutes against a printing limit of 50 s: only beats every few seconds keep the watchdog off
        rig = build(terminal_replies=[NOTHING_TYPED] * 2, print_pause_seconds=120.0, printing=50.0, freshness_seconds=900.0)
        result = take(rig)
        assert result.outcome is TurnOutcome.PRINTED and rig.keeper.overruns == 0

    def test_it_stays_in_the_printing_state_while_it_waits(self):
        states = []
        rig = build(terminal_replies=[NOTHING_TYPED] * 3, on_print=lambda n: states.append(rig.keeper.state))
        take(rig)
        assert set(states) == {FloorState.PRINTING}

    def test_a_printer_that_never_comes_back_makes_the_portent_too_late(self):
        rig = build(terminal_replies=[NOTHING_TYPED] * 100)
        result = take(rig)
        assert result.outcome is TurnOutcome.TOO_LATE and result.characters == 0
        assert 30 <= result.print_tries <= 40                          # about one try every 10 s for the 6 minutes
        assert rig.clock.now - 1000.0 < 360.0                       # it stopped as soon as no further try could be made, not by waiting out the rest
        assert rig.history.recent()[0].outcome == "too late to print" and rig.history.recent()[0].portent is not None

    def test_too_late_is_a_critical_fault_that_says_how_long_and_how_many_tries(self):
        rig = build(terminal_replies=[NOTHING_TYPED] * 100)
        take(rig)
        assert len(rig.taker.report.criticals) == 1 and "360 s" in rig.taker.report.criticals[0]

    def test_the_floor_is_yielded_and_the_watchdog_never_acted(self):
        rig = build(terminal_replies=[NOTHING_TYPED] * 100)
        take(rig)
        assert rig.keeper.state is FloorState.RECOVERING and rig.keeper.overruns == 0

    def test_the_freshness_limit_is_a_setting(self):
        rig = build(terminal_replies=[NOTHING_TYPED] * 100, freshness_seconds=60.0)
        result = take(rig)
        assert result.outcome is TurnOutcome.TOO_LATE and 4 <= result.print_tries <= 7

    def test_the_freshness_is_counted_from_the_start_of_the_turn_so_a_long_write_leaves_less_waiting(self):
        rig = build(terminal_replies=[NOTHING_TYPED] * 100, writer_takes=200.0)
        result = take(rig)
        assert result.outcome is TurnOutcome.TOO_LATE and result.print_tries <= 17

    def test_a_portent_written_after_the_freshness_has_passed_is_never_tried(self):
        rig = build(writer_takes=400.0, freshness_seconds=360.0, writing=1000.0)
        result = take(rig)
        assert result.outcome is TurnOutcome.TOO_LATE and result.print_tries == 0 and rig.terminal.texts == []
        assert rig.taker.report.criticals == [] and len(rig.taker.report.warnings) == 1

    def test_a_portent_that_has_begun_typing_is_finished_even_past_the_limit(self):
        rig = build(terminal_takes=500.0)
        result = take(rig)
        assert result.outcome is TurnOutcome.PRINTED

    def test_the_wait_between_tries_is_a_setting(self):
        rig = build(terminal_replies=[NOTHING_TYPED, NOTHING_TYPED], print_pause_seconds=2.0)
        assert take(rig).printing_seconds == pytest.approx(4.0)

    def test_the_printer_working_again_for_the_next_turn_needs_nothing(self):
        rig = build(terminal_replies=[NOTHING_TYPED] * 100)
        take(rig)
        rig.terminal.replies.clear()
        rig.clock.advance(10)
        assert take(rig).outcome is TurnOutcome.PRINTED


class TestTheTerminalIsHandedTheHeartbeat:
    """Once typing has begun the terminal waits for a held or lost port itself; it sends the heartbeat so the watchdog does not act."""

    def test_a_long_wait_inside_the_terminal_does_not_cost_the_floor(self):
        rig = build(printing=50.0, freshness_seconds=900.0)

        def a_long_wait_with_beats(number):
            for _ in range(60):                     # 600 s inside the terminal, a beat every 10 s
                rig.clock.advance(10)
                assert rig.terminal.beats() is True
        rig.terminal.on_call = a_long_wait_with_beats
        result = take(rig)
        assert result.outcome is TurnOutcome.PRINTED and rig.keeper.overruns == 0

    def test_without_the_beats_the_same_wait_is_an_overrun(self):
        rig = build(printing=50.0, freshness_seconds=900.0)
        rig.terminal.on_call = lambda number: rig.clock.advance(600)
        take(rig)
        assert rig.keeper.overruns == 1

    def test_the_heartbeat_given_is_the_turns_own(self):
        rig = build()
        take(rig)
        assert rig.terminal.beats.__self__.holder == "room"


class TestWhenThePrinterFailsHalfway:
    def test_it_is_not_typed_again_so_the_start_is_not_repeated_on_the_paper(self):
        rig = build(terminal_replies=[PrintResult(40, 3.0, False)])
        result = take(rig)
        assert result.outcome is TurnOutcome.PRINT_FAILED and result.characters == 40 and len(rig.terminal.texts) == 1

    def test_it_is_a_critical_fault_and_the_record_says_so(self):
        rig = build(terminal_replies=[PrintResult(40, 3.0, False)])
        take(rig)
        assert len(rig.taker.report.criticals) == 1 and "40 characters" in rig.taker.report.criticals[0]
        assert rig.history.recent()[0].outcome == "print failed"

    def test_the_floor_is_yielded(self):
        rig = build(terminal_replies=[PrintResult(40, 3.0, False)])
        take(rig)
        assert rig.keeper.state is FloorState.RECOVERING


class TestNothingToPrint:
    def test_a_portent_that_formats_to_nothing_is_not_sent_to_the_terminal(self):
        rig = build(formatter=EmptyFormatter())
        result = take(rig)
        assert result.outcome is TurnOutcome.NOTHING_TO_PRINT and rig.terminal.texts == []
        assert rig.history.recent()[0].outcome == "nothing to print" and rig.keeper.state is FloorState.RECOVERING


class TestWhenTheFloorWasLostWhileWriting:
    def test_the_portent_is_printed_anyway_because_printing_has_priority(self):
        rig = build(writer_takes=310.0, writing=300.0, freshness_seconds=600.0)        # the writing watchdog fires at 300 s
        result = take(rig)
        assert result.outcome is TurnOutcome.PRINTED and len(rig.terminal.texts) == 1
        assert rig.keeper.overruns == 1

    def test_it_is_a_warning(self):
        rig = build(writer_takes=310.0, writing=300.0, freshness_seconds=600.0)
        take(rig)
        assert any("floor was lost" in w for w in rig.taker.report.warnings)


class TestWhenSomethingGoesWrong:
    def test_an_error_in_the_writer_ends_the_turn_with_an_error_and_yields_the_floor(self):
        rig = build(writer_replies=[RuntimeError("boom")])
        result = take(rig)
        assert result.outcome is TurnOutcome.ERROR and rig.keeper.state is FloorState.RECOVERING
        assert rig.taker.report.warnings == ["a turn failed unexpectedly (RuntimeError); the floor was yielded"]

    def test_an_error_in_the_terminal_marks_the_record_and_yields_the_floor(self):
        rig = build(terminal_replies=[OSError("gone")])
        result = take(rig)
        assert result.outcome is TurnOutcome.ERROR and result.record_id is not None
        assert rig.history.get(result.record_id).outcome == "error" and rig.keeper.state is FloorState.RECOVERING

    def test_an_error_in_the_history_does_not_stop_the_turn_from_ending_cleanly(self):
        rig = build()
        rig.history.add = lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("history broke"))
        result = take(rig)
        assert result.outcome is TurnOutcome.ERROR and rig.keeper.state is FloorState.RECOVERING

    def test_the_error_names_only_its_type_not_its_message(self):
        rig = build(writer_replies=[RuntimeError(SECRET_HINGE)])
        take(rig)
        assert SECRET_HINGE not in " ".join(rig.taker.report.lines())

    def test_something_that_is_not_a_turn_is_refused_without_raising(self):
        rig = build()
        result = rig.taker.take_turn(None, attempt())
        assert result.outcome is TurnOutcome.ERROR and rig.writer.calls == [] and len(rig.taker.report.warnings) == 1

    def test_the_next_turn_works_after_an_error(self):
        rig = build(writer_replies=[RuntimeError("boom")])
        take(rig)
        rig.clock.advance(10)
        assert take(rig).outcome is TurnOutcome.PRINTED

    def test_in_strict_mode_an_unexpected_error_raises_and_the_floor_is_still_yielded(self):
        rig = build(writer_replies=[RuntimeError("boom")], strict=True)
        turn = rig.keeper.take_floor("room")
        with pytest.raises(TurnTakerError):
            rig.taker.take_turn(turn, attempt())
        assert rig.keeper.state is FloorState.RECOVERING


class TestWithoutAHistory:
    def test_a_turn_still_works(self):
        rig = build(history=False)
        result = take(rig)
        assert result.outcome is TurnOutcome.PRINTED and result.record_id is None


class TestBadSettings:
    @pytest.mark.parametrize("name", ["freshness_seconds", "write_attempt_seconds", "write_budget_seconds", "print_pause_seconds"])
    @pytest.mark.parametrize("bad", [0, -1, float("nan"), "x", None, True])
    def test_a_bad_number_above_zero_is_the_default_with_a_warning(self, name, bad):
        taker = TurnTaker(FakeWriter(), PortentFormatter(), None, **{name: bad})
        assert len(taker.report.warnings) == 1 and name in taker.report.warnings[0]

    @pytest.mark.parametrize("bad", [0, 1.5, -2, "3", None, True])
    def test_bad_write_attempts_is_the_default_with_a_warning(self, bad):
        taker = TurnTaker(FakeWriter(), PortentFormatter(), None, write_attempts=bad)
        assert len(taker.report.warnings) == 1 and "write_attempts" in taker.report.warnings[0]

    @pytest.mark.parametrize("bad", [-1, "x", None])
    def test_a_bad_pause_is_the_default_with_a_warning(self, bad):
        taker = TurnTaker(FakeWriter(), PortentFormatter(), None, write_pause_seconds=bad)
        assert len(taker.report.warnings) == 1

    def test_a_pause_of_zero_is_allowed(self):
        assert TurnTaker(FakeWriter(), PortentFormatter(), None, write_pause_seconds=0).report.ok

    def test_a_clock_or_sleep_that_is_not_a_function_is_replaced_with_a_warning(self):
        assert len(TurnTaker(FakeWriter(), PortentFormatter(), None, clock=5).report.warnings) == 1
        assert len(TurnTaker(FakeWriter(), PortentFormatter(), None, sleep="x").report.warnings) == 1

    def test_good_settings_are_quiet(self):
        assert TurnTaker(FakeWriter(), PortentFormatter(), None).report.ok

    def test_in_strict_mode_a_bad_setting_raises(self):
        with pytest.raises(TurnTakerError):
            TurnTaker(FakeWriter(), PortentFormatter(), None, freshness_seconds=-1, strict=True)


class TestTheLogs:
    """A problem has to be readable afterwards in the logs. Numbers and labels only: never what was heard or written."""

    def lines(self, caplog):
        return [r.getMessage() for r in caplog.records if r.name == "pom.conductor.turn_taker"]

    def test_a_good_turn_is_told_in_three_lines(self, caplog):
        rig = build(writer_takes=42.0, terminal_takes=17.0)
        with caplog.at_level(logging.INFO, logger="pom.conductor.turn_taker"):
            take(rig)
        lines = self.lines(caplog)
        assert lines[0] == "turn: the room's hinge scored 7.5 against a bar of 6.8"
        assert lines[1] == "turn: a portent of 5 words was written in 42.0 s (1 attempt)"
        assert lines[2] == "turn: printed (writing 42.0 s, printing 17.0 s, 5 words, %d characters)" % len(rig.terminal.texts[0])

    def test_a_retry_of_the_oracle_is_logged(self, caplog):
        rig = build(writer_replies=[None, portent()])
        with caplog.at_level(logging.INFO, logger="pom.conductor.turn_taker"):
            take(rig)
        assert any("the oracle gave nothing (attempt 1 of 3); trying again in 10 s" in line for line in self.lines(caplog))

    def test_a_wait_for_the_printer_is_logged_at_the_start_and_every_sixth_try(self, caplog):
        rig = build(terminal_replies=[NOTHING_TYPED] * 100)
        with caplog.at_level(logging.INFO, logger="pom.conductor.turn_taker"):
            take(rig)
        waits = [line for line in self.lines(caplog) if "cannot print yet" in line]
        assert 5 <= len(waits) <= 7 and "(try 1)" in waits[0] and "(try 7)" in waits[1]

    def test_the_end_of_a_failed_turn_is_logged_with_its_outcome(self, caplog):
        rig = build(terminal_replies=[NOTHING_TYPED] * 100)
        with caplog.at_level(logging.INFO, logger="pom.conductor.turn_taker"):
            take(rig)
        assert self.lines(caplog)[-1].startswith("turn: too late to print (writing 0.0 s")

    def test_nothing_that_was_said_or_written_reaches_a_log_or_a_report(self, caplog):
        rig = build(writer_replies=[None, portent()], terminal_replies=[NOTHING_TYPED, PrintResult(10, 1.0, False)])
        with caplog.at_level(logging.DEBUG):
            take(rig)
            rig.clock.advance(10)
            rig2 = build(writer_replies=[RuntimeError(SECRET_HINGE)])
            take(rig2)
        text = caplog.text + "\n".join(rig.taker.report.lines() + rig2.taker.report.lines())
        for word in ("neighbor", "secret", "cellar", "footstep"):
            assert word not in text


class TestReporting:
    def test_report_lines_include_the_writers_problems(self):
        rig = build(writer_replies=[None, None, None])
        take(rig)
        lines = rig.taker.report_lines()
        assert "writer: a problem the writer met" in lines and any("the oracle wrote nothing" in line for line in lines)
