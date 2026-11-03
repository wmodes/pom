"""Tests for pom.conductor.keyboard_watcher: lines typed at the terminal in, attempts out.

The terminal and the typed-line scorer are small fakes (what they do is tested elsewhere); what the watcher does with them is the point.
"""
import logging
import threading

import pytest

from pom.conductor import KeyboardWatcher
from pom.conductor.keyboard_watcher import KeyboardWatcherError
from pom.fault_recorder import FaultRecorder
from pom.qualifying import Attempt, Ruling
from pom.scoring import ScoredBy

SECRET_LINE = "Tell me about my neighbor's secret."


class FakeKeyboard:
    """Hands out the scripted replies to read_line (a line, None for a quiet moment, or an exception to raise); when they run out, `on_empty` runs."""

    def __init__(self, *script):
        self.script, self.timeouts, self.on_empty = list(script), [], None

    def read_line(self, timeout=None):
        self.timeouts.append(timeout)
        if self.script:
            reply = self.script.pop(0)
            if callable(reply) and not isinstance(reply, Exception):
                reply()                                  # a hook that plays what happens meanwhile (a turn begins)
                return None
            if isinstance(reply, Exception):
                raise reply
            return reply
        if self.on_empty:
            self.on_empty()
        return None


class FakeTypedScorer:
    """Makes an Attempt of kind typed for each line, or raises if told to."""

    def __init__(self, fail_on=()):
        self.lines, self.fail_on, self.report = [], set(fail_on), FaultRecorder()

    def score(self, line):
        self.lines.append(line)
        if line in self.fail_on:
            raise RuntimeError(f"scorer broke on {line}")
        return Attempt(line, "typed", 7.0, ScoredBy.MODEL, Ruling.QUALIFIED, 12.0, 6.0, 30, 3)


def watcher_over(*script, fail_on=(), **options):
    keyboard, scorer = FakeKeyboard(*script), FakeTypedScorer(fail_on)
    sleeps = []
    watcher = KeyboardWatcher(keyboard, scorer, sleep=sleeps.append, **options)
    keyboard.on_empty = watcher.close
    return watcher, keyboard, scorer, sleeps


class TestWhatIsYielded:
    def test_each_typed_line_comes_out_as_an_attempt(self):
        watcher, _, scorer, _ = watcher_over("First line.", "Second line.")
        attempts = list(watcher.watch())
        assert [a.text for a in attempts] == ["First line.", "Second line."] and scorer.lines == ["First line.", "Second line."]
        assert all(a.kind == "typed" and a.is_hinge for a in attempts)

    def test_a_quiet_moment_gives_nothing_and_the_watching_goes_on(self):
        watcher, _, scorer, _ = watcher_over(None, None, "A line.", None)
        assert [a.text for a in watcher.watch()] == ["A line."]

    @pytest.mark.parametrize("empty", ["", "   ", "\t "])
    def test_an_empty_line_from_c_rtn_alone_is_skipped(self, empty):
        watcher, _, scorer, _ = watcher_over(empty, "Real line.")
        assert [a.text for a in watcher.watch()] == ["Real line."] and scorer.lines == ["Real line."]

    def test_something_that_is_not_a_line_is_ignored(self):
        watcher, _, scorer, _ = watcher_over(5, b"bytes", "Real line.")
        assert [a.text for a in watcher.watch()] == ["Real line."]

    def test_the_keyboard_is_asked_with_the_poll_time(self):
        watcher, keyboard, _, _ = watcher_over("A line.", poll_seconds=0.25)
        list(watcher.watch())
        assert set(keyboard.timeouts) == {0.25}


class TestPausingForATurn:
    def test_lines_that_finish_while_paused_are_thrown_away_unscored(self):
        watcher, _, scorer, _ = watcher_over("Before.", "During one.", "During two.")
        sequence = watcher.watch()
        assert next(sequence).text == "Before."
        watcher.pause()
        assert list(sequence) == []                        # the rest are read and dropped, then the script runs out and it closes
        assert scorer.lines == ["Before."] and watcher.dropped_lines == 2

    def test_after_skip_ahead_lines_are_answered_again(self):
        watcher, keyboard, scorer, _ = watcher_over("During.", lambda: watcher.skip_ahead(), "After.")
        watcher.pause()
        assert [a.text for a in watcher.watch()] == ["After."]
        assert scorer.lines == ["After."] and watcher.dropped_lines == 1

    def test_paused_says_whether_a_pause_is_on(self):
        watcher, _, _, _ = watcher_over()
        assert not watcher.paused
        watcher.pause()
        assert watcher.paused
        watcher.skip_ahead()
        assert not watcher.paused

    def test_a_line_typed_during_a_turn_is_counted_never_kept(self, caplog):
        watcher, _, _, _ = watcher_over(SECRET_LINE)
        watcher.pause()
        with caplog.at_level(logging.DEBUG):
            list(watcher.watch())
        assert watcher.dropped_lines == 1 and "neighbor" not in caplog.text and "secret" not in caplog.text

    def test_both_calls_return_at_once_and_may_be_repeated_from_another_thread(self):
        watcher, _, _, _ = watcher_over()
        done = threading.Thread(target=lambda: [watcher.pause(), watcher.pause(), watcher.skip_ahead(), watcher.skip_ahead()])
        done.start()
        done.join(timeout=2)
        assert not done.is_alive() and not watcher.paused

    def test_the_keyboard_is_still_read_while_paused_so_its_buffer_stays_empty(self):
        watcher, keyboard, _, _ = watcher_over(None, None, None)
        watcher.pause()
        list(watcher.watch())
        assert len(keyboard.timeouts) == 4                 # three scripted quiet moments, then the one that closes it


class TestClosing:
    def test_close_ends_the_watching(self):
        watcher, keyboard, _, _ = watcher_over()
        keyboard.on_empty = None
        watcher.close()
        assert list(watcher.watch()) == []

    def test_close_from_another_thread_ends_it_within_a_poll(self):
        watcher, keyboard, _, _ = watcher_over()
        keyboard.on_empty = lambda: threading.Timer(0.01, watcher.close).start()
        assert list(watcher.watch()) == []

    def test_closing_twice_is_fine(self):
        watcher, _, _, _ = watcher_over()
        watcher.close()
        watcher.close()


class TestWhenSomethingBreaks:
    def test_a_terminal_that_raises_is_a_warning_and_a_pause_and_the_watching_goes_on(self):
        watcher, _, _, sleeps = watcher_over(OSError("keyboard gone"), "Still here.")
        assert [a.text for a in watcher.watch()] == ["Still here."]
        assert watcher.report.warnings == ["the keyboard could not be read (OSError); trying again"] and sleeps == [1.0]

    def test_a_scorer_that_raises_drops_that_line_and_goes_on(self):
        watcher, _, scorer, sleeps = watcher_over("Bad.", "Good.", fail_on=["Bad."])
        assert [a.text for a in watcher.watch()] == ["Good."]
        assert watcher.report.warnings == ["a typed line could not be scored (RuntimeError); it was dropped"] and sleeps == [1.0]

    def test_the_error_names_only_its_type_not_its_message(self):
        watcher, _, _, _ = watcher_over(SECRET_LINE, fail_on=[SECRET_LINE])
        list(watcher.watch())
        assert "neighbor" not in " ".join(watcher.report.lines())

    def test_in_strict_mode_a_broken_part_raises(self):
        watcher, _, _, _ = watcher_over(OSError("gone"), strict=True)
        with pytest.raises(KeyboardWatcherError):
            list(watcher.watch())

    def test_the_error_pause_is_a_setting(self):
        watcher, _, _, sleeps = watcher_over(OSError("x"), error_pause_seconds=0.2)
        list(watcher.watch())
        assert sleeps == [0.2]


class TestBadSettings:
    @pytest.mark.parametrize("bad", [0, -1, float("nan"), "x", None, True])
    def test_a_bad_poll_is_the_default_with_a_warning(self, bad):
        watcher = KeyboardWatcher(FakeKeyboard(), FakeTypedScorer(), poll_seconds=bad)
        assert len(watcher.report.warnings) == 1 and "poll_seconds" in watcher.report.warnings[0]

    @pytest.mark.parametrize("bad", [-1, float("inf"), "x", None])
    def test_a_bad_error_pause_is_the_default_with_a_warning(self, bad):
        watcher = KeyboardWatcher(FakeKeyboard(), FakeTypedScorer(), error_pause_seconds=bad)
        assert len(watcher.report.warnings) == 1

    def test_an_error_pause_of_zero_is_allowed(self):
        assert KeyboardWatcher(FakeKeyboard(), FakeTypedScorer(), error_pause_seconds=0).report.ok

    def test_a_sleep_that_is_not_a_function_is_replaced_with_a_warning(self):
        assert len(KeyboardWatcher(FakeKeyboard(), FakeTypedScorer(), sleep="x").report.warnings) == 1

    def test_in_strict_mode_a_bad_setting_raises(self):
        with pytest.raises(KeyboardWatcherError):
            KeyboardWatcher(FakeKeyboard(), FakeTypedScorer(), poll_seconds=0, strict=True)


class TestReporting:
    def test_report_lines_include_the_typed_line_scorers_problems(self):
        watcher, _, scorer, _ = watcher_over()
        scorer.report.warn("the scorer met a problem")
        assert "warning: the scorer met a problem" in watcher.report_lines()

    def test_a_scorer_with_no_report_is_fine(self):
        class Plain:
            def score(self, line):
                raise AssertionError
        assert KeyboardWatcher(FakeKeyboard(), Plain()).report_lines() == []
