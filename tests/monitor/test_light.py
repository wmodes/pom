"""Tests for pom.monitor.light: the four things a status light can say."""
import pytest

from pom.monitor.light import Light


def test_the_four_lights_and_their_words():
    assert [light.value for light in Light] == ["up", "waiting", "down", "none"]
    assert Light.UP == "up" and Light.WAITING == "waiting" and Light.DOWN == "down" and Light.NONE == "none"


def test_a_light_is_its_word_when_made_into_text():
    assert f"{Light.WAITING}" == "waiting" and str(Light.DOWN) == "down"


@pytest.mark.parametrize("word, light", [("up", Light.UP), ("waiting", Light.WAITING), ("down", Light.DOWN), ("none", Light.NONE)])
def test_a_word_makes_its_light(word, light):
    assert Light.of(word) is light


@pytest.mark.parametrize("light", list(Light))
def test_a_light_makes_itself(light):
    assert Light.of(light) is light


@pytest.mark.parametrize("value, light", [(True, Light.UP), (False, Light.DOWN), (None, Light.NONE)])
def test_the_old_true_false_none_still_make_their_lights(value, light):
    assert Light.of(value) is light


@pytest.mark.parametrize("value, light", [(1, Light.UP), ("yes", Light.UP), (0, Light.DOWN), ("", Light.DOWN), ("UP", Light.UP), (" Waiting ", Light.WAITING)])
def test_anything_else_is_up_or_down_as_it_would_be_as_a_bool_and_a_word_is_not_fussy_about_case_or_spaces(value, light):
    assert Light.of(value) is light
