"""Tests for pom.library.endings: which passages stop before their thought is done."""
import pytest

from pom.library.endings import CLOSING_MARKS, DANGLING_WORDS, ends_sentence, ends_unfinished


class TestAFinishedPassage:
    @pytest.mark.parametrize("text", [
        "The cellar remembers every footstep.",
        "Is anyone there?",
        "Stop!",
        'She said, "come in."',
        "He left (quietly)",
        "A list [of things]",
        "He said 'no'",
        "Trailing spaces are ignored.   \n",
    ])
    def test_ending_on_a_closing_mark_is_finished(self, text):
        assert ends_unfinished(text) is False

    @pytest.mark.parametrize("text", [
        "Greedy gold took little mine,",         # verse may end on a comma
        "The wipers blur the greyest sky;",      # or a semicolon
        "And rain drives me to dust",            # or no mark at all
        "Mass. Brass",
    ])
    def test_a_comma_semicolon_or_bare_ending_that_is_not_dangling_is_finished(self, text):
        assert ends_unfinished(text) is False

    def test_every_closing_mark_is_a_mark_that_ends_a_passage(self):
        for mark in CLOSING_MARKS:
            assert ends_unfinished(f"The end of it all{mark}") is False


class TestAnUnfinishedPassage:
    def test_a_lead_in_ending_on_a_colon_is_unfinished(self):
        assert ends_unfinished("The following bugle signals may be used off the battle field, when not likely to convey information to the enemy:") is True

    def test_a_colon_followed_by_spaces_is_still_a_colon(self):
        assert ends_unfinished("Note the following:  \n") is True

    @pytest.mark.parametrize("word", sorted(DANGLING_WORDS))
    def test_every_dangling_word_marks_an_unfinished_ending(self, word):
        assert ends_unfinished(f"The chief of these, and one that has been in silence, {word}") is True

    def test_the_words_are_matched_whatever_the_case(self):
        assert ends_unfinished("decades, is THE") is True
        assert ends_unfinished("decades, is The") is True

    @pytest.mark.parametrize("tail", [",", ";", " -", " --"])
    def test_a_dangling_word_followed_by_other_marks_is_still_dangling(self, tail):
        # "...and then the," or "...of --" were cut as surely as "...of".
        assert ends_unfinished("and then the" + tail) is True

    def test_an_ellipsis_is_a_closing_mark_so_the_writer_meant_to_trail_off(self):
        assert ends_unfinished("and then the...") is False

    def test_the_dangling_word_must_be_the_whole_last_word(self):
        assert ends_unfinished("and then he saw another") is False      # ends in "other", not "a"
        assert ends_unfinished("lather") is False                       # ends in "her" but is not "her"
        assert ends_unfinished("and then he saw them") is False

    def test_the_printed_example_that_started_this(self):
        assert ends_unfinished("The chief of these, and one that has been in silence for many\ndecades, is the") is True


class TestWhatIsNotJudged:
    @pytest.mark.parametrize("value", ["", "   ", "\n", None, 5, b"the", ["the"]])
    def test_no_ending_is_not_unfinished(self, value):
        assert ends_unfinished(value) is False

    def test_one_word_that_is_dangling_is_unfinished(self):
        assert ends_unfinished("the") is True

    def test_a_word_that_ends_a_sentence_is_finished_with_a_full_stop(self):
        assert ends_unfinished("That is all about her.") is False


class TestEdgesOfTheTest:
    @pytest.mark.parametrize("text", ["--", ",", " ; ", "- -"])
    def test_a_passage_with_no_words_in_it_is_not_judged(self, text):
        assert ends_unfinished(text) is False

    def test_the_words_that_cannot_end_a_thought_are_exactly_these(self):
        # Pinned, so adding or removing a word is a decision that shows up here (and in the library's 1,059 passages).
        assert DANGLING_WORDS == frozenset({
            "a", "an", "the", "of", "and", "or", "but", "to", "in", "on", "at", "by", "with", "for", "from", "into", "as",
            "is", "was", "that", "which", "its", "his", "her", "their", "our"})


class TestASentenceEnd:
    """Where the library's cataloguer may cut: a full stop, question mark or exclamation mark, then any closing quotes or brackets."""

    @pytest.mark.parametrize("text", [
        "He left.", "Is it?", "Stop!", "What?!", "Wait...", "ends here.   ", "End.\n",
        'He said, "go."', "He said, 'go.'", 'He said, "go!"', 'He asked, "why?"', "She said, \"he said 'no.'\"", "She said, 'he said \"no.\"'",
        "(and so on.)", "[see above.]", 'He said (and meant it.")', "by them.--", 'by them."--', "by them.'\"", "by them.\u201d", "by them.\u2019",
        "by them.\u2014",
    ])
    def test_these_end_a_sentence(self, text):
        assert ends_sentence(text) is True, text

    @pytest.mark.parametrize("text", [
        "He left", 'He said "go"', "'Some are born great'--", "although", "Dr. Smith, the", "as follows:", "and then,", "Yes;",
        "a dash after no stop --", "ends on a comma,\n", "(and so on)", "He said 'go'",
    ])
    def test_these_do_not(self, text):
        assert ends_sentence(text) is False, text

    @pytest.mark.parametrize("value", ["", "   ", "\n", None, 5, b"end.", ["end."]])
    def test_nothing_to_judge_is_not_a_sentence_end(self, value):
        assert ends_sentence(value) is False

    def test_a_full_stop_in_the_middle_is_not_the_end(self):
        assert ends_sentence("It ended. Then it began") is False

