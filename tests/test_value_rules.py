"""Tests for pom.value_rules' `nearest`: a number past an end its rule includes gives that end; anything else has none. (What each rule
accepts and rejects is tested through the settings, in test_config.py and test_defaults.py.)"""
import math

import pytest

from pom import setting_rules, value_rules
from pom.value_rules import above_zero, any_number, at_least, below_one, count, not_negative, percent, whole_at_least


@pytest.mark.parametrize("rule, value, nearest", [
    (at_least(2), 1.5, 2.0),
    (not_negative(), -3, 0.0),
    (percent(), 250, 100.0),
    (below_one(), -0.1, 0.0),
    (whole_at_least(3), 1, 3),
    (count(), -4, 1),
])
def test_a_number_past_an_included_end_gets_that_end(rule, value, nearest):
    assert rule.nearest(value) == nearest


@pytest.mark.parametrize("rule, value", [
    (percent(), 0),               # 0 is not in "above 0", so there is no nearest number to give
    (below_one(), 1.0),           # nor is 1 in "below 1"
    (not_negative(), math.inf),   # no upper end
    (not_negative(), math.nan),
    (at_least(2), "1"),
    (at_least(2), True),          # a boolean is never a number
    (count(), 2.5),               # not a whole number
])
def test_anything_else_has_no_nearest(rule, value):
    assert rule.nearest(value) is None


def test_a_rule_with_no_included_ends_has_no_nearest_at_all():
    assert above_zero().nearest is None and any_number().nearest is None


def test_the_old_names_still_work():
    assert setting_rules.SettingRule is value_rules.ValueRule and setting_rules.above_zero is value_rules.above_zero
    assert value_rules.SettingRejected is value_rules.ValueRejected and value_rules.SettingCorrected is value_rules.ValueCorrected
