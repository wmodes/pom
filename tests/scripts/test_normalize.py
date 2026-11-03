"""Tests for the paragraph rules in scripts/dev/library/normalize.py that decide what is a heading.

An ALL-CAPS line is a heading, and headings are dropped as holding furniture. But some books print an emphasised last word in capitals
on a line of its own ("...it seemeth to me nevertheless to be" / "IMPOSSIBLE."), and dropping that left the sentence ending "to be",
which the oracle then copied. The tail of an unfinished sentence belongs to the sentence."""
import importlib.util
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("normalize", ROOT / "scripts" / "dev" / "library" / "normalize.py")
normalize = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(normalize)


def paragraphs(block):
    return normalize.split_prose_block(block, Counter())


class TestAnEmphasisedLastWordStaysWithItsSentence:
    def test_the_nietzsche_case(self):
        block = ("And it is not those who lead OUT OF danger that please you best, but\nthose who lead you away from all paths, the misleaders. But if\n"
                 "such longing in you be ACTUAL, it seemeth to me nevertheless to be\nIMPOSSIBLE.")
        got = paragraphs(block)
        assert len(got) == 1 and got[0].endswith("nevertheless to be IMPOSSIBLE.")

    def test_a_tail_of_several_capital_words(self):
        got = paragraphs("Of all that is written I love only that which a man hath written with his\nown blood and THE SPIRIT OF GRAVITY.")
        assert len(got) == 1 and got[0].endswith("and THE SPIRIT OF GRAVITY.")

    def test_a_tail_of_four_words_is_still_a_tail_and_five_is_a_heading(self):
        lead = "He walked on alone through the long evening and at last he said to them"
        assert len(paragraphs(lead + "\nTHE WORLD IS DEEP")) == 1
        assert len(paragraphs(lead + "\nTHE WORLD IS TOO DEEP")) == 2


class TestAHeadingIsStillAHeading:
    def test_after_a_line_that_finished_its_sentence(self):
        assert paragraphs("He walked on alone through the long evening.\nTHE SECOND PART") == [
            "He walked on alone through the long evening.", "THE SECOND PART"]

    def test_after_a_line_ending_in_a_comma_or_a_colon(self):
        assert len(paragraphs("Yours respectfully, and so on, and so forth, and the rest,\nJOHN RANDOLPH,")) == 2
        assert len(paragraphs("He walked on alone through the long evening and said:\nTHE SECOND PART")) == 2

    def test_a_short_title_line_before_it_is_not_prose(self):
        assert paragraphs("Section I\nINTRODUCTORY AND GENERAL") == ["Section I", "INTRODUCTORY AND GENERAL"]

    def test_a_caps_line_after_a_caps_line_is_another_heading(self):
        assert paragraphs("ON THE ORIGIN AND IMAGINARY EFFICACY OF\nAMULETS & CHARMS,") == [
            "ON THE ORIGIN AND IMAGINARY EFFICACY OF", "AMULETS & CHARMS,"]

    def test_an_indented_caps_line_after_a_caps_line_is_still_another_heading(self):
        assert paragraphs("ON THE ORIGIN AND IMAGINARY EFFICACY OF\n  AMULETS & CHARMS,") == [
            "ON THE ORIGIN AND IMAGINARY EFFICACY OF", "AMULETS & CHARMS,"]

    def test_a_caps_line_that_opens_a_block_is_a_heading(self):
        assert paragraphs("THE FIRST PART\nHe walked on alone through the long evening.") == [
            "THE FIRST PART", "He walked on alone through the long evening."]
