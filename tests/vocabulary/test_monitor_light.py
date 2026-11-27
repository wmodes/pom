"""Tests for pom.monitor.light: the four things a status light can say."""
import pytest

from pom.vocabulary import MonitorLight


def test_the_four_lights_and_their_words():
    assert [light.value for light in MonitorLight] == ["up", "waiting", "down", "none"]
    assert MonitorLight.UP == "up" and MonitorLight.WAITING == "waiting" and MonitorLight.DOWN == "down" and MonitorLight.NONE == "none"


def test_a_light_is_its_word_when_made_into_text():
    assert f"{MonitorLight.WAITING}" == "waiting" and str(MonitorLight.DOWN) == "down"


@pytest.mark.parametrize("word, light", [("up", MonitorLight.UP), ("waiting", MonitorLight.WAITING), ("down", MonitorLight.DOWN), ("none", MonitorLight.NONE)])
def test_a_word_makes_its_light(word, light):
    assert MonitorLight.of(word) is light


@pytest.mark.parametrize("light", list(MonitorLight))
def test_a_light_makes_itself(light):
    assert MonitorLight.of(light) is light


@pytest.mark.parametrize("value, light", [(True, MonitorLight.UP), (False, MonitorLight.DOWN), (None, MonitorLight.NONE)])
def test_the_old_true_false_none_still_make_their_lights(value, light):
    assert MonitorLight.of(value) is light


@pytest.mark.parametrize("value, light", [(1, MonitorLight.UP), ("yes", MonitorLight.UP), (0, MonitorLight.DOWN), ("", MonitorLight.DOWN), ("UP", MonitorLight.UP), (" Waiting ", MonitorLight.WAITING)])
def test_anything_else_is_up_or_down_as_it_would_be_as_a_bool_and_a_word_is_not_fussy_about_case_or_spaces(value, light):
    assert MonitorLight.of(value) is light
