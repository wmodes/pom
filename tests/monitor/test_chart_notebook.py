"""Tests for pom.monitor.chart_notebook: every attempt as numbers, in memory only."""
import logging
import threading

import pytest

from pom.monitor.chart_notebook import (DEFAULT_MAX_AGE_SECONDS, DEFAULT_MAX_ENTRIES, ChartNotebook, ChartNotebookError, NotebookEntry)


def notebook(**options):
    return ChartNotebook(**options)


def feed(t, scores, start=0.0, step=1.0, bar=5.0):
    for i, score in enumerate(scores):
        t.add(score, bar, start + i * step)


class TestTheDefaults:
    def test_the_starting_values(self):
        assert (DEFAULT_MAX_AGE_SECONDS, DEFAULT_MAX_ENTRIES) == (3600.0, 5000)

    def test_good_settings_are_quiet(self):
        assert notebook().report.ok and notebook(max_age_seconds=0.001, max_entries=1).report.ok

    def test_it_starts_empty(self):
        t = notebook()
        assert t.recent() == [] and t.latest_at is None


class TestAdding:
    def test_a_point_keeps_the_numbers_given(self):
        t = notebook()
        assert t.add(6.5, 6.2, 12.5, True) is True
        assert t.recent() == [NotebookEntry(12.5, 6.5, 6.2, True)]

    def test_a_point_is_not_a_qualifier_unless_said_so(self):
        t = notebook()
        t.add(3.0, 5.0, 1.0)
        assert t.recent()[0].qualified is False

    def test_a_sentence_with_no_bar_yet_is_kept(self):
        t = notebook()
        t.add(3.0, None, 1.0)
        assert t.recent()[0].bar is None

    @pytest.mark.parametrize("bar", ["5", float("nan"), float("inf"), True, [1]])
    def test_a_bar_that_is_not_a_number_is_none(self, bar):
        t = notebook()
        t.add(3.0, bar, 1.0)
        assert t.recent()[0].bar is None

    def test_numbers_are_stored_as_floats(self):
        t = notebook()
        t.add(3, 5, 1)
        p = t.recent()[0]
        assert all(isinstance(v, float) for v in (p.at, p.score, p.bar))

    def test_a_zero_score_is_a_real_score(self):
        t = notebook()
        assert t.add(0, 0, 0) is True and len(t.recent()) == 1

    def test_the_points_come_back_oldest_first(self):
        t = notebook()
        feed(t, [1, 2, 3])
        assert [p.score for p in t.recent()] == [1.0, 2.0, 3.0]

    def test_the_list_is_a_copy(self):
        t = notebook()
        feed(t, [1, 2])
        t.recent().clear()
        assert len(t.recent()) == 2

    def test_a_point_cannot_be_changed(self):
        t = notebook()
        t.add(1, 1, 1)
        with pytest.raises(AttributeError):
            t.recent()[0].score = 9.0

    def test_the_newest_audio_time_is_known(self):
        t = notebook()
        feed(t, [1, 2, 3], start=10.0, step=5.0)
        assert t.latest_at == 20.0


class TestWhatCannotBeKept:
    @pytest.mark.parametrize("score", [None, "7", float("nan"), float("inf"), True, [7]])
    def test_a_score_that_is_not_a_number_is_not_kept(self, score):
        t = notebook()
        assert t.add(score, 5.0, 1.0) is False
        assert t.recent() == [] and len(t.report.warnings) == 1

    @pytest.mark.parametrize("at", [None, "7", float("nan"), float("inf"), True, [7]])
    def test_a_time_that_is_not_a_number_is_not_kept(self, at):
        t = notebook()
        assert t.add(5.0, 5.0, at) is False
        assert t.recent() == [] and len(t.report.warnings) == 1

    def test_strict_mode_raises_for_it(self):
        with pytest.raises(ChartNotebookError):
            notebook(strict=True).add("x", 5.0, 1.0)


class TestForgetting:
    def test_points_older_than_the_limit_before_the_newest_are_dropped(self):
        t = notebook(max_age_seconds=60)
        feed(t, [1, 2, 3], start=0.0, step=1.0)
        t.add(4.0, 5.0, 100.0)
        assert [p.score for p in t.recent()] == [4.0]

    def test_a_point_exactly_at_the_limit_is_kept(self):
        t = notebook(max_age_seconds=60)
        t.add(1.0, 5.0, 0.0)
        t.add(2.0, 5.0, 60.0)
        assert len(t.recent()) == 2
        t.add(3.0, 5.0, 60.5)
        assert [p.score for p in t.recent()] == [2.0, 3.0]

    def test_time_is_the_audio_time_not_the_wall_clock(self):
        t = notebook(max_age_seconds=60)
        t.add(1.0, 5.0, 0.0)
        t.add(2.0, 5.0, 30.0)                                # a fast replay: no wall-clock time passes at all
        assert len(t.recent()) == 2

    def test_the_oldest_go_first_when_there_are_too_many(self):
        t = notebook(max_entries=3)
        feed(t, [1, 2, 3, 4, 5])
        assert [p.score for p in t.recent()] == [3.0, 4.0, 5.0]

    def test_the_age_limit_is_a_setting(self):
        t = notebook(max_age_seconds=10)
        feed(t, [1, 2], start=0.0, step=11.0)
        assert [p.score for p in t.recent()] == [2.0]


class TestAskingForAStretch:
    def test_only_the_last_so_many_seconds_before_the_newest_point(self):
        t = notebook()
        feed(t, list(range(11)), start=0.0, step=1.0)
        assert [p.score for p in t.recent(3)] == [7.0, 8.0, 9.0, 10.0]

    def test_a_stretch_longer_than_everything_gives_everything(self):
        t = notebook()
        feed(t, [1, 2, 3])
        assert len(t.recent(1000)) == 3

    def test_a_stretch_of_nothing_when_empty_is_empty(self):
        assert notebook().recent(30) == []

    def test_no_stretch_means_everything_kept(self):
        t = notebook()
        feed(t, [1, 2, 3])
        assert len(t.recent(None)) == 3


class TestWhenSeveralThreadsUseIt:
    def test_many_adders_and_a_reader_leave_every_point_whole(self):
        t = notebook(max_entries=2000, max_age_seconds=10 ** 9)
        errors = []

        def adder(base):
            for i in range(100):
                if not t.add(float(i % 9), 5.0, base + i):
                    errors.append("not kept")

        def reader():
            for _ in range(200):
                t.recent(50)

        threads = [threading.Thread(target=adder, args=(n * 1000,)) for n in range(8)] + [threading.Thread(target=reader)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert not errors and len(t.recent()) == 800


class TestTheLockAndTheMemory:
    def test_every_change_and_read_takes_the_lock(self):
        class SpyLock:
            entered = 0

            def __enter__(self):
                SpyLock.entered += 1

            def __exit__(self, *exc):
                return False

        t = notebook()
        t._lock = SpyLock()
        t.add(1, 1, 1)
        assert SpyLock.entered == 1
        t.recent()
        assert SpyLock.entered == 2
        t.latest_at
        assert SpyLock.entered == 3

    def test_old_points_are_dropped_as_new_ones_arrive_even_if_nobody_reads(self):
        t = notebook(max_age_seconds=60)
        t.add(1, 1, 0.0)
        t.add(2, 1, 100.0)
        assert len(t._entries) == 1


class TestBadSettings:
    @pytest.mark.parametrize("bad", [0, -1, "3600", None, True, [60], float("nan"), float("inf")])
    def test_a_bad_age_means_the_default_with_a_warning(self, bad):
        t = notebook(max_age_seconds=bad)
        assert len(t.report.warnings) == 1 and "max_age_seconds" in t.report.warnings[0]
        t.add(1, 1, 0.0)
        t.add(2, 1, 3599.0)
        assert len(t.recent()) == 2
        t.add(3, 1, 3601.0)
        assert len(t.recent()) == 2                           # the first has aged out: the default hour

    @pytest.mark.parametrize("bad", [0, -1, 2.5, "5000", None, True])
    def test_a_bad_point_limit_means_the_default_with_a_warning(self, bad):
        t = notebook(max_entries=bad)
        assert len(t.report.warnings) == 1 and "max_entries" in t.report.warnings[0]
        feed(t, [1.0] * (DEFAULT_MAX_ENTRIES + 5), step=0.0)
        assert len(t.recent()) == DEFAULT_MAX_ENTRIES

    @pytest.mark.parametrize("option,bad", [("max_age_seconds", 0), ("max_entries", 0)])
    def test_strict_mode_raises(self, option, bad):
        with pytest.raises(ChartNotebookError, match=option):
            notebook(strict=True, **{option: bad})

    def test_strict_mode_with_good_settings_does_not_raise(self):
        assert notebook(strict=True).report.ok

    def test_two_bad_settings_give_two_warnings(self):
        assert len(notebook(max_age_seconds=0, max_entries=0).report.warnings) == 2


class TestNothingIsLogged:
    def test_no_value_reaches_the_log_or_the_report(self, caplog):
        caplog.set_level(logging.DEBUG)
        t = notebook()
        t.add(7.123456, 6.654321, 99.0, True)
        t.add("secret", 1.0, 1.0)
        assert "7.123456" not in caplog.text + "\n".join(t.report.lines())
        assert "secret" not in caplog.text + "\n".join(t.report.lines())


class TestHoldsEverything:
    """`holds_everything` is True while no attempt has been forgotten, so the chart knows the notebook reaches back to the stream's start."""

    def test_an_empty_notebook_holds_everything(self):
        assert notebook().holds_everything is True

    def test_after_some_attempts_it_still_does(self):
        t = notebook()
        feed(t, [1, 2, 3])
        assert t.holds_everything is True

    def test_an_attempt_forgotten_for_age_ends_it(self):
        t = notebook(max_age_seconds=60)
        feed(t, [1, 2, 3], start=0.0)
        t.add(4.0, 5.0, 100.0)
        assert t.holds_everything is False

    def test_an_attempt_forgotten_for_room_ends_it(self):
        t = notebook(max_entries=3)
        feed(t, [1, 2, 3])
        assert t.holds_everything is True
        t.add(4.0, 5.0, 10.0)
        assert t.holds_everything is False

    def test_it_stays_false(self):
        t = notebook(max_entries=2)
        feed(t, [1, 2, 3, 4])
        feed(t, [5], start=100.0)
        assert t.holds_everything is False

    def test_a_point_that_could_not_be_kept_does_not_count_as_forgotten(self):
        t = notebook()
        t.add("x", 5.0, 1.0)
        assert t.holds_everything is True and t.recent() == []

    def test_it_takes_the_lock(self):
        t = notebook()
        t._lock.acquire()
        asker = threading.Thread(target=lambda: t.holds_everything)
        try:
            asker.start()
            asker.join(0.2)
            assert asker.is_alive()
        finally:
            t._lock.release()
            asker.join(2)


def grid(interval=60.0, anchor=5.85, bar=lambda at: 6.0, asked=None):
    """A stand-in for the bar's `ticks_between`: a tick every `interval` seconds from 0, as the real one has (the start's own tick included when it falls on one)."""
    def source(start, end):
        if asked is not None:
            asked.append((start, end))
        first = int(-(-start // interval))
        return [(k * interval, anchor, bar(k * interval)) for k in range(first, int(end // interval) + 1)]
    return source


def with_ticks(**options):
    options.setdefault("ticks_source", grid())
    options.setdefault("began_at", 0.0)
    return ChartNotebook(**options)


class TestTicksAreEntriesToo:
    """The ticks since the previous attempt are written down, with the bar at each, when the next attempt arrives."""

    def test_an_attempt_writes_the_ticks_since_the_one_before(self):
        t = with_ticks()
        t.add(3.0, 5.0, 10.0)
        t.add(4.0, 5.0, 200.0)
        assert [tick.at for tick in t.ticks()] == [60.0, 120.0, 180.0]

    def test_a_tick_carries_the_anchor_score_and_the_bar_at_that_tick(self):
        t = with_ticks(ticks_source=grid(anchor=5.85, bar=lambda at: 7.0 - at / 100.0))
        t.add(3.0, 5.0, 10.0)
        t.add(4.0, 5.0, 130.0)
        assert t.ticks() == [NotebookEntry(60.0, 5.85, 6.4, False, "tick"), NotebookEntry(120.0, 5.85, 5.8, False, "tick")]

    def test_a_tick_is_never_a_qualifier(self):
        t = with_ticks()
        t.add(9.0, 5.0, 10.0, True)
        t.add(9.0, 5.0, 100.0, True)
        assert not any(tick.qualified for tick in t.ticks())

    def test_a_tick_with_no_bar_yet_is_kept_with_none(self):
        t = with_ticks(ticks_source=grid(bar=lambda at: None))
        t.add(3.0, 5.0, 10.0)
        t.add(3.0, 5.0, 100.0)
        assert [tick.bar for tick in t.ticks()] == [None]

    def test_the_attempts_and_the_ticks_are_kept_apart(self):
        t = with_ticks()
        t.add(3.0, 5.0, 10.0)
        t.add(4.0, 5.0, 200.0)
        assert [e.kind for e in t.recent()] == ["attempt", "attempt"] and {e.kind for e in t.ticks()} == {"tick"}

    def test_the_ticks_come_before_the_attempt_in_time_order(self):
        t = with_ticks()
        t.add(3.0, 5.0, 10.0)
        t.add(4.0, 5.0, 200.0)
        assert [e.at for e in t._entries] == sorted(e.at for e in t._entries)

    def test_the_source_is_asked_from_the_previous_attempt_to_this_one(self):
        asked = []
        t = with_ticks(ticks_source=grid(asked=asked))
        t.add(3.0, 5.0, 10.0)
        t.add(4.0, 5.0, 200.0)
        assert asked == [(0.0, 10.0), (10.0, 200.0)]

    def test_before_the_first_attempt_it_starts_from_the_streams_start(self):
        t = with_ticks(began_at=0.0)
        t.add(3.0, 5.0, 150.0)
        assert [tick.at for tick in t.ticks()] == [60.0, 120.0]

    def test_with_no_start_known_the_first_attempt_has_no_ticks_before_it(self):
        asked = []
        t = ChartNotebook(ticks_source=grid(asked=asked))
        t.add(3.0, 5.0, 150.0)
        assert t.ticks() == [] and asked == []

    def test_a_tick_that_falls_on_an_attempt_is_written_once(self):
        t = with_ticks()
        t.add(3.0, 5.0, 60.0)
        t.add(3.0, 5.0, 190.0)
        assert [tick.at for tick in t.ticks()] == [60.0, 120.0, 180.0]

    def test_a_tick_that_falls_on_the_attempt_is_written_before_it(self):
        t = with_ticks()
        t.add(3.0, 5.0, 10.0)
        t.add(3.0, 5.0, 120.0)
        assert [tick.at for tick in t.ticks()] == [60.0, 120.0]

    def test_without_a_ticks_source_there_are_none(self):
        t = ChartNotebook(began_at=0.0)
        t.add(3.0, 5.0, 10.0)
        t.add(3.0, 5.0, 200.0)
        assert t.ticks() == []

    def test_a_tick_is_not_a_latest_attempt(self):
        t = with_ticks()
        t.add(3.0, 5.0, 10.0)
        t.add(4.0, 5.0, 200.0)
        assert t.latest_at == 200.0

    def test_an_attempt_that_is_refused_writes_no_ticks(self):
        asked = []
        t = with_ticks(ticks_source=grid(asked=asked))
        t.add(3.0, 5.0, 10.0)
        assert t.add("x", 5.0, 200.0) is False
        assert t.ticks() == [] and len(asked) == 1


class TestNoTicksForASilence:
    """A stretch longer than the longest silence is drawn as a break, with what is inside hidden, so nothing is written for it."""

    def test_a_silence_between_attempts_gets_no_ticks(self):
        t = with_ticks(longest_silence_seconds=300.0)
        t.add(3.0, 5.0, 10.0)
        t.add(4.0, 5.0, 10.0 + 53_000.0)                           # fifteen hours quiet
        assert t.ticks() == []

    def test_and_the_bar_is_not_even_asked(self):
        asked = []
        t = with_ticks(longest_silence_seconds=300.0, ticks_source=grid(asked=asked))
        t.add(3.0, 5.0, 10.0)
        t.add(4.0, 5.0, 5000.0)
        assert asked == [(0.0, 10.0)]

    def test_exactly_the_longest_silence_is_not_a_silence(self):
        t = with_ticks(longest_silence_seconds=300.0)
        t.add(3.0, 5.0, 10.0)
        t.add(4.0, 5.0, 310.0)
        assert [tick.at for tick in t.ticks()] == [60.0, 120.0, 180.0, 240.0, 300.0]

    def test_a_second_over_is(self):
        t = with_ticks(longest_silence_seconds=300.0)
        t.add(3.0, 5.0, 10.0)
        t.add(4.0, 5.0, 311.0)
        assert t.ticks() == []

    def test_a_quiet_start_is_a_silence_too(self):
        t = with_ticks(longest_silence_seconds=300.0, began_at=0.0)
        t.add(3.0, 5.0, 4000.0)
        assert t.ticks() == []

    def test_after_a_silence_the_next_gap_gets_its_ticks_again(self):
        t = with_ticks(longest_silence_seconds=300.0)
        t.add(3.0, 5.0, 10.0)
        t.add(3.0, 5.0, 5000.0)
        t.add(3.0, 5.0, 5200.0)
        assert [tick.at for tick in t.ticks()] == [5040.0, 5100.0, 5160.0]

    def test_two_attempts_at_the_same_moment_do_not_ask_the_bar_again(self):
        asked = []
        t = with_ticks(ticks_source=grid(asked=asked))
        t.add(3.0, 5.0, 100.0)
        t.add(4.0, 5.0, 100.0)
        assert asked == [(0.0, 100.0)]

    def test_the_longest_silence_is_the_one_given(self):
        t = with_ticks(longest_silence_seconds=100.0)
        t.add(3.0, 5.0, 10.0)
        t.add(3.0, 5.0, 200.0)
        assert t.ticks() == []


class TestTicksAndTheLimits:
    def test_ticks_are_forgotten_with_the_attempts_by_age(self):
        t = with_ticks(max_age_seconds=100.0)
        t.add(3.0, 5.0, 10.0)
        t.add(3.0, 5.0, 250.0)                                      # ticks 60 .. 240 are written; the newest entry is 250, so those before 150 go
        assert [tick.at for tick in t.ticks()] == [180.0, 240.0]

    def test_in_a_long_silence_nothing_is_forgotten_ticks_included(self):
        t = with_ticks(longest_silence_seconds=1000.0)
        t.add(3.0, 5.0, 10.0)
        t.add(3.0, 5.0, 400.0)
        before = (len(t.recent()), len(t.ticks()))
        assert before == (2, 6) and (len(t.recent()), len(t.ticks())) == before

    def test_the_limit_counts_ticks_too(self):
        t = with_ticks(max_entries=4, longest_silence_seconds=1000.0)
        t.add(3.0, 5.0, 10.0)
        t.add(3.0, 5.0, 400.0)
        assert len(t._entries) == 4 and t.holds_everything is False

    def test_it_holds_everything_while_nothing_is_forgotten_ticks_included(self):
        t = with_ticks()
        t.add(3.0, 5.0, 10.0)
        t.add(3.0, 5.0, 200.0)
        assert t.holds_everything is True


class TestWhenTheBarCannotBeAsked:
    def test_a_failing_source_is_a_warning_and_the_attempt_is_kept(self):
        def broken(start, end):
            raise RuntimeError("the secret in the room")
        t = with_ticks(ticks_source=broken, began_at=None)
        t.add(3.0, 5.0, 10.0)
        assert t.add(4.0, 5.0, 200.0) is True and t.ticks() == [] and len(t.recent()) == 2
        assert t.report.warnings and "secret" not in " ".join(t.report.warnings)

    def test_in_strict_mode_it_raises(self):
        def broken(start, end):
            raise RuntimeError("x")
        t = with_ticks(ticks_source=broken, strict=True, began_at=None)
        t.add(3.0, 5.0, 10.0)
        with pytest.raises(ChartNotebookError):
            t.add(4.0, 5.0, 200.0)

    def test_a_tick_without_a_number_is_skipped_with_a_warning(self):
        t = with_ticks(ticks_source=lambda start, end: [("x", 5.0, 6.0), (60.0, "y", 6.0), (90.0, 5.0, 6.0), "junk"], began_at=None)
        t.add(3.0, 5.0, 10.0)
        t.add(4.0, 5.0, 200.0)
        assert [tick.at for tick in t.ticks()] == [90.0] and len(t.report.warnings) == 3

    def test_ticks_outside_the_stretch_asked_for_are_not_kept(self):
        t = with_ticks(ticks_source=lambda start, end: [(5.0, 5.0, 6.0), (100.0, 5.0, 6.0), (999.0, 5.0, 6.0)], began_at=None)
        t.add(3.0, 5.0, 10.0)
        t.add(4.0, 5.0, 200.0)
        assert [tick.at for tick in t.ticks()] == [100.0]

    def test_the_bar_is_asked_without_the_lock_held(self):
        seen = []
        t = ChartNotebook(began_at=0.0)
        t._ticks_source = lambda start, end: seen.append(len(t.recent())) or []          # would hang if `add` still held the lock
        asker = threading.Thread(target=lambda: t.add(3.0, 5.0, 10.0))
        asker.start()
        asker.join(2)
        assert not asker.is_alive() and seen == [0]

    @pytest.mark.parametrize("bad", [0, -5, "x", None, float("nan")])
    def test_a_bad_longest_silence_is_a_warning_and_the_fallback(self, bad):
        t = with_ticks(longest_silence_seconds=bad)
        assert len(t.report.warnings) == 1 and "longest_silence_seconds" in t.report.warnings[0]
        t.add(3.0, 5.0, 10.0)
        t.add(3.0, 5.0, 310.0)
        t.add(3.0, 5.0, 700.0)
        assert [tick.at for tick in t.ticks()][-1] == 300.0 and 360.0 not in [tick.at for tick in t.ticks()]
