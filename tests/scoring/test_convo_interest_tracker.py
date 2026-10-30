"""Tests for pom.scoring.convo_interest_tracker: the trigger decision, the cooldown and the time, over any TriggerBar.

Most tests use a fake bar the test controls, so only the tracker is under test. The last classes run the tracker over each real bar.
"""
import logging
import random

import pytest

from pom.scoring.convo_interest_tracker import (DEFAULT_COOLDOWN_SECONDS, ConvoInterestTracker, ConvoInterestTrackerError,
                                                ScoreEffect)
from pom.scoring.decaying_quantile_bar import DecayingQuantileBar
from pom.scoring.moving_average_bar import MovingAverageBar
from pom.scoring.percentile_bar import PercentileBar
from pom.scoring.trigger_bar import TriggerBar


class FakeBar(TriggerBar):
    """A bar the test sets: it answers `level` and records what it was asked and given."""

    def __init__(self, level=5.0, ignored=()):
        self.level, self.ignored, self.added, self.asked, self.sizes = level, set(ignored), [], [], []

    def level_at(self, now):
        self.asked.append(now)
        return self.level

    def window_size_at(self, now):
        self.sizes.append(now)
        return 7

    def add(self, score, now, triggered):
        self.added.append((score, now, triggered))

    def ignores(self, score):
        return score in self.ignored

    def describe(self):
        return "a fake bar"


def tracker(level=5.0, cooldown=0, ignored=(), **options):
    bar = FakeBar(level, ignored)
    return ConvoInterestTracker(bar, cooldown_seconds=cooldown, **options), bar


class TestTheDefaults:
    def test_the_starting_value(self):
        assert DEFAULT_COOLDOWN_SECONDS == 60.0

    def test_with_no_bar_given_it_uses_a_percentile_bar(self):
        t = ConvoInterestTracker()
        assert isinstance(t._bar, PercentileBar) and t.report.ok

    def test_good_settings_are_quiet(self):
        assert ConvoInterestTracker(FakeBar(), cooldown_seconds=0).report.ok


class TestTheTrigger:
    def test_a_score_above_the_bar_triggers(self):
        t, _ = tracker(level=5.0)
        assert t.record(5.01, 1.0) is ScoreEffect.TRIGGERED

    def test_a_score_at_the_bar_does_not(self):
        t, _ = tracker(level=5.0)
        assert t.record(5.0, 1.0) is ScoreEffect.COUNTED

    def test_a_score_below_the_bar_does_not(self):
        t, _ = tracker(level=5.0)
        assert t.record(1.0, 1.0) is ScoreEffect.COUNTED

    def test_a_bar_with_no_level_never_triggers(self):
        t, _ = tracker(level=None)
        assert t.record(9.0, 1.0) is ScoreEffect.COUNTED

    def test_a_negative_bar_is_a_bar(self):
        t, _ = tracker(level=-1.0)
        assert t.record(0.0, 1.0) is ScoreEffect.TRIGGERED

    def test_a_score_is_judged_against_the_bar_before_it_is_handed_over(self):
        t, bar = tracker(level=5.0)
        order = []
        bar.level_at = lambda now: order.append("asked") or 5.0
        bar.add = lambda score, now, triggered: order.append("added")
        t.record(9.0, 1.0)
        assert order == ["asked", "added"]

    def test_every_counted_score_is_handed_to_the_bar_with_the_time_and_whether_it_triggered(self):
        t, bar = tracker(level=5.0)
        t.record(9.0, 10.0)
        t.record(2.0, 11.0)
        assert bar.added == [(9.0, 10.0, True), (2.0, 11.0, False)]

    def test_a_score_is_handed_over_as_a_float(self):
        t, bar = tracker()
        t.record(7, 1.0)
        assert isinstance(bar.added[0][0], float)

    def test_the_counted_total_grows(self):
        t, _ = tracker()
        for i in range(4):
            t.record(1.0, float(i))
        assert t.counted == 4


class TestWhatIsIgnored:
    @pytest.mark.parametrize("junk", [None, "7", "", True, False, [5], {}, float("nan"), float("inf"), float("-inf")])
    def test_what_is_not_a_score_changes_nothing(self, junk):
        t, bar = tracker()
        assert t.record(junk, 1.0) is ScoreEffect.IGNORED
        assert t.counted == 0 and bar.added == []

    @pytest.mark.parametrize("junk", [None, "7", True, [5], float("nan"), float("inf")])
    def test_a_time_that_is_not_a_number_changes_nothing(self, junk):
        t, bar = tracker()
        assert t.record(9.0, junk) is ScoreEffect.IGNORED
        assert t.counted == 0 and bar.added == []
        assert t.trigger_level_at(junk) is None

    def test_a_score_the_bar_ignores_is_ignored(self):
        t, bar = tracker(ignored={0.1})
        assert t.record(0.1, 1.0) is ScoreEffect.IGNORED
        assert t.counted == 0 and bar.added == [] and bar.asked == []

    def test_a_zero_is_a_real_score_not_a_missing_one(self):
        t, _ = tracker()
        assert t.record(0, 0) is ScoreEffect.COUNTED

    def test_unscored_fragments_between_scores_do_not_disturb_anything(self):
        t, bar = tracker(level=5.0)
        for i, score in enumerate([1, None, 2, None, 9]):
            t.record(score, float(i))
        assert t.counted == 3 and bar.added[-1] == (9.0, 4.0, True)


class TestTheCooldown:
    def test_a_second_strong_score_right_after_a_trigger_is_cooling_down(self):
        t, _ = tracker(level=5.0, cooldown=60)
        assert t.record(9, 5) is ScoreEffect.TRIGGERED
        assert t.record(9, 6) is ScoreEffect.COOLING_DOWN

    def test_the_cooldown_ends_exactly_when_the_time_is_up(self):
        t, _ = tracker(level=5.0, cooldown=60)
        t.record(9, 5)
        assert t.record(9, 64.9) is ScoreEffect.COOLING_DOWN
        assert t.record(9, 65) is ScoreEffect.TRIGGERED

    def test_a_score_that_cooled_down_does_not_restart_the_cooldown(self):
        t, _ = tracker(level=5.0, cooldown=60)
        t.record(9, 5)
        assert t.record(9, 35) is ScoreEffect.COOLING_DOWN
        assert t.record(9, 65) is ScoreEffect.TRIGGERED            # 60 s after the trigger, not after the 35

    def test_a_score_below_the_bar_during_the_cooldown_is_just_counted(self):
        t, _ = tracker(level=5.0, cooldown=60)
        t.record(9, 5)
        assert t.record(2, 6) is ScoreEffect.COUNTED

    def test_a_score_that_cooled_down_is_still_handed_to_the_bar_and_counted(self):
        t, bar = tracker(level=5.0, cooldown=60)
        t.record(9, 5)
        t.record(9, 6)
        assert bar.added == [(9.0, 5.0, True), (9.0, 6.0, False)] and t.counted == 2

    def test_a_cooldown_of_zero_lets_every_strong_score_trigger(self):
        t, _ = tracker(level=5.0, cooldown=0)
        assert [t.record(9, 5 + i) for i in range(3)] == [ScoreEffect.TRIGGERED] * 3

    def test_there_is_no_cooldown_before_the_first_trigger(self):
        t, _ = tracker(level=5.0, cooldown=10_000)
        assert t.record(9, 5) is ScoreEffect.TRIGGERED

    def test_a_burst_gives_one_trigger_and_the_next_comes_after_the_cooldown(self):
        t, _ = tracker(level=5.0, cooldown=30)
        effects = [t.record(20, 100 + i) for i in range(6)] + [t.record(20, 140)]
        assert effects == [ScoreEffect.TRIGGERED] + [ScoreEffect.COOLING_DOWN] * 5 + [ScoreEffect.TRIGGERED]


class TestTheTime:
    def test_a_time_that_steps_backwards_is_held_at_the_latest(self):
        t, bar = tracker(level=5.0)
        t.record(1.0, 100.0)
        t.record(1.0, 40.0)
        assert bar.asked == [100.0, 100.0] and bar.added[-1][1] == 100.0

    def test_the_questions_use_the_held_time_too(self):
        t, bar = tracker()
        t.record(1.0, 100.0)
        t.trigger_level_at(1.0)
        t.window_size_at(1.0)
        assert bar.asked[-1] == 100.0 and bar.sizes[-1] == 100.0

    def test_a_later_time_is_used_as_it_is(self):
        t, bar = tracker()
        t.record(1.0, 100.0)
        t.trigger_level_at(250.0)
        assert bar.asked[-1] == 250.0

    def test_before_any_score_the_time_asked_about_is_used(self):
        t, bar = tracker()
        t.trigger_level_at(7.0)
        t.window_size_at(8.0)
        assert bar.asked == [7.0] and bar.sizes == [8.0]

    def test_a_window_size_asked_with_no_usable_time_uses_the_latest_time(self):
        t, bar = tracker()
        t.record(1.0, 100.0)
        assert t.window_size_at(None) == 7 and bar.sizes[-1] == 100.0

    def test_a_window_size_asked_with_no_usable_time_before_any_score_uses_zero(self):
        t, bar = tracker()
        t.window_size_at(float("nan"))
        assert bar.sizes[-1] == 0.0


class TestTheQuestions:
    def test_the_bar_level_is_the_bars(self):
        t, _ = tracker(level=6.25)
        assert t.trigger_level_at(1.0) == 6.25

    def test_the_window_size_is_the_bars(self):
        t, _ = tracker()
        assert t.window_size_at(1.0) == 7

    def test_describe_adds_the_cooldown_to_the_bars_line(self):
        t, _ = tracker(cooldown=60)
        assert t.describe() == "a fake bar; at most one trigger every 60 s"

    def test_the_questions_change_nothing(self):
        t, bar = tracker()
        t.trigger_level_at(1.0)
        t.window_size_at(1.0)
        assert t.counted == 0 and bar.added == []


class TestBadSettings:
    @pytest.mark.parametrize("bad", [-1, -0.1, "60", None, True, [60], float("nan"), float("inf")])
    def test_a_bad_cooldown_means_the_default_with_a_warning(self, bad):
        t, _ = tracker(level=5.0, cooldown=bad)
        assert len(t.report.warnings) == 1 and "cooldown_seconds" in t.report.warnings[0]
        assert t.record(9, 5) is ScoreEffect.TRIGGERED and t.record(9, 30) is ScoreEffect.COOLING_DOWN     # the default 60 s

    @pytest.mark.parametrize("not_a_bar", ["percentile", 5, [PercentileBar()], object()])
    def test_something_that_is_not_a_bar_means_the_default_bar_with_a_warning(self, not_a_bar):
        t = ConvoInterestTracker(not_a_bar)
        assert isinstance(t._bar, PercentileBar)
        assert len(t.report.warnings) == 1 and "bar" in t.report.warnings[0]

    def test_strict_mode_raises(self):
        with pytest.raises(ConvoInterestTrackerError, match="cooldown_seconds"):
            ConvoInterestTracker(FakeBar(), cooldown_seconds=-1, strict=True)
        with pytest.raises(ConvoInterestTrackerError, match="bar"):
            ConvoInterestTracker("nope", strict=True)

    def test_strict_mode_with_good_settings_does_not_raise(self):
        assert ConvoInterestTracker(FakeBar(), strict=True).report.ok


class TestOverARealPercentileBar:
    """The tracker joined to the PercentileBar, against a few lines that do the same thing the slow, obvious way."""

    @staticmethod
    def reference(events, window_seconds, min_scores, percentile, cooldown, starting_bar):
        held, out, last_trigger = [], [], None
        for now, score in events:
            if len(held) < min_scores:
                level = starting_bar
            else:
                window = [s for t, s in held if t >= now - window_seconds]
                if len(window) < min_scores:
                    window = [s for _, s in held[-min_scores:]]
                ranked = sorted(window)
                level = next(v for i, v in enumerate(ranked, start=1) if i * 100 >= percentile * len(ranked))
            fires = score > level and (last_trigger is None or now - last_trigger >= cooldown)
            if fires:
                last_trigger = now
            out.append(fires)
            held.append((now, score))
        return out

    @pytest.mark.parametrize("seed,gap,percentile,cooldown", [(1, 1.0, 100, 0), (2, 0.2, 99.5, 0), (3, 40.0, 90, 0),
                                                              (4, 5.0, 50, 0), (5, 1.0, 97.5, 60), (6, 5.0, 100, 30)])
    def test_the_same_triggers_as_the_reference(self, seed, gap, percentile, cooldown):
        rng = random.Random(seed)
        now, events = 0.0, []
        for _ in range(600):
            now += rng.uniform(0.1, 2 * gap)
            events.append((now, round(rng.choice([1.0, 1.0, 2.0, 3.0, 3.5, 4.0, 5.0, 5.5, 6.0, 7.0, 8.0]) + rng.random(), 2)))
        t = ConvoInterestTracker(PercentileBar(percentile, 120, 12, 6.8), cooldown)
        got = [t.record(score, when) is ScoreEffect.TRIGGERED for when, score in events]
        assert got == self.reference(events, 120, 12, percentile, cooldown, 6.8)
        assert any(got) and not all(got)

    def test_a_dull_room_and_a_lively_room_need_different_scores(self):
        dull, lively = (ConvoInterestTracker(PercentileBar(100, 60, 5, 100.0), 0) for _ in range(2))
        for i, (a, b) in enumerate(zip([2, 3, 3, 3, 4], [6, 7, 7, 7, 8])):
            dull.record(a, float(i))
            lively.record(b, float(i))
        assert dull.record(6, 5) is ScoreEffect.TRIGGERED and lively.record(6, 5) is ScoreEffect.COUNTED

    def test_the_starting_line_lets_a_strong_score_fire_in_the_first_minute(self):
        t = ConvoInterestTracker(PercentileBar(starting_bar=6.8), 0)
        assert t.record(7.0, 1.0) is ScoreEffect.TRIGGERED

    def test_leaving_triggers_out_lets_a_good_score_follow_a_great_one(self):
        keep = ConvoInterestTracker(PercentileBar(100, 300, 5, 100.0, exclude_triggers=False), 0)
        leave = ConvoInterestTracker(PercentileBar(100, 300, 5, 100.0, exclude_triggers=True), 0)
        for t in (keep, leave):
            for i in range(5):
                t.record(3.0, float(i))
            t.record(8.9, 5.0)
        keep_bar, leave_bar = keep.trigger_level_at(6.0), leave.trigger_level_at(6.0)
        assert keep_bar == 8.9 and leave_bar == 3.0


class TestOverARealDecayingQuantileBar:
    def test_a_score_above_the_starting_line_triggers_at_first(self):
        t = ConvoInterestTracker(DecayingQuantileBar(starting_bar=6.8), 0)
        assert t.record(7.5, 1.0) is ScoreEffect.TRIGGERED

    def test_triggering_scores_are_left_out_when_asked(self):
        t = ConvoInterestTracker(DecayingQuantileBar(starting_bar=6.8, exclude_triggers=True), 0)
        t.record(9.0, 1.0)
        assert t.window_size_at(1.0) == 0

    def test_scores_that_did_not_trigger_are_kept(self):
        t = ConvoInterestTracker(DecayingQuantileBar(starting_bar=6.8, exclude_triggers=True), 0)
        t.record(2.0, 1.0)
        assert t.window_size_at(1.0) == 1

    def test_the_bar_follows_the_room_down(self):
        t = ConvoInterestTracker(DecayingQuantileBar(starting_bar=6.8, percentile=90, half_life_seconds=30), 0)
        for i in range(200):
            t.record(2.0, float(i))
        assert t.trigger_level_at(200.0) < 3.0

    def test_describe_includes_the_cooldown(self):
        assert "at most one trigger every 60 s" in ConvoInterestTracker(DecayingQuantileBar(), 60).describe()


class TestOverARealMovingAverageBar:
    def test_a_score_under_the_floor_is_ignored(self):
        t = ConvoInterestTracker(MovingAverageBar(), 0)
        assert t.record(0.2, 1.0) is ScoreEffect.IGNORED and t.counted == 0

    def test_a_score_far_above_the_average_triggers(self):
        t = ConvoInterestTracker(MovingAverageBar(fraction=0.25, starting_bar=4.0), 0)
        assert t.record(6.0, 1.0) is ScoreEffect.TRIGGERED

    def test_time_does_not_change_the_bar(self):
        t = ConvoInterestTracker(MovingAverageBar(), 0)
        t.record(3.0, 1.0)
        assert t.trigger_level_at(2.0) == t.trigger_level_at(50_000.0)


class TestLogging:
    def test_only_numbers_are_logged(self, caplog):
        with caplog.at_level(logging.DEBUG):
            t, _ = tracker(level=5.0)
            t.record(9.0, 1.0)
        assert "bar 5.000" in caplog.text and "triggered" in caplog.text

    def test_a_missing_bar_level_is_logged_as_none(self, caplog):
        with caplog.at_level(logging.DEBUG):
            t, _ = tracker(level=None)
            t.record(9.0, 1.0)
        assert "bar none" in caplog.text
