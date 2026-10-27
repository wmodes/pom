"""Tests for pom.scoring.convo_interest_tracker: the window, the fence and the trigger."""
import logging
import random
import statistics

import pytest

from pom.scoring.convo_interest_tracker import (DEFAULT_COOLDOWN_SECONDS, DEFAULT_FENCE_MULTIPLE, DEFAULT_MIN_SCORES,
                                                DEFAULT_WINDOW_SECONDS, ConvoInterestTracker, ConvoInterestTrackerError,
                                                ScoreEffect)


def tracker(**options):
    """A tracker for the tests: five scores make a window (not the default 20) and the window is 60 seconds, to keep
    the arithmetic short, and there is no cooldown unless a test asks for one. The scores 1 to 5 have Q1 2, Q3 4 and
    IQR 2, so the bar is 4 + 2 x fence_multiple."""
    options.setdefault("min_scores", 5)
    options.setdefault("window_seconds", 60)
    options.setdefault("cooldown_seconds", 0)
    return ConvoInterestTracker(**options)


def feed(t, scores, start=0.0, step=1.0):
    """Records the scores one after another, `step` seconds apart from `start`; returns what happened to each."""
    return [t.record(score, start + i * step) for i, score in enumerate(scores)]


def warmed_up(**options):
    """A tracker that has been given the scores 1 to 5 at seconds 0 to 4: Q1 2, Q3 4, IQR 2."""
    t = tracker(**options)
    assert feed(t, [1, 2, 3, 4, 5]) == [ScoreEffect.COUNTED] * 5
    return t


class TestTheDefaults:
    def test_the_starting_values(self):
        assert (DEFAULT_FENCE_MULTIPLE, DEFAULT_WINDOW_SECONDS, DEFAULT_MIN_SCORES, DEFAULT_COOLDOWN_SECONDS) == (1.0, 300.0, 20, 60.0)

    def test_good_settings_are_quiet(self):
        assert ConvoInterestTracker().report.ok
        assert tracker(fence_multiple=0, window_seconds=0.5, min_scores=2, cooldown_seconds=0).report.ok


class TestTheFence:
    def test_the_bar_is_q3_plus_the_multiple_of_the_iqr(self):
        assert warmed_up(fence_multiple=1.0).trigger_level_at(5) == pytest.approx(6.0)     # 4 + 1 x 2
        assert warmed_up(fence_multiple=1.5).trigger_level_at(5) == pytest.approx(7.0)     # 4 + 1.5 x 2
        assert warmed_up(fence_multiple=0.5).trigger_level_at(5) == pytest.approx(5.0)

    def test_a_multiple_of_zero_puts_the_bar_at_the_upper_quartile(self):
        assert warmed_up(fence_multiple=0).trigger_level_at(5) == pytest.approx(4.0)

    def test_the_default_multiple_is_one(self):
        t = ConvoInterestTracker(min_scores=5)
        feed(t, [1, 2, 3, 4, 5])
        assert t.trigger_level_at(5) == pytest.approx(6.0)

    def test_a_score_must_be_above_the_bar_not_just_at_it(self):
        t = warmed_up()
        assert t.record(6.0, 5) is ScoreEffect.COUNTED
        assert warmed_up().record(6.01, 5) is ScoreEffect.TRIGGERED

    def test_a_much_higher_score_triggers(self):
        assert warmed_up().record(9, 5) is ScoreEffect.TRIGGERED

    def test_a_low_score_does_not(self):
        assert warmed_up().record(1, 5) is ScoreEffect.COUNTED

    def test_a_score_is_judged_against_the_window_before_it_joins(self):
        t = warmed_up()
        level = t.trigger_level_at(5)
        assert t.record(100, 5) is ScoreEffect.TRIGGERED            # it would raise Q3 itself, but is judged before
        assert t.trigger_level_at(6) > level

    def test_a_triggering_score_joins_the_window_like_any_other(self):
        t = warmed_up()
        t.record(9, 5)
        assert t.counted == 6 and t.window_size_at(6) == 6

    def test_a_dull_room_and_a_lively_room_need_different_scores(self):
        dull, lively = tracker(), tracker()
        feed(dull, [2, 3, 3, 3, 4])
        feed(lively, [6, 7, 7, 7, 8])
        assert dull.record(6, 5) is ScoreEffect.TRIGGERED
        assert lively.record(6, 5) is ScoreEffect.COUNTED
        assert lively.record(10, 6) is ScoreEffect.TRIGGERED

    def test_a_flat_room_triggers_on_anything_above_it(self):
        t = tracker()
        feed(t, [3, 3, 3, 3, 3])
        assert t.trigger_level_at(5) == pytest.approx(3.0)           # Q3 = 3 and the IQR = 0
        assert t.record(3, 5) is ScoreEffect.COUNTED and t.record(3.1, 6) is ScoreEffect.TRIGGERED

    def test_the_lowest_scores_count_there_is_no_floor(self):
        t = tracker()
        assert feed(t, [0, 0, 0, 0, 0]) == [ScoreEffect.COUNTED] * 5
        assert t.trigger_level_at(5) == 0.0 and t.counted == 5

    def test_low_scores_widen_the_fence(self):
        without, with_lows = tracker(), tracker()
        feed(without, [4, 4, 4, 4, 4, 4, 4, 4])
        feed(with_lows, [0, 0, 0, 0, 4, 4, 4, 4])
        assert with_lows.trigger_level_at(8) > without.trigger_level_at(8)


class TestTheCooldown:
    def test_a_second_strong_score_right_after_a_trigger_is_cooling_down(self):
        t = warmed_up(cooldown_seconds=60)
        assert t.record(9, 5) is ScoreEffect.TRIGGERED
        assert t.record(9, 6) is ScoreEffect.COOLING_DOWN

    def test_the_cooldown_ends_exactly_when_the_time_is_up(self):
        t = warmed_up(cooldown_seconds=60)
        t.record(9, 5)
        assert t.record(20, 64.9) is ScoreEffect.COOLING_DOWN           # each strong score is stronger than the last,
        assert t.record(50, 65) is ScoreEffect.TRIGGERED                # as the window's own Q3 rises: 60 s after second 5

    def test_a_score_that_cooled_down_does_not_restart_the_cooldown(self):
        t = warmed_up(cooldown_seconds=60)
        t.record(9, 5)                                                  # triggers
        assert t.record(20, 35) is ScoreEffect.COOLING_DOWN             # 30 s later
        assert t.record(50, 65) is ScoreEffect.TRIGGERED                # 60 s after the trigger, not after the 35

    def test_a_score_below_the_bar_during_the_cooldown_is_just_counted(self):
        t = warmed_up(cooldown_seconds=60)
        t.record(9, 5)
        assert t.record(2, 6) is ScoreEffect.COUNTED

    def test_a_score_that_cooled_down_still_joins_the_window(self):
        t = warmed_up(cooldown_seconds=60)
        t.record(9, 5)
        before = t.window_size_at(6), t.counted
        t.record(9, 6)
        assert (t.window_size_at(6), t.counted) == (before[0] + 1, before[1] + 1)

    def test_a_cooldown_of_zero_lets_every_strong_score_trigger(self):
        t = warmed_up(cooldown_seconds=0)
        assert [t.record(score, 5 + i) for i, score in enumerate((9, 20, 50))] == [ScoreEffect.TRIGGERED] * 3

    def test_a_burst_gives_one_trigger_and_the_next_comes_after_the_cooldown(self):
        t = tracker(cooldown_seconds=30, window_seconds=1000)
        feed(t, [1, 2, 3, 4, 5] * 4)                                    # a calm room, seconds 0 to 19
        effects = [t.record(20, 100 + i) for i in range(6)] + [t.record(100, 140)]
        assert effects == [ScoreEffect.TRIGGERED] + [ScoreEffect.COOLING_DOWN] * 5 + [ScoreEffect.TRIGGERED]

    def test_there_is_no_cooldown_before_the_first_trigger(self):
        t = warmed_up(cooldown_seconds=10_000)
        assert t.record(9, 5) is ScoreEffect.TRIGGERED


class TestTheWarmUp:
    def test_there_is_no_bar_before_enough_scores(self):
        t = tracker()
        assert t.trigger_level_at(0) is None
        feed(t, [5, 5, 5, 5])
        assert t.trigger_level_at(4) is None

    def test_nothing_triggers_before_the_window_holds_enough(self):
        t = tracker()
        assert ScoreEffect.TRIGGERED not in feed(t, [1, 1, 1, 1, 9])      # the fifth is only the fifth to arrive

    def test_the_sixth_score_can_be_the_first_to_trigger(self):
        t = tracker()
        feed(t, [1, 2, 3, 4, 5])
        assert t.record(9, 5) is ScoreEffect.TRIGGERED

    def test_the_minimum_is_a_setting(self):
        t = tracker(min_scores=2)
        assert feed(t, [1, 5, 9]) == [ScoreEffect.COUNTED, ScoreEffect.COUNTED, ScoreEffect.TRIGGERED]
        t = tracker(min_scores=3)
        assert feed(t, [1, 5, 9, 20]) == [ScoreEffect.COUNTED] * 3 + [ScoreEffect.TRIGGERED]       # Q3 7, IQR 4: the bar is 11

    def test_while_warming_up_the_window_size_is_all_it_holds(self):
        t = tracker()
        feed(t, [1, 2, 3])
        assert t.window_size_at(3) == 3


class TestTheWindow:
    def test_scores_older_than_the_window_age_out(self):
        short, long_ = tracker(window_seconds=10), tracker(window_seconds=1000)
        for t in (short, long_):
            feed(t, [1] * 10)                                         # a dull stretch, seconds 0 to 9
            feed(t, [9] * 10, start=100)                              # a lively one, seconds 100 to 109
        assert short.trigger_level_at(110) == pytest.approx(9.0)       # only the lively stretch is left
        assert long_.trigger_level_at(110) == pytest.approx(17.0)      # both stretches: Q1 1, Q3 9, IQR 8
        assert short.record(10, 110) is ScoreEffect.TRIGGERED
        assert long_.record(10, 110) is ScoreEffect.COUNTED

    def test_a_score_exactly_at_the_edge_of_the_window_is_still_in_it(self):
        t = tracker(window_seconds=10)
        feed(t, [1, 2, 3, 4, 5, 6], start=0, step=2)                  # seconds 0, 2, ... 10
        assert t.window_size_at(10) == 6                              # the one at second 0 is exactly 10 s back
        assert t.window_size_at(10.5) == 5

    def test_a_quiet_room_keeps_its_last_few_scores_however_old(self):
        t = tracker(window_seconds=10)
        feed(t, [1, 2, 3, 4, 5], step=100)                            # a score every hundred seconds
        assert t.window_size_at(500) == 5
        assert t.trigger_level_at(500) == pytest.approx(6.0)
        assert t.record(7, 500) is ScoreEffect.TRIGGERED

    def test_the_window_is_the_larger_of_the_time_and_the_count(self):
        t = tracker(window_seconds=10)
        feed(t, list(range(1, 31)), step=1)                           # thirty scores, one a second
        assert t.window_size_at(30) == 10                             # the last 10 seconds: more than 5, so time wins
        feed(t, [5], start=500)
        assert t.window_size_at(500) == 5                             # only itself in 10 s: the count wins

    def test_a_busy_room_and_a_quiet_one_both_get_a_stretch_of_time(self):
        busy, quiet = tracker(window_seconds=60, min_scores=5), tracker(window_seconds=60, min_scores=5)
        feed(busy, [3] * 600, step=0.5)                               # two scores a second for five minutes
        feed(quiet, [3] * 20, step=15)                                # one every fifteen seconds
        assert busy.window_size_at(300) == 120
        assert quiet.window_size_at(300) == 5                         # 60 s holds only 4: the count of 5 wins

    def test_memory_is_bounded(self):
        t = tracker(window_seconds=60, min_scores=5)
        feed(t, [3] * 10_000, step=1)
        assert len(t._recent) <= 61

    def test_a_time_that_steps_backwards_is_held_at_the_latest(self):
        t = tracker(window_seconds=60)
        feed(t, [1] * 5, start=0)                                      # a dull stretch, seconds 0 to 4
        feed(t, [9] * 5, start=100)                                    # a lively one, seconds 100 to 104
        assert t.window_size_at(1) == 5                                # asked about second 1, it is still second 104
        assert t.trigger_level_at(1) == pytest.approx(9.0)             # only the lively stretch is in the window
        assert t.record(10, 1) is ScoreEffect.TRIGGERED
        assert t.window_size_at(104) == 6                              # it joined at second 104; filed under second 1 it would be out
        assert t.window_size_at(1) == 6


class TestWhatIsIgnored:
    @pytest.mark.parametrize("junk", [None, "7", "", True, False, [5], {}, float("nan"), float("inf"), float("-inf")])
    def test_what_is_not_a_score_changes_nothing(self, junk):
        t = warmed_up()
        before = (t.counted, t.trigger_level_at(5), t.window_size_at(5))
        assert t.record(junk, 5) is ScoreEffect.IGNORED
        assert (t.counted, t.trigger_level_at(5), t.window_size_at(5)) == before

    @pytest.mark.parametrize("junk", [None, "7", True, [5], float("nan"), float("inf")])
    def test_a_time_that_is_not_a_number_changes_nothing(self, junk):
        t = warmed_up()
        before = (t.counted, t.trigger_level_at(5))
        assert t.record(9, junk) is ScoreEffect.IGNORED
        assert (t.counted, t.trigger_level_at(5)) == before
        assert t.trigger_level_at(junk) is None

    def test_unscored_fragments_between_scores_do_not_disturb_the_trigger(self):
        t = tracker()
        feed(t, [1, None, 2, None, 3, None, 4, 5])
        assert t.counted == 5 and t.record(9, 9) is ScoreEffect.TRIGGERED

    def test_a_zero_is_a_real_score_not_a_missing_one(self):
        assert tracker().record(0, 0) is ScoreEffect.COUNTED


class TestAgainstAPlainReference:
    """The tracker against a few lines that do the same thing the slow, obvious way."""

    @staticmethod
    def reference(events, window_seconds, min_scores, multiple, cooldown):
        held, out, last_trigger = [], [], None
        for now, score in events:
            if len(held) < min_scores:
                bar = None
            else:
                window = [s for t, s in held if t >= now - window_seconds]
                if len(window) < min_scores:
                    window = [s for _, s in held[-min_scores:]]
                q1, _, q3 = statistics.quantiles(window, n=4, method="inclusive")
                bar = q3 + multiple * (q3 - q1)
            fires = bar is not None and score > bar and (last_trigger is None or now - last_trigger >= cooldown)
            if fires:
                last_trigger = now
            out.append(fires)
            held.append((now, score))
        return out

    @pytest.mark.parametrize("seed,gap,multiple,cooldown", [(1, 1.0, 1.0, 0), (2, 0.2, 0.6, 0), (3, 40.0, 1.5, 0),
                                                            (4, 5.0, 0.0, 0), (5, 1.0, 0.6, 60), (6, 5.0, 0.0, 30)])
    def test_the_same_triggers_as_the_reference(self, seed, gap, multiple, cooldown):
        rng = random.Random(seed)
        now, events = 0.0, []
        for _ in range(600):
            now += rng.uniform(0.1, 2 * gap)
            events.append((now, round(rng.choice([1.0, 1.0, 2.0, 3.0, 3.5, 4.0, 5.0, 5.5, 6.0, 7.0, 8.0]) + rng.random(), 2)))
        t = ConvoInterestTracker(multiple, 120, 12, cooldown)
        got = [t.record(score, when) is ScoreEffect.TRIGGERED for when, score in events]
        assert got == self.reference(events, 120, 12, multiple, cooldown)
        assert any(got) and not all(got)


class TestBadSettings:
    @pytest.mark.parametrize("bad", [-0.1, "1", None, True, [1], float("nan"), float("inf")])
    def test_a_bad_fence_multiple_means_the_default_with_a_warning(self, bad):
        t = tracker(fence_multiple=bad)
        assert len(t.report.warnings) == 1 and "fence_multiple" in t.report.warnings[0]
        feed(t, [1, 2, 3, 4, 5])
        assert t.trigger_level_at(5) == pytest.approx(6.0)             # the default of 1

    @pytest.mark.parametrize("bad", [0, -1, "60", None, True, [60], float("nan"), float("inf")])
    def test_a_bad_window_means_the_default_with_a_warning(self, bad):
        t = tracker(window_seconds=bad)
        assert len(t.report.warnings) == 1 and "window_seconds" in t.report.warnings[0]
        feed(t, [1] * 10, step=20)                                     # seconds 0 to 180
        assert t.window_size_at(250) == 10                             # the default 300 s holds them all

    @pytest.mark.parametrize("bad", [0, 1, -1, 2.5, "5", None, True, [5]])
    def test_a_bad_minimum_means_the_default_with_a_warning(self, bad):
        t = tracker(min_scores=bad)
        assert len(t.report.warnings) == 1 and "min_scores" in t.report.warnings[0]
        assert ScoreEffect.TRIGGERED not in feed(t, list(range(1, 20)) + [99])        # the twentieth is only the twentieth
        assert t.record(99, 30) is ScoreEffect.TRIGGERED               # the twentieth count was the first to allow it

    @pytest.mark.parametrize("bad", [-1, -0.1, "60", None, True, [60], float("nan"), float("inf")])
    def test_a_bad_cooldown_means_the_default_with_a_warning(self, bad):
        t = tracker(cooldown_seconds=bad)
        assert len(t.report.warnings) == 1 and "cooldown_seconds" in t.report.warnings[0]
        t = warmed_up(cooldown_seconds=bad)
        assert t.record(9, 5) is ScoreEffect.TRIGGERED and t.record(9, 30) is ScoreEffect.COOLING_DOWN   # the default 60 s

    def test_two_bad_settings_give_two_warnings(self):
        assert len(ConvoInterestTracker(fence_multiple=-1, window_seconds=0).report.warnings) == 2

    @pytest.mark.parametrize("option,bad,pattern", [("fence_multiple", -1, "fence_multiple"),
                                                    ("window_seconds", 0, "window_seconds"),
                                                    ("min_scores", 1, "min_scores"),
                                                    ("cooldown_seconds", -1, "cooldown_seconds")])
    def test_strict_mode_raises(self, option, bad, pattern):
        with pytest.raises(ConvoInterestTrackerError, match=pattern):
            tracker(strict=True, **{option: bad})

    def test_strict_mode_with_good_settings_does_not_raise(self):
        assert tracker(strict=True).report.ok


class TestLogging:
    def test_only_numbers_are_logged(self, caplog):
        with caplog.at_level(logging.DEBUG):
            t = tracker()
            feed(t, [1, 2, 3, 4, 5, 9])
        assert "bar 6.000" in caplog.text and "triggered" in caplog.text
