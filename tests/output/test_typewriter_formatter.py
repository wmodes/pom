"""Tests for the TypewriterFormatter: fences out, ASCII only, wrapped to the line, line feeds after."""
import pytest

from pom.output import TypewriterFormatter


def fmt(text, width=20, indent=4, feeds=0):
    return TypewriterFormatter(width, indent, feeds).format(text)


# --- Wrapping ------------------------------------------------------------------------------

def test_a_long_line_is_wrapped_at_the_line_width():
    assert fmt("one two three four five six seven", 15) == "one two three\nfour five six\nseven"


def test_a_line_that_fits_is_left_alone():
    assert fmt("short enough", 12) == "short enough"


def test_a_word_longer_than_the_line_is_cut_at_the_line_width():
    assert fmt("abcdefghij", 4) == "abcd\nefgh\nij"


def test_line_breaks_the_model_wrote_stay_line_breaks():
    assert fmt("first line\nsecond line", 40) == "first line\nsecond line"


def test_each_written_line_is_wrapped_on_its_own():
    assert fmt("aaa bbb ccc\nddd", 7) == "aaa bbb\nccc\nddd"


def test_runs_of_spaces_collapse_and_indentation_is_dropped():
    assert fmt("   a    b \t c  ", 40) == "a b c"


def test_a_blank_line_stays_but_a_run_of_them_becomes_one():
    assert fmt("a\n\n\n\n\nb") == "a\n\nb"


def test_blank_lines_at_the_start_and_end_are_dropped():
    assert fmt("\n\n  a  \n\n\n") == "a"


def test_windows_and_old_mac_line_endings_are_ordinary_line_breaks():
    assert fmt("a\r\nb\rc") == "a\nb\nc"


@pytest.mark.parametrize("text", ["", "   ", "\n\n", "\t \r\n"])
def test_nothing_in_gives_nothing_out_even_with_line_feeds_after(text):
    assert fmt(text, feeds=4) == ""


# --- Printable ASCII -----------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("“quoted”", '"quoted"'), ("it’s", "it's"), ("‘x’", "'x'"),
    ("a—b", "a--b"), ("a–b", "a-b"), ("wait…", "wait..."), ("• item", "* item"),
    ("café naïve", "cafe naive"), ("Straße", "Strasse"), ("øre", "ore"),
    ("a b", "a b"), ("½ cup", "1/2 cup"), ("ﬁnd", "find"), ("3 × 4", "3 x 4"),
    ("20° hot", "20 degrees hot"),
])
def test_what_the_typewriter_cannot_print_becomes_what_it_can(text, expected):
    assert fmt(text, 40) == expected


def test_what_has_no_plain_form_is_dropped():
    assert fmt("hello \U0001F600 there ☃", 40) == "hello there"


def test_control_characters_are_dropped_and_tabs_become_spaces():
    assert fmt("a\x00b\x07c\x7fd\te", 40) == "abcd e"


def test_only_printable_ascii_and_line_feeds_come_out():
    typed = fmt("mixed “text” — café \U0001F600\n中文 end", 40)
    assert all(c == "\n" or " " <= c <= "~" for c in typed)


# --- Quotations in triple backticks --------------------------------------------------------

def test_a_fenced_quote_is_indented_with_a_blank_line_before_and_after():
    assert fmt('Say:\n```\n"Go now."\n```\nDone.', 30) == 'Say:\n\n    "Go now."\n\nDone.'


def test_the_fence_lines_are_never_printed():
    assert "`" not in fmt("a\n```\nquote\n```\nb")


def test_a_language_tag_after_the_opening_fence_is_not_printed():
    assert fmt("a\n```text\nquote\n```\nb", 30) == "a\n\n    quote\n\nb"


def test_a_fence_inside_a_line_still_makes_a_quote():
    assert fmt("before ```quote``` after", 30) == "before\n\n    quote\n\nafter"


def test_text_on_the_opening_fences_line_is_the_quote_not_a_tag():
    assert fmt("```two words\nmore```", 30) == "    two words\n    more"


def test_a_quote_is_wrapped_to_the_line_width_less_the_indent():
    typed = fmt("```\none two three four five six\n```", 16, indent=4)
    assert typed.split("\n") == ["    one two", "    three four", "    five six"]
    assert max(len(line) for line in typed.split("\n")) <= 16


def test_the_quote_indent_is_the_configured_number_of_spaces():
    assert fmt("```q```", 30, indent=7) == "       q"
    assert fmt("```q```", 30, indent=0) == "q"


def test_an_indent_as_wide_as_the_line_is_cut_down_to_leave_a_character():
    assert fmt("```ab```", 5, indent=9) == "    a\n    b"


def test_a_fence_that_is_never_closed_quotes_everything_after_it():
    assert fmt("Intro.\n```\nlost mid-thought", 30) == "Intro.\n\n    lost mid-thought"


def test_two_fenced_pieces_are_two_quotes():
    assert fmt("a\n```one```\nb\n```two```\nc", 30) == "a\n\n    one\n\nb\n\n    two\n\nc"


def test_the_models_own_blank_lines_around_a_quote_do_not_double_up():
    assert fmt("a\n\n```\nq\n```\n\nb", 30) == "a\n\n    q\n\nb"


def test_a_quote_at_the_very_start_or_end_has_no_blank_line_outside_it():
    assert fmt("```\nq\n```", 30) == "    q"
    assert fmt("```q``` then", 30) == "    q\n\nthen"
    assert fmt("then ```q```", 30) == "then\n\n    q"


def test_an_empty_fence_prints_nothing_but_the_text_around_it():
    assert fmt("a\n```\n```\nb") == "a\n\nb"


def test_a_quote_is_made_printable_too():
    assert fmt("```\n“naïve”\n```", 30) == '    "naive"'


def test_a_blank_line_inside_a_quote_stays_blank_without_trailing_spaces():
    assert fmt("```\none\n\ntwo\n```", 30) == "    one\n\n    two"


# --- Line feeds after ----------------------------------------------------------------------

def test_the_text_ends_with_the_line_feeds_after():
    assert fmt("a", feeds=4) == "a\n\n\n\n"
    assert fmt("a", feeds=1) == "a\n"


def test_no_line_feeds_after_means_no_newline_at_the_end():
    assert fmt("a", feeds=0) == "a"


def test_line_feeds_after_are_counted_after_the_last_line_not_after_blank_lines_the_model_wrote():
    assert fmt("a\n\n\n", feeds=2) == "a\n\n"


def test_the_defaults_are_a_65_character_line_a_4_space_indent_and_4_line_feeds():
    formatter = TypewriterFormatter()
    assert (formatter.line_width, formatter.quote_indent, formatter.linefeeds_after) == (65, 4, 4)


def test_nonsense_numbers_are_made_sane_and_nothing_raises():
    formatter = TypewriterFormatter(0, -3, -1)
    assert (formatter.line_width, formatter.quote_indent, formatter.linefeeds_after) == (1, 0, 0)
    assert formatter.format("ab") == "a\nb"


def test_the_longest_line_never_passes_the_width():
    text = ("A long portent with several sentences, some of them quite lengthy and others short. " * 5
            + "\n```\n" + "A quotation that goes on for a while as well, with words. " * 3 + "\n```\n"
            + "Supercalifragilisticexpialidocious" * 2)
    typed = fmt(text, 30, indent=6)
    assert max(len(line) for line in typed.split("\n")) <= 30
