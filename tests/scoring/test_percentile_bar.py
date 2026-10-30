"""Tests for pom.scoring.percentile_bar: a nearest-rank percentile of a window of time, with a starting line."""
import logging
import random
from types import SimpleNamespace

import pytest

from pom.scoring.percentile_bar import (DEFAULT_MIN_SCORES, DEFAULT_PERCENTILE, DEFAULT_STARTING_BAR, DEFAULT_WINDOW_SECONDS,
                                        PercentileBar, PercentileBarError)
from pom.scoring.trigger_bar import TriggerBar


def bar(**options):
    """A bar for the tests: five scores make a window (not the default 20), the window is 60 seconds, the percentile is 100 (the
    bar is the best score of the window) and the starting line is 6.8, to keep the arithmetic short."""
    options.setdefault("percentile", 100.0)
    options.setdefault("min_scores", 5)
    options.setdefault("window_seconds", 60)
    return PercentileBar(**options)


def feed(b, scores, start=0.0, step=1.0, triggered=False):
    for i, score in enumerate(scores):
        if score is not None:
            b.add(score, start + i * step, triggered)


def warmed_up(**options):
    """A bar that has been given the scores 1 to 5 at seconds 0 to 4: the best is 5."""
    b = bar(**options)
    feed(b, [1, 2, 3, 4, 5])
    return b


class TestTheDefaults:
    def test_the_starting_values(self):
        assert (DEFAULT_PERCENTILE, DEFAULT_WINDOW_SECONDS, DEFAULT_MIN_SCORES, DEFAULT_STARTING_BAR) == (99.5, 300.0, 20, 6.8)

    def test_good_settings_are_quiet(self):
        assert PercentileBar().report.ok
        assert bar(percentile=0.001, window_seconds=0.5, min_scores=2).report.ok
        assert bar(percentile=100, starting_bar=-2).report.ok

    def test_it_is_a_trigger_bar(self):
        assert isinstance(PercentileBar(), TriggerBar)

    def test_it_ignores_no_score(self):
        assert PercentileBar().ignores(0.0) is False and PercentileBar().ignores(9.0) is False


class TestTheStartingLine:
    def test_before_any_score_the_bar_is_the_starting_line(self):
        assert bar(starting_bar=6.8).level_at(0.0) == 6.8
        assert bar(starting_bar=4.25).level_at(0.0) == 4.25

    def test_it_stays_the_starting_line_until_enough_scores_have_been_heard(self):
        b = bar(starting_bar=6.8)
        feed(b, [9, 9, 9, 9])
        assert b.level_at(4.0) == 6.8
        b.add(9, 4.0, False)
        assert b.level_at(5.0) == 9.0

    def test_the_default_needs_twenty(self):
        b = PercentileBar(percentile=100)
        feed(b, [9.0] * 19)
        assert b.level_at(19.0) == DEFAULT_STARTING_BAR
        b.add(9.0, 19.0, False)
        assert b.level_at(20.0) == 9.0

    def test_there_is_always_a_bar(self):
        assert PercentileBar().level_at(0.0) is not None

    def test_while_warming_up_the_window_size_is_all_it_holds(self):
        b = bar()
        feed(b, [1, 2, 3])
        assert b.window_size_at(3.0) == 3


class TestThePercentileBar:
    @pytest.mark.parametrize("percentile,level", [(100, 5.0), (99.3, 5.0), (81, 5.0), (80, 4.0), (60, 3.0), (50, 3.0),
                                                  (41, 3.0), (40, 2.0), (21, 2.0), (20, 1.0), (1, 1.0), (0.001, 1.0)])
    def test_the_bar_is_the_score_that_share_of_the_window_sits_at_or_below(self, percentile, level):
        assert warmed_up(percentile=percentile).level_at(5.0) == level

    def test_a_hundred_puts_the_bar_at_the_best_score(self):
        assert warmed_up(percentile=100).level_at(5.0) == 5.0

    def test_the_bar_is_a_score_from_the_window_never_an_in_between(self):
        assert warmed_up(percentile=90).level_at(5.0) == 5.0
        assert warmed_up(percentile=70).level_at(5.0) == 4.0

    def test_a_percentile_that_is_a_whole_rank_does_not_round_up_by_float_noise(self):
        b = bar(percentile=64.4, min_scores=250, window_seconds=1000)
        feed(b, list(range(1, 251)))                               # 64.4% of 250 is exactly 161
        assert b.level_at(250.0) == 161.0

    def test_ninety_nine_and_a_half_sits_below_the_best_only_with_two_hundred_scores(self):
        few, many = bar(percentile=99.5, min_scores=5, window_seconds=1000), bar(percentile=99.5, min_scores=5, window_seconds=1000)
        feed(few, list(range(1, 200)))
        feed(many, list(range(1, 201)))
        assert few.level_at(200.0) == 199.0 and many.level_at(201.0) == 199.0
        assert bar(percentile=99.5, min_scores=5, window_seconds=1000).level_at(0.0) == 6.8

    def test_the_default_percentile_is_995(self):
        b = PercentileBar(min_scores=5)
        feed(b, [1, 2, 3, 4, 5])
        assert b.level_at(5.0) == 5.0

    def test_a_lower_percentile_gives_a_lower_bar(self):
        high, low = bar(percentile=100), bar(percentile=60)
        for b in (high, low):
            feed(b, [1, 2, 3, 4, 5])
        assert low.level_at(5.0) < high.level_at(5.0)

    def test_equal_scores_give_that_score(self):
        b = bar()
        feed(b, [3.0] * 5)
        assert b.level_at(5.0) == 3.0

    def test_the_lowest_scores_count_there_is_no_floor(self):
        b = bar()
        feed(b, [0, 0, 0, 0, 0])
        assert b.level_at(5.0) == 0.0

    def test_low_scores_pull_a_middle_percentile_down(self):
        without, with_lows = bar(percentile=50), bar(percentile=50)
        feed(without, [4] * 8)
        feed(with_lows, [0, 0, 0, 0, 4, 4, 4, 4])
        assert without.level_at(8.0) == 4.0 and with_lows.level_at(8.0) == 0.0

    def test_low_scores_do_not_move_the_best(self):
        without, with_lows = bar(), bar()
        feed(without, [4] * 8)
        feed(with_lows, [0, 0, 0, 0, 4, 4, 4, 4])
        assert without.level_at(8.0) == with_lows.level_at(8.0) == 4.0

    def test_a_score_added_raises_the_best(self):
        b = warmed_up()
        level = b.level_at(5.0)
        b.add(9.0, 5.0, False)
        assert b.level_at(6.0) > level


class TestTheWindow:
    def test_scores_older_than_the_window_age_out(self):
        short, long_ = bar(percentile=50, window_seconds=10), bar(percentile=50, window_seconds=1000)
        for b in (short, long_):
            feed(b, [1] * 10)
            feed(b, [9] * 10, start=100)
        assert short.level_at(110.0) == 9.0 and long_.level_at(110.0) == 1.0

    def test_a_score_exactly_at_the_edge_of_the_window_is_still_in_it(self):
        b = bar(window_seconds=10)
        feed(b, [1, 2, 3, 4, 5, 6], start=0, step=2)
        assert b.window_size_at(10.0) == 6
        assert b.window_size_at(10.5) == 5

    def test_a_quiet_room_keeps_its_last_few_scores_however_old(self):
        b = bar(window_seconds=10)
        feed(b, [1, 2, 3, 4, 5], step=100)
        assert b.window_size_at(500.0) == 5 and b.level_at(500.0) == 5.0

    def test_a_quiet_room_takes_the_newest_scores_not_the_oldest(self):
        b = bar(window_seconds=60)
        feed(b, [9, 8, 7, 1, 2, 3, 4, 5])                    # eight scores, seconds 0 to 7, all still held
        assert b.level_at(1000.0) == 5.0                     # nothing is inside the window: the last five (1 to 5), not the first five

    def test_the_window_is_the_larger_of_the_time_and_the_count(self):
        b = bar(window_seconds=10)
        feed(b, list(range(1, 31)), step=1)
        assert b.window_size_at(30.0) == 10
        feed(b, [5], start=500)
        assert b.window_size_at(500.0) == 5

    def test_a_busy_room_and_a_quiet_one_both_get_a_stretch_of_time(self):
        busy, quiet = bar(window_seconds=60), bar(window_seconds=60)
        feed(busy, [3] * 600, step=0.5)
        feed(quiet, [3] * 20, step=15)
        assert busy.window_size_at(300.0) == 120 and quiet.window_size_at(300.0) == 5

    def test_memory_is_bounded(self):
        b = bar(window_seconds=60)
        feed(b, [3] * 10_000, step=1)
        assert len(b._recent) <= 61

    def test_a_time_asked_about_before_the_scores_still_works(self):
        b = warmed_up()
        assert b.level_at(0.0) == 5.0


class TestExcludingTriggers:
    def test_a_triggering_score_is_left_out_when_asked(self):
        b = bar(exclude_triggers=True)
        feed(b, [9, 9, 9], triggered=True)
        assert b.window_size_at(3.0) == 0

    def test_a_triggering_score_is_kept_by_default(self):
        b = bar()
        feed(b, [9, 9, 9], triggered=True)
        assert b.window_size_at(3.0) == 3

    def test_only_the_triggering_scores_are_left_out(self):
        b = bar(exclude_triggers=True)
        b.add(3.0, 0.0, False)
        b.add(9.0, 1.0, True)
        b.add(4.0, 2.0, False)
        assert b.window_size_at(2.0) == 2

    def test_leaving_a_zinger_out_keeps_the_bar_at_the_rooms_high_tide(self):
        keep, leave = bar(), bar(exclude_triggers=True)
        for b in (keep, leave):
            feed(b, [3.0] * 5)
            b.add(9.0, 5.0, True)
        assert keep.level_at(6.0) == 9.0 and leave.level_at(6.0) == 3.0

    def test_a_score_that_did_not_trigger_is_kept_either_way(self):
        b = bar(exclude_triggers=True)
        b.add(9.0, 0.0, False)
        assert b.window_size_at(0.0) == 1


class TestWhatIsIgnored:
    @pytest.mark.parametrize("junk", [None, "7", True, [5], float("nan"), float("inf")])
    def test_a_score_that_is_not_a_number_changes_nothing(self, junk):
        b = warmed_up()
        before = (b.window_size_at(5.0), b.level_at(5.0))
        b.add(junk, 5.0, False)
        assert (b.window_size_at(5.0), b.level_at(5.0)) == before

    @pytest.mark.parametrize("junk", [None, "7", True, [5], float("nan"), float("inf")])
    def test_a_time_that_is_not_a_number_changes_nothing(self, junk):
        b = warmed_up()
        before = (b.window_size_at(5.0), b.level_at(5.0))
        b.add(9.0, junk, False)
        assert (b.window_size_at(5.0), b.level_at(5.0)) == before
        assert b.level_at(junk) is None and b.window_size_at(junk) == 5

    def test_a_zero_is_a_real_score(self):
        b = bar()
        b.add(0.0, 0.0, False)
        assert b.window_size_at(0.0) == 1


class TestAgainstAPlainReference:
    """The bar against a few lines that do the same thing the slow, obvious way."""

    @staticmethod
    def reference(events, window_seconds, min_scores, percentile, starting_bar):
        held, out = [], []
        for now, score in events:
            if len(held) < min_scores:
                level = starting_bar
            else:
                window = [s for t, s in held if t >= now - window_seconds]
                if len(window) < min_scores:
                    window = [s for _, s in held[-min_scores:]]
                ranked = sorted(window)
                level = next(v for i, v in enumerate(ranked, start=1) if i * 100 >= percentile * len(ranked))
            out.append(level)
            held.append((now, score))
        return out

    @pytest.mark.parametrize("seed,gap,percentile", [(1, 1.0, 100), (2, 0.2, 99.5), (3, 40.0, 90), (4, 5.0, 50), (5, 1.0, 97.5)])
    def test_the_same_bar_as_the_reference(self, seed, gap, percentile):
        rng = random.Random(seed)
        now, events = 0.0, []
        for _ in range(400):
            now += rng.uniform(0.1, 2 * gap)
            events.append((now, round(rng.choice([1.0, 1.0, 2.0, 3.0, 3.5, 4.0, 5.0, 5.5, 6.0, 7.0, 8.0]) + rng.random(), 2)))
        b = PercentileBar(percentile, 120, 12, 6.8)
        got = []
        for when, score in events:
            got.append(b.level_at(when))
            b.add(score, when, False)
        assert got == self.reference(events, 120, 12, percentile, 6.8)


class TestDescribing:
    def test_it_says_how_it_works_in_one_line(self):
        text = PercentileBar(99.5, 300, 20, 6.8).describe()
        assert text == "the 99.5th percentile of the last 300 s of scores (at least the last 20), starting at 6.8"

    def test_it_mentions_leaving_triggers_out_only_when_it_does(self):
        assert "triggering scores left out" in PercentileBar(exclude_triggers=True).describe()
        assert "triggering scores left out" not in PercentileBar().describe()


class TestBadSettings:
    @pytest.mark.parametrize("bad", [0, -1, 100.1, 101, "99", None, True, [99], float("nan"), float("inf")])
    def test_a_bad_percentile_means_the_default_with_a_warning(self, bad):
        b = bar(percentile=bad)
        assert len(b.report.warnings) == 1 and "percentile" in b.report.warnings[0]
        assert b._percentile == DEFAULT_PERCENTILE

    @pytest.mark.parametrize("bad", [0, -1, "60", None, True, [60], float("nan"), float("inf")])
    def test_a_bad_window_means_the_default_with_a_warning(self, bad):
        b = bar(window_seconds=bad)
        assert len(b.report.warnings) == 1 and "window_seconds" in b.report.warnings[0]
        assert b._window_seconds == DEFAULT_WINDOW_SECONDS

    @pytest.mark.parametrize("bad", [0, 1, -1, 2.5, "5", None, True, [5]])
    def test_a_bad_minimum_means_the_default_with_a_warning(self, bad):
        b = bar(min_scores=bad)
        assert len(b.report.warnings) == 1 and "min_scores" in b.report.warnings[0]
        assert b._min_scores == DEFAULT_MIN_SCORES

    @pytest.mark.parametrize("bad", ["6.8", None, True, [6.8], float("nan"), float("inf")])
    def test_a_bad_starting_bar_means_the_default_with_a_warning(self, bad):
        b = bar(starting_bar=bad)
        assert len(b.report.warnings) == 1 and "starting_bar" in b.report.warnings[0]
        assert b.level_at(0.0) == DEFAULT_STARTING_BAR

    def test_a_negative_starting_bar_is_allowed(self):
        assert bar(starting_bar=-2.5).level_at(0.0) == -2.5

    @pytest.mark.parametrize("option,bad", [("percentile", 0), ("window_seconds", 0), ("min_scores", 1), ("starting_bar", None)])
    def test_strict_mode_raises(self, option, bad):
        with pytest.raises(PercentileBarError, match=option):
            bar(strict=True, **{option: bad})

    def test_strict_mode_with_good_settings_does_not_raise(self):
        assert bar(strict=True).report.ok

    def test_two_bad_settings_give_two_warnings(self):
        assert len(PercentileBar(percentile=-1, window_seconds=0).report.warnings) == 2


class TestBuildingFromSettings:
    def test_it_reads_the_settings_it_needs(self):
        settings = SimpleNamespace(percentile=95.0, window_seconds=120.0, min_scores=8, starting_bar=5.5, exclude_triggers=True, other="x")
        b = PercentileBar.from_settings(settings)
        assert (b._percentile, b._window_seconds, b._min_scores, b._starting_bar, b._exclude_triggers) == (95.0, 120.0, 8, 5.5, True)
        assert b.report.ok

    def test_bad_settings_are_replaced_as_usual(self):
        b = PercentileBar.from_settings(SimpleNamespace(percentile=500, window_seconds=120.0, min_scores=8, starting_bar=5.5,
                                                        exclude_triggers=False))
        assert b._percentile == DEFAULT_PERCENTILE and len(b.report.warnings) == 1

    def test_strict_is_passed_on(self):
        with pytest.raises(PercentileBarError):
            PercentileBar.from_settings(SimpleNamespace(percentile=500, window_seconds=1.0, min_scores=8, starting_bar=1.0,
                                                        exclude_triggers=False), strict=True)


class TestLogging:
    def test_nothing_is_logged_by_default(self, caplog):
        with caplog.at_level(logging.DEBUG):
            b = PercentileBar()
            feed(b, [1.0, 2.0, 9.0])
            b.level_at(3.0)
        assert caplog.text == ""
