"""Tests for pom.monitor.score_trace: every scored sentence as numbers, in memory only."""
import logging
import threading

import pytest

from pom.monitor.score_trace import (DEFAULT_MAX_AGE_SECONDS, DEFAULT_MAX_POINTS, ScoreTrace, ScoreTraceError, TracePoint)


def trace(**options):
    return ScoreTrace(**options)


def feed(t, scores, start=0.0, step=1.0, bar=5.0):
    for i, score in enumerate(scores):
        t.add(score, bar, start + i * step)


class TestTheDefaults:
    def test_the_starting_values(self):
        assert (DEFAULT_MAX_AGE_SECONDS, DEFAULT_MAX_POINTS) == (3600.0, 5000)

    def test_good_settings_are_quiet(self):
        assert trace().report.ok and trace(max_age_seconds=0.001, max_points=1).report.ok

    def test_it_starts_empty(self):
        t = trace()
        assert t.recent() == [] and t.latest_at is None


class TestAdding:
    def test_a_point_keeps_the_numbers_given(self):
        t = trace()
        assert t.add(6.5, 6.2, 12.5, True) is True
        assert t.recent() == [TracePoint(12.5, 6.5, 6.2, True)]

    def test_a_point_is_not_a_trigger_unless_said_so(self):
        t = trace()
        t.add(3.0, 5.0, 1.0)
        assert t.recent()[0].triggered is False

    def test_a_sentence_with_no_bar_yet_is_kept(self):
        t = trace()
        t.add(3.0, None, 1.0)
        assert t.recent()[0].bar is None

    @pytest.mark.parametrize("bar", ["5", float("nan"), float("inf"), True, [1]])
    def test_a_bar_that_is_not_a_number_is_none(self, bar):
        t = trace()
        t.add(3.0, bar, 1.0)
        assert t.recent()[0].bar is None

    def test_numbers_are_stored_as_floats(self):
        t = trace()
        t.add(3, 5, 1)
        p = t.recent()[0]
        assert all(isinstance(v, float) for v in (p.at, p.score, p.bar))

    def test_a_zero_score_is_a_real_score(self):
        t = trace()
        assert t.add(0, 0, 0) is True and len(t.recent()) == 1

    def test_the_points_come_back_oldest_first(self):
        t = trace()
        feed(t, [1, 2, 3])
        assert [p.score for p in t.recent()] == [1.0, 2.0, 3.0]

    def test_the_list_is_a_copy(self):
        t = trace()
        feed(t, [1, 2])
        t.recent().clear()
        assert len(t.recent()) == 2

    def test_a_point_cannot_be_changed(self):
        t = trace()
        t.add(1, 1, 1)
        with pytest.raises(AttributeError):
            t.recent()[0].score = 9.0

    def test_the_newest_audio_time_is_known(self):
        t = trace()
        feed(t, [1, 2, 3], start=10.0, step=5.0)
        assert t.latest_at == 20.0


class TestWhatCannotBeKept:
    @pytest.mark.parametrize("score", [None, "7", float("nan"), float("inf"), True, [7]])
    def test_a_score_that_is_not_a_number_is_not_kept(self, score):
        t = trace()
        assert t.add(score, 5.0, 1.0) is False
        assert t.recent() == [] and len(t.report.warnings) == 1

    @pytest.mark.parametrize("at", [None, "7", float("nan"), float("inf"), True, [7]])
    def test_a_time_that_is_not_a_number_is_not_kept(self, at):
        t = trace()
        assert t.add(5.0, 5.0, at) is False
        assert t.recent() == [] and len(t.report.warnings) == 1

    def test_strict_mode_raises_for_it(self):
        with pytest.raises(ScoreTraceError):
            trace(strict=True).add("x", 5.0, 1.0)


class TestForgetting:
    def test_points_older_than_the_limit_before_the_newest_are_dropped(self):
        t = trace(max_age_seconds=60)
        feed(t, [1, 2, 3], start=0.0, step=1.0)
        t.add(4.0, 5.0, 100.0)
        assert [p.score for p in t.recent()] == [4.0]

    def test_a_point_exactly_at_the_limit_is_kept(self):
        t = trace(max_age_seconds=60)
        t.add(1.0, 5.0, 0.0)
        t.add(2.0, 5.0, 60.0)
        assert len(t.recent()) == 2
        t.add(3.0, 5.0, 60.5)
        assert [p.score for p in t.recent()] == [2.0, 3.0]

    def test_time_is_the_audio_time_not_the_wall_clock(self):
        t = trace(max_age_seconds=60)
        t.add(1.0, 5.0, 0.0)
        t.add(2.0, 5.0, 30.0)                                # a fast replay: no wall-clock time passes at all
        assert len(t.recent()) == 2

    def test_the_oldest_go_first_when_there_are_too_many(self):
        t = trace(max_points=3)
        feed(t, [1, 2, 3, 4, 5])
        assert [p.score for p in t.recent()] == [3.0, 4.0, 5.0]

    def test_the_age_limit_is_a_setting(self):
        t = trace(max_age_seconds=10)
        feed(t, [1, 2], start=0.0, step=11.0)
        assert [p.score for p in t.recent()] == [2.0]


class TestAskingForAStretch:
    def test_only_the_last_so_many_seconds_before_the_newest_point(self):
        t = trace()
        feed(t, list(range(11)), start=0.0, step=1.0)
        assert [p.score for p in t.recent(3)] == [7.0, 8.0, 9.0, 10.0]

    def test_a_stretch_longer_than_everything_gives_everything(self):
        t = trace()
        feed(t, [1, 2, 3])
        assert len(t.recent(1000)) == 3

    def test_a_stretch_of_nothing_when_empty_is_empty(self):
        assert trace().recent(30) == []

    def test_no_stretch_means_everything_kept(self):
        t = trace()
        feed(t, [1, 2, 3])
        assert len(t.recent(None)) == 3


class TestWhenSeveralThreadsUseIt:
    def test_many_adders_and_a_reader_leave_every_point_whole(self):
        t = trace(max_points=2000, max_age_seconds=10 ** 9)
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

        t = trace()
        t._lock = SpyLock()
        t.add(1, 1, 1)
        assert SpyLock.entered == 1
        t.recent()
        assert SpyLock.entered == 2
        t.latest_at
        assert SpyLock.entered == 3

    def test_old_points_are_dropped_as_new_ones_arrive_even_if_nobody_reads(self):
        t = trace(max_age_seconds=60)
        t.add(1, 1, 0.0)
        t.add(2, 1, 100.0)
        assert len(t._points) == 1


class TestBadSettings:
    @pytest.mark.parametrize("bad", [0, -1, "3600", None, True, [60], float("nan"), float("inf")])
    def test_a_bad_age_means_the_default_with_a_warning(self, bad):
        t = trace(max_age_seconds=bad)
        assert len(t.report.warnings) == 1 and "max_age_seconds" in t.report.warnings[0]
        t.add(1, 1, 0.0)
        t.add(2, 1, 3599.0)
        assert len(t.recent()) == 2
        t.add(3, 1, 3601.0)
        assert len(t.recent()) == 2                           # the first has aged out: the default hour

    @pytest.mark.parametrize("bad", [0, -1, 2.5, "5000", None, True])
    def test_a_bad_point_limit_means_the_default_with_a_warning(self, bad):
        t = trace(max_points=bad)
        assert len(t.report.warnings) == 1 and "max_points" in t.report.warnings[0]
        feed(t, [1.0] * (DEFAULT_MAX_POINTS + 5), step=0.0)
        assert len(t.recent()) == DEFAULT_MAX_POINTS

    @pytest.mark.parametrize("option,bad", [("max_age_seconds", 0), ("max_points", 0)])
    def test_strict_mode_raises(self, option, bad):
        with pytest.raises(ScoreTraceError, match=option):
            trace(strict=True, **{option: bad})

    def test_strict_mode_with_good_settings_does_not_raise(self):
        assert trace(strict=True).report.ok

    def test_two_bad_settings_give_two_warnings(self):
        assert len(trace(max_age_seconds=0, max_points=0).report.warnings) == 2


class TestNothingIsLogged:
    def test_no_value_reaches_the_log_or_the_report(self, caplog):
        caplog.set_level(logging.DEBUG)
        t = trace()
        t.add(7.123456, 6.654321, 99.0, True)
        t.add("secret", 1.0, 1.0)
        assert "7.123456" not in caplog.text + "\n".join(t.report.lines())
        assert "secret" not in caplog.text + "\n".join(t.report.lines())
