"""Tests for pom.qualifying.moving_average_bar: a decaying average of the scores above a floor, with the bar a fraction above it."""
import pytest

from pom.defaults import QualifyingSettings
from pom.qualifying.moving_average_bar import DEFAULT_SCORE_RANGE, MovingAverageBar, MovingAverageBarError
from pom.qualifying.bar import Bar


def bar(**settings):
    """A bar for the tests: a half-life of 1 score (each score moves the average half way to itself), 25% above, a floor at 0.45;
    other `[qualifying]` settings by keyword."""
    settings.setdefault("average_half_life_scores", 1.0)
    return MovingAverageBar(QualifyingSettings(**settings))


def feed(b, scores):
    for i, score in enumerate(scores):
        b.add(score, float(i))


class TestTheDefaults:
    def test_the_starting_values(self):
        q = QualifyingSettings()
        assert (q.average_fraction, q.average_half_life_scores, q.average_floor_fraction, q.starting_bar) == (0.25, 10.0, 0.05, 6.8)
        assert DEFAULT_SCORE_RANGE == (0.0, 9.0)

    def test_good_settings_are_quiet(self, logged):
        assert logged.of(MovingAverageBar(QualifyingSettings())).ok()
        assert logged.of(MovingAverageBar(QualifyingSettings(average_fraction=0, average_half_life_scores=0.001, average_floor_fraction=0, starting_bar=-1))).ok()

    def test_it_is_a_bar(self):
        assert isinstance(MovingAverageBar(QualifyingSettings()), Bar)


class TestTheStartingLine:
    def test_the_bar_starts_at_the_starting_line_whatever_the_fraction(self):
        for fraction in (0, 0.25, 1.0, 3.5):
            assert MovingAverageBar(QualifyingSettings(average_fraction=fraction, starting_bar=6.8)).level_at(0.0) == pytest.approx(6.8)

    def test_the_starting_line_is_settable(self):
        assert MovingAverageBar(QualifyingSettings(starting_bar=4.25)).level_at(0.0) == pytest.approx(4.25)

    def test_there_is_always_a_bar(self):
        assert MovingAverageBar(QualifyingSettings()).level_at(0.0) is not None

    def test_a_score_moves_the_average_part_of_the_way_from_the_starting_line(self):
        b = bar(starting_bar=5.0, average_fraction=0)             # half-life 1: half way
        b.add(9.0, 0.0)
        assert b.level_at(0.0) == pytest.approx(7.0)

    def test_the_starting_line_fades_by_the_half_life_in_scores(self):
        b = MovingAverageBar(QualifyingSettings(average_fraction=0, average_half_life_scores=10, starting_bar=9.0, average_floor_fraction=0))
        feed(b, [1.0] * 10)                                # ten scores: half way from 9 to 1
        assert b.level_at(0.0) == pytest.approx(5.0, abs=1e-9)
        feed(b, [1.0] * 10)
        assert b.level_at(0.0) == pytest.approx(3.0, abs=1e-9)


class TestTheAverageAndTheBar:
    def test_the_bar_is_the_average_plus_the_fraction(self):
        b = bar(starting_bar=4.0, average_fraction=0.25)           # the average starts at 3.2
        b.add(5.0, 0.0)                             # half way: 4.1
        assert b.level_at(0.0) == pytest.approx(4.1 * 1.25)

    def test_a_fraction_of_zero_puts_the_bar_at_the_average(self):
        b = bar(starting_bar=4.0, average_fraction=0)
        b.add(6.0, 0.0)
        assert b.level_at(0.0) == pytest.approx(5.0)

    def test_a_bigger_fraction_gives_a_higher_bar(self):
        low, high = bar(average_fraction=0.1), bar(average_fraction=0.5)
        for b in (low, high):
            feed(b, [5.0, 5.0])
        assert low.level_at(0.0) < high.level_at(0.0)

    def test_time_does_not_matter(self):
        b = bar()
        feed(b, [5.0, 6.0])
        assert b.level_at(0.0) == b.level_at(1_000_000.0)

    def test_the_average_follows_a_constant_score(self):
        b = bar(average_fraction=0, starting_bar=0.0, average_floor_fraction=0)
        feed(b, [8.0] * 30)
        assert b.level_at(0.0) == pytest.approx(8.0, abs=1e-6)

    def test_a_lively_room_gets_a_higher_bar_than_a_dull_one(self):
        dull, lively = bar(), bar()
        feed(dull, [2.0] * 20)
        feed(lively, [7.0] * 20)
        assert dull.level_at(0.0) < lively.level_at(0.0)

    def test_the_half_life_sets_how_far_each_score_moves_it(self):
        slow, fast = MovingAverageBar(QualifyingSettings(average_half_life_scores=20, average_fraction=0, starting_bar=2.0)), MovingAverageBar(QualifyingSettings(average_half_life_scores=2, average_fraction=0, starting_bar=2.0))
        for b in (slow, fast):
            b.add(8.0, 0.0)
        assert fast.level_at(0.0) > slow.level_at(0.0)
        assert fast.level_at(0.0) == pytest.approx(2.0 + (1 - 0.5 ** 0.5) * 6.0)


class TestTheFloor:
    def test_a_score_below_the_floor_is_ignored(self):
        b = bar()
        before = b.level_at(0.0)
        b.add(0.4, 0.0)
        assert b.level_at(0.0) == before and b.counted == 0 and b.ignores(0.4) is True

    def test_a_score_exactly_at_the_floor_counts(self):
        b = bar()
        assert b.ignores(0.45) is False
        b.add(0.45, 0.0)
        assert b.counted == 1

    def test_the_floor_is_a_fraction_of_the_range(self):
        assert MovingAverageBar(QualifyingSettings(average_floor_fraction=0.5)).ignores(4.49) is True
        assert MovingAverageBar(QualifyingSettings(average_floor_fraction=0.5)).ignores(4.5) is False
        assert MovingAverageBar(QualifyingSettings(average_floor_fraction=0), score_range=(0, 9)).ignores(0.0) is False

    def test_the_floor_follows_the_score_range(self):
        assert MovingAverageBar(QualifyingSettings(average_floor_fraction=0.1), score_range=(0, 100)).ignores(9.9) is True
        assert MovingAverageBar(QualifyingSettings(average_floor_fraction=0.1), score_range=(0, 100)).ignores(10.0) is False
        assert MovingAverageBar(QualifyingSettings(average_floor_fraction=0.5), score_range=(10, 20)).ignores(14.9) is True

    @pytest.mark.parametrize("junk", [None, "7", True, [5], float("nan"), float("inf")])
    def test_what_is_not_a_score_is_ignored(self, junk):
        b = bar()
        before = b.level_at(0.0)
        assert b.ignores(junk) is True
        b.add(junk, 0.0)
        assert b.level_at(0.0) == before and b.counted == 0


class TestTheWindowSize:
    def test_it_counts_the_scores_averaged(self):
        b = bar()
        assert b.window_size_at(0.0) == 0
        feed(b, [3.0, 4.0, 0.1, 5.0])                       # the 0.1 is under the floor
        assert b.window_size_at(0.0) == 3


class TestDescribing:
    def test_it_says_how_it_works_in_one_line(self):
        assert MovingAverageBar(QualifyingSettings(average_fraction=0.25, average_half_life_scores=10, average_floor_fraction=0.05, starting_bar=6.8)).describe() == \
            "25% above a decaying average of scores (half-life 10 scores, ignoring scores under 0.45), starting at 6.8"


class TestTheScoreRange:
    """The score range is an argument of the bar's own, not a setting, so the bar still checks it."""

    @pytest.mark.parametrize("bad", [(5, 5), (9, 0), "09", None, (1,), (None, 9), (0, float("nan")), 5])
    def test_a_bad_score_range_means_the_default_with_a_warning(self, bad, logged):
        b = MovingAverageBar(QualifyingSettings(), score_range=bad)
        assert len(logged.of(b).warnings()) == 1 and "score_range" in logged.of(b).warnings()[0]
        assert b.ignores(0.44) and not b.ignores(0.45)                     # the floor on the default 0 to 9

    def test_strict_mode_raises(self):
        with pytest.raises(MovingAverageBarError):
            MovingAverageBar(QualifyingSettings(), score_range=(3, 3), strict=True)

    def test_strict_mode_with_good_settings_does_not_raise(self, logged):
        assert logged.of(MovingAverageBar(QualifyingSettings(), strict=True)).ok()


class TestBuildingFromSettings:
    def test_it_is_built_from_the_qualifying_settings(self, logged):
        settings = QualifyingSettings(average_fraction=0.5, average_half_life_scores=4.0, average_floor_fraction=0.1, starting_bar=5.0)
        b = MovingAverageBar.from_settings(settings, started_at=0.0)
        assert b.qualifying is settings and b.ignores(0.89) and not b.ignores(0.9)
        assert b.level_at(0.0) == pytest.approx(5.0) and logged.of(b).ok()


class TestLogging:
    def test_only_numbers_are_logged(self, logged):
        b = MovingAverageBar(QualifyingSettings())
        feed(b, [3.0, 4.0])
        assert "secret" not in logged.text()


def test_a_score_that_is_not_a_number_is_ignored_with_a_warning(logged):
    b = bar()
    assert b.ignores("high") and len(logged.of(b).warnings()) == 1
    assert b.ignores(None) and len(logged.of(b).warnings()) == 2           # the BarKeeper never hands None: here it is a bad value
