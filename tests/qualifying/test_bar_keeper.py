"""Tests for pom.qualifying.bar_keeper: the qualifying decision, the cooldown and the time, over any Bar.

Most tests use a fake bar the test controls, so only the BarKeeper is under test. The last classes run the BarKeeper over each real bar.
"""
import logging
import random
import threading
import time

import pytest

from pom.qualifying.bar_keeper import (DEFAULT_COOLDOWN_SECONDS, DEFAULT_QUALIFIER_BAR_INFLUENCE, BarKeeper, BarKeeperError,
                                                Ruling)
from pom.qualifying.decaying_quantile_bar import DecayingQuantileBar
from pom.qualifying.moving_average_bar import MovingAverageBar
from pom.qualifying.percentile_bar import PercentileBar
from pom.qualifying.bar import Bar


class FakeBar(Bar):
    """A bar the test sets: it answers `level` and records what it was asked and given."""

    def __init__(self, level=5.0, ignored=()):
        self.level, self.ignored, self.added, self.asked, self.sizes = level, set(ignored), [], [], []

    def level_at(self, now):
        self.asked.append(now)
        return self.level

    def window_size_at(self, now):
        self.sizes.append(now)
        return 7

    def add(self, score, now):
        self.added.append((score, now))

    def ignores(self, score):
        return score in self.ignored

    def describe(self):
        return "a fake bar"


def bar_keeper(level=5.0, cooldown=0, ignored=(), **options):
    bar = FakeBar(level, ignored)
    return BarKeeper(bar, cooldown_seconds=cooldown, **options), bar


class TestTheDefaults:
    def test_the_starting_value(self):
        assert DEFAULT_COOLDOWN_SECONDS == 60.0

    def test_with_no_bar_given_it_uses_a_percentile_bar(self):
        t = BarKeeper()
        assert isinstance(t._bar, PercentileBar) and t.report.ok

    def test_good_settings_are_quiet(self):
        assert BarKeeper(FakeBar(), cooldown_seconds=0).report.ok


class TestQualifying:
    def test_a_score_above_the_bar_qualifies(self):
        t, _ = bar_keeper(level=5.0)
        assert t.rule(5.01, 1.0) is Ruling.QUALIFIED

    def test_a_score_at_the_bar_does_not(self):
        t, _ = bar_keeper(level=5.0)
        assert t.rule(5.0, 1.0) is Ruling.DID_NOT_QUALIFY

    def test_a_score_below_the_bar_does_not(self):
        t, _ = bar_keeper(level=5.0)
        assert t.rule(1.0, 1.0) is Ruling.DID_NOT_QUALIFY

    def test_a_bar_with_no_level_never_qualifies(self):
        t, _ = bar_keeper(level=None)
        assert t.rule(9.0, 1.0) is Ruling.DID_NOT_QUALIFY

    def test_a_negative_bar_is_a_bar(self):
        t, _ = bar_keeper(level=-1.0)
        assert t.rule(0.0, 1.0) is Ruling.QUALIFIED

    def test_a_score_is_judged_against_the_bar_before_it_is_handed_over(self):
        t, bar = bar_keeper(level=5.0)
        order = []
        bar.level_at = lambda now: order.append("asked") or 5.0
        bar.add = lambda score, now: order.append("added")
        t.rule(9.0, 1.0)
        assert order == ["asked", "added"]

    def test_every_counted_score_is_handed_to_the_bar_with_the_time_and_the_value_it_goes_in_as(self):
        t, bar = bar_keeper(level=5.0)
        t.rule(9.0, 10.0)
        t.rule(2.0, 11.0)
        assert bar.added == [(7.0, 10.0), (2.0, 11.0)]       # the qualifier goes in half way from the bar (5) to the score (9), the other score as it is

    def test_a_score_is_handed_over_as_a_float(self):
        t, bar = bar_keeper()
        t.rule(7, 1.0)
        assert isinstance(bar.added[0][0], float)

    def test_the_counted_total_grows(self):
        t, _ = bar_keeper()
        for i in range(4):
            t.rule(1.0, float(i))
        assert t.counted == 4


class TestWhatIsIgnored:
    @pytest.mark.parametrize("junk", [None, "7", "", True, False, [5], {}, float("nan"), float("inf"), float("-inf")])
    def test_what_is_not_a_score_changes_nothing(self, junk):
        t, bar = bar_keeper()
        assert t.rule(junk, 1.0) is Ruling.NO_MARK
        assert t.counted == 0 and bar.added == []

    @pytest.mark.parametrize("junk", [None, "7", True, [5], float("nan"), float("inf")])
    def test_a_time_that_is_not_a_number_changes_nothing(self, junk):
        t, bar = bar_keeper()
        assert t.rule(9.0, junk) is Ruling.NO_MARK
        assert t.counted == 0 and bar.added == []
        assert t.bar_at(junk) is None

    def test_a_score_the_bar_ignores_is_ignored(self):
        t, bar = bar_keeper(ignored={0.1})
        assert t.rule(0.1, 1.0) is Ruling.NO_MARK
        assert t.counted == 0 and bar.added == [] and bar.asked == []

    def test_a_zero_is_a_real_score_not_a_missing_one(self):
        t, _ = bar_keeper()
        assert t.rule(0, 0) is Ruling.DID_NOT_QUALIFY

    def test_unscored_clips_between_scores_do_not_disturb_anything(self):
        t, bar = bar_keeper(level=5.0)
        for i, score in enumerate([1, None, 2, None, 9]):
            t.rule(score, float(i))
        assert t.counted == 3 and bar.added[-1] == (7.0, 4.0)


class TestTheCooldown:
    def test_a_second_strong_score_right_after_a_qualifier_is_cooling_down(self):
        t, _ = bar_keeper(level=5.0, cooldown=60)
        assert t.rule(9, 5) is Ruling.QUALIFIED
        assert t.rule(9, 6) is Ruling.HELD

    def test_the_cooldown_ends_exactly_when_the_time_is_up(self):
        t, _ = bar_keeper(level=5.0, cooldown=60)
        t.rule(9, 5)
        assert t.rule(9, 64.9) is Ruling.HELD
        assert t.rule(9, 65) is Ruling.QUALIFIED

    def test_a_score_that_cooled_down_does_not_restart_the_cooldown(self):
        t, _ = bar_keeper(level=5.0, cooldown=60)
        t.rule(9, 5)
        assert t.rule(9, 35) is Ruling.HELD
        assert t.rule(9, 65) is Ruling.QUALIFIED            # 60 s after the qualifier, not after the 35

    def test_a_score_below_the_bar_during_the_cooldown_is_just_counted(self):
        t, _ = bar_keeper(level=5.0, cooldown=60)
        t.rule(9, 5)
        assert t.rule(2, 6) is Ruling.DID_NOT_QUALIFY

    def test_a_held_score_is_handed_to_the_bar_as_the_qualifier_it_would_have_been_and_counted(self):
        t, bar = bar_keeper(level=5.0, cooldown=60)
        t.rule(9, 5)
        t.rule(9, 6)
        assert bar.added == [(7.0, 5.0), (7.0, 6.0)] and t.counted == 2

    def test_a_score_below_the_bar_in_the_cooldown_is_handed_over_as_it_is(self):
        t, bar = bar_keeper(level=5.0, cooldown=60)
        t.rule(9, 5)
        t.rule(2, 6)
        assert bar.added == [(7.0, 5.0), (2.0, 6.0)]

    def test_a_cooldown_of_zero_lets_every_strong_score_qualify(self):
        t, _ = bar_keeper(level=5.0, cooldown=0)
        assert [t.rule(9, 5 + i) for i in range(3)] == [Ruling.QUALIFIED] * 3

    def test_there_is_no_cooldown_before_the_first_qualifier(self):
        t, _ = bar_keeper(level=5.0, cooldown=10_000)
        assert t.rule(9, 5) is Ruling.QUALIFIED

    def test_a_burst_gives_one_qualifier_and_the_next_comes_after_the_cooldown(self):
        t, _ = bar_keeper(level=5.0, cooldown=30)
        rulings = [t.rule(20, 100 + i) for i in range(6)] + [t.rule(20, 140)]
        assert rulings == [Ruling.QUALIFIED] + [Ruling.HELD] * 5 + [Ruling.QUALIFIED]


class TestTheTime:
    def test_a_time_that_steps_backwards_is_held_at_the_latest(self):
        t, bar = bar_keeper(level=5.0)
        t.rule(1.0, 100.0)
        t.rule(1.0, 40.0)
        assert bar.asked == [100.0, 100.0] and bar.added[-1][1] == 100.0

    def test_the_questions_use_the_held_time_too(self):
        t, bar = bar_keeper()
        t.rule(1.0, 100.0)
        t.bar_at(1.0)
        t.window_size_at(1.0)
        assert bar.asked[-1] == 100.0 and bar.sizes[-1] == 100.0

    def test_a_later_time_is_used_as_it_is(self):
        t, bar = bar_keeper()
        t.rule(1.0, 100.0)
        t.bar_at(250.0)
        assert bar.asked[-1] == 250.0

    def test_before_any_score_the_time_asked_about_is_used(self):
        t, bar = bar_keeper()
        t.bar_at(7.0)
        t.window_size_at(8.0)
        assert bar.asked == [7.0] and bar.sizes == [8.0]

    def test_a_window_size_asked_with_no_usable_time_uses_the_latest_time(self):
        t, bar = bar_keeper()
        t.rule(1.0, 100.0)
        assert t.window_size_at(None) == 7 and bar.sizes[-1] == 100.0

    def test_a_window_size_asked_with_no_usable_time_before_any_score_uses_zero(self):
        t, bar = bar_keeper()
        t.window_size_at(float("nan"))
        assert bar.sizes[-1] == 0.0


class TestTheQuestions:
    def test_the_bar_level_is_the_bars(self):
        t, _ = bar_keeper(level=6.25)
        assert t.bar_at(1.0) == 6.25

    def test_the_window_size_is_the_bars(self):
        t, _ = bar_keeper()
        assert t.window_size_at(1.0) == 7

    def test_describe_adds_the_cooldown_and_the_qualifier_influence_to_the_bars_line(self):
        t, _ = bar_keeper(cooldown=60)
        assert t.describe() == "a fake bar; at most one qualifier every 60 s; a qualifying score goes into the bar 50% of the way up from the bar"

    @pytest.mark.parametrize("influence, said", [(0, "0%"), (1, "100%"), (0.25, "25%"), (1.1, "110%"), (-0.1, "-10%")])
    def test_describe_says_the_qualifier_influence_as_a_percent_even_beyond_0_to_100(self, influence, said):
        t, _ = bar_keeper(qualifier_bar_influence=influence)
        assert t.describe().endswith(f"a qualifying score goes into the bar {said} of the way up from the bar")

    def test_the_questions_change_nothing(self):
        t, bar = bar_keeper()
        t.bar_at(1.0)
        t.window_size_at(1.0)
        assert t.counted == 0 and bar.added == []


class TestBadSettings:
    @pytest.mark.parametrize("bad", [-1, -0.1, "60", None, True, [60], float("nan"), float("inf")])
    def test_a_bad_cooldown_means_the_default_with_a_warning(self, bad):
        t, _ = bar_keeper(level=5.0, cooldown=bad)
        assert len(t.report.warnings) == 1 and "cooldown_seconds" in t.report.warnings[0]
        assert t.rule(9, 5) is Ruling.QUALIFIED and t.rule(9, 30) is Ruling.HELD     # the default 60 s

    @pytest.mark.parametrize("not_a_bar", ["percentile", 5, [PercentileBar()], object()])
    def test_something_that_is_not_a_bar_means_the_default_bar_with_a_warning(self, not_a_bar):
        t = BarKeeper(not_a_bar)
        assert isinstance(t._bar, PercentileBar)
        assert len(t.report.warnings) == 1 and "bar" in t.report.warnings[0]

    def test_strict_mode_raises(self):
        with pytest.raises(BarKeeperError, match="cooldown_seconds"):
            BarKeeper(FakeBar(), cooldown_seconds=-1, strict=True)
        with pytest.raises(BarKeeperError, match="bar"):
            BarKeeper("nope", strict=True)

    def test_strict_mode_with_good_settings_does_not_raise(self):
        assert BarKeeper(FakeBar(), strict=True).report.ok


class TestOverARealPercentileBar:
    """The BarKeeper joined to the PercentileBar, against a few lines that do the same thing the slow, obvious way."""

    @staticmethod
    def reference(events, window_seconds, min_scores, percentile, cooldown, starting_bar):
        held, out, last_qualifier = [], [], None
        for now, score in events:
            if len(held) < min_scores:
                level = starting_bar
            else:
                window = [s for t, s in held if t >= now - window_seconds]
                if len(window) < min_scores:
                    window = [s for _, s in held[-min_scores:]]
                ranked = sorted(window)
                level = next(v for i, v in enumerate(ranked, start=1) if i * 100 >= percentile * len(ranked))
            fires = score > level and (last_qualifier is None or now - last_qualifier >= cooldown)
            if fires:
                last_qualifier = now
            out.append(fires)
            held.append((now, score))
        return out

    @pytest.mark.parametrize("seed,gap,percentile,cooldown", [(1, 1.0, 100, 0), (2, 0.2, 99.5, 0), (3, 40.0, 90, 0),
                                                              (4, 5.0, 50, 0), (5, 1.0, 97.5, 60), (6, 5.0, 100, 30)])
    def test_the_same_qualifiers_as_the_reference(self, seed, gap, percentile, cooldown):
        rng = random.Random(seed)
        now, events = 0.0, []
        for _ in range(600):
            now += rng.uniform(0.1, 2 * gap)
            events.append((now, round(rng.choice([1.0, 1.0, 2.0, 3.0, 3.5, 4.0, 5.0, 5.5, 6.0, 7.0, 8.0]) + rng.random(), 2)))
        t = BarKeeper(PercentileBar(percentile, 120, 12, 6.8), cooldown, qualifier_bar_influence=1.0)    # the reference holds every score as it is
        got = [t.rule(score, when) is Ruling.QUALIFIED for when, score in events]
        assert got == self.reference(events, 120, 12, percentile, cooldown, 6.8)
        assert any(got) and not all(got)

    def test_a_dull_room_and_a_lively_room_need_different_scores(self):
        dull, lively = (BarKeeper(PercentileBar(100, 60, 5, 100.0), 0) for _ in range(2))
        for i, (a, b) in enumerate(zip([2, 3, 3, 3, 4], [6, 7, 7, 7, 8])):
            dull.rule(a, float(i))
            lively.rule(b, float(i))
        assert dull.rule(6, 5) is Ruling.QUALIFIED and lively.rule(6, 5) is Ruling.DID_NOT_QUALIFY

    def test_the_starting_line_lets_a_strong_score_fire_in_the_first_minute(self):
        t = BarKeeper(PercentileBar(starting_bar=6.8), 0)
        assert t.rule(7.0, 1.0) is Ruling.QUALIFIED

    def test_leaving_qualifiers_out_lets_a_good_score_follow_a_great_one(self):
        keep = BarKeeper(PercentileBar(100, 300, 5, 100.0), 0, qualifier_bar_influence=1.0)
        leave = BarKeeper(PercentileBar(100, 300, 5, 100.0), 0, qualifier_bar_influence=0.0)
        for t in (keep, leave):
            for i in range(5):
                t.rule(3.0, float(i))
            t.rule(8.9, 5.0)
        keep_bar, leave_bar = keep.bar_at(6.0), leave.bar_at(6.0)
        assert keep_bar == 8.9 and leave_bar == 3.0


class TestOverARealDecayingQuantileBar:
    def test_a_score_above_the_starting_line_qualifies_at_first(self):
        t = BarKeeper(DecayingQuantileBar(starting_bar=6.8), 0)
        assert t.rule(7.5, 1.0) is Ruling.QUALIFIED

    def test_a_qualifier_with_no_influence_goes_in_as_the_bar_itself(self):
        t = BarKeeper(DecayingQuantileBar(starting_bar=6.8), 0, qualifier_bar_influence=0.0)
        t.rule(9.0, 1.0)
        assert t.window_size_at(1.0) == 1 and t.bar_at(1.0) == pytest.approx(6.8)

    def test_scores_that_did_not_qualify_are_kept(self):
        t = BarKeeper(DecayingQuantileBar(starting_bar=6.8), 0, qualifier_bar_influence=0.0)
        t.rule(2.0, 1.0)
        assert t.window_size_at(1.0) == 1

    def test_the_bar_follows_the_room_down(self):
        t = BarKeeper(DecayingQuantileBar(starting_bar=6.8, percentile=90, half_life_seconds=30), 0)
        for i in range(200):
            t.rule(2.0, float(i))
        assert t.bar_at(200.0) < 3.0

    def test_describe_includes_the_cooldown(self):
        assert "at most one qualifier every 60 s" in BarKeeper(DecayingQuantileBar(), 60).describe()

    @staticmethod
    def share_above_the_bar(influence, seed=7, scores=6000, step=2.0):
        """The share of an unchanging room's scores that stand above the bar once it has settled (no cooldown, so held scores do not hide any)."""
        import random
        rng = random.Random(seed)
        t = BarKeeper(DecayingQuantileBar(starting_bar=3.0, percentile=99.5, half_life_seconds=300), 0, qualifier_bar_influence=influence)
        above = counted = 0
        for i in range(scores):
            ruling = t.rule(rng.uniform(0.0, 9.0), i * step)
            if i >= scores // 2:                                   # the second half, after the bar has found its level
                counted += 1
                above += ruling is Ruling.QUALIFIED
        return above / counted

    def test_the_less_a_qualifier_counts_the_more_of_the_room_stands_above_the_bar(self):
        shares = [self.share_above_the_bar(f) for f in (1.0, 0.5, 0.25)]
        assert shares[0] < shares[1] < shares[2]

    def test_with_no_influence_a_room_that_is_louder_than_the_bar_keeps_clearing_it(self):
        """The bar can never follow the room up: a bar that settled low stays low while every score clears it."""
        t = BarKeeper(DecayingQuantileBar(starting_bar=3.0, percentile=99.5, half_life_seconds=300), 0, qualifier_bar_influence=0.0)
        for i in range(40):
            t.rule(2.0 + (i % 3) * 0.1, float(i))
        later = [t.rule(5.0 + (i % 3), 100.0 + i) for i in range(60)]
        assert all(r is Ruling.QUALIFIED for r in later) and t.bar_at(160.0) < 3.0

    def test_with_some_influence_the_bar_follows_the_room_up(self):
        """The same quiet-then-livelier room as with no influence: now the bar rises with the zingers and fewer of them clear it."""
        def run(influence):
            t = BarKeeper(DecayingQuantileBar(starting_bar=3.0, percentile=99.5, half_life_seconds=300), 0, qualifier_bar_influence=influence)
            for i in range(40):
                t.rule(2.0 + (i % 3) * 0.1, float(i))
            later = [t.rule(5.0 + (i % 3), 100.0 + i) for i in range(60)]
            return later.count(Ruling.QUALIFIED), t.bar_at(160.0)
        (none_cleared, none_bar), (half_cleared, half_bar) = run(0.0), run(0.5)
        assert none_cleared == 60 and half_cleared < none_cleared
        assert half_bar > none_bar + 1.0 and half_bar > 5.0


class TestOverARealMovingAverageBar:
    def test_a_score_under_the_floor_is_ignored(self):
        t = BarKeeper(MovingAverageBar(), 0)
        assert t.rule(0.2, 1.0) is Ruling.NO_MARK and t.counted == 0

    def test_a_score_far_above_the_average_qualifies(self):
        t = BarKeeper(MovingAverageBar(fraction=0.25, starting_bar=4.0), 0)
        assert t.rule(6.0, 1.0) is Ruling.QUALIFIED

    def test_time_does_not_change_the_bar(self):
        t = BarKeeper(MovingAverageBar(), 0)
        t.rule(3.0, 1.0)
        assert t.bar_at(2.0) == t.bar_at(50_000.0)


class TestLogging:
    def test_only_numbers_are_logged(self, caplog):
        with caplog.at_level(logging.DEBUG):
            t, _ = bar_keeper(level=5.0)
            t.rule(9.0, 1.0)
        assert "bar 5.000" in caplog.text and "qualified" in caplog.text

    def test_a_missing_bar_level_is_logged_as_none(self, caplog):
        with caplog.at_level(logging.DEBUG):
            t, _ = bar_keeper(level=None)
            t.rule(9.0, 1.0)
        assert "bar none" in caplog.text


class TestRuleTyped:
    """A score handed to the Printer directly (a typed line): held to the bar, with no cooldown, and handed to the bar by the one rule."""

    def test_a_score_above_the_bar_qualifies(self):
        t, _ = bar_keeper(level=5.0)
        assert t.rule_typed(5.1, 1.0) is Ruling.QUALIFIED

    def test_a_score_at_the_bar_does_not_qualify(self):
        t, _ = bar_keeper(level=5.0)
        assert t.rule_typed(5.0, 1.0) is Ruling.DID_NOT_QUALIFY

    def test_a_score_below_the_bar_does_not_qualify(self):
        t, _ = bar_keeper(level=5.0)
        assert t.rule_typed(1.0, 1.0) is Ruling.DID_NOT_QUALIFY

    def test_with_no_bar_nothing_qualifies_and_the_score_goes_in_as_it_is(self):
        t, bar = bar_keeper(level=None)
        assert t.rule_typed(9.0, 1.0, bar_influence=0.5) is Ruling.DID_NOT_QUALIFY
        assert bar.added == [(9.0, 1.0)]

    @pytest.mark.parametrize("score,influence,goes_in_as", [(9.0, 1, 9.0), (9.0, 0.5, 7.0), (9.0, 0, 5.0), (9.0, 1.1, 9.4), (9.0, -0.1, 4.6),
                                                           (1.0, 0.5, 3.0), (1.0, 1, 1.0)])
    def test_every_typed_line_goes_in_as_the_bar_plus_the_influence_of_the_way_to_the_score(self, score, influence, goes_in_as):
        """With the bar at 5, a line above it or below it, by the same rule."""
        t, bar = bar_keeper(level=5.0)
        t.rule_typed(score, 1.0, bar_influence=influence)
        assert bar.added == [(pytest.approx(goes_in_as), 1.0)] and t.counted == 1

    def test_a_whole_number_score_goes_in_as_a_float_with_a_bar_or_without(self):
        for level in (5.0, None):
            t, bar = bar_keeper(level=level)
            t.rule_typed(9, 1.0, bar_influence=0.5)
            assert isinstance(bar.added[0][0], float)

    def test_by_default_a_typed_line_goes_in_as_the_bar_itself(self):
        t, bar = bar_keeper(level=5.0)
        t.rule_typed(9.0, 1.0)
        assert bar.added == [(5.0, 1.0)] and t.counted == 1

    @pytest.mark.parametrize("influence", [None, "1", True, float("nan")])
    def test_an_influence_that_is_not_a_number_counts_as_nothing(self, influence):
        t, bar = bar_keeper(level=5.0)
        t.rule_typed(9.0, 1.0, bar_influence=influence)
        assert bar.added == [(5.0, 1.0)]

    def test_the_qualifier_influence_is_not_stacked_on_a_typed_lines_own(self):
        t, bar = bar_keeper(level=5.0, qualifier_bar_influence=0.0)
        t.rule_typed(9.0, 1.0, bar_influence=1.0)
        assert bar.added == [(9.0, 1.0)]

    def test_it_has_no_cooldown_of_its_own(self):
        t, _ = bar_keeper(level=5.0, cooldown=60)
        assert [t.rule_typed(9.0, now) for now in (1.0, 2.0, 3.0)] == [Ruling.QUALIFIED] * 3

    def test_a_typed_qualifier_does_not_start_the_rooms_cooldown(self):
        t, _ = bar_keeper(level=5.0, cooldown=60)
        t.rule_typed(9.0, 1.0)
        assert t.rule(9.0, 2.0) is Ruling.QUALIFIED

    def test_the_rooms_cooldown_does_not_hold_a_typed_score_back(self):
        t, _ = bar_keeper(level=5.0, cooldown=60)
        t.rule(9.0, 1.0)
        assert t.rule_typed(9.0, 2.0) is Ruling.QUALIFIED

    @pytest.mark.parametrize("score,now", [(None, 1.0), ("9", 1.0), (True, 1.0), (float("nan"), 1.0), (float("inf"), 1.0),
                                          (9.0, None), (9.0, "1"), (9.0, float("nan"))])
    def test_a_score_or_time_that_is_not_a_number_is_ignored(self, score, now):
        t, bar = bar_keeper(level=5.0)
        assert t.rule_typed(score, now, bar_influence=1.0) is Ruling.NO_MARK and bar.added == [] and t.counted == 0

    def test_a_score_the_bar_ignores_is_ignored(self):
        t, bar = bar_keeper(level=5.0, ignored=(9.0,))
        assert t.rule_typed(9.0, 1.0, bar_influence=1.0) is Ruling.NO_MARK and bar.added == []

    def test_the_bar_is_asked_at_a_time_that_never_runs_backwards(self):
        t, bar = bar_keeper(level=5.0)
        t.rule(1.0, 10.0)
        bar.asked.clear()
        t.rule_typed(1.0, 4.0)
        assert bar.asked == [10.0]

    def test_a_typed_line_moves_the_bar_keeper_time_forward(self):
        t, bar = bar_keeper(level=5.0)
        t.rule_typed(1.0, 8.0)
        bar.asked.clear()
        t.bar_at(2.0)
        assert bar.asked == [8.0]


class LurchingBar(FakeBar):
    """A bar that notices when two threads are inside it at once, and stays inside long enough for the other one to arrive."""

    def __init__(self):
        super().__init__(level=5.0)
        self.inside, self.overlaps = 0, 0

    def _visit(self):
        self.inside += 1
        if self.inside > 1:
            self.overlaps += 1
        time.sleep(0.001)
        self.inside -= 1

    def level_at(self, now):
        self._visit()
        return super().level_at(now)

    def window_size_at(self, now):
        self._visit()
        return super().window_size_at(now)

    def add(self, score, now):
        self._visit()
        super().add(score, now)


class TestTwoThreads:
    """The room's thread and the keyboard's thread share one BarKeeper: its calls must not overlap."""

    def test_the_calls_of_three_threads_never_overlap_inside_the_bar(self):
        bar = LurchingBar()
        t = BarKeeper(bar, cooldown_seconds=0)
        calls = [lambda i: t.rule(7.0, float(i)), lambda i: t.rule_typed(7.0, float(i), bar_influence=1.0),
                 lambda i: (t.bar_at(float(i)), t.window_size_at(float(i)))]
        threads = [threading.Thread(target=lambda call=call: [call(i) for i in range(20)]) for call in calls]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert bar.overlaps == 0

    def test_every_score_from_both_threads_is_counted(self):
        t = BarKeeper(LurchingBar(), cooldown_seconds=0)
        room = threading.Thread(target=lambda: [t.rule(7.0, float(i)) for i in range(20)])
        keys = threading.Thread(target=lambda: [t.rule_typed(7.0, float(i), bar_influence=1.0) for i in range(20)])
        room.start()
        keys.start()
        room.join(timeout=10)
        keys.join(timeout=10)
        assert t.counted == 40


class TestAStuckLock:
    """If something ever holds the lock and never lets go, a call gives up, says so, and answers as if it had nothing."""

    @pytest.fixture
    def stuck(self, monkeypatch):
        monkeypatch.setattr("pom.qualifying.bar_keeper.LOCK_TIMEOUT_SECONDS", 0.05)
        t, bar = bar_keeper(level=5.0)
        t._lock.acquire()          # held by "another thread" that never finishes
        yield t, bar
        t._lock.release()

    def test_rule_gives_no_mark_and_leaves_the_bar_alone(self, stuck):
        t, bar = stuck
        assert t.rule(9.0, 1.0) is Ruling.NO_MARK
        assert bar.added == [] and t.counted == 0

    def test_it_waits_no_longer_than_the_timeout(self, stuck):
        t, _ = stuck
        started = time.monotonic()
        t.rule(9.0, 1.0)
        assert time.monotonic() - started < 1.0

    def test_rule_typed_gives_no_mark_and_leaves_the_bar_alone(self, stuck):
        t, bar = stuck
        assert t.rule_typed(9.0, 1.0, bar_influence=1.0) is Ruling.NO_MARK
        assert bar.added == [] and t.counted == 0

    def test_the_questions_answer_with_nothing(self, stuck):
        t, bar = stuck
        assert t.bar_at(1.0) is None and t.window_size_at(1.0) == 0
        assert bar.asked == [] and bar.sizes == []

    def test_each_give_up_is_a_critical_fault_that_names_the_call(self, stuck):
        t, _ = stuck
        t.rule(9.0, 1.0)
        t.bar_at(1.0)
        assert len(t.report.criticals) == 2
        assert "rule" in t.report.criticals[0] and "bar_at" in t.report.criticals[1]

    def test_in_strict_mode_it_raises(self, monkeypatch):
        monkeypatch.setattr("pom.qualifying.bar_keeper.LOCK_TIMEOUT_SECONDS", 0.05)
        t, _ = bar_keeper(strict=True)
        t._lock.acquire()
        try:
            with pytest.raises(BarKeeperError):
                t.rule(9.0, 1.0)
        finally:
            t._lock.release()

    def test_after_it_is_freed_the_next_call_works_normally(self, monkeypatch):
        monkeypatch.setattr("pom.qualifying.bar_keeper.LOCK_TIMEOUT_SECONDS", 0.05)
        t, _ = bar_keeper(level=5.0)
        t._lock.acquire()
        assert t.rule(9.0, 1.0) is Ruling.NO_MARK
        t._lock.release()
        assert t.rule(9.0, 2.0) is Ruling.QUALIFIED

    def test_an_exception_inside_a_call_still_lets_go_of_the_lock(self):
        class Exploding(FakeBar):
            def level_at(self, now):
                raise RuntimeError("boom")
        t = BarKeeper(Exploding(), cooldown_seconds=0)
        with pytest.raises(RuntimeError):
            t.rule(9.0, 1.0)
        assert t._lock.acquire(timeout=0.5)       # not left held
        t._lock.release()


class TestTheQualifierBarInfluenceIsChecked:
    @pytest.mark.parametrize("bad", [None, "0.5", True, False, [0.5], float("nan"), float("inf"), float("-inf")])
    def test_a_value_that_is_not_a_number_is_the_default_with_a_warning(self, bad):
        t = BarKeeper(FakeBar(), cooldown_seconds=0, qualifier_bar_influence=bad)
        assert t._qualifier_bar_influence == DEFAULT_QUALIFIER_BAR_INFLUENCE and len(t.report.warnings) == 1
        assert "qualifier_bar_influence" in t.report.warnings[0]

    @pytest.mark.parametrize("good", [0, 0.0, 0.25, 1, 1.1, -0.1, 7])
    def test_any_finite_number_is_kept_and_is_not_clamped(self, good):
        t = BarKeeper(FakeBar(), cooldown_seconds=0, qualifier_bar_influence=good)
        assert t._qualifier_bar_influence == float(good) and t.report.ok

    def test_strict_raises_instead(self):
        with pytest.raises(BarKeeperError):
            BarKeeper(FakeBar(), qualifier_bar_influence="half", strict=True)

    @pytest.mark.parametrize("influence,goes_in_as", [(1.1, 9.4), (-0.1, 4.6), (0.5, 7.0), (1, 9.0), (0, 5.0)])
    def test_the_value_goes_in_as_the_bar_plus_the_influence_of_the_way_to_the_score(self, influence, goes_in_as):
        """With the bar at 5 and a score of 9: a value beyond 0 to 1 overshoots at either end, as it is not clamped."""
        t, bar = bar_keeper(level=5.0, qualifier_bar_influence=influence)
        t.rule(9.0, 1.0)
        assert bar.added == [(pytest.approx(goes_in_as), 1.0)]


class TestAHeldScoreAndQualifierBarInfluence:
    """A score held by the cooldown stood above the bar like a qualifier, so `qualifier_bar_influence` must apply to it too."""

    @staticmethod
    def level_after_a_held_nine(influence):
        bar = PercentileBar(100, 600, 5, 6.0)
        t = BarKeeper(bar, 60, qualifier_bar_influence=influence)
        for i, score in enumerate([2.0, 3.0, 3.0, 4.0, 3.0]):
            t.rule(score, float(i))
        assert t.rule(7.0, 10.0) is Ruling.QUALIFIED       # fires: above the bar of the room's best, 4
        assert t.rule(9.0, 20.0) is Ruling.HELD            # above the bar, inside the 60 s cooldown
        return bar.level_at(30.0)

    def test_with_no_influence_a_held_score_does_not_raise_the_bar(self):
        assert self.level_after_a_held_nine(0.0) == 4.0   # the 7 that fired and the 9 that was held both went in as the bar itself, 4

    def test_with_full_influence_a_held_score_joins_the_bar(self):
        assert self.level_after_a_held_nine(1.0) == 9.0
