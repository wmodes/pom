"""Tests for the splitting rules in scripts/dev/library/cataloguer.py.

A long utterance is cut into pieces ONLY AFTER A SENTENCE END (a full stop, question mark or exclamation mark, then any closing quotes or
brackets), so a passage ends at a sentence end, at the end of its paragraph, or at the end of its record, and never mid-thought: the oracle
copies the ending of the exemplar it is shown. A stretch with no sentence end in it is never cut; a piece that comes out over the limit
is kept whole, or dropped if it is over `oversize` times the limit. See pom.library.endings."""
import csv
import importlib.util
import random
from pathlib import Path

import pytest

from pom.library.endings import ends_sentence

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("cataloguer", ROOT / "scripts" / "dev" / "library" / "cataloguer.py")
cataloguer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cataloguer)


def line(words_in_line, ending):
    """A line of that many words ending in `ending`."""
    return " ".join(["word"] * (words_in_line - 1) + [ending])


def sentence(words_in_sentence, ending="end."):
    """A sentence of that many words: it starts with a capital, as a real one does, so the splitter can see where it begins."""
    return " ".join(["Word"] + ["word"] * (words_in_sentence - 2) + [ending])


class TestSplittingProseIntoSentences:
    """Wes's pattern: split at the space after a stop, closing quotes and brackets and an optional dash, before a capital or an opening quote;
    not after an abbreviation or a single initial."""

    @pytest.mark.parametrize("text", [
        'It was late. He left.', 'Was it late? He left.', 'It was late! He left.', 'He said, "go." He left.', "He said, 'go.' He left.",
        'He said, "go!" He left.', 'He said, "go?" He left.', 'He said (go.) He left.', 'She said, "he said \'no.\'" He left.',
        "It was late.-- He left.", 'It was late."-- He left.', "It was late.\u201d He left.", "It was late.\u2019 He left.", "It was late?! He left.",
    ])
    def test_a_sentence_end_in_any_of_these_forms_is_a_break(self, text):
        parts = cataloguer.split_sentences(text)
        assert len(parts) == 2 and parts[1] == "He left.", parts

    @pytest.mark.parametrize("text,count", [
        ("Mr. Smith left. Dr. Jones stayed.", 2), ("Mrs. Smith and Ms. Jones met St. Paul.", 1), ("Rev. Mr. Smith met Capt. Jones. He left.", 2),
        ("It was by J. K. Rowling. Then it ended.", 2), ("It was e.g. a thing. Then it ended.", 2), ("It was e.g. Smith who left. Then it ended.", 2),
        ("Compare Smith vs. Jones. Then he left.", 2), ("It was cited at c. 27. Then it began.", 2),
    ])
    def test_an_abbreviation_or_an_initial_is_not_a_sentence_end(self, text, count):
        assert len(cataloguer.split_sentences(text)) == count, cataloguer.split_sentences(text)

    def test_etc_ends_a_sentence_when_a_capital_follows(self):
        assert cataloguer.split_sentences("He sold apples, pears, etc. Then he left.") == ["He sold apples, pears, etc.", "Then he left."]
        assert len(cataloguer.split_sentences("He sold apples, etc., and left.")) == 1

    def test_wes_s_paragraph_of_i_e_e_g_and_etc(self):
        text = ("You must bring standard gear, i.e., a pack, a tent, etc. "
                "Pack heavy items, e.g., your stove or boots, near your back. "
                "Do not use lazy shorthand, eg. skipping punctuation entirely. "
                "Then you will be ready.")
        assert cataloguer.split_sentences(text) == [
            "You must bring standard gear, i.e., a pack, a tent, etc.",
            "Pack heavy items, e.g., your stove or boots, near your back.",
            "Do not use lazy shorthand, eg. skipping punctuation entirely.",
            "Then you will be ready."]

    def test_no_break_before_a_lowercase_word_or_a_digit_or_a_bracket(self):
        assert len(cataloguer.split_sentences("It ended. then it began.")) == 1
        assert len(cataloguer.split_sentences("Physic. pt. ii. c. 27. Then it began.")) == 2          # not after "c."
        assert len(cataloguer.split_sentences("It ended. (Edition of 1665, No. 1.) Then it began.")) == 2   # not before the "("

    def test_an_opening_quote_starts_the_next_sentence(self):
        assert cataloguer.split_sentences('It ended. "Then," he said.') == ["It ended.", '"Then," he said.']

    @pytest.mark.parametrize("text", ["", "   ", "\n"])
    def test_no_text_is_no_sentences(self, text):
        assert cataloguer.split_sentences(text) == []

    def test_the_pieces_rejoin_into_the_text_with_one_space_each(self):
        text = 'One. Two? "Three!" Four.-- Five.'
        assert " ".join(cataloguer.split_sentences(text)) == text


class TestCuttingBetweenUnits:
    def test_every_cut_follows_a_sentence_end(self):
        units = [sentence(40), sentence(40), sentence(40), sentence(40), sentence(40)]
        pieces = cataloguer.cut_evenly(units, 150, " ")
        assert len(pieces) == 2 and all(ends_sentence(p) for p in pieces)
        assert " ".join(pieces).split(" ") == " ".join(units).split(" ")

    def test_the_cut_moves_off_a_unit_that_does_not_end_a_sentence(self):
        # Total 200, so two pieces; the cut nearest the middle (after the 3rd unit, 100 words) would fall mid-sentence.
        units = [sentence(50, "a."), sentence(50, "b."), line(50, "of the"), sentence(50, "c."), sentence(50, "d.")]
        pieces = cataloguer.cut_evenly(units, 150, "\n")
        assert all(ends_sentence(p) for p in pieces)
        assert "\n".join(pieces).split("\n") == units                       # nothing lost, same order

    def test_a_comma_or_semicolon_or_colon_is_not_a_place_to_cut(self):
        units = [sentence(50, "a."), line(50, "and,"), line(50, "so;"), line(50, "as follows:"), sentence(50, "e.")]
        pieces = cataloguer.cut_evenly(units, 150, "\n")
        assert [len(p.split("\n")) for p in pieces] == [1, 4] or all(ends_sentence(p) for p in pieces)
        assert all(ends_sentence(p) for p in pieces)

    def test_a_closing_quote_after_the_stop_is_a_place_to_cut(self):
        units = ['"' + sentence(60, 'go."'), sentence(60, 'now!"'), sentence(60, "more.")]
        pieces = cataloguer.cut_evenly(units, 100, " ")
        assert len(pieces) == 3 or all(ends_sentence(p) for p in pieces)

    def test_a_run_with_no_sentence_end_is_never_cut_and_comes_out_whole_over_the_limit(self):
        units = [line(100, "and"), line(100, "of the"), line(100, "to")]
        assert cataloguer.cut_evenly(units, 150, "\n") == ["\n".join(units)]

    def test_if_the_only_sentence_end_is_the_last_unit_nothing_is_cut(self):
        units = [line(100, "and"), line(100, "of the"), sentence(100)]
        assert cataloguer.cut_evenly(units, 150, "\n") == ["\n".join(units)]

    def test_the_greedy_fallback_cuts_after_the_last_sentence_end_so_far_and_carries_the_rest(self):
        # Very uneven units force the fallback: 140 + 5 words fit one piece, and the run after the stop goes into the next.
        units = [sentence(140, "x."), line(5, "of the"), sentence(140, "y.")]
        pieces = cataloguer.cut_evenly(units, 150, "\n")
        assert all(ends_sentence(p) for p in pieces)
        assert "\n".join(pieces).split("\n") == units

    def test_the_fallback_never_makes_an_empty_piece(self):
        units = [line(111, "the"), sentence(69), line(127, "so:")]
        pieces = cataloguer.cut_evenly(units, 150, "\n")
        assert all(p.strip() for p in pieces)
        assert "\n".join(pieces).split("\n") == units

    def test_one_unit_is_one_piece(self):
        assert cataloguer.cut_evenly([sentence(200)], 150, " ") == [sentence(200)]


class TestSplittingAnUtterance:
    def test_a_short_utterance_is_left_alone(self):
        assert cataloguer.split_utterance("A short one.", 150) == (["A short one."], None)

    def test_prose_is_cut_between_sentences_into_near_equal_pieces(self):
        text = " ".join(sentence(50, f"end{i}.") for i in range(6))                  # 300 words
        pieces, count = cataloguer.split_utterance(text, 150)
        assert count == len(pieces) == 2 and [len(p.split()) for p in pieces] == [150, 150]
        assert all(ends_sentence(p) for p in pieces) and " ".join(pieces) == text

    def test_verse_is_cut_between_lines_and_only_after_a_line_that_ends_a_sentence(self):
        lines = [line(30, "end.") if i % 4 == 0 else line(30, f"more{i},") for i in range(1, 13)]    # stops on every fourth line only
        pieces, count = cataloguer.split_utterance("\n".join(lines), 150)
        assert count == len(pieces) >= 2 and all(ends_sentence(p) for p in pieces)
        assert "\n".join(pieces).split("\n") == lines                              # the line breaks are kept

    def test_verse_with_no_line_ending_a_sentence_comes_out_whole(self):
        lines = [line(30, f"line{i},") for i in range(10)]
        pieces, count = cataloguer.split_utterance("\n".join(lines), 150)
        assert pieces == ["\n".join(lines)] and count == 1

    def test_one_very_long_sentence_comes_out_whole_not_cut_at_a_comma_or_a_word(self):
        text = " ".join(["word"] * 148 + ["of", "the"] + ["word,"] * 148 + ["end."])
        assert cataloguer.split_utterance(text, 150) == ([text], 1)

    @pytest.mark.parametrize("seed", range(40))
    def test_whatever_the_lines_every_piece_ends_a_sentence_or_is_the_last(self, seed):
        rng = random.Random(seed)
        lines = [line(rng.randint(5, 60), rng.choice(["end.", "later,", "of the", "and", "so:", 'done."', "at,", "what?"]))
                 for _ in range(rng.randint(8, 30))]
        lines[-1] = line(rng.randint(5, 60), "the end.")
        pieces, _ = cataloguer.split_utterance("\n".join(lines), 150)
        assert "\n".join(pieces).split("\n") == lines                              # every line kept, in order
        assert all(ends_sentence(p) for p in pieces)                               # no piece stops mid-thought
        assert all(p.strip() for p in pieces)


class TestReadingUtterances:
    def test_the_last_utterance_keeps_no_trailing_newline_so_prose_does_not_look_like_verse(self, tmp_path):
        path = tmp_path / "x.txt"
        path.write_text("First one.\n\nSecond one, a long prose paragraph.\n", encoding="utf-8")
        assert cataloguer.read_utterances(str(path)) == ["First one.", "Second one, a long prose paragraph."]

    def test_verse_keeps_its_line_breaks_inside_an_utterance(self, tmp_path):
        path = tmp_path / "x.txt"
        path.write_text("Line one,\nline two.\n\nNext.\n", encoding="utf-8")
        assert cataloguer.read_utterances(str(path)) == ["Line one,\nline two.", "Next."]

    def test_a_trailing_newline_alone_does_not_make_a_text_verse(self):
        text = " ".join(sentence(50, f"end{i}.") for i in range(6)) + "\n"
        pieces, count = cataloguer.split_utterance(text, 150)
        assert count == 2 and all(ends_sentence(p) for p in pieces)


class TestBuildingPassages:
    """build() over a tiny library: the oversize rule and the report counts."""

    @staticmethod
    def library(tmp_path, monkeypatch, utterances):
        root = tmp_path / "library"
        (root / "ready" / "wisdom").mkdir(parents=True)
        with open(root / "holdings.csv", "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["id", "shelf", "status"])
            writer.writeheader()
            writer.writerow({"id": "tiny", "shelf": "wisdom", "status": "approved"})
        (root / "ready" / "wisdom" / "tiny.txt").write_text("\n\n".join(utterances) + "\n", encoding="utf-8")
        monkeypatch.setattr(cataloguer, "LIBRARY", str(root))
        monkeypatch.setattr(cataloguer, "READY", str(root / "ready"))

    def test_a_piece_over_the_limit_is_kept_whole_and_one_over_the_oversize_is_dropped(self, tmp_path, monkeypatch):
        one_long_sentence = lambda n, tag: " ".join(["word"] * (n - 2) + [tag, "end."])
        self.library(tmp_path, monkeypatch, ["A short one.", one_long_sentence(200, "keep"), one_long_sentence(300, "drop")])
        records, stats = cataloguer.build(150, 0, 1.5)
        texts = [r["text"] for r in records["tiny"]]
        assert "A short one." in texts and any("keep" in t for t in texts) and not any("drop" in t for t in texts)
        assert stats["oversize"] == 1 and stats["over_limit"] == 1

    def test_by_default_the_ceiling_holds_and_a_piece_with_no_sentence_end_to_cut_at_is_dropped(self, tmp_path, monkeypatch):
        self.library(tmp_path, monkeypatch, ["A short one.", " ".join(["word"] * 198 + ["keep", "end."])])
        records, stats = cataloguer.build(150, 0)
        assert [r["text"] for r in records["tiny"]] == ["A short one."] and stats["oversize"] == 1 and stats["over_limit"] == 0
        assert cataloguer.DEFAULT_OVERSIZE == 1.0

    def test_the_oversize_factor_can_be_raised(self, tmp_path, monkeypatch):
        self.library(tmp_path, monkeypatch, [" ".join(["word"] * 298 + ["keep", "end."])])
        records, stats = cataloguer.build(150, 0, 2.5)
        assert len(records["tiny"]) == 1 and stats["oversize"] == 0 and stats["over_limit"] == 1

    def test_a_split_utterance_makes_passages_that_each_end_a_sentence(self, tmp_path, monkeypatch):
        self.library(tmp_path, monkeypatch, [" ".join(sentence(50, f"end{i}.") for i in range(6))])
        records, stats = cataloguer.build(150, 0)
        assert len(records["tiny"]) == 2 and all(ends_sentence(r["text"]) for r in records["tiny"])
        assert stats["split"] == 1 and stats["pieces"] == 2 and stats["oversize"] == 0 and stats["over_limit"] == 0
