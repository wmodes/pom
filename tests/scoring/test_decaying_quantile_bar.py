"""Tests for pom.scoring.decaying_quantile_bar: a weighted, interpolated high quantile with a fading starting line."""
import logging
import math
from types import SimpleNamespace

import pytest

from pom.scoring.decaying_quantile_bar import (DEFAULT_HALF_LIFE_SECONDS, DEFAULT_PERCENTILE, DEFAULT_STARTING_BAR,
                                               DEFAULT_STARTING_WEIGHT, DecayingQuantileBar, DecayingQuantileBarError)
from pom.scoring.trigger_bar import TriggerBar

TINY = 1e-12            # a starting weight so small that the starting line does not affect the result


def bar(**options):
    """A bar with a very long half-life (no decay worth speaking of) and no starting line, unless a test says otherwise."""
    options.setdefault("half_life_seconds", 1e12)
    options.setdefault("starting_weight", TINY)
    return DecayingQuantileBar(**options)


def feed(b, scores, start=0.0, step=1.0, triggered=False):
    for i, score in enumerate(scores):
        b.add(score, start + i * step, triggered)


def line_through_the_shares(scores, percentile):
    """The percentile of equal-weight scores worked out the plain way: score k (counting from 1) covers the stretch from k-1 to k,
    the target is percentile% of n, and the value runs in a straight line from the previous score to the one whose stretch holds it."""
    ranked = sorted(scores)
    target = percentile / 100.0 * len(ranked)
    k = max(1, math.ceil(target))
    if k == 1:
        return ranked[0]
    return ranked[k - 2] + (target - (k - 1)) * (ranked[k - 1] - ranked[k - 2])


class TestTheDefaults:
    def test_the_starting_values(self):
        assert (DEFAULT_PERCENTILE, DEFAULT_HALF_LIFE_SECONDS, DEFAULT_STARTING_BAR, DEFAULT_STARTING_WEIGHT) == (99.5, 300.0, 6.8, 20.0)

    def test_good_settings_are_quiet(self):
        assert DecayingQuantileBar().report.ok
        assert DecayingQuantileBar(percentile=100, half_life_seconds=0.001, starting_bar=-3, starting_weight=0.001).report.ok

    def test_it_is_a_trigger_bar(self):
        assert isinstance(DecayingQuantileBar(), TriggerBar)

    def test_it_ignores_no_score(self):
        assert DecayingQuantileBar().ignores(0.0) is False and DecayingQuantileBar().ignores(9.0) is False


class TestTheStartingLine:
    def test_before_any_score_the_bar_is_the_starting_line(self):
        assert DecayingQuantileBar(starting_bar=6.8).level_at(0.0) == pytest.approx(6.8)
        assert DecayingQuantileBar(starting_bar=5.0, percentile=50).level_at(100.0) == pytest.approx(5.0)

    def test_the_starting_line_is_settable(self):
        assert DecayingQuantileBar(starting_bar=4.25).level_at(0.0) == pytest.approx(4.25)

    def test_there_is_always_a_bar(self):
        assert DecayingQuantileBar().level_at(0.0) is not None

    def test_one_early_score_barely_moves_the_bar_off_the_starting_line(self):
        b = DecayingQuantileBar(starting_bar=6.8)
        b.level_at(0.0)
        b.add(9.0, 0.0, False)
        assert b.level_at(0.1) == pytest.approx((20 * 6.8 + 1 * 9.0) / 21, abs=0.01)     # 20 parts starting line to 1 part score

    def test_low_scores_pull_the_bar_down_from_the_starting_line_as_they_arrive(self):
        b = DecayingQuantileBar(starting_bar=6.8, percentile=50, half_life_seconds=1e12)
        level_before = b.level_at(0.0)
        feed(b, [2.0] * 200)
        assert level_before == pytest.approx(6.8) and b.level_at(200.0) < 3.0

    def test_the_starting_line_fades_with_the_half_life(self):
        b = DecayingQuantileBar(starting_bar=9.0, percentile=75, half_life_seconds=100, starting_weight=20)
        b.level_at(0.0)
        feed(b, [2.0] * 20, start=0.0, step=0.001)           # 20 real scores heard at once: the line holds as much weight
        assert b.level_at(0.02) == pytest.approx(5.5, abs=0.1)         # half and half: (9 + 2) / 2
        assert b.level_at(1000.0) == pytest.approx(9.0)      # ten half-lives later every real score is gone

    def test_while_scores_keep_arriving_the_starting_line_fades_away(self):
        b = DecayingQuantileBar(starting_bar=9.0, percentile=50, half_life_seconds=100, starting_weight=20)
        b.level_at(0.0)
        feed(b, [2.0] * 50, start=500.0, step=0.01)          # five half-lives later: the line has 20/32 of one score's weight
        assert b.level_at(501.0) == pytest.approx(2.0, abs=0.35)

    def test_the_starting_line_counts_from_the_first_use_not_from_each_use(self):
        b = DecayingQuantileBar(starting_bar=9.0, percentile=50, half_life_seconds=100, starting_weight=20)
        b.level_at(0.0)
        b.add(2.0, 500.0, False)
        b.level_at(500.0)                                     # asked again: the line must not start over
        feed(b, [2.0] * 50, start=500.0, step=0.01)
        assert b.level_at(501.0) == pytest.approx(2.0, abs=0.35)

    def test_after_a_long_silence_the_bar_returns_to_the_starting_line(self):
        b = DecayingQuantileBar(starting_bar=6.8, half_life_seconds=60)
        feed(b, [1.0, 2.0, 3.0, 4.0, 9.0], step=1.0)
        assert b.level_at(10 * 60 + 100) == pytest.approx(6.8)

    def test_the_starting_weight_sets_how_long_the_line_lasts(self):
        light = DecayingQuantileBar(starting_bar=9.0, percentile=50, half_life_seconds=1e12, starting_weight=1)
        heavy = DecayingQuantileBar(starting_bar=9.0, percentile=50, half_life_seconds=1e12, starting_weight=500)
        for b in (light, heavy):
            b.level_at(0.0)
            feed(b, [2.0] * 50)
        assert light.level_at(50.0) < 2.5 and heavy.level_at(50.0) > 8.0

    def test_the_line_counts_from_the_first_time_the_bar_is_used(self):
        b = DecayingQuantileBar(starting_bar=9.0, percentile=50, half_life_seconds=100, starting_weight=20)
        b.add(2.0, 5000.0, False)                            # the bar's first use, at 5000 s
        assert b.level_at(5000.0) > 5.0                      # the line is fresh then, not 5000 s old


class TestTheWeightedQuantile:
    @pytest.mark.parametrize("percentile", [100, 99.5, 99, 90, 75, 50, 25, 10, 1])
    def test_equal_weights_match_the_plain_interpolated_percentile(self, percentile):
        scores = [3.1, 1.0, 5.5, 2.2, 7.3, 4.4, 1.0, 6.1, 3.9, 2.8, 5.0, 4.2, 6.6, 1.5, 3.3, 8.0, 2.0, 4.8, 3.0]   # 19: no percentile lands on a boundary
        b = bar(percentile=percentile)
        feed(b, scores)
        assert b.level_at(1.0 * len(scores)) == pytest.approx(line_through_the_shares(scores, percentile), abs=1e-9)

    def test_a_hundred_is_the_best_score(self):
        b = bar(percentile=100)
        feed(b, [1, 5, 3, 9, 2])
        assert b.level_at(5.0) == pytest.approx(9.0)

    def test_a_high_percentile_of_few_scores_is_between_the_top_two(self):
        b = bar(percentile=99.5)
        feed(b, [1, 5, 3, 9, 2])
        assert 8.5 < b.level_at(5.0) < 9.0                   # 97.5% of the way from the second-best (5) to the best (9)

    def test_a_high_percentile_of_many_scores_sits_below_the_best(self):
        b = bar(percentile=99)
        feed(b, [float(i % 9) + (i % 7) / 10 for i in range(300)] + [9.5])
        assert b.level_at(301.0) < 9.5

    def test_a_lower_percentile_gives_a_lower_bar(self):
        high, low = bar(percentile=99), bar(percentile=80)
        for b in (high, low):
            feed(b, [float(i % 10) for i in range(100)])
        assert low.level_at(100.0) < high.level_at(100.0)

    def test_the_bar_can_fall_between_scores(self):
        b = bar(percentile=75)
        feed(b, [2.0, 4.0])
        assert b.level_at(2.0) == pytest.approx(3.0)

    def test_equal_scores_give_that_score(self):
        b = bar(percentile=99.5)
        feed(b, [3.0] * 30)
        assert b.level_at(30.0) == pytest.approx(3.0)

    def test_the_order_scores_arrive_in_does_not_matter_when_the_weights_are_equal(self):
        scores = [3.1, 5.5, 2.2, 7.3, 4.4, 6.1, 3.9, 2.8]
        forward, backward = bar(percentile=90), bar(percentile=90)
        feed(forward, scores)
        feed(backward, list(reversed(scores)))
        assert forward.level_at(8.0) == pytest.approx(backward.level_at(8.0))


class TestTheDecay:
    def test_an_old_zinger_loses_its_hold_gradually(self):
        b = DecayingQuantileBar(percentile=99, half_life_seconds=60, starting_bar=3.0, starting_weight=TINY)
        b.add(9.0, 0.0, False)
        feed(b, [3.0] * 60, start=1.0, step=1.0)
        levels = [b.level_at(t) for t in (61, 120, 180, 240, 300)]
        assert levels == sorted(levels, reverse=True) and levels[0] > levels[-1]
        assert levels[0] > 3.5 and levels[-1] < levels[0]

    def test_a_fresh_score_beats_the_bar_set_by_an_old_zinger_but_not_one_just_heard(self):
        old = DecayingQuantileBar(percentile=99.5, half_life_seconds=60, starting_weight=TINY, starting_bar=3.0)
        fresh = DecayingQuantileBar(percentile=99.5, half_life_seconds=60, starting_weight=TINY, starting_bar=3.0)
        old.add(9.0, 0.0, False)                                   # a zinger ten minutes ago
        feed(old, [3.0] * 120, start=1.0, step=5.0)                # then ordinary talk up to 596 s
        feed(fresh, [3.0] * 120, start=1.0, step=5.0)
        fresh.add(9.0, 597.0, False)                               # a zinger three seconds ago
        assert old.level_at(600.0) < 7.2 < fresh.level_at(600.0)

    def test_scores_count_for_half_after_one_half_life(self):
        b = DecayingQuantileBar(percentile=50, half_life_seconds=100, starting_weight=TINY, starting_bar=0.0)
        b.add(0.0, 0.0, False)           # weight 0.5 at t = 100
        b.add(10.0, 100.0, False)        # weight 1 at t = 100
        # weights 0.5 and 1: the 0.0 covers 0 to 0.5, the 10.0 covers 0.5 to 1.5; the 50th percentile (0.75) is a quarter of the way
        # along the 10.0's share, so a quarter of the way from 0.0 to 10.0
        assert b.level_at(100.0) == pytest.approx(2.5, abs=1e-6)

    def test_time_decays_the_weight_not_the_number_of_scores(self):
        busy, quiet = bar(percentile=50, half_life_seconds=100), bar(percentile=50, half_life_seconds=100)
        busy.add(9.0, 0.0, False)
        quiet.add(9.0, 0.0, False)
        feed(busy, [1.0] * 50, start=100.0, step=0.01)
        feed(quiet, [1.0] * 2, start=100.0, step=0.01)
        assert busy.level_at(101.0) < quiet.level_at(101.0) or busy.level_at(101.0) == pytest.approx(1.0)

    def test_a_score_heard_after_the_time_asked_about_counts_in_full_not_more(self):
        b = DecayingQuantileBar(percentile=50, half_life_seconds=60, starting_weight=TINY, starting_bar=0.0)
        b.add(1.0, 0.0, False)
        b.add(9.0, 100.0, False)
        # asked about t = 50, before the second score: its age counts as 0, so its weight is 1, and the first is 2 ** (-50/60) = 0.561
        assert b.level_at(50.0) == pytest.approx(1.0 + (0.5 * (0.5612 + 1.0) - 0.5612) / 1.0 * 8.0, abs=0.01)

    def test_a_clock_that_steps_back_does_not_break_it(self):
        b = DecayingQuantileBar(half_life_seconds=60)
        b.add(5.0, 100.0, False)
        assert b.level_at(50.0) is not None


class TestExcludingTriggers:
    def test_a_triggering_score_is_left_out_when_asked(self):
        b = DecayingQuantileBar(exclude_triggers=True)
        b.add(9.0, 0.0, True)
        assert b.window_size_at(0.0) == 0

    def test_a_triggering_score_is_kept_by_default(self):
        b = DecayingQuantileBar()
        b.add(9.0, 0.0, True)
        assert b.window_size_at(0.0) == 1

    def test_only_the_triggering_scores_are_left_out(self):
        b = DecayingQuantileBar(exclude_triggers=True)
        b.add(3.0, 0.0, False)
        b.add(9.0, 1.0, True)
        b.add(4.0, 2.0, False)
        assert b.window_size_at(2.0) == 2

    def test_leaving_a_zinger_out_keeps_the_bar_at_the_rooms_high_tide(self):
        keep, leave = bar(percentile=99.5, starting_bar=3.0), bar(percentile=99.5, exclude_triggers=True, starting_bar=3.0)
        for b in (keep, leave):
            feed(b, [3.0] * 30)
            b.add(9.0, 30.0, True)
        assert keep.level_at(31.0) == pytest.approx(8.07, abs=0.01) and leave.level_at(31.0) == pytest.approx(3.0)

    def test_a_score_that_did_not_trigger_is_kept_either_way(self):
        b = DecayingQuantileBar(exclude_triggers=True)
        b.add(9.0, 0.0, False)
        assert b.window_size_at(0.0) == 1


class TestTheWindowSize:
    def test_it_counts_real_scores_only(self):
        b = DecayingQuantileBar()
        assert b.window_size_at(0.0) == 0
        feed(b, [1, 2, 3])
        assert b.window_size_at(3.0) == 3

    def test_scores_older_than_eight_half_lives_are_forgotten(self):
        b = DecayingQuantileBar(half_life_seconds=10)
        b.add(5.0, 0.0, False)
        assert b.window_size_at(80.0) == 1 and b.window_size_at(80.1) == 0

    def test_memory_is_bounded(self):
        b = DecayingQuantileBar(half_life_seconds=10)
        feed(b, [3.0] * 5000, step=0.5)
        assert len(b._memory) <= 8 * 10 / 0.5 + 2


class TestWhatIsIgnored:
    @pytest.mark.parametrize("junk", [None, "7", True, [5], float("nan"), float("inf")])
    def test_a_score_that_is_not_a_number_changes_nothing(self, junk):
        b = DecayingQuantileBar()
        b.add(junk, 0.0, False)
        assert b.window_size_at(0.0) == 0

    @pytest.mark.parametrize("junk", [None, "7", True, [5], float("nan"), float("inf")])
    def test_a_time_that_is_not_a_number_changes_nothing(self, junk):
        b = DecayingQuantileBar()
        b.add(5.0, junk, False)
        assert b.window_size_at(0.0) == 0 and b.level_at(junk) is None

    def test_a_zero_is_a_real_score(self):
        b = DecayingQuantileBar()
        b.add(0.0, 0.0, False)
        assert b.window_size_at(0.0) == 1


class TestDescribing:
    def test_it_says_how_it_works_in_one_line(self):
        assert DecayingQuantileBar(99.5, 300, 6.8).describe() == \
            "the 99.5th percentile of scores that count half as much every 300 s, starting at 6.8"

    def test_it_mentions_leaving_triggers_out_only_when_it_does(self):
        assert "triggering scores left out" in DecayingQuantileBar(exclude_triggers=True).describe()
        assert "triggering scores left out" not in DecayingQuantileBar().describe()


class TestBadSettings:
    @pytest.mark.parametrize("bad", [0, -1, 100.1, "99", None, True, [99], float("nan"), float("inf")])
    def test_a_bad_percentile_means_the_default_with_a_warning(self, bad):
        b = DecayingQuantileBar(percentile=bad)
        assert len(b.report.warnings) == 1 and "percentile" in b.report.warnings[0]
        assert b._percentile == DEFAULT_PERCENTILE

    @pytest.mark.parametrize("bad", [0, -5, "300", None, True, float("nan"), float("inf")])
    def test_a_bad_half_life_means_the_default_with_a_warning(self, bad):
        b = DecayingQuantileBar(half_life_seconds=bad)
        assert len(b.report.warnings) == 1 and "half_life_seconds" in b.report.warnings[0]
        assert b._half_life == DEFAULT_HALF_LIFE_SECONDS

    @pytest.mark.parametrize("bad", ["6.8", None, True, [6.8], float("nan"), float("inf")])
    def test_a_bad_starting_bar_means_the_default_with_a_warning(self, bad):
        b = DecayingQuantileBar(starting_bar=bad)
        assert len(b.report.warnings) == 1 and "starting_bar" in b.report.warnings[0]
        assert b.level_at(0.0) == pytest.approx(DEFAULT_STARTING_BAR)

    @pytest.mark.parametrize("bad", [0, -1, "20", None, True, float("nan")])
    def test_a_bad_starting_weight_means_the_default_with_a_warning(self, bad):
        b = DecayingQuantileBar(starting_weight=bad)
        assert len(b.report.warnings) == 1 and "starting_weight" in b.report.warnings[0]
        assert b._starting_weight == DEFAULT_STARTING_WEIGHT

    def test_a_negative_starting_bar_is_allowed(self):
        assert DecayingQuantileBar(starting_bar=-2.5).level_at(0.0) == pytest.approx(-2.5)

    @pytest.mark.parametrize("option,bad", [("percentile", 0), ("half_life_seconds", 0), ("starting_bar", None), ("starting_weight", 0)])
    def test_strict_mode_raises(self, option, bad):
        with pytest.raises(DecayingQuantileBarError, match=option):
            DecayingQuantileBar(strict=True, **{option: bad})

    def test_strict_mode_with_good_settings_does_not_raise(self):
        assert DecayingQuantileBar(strict=True).report.ok

    def test_two_bad_settings_give_two_warnings(self):
        assert len(DecayingQuantileBar(percentile=0, half_life_seconds=0).report.warnings) == 2


class TestBuildingFromSettings:
    def test_it_reads_the_settings_it_needs(self):
        settings = SimpleNamespace(percentile=95.0, half_life_seconds=120.0, starting_bar=5.5, exclude_triggers=True, unrelated="x")
        b = DecayingQuantileBar.from_settings(settings)
        assert (b._percentile, b._half_life, b._starting_bar, b._exclude_triggers) == (95.0, 120.0, 5.5, True)
        assert b.report.ok

    def test_bad_settings_are_replaced_as_usual(self):
        settings = SimpleNamespace(percentile=500, half_life_seconds=120.0, starting_bar=5.5, exclude_triggers=False)
        b = DecayingQuantileBar.from_settings(settings)
        assert b._percentile == DEFAULT_PERCENTILE and len(b.report.warnings) == 1

    def test_strict_is_passed_on(self):
        with pytest.raises(DecayingQuantileBarError):
            DecayingQuantileBar.from_settings(SimpleNamespace(percentile=500, half_life_seconds=1.0, starting_bar=1.0,
                                                              exclude_triggers=False), strict=True)


class TestLogging:
    def test_only_numbers_are_kept_and_nothing_is_logged_by_default(self, caplog):
        with caplog.at_level(logging.DEBUG):
            b = DecayingQuantileBar()
            feed(b, [1.0, 2.0, 9.0])
            b.level_at(3.0)
        assert "secret" not in caplog.text
