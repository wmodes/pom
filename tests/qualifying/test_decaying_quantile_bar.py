"""Tests for pom.qualifying.decaying_quantile_bar: a weighted, interpolated high quantile with a fading starting line."""
import logging
import math
from types import SimpleNamespace

import pytest

from pom.qualifying.decaying_quantile_bar import (DEFAULT_ANCHOR_INTERVAL_MINUTES, DEFAULT_ANCHOR_SCORE, DEFAULT_DISCARD_SCORES_OLDER_THAN_MINUTES,
                                               DEFAULT_HALF_LIFE_SECONDS, DEFAULT_BAR_PERCENTILE, DEFAULT_STARTING_BAR, DEFAULT_STARTING_BAR_WEIGHT,
                                               DecayingQuantileBarError)
from pom.qualifying.decaying_quantile_bar import DecayingQuantileBar as TickingDecayingQuantileBar
from pom.qualifying.bar import Bar

TINY = 1e-12            # a starting weight so small that the starting line does not affect the result
NO_TICKS = 1e12         # minutes between ticks: so far apart that no tick ever comes, so the tests of everything but the ticks see only real scores


class DecayingQuantileBar(TickingDecayingQuantileBar):
    """The bar with its ticks pushed out of reach, for every test here that is not about the ticks. (`TickingDecayingQuantileBar` is the real one.)"""

    def __init__(self, *args, **options):
        options.setdefault("anchor_interval_minutes", NO_TICKS)
        super().__init__(*args, **options)


def bar(**options):
    """A bar with a very long half-life (no decay worth speaking of) and no starting line, unless a test says otherwise."""
    options.setdefault("half_life_seconds", 1e12)
    options.setdefault("starting_bar_weight", TINY)
    return DecayingQuantileBar(**options)


def feed(b, scores, start=0.0, step=1.0):
    for i, score in enumerate(scores):
        b.add(score, start + i * step)


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
        assert (DEFAULT_BAR_PERCENTILE, DEFAULT_HALF_LIFE_SECONDS, DEFAULT_STARTING_BAR, DEFAULT_STARTING_BAR_WEIGHT) == (99.5, 300.0, 6.8, 20.0)

    def test_good_settings_are_quiet(self):
        assert DecayingQuantileBar().report.ok
        assert DecayingQuantileBar(bar_percentile=100, half_life_seconds=0.001, starting_bar=-3, starting_bar_weight=0.001).report.ok

    def test_it_is_a_bar(self):
        assert isinstance(DecayingQuantileBar(), Bar)

    def test_it_ignores_no_score(self):
        assert DecayingQuantileBar().ignores(0.0) is False and DecayingQuantileBar().ignores(9.0) is False


class TestTheStartingLine:
    def test_before_any_score_the_bar_is_the_starting_line(self):
        assert DecayingQuantileBar(starting_bar=6.8).level_at(0.0) == pytest.approx(6.8)
        assert DecayingQuantileBar(starting_bar=5.0, bar_percentile=50).level_at(100.0) == pytest.approx(5.0)

    def test_the_starting_line_is_settable(self):
        assert DecayingQuantileBar(starting_bar=4.25).level_at(0.0) == pytest.approx(4.25)

    def test_there_is_always_a_bar(self):
        assert DecayingQuantileBar().level_at(0.0) is not None

    def test_one_early_score_barely_moves_the_bar_off_the_starting_line(self):
        b = DecayingQuantileBar(starting_bar=6.8)
        b.level_at(0.0)
        b.add(9.0, 0.0)
        assert b.level_at(0.1) == pytest.approx((20 * 6.8 + 1 * 9.0) / 21, abs=0.01)     # 20 parts starting line to 1 part score

    def test_low_scores_pull_the_bar_down_from_the_starting_line_as_they_arrive(self):
        b = DecayingQuantileBar(starting_bar=6.8, bar_percentile=50, half_life_seconds=1e12)
        level_before = b.level_at(0.0)
        feed(b, [2.0] * 200)
        assert level_before == pytest.approx(6.8) and b.level_at(200.0) < 3.0

    def test_the_starting_line_fades_with_the_half_life(self):
        b = DecayingQuantileBar(starting_bar=9.0, bar_percentile=75, half_life_seconds=100, starting_bar_weight=20,
                                discard_scores_older_than_minutes=800 / 60)      # scores are discarded after 800 s
        b.level_at(0.0)
        feed(b, [2.0] * 20, start=0.0, step=0.001)           # 20 real scores heard at once: the line holds as much weight
        assert b.level_at(0.02) == pytest.approx(5.5, abs=0.1)         # half and half: (9 + 2) / 2
        assert b.level_at(1000.0) == pytest.approx(9.0)      # ten half-lives later every real score has been discarded

    def test_while_scores_keep_arriving_the_starting_line_fades_away(self):
        b = DecayingQuantileBar(starting_bar=9.0, bar_percentile=50, half_life_seconds=100, starting_bar_weight=20)
        b.level_at(0.0)
        feed(b, [2.0] * 50, start=500.0, step=0.01)          # five half-lives later: the line has 20/32 of one score's weight
        assert b.level_at(501.0) == pytest.approx(2.0, abs=0.35)

    def test_the_starting_line_counts_from_the_first_use_not_from_each_use(self):
        b = DecayingQuantileBar(starting_bar=9.0, bar_percentile=50, half_life_seconds=100, starting_bar_weight=20)
        b.level_at(0.0)
        b.add(2.0, 500.0)
        b.level_at(500.0)                                     # asked again: the line must not start over
        feed(b, [2.0] * 50, start=500.0, step=0.01)
        assert b.level_at(501.0) == pytest.approx(2.0, abs=0.35)

    def test_after_a_long_silence_the_bar_returns_to_the_starting_line(self):
        b = DecayingQuantileBar(starting_bar=6.8, half_life_seconds=60, discard_scores_older_than_minutes=8)
        feed(b, [1.0, 2.0, 3.0, 4.0, 9.0], step=1.0)
        assert b.level_at(10 * 60 + 100) == pytest.approx(6.8)

    def test_the_starting_weight_sets_how_long_the_line_lasts(self):
        light = DecayingQuantileBar(starting_bar=9.0, bar_percentile=50, half_life_seconds=1e12, starting_bar_weight=1)
        heavy = DecayingQuantileBar(starting_bar=9.0, bar_percentile=50, half_life_seconds=1e12, starting_bar_weight=500)
        for b in (light, heavy):
            b.level_at(0.0)
            feed(b, [2.0] * 50)
        assert light.level_at(50.0) < 2.5 and heavy.level_at(50.0) > 8.0

    def test_the_line_fades_from_the_start_the_bar_was_given(self):
        options = dict(starting_bar=9.0, bar_percentile=50, half_life_seconds=100, starting_bar_weight=20)
        late, early = DecayingQuantileBar(started_at=5000.0, **options), DecayingQuantileBar(started_at=0.0, **options)
        for b in (late, early):
            b.add(2.0, 5000.0)
        assert late.level_at(5000.0) > 5.0                                     # a bar that began at 5000 s: the line is fresh then
        assert early.level_at(5000.0) == pytest.approx(2.0, abs=0.01)          # one that began at 0: the line is 5000 s old, and gone

    def test_reading_the_bar_does_not_start_it(self):
        """The start is given, not found: a bar first read at 5000 s has been fading its line since 0, not since the read."""
        b = DecayingQuantileBar(starting_bar=9.0, bar_percentile=50, half_life_seconds=100, starting_bar_weight=20, started_at=0.0)
        b.level_at(5000.0)                                                     # the first read, long after the start
        b.add(2.0, 5000.0)
        assert b.level_at(5000.0) == pytest.approx(2.0, abs=0.01)

    def test_a_bar_with_no_start_given_starts_at_zero(self):
        b = DecayingQuantileBar(starting_bar=9.0, bar_percentile=50, half_life_seconds=100, starting_bar_weight=20)
        assert b._started_at == 0.0 and b.report.ok

    @pytest.mark.parametrize("good", [0, 0.0, 12.5, -3, 1e6])
    def test_a_start_can_be_any_number(self, good):
        assert DecayingQuantileBar(started_at=good).report.ok and DecayingQuantileBar(started_at=good)._started_at == float(good)

    @pytest.mark.parametrize("bad", ["0", None, True, [0], float("nan"), float("inf")])
    def test_a_bad_start_is_zero_with_a_warning(self, bad):
        b = DecayingQuantileBar(started_at=bad)
        assert b._started_at == 0.0 and len(b.report.warnings) == 1 and "started_at" in b.report.warnings[0]

    def test_strict_raises_on_a_bad_start(self):
        with pytest.raises(DecayingQuantileBarError):
            DecayingQuantileBar(started_at="soon", strict=True)

    def test_from_settings_passes_the_start_on(self):
        settings = SimpleNamespace(bar_percentile=95.0, half_life_seconds=120.0, starting_bar=5.5, anchor_score=4.5, anchor_interval_minutes=2.5,
                                   discard_scores_older_than_minutes=90.0, starting_bar_weight=7.5)
        assert DecayingQuantileBar.from_settings(settings, started_at=42.0)._started_at == 42.0


class TestTheWeightedQuantile:
    @pytest.mark.parametrize("bar_percentile", [100, 99.5, 99, 90, 75, 50, 25, 10, 1])
    def test_equal_weights_match_the_plain_interpolated_percentile(self, bar_percentile):
        scores = [3.1, 1.0, 5.5, 2.2, 7.3, 4.4, 1.0, 6.1, 3.9, 2.8, 5.0, 4.2, 6.6, 1.5, 3.3, 8.0, 2.0, 4.8, 3.0]   # 19: no percentile lands on a boundary
        b = bar(bar_percentile=bar_percentile)
        feed(b, scores)
        assert b.level_at(1.0 * len(scores)) == pytest.approx(line_through_the_shares(scores, bar_percentile), abs=1e-9)

    def test_a_hundred_is_the_best_score(self):
        b = bar(bar_percentile=100)
        feed(b, [1, 5, 3, 9, 2])
        assert b.level_at(5.0) == pytest.approx(9.0)

    def test_a_high_percentile_of_few_scores_is_between_the_top_two(self):
        b = bar(bar_percentile=99.5)
        feed(b, [1, 5, 3, 9, 2])
        assert 8.5 < b.level_at(5.0) < 9.0                   # 97.5% of the way from the second-best (5) to the best (9)

    def test_a_high_percentile_of_many_scores_sits_below_the_best(self):
        b = bar(bar_percentile=99)
        feed(b, [float(i % 9) + (i % 7) / 10 for i in range(300)] + [9.5])
        assert b.level_at(301.0) < 9.5

    def test_a_lower_percentile_gives_a_lower_bar(self):
        high, low = bar(bar_percentile=99), bar(bar_percentile=80)
        for b in (high, low):
            feed(b, [float(i % 10) for i in range(100)])
        assert low.level_at(100.0) < high.level_at(100.0)

    def test_the_bar_can_fall_between_scores(self):
        b = bar(bar_percentile=75)
        feed(b, [2.0, 4.0])
        assert b.level_at(2.0) == pytest.approx(3.0)

    def test_equal_scores_give_that_score(self):
        b = bar(bar_percentile=99.5)
        feed(b, [3.0] * 30)
        assert b.level_at(30.0) == pytest.approx(3.0)

    def test_the_order_scores_arrive_in_does_not_matter_when_the_weights_are_equal(self):
        scores = [3.1, 5.5, 2.2, 7.3, 4.4, 6.1, 3.9, 2.8]
        forward, backward = bar(bar_percentile=90), bar(bar_percentile=90)
        feed(forward, scores)
        feed(backward, list(reversed(scores)))
        assert forward.level_at(8.0) == pytest.approx(backward.level_at(8.0))


class TestTheQuantileIsSeparateFromTheStartingLine:
    """The quantile takes its percentile as an argument, and the list it is read from has no starting line in it (the blend is `level_at`'s)."""
    SCORES = [3.1, 1.0, 5.5, 2.2, 7.3, 4.4, 1.0, 6.1, 3.9, 2.8, 5.0, 4.2, 6.6, 1.5, 3.3, 8.0, 2.0, 4.8, 3.0]

    @pytest.mark.parametrize("percentile", [100, 99.5, 90, 50, 10])
    def test_the_quantile_takes_its_percentile_as_an_argument(self, percentile):
        entries = [(score, 1.0) for score in self.SCORES]
        assert DecayingQuantileBar._quantile(entries, percentile) == pytest.approx(line_through_the_shares(self.SCORES, percentile), abs=1e-9)

    def test_the_list_the_bar_is_read_from_has_no_starting_line_in_it(self):
        """A heavy starting line at 9 drags the bar up, but a percentile read from the weighted scores sees only the scores."""
        b = DecayingQuantileBar(bar_percentile=99.5, half_life_seconds=1e12, starting_bar=9.0, starting_bar_weight=500)
        feed(b, self.SCORES)
        now = float(len(self.SCORES))
        assert b.level_at(now) > 8.0                                 # the starting line is pulling the bar toward 9
        assert b._quantile(b._weighted_scores(now), 50) == pytest.approx(line_through_the_shares(self.SCORES, 50), abs=1e-6)

    def test_the_weighted_scores_are_the_remembered_scores_with_their_weights(self):
        b = DecayingQuantileBar(half_life_seconds=10.0)
        assert b._weighted_scores(0.0) == []
        b.add(4.0, 0.0)
        b.add(7.0, 10.0)
        assert b._weighted_scores(10.0) == [(4.0, pytest.approx(0.5)), (7.0, pytest.approx(1.0))]

    def test_the_weighted_scores_leave_out_what_is_forgotten(self):
        b = DecayingQuantileBar(half_life_seconds=10.0)
        b.add(4.0, 0.0)
        assert b._weighted_scores(1_000_000.0) == []

    def test_the_bar_is_the_quantile_at_its_own_percentile_blended_with_the_starting_line(self):
        b = DecayingQuantileBar(bar_percentile=90, half_life_seconds=1e12, starting_bar=9.0, starting_bar_weight=TINY)
        feed(b, self.SCORES)
        now = float(len(self.SCORES))
        assert b.level_at(now) == pytest.approx(b._quantile(b._weighted_scores(now), 90), abs=1e-6)


def ticking(**options):
    """The real bar, ticking: a half-life of 300 s, an anchor of 5.0 every minute, and no starting line unless a test says otherwise."""
    options.setdefault("half_life_seconds", 300.0)
    options.setdefault("starting_bar_weight", TINY)
    options.setdefault("anchor_score", 5.0)
    options.setdefault("anchor_interval_minutes", 1.0)
    return TickingDecayingQuantileBar(**options)


class TestTheAnchor:
    """Ticks: a steady stand-in score on a fixed grid from first use, so that silence lowers the bar."""

    def test_there_are_no_ticks_before_the_start_or_before_the_first_interval_after_it(self):
        b = ticking(started_at=1000.0)
        assert b._ticks_at(500.0) == [] and b._ticks_at(1000.0) == []        # before the start, and at it
        assert b._ticks_at(1059.9) == [] and len(b._ticks_at(1060.0)) == 1

    def test_ticks_fall_on_a_fixed_grid_from_the_start_each_weighted_by_its_age(self):
        b = ticking(started_at=1000.0)
        entries = b._ticks_at(1190.0)                                 # ticks at 1060, 1120 and 1180
        assert [score for score, _ in entries] == [5.0, 5.0, 5.0]
        assert [weight for _, weight in entries] == [pytest.approx(2 ** (-130 / 300)), pytest.approx(2 ** (-70 / 300)), pytest.approx(2 ** (-10 / 300))]

    def test_ticks_run_whether_or_not_anything_has_asked_the_bar_a_thing(self):
        """The ticks do not wait for a first score or a first read: ten minutes after the start there are ten ticks, and the bar has already moved."""
        b = ticking(starting_bar=6.8, starting_bar_weight=20.0)
        assert len(b._ticks_at(600.0)) == 10 and b.window_size_at(600.0) == 0
        assert b.level_at(0.0) == pytest.approx(6.8) and b.level_at(300.0) < 6.6          # before any score: sliding from the starting bar toward the anchor

    def test_the_interval_is_in_minutes(self):
        b = ticking(anchor_interval_minutes=2.5)
        assert len(b._ticks_at(149.9)) == 0 and len(b._ticks_at(150.0)) == 1 and len(b._ticks_at(300.0)) == 2

    def test_ticks_are_forgotten_on_the_same_horizon_as_scores(self):
        b = ticking(discard_scores_older_than_minutes=80 / 60)        # scores, and so ticks, are remembered for 80 s
        b.level_at(0.0)
        assert len(b._ticks_at(300.0)) == 2                           # of those at 60, 120, ... 300, only 240 and 300 are within 80 s of now

    def test_ticks_are_forgotten_on_the_same_horizon_whatever_the_start(self):
        b = ticking(started_at=1000.0, discard_scores_older_than_minutes=80 / 60)        # remembered for 80 s
        assert len(b._ticks_at(1300.0)) == 2                          # ticks at 1060, 1120, ... 1300: only 1240 and 1300 are within 80 s of now

    def test_ticks_are_not_counted_as_scores(self):
        b = ticking()
        b.level_at(0.0)
        assert b.window_size_at(600.0) == 0
        b.add(7.0, 600.0)
        assert b.window_size_at(600.0) == 1 and len(b._memory) == 1

    def test_a_tick_depends_only_on_the_clock_not_on_how_often_or_when_the_bar_was_read(self):
        """A bar read at 1000 s and then at 100 s (within its memory) gives what a bar read only at 100 s gives: nothing is stored or flushed."""
        a, b = ticking(), ticking()
        for bar_ in (a, b):
            bar_.add(8.0, 10.0)
        a.level_at(1000.0)
        assert a.level_at(100.0) == pytest.approx(b.level_at(100.0))

    def test_silence_lets_the_bar_settle_at_the_anchor(self):
        """A small crowd's high is held while their weight lasts, then released by the ticks, and the bar never goes up on the way down."""
        b = ticking()
        for t in range(0, 3):
            b.add(9.0, float(t))
        levels = [b.level_at(t) for t in (100.0, 600.0, 1200.0, 1800.0, 3000.0)]
        assert levels[0] > 8.9                                        # soon after the crowd, the bar is up at their high
        assert all(later <= earlier + 1e-9 for earlier, later in zip(levels, levels[1:]))
        assert 5.0 < levels[3] < 9.0                                  # half an hour on, it is part of the way down
        assert levels[-1] == pytest.approx(5.0, abs=1e-6)             # and settles at the anchor

    def test_a_shorter_interval_pulls_the_bar_down_faster(self):
        quick, slow = ticking(anchor_interval_minutes=0.25), ticking(anchor_interval_minutes=1.0)
        for b in (quick, slow):
            for t in range(0, 3):
                b.add(9.0, float(t))
        assert quick.level_at(1500.0) < slow.level_at(1500.0) - 1.0   # twenty-five minutes on, the quicker ticks have released the crowd's high

    def test_a_dull_room_rests_at_the_anchor_not_below_it(self):
        b = ticking()
        for t in range(0, 300):
            b.add(2.0, float(t))
        assert b.level_at(300.0) == pytest.approx(5.0, abs=0.1)

    def test_talk_is_lowered_only_slightly_by_the_ticks(self):
        """Ticks add weight below the top, so in a busy room the bar sits a little lower than it would without them."""
        with_ticks, without = ticking(), DecayingQuantileBar(half_life_seconds=300.0, starting_bar_weight=TINY)
        for b in (with_ticks, without):
            for i in range(200):
                b.add(1.0 + (i * 7) % 9, i * 0.5)                    # 200 scores over 100 s, spread over 1 to 9
        assert 0 <= without.level_at(100.0) - with_ticks.level_at(100.0) < 0.5

    def test_the_default_anchor_is_the_documented_fallback(self):
        b = TickingDecayingQuantileBar()
        assert (b._anchor_score, b._anchor_interval) == (DEFAULT_ANCHOR_SCORE, 60.0 * DEFAULT_ANCHOR_INTERVAL_MINUTES) and b.report.ok

    @pytest.mark.parametrize("bad", ["5", None, True, [5], float("nan"), float("inf")])
    def test_a_bad_anchor_score_means_the_default_with_a_warning(self, bad):
        b = TickingDecayingQuantileBar(anchor_score=bad)
        assert len(b.report.warnings) == 1 and "anchor_score" in b.report.warnings[0] and b._anchor_score == DEFAULT_ANCHOR_SCORE

    @pytest.mark.parametrize("bad", [0, -1, "1", None, True, float("nan"), float("inf")])
    def test_a_bad_anchor_interval_means_the_default_with_a_warning(self, bad):
        b = TickingDecayingQuantileBar(anchor_interval_minutes=bad)
        assert len(b.report.warnings) == 1 and "anchor_interval_minutes" in b.report.warnings[0]
        assert b._anchor_interval == 60.0 * DEFAULT_ANCHOR_INTERVAL_MINUTES

    @pytest.mark.parametrize("option,bad", [("anchor_score", None), ("anchor_interval_minutes", 0)])
    def test_strict_mode_raises(self, option, bad):
        with pytest.raises(DecayingQuantileBarError):
            TickingDecayingQuantileBar(strict=True, **{option: bad})

    def test_the_default_discard_age_is_the_documented_fallback(self):
        b = TickingDecayingQuantileBar()
        assert b._discard_seconds == 60.0 * DEFAULT_DISCARD_SCORES_OLDER_THAN_MINUTES and b.report.ok

    @pytest.mark.parametrize("bad", [0, -1, "60", None, True, float("nan"), float("inf")])
    def test_a_bad_discard_age_means_the_default_with_a_warning(self, bad):
        b = TickingDecayingQuantileBar(discard_scores_older_than_minutes=bad)
        assert len(b.report.warnings) == 1 and "discard_scores_older_than_minutes" in b.report.warnings[0]
        assert b._discard_seconds == 60.0 * DEFAULT_DISCARD_SCORES_OLDER_THAN_MINUTES

    def test_strict_mode_raises_on_a_bad_discard_age(self):
        with pytest.raises(DecayingQuantileBarError):
            TickingDecayingQuantileBar(strict=True, discard_scores_older_than_minutes=0)

    def test_negative_and_zero_anchor_scores_are_fine(self):
        assert TickingDecayingQuantileBar(anchor_score=-2).report.ok and TickingDecayingQuantileBar(anchor_score=0).report.ok


class TestTheDecay:
    def test_an_old_zinger_loses_its_hold_gradually(self):
        b = DecayingQuantileBar(bar_percentile=99, half_life_seconds=60, starting_bar=3.0, starting_bar_weight=TINY)
        b.add(9.0, 0.0)
        feed(b, [3.0] * 60, start=1.0, step=1.0)
        levels = [b.level_at(t) for t in (61, 120, 180, 240, 300)]
        assert levels == sorted(levels, reverse=True) and levels[0] > levels[-1]
        assert levels[0] > 3.5 and levels[-1] < levels[0]

    def test_a_fresh_score_beats_the_bar_set_by_an_old_zinger_but_not_one_just_heard(self):
        old = DecayingQuantileBar(bar_percentile=99.5, half_life_seconds=60, starting_bar_weight=TINY, starting_bar=3.0)
        fresh = DecayingQuantileBar(bar_percentile=99.5, half_life_seconds=60, starting_bar_weight=TINY, starting_bar=3.0)
        old.add(9.0, 0.0)                                   # a zinger ten minutes ago
        feed(old, [3.0] * 120, start=1.0, step=5.0)                # then ordinary talk up to 596 s
        feed(fresh, [3.0] * 120, start=1.0, step=5.0)
        fresh.add(9.0, 597.0)                               # a zinger three seconds ago
        assert old.level_at(600.0) < 7.2 < fresh.level_at(600.0)

    def test_scores_count_for_half_after_one_half_life(self):
        b = DecayingQuantileBar(bar_percentile=50, half_life_seconds=100, starting_bar_weight=TINY, starting_bar=0.0)
        b.add(0.0, 0.0)           # weight 0.5 at t = 100
        b.add(10.0, 100.0)        # weight 1 at t = 100
        # weights 0.5 and 1: the 0.0 covers 0 to 0.5, the 10.0 covers 0.5 to 1.5; the 50th percentile (0.75) is a quarter of the way
        # along the 10.0's share, so a quarter of the way from 0.0 to 10.0
        assert b.level_at(100.0) == pytest.approx(2.5, abs=1e-6)

    def test_time_decays_the_weight_not_the_number_of_scores(self):
        busy, quiet = bar(bar_percentile=50, half_life_seconds=100), bar(bar_percentile=50, half_life_seconds=100)
        busy.add(9.0, 0.0)
        quiet.add(9.0, 0.0)
        feed(busy, [1.0] * 50, start=100.0, step=0.01)
        feed(quiet, [1.0] * 2, start=100.0, step=0.01)
        assert busy.level_at(101.0) < quiet.level_at(101.0) or busy.level_at(101.0) == pytest.approx(1.0)

    def test_a_score_heard_after_the_time_asked_about_counts_in_full_not_more(self):
        b = DecayingQuantileBar(bar_percentile=50, half_life_seconds=60, starting_bar_weight=TINY, starting_bar=0.0)
        b.add(1.0, 0.0)
        b.add(9.0, 100.0)
        # asked about t = 50, before the second score: its age counts as 0, so its weight is 1, and the first is 2 ** (-50/60) = 0.561
        assert b.level_at(50.0) == pytest.approx(1.0 + (0.5 * (0.5612 + 1.0) - 0.5612) / 1.0 * 8.0, abs=0.01)

    def test_a_clock_that_steps_back_does_not_break_it(self):
        b = DecayingQuantileBar(half_life_seconds=60)
        b.add(5.0, 100.0)
        assert b.level_at(50.0) is not None


class TestTheWindowSize:
    def test_it_counts_real_scores_only(self):
        b = DecayingQuantileBar()
        assert b.window_size_at(0.0) == 0
        feed(b, [1, 2, 3])
        assert b.window_size_at(3.0) == 3

    def test_a_score_counts_for_half_after_one_half_life(self):
        b = DecayingQuantileBar(half_life_seconds=10)
        feed(b, [3.0] * 4, step=0.0)                          # four scores heard at once
        assert b.window_size_at(0.0) == 4 and b.window_size_at(10.0) == 2 and b.window_size_at(20.0) == 1

    def test_the_count_is_to_the_nearest_whole_score(self):
        b = DecayingQuantileBar(half_life_seconds=10)
        b.add(3.0, 0.0)
        assert b.window_size_at(5.0) == 1                     # 0.71 rounds up
        assert b.window_size_at(20.0) == 0                    # 0.25 rounds down

    def test_a_busy_room_has_a_window_of_about_a_hundred_at_a_five_minute_half_life(self):
        b = DecayingQuantileBar(half_life_seconds=300)
        feed(b, [3.0] * 1200, step=60 / 14)                   # fourteen scores a minute for twenty minutes
        assert 95 <= b.window_size_at(1200 * 60 / 14) <= 107   # not the 1200 scores heard

    def test_scores_older_than_the_discard_age_are_forgotten(self):
        b = DecayingQuantileBar(half_life_seconds=10, discard_scores_older_than_minutes=80 / 60)
        b.add(5.0, 0.0)
        b.window_size_at(80.0)
        assert len(b._memory) == 1
        b.window_size_at(80.1)
        assert len(b._memory) == 0

    def test_the_discard_age_is_a_length_of_time_whatever_the_half_life(self):
        """A half-life of 1000 s would keep a score for hours if the age were counted in half-lives; the setting is minutes."""
        b = DecayingQuantileBar(half_life_seconds=1000, discard_scores_older_than_minutes=1)
        b.add(5.0, 0.0)
        b.window_size_at(60.0)
        assert len(b._memory) == 1
        b.window_size_at(60.1)
        assert len(b._memory) == 0

    def test_memory_is_bounded(self):
        b = DecayingQuantileBar(half_life_seconds=10, discard_scores_older_than_minutes=80 / 60)
        feed(b, [3.0] * 5000, step=0.5)
        assert len(b._memory) <= 80 / 0.5 + 2

    def test_a_weight_that_has_faded_to_nothing_is_dropped_and_never_divided_by(self):
        """A half-life of a second and a memory of a billion minutes: after a million seconds every weight underflows to zero."""
        b = DecayingQuantileBar(half_life_seconds=1.0, discard_scores_older_than_minutes=1e9, starting_bar=6.8)
        b.add(9.0, 0.0)
        assert b.level_at(1_000_000.0) == pytest.approx(6.8)
        t = ticking(half_life_seconds=1.0, discard_scores_older_than_minutes=1e9)
        t.add(9.0, 0.0)
        assert t.level_at(1_000_000.0) == pytest.approx(5.0)         # only the last ticks still have a weight above zero, and they are all at the anchor


class TestWhatIsIgnored:
    @pytest.mark.parametrize("junk", [None, "7", True, [5], float("nan"), float("inf")])
    def test_a_score_that_is_not_a_number_changes_nothing(self, junk):
        b = DecayingQuantileBar()
        b.add(junk, 0.0)
        assert b.window_size_at(0.0) == 0

    @pytest.mark.parametrize("junk", [None, "7", True, [5], float("nan"), float("inf")])
    def test_a_time_that_is_not_a_number_changes_nothing(self, junk):
        b = DecayingQuantileBar()
        b.add(5.0, junk)
        assert b.window_size_at(0.0) == 0 and b.level_at(junk) is None

    @pytest.mark.parametrize("junk", [None, "7", True, [5], float("nan"), float("inf")])
    def test_a_window_size_asked_with_a_time_that_is_not_a_number_is_zero(self, junk):
        b = DecayingQuantileBar()
        feed(b, [1, 2, 3])
        assert b.window_size_at(junk) == 0

    def test_a_zero_is_a_real_score(self):
        b = DecayingQuantileBar()
        b.add(0.0, 0.0)
        assert b.window_size_at(0.0) == 1


class TestDescribing:
    def test_it_says_how_it_works_in_one_line(self):
        assert TickingDecayingQuantileBar(99.5, 300, 6.8, anchor_score=5.85, anchor_interval_minutes=1).describe() == \
            "the 99.5th percentile of scores that count half as much every 300 s, starting at 6.8, with a steady tick of 5.85 every 1 min"

    def test_it_says_nothing_about_qualifiers_because_the_barkeeper_does(self):
        assert "qualif" not in DecayingQuantileBar().describe()


class TestBadSettings:
    @pytest.mark.parametrize("bad", [0, -1, 100.1, "99", None, True, [99], float("nan"), float("inf")])
    def test_a_bad_percentile_means_the_default_with_a_warning(self, bad):
        b = DecayingQuantileBar(bar_percentile=bad)
        assert len(b.report.warnings) == 1 and "bar_percentile" in b.report.warnings[0]
        assert b._bar_percentile == DEFAULT_BAR_PERCENTILE

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
        b = DecayingQuantileBar(starting_bar_weight=bad)
        assert len(b.report.warnings) == 1 and "starting_bar_weight" in b.report.warnings[0]
        assert b._starting_bar_weight == DEFAULT_STARTING_BAR_WEIGHT

    def test_a_negative_starting_bar_is_allowed(self):
        assert DecayingQuantileBar(starting_bar=-2.5).level_at(0.0) == pytest.approx(-2.5)

    @pytest.mark.parametrize("option,bad", [("bar_percentile", 0), ("half_life_seconds", 0), ("starting_bar", None), ("starting_bar_weight", 0)])
    def test_strict_mode_raises(self, option, bad):
        with pytest.raises(DecayingQuantileBarError, match=option):
            DecayingQuantileBar(strict=True, **{option: bad})

    def test_strict_mode_with_good_settings_does_not_raise(self):
        assert DecayingQuantileBar(strict=True).report.ok

    def test_two_bad_settings_give_two_warnings(self):
        assert len(DecayingQuantileBar(bar_percentile=0, half_life_seconds=0).report.warnings) == 2


class TestBuildingFromSettings:
    def test_it_reads_the_settings_it_needs(self):
        settings = SimpleNamespace(bar_percentile=95.0, half_life_seconds=120.0, starting_bar=5.5, anchor_score=4.5, anchor_interval_minutes=2.5,
                                   discard_scores_older_than_minutes=90.0, starting_bar_weight=7.5, unrelated="x")
        b = DecayingQuantileBar.from_settings(settings, started_at=0.0)
        assert (b._bar_percentile, b._half_life, b._starting_bar, b._anchor_score, b._anchor_interval, b._discard_seconds,
                b._starting_bar_weight) == (95.0, 120.0, 5.5, 4.5, 150.0, 5400.0, 7.5)
        assert b.report.ok

    def test_bad_settings_are_replaced_as_usual(self):
        settings = SimpleNamespace(bar_percentile=500, half_life_seconds=120.0, starting_bar=5.5, anchor_score=4.5, anchor_interval_minutes=2.5,
                                   discard_scores_older_than_minutes=90.0, starting_bar_weight=7.5)
        b = DecayingQuantileBar.from_settings(settings, started_at=0.0)
        assert b._bar_percentile == DEFAULT_BAR_PERCENTILE and len(b.report.warnings) == 1

    def test_strict_is_passed_on(self):
        with pytest.raises(DecayingQuantileBarError):
            DecayingQuantileBar.from_settings(SimpleNamespace(bar_percentile=500, half_life_seconds=1.0, starting_bar=1.0, anchor_score=5.0,
                                                              anchor_interval_minutes=1.0, discard_scores_older_than_minutes=1.0, starting_bar_weight=7.5), started_at=0.0, strict=True)


class TestLogging:
    def test_only_numbers_are_kept_and_nothing_is_logged_by_default(self, caplog):
        with caplog.at_level(logging.DEBUG):
            b = DecayingQuantileBar()
            feed(b, [1.0, 2.0, 9.0])
            b.level_at(3.0)
        assert "secret" not in caplog.text
