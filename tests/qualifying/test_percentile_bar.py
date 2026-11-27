"""Tests for pom.qualifying.percentile_bar: a nearest-rank percentile of a window of time, with a starting line."""
import random

import pytest

from pom.defaults import QualifyingSettings
from pom.qualifying.percentile_bar import PercentileBar
from pom.qualifying.bar import Bar


def bar(**options):
    """A bar for the tests: five scores make a window (not the default 20), the window is 60 seconds, the percentile is 100 (the
    bar is the best score of the window) and the starting line is 6.8, to keep the arithmetic short."""
    options.setdefault("bar_percentile", 100.0)
    options.setdefault("min_scores", 5)
    options.setdefault("window_seconds", 60)
    return PercentileBar(QualifyingSettings(**options))


def feed(b, scores, start=0.0, step=1.0):
    for i, score in enumerate(scores):
        if score is not None:
            b.add(score, start + i * step)


def warmed_up(**options):
    """A bar that has been given the scores 1 to 5 at seconds 0 to 4: the best is 5."""
    b = bar(**options)
    feed(b, [1, 2, 3, 4, 5])
    return b


class TestTheDefaults:
    def test_the_starting_values(self):
        q = QualifyingSettings()
        assert (q.bar_percentile, q.window_seconds, q.min_scores, q.starting_bar) == (99.5, 300.0, 20, 6.8)

    def test_good_settings_are_quiet(self, logged):
        assert logged.of(PercentileBar(QualifyingSettings())).ok()
        assert logged.of(bar(bar_percentile=0.001, window_seconds=0.5, min_scores=2)).ok()
        assert logged.of(bar(bar_percentile=100, starting_bar=-2)).ok()

    def test_it_is_a_bar(self):
        assert isinstance(PercentileBar(QualifyingSettings()), Bar)

    def test_it_ignores_no_score(self):
        assert PercentileBar(QualifyingSettings()).ignores(0.0) is False and PercentileBar(QualifyingSettings()).ignores(9.0) is False


class TestTheStartingLine:
    def test_before_any_score_the_bar_is_the_starting_line(self):
        assert bar(starting_bar=6.8).level_at(0.0) == 6.8
        assert bar(starting_bar=4.25).level_at(0.0) == 4.25

    def test_it_stays_the_starting_line_until_enough_scores_have_been_heard(self):
        b = bar(starting_bar=6.8)
        feed(b, [9, 9, 9, 9])
        assert b.level_at(4.0) == 6.8
        b.add(9, 4.0)
        assert b.level_at(5.0) == 9.0

    def test_the_default_needs_twenty(self):
        b = PercentileBar(QualifyingSettings(bar_percentile=100))
        feed(b, [9.0] * 19)
        assert b.level_at(19.0) == QualifyingSettings().starting_bar
        b.add(9.0, 19.0)
        assert b.level_at(20.0) == 9.0

    def test_there_is_always_a_bar(self):
        assert PercentileBar(QualifyingSettings()).level_at(0.0) is not None

    def test_while_warming_up_the_window_size_is_all_it_holds(self):
        b = bar()
        feed(b, [1, 2, 3])
        assert b.window_size_at(3.0) == 3


class TestThePercentileBar:
    @pytest.mark.parametrize("percentile,level", [(100, 5.0), (99.3, 5.0), (81, 5.0), (80, 4.0), (60, 3.0), (50, 3.0),
                                                  (41, 3.0), (40, 2.0), (21, 2.0), (20, 1.0), (1, 1.0), (0.001, 1.0)])
    def test_the_bar_is_the_score_that_share_of_the_window_sits_at_or_below(self, percentile, level):
        assert warmed_up(bar_percentile=percentile).level_at(5.0) == level

    def test_a_hundred_puts_the_bar_at_the_best_score(self):
        assert warmed_up(bar_percentile=100).level_at(5.0) == 5.0

    def test_the_bar_is_a_score_from_the_window_never_an_in_between(self):
        assert warmed_up(bar_percentile=90).level_at(5.0) == 5.0
        assert warmed_up(bar_percentile=70).level_at(5.0) == 4.0

    def test_a_percentile_that_is_a_whole_rank_does_not_round_up_by_float_noise(self):
        b = bar(bar_percentile=64.4, min_scores=250, window_seconds=1000)
        feed(b, list(range(1, 251)))                               # 64.4% of 250 is exactly 161
        assert b.level_at(250.0) == 161.0

    def test_ninety_nine_and_a_half_sits_below_the_best_only_with_two_hundred_scores(self):
        few, many = bar(bar_percentile=99.5, min_scores=5, window_seconds=1000), bar(bar_percentile=99.5, min_scores=5, window_seconds=1000)
        feed(few, list(range(1, 200)))
        feed(many, list(range(1, 201)))
        assert few.level_at(200.0) == 199.0 and many.level_at(201.0) == 199.0
        assert bar(bar_percentile=99.5, min_scores=5, window_seconds=1000).level_at(0.0) == 6.8

    def test_the_default_percentile_is_995(self):
        b = PercentileBar(QualifyingSettings(min_scores=5))
        feed(b, [1, 2, 3, 4, 5])
        assert b.level_at(5.0) == 5.0

    def test_a_lower_percentile_gives_a_lower_bar(self):
        high, low = bar(bar_percentile=100), bar(bar_percentile=60)
        for b in (high, low):
            feed(b, [1, 2, 3, 4, 5])
        assert low.level_at(5.0) < high.level_at(5.0)

    def test_equal_scores_give_that_score(self):
        b = bar()
        feed(b, [3.0] * 5)
        assert b.level_at(5.0) == 3.0

    def test_the_lowest_scores_count_there_is_no_floor(self):
        b = bar()
        feed(b, [0, 0, 0, 0, 0])
        assert b.level_at(5.0) == 0.0

    def test_low_scores_pull_a_middle_percentile_down(self):
        without, with_lows = bar(bar_percentile=50), bar(bar_percentile=50)
        feed(without, [4] * 8)
        feed(with_lows, [0, 0, 0, 0, 4, 4, 4, 4])
        assert without.level_at(8.0) == 4.0 and with_lows.level_at(8.0) == 0.0

    def test_low_scores_do_not_move_the_best(self):
        without, with_lows = bar(), bar()
        feed(without, [4] * 8)
        feed(with_lows, [0, 0, 0, 0, 4, 4, 4, 4])
        assert without.level_at(8.0) == with_lows.level_at(8.0) == 4.0

    def test_a_score_added_raises_the_best(self):
        b = warmed_up()
        level = b.level_at(5.0)
        b.add(9.0, 5.0)
        assert b.level_at(6.0) > level


class TestTheWindow:
    def test_scores_older_than_the_window_age_out(self):
        short, long_ = bar(bar_percentile=50, window_seconds=10), bar(bar_percentile=50, window_seconds=1000)
        for b in (short, long_):
            feed(b, [1] * 10)
            feed(b, [9] * 10, start=100)
        assert short.level_at(110.0) == 9.0 and long_.level_at(110.0) == 1.0

    def test_a_score_exactly_at_the_edge_of_the_window_is_still_in_it(self):
        b = bar(window_seconds=10)
        feed(b, [1, 2, 3, 4, 5, 6], start=0, step=2)
        assert b.window_size_at(10.0) == 6
        assert b.window_size_at(10.5) == 5

    def test_a_quiet_room_keeps_its_last_few_scores_however_old(self):
        b = bar(window_seconds=10)
        feed(b, [1, 2, 3, 4, 5], step=100)
        assert b.window_size_at(500.0) == 5 and b.level_at(500.0) == 5.0

    def test_a_quiet_room_takes_the_newest_scores_not_the_oldest(self):
        b = bar(window_seconds=60)
        feed(b, [9, 8, 7, 1, 2, 3, 4, 5])                    # eight scores, seconds 0 to 7, all still held
        assert b.level_at(1000.0) == 5.0                     # nothing is inside the window: the last five (1 to 5), not the first five

    def test_the_window_is_the_larger_of_the_time_and_the_count(self):
        b = bar(window_seconds=10)
        feed(b, list(range(1, 31)), step=1)
        assert b.window_size_at(30.0) == 10
        feed(b, [5], start=500)
        assert b.window_size_at(500.0) == 5

    def test_a_busy_room_and_a_quiet_one_both_get_a_stretch_of_time(self):
        busy, quiet = bar(window_seconds=60), bar(window_seconds=60)
        feed(busy, [3] * 600, step=0.5)
        feed(quiet, [3] * 20, step=15)
        assert busy.window_size_at(300.0) == 120 and quiet.window_size_at(300.0) == 5

    def test_memory_is_bounded(self):
        b = bar(window_seconds=60)
        feed(b, [3] * 10_000, step=1)
        assert len(b._recent) <= 61

    def test_a_time_asked_about_before_the_scores_still_works(self):
        b = warmed_up()
        assert b.level_at(0.0) == 5.0


class TestAgainstAPlainReference:
    """The bar against a few lines that do the same thing the slow, obvious way."""

    @staticmethod
    def reference(events, window_seconds, min_scores, percentile, starting_bar):
        held, out = [], []
        for now, score in events:
            if len(held) < min_scores:
                level = starting_bar
            else:
                window = [s for t, s in held if t >= now - window_seconds]
                if len(window) < min_scores:
                    window = [s for _, s in held[-min_scores:]]
                ranked = sorted(window)
                level = next(v for i, v in enumerate(ranked, start=1) if i * 100 >= percentile * len(ranked))
            out.append(level)
            held.append((now, score))
        return out

    @pytest.mark.parametrize("seed,gap,percentile", [(1, 1.0, 100), (2, 0.2, 99.5), (3, 40.0, 90), (4, 5.0, 50), (5, 1.0, 97.5)])
    def test_the_same_bar_as_the_reference(self, seed, gap, percentile):
        rng = random.Random(seed)
        now, events = 0.0, []
        for _ in range(400):
            now += rng.uniform(0.1, 2 * gap)
            events.append((now, round(rng.choice([1.0, 1.0, 2.0, 3.0, 3.5, 4.0, 5.0, 5.5, 6.0, 7.0, 8.0]) + rng.random(), 2)))
        b = PercentileBar(QualifyingSettings(bar_percentile=percentile, window_seconds=120, min_scores=12, starting_bar=6.8))
        got = []
        for when, score in events:
            got.append(b.level_at(when))
            b.add(score, when)
        assert got == self.reference(events, 120, 12, percentile, 6.8)


class TestDescribing:
    def test_it_says_how_it_works_in_one_line(self):
        text = PercentileBar(QualifyingSettings(bar_percentile=99.5, window_seconds=300, min_scores=20, starting_bar=6.8)).describe()
        assert text == "the 99.5th percentile of the last 300 s of scores (at least the last 20), starting at 6.8"

    def test_it_says_nothing_about_qualifiers_because_the_barkeeper_does(self):
        assert "qualif" not in PercentileBar(QualifyingSettings()).describe()


class TestTheSettings:
    def test_a_negative_starting_bar_is_allowed(self):
        assert bar(starting_bar=-2.5).level_at(0.0) == -2.5

    def test_it_keeps_the_settings_it_was_handed(self):
        settings = QualifyingSettings()
        assert PercentileBar(settings).qualifying is settings


class TestBuildingFromSettings:
    def test_it_is_built_from_the_qualifying_settings(self, logged):
        settings = QualifyingSettings(bar_percentile=95.0, window_seconds=120.0, min_scores=8, starting_bar=5.5)
        b = PercentileBar.from_settings(settings, started_at=0.0)
        assert b.qualifying is settings and b.level_at(0.0) == 5.5
        assert logged.of(b).ok()


class TestLogging:
    def test_nothing_is_logged_by_default(self, logged):
        b = PercentileBar(QualifyingSettings())
        feed(b, [1.0, 2.0, 9.0])
        b.level_at(3.0)
        assert logged.text() == ""


class TestTheValuesItIsHandedAreChecked:
    """Every public method checks what it is handed (Design Audit 04 item 3); the BarKeeper checks first, so these come from other callers."""

    def test_a_time_that_is_not_a_number_is_the_stream_time_now(self, logged):
        b = PercentileBar(QualifyingSettings(min_scores=1, window_seconds=60, bar_percentile=100.0), stream_now=lambda: 30.0)
        b.add(7.0, float("nan"))
        assert b.window_size_at(30.0) == 1 and b.level_at(None) == 7.0
        assert logged.of(b).warnings()[0].startswith("PercentileBar.add was given a now of NaN")

    def test_a_score_that_is_not_a_number_is_left_out_with_a_warning(self, logged):
        b = bar()
        b.add("high", 1.0)
        assert b.window_size_at(1.0) == 0 and len(logged.of(b).warnings()) == 1

    def test_a_score_below_zero_is_zero(self):
        b = PercentileBar(QualifyingSettings(min_scores=1, window_seconds=60, bar_percentile=100.0))
        b.add(-4.0, 1.0)
        assert b.level_at(1.0) == 0.0


def test_a_window_size_asked_with_a_time_that_is_not_a_number_is_asked_at_the_stream_time_now(logged):
    b = PercentileBar(QualifyingSettings(min_scores=1, window_seconds=60, bar_percentile=100.0), stream_now=lambda: 30.0)
    b.add(7.0, 0.0)
    assert b.window_size_at(float("nan")) == b.window_size_at(30.0) == 1
    assert logged.of(b).warnings() == ["PercentileBar.window_size_at was given a now of NaN, which is not a number of at least 0; using 30"]
