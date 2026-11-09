"""Tests for the TickWriter: which ticks the chart draws for a stretch, as entries, with problems reported and never raised."""
import pytest

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger
from pom.monitor.notebook_entry import NotebookEntry
from pom.monitor.tick_writer import TickWriter


def grid(interval=60.0, anchor=5.85, bar=lambda at: 6.0, asked=None):
    """A stand-in for the bar's `ticks_between`: a tick every `interval` seconds from 0 (the start's own tick included when it falls on one)."""
    def source(start, end):
        if asked is not None:
            asked.append((start, end))
        first = int(-(-start // interval))
        return [(k * interval, anchor, bar(k * interval)) for k in range(first, int(end // interval) + 1)]
    return source


def writer(source="grid", longest=300.0, head=150.0, began_at=0.0):
    """A TickWriter over the grid (or `source`), and the recorder it reports to."""
    report = FaultRecorder(strict=False, error=ValueError, logger=get_logger("test_tick_writer", False))
    return TickWriter(grid() if source == "grid" else source, longest, head, began_at, report), report


def times(entries):
    return [entry.at for entry in entries]


class TestTicksBetween:
    def test_the_ticks_after_the_start_and_up_to_the_end_are_entries(self):
        w, _ = writer(source=grid(bar=lambda at: at / 100.0))
        assert w.ticks_between(0.0, 120.0) == [NotebookEntry(60.0, 5.85, 0.6, False, "tick"), NotebookEntry(120.0, 5.85, 1.2, False, "tick")]

    def test_the_starts_own_tick_is_left_to_whatever_was_heard_there(self):
        w, _ = writer()
        assert times(w.ticks_between(60.0, 180.0)) == [120.0, 180.0]

    def test_a_tick_the_source_gives_outside_the_stretch_is_dropped(self):
        w, _ = writer(source=lambda start, end: [(5.0, 5.0, 6.0), (100.0, 5.0, 6.0), (999.0, 5.0, 6.0)])
        assert times(w.ticks_between(10.0, 200.0)) == [100.0]

    def test_without_a_source_there_are_none(self):
        w, report = writer(source=None)
        assert w.ticks_between(0.0, 1000.0) == [] and report.warnings == []

    def test_a_bar_that_is_not_a_number_is_none(self):
        w, _ = writer(source=lambda start, end: [(60.0, 5.85, None)])
        assert w.ticks_between(0.0, 100.0)[0].bar is None

    def test_a_source_that_fails_is_a_warning_and_no_ticks(self):
        def broken(start, end):
            raise RuntimeError("the bar is gone")
        w, report = writer(source=broken)
        assert w.ticks_between(0.0, 100.0) == [] and len(report.warnings) == 1
        assert "the bar is gone" not in report.warnings[0]                        # never a value from the room

    @pytest.mark.parametrize("bad", [(60.0,), (60.0, 5.0), ("60", 5.0, 6.0), (60.0, None, 6.0), (float("nan"), 5.0, 6.0), 7])
    def test_a_tick_that_is_not_numbers_is_a_warning_and_skipped(self, bad):
        w, report = writer(source=lambda start, end: [bad, (100.0, 5.0, 6.0)])
        assert times(w.ticks_between(0.0, 200.0)) == [100.0] and len(report.warnings) == 1


class TestTheFirstMinutes:
    def test_they_run_from_the_attempt_for_head_seconds(self):
        w, _ = writer(head=150.0)
        assert times(w.first_minutes(10.0)) == [60.0, 120.0]

    def test_with_no_head_there_are_none(self):
        w, _ = writer(head=0.0)
        assert w.first_minutes(10.0) == []


class TestAStretchThatIsNotASilence:
    def test_every_tick_in_it_is_drawn(self):
        w, _ = writer(longest=300.0)
        assert times(w.stretch(10.0, [], 250.0)) == [60.0, 120.0, 180.0, 240.0]

    def test_exactly_the_longest_silence_is_not_a_silence(self):
        w, _ = writer(longest=300.0)
        assert times(w.stretch(0.0, [], 300.0)) == [60.0, 120.0, 180.0, 240.0, 300.0]

    def test_it_ignores_what_was_worked_out_for_the_first_minutes(self):
        w, _ = writer(longest=300.0)
        leftover = w.first_minutes(10.0)
        assert times(w.stretch(10.0, leftover, 250.0)) == [60.0, 120.0, 180.0, 240.0]          # not the first minutes twice


class TestASilence:
    def test_it_is_its_first_minutes_and_its_last_and_nothing_between(self):
        w, _ = writer(longest=300.0, head=150.0)
        assert times(w.stretch(10.0, w.first_minutes(10.0), 50_000.0)) == [60.0, 120.0, 49_860.0, 49_920.0, 49_980.0]

    def test_the_cut_is_never_asked_of_the_source(self):
        asked = []
        w, _ = writer(source=grid(asked=asked), longest=300.0, head=150.0)
        w.stretch(10.0, [], 50_000.0)
        assert asked == [(49_850.0, 50_000.0)]

    def test_the_first_minutes_are_the_ones_it_is_given_not_worked_out_again(self):
        asked = []
        w, _ = writer(source=grid(asked=asked), longest=300.0, head=150.0)
        given = [NotebookEntry(60.0, 1.0, 2.0, False, "tick")]
        assert w.stretch(10.0, given, 50_000.0)[0] == given[0] and (10.0, 160.0) not in asked

    def test_first_minutes_that_would_reach_past_the_end_are_left_out(self):
        w, _ = writer(longest=300.0, head=400.0)                                              # a head longer than the longest silence is allowed
        assert max(times(w.stretch(10.0, w.first_minutes(10.0), 320.0))) <= 320.0

    def test_with_no_head_a_silence_has_no_ticks(self):
        w, _ = writer(longest=300.0, head=0.0)
        assert w.stretch(10.0, [], 50_000.0) == []


class TestTheQuietBeforeTheFirstAttempt:
    def test_a_stretch_with_no_attempt_yet_runs_from_the_start_of_listening(self):
        w, _ = writer(longest=300.0, began_at=0.0)
        assert times(w.stretch(None, [], 250.0)) == [60.0, 120.0, 180.0, 240.0]

    def test_a_long_one_is_a_silence_whose_first_minutes_are_worked_out_now(self):
        w, _ = writer(longest=300.0, head=150.0, began_at=0.0)
        assert times(w.stretch(None, [], 4000.0)) == [60.0, 120.0, 3900.0, 3960.0]

    def test_without_a_known_start_there_is_nothing(self):
        w, _ = writer(began_at=None)
        assert w.stretch(None, [], 4000.0) == []

    def test_a_start_that_is_not_a_number_is_not_known(self):
        w, _ = writer(began_at="yesterday")
        assert w.began_at is None and w.stretch(None, [], 4000.0) == []


class TestWhenThereIsNothingToDraw:
    def test_without_a_source(self):
        w, _ = writer(source=None)
        assert w.stretch(10.0, [], 5000.0) == []

    def test_a_stretch_of_no_length(self):
        asked = []
        w, _ = writer(source=grid(asked=asked))
        assert w.stretch(100.0, [], 100.0) == [] and asked == []

    def test_an_end_before_the_start(self):
        w, _ = writer()
        assert w.stretch(100.0, [], 50.0) == []

    @pytest.mark.parametrize("bad", [None, "now", float("nan"), True])
    def test_an_end_that_is_not_a_number(self, bad):
        w, _ = writer()
        assert w.stretch(10.0, [], bad) == []
