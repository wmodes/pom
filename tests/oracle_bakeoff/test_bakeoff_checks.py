"""Tests for the bake-off's mechanical checks: what counts as repeating the hinge, extra text, and a close length.

These checks decide three of the rubric's seven items for every model, so a wrong one would skew the whole
comparison. The module lives with the experiment, not in src/pom, so it is put on the path here.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments" / "oracle-bakeoff"))
from bakeoff_checks import (check_portent, extra_text, length_close, length_ratio,   # noqa: E402
                            longest_shared_run, repeats_hinge, words)

FATHER = "I watched my father die and I felt nothing at all."


class TestWords:
    def test_lower_case_without_punctuation(self):
        assert words("Hello, World! It's 5.") == ["hello", "world", "it's", "5"]

    def test_a_curly_apostrophe_is_a_straight_one(self):
        assert words("don’t") == ["don't"]

    def test_nothing_gives_an_empty_list(self):
        assert words("  ...  ") == []


class TestLongestSharedRun:
    def test_the_longest_unbroken_run(self):
        assert longest_shared_run("a b c d e".split(), "x b c d y".split()) == 3

    def test_words_that_are_apart_do_not_count_as_a_run(self):
        assert longest_shared_run("a b c".split(), "a x b x c".split()) == 1

    def test_no_words_in_common(self):
        assert longest_shared_run(["a"], ["b"]) == 0

    def test_an_empty_list(self):
        assert longest_shared_run([], ["a"]) == 0 and longest_shared_run(["a"], []) == 0


class TestRepeatsHinge:
    def test_the_whole_hinge_repeated(self):
        assert repeats_hinge(FATHER, "He said it plainly. " + FATHER)

    def test_one_word_changed_still_leaves_a_long_run(self):
        # "die and I felt nothing at all" is seven words in a row
        assert repeats_hinge(FATHER, "I watched my mother die and I felt nothing at all.")

    def test_five_words_in_a_row_is_a_repeat(self):
        assert repeats_hinge(FATHER, "Then I felt nothing at all, and wept.")

    def test_four_words_in_a_row_is_not(self):
        assert not repeats_hinge(FATHER, "He felt nothing at all, and wept.")

    def test_the_same_words_scattered_are_not_a_repeat(self):
        assert not repeats_hinge(FATHER, "My father, I watched; nothing at all, I felt, and I die.")

    def test_a_short_hinge_needs_a_run_of_sixty_percent(self):
        # six words: 60% rounds up to four
        assert repeats_hinge("I think my car is possessed.", "Still, my car is possessed by the dark.")
        assert not repeats_hinge("I think my car is possessed.", "Still, my car runs.")

    def test_three_words_in_a_row_from_a_short_hinge_is_not_a_repeat(self):
        # six words need a run of four (60% rounded up); "my car is" is only three
        assert not repeats_hinge("I think my car is possessed.", "Still, my car is a ruin.")

    def test_case_and_punctuation_do_not_hide_a_repeat(self):
        assert repeats_hinge(FATHER, "I WATCHED my father die, and I felt nothing... at all")

    @pytest.mark.parametrize("hinge", ["", "   ", "...!"])
    def test_a_hinge_without_words_cannot_be_repeated(self, hinge):
        assert not repeats_hinge(hinge, "Anything at all.")


class TestExtraText:
    def test_a_clean_portent(self):
        assert extra_text("The room was cold.\nThe lamp was out.") == []

    def test_a_code_fence(self):
        assert extra_text("```\nThe room was cold.\n```") == ["a code fence"]

    @pytest.mark.parametrize("first", ["# The Candle", "**The Candle**"])
    def test_a_heading_or_bold_title(self, first):
        assert extra_text(first + "\nThe room was cold.") == ["a heading or bold title"]

    @pytest.mark.parametrize("first", ["Here is your passage:", "Sure! Here you go.", "Certainly, here", "Okay"])
    def test_a_preface(self, first):
        assert "a preface" in extra_text(first + "\nThe room was cold.")

    @pytest.mark.parametrize("first", ["Passage: The room was cold.", "Example: The room was cold.", "Theme: cold"])
    def test_a_label(self, first):
        assert extra_text(first) == ["a label"]

    def test_a_note_on_a_later_line(self):
        assert extra_text("The room was cold.\n\nNote: this follows the style.") == ["a note or explanation"]

    def test_a_word_like_a_preface_inside_a_line_is_not_extra(self):
        assert extra_text("The room was cold, and sure of itself.") == []

    def test_a_first_line_in_quotes_is_not_extra_because_dialogue_is_allowed(self):
        assert extra_text('"Nothing?" asked the Mockingbird.') == []

    def test_empty_text(self):
        assert extra_text("") == []


class TestLength:
    def test_ratio_is_portent_words_over_exemplar_words(self):
        assert length_ratio("one two three four", "one two") == 2.0

    def test_an_exemplar_without_words_gives_zero(self):
        assert length_ratio("one two", "") == 0.0

    @pytest.mark.parametrize("portent_words, close", [(4, False), (5, True), (10, True), (20, True), (21, False)])
    def test_half_to_double_of_ten_words_is_close_and_the_edges_count(self, portent_words, close):
        assert length_close("word " * portent_words, "word " * 10) is close


class TestCheckPortent:
    def test_all_three_checks_in_one_dictionary(self):
        result = check_portent(FATHER, "The lamp went out slowly.", ["Ah, the lamp is out.", "And so is the sun."])
        assert result == {"repeats_hinge": False, "extra_text": [], "length_ratio": 0.5, "length_close": True}

    def test_a_bad_portent_fails_each_check(self):
        result = check_portent(FATHER, "Here is a story: " + FATHER + " " + "more " * 40, ["Ah."])
        assert result["repeats_hinge"] and "a preface" in result["extra_text"] and not result["length_close"]

    def test_the_ratio_is_rounded_to_two_places(self):
        assert check_portent("A hinge.", "word " * 3, ["word " * 7])["length_ratio"] == 0.43

    def test_the_length_is_judged_against_the_exemplars_together(self):
        result = check_portent("A hinge.", "word " * 8, ["word " * 5, "word " * 5])
        assert result["length_ratio"] == 0.8 and result["length_close"]
