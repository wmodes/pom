"""Tests for pom.qualifying.bar_keeper_factory: the settings name an algorithm and the factory builds the BarKeeper with its bar."""
import pytest

from pom.defaults import BAR_ALGORITHMS, QualifyingSettings
from pom.qualifying import (ALGORITHMS, bar_from_settings, bar_keeper_from_settings, BarKeeper, DecayingQuantileBar, MovingAverageBar, PercentileBar, Ruling)


def settings(**overrides):
    """The `[qualifying]` settings, with some values changed from the defaults so a test can see they arrived."""
    values = dict(algorithm="percentile", bar_percentile=99.0, window_seconds=120.0, min_scores=8, cooldown_seconds=45.0, starting_bar=5.5,
                  qualifier_bar_influence=0.25, half_life_seconds=90.0, average_fraction=0.3, average_half_life_scores=6.0,
                  average_floor_fraction=0.1, anchor_score=5.0, anchor_interval_minutes=2.0, discard_scores_older_than_minutes=90.0,
                  starting_bar_weight=12.0)
    values.update(overrides)
    return QualifyingSettings(**values)


class TestTheList:
    def test_the_algorithms_by_name(self):
        assert set(ALGORITHMS) == {"percentile", "decaying_quantile", "moving_average"}
        assert ALGORITHMS["percentile"] is PercentileBar
        assert ALGORITHMS["decaying_quantile"] is DecayingQuantileBar
        assert ALGORITHMS["moving_average"] is MovingAverageBar

    def test_the_bars_here_are_the_ones_the_config_allows(self):
        """Which bars exist is said in the config's list of names (where the default is too) and here, by class; this keeps the two the same."""
        assert tuple(ALGORITHMS) == BAR_ALGORITHMS


class TestBuildingABar:
    @pytest.mark.parametrize("name,kind", [("percentile", PercentileBar), ("decaying_quantile", DecayingQuantileBar),
                                           ("moving_average", MovingAverageBar)])
    def test_the_name_picks_the_bar(self, name, kind, logged):
        bar = bar_from_settings(settings(algorithm=name), started_at=0.0)
        assert type(bar) is kind and logged.of(bar).ok()

    def test_the_percentile_bar_gets_its_settings(self):
        given = settings(algorithm="percentile")
        assert bar_from_settings(given, started_at=0.0).qualifying is given

    def test_the_decaying_quantile_bar_gets_its_settings(self):
        bar = bar_from_settings(settings(algorithm="decaying_quantile"), started_at=0.0)
        assert (bar._bar_percentile, bar._half_life, bar._starting_bar) == (99.0, 90.0, 5.5)

    def test_the_moving_average_bar_gets_its_settings(self):
        bar = bar_from_settings(settings(algorithm="moving_average"), started_at=0.0)
        assert bar._fraction == 0.3 and bar._floor == pytest.approx(0.9) and bar._half_life == 6.0
        assert bar.level_at(0.0) == pytest.approx(5.5)

    def test_a_bad_value_is_the_bars_to_report(self, logged):
        bar = bar_from_settings(settings(algorithm="decaying_quantile", bar_percentile=500), started_at=0.0)
        assert len(logged.of(bar).warnings()) == 1 and "percentile" in logged.of(bar).warnings()[0]

    def test_strict_is_passed_to_the_bar(self):
        with pytest.raises(Exception, match="percentile"):
            bar_from_settings(settings(algorithm="decaying_quantile", bar_percentile=500), started_at=0.0, strict=True)


class TestBuildingABarKeeper:
    def test_it_is_a_bar_keeper_with_the_named_bar_and_the_cooldown(self, logged):
        bar_keeper = bar_keeper_from_settings(settings(algorithm="decaying_quantile", cooldown_seconds=45.0), started_at=0.0)
        assert isinstance(bar_keeper, BarKeeper) and isinstance(bar_keeper._bar, DecayingQuantileBar)
        assert bar_keeper.qualifying.cooldown_seconds == 45.0 and logged.of(bar_keeper).ok()

    def test_strict_is_passed_through_to_a_bad_bar_setting(self):
        with pytest.raises(Exception, match="percentile"):
            bar_keeper_from_settings(settings(algorithm="decaying_quantile", bar_percentile=500), started_at=0.0, strict=True)

    @pytest.mark.parametrize("name", ["percentile", "decaying_quantile", "moving_average"])
    def test_every_bar_keeper_starts_at_the_starting_line_and_qualifies_on_a_strong_score(self, name):
        bar_keeper = bar_keeper_from_settings(settings(algorithm=name, starting_bar=5.0, bar_percentile=100.0), started_at=0.0)
        assert bar_keeper.bar_at(0.0) == pytest.approx(5.0)
        assert bar_keeper.rule(9.0, 1.0) is Ruling.QUALIFIED

    @pytest.mark.parametrize("name", ["percentile", "decaying_quantile", "moving_average"])
    def test_every_bar_keeper_describes_itself_with_the_cooldown(self, name):
        text = bar_keeper_from_settings(settings(algorithm=name, cooldown_seconds=45.0), started_at=0.0).describe()
        assert "at most one qualifier every 45 s" in text and "starting at 5.5" in text

    @pytest.mark.parametrize("name", ["percentile", "decaying_quantile", "moving_average"])
    def test_every_bar_keeper_says_how_much_a_qualifier_counts(self, name):
        text = bar_keeper_from_settings(settings(algorithm=name, qualifier_bar_influence=0.25), started_at=0.0).describe()
        assert text.endswith("a qualifying score goes into the bar 25% of the way up from the bar")

    def test_the_bar_keeper_and_its_bar_are_handed_the_same_settings(self):
        given = settings(qualifier_bar_influence=1.1)
        bar_keeper = bar_keeper_from_settings(given, started_at=0.0)
        assert bar_keeper.qualifying is given and bar_keeper._bar.qualifying is given


class TestTheStart:
    """The stream time at which the bar begins reaches the bar that has a clock; the others take it and have no use for it."""

    def test_the_decaying_quantile_bar_gets_the_start(self):
        assert bar_from_settings(settings(algorithm="decaying_quantile"), started_at=123.0)._started_at == 123.0

    def test_through_the_bar_keeper_too(self):
        assert bar_keeper_from_settings(settings(algorithm="decaying_quantile"), started_at=77.0)._bar._started_at == 77.0

    @pytest.mark.parametrize("name", ["percentile", "moving_average"])
    def test_the_bars_without_a_clock_take_it_and_ignore_it(self, name, logged):
        assert logged.of(bar_from_settings(settings(algorithm=name), started_at=500.0)).ok()

    @pytest.mark.parametrize("bar_class", [PercentileBar, DecayingQuantileBar, MovingAverageBar])
    def test_a_bar_built_from_settings_needs_its_start(self, bar_class):
        with pytest.raises(TypeError):
            bar_class.from_settings(settings())

    def test_a_start_is_required(self):
        with pytest.raises(TypeError):
            bar_keeper_from_settings(settings())
        with pytest.raises(TypeError):
            bar_from_settings(settings())
