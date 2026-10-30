"""Tests for pom.scoring.moving_average_bar: a decaying average of the scores above a floor, with the bar a fraction above it."""
import logging
from types import SimpleNamespace

import pytest

from pom.scoring.moving_average_bar import (DEFAULT_FLOOR_FRACTION, DEFAULT_FRACTION, DEFAULT_HALF_LIFE_SCORES, DEFAULT_SCORE_RANGE,
                                            DEFAULT_STARTING_BAR, MovingAverageBar, MovingAverageBarError)
from pom.scoring.trigger_bar import TriggerBar


def bar(**options):
    """A bar for the tests: a half-life of 1 score (each score moves the average half way to itself), 25% above, a floor at 0.45."""
    options.setdefault("half_life_scores", 1.0)
    return MovingAverageBar(**options)


def feed(b, scores):
    for i, score in enumerate(scores):
        b.add(score, float(i), False)


class TestTheDefaults:
    def test_the_starting_values(self):
        assert (DEFAULT_FRACTION, DEFAULT_HALF_LIFE_SCORES, DEFAULT_FLOOR_FRACTION, DEFAULT_STARTING_BAR) == (0.25, 10.0, 0.05, 6.8)
        assert DEFAULT_SCORE_RANGE == (0.0, 9.0)

    def test_good_settings_are_quiet(self):
        assert MovingAverageBar().report.ok
        assert MovingAverageBar(fraction=0, half_life_scores=0.001, floor_fraction=0, starting_bar=-1).report.ok

    def test_it_is_a_trigger_bar(self):
        assert isinstance(MovingAverageBar(), TriggerBar)


class TestTheStartingLine:
    def test_the_bar_starts_at_the_starting_line_whatever_the_fraction(self):
        for fraction in (0, 0.25, 1.0, 3.5):
            assert MovingAverageBar(fraction=fraction, starting_bar=6.8).level_at(0.0) == pytest.approx(6.8)

    def test_the_starting_line_is_settable(self):
        assert MovingAverageBar(starting_bar=4.25).level_at(0.0) == pytest.approx(4.25)

    def test_there_is_always_a_bar(self):
        assert MovingAverageBar().level_at(0.0) is not None

    def test_a_score_moves_the_average_part_of_the_way_from_the_starting_line(self):
        b = bar(starting_bar=5.0, fraction=0)             # half-life 1: half way
        b.add(9.0, 0.0, False)
        assert b.level_at(0.0) == pytest.approx(7.0)

    def test_the_starting_line_fades_by_the_half_life_in_scores(self):
        b = MovingAverageBar(fraction=0, half_life_scores=10, starting_bar=9.0, floor_fraction=0)
        feed(b, [1.0] * 10)                                # ten scores: half way from 9 to 1
        assert b.level_at(0.0) == pytest.approx(5.0, abs=1e-9)
        feed(b, [1.0] * 10)
        assert b.level_at(0.0) == pytest.approx(3.0, abs=1e-9)


class TestTheAverageAndTheBar:
    def test_the_bar_is_the_average_plus_the_fraction(self):
        b = bar(starting_bar=4.0, fraction=0.25)           # the average starts at 3.2
        b.add(5.0, 0.0, False)                             # half way: 4.1
        assert b.level_at(0.0) == pytest.approx(4.1 * 1.25)

    def test_a_fraction_of_zero_puts_the_bar_at_the_average(self):
        b = bar(starting_bar=4.0, fraction=0)
        b.add(6.0, 0.0, False)
        assert b.level_at(0.0) == pytest.approx(5.0)

    def test_a_bigger_fraction_gives_a_higher_bar(self):
        low, high = bar(fraction=0.1), bar(fraction=0.5)
        for b in (low, high):
            feed(b, [5.0, 5.0])
        assert low.level_at(0.0) < high.level_at(0.0)

    def test_time_does_not_matter(self):
        b = bar()
        feed(b, [5.0, 6.0])
        assert b.level_at(0.0) == b.level_at(1_000_000.0)

    def test_the_average_follows_a_constant_score(self):
        b = bar(fraction=0, starting_bar=0.0, floor_fraction=0)
        feed(b, [8.0] * 30)
        assert b.level_at(0.0) == pytest.approx(8.0, abs=1e-6)

    def test_a_lively_room_gets_a_higher_bar_than_a_dull_one(self):
        dull, lively = bar(), bar()
        feed(dull, [2.0] * 20)
        feed(lively, [7.0] * 20)
        assert dull.level_at(0.0) < lively.level_at(0.0)

    def test_the_half_life_sets_how_far_each_score_moves_it(self):
        slow, fast = MovingAverageBar(half_life_scores=20, fraction=0, starting_bar=2.0), MovingAverageBar(half_life_scores=2, fraction=0, starting_bar=2.0)
        for b in (slow, fast):
            b.add(8.0, 0.0, False)
        assert fast.level_at(0.0) > slow.level_at(0.0)
        assert fast.level_at(0.0) == pytest.approx(2.0 + (1 - 0.5 ** 0.5) * 6.0)


class TestTheFloor:
    def test_a_score_below_the_floor_is_ignored(self):
        b = bar()
        before = b.level_at(0.0)
        b.add(0.4, 0.0, False)
        assert b.level_at(0.0) == before and b.counted == 0 and b.ignores(0.4) is True

    def test_a_score_exactly_at_the_floor_counts(self):
        b = bar()
        assert b.ignores(0.45) is False
        b.add(0.45, 0.0, False)
        assert b.counted == 1

    def test_the_floor_is_a_fraction_of_the_range(self):
        assert MovingAverageBar(floor_fraction=0.5).ignores(4.49) is True
        assert MovingAverageBar(floor_fraction=0.5).ignores(4.5) is False
        assert MovingAverageBar(floor_fraction=0, score_range=(0, 9)).ignores(0.0) is False

    def test_the_floor_follows_the_score_range(self):
        assert MovingAverageBar(floor_fraction=0.1, score_range=(0, 100)).ignores(9.9) is True
        assert MovingAverageBar(floor_fraction=0.1, score_range=(0, 100)).ignores(10.0) is False
        assert MovingAverageBar(floor_fraction=0.5, score_range=(10, 20)).ignores(14.9) is True

    @pytest.mark.parametrize("junk", [None, "7", True, [5], float("nan"), float("inf")])
    def test_what_is_not_a_score_is_ignored(self, junk):
        b = bar()
        before = b.level_at(0.0)
        assert b.ignores(junk) is True
        b.add(junk, 0.0, False)
        assert b.level_at(0.0) == before and b.counted == 0

    def test_a_triggering_score_joins_the_average_like_any_other(self):
        b = bar(starting_bar=4.0, fraction=0)
        b.add(8.0, 0.0, True)
        assert b.level_at(0.0) == pytest.approx(6.0)


class TestTheWindowSize:
    def test_it_counts_the_scores_averaged(self):
        b = bar()
        assert b.window_size_at(0.0) == 0
        feed(b, [3.0, 4.0, 0.1, 5.0])                       # the 0.1 is under the floor
        assert b.window_size_at(0.0) == 3


class TestBadSettings:
    @pytest.mark.parametrize("bad", [-0.1, "0.25", None, True, [1], float("nan"), float("inf")])
    def test_a_bad_fraction_means_the_default_with_a_warning(self, bad):
        b = MovingAverageBar(fraction=bad)
        assert len(b.report.warnings) == 1 and "average_fraction" in b.report.warnings[0]
        assert b._fraction == DEFAULT_FRACTION

    @pytest.mark.parametrize("bad", [0, -1, "10", None, True, float("nan"), float("inf")])
    def test_a_bad_half_life_means_the_default_with_a_warning(self, bad):
        b = MovingAverageBar(half_life_scores=bad)
        assert len(b.report.warnings) == 1 and "average_half_life_scores" in b.report.warnings[0]
        assert b._weight == pytest.approx(1 - 0.5 ** (1 / DEFAULT_HALF_LIFE_SCORES))

    @pytest.mark.parametrize("bad", [-0.01, 1, 1.5, "0.05", None, True, float("nan")])
    def test_a_bad_floor_fraction_means_the_default_with_a_warning(self, bad):
        b = MovingAverageBar(floor_fraction=bad)
        assert len(b.report.warnings) == 1 and "average_floor_fraction" in b.report.warnings[0]
        assert b._floor == pytest.approx(0.45)

    @pytest.mark.parametrize("bad", ["6.8", None, True, [6.8], float("nan"), float("inf")])
    def test_a_bad_starting_bar_means_the_default_with_a_warning(self, bad):
        b = MovingAverageBar(starting_bar=bad)
        assert len(b.report.warnings) == 1 and "starting_bar" in b.report.warnings[0]
        assert b.level_at(0.0) == pytest.approx(DEFAULT_STARTING_BAR)

    @pytest.mark.parametrize("bad", [(5, 5), (9, 0), "09", None, (1,), (None, 9), (0, float("nan")), 5])
    def test_a_bad_score_range_means_the_default_with_a_warning(self, bad):
        b = MovingAverageBar(score_range=bad)
        assert len(b.report.warnings) == 1 and "score_range" in b.report.warnings[0]
        assert b._floor == pytest.approx(0.45)

    def test_a_good_floor_of_zero_and_a_fraction_of_zero_are_fine(self):
        assert MovingAverageBar(floor_fraction=0, fraction=0).report.ok

    @pytest.mark.parametrize("option,bad", [("fraction", -1), ("half_life_scores", 0), ("floor_fraction", 1), ("starting_bar", None),
                                            ("score_range", (3, 3))])
    def test_strict_mode_raises(self, option, bad):
        with pytest.raises(MovingAverageBarError):
            MovingAverageBar(strict=True, **{option: bad})

    def test_strict_mode_with_good_settings_does_not_raise(self):
        assert MovingAverageBar(strict=True).report.ok

    def test_two_bad_settings_give_two_warnings(self):
        assert len(MovingAverageBar(fraction=-1, half_life_scores=0).report.warnings) == 2


class TestBuildingFromSettings:
    def test_it_reads_the_settings_it_needs(self):
        settings = SimpleNamespace(average_fraction=0.5, average_half_life_scores=4.0, average_floor_fraction=0.1, starting_bar=5.0, other="x")
        b = MovingAverageBar.from_settings(settings)
        assert (b._fraction, b._floor) == (0.5, pytest.approx(0.9))
        assert b.level_at(0.0) == pytest.approx(5.0) and b._weight == pytest.approx(1 - 0.5 ** 0.25) and b.report.ok

    def test_bad_settings_are_replaced_as_usual(self):
        b = MovingAverageBar.from_settings(SimpleNamespace(average_fraction=-3, average_half_life_scores=4.0, average_floor_fraction=0.1,
                                                           starting_bar=5.0))
        assert b._fraction == DEFAULT_FRACTION and len(b.report.warnings) == 1

    def test_strict_is_passed_on(self):
        with pytest.raises(MovingAverageBarError):
            MovingAverageBar.from_settings(SimpleNamespace(average_fraction=-3, average_half_life_scores=4.0, average_floor_fraction=0.1,
                                                           starting_bar=5.0), strict=True)


class TestLogging:
    def test_only_numbers_are_logged(self, caplog):
        with caplog.at_level(logging.DEBUG):
            b = MovingAverageBar()
            feed(b, [3.0, 4.0])
        assert "secret" not in caplog.text
