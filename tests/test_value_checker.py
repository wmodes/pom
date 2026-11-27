"""Tests for pom.value_checker: a public method checks what it is handed, keeps a good value, turns a bad one into the kept part, the nearest
good value or its fallback value, and warns in its own name, never the checker's and never quoting words."""
import math

import pytest

from pom.bookkeeper import Bookkeeper
from pom.value_checker import ValueChecker, described
from pom.value_rules import at_least, count, not_negative, only_characters, percent, text


def checker(strict=False):
    return ValueChecker(Bookkeeper().logger_for("pom.monitor.turn_history", strict=strict), "TurnHistory")


class TestAGoodValueComesBackAsTheRuleStoresIt:
    def test_a_good_number_is_kept_without_a_word(self, logged):
        assert checker()(3, not_negative(), fallback_value=0.0, method="add", argument="heard_at") == 3.0
        assert logged.ok()

    def test_a_good_count_stays_whole(self, logged):
        value = checker()(4, count(), fallback_value=0, method="add", argument="window_size")
        assert value == 4 and isinstance(value, int) and logged.ok()


class TestABadValueGetsTheNearestGoodValueOrTheFallbackValue:
    def test_a_number_below_the_range_gets_its_low_end(self, logged):
        assert checker()(-2.5, not_negative(), fallback_value=None, method="add", argument="heard_at") == 0.0
        assert logged.warnings() == ["TurnHistory.add was given a heard_at of -2.5, which is not a number of at least 0; using the nearest, 0"]

    def test_a_number_above_the_range_gets_its_high_end(self, logged):
        assert checker()(150, percent(), fallback_value=None, method="add", argument="share") == 100.0

    def test_a_count_below_one_gets_one(self, logged):
        assert checker()(0, count(), fallback_value=7, method="add", argument="window_size") == 1

    def test_nan_has_no_nearest_so_it_gets_the_fallback_value(self, logged):
        assert checker()(math.nan, not_negative(), fallback_value=None, method="add", argument="heard_at") is None
        assert logged.warnings() == ["TurnHistory.add was given a heard_at of NaN, which is not a number of at least 0; using None"]

    def test_the_wrong_type_gets_the_fallback_value(self, logged):
        assert checker()("12", at_least(1), fallback_value=1.0, method="add", argument="score") == 1.0

    def test_a_value_the_rule_can_keep_part_of_comes_back_as_that_part(self, logged):
        assert checker()("aab!", only_characters("ab"), fallback_value="", method="set", argument="keys") == "ab"
        assert len(logged.warnings()) == 1 and logged.warnings()[0].startswith("TurnHistory.set was given a keys that may hold only")


class TestTheWarningIsTheReceivers:
    def test_it_goes_through_the_receivers_own_logger(self, logged):
        checker()(-1, not_negative(), fallback_value=None, method="add", argument="heard_at")
        assert logged.warnings("pom.monitor.turn_history") and not logged.warnings("pom.value_checker")

    def test_it_names_the_receiving_class_method_and_argument_not_the_checker(self, logged):
        checker()(None, not_negative(), fallback_value=0.0, method="add", argument="heard_at")
        assert logged.warnings()[0].startswith("TurnHistory.add was given a heard_at of None") and "ValueChecker" not in logged.text()

    def test_an_argument_starting_with_a_vowel_reads_as_an(self, logged):
        checker()(None, not_negative(), fallback_value=0.0, method="add", argument="at")
        assert logged.warnings()[0].startswith("TurnHistory.add was given an at of None")

    def test_it_never_quotes_words_handed_in_by_mistake(self, logged):
        checker()("my mother's dog barked in her voice", not_negative(), fallback_value=0.0, method="add", argument="heard_at")
        assert "a str" in logged.warnings()[0] and "dog" not in logged.text()

    def test_in_strict_mode_the_receivers_logger_raises_instead(self):
        with pytest.raises(ValueError, match="TurnHistory.add was given a heard_at"):
            checker(strict=True)(-1, not_negative(), fallback_value=None, method="add", argument="heard_at")


class TestAValueIsDescribedWithoutItsWords:
    @pytest.mark.parametrize("value, words", [(2.5, "2.5"), (math.nan, "NaN"), (math.inf, "infinity"), (-math.inf, "minus infinity"),
                                              (None, "None"), (True, "True"), ("secret", "a str"), ([1], "a list"), (object(), "an object"),
                                              (math.floor, "math.floor")])
    def test_each_kind(self, value, words):
        assert described(value) == words

    def test_a_rule_with_no_ends_has_no_nearest(self, logged):
        assert checker()(5, text(), fallback_value="", method="set", argument="label") == ""


class TestTheKindsOfValueHaveOneRuleAndOneFallbackValueEach:
    """The table agreed with Wes (Design Audit 04 item 3), written once, in the ValueChecker."""

    @staticmethod
    def kinds(stream_now=lambda: 42.0):
        return ValueChecker(Bookkeeper().logger_for("pom.monitor.turn_history"), "TurnHistory", stream_now)

    @pytest.mark.parametrize("value, expected", [(3, 3.0), (-1, 0.0), (-math.inf, 0.0), (math.nan, None), (math.inf, None), ("7", None)])
    def test_a_score(self, value, expected):
        assert self.kinds().score(value, method="add") == expected

    def test_an_unscored_score_is_quiet_where_it_is_in_spec_and_a_warning_where_it_is_not(self, logged):
        assert self.kinds().score(None, method="add") is None and logged.ok()
        assert self.kinds().score(None, method="add", may_be_unscored=False) is None and len(logged.warnings()) == 1

    @pytest.mark.parametrize("value, expected", [(12.5, 12.5), (-3, 0.0), (math.nan, 42.0), (math.inf, 42.0), (None, 42.0), ("1", 42.0)])
    def test_a_stream_time(self, value, expected):
        assert self.kinds().stream_time(value, method="add", argument="heard_at") == expected

    def test_the_stream_clock_is_asked_only_for_a_time_that_is_not_a_number(self):
        asked = []
        kinds = self.kinds(stream_now=lambda: asked.append(1) or 42.0)
        kinds.stream_time(5.0, method="add", argument="at")
        kinds.stream_time(-5.0, method="add", argument="at")
        assert asked == []

    @pytest.mark.parametrize("value, expected", [(6.5, 6.5), (None, None), (-1, 0.0), (math.nan, None), ("high", None)])
    def test_a_bar(self, value, expected):
        assert self.kinds().bar(value, method="add") == expected

    @pytest.mark.parametrize("value, expected", [(4, 4), (0, 0), (-2, 0), (2.5, 0), (None, 0), (True, 0)])
    def test_a_count(self, value, expected):
        assert self.kinds().count(value, method="add", argument="window_size") == expected

    def test_without_a_stream_clock_the_checker_keeps_its_own(self):
        kinds = ValueChecker(Bookkeeper().logger_for("pom.monitor.turn_history"), "TurnHistory")
        assert 0.0 <= kinds.stream_time(math.nan, method="add", argument="at") < 5.0
