"""Tests for pom.scoring.tracker_factory: the settings name an algorithm and the factory builds the tracker with its bar."""
from types import SimpleNamespace

import pytest

from pom.scoring import (ALGORITHMS, ConvoInterestTracker, DecayingQuantileBar, MovingAverageBar, PercentileBar, ScoreEffect,
                         bar_from_settings, tracker_from_settings)
from pom.scoring.convo_interest_tracker import ConvoInterestTrackerError
from pom.scoring.percentile_bar import PercentileBarError


def settings(**overrides):
    """Settings like the config makes, with every algorithm's keys present."""
    values = dict(algorithm="percentile", percentile=99.0, window_seconds=120.0, min_scores=8, cooldown_seconds=45.0, starting_bar=5.5,
                  exclude_triggers=True, half_life_seconds=90.0, average_fraction=0.3, average_half_life_scores=6.0,
                  average_floor_fraction=0.1)
    values.update(overrides)
    return SimpleNamespace(**values)


class TestTheList:
    def test_the_algorithms_by_name(self):
        assert set(ALGORITHMS) == {"percentile", "decaying_quantile", "moving_average"}
        assert ALGORITHMS["percentile"] is PercentileBar
        assert ALGORITHMS["decaying_quantile"] is DecayingQuantileBar
        assert ALGORITHMS["moving_average"] is MovingAverageBar


class TestBuildingABar:
    @pytest.mark.parametrize("name,kind", [("percentile", PercentileBar), ("decaying_quantile", DecayingQuantileBar),
                                           ("moving_average", MovingAverageBar)])
    def test_the_name_picks_the_bar(self, name, kind):
        bar = bar_from_settings(settings(algorithm=name))
        assert type(bar) is kind and bar.report.ok

    def test_the_percentile_bar_gets_its_settings(self):
        bar = bar_from_settings(settings(algorithm="percentile"))
        assert (bar._percentile, bar._window_seconds, bar._min_scores, bar._starting_bar, bar._exclude_triggers) == (99.0, 120.0, 8, 5.5, True)

    def test_the_decaying_quantile_bar_gets_its_settings(self):
        bar = bar_from_settings(settings(algorithm="decaying_quantile"))
        assert (bar._percentile, bar._half_life, bar._starting_bar, bar._exclude_triggers) == (99.0, 90.0, 5.5, True)

    def test_the_moving_average_bar_gets_its_settings(self):
        bar = bar_from_settings(settings(algorithm="moving_average"))
        assert bar._fraction == 0.3 and bar._floor == pytest.approx(0.9) and bar._half_life == 6.0
        assert bar.level_at(0.0) == pytest.approx(5.5)

    def test_each_bar_ignores_the_other_algorithms_settings(self):
        sparse = SimpleNamespace(algorithm="percentile", percentile=99.0, window_seconds=120.0, min_scores=8, starting_bar=5.5,
                                 exclude_triggers=False)
        assert bar_from_settings(sparse).report.ok

    def test_no_algorithm_named_means_the_percentile_bar(self):
        bar = bar_from_settings(SimpleNamespace(percentile=99.0, window_seconds=120.0, min_scores=8, starting_bar=5.5, exclude_triggers=False))
        assert type(bar) is PercentileBar and bar.report.ok

    @pytest.mark.parametrize("bad", ["median", "", None, 5, True, ["percentile"], "Percentile"])
    def test_an_unknown_name_means_the_percentile_bar_with_a_warning(self, bad):
        bar = bar_from_settings(settings(algorithm=bad))
        assert type(bar) is PercentileBar
        assert len(bar.report.warnings) == 1 and "algorithm" in bar.report.warnings[0]

    def test_strict_mode_raises_for_an_unknown_name(self):
        with pytest.raises(PercentileBarError, match="algorithm"):
            bar_from_settings(settings(algorithm="median"), strict=True)

    def test_a_bad_value_is_the_bars_to_report(self):
        bar = bar_from_settings(settings(algorithm="decaying_quantile", percentile=500))
        assert len(bar.report.warnings) == 1 and "percentile" in bar.report.warnings[0]

    def test_strict_is_passed_to_the_bar(self):
        with pytest.raises(Exception, match="percentile"):
            bar_from_settings(settings(algorithm="decaying_quantile", percentile=500), strict=True)


class TestBuildingATracker:
    def test_it_is_a_tracker_with_the_named_bar_and_the_cooldown(self):
        tracker = tracker_from_settings(settings(algorithm="decaying_quantile", cooldown_seconds=45.0))
        assert isinstance(tracker, ConvoInterestTracker) and isinstance(tracker._bar, DecayingQuantileBar)
        assert tracker._cooldown_seconds == 45.0 and tracker.report.ok

    def test_a_bad_cooldown_is_the_trackers_to_report(self):
        tracker = tracker_from_settings(settings(cooldown_seconds=-5))
        assert len(tracker.report.warnings) == 1 and "cooldown_seconds" in tracker.report.warnings[0]

    def test_strict_is_passed_to_the_tracker(self):
        with pytest.raises(ConvoInterestTrackerError):
            tracker_from_settings(settings(cooldown_seconds=-5), strict=True)

    def test_strict_is_passed_through_to_a_bad_bar_setting(self):
        with pytest.raises(Exception, match="percentile"):
            tracker_from_settings(settings(algorithm="decaying_quantile", percentile=500), strict=True)

    @pytest.mark.parametrize("name", ["percentile", "decaying_quantile", "moving_average"])
    def test_every_tracker_starts_at_the_starting_line_and_triggers_on_a_strong_score(self, name):
        tracker = tracker_from_settings(settings(algorithm=name, starting_bar=5.0, percentile=100.0))
        assert tracker.trigger_level_at(0.0) == pytest.approx(5.0)
        assert tracker.record(9.0, 1.0) is ScoreEffect.TRIGGERED

    @pytest.mark.parametrize("name", ["percentile", "decaying_quantile", "moving_average"])
    def test_every_tracker_describes_itself_with_the_cooldown(self, name):
        text = tracker_from_settings(settings(algorithm=name, cooldown_seconds=45.0)).describe()
        assert text.endswith("at most one trigger every 45 s") and "starting at 5.5" in text
