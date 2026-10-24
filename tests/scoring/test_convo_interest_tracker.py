"""Tests for pom.scoring.convo_interest_tracker: the floor, the average and the trigger."""
import logging
import math

import pytest

from pom.scoring.convo_interest_tracker import (DEFAULT_FLOOR_IGNORE_FRACTION, DEFAULT_INTEREST_HALF_LIFE, DEFAULT_MIN_SCORES,
                                                DEFAULT_SCORE_RANGE, DEFAULT_TRIGGER_FRACTION, ConvoInterestTracker,
                                                ConvoInterestTrackerError, ScoreEffect)


class TestTheFloor:
    def test_the_default_floor_is_five_percent_of_the_zero_to_nine_scale(self):
        assert DEFAULT_FLOOR_IGNORE_FRACTION == 0.05 and DEFAULT_SCORE_RANGE == (0.0, 9.0)
        assert ConvoInterestTracker().avg_convo_floor == pytest.approx(0.45)

    def test_the_fraction_moves_the_floor_up_the_range(self):
        assert ConvoInterestTracker(floor_ignore_fraction=0.5).avg_convo_floor == pytest.approx(4.5)
        assert ConvoInterestTracker(floor_ignore_fraction=0.1).avg_convo_floor == pytest.approx(0.9)

    def test_a_fraction_of_zero_puts_the_floor_at_the_bottom_of_the_range(self):
        assert ConvoInterestTracker(floor_ignore_fraction=0).avg_convo_floor == 0.0

    def test_the_floor_follows_the_score_range(self):
        assert ConvoInterestTracker(0.1, score_range=(1, 11)).avg_convo_floor == pytest.approx(2.0)
        assert ConvoInterestTracker(0.5, score_range=(-10, 10)).avg_convo_floor == pytest.approx(0.0)

    def test_the_floor_cannot_be_assigned(self):
        with pytest.raises(AttributeError):
            ConvoInterestTracker().avg_convo_floor = 3.0

    def test_good_settings_are_quiet(self):
        assert ConvoInterestTracker(0.2, score_range=(0, 9)).report.ok


class TestIsAboveFloor:
    def test_scores_at_or_above_the_floor_count(self):
        tracker = ConvoInterestTracker()
        assert tracker.is_above_floor(0.45) and tracker.is_above_floor(0.46)
        assert tracker.is_above_floor(5) and tracker.is_above_floor(9.0)

    def test_scores_below_the_floor_are_ignored(self):
        tracker = ConvoInterestTracker()
        assert not tracker.is_above_floor(0.44) and not tracker.is_above_floor(0)

    def test_the_floor_itself_counts(self):
        tracker = ConvoInterestTracker(floor_ignore_fraction=0.5)
        assert tracker.is_above_floor(tracker.avg_convo_floor)

    def test_an_unscored_fragment_is_never_above_the_floor(self):
        assert not ConvoInterestTracker().is_above_floor(None)

    @pytest.mark.parametrize("junk", ["7", "", True, False, [5], {}, float("nan"), float("inf"), float("-inf")])
    def test_anything_that_is_not_a_number_is_no(self, junk):
        assert ConvoInterestTracker().is_above_floor(junk) is False

    def test_a_zero_floor_lets_every_real_score_through(self):
        tracker = ConvoInterestTracker(floor_ignore_fraction=0)
        assert tracker.is_above_floor(0) and not tracker.is_above_floor(-1)


class TestBadSettings:
    @pytest.mark.parametrize("bad", [-0.1, 1, 1.5, 5, "0.05", None, True, [0.05], float("nan"), float("inf")])
    def test_a_bad_fraction_means_the_default_with_a_warning(self, bad):
        tracker = ConvoInterestTracker(floor_ignore_fraction=bad)
        assert tracker.avg_convo_floor == pytest.approx(0.45)
        assert len(tracker.report.warnings) == 1 and "floor_ignore_fraction" in tracker.report.warnings[0]

    @pytest.mark.parametrize("bad", [(5, 5), (9, 0), (0,), (0, 9, 10), "09", None, 9, ("a", "b"), (True, 9),
                                     (0, float("nan")), (0, float("inf"))])
    def test_a_bad_range_means_the_default_with_a_warning(self, bad):
        tracker = ConvoInterestTracker(score_range=bad)
        assert tracker.avg_convo_floor == pytest.approx(0.45)
        assert len(tracker.report.warnings) == 1 and "score_range" in tracker.report.warnings[0]

    def test_two_bad_settings_give_two_warnings(self):
        assert len(ConvoInterestTracker(floor_ignore_fraction=2, score_range=(3, 1)).report.warnings) == 2

    def test_strict_mode_raises_on_a_bad_fraction(self):
        with pytest.raises(ConvoInterestTrackerError, match="floor_ignore_fraction"):
            ConvoInterestTracker(floor_ignore_fraction=2, strict=True)

    def test_strict_mode_raises_on_a_bad_range(self):
        with pytest.raises(ConvoInterestTrackerError, match="score_range"):
            ConvoInterestTracker(score_range=(9, 0), strict=True)

    def test_strict_mode_with_good_settings_does_not_raise(self):
        assert ConvoInterestTracker(0.1, score_range=(0, 9), strict=True).avg_convo_floor == pytest.approx(0.9)


class TestLogging:
    def test_the_floor_is_logged_at_debug_and_nothing_else_is(self, caplog):
        with caplog.at_level(logging.DEBUG):
            ConvoInterestTracker()
        assert "floor set at" in caplog.text


def half_life_for(weight):
    """The half-life, in scores, that makes each score move the average `weight` of the way (1 means all of it)."""
    return 1e-9 if weight >= 1 else math.log(0.5) / math.log(1 - weight)


def tracker(**options):
    """A tracker for the average and trigger tests: five scores must count (not the default ten) to keep the
    sequences short, and any setting can be changed."""
    options.setdefault("min_scores", 5)
    return ConvoInterestTracker(**options)


def feed(t, *scores):
    return [t.record(score) for score in scores]


class TestTheAverage:
    def test_the_defaults_are_the_starting_values(self):
        assert (DEFAULT_TRIGGER_FRACTION, DEFAULT_INTEREST_HALF_LIFE, DEFAULT_MIN_SCORES) == (0.10, 10.0, 10)

    def test_the_weight_comes_from_the_half_life(self):
        assert ConvoInterestTracker(interest_half_life=10).interest_weight == pytest.approx(1 - 0.5 ** 0.1)
        assert ConvoInterestTracker(interest_half_life=10).interest_weight == pytest.approx(0.067, abs=0.0005)
        assert ConvoInterestTracker(interest_half_life=1).interest_weight == pytest.approx(0.5)
        assert ConvoInterestTracker().interest_weight == pytest.approx(1 - 0.5 ** (1 / DEFAULT_INTEREST_HALF_LIFE))

    def test_a_scores_influence_halves_after_the_half_life(self):
        t = ConvoInterestTracker(floor_ignore_fraction=0, interest_half_life=4)
        t.record(8)
        for _ in range(4):
            t.record(0)                                           # four more scores pull the 8 down to half
        assert t.avg_convo_interest == pytest.approx(4.0)

    def test_a_longer_half_life_remembers_longer(self):
        short, long_ = ConvoInterestTracker(floor_ignore_fraction=0, interest_half_life=2), ConvoInterestTracker(floor_ignore_fraction=0, interest_half_life=20)
        for t in (short, long_):
            t.record(8)
            for _ in range(5):
                t.record(0)
        assert long_.avg_convo_interest > short.avg_convo_interest

    def test_there_is_no_average_and_no_trigger_level_before_a_score_counts(self):
        t = tracker()
        assert t.avg_convo_interest is None and t.trigger_level is None and t.counted == 0

    def test_the_first_score_that_counts_becomes_the_average(self):
        t = tracker()
        assert t.record(6) is ScoreEffect.COUNTED
        assert t.avg_convo_interest == 6.0 and t.counted == 1

    def test_each_later_score_moves_the_average_a_fraction_of_the_way_toward_itself(self):
        t = tracker(interest_half_life=half_life_for(0.1))
        feed(t, 6, 8)
        assert t.avg_convo_interest == pytest.approx(6.2)          # 6 + 0.1 * (8 - 6)
        feed(t, 2)
        assert t.avg_convo_interest == pytest.approx(5.78)         # 6.2 + 0.1 * (2 - 6.2)

    def test_a_weight_of_one_makes_the_average_the_latest_score(self):
        t = tracker(interest_half_life=half_life_for(1))
        feed(t, 6, 3)
        assert t.avg_convo_interest == 3.0

    def test_a_small_weight_forgets_slowly(self):
        slow, quick = tracker(interest_half_life=half_life_for(0.01)), tracker(interest_half_life=half_life_for(0.5))
        for t in (slow, quick):
            feed(t, 4, 8)
        assert abs(slow.avg_convo_interest - 4) < abs(quick.avg_convo_interest - 4)

    def test_a_steady_room_leaves_the_average_where_it_is(self):
        t = tracker()
        feed(t, 5, 5, 5, 5)
        assert t.avg_convo_interest == pytest.approx(5.0)

    def test_old_talk_fades_as_new_talk_comes_in(self):
        t = tracker(interest_half_life=half_life_for(0.2))
        feed(t, 2)
        feed(t, *([8] * 30))
        assert t.avg_convo_interest == pytest.approx(8.0, abs=0.01)

    @pytest.mark.parametrize("ignored", [None, 0.1, 0.44, 0, -3, "7", True, [5], float("nan"), float("inf")])
    def test_what_does_not_count_changes_nothing(self, ignored):
        t = tracker()
        feed(t, 5, 6)
        before = (t.avg_convo_interest, t.counted, t.trigger_level)
        assert t.record(ignored) is ScoreEffect.IGNORED
        assert (t.avg_convo_interest, t.counted, t.trigger_level) == before

    def test_the_first_score_can_be_ignored_and_the_average_waits(self):
        t = tracker()
        assert t.record(0.2) is ScoreEffect.IGNORED and t.avg_convo_interest is None
        assert t.record(7) is ScoreEffect.COUNTED and t.avg_convo_interest == 7.0

    def test_a_score_at_the_floor_counts(self):
        t = tracker()
        assert t.record(t.avg_convo_floor) is ScoreEffect.COUNTED

    def test_a_floor_of_zero_counts_a_zero(self):
        t = tracker(floor_ignore_fraction=0)
        assert t.record(0) is ScoreEffect.COUNTED and t.avg_convo_interest == 0.0

    def test_scores_below_the_floor_never_drag_the_average_down(self):
        t = tracker()
        feed(t, 6, 6, 6)
        feed(t, 0.1, 0.3, 0.2, 0.4)
        assert t.avg_convo_interest == pytest.approx(6.0) and t.counted == 3


class TestTheTrigger:
    def warmed_up(self, **options):
        """A tracker that has seen five scores of 5, so the average is 5 and the trigger may fire."""
        t = tracker(**options)
        assert feed(t, 5, 5, 5, 5, 5) == [ScoreEffect.COUNTED] * 5
        return t

    def test_the_trigger_level_is_the_average_plus_the_fraction(self):
        t = self.warmed_up()
        assert t.trigger_level == pytest.approx(5.5)                # 5 * 1.10

    def test_a_score_at_the_level_triggers(self):
        t = self.warmed_up()
        assert t.record(5.5) is ScoreEffect.TRIGGERED

    def test_a_score_just_under_the_level_does_not(self):
        t = self.warmed_up()
        assert t.record(5.49) is ScoreEffect.COUNTED

    def test_a_much_higher_score_triggers(self):
        assert self.warmed_up().record(9) is ScoreEffect.TRIGGERED

    def test_a_score_at_the_average_does_not_trigger_with_a_fraction_above_zero(self):
        assert self.warmed_up().record(5.0) is ScoreEffect.COUNTED

    def test_a_fraction_of_zero_triggers_on_anything_at_the_average_or_more(self):
        t = self.warmed_up(trigger_fraction=0)
        assert t.record(5.0) is ScoreEffect.TRIGGERED

    def test_the_fraction_sets_how_far_above_it_must_be(self):
        t = self.warmed_up(trigger_fraction=0.5)
        assert t.trigger_level == pytest.approx(7.5)
        assert t.record(7.4) is ScoreEffect.COUNTED
        t = self.warmed_up(trigger_fraction=0.5)
        assert t.record(7.5) is ScoreEffect.TRIGGERED

    def test_nothing_triggers_before_enough_scores_have_counted(self):
        t = tracker()
        effects = feed(t, 5, 5, 5, 5, 9)                            # the ninth point is only the fifth to arrive
        assert ScoreEffect.TRIGGERED not in effects and t.counted == 5

    def test_the_sixth_score_can_be_the_first_to_trigger(self):
        t = tracker()
        feed(t, 5, 5, 5, 5, 5)
        assert t.record(9) is ScoreEffect.TRIGGERED

    def test_the_minimum_is_a_setting(self):
        t = tracker(min_scores=1)
        assert feed(t, 5, 9) == [ScoreEffect.COUNTED, ScoreEffect.TRIGGERED]
        t = tracker(min_scores=3)
        assert feed(t, 5, 9, 9, 9) == [ScoreEffect.COUNTED, ScoreEffect.COUNTED, ScoreEffect.COUNTED, ScoreEffect.TRIGGERED]

    def test_a_score_is_judged_against_the_average_before_it_is_added(self):
        t = self.warmed_up(interest_half_life=half_life_for(1))                       # the average jumps to each new score
        assert t.record(5.5) is ScoreEffect.TRIGGERED               # judged against 5, not against itself

    def test_a_triggering_score_is_added_to_the_average(self):
        t = self.warmed_up()
        assert t.record(9) is ScoreEffect.TRIGGERED
        assert t.avg_convo_interest == pytest.approx(5 + t.interest_weight * (9 - 5)) and t.counted == 6

    def test_a_score_below_the_floor_never_triggers_even_when_the_average_is_tiny(self):
        t = tracker(trigger_fraction=0)
        feed(t, 0.5, 0.5, 0.5, 0.5, 0.5)
        assert t.record(0.4) is ScoreEffect.IGNORED

    def test_a_dull_room_and_a_lively_room_need_different_scores(self):
        dull, lively = tracker(), tracker()
        feed(dull, 3, 3, 3, 3, 3)
        feed(lively, 7, 7, 7, 7, 7)
        assert dull.record(5) is ScoreEffect.TRIGGERED
        assert lively.record(5) is ScoreEffect.COUNTED
        assert lively.record(8) is ScoreEffect.TRIGGERED

    def test_a_run_of_strong_talk_raises_the_bar(self):
        t = self.warmed_up()
        first = t.trigger_level
        feed(t, 9, 9, 9, 9, 9, 9)
        assert t.trigger_level > first

    def test_unscored_fragments_between_scores_do_not_disturb_the_trigger(self):
        t = tracker()
        feed(t, 5, None, 5, None, 5, None, 5, 5)
        assert t.counted == 5 and t.record(6) is ScoreEffect.TRIGGERED


class TestBadTriggerSettings:
    @pytest.mark.parametrize("bad", [-0.1, "0.1", None, True, [0.1], float("nan"), float("inf")])
    def test_a_bad_trigger_fraction_means_the_default_with_a_warning(self, bad):
        t = tracker(trigger_fraction=bad)
        assert t.report.warnings and "trigger_fraction" in t.report.warnings[0] and len(t.report.warnings) == 1
        feed(t, 5, 5, 5, 5, 5)
        assert t.trigger_level == pytest.approx(5.5)

    @pytest.mark.parametrize("bad", [0, -1, -0.5, "10", None, True, [10], float("nan"), float("inf")])
    def test_a_bad_half_life_means_the_default_with_a_warning(self, bad):
        t = tracker(interest_half_life=bad)
        assert len(t.report.warnings) == 1 and "interest_half_life" in t.report.warnings[0]
        feed(t, 6, 8)
        assert t.avg_convo_interest == pytest.approx(6 + (1 - 0.5 ** 0.1) * 2)

    @pytest.mark.parametrize("bad", [0, -1, 1.5, "5", None, True, [5]])
    def test_a_bad_minimum_means_the_default_with_a_warning(self, bad):
        t = tracker(min_scores=bad)
        assert len(t.report.warnings) == 1 and "min_scores" in t.report.warnings[0]
        assert ScoreEffect.TRIGGERED not in feed(t, *([5] * 9))
        assert feed(t, 9, 9) == [ScoreEffect.COUNTED, ScoreEffect.TRIGGERED]       # the tenth count is the first to trigger

    def test_good_trigger_settings_are_quiet(self):
        assert tracker(trigger_fraction=0, interest_half_life=half_life_for(1), min_scores=1).report.ok

    @pytest.mark.parametrize("option,bad,pattern", [("trigger_fraction", -1, "trigger_fraction"),
                                                    ("interest_half_life", 0, "interest_half_life"),
                                                    ("min_scores", 0, "min_scores")])
    def test_strict_mode_raises(self, option, bad, pattern):
        with pytest.raises(ConvoInterestTrackerError, match=pattern):
            tracker(strict=True, **{option: bad})

    def test_what_a_score_was_is_not_logged_beyond_numbers(self, caplog):
        with caplog.at_level(logging.DEBUG):
            t = tracker()
            feed(t, 6, 7)
        assert "average now" in caplog.text
