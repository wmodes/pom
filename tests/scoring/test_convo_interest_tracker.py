"""Tests for pom.scoring.convo_interest_tracker: the floor below which scores are ignored."""
import logging

import pytest

from pom.scoring.convo_interest_tracker import (DEFAULT_FLOOR_FRACTION, DEFAULT_SCORE_RANGE,
                                                ConvoInterestTracker, ConvoInterestTrackerError)


class TestTheFloor:
    def test_the_default_floor_is_five_percent_of_the_zero_to_nine_scale(self):
        assert DEFAULT_FLOOR_FRACTION == 0.05 and DEFAULT_SCORE_RANGE == (0.0, 9.0)
        assert ConvoInterestTracker().avg_convo_floor == pytest.approx(0.45)

    def test_the_fraction_moves_the_floor_up_the_range(self):
        assert ConvoInterestTracker(floor_fraction=0.5).avg_convo_floor == pytest.approx(4.5)
        assert ConvoInterestTracker(floor_fraction=0.1).avg_convo_floor == pytest.approx(0.9)

    def test_a_fraction_of_zero_puts_the_floor_at_the_bottom_of_the_range(self):
        assert ConvoInterestTracker(floor_fraction=0).avg_convo_floor == 0.0

    def test_the_floor_follows_the_score_range(self):
        assert ConvoInterestTracker(0.1, score_range=(1, 11)).avg_convo_floor == pytest.approx(2.0)
        assert ConvoInterestTracker(0.5, score_range=(-10, 10)).avg_convo_floor == pytest.approx(0.0)

    def test_the_floor_cannot_be_assigned(self):
        with pytest.raises(AttributeError):
            ConvoInterestTracker().avg_convo_floor = 3.0

    def test_good_settings_are_quiet(self):
        assert ConvoInterestTracker(0.2, (0, 9)).report.ok


class TestIsAboveFloor:
    def test_scores_at_or_above_the_floor_count(self):
        tracker = ConvoInterestTracker()
        assert tracker.is_above_floor(0.45) and tracker.is_above_floor(0.46)
        assert tracker.is_above_floor(5) and tracker.is_above_floor(9.0)

    def test_scores_below_the_floor_are_ignored(self):
        tracker = ConvoInterestTracker()
        assert not tracker.is_above_floor(0.44) and not tracker.is_above_floor(0)

    def test_the_floor_itself_counts(self):
        tracker = ConvoInterestTracker(floor_fraction=0.5)
        assert tracker.is_above_floor(tracker.avg_convo_floor)

    def test_an_unscored_fragment_is_never_above_the_floor(self):
        assert not ConvoInterestTracker().is_above_floor(None)

    @pytest.mark.parametrize("junk", ["7", "", True, False, [5], {}, float("nan"), float("inf"), float("-inf")])
    def test_anything_that_is_not_a_number_is_no(self, junk):
        assert ConvoInterestTracker().is_above_floor(junk) is False

    def test_a_zero_floor_lets_every_real_score_through(self):
        tracker = ConvoInterestTracker(floor_fraction=0)
        assert tracker.is_above_floor(0) and not tracker.is_above_floor(-1)


class TestBadSettings:
    @pytest.mark.parametrize("bad", [-0.1, 1, 1.5, 5, "0.05", None, True, [0.05], float("nan"), float("inf")])
    def test_a_bad_fraction_means_the_default_with_a_warning(self, bad):
        tracker = ConvoInterestTracker(floor_fraction=bad)
        assert tracker.avg_convo_floor == pytest.approx(0.45)
        assert len(tracker.report.warnings) == 1 and "floor_fraction" in tracker.report.warnings[0]

    @pytest.mark.parametrize("bad", [(5, 5), (9, 0), (0,), (0, 9, 10), "09", None, 9, ("a", "b"), (True, 9),
                                     (0, float("nan")), (0, float("inf"))])
    def test_a_bad_range_means_the_default_with_a_warning(self, bad):
        tracker = ConvoInterestTracker(score_range=bad)
        assert tracker.avg_convo_floor == pytest.approx(0.45)
        assert len(tracker.report.warnings) == 1 and "score_range" in tracker.report.warnings[0]

    def test_two_bad_settings_give_two_warnings(self):
        assert len(ConvoInterestTracker(floor_fraction=2, score_range=(3, 1)).report.warnings) == 2

    def test_strict_mode_raises_on_a_bad_fraction(self):
        with pytest.raises(ConvoInterestTrackerError, match="floor_fraction"):
            ConvoInterestTracker(floor_fraction=2, strict=True)

    def test_strict_mode_raises_on_a_bad_range(self):
        with pytest.raises(ConvoInterestTrackerError, match="score_range"):
            ConvoInterestTracker(score_range=(9, 0), strict=True)

    def test_strict_mode_with_good_settings_does_not_raise(self):
        assert ConvoInterestTracker(0.1, (0, 9), strict=True).avg_convo_floor == pytest.approx(0.9)


class TestLogging:
    def test_the_floor_is_logged_at_debug_and_nothing_else_is(self, caplog):
        with caplog.at_level(logging.DEBUG):
            ConvoInterestTracker()
        assert "floor set at" in caplog.text
