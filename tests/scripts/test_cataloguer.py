"""Tests for the splitting rules in scripts/dev/library/cataloguer.py: a long utterance is cut into pieces of at most `limit`
words, and never into a piece that stops before its thought is done (it ends on a colon, or on a word like "the" or "of"),
because the oracle copies the ending of the exemplar it is shown. See pom.library.endings."""
import importlib.util
import random
from pathlib import Path

import pytest

from pom.library.endings import ends_unfinished

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("cataloguer", ROOT / "scripts" / "dev" / "library" / "cataloguer.py")
cataloguer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cataloguer)


def line(words_in_line, ending):
    """A verse line of that many words ending in `ending` (a word, possibly with its punctuation)."""
    return " ".join(["word"] * (words_in_line - 1) + [ending])


class TestChunkingWords:
    """The last resort for one long sentence: cut every `limit` words, but not after "the"."""

    def test_the_words_are_all_kept_in_order(self):
        tokens = [f"w{i}" for i in range(23)]
        chunks = cataloguer.chunk_words(tokens, 5)
        assert " ".join(chunks).split() == tokens

    def test_a_chunk_is_never_over_the_limit(self):
        tokens = ["the"] * 40 + ["end"]
        assert all(len(c.split()) <= 5 for c in cataloguer.chunk_words(tokens, 5))

    def test_a_chunk_does_not_end_on_a_word_that_cannot_end_a_thought(self):
        tokens = "one two three four the five six seven eight of nine ten eleven twelve".split()
        chunks = cataloguer.chunk_words(tokens, 5)
        assert chunks == ["one two three four", "the five six seven eight", "of nine ten eleven twelve"]
        assert not any(ends_unfinished(c) for c in chunks)

    def test_a_chunk_does_not_end_on_a_colon(self):
        tokens = "one two three four five: six seven eight nine ten".split()
        assert cataloguer.chunk_words(tokens, 5) == ["one two three four", "five: six seven eight nine", "ten"]

    def test_a_run_of_dangling_words_still_finishes_with_chunks_of_at_least_one_word(self):
        tokens = ["the"] * 12
        chunks = cataloguer.chunk_words(tokens, 5)
        assert " ".join(chunks).split() == tokens and all(chunks)

    def test_the_very_last_chunk_is_whatever_the_text_ends_with(self):
        assert cataloguer.chunk_words("a b c d the".split(), 5) == ["a b c d the"]

    def test_text_short_enough_is_one_chunk_and_nothing_is_one_empty_list(self):
        assert cataloguer.chunk_words("a b".split(), 5) == ["a b"]
        assert cataloguer.chunk_words([], 5) == []


class TestCuttingBetweenUnits:
    def test_the_nearest_cut_to_an_even_share_is_used_when_it_is_fine(self):
        units = [line(60, "one."), line(60, "two."), line(60, "three.")]
        assert cataloguer.cut_evenly(units, 150, "\n") == ["\n".join(units[:2]), units[2]] or \
            len(cataloguer.cut_evenly(units, 150, "\n")) == 2

    def test_the_cut_moves_off_a_unit_that_ends_unfinished(self):
        # Total 200, so two pieces; the cut nearest the middle (after the 3rd unit, 100 words) would end "...of the".
        units = [line(50, "a."), line(50, "b."), line(50, "of the"), line(50, "c."), line(50, "d.")]
        pieces = cataloguer.cut_evenly(units, 150, "\n")
        assert not any(ends_unfinished(p) for p in pieces)
        assert "\n".join(pieces).split("\n") == units                       # nothing lost, same order

    def test_a_colon_ending_is_not_cut_after_either(self):
        units = [line(50, "a."), line(50, "as follows:"), line(50, "b."), line(50, "c.")]
        pieces = cataloguer.cut_evenly(units, 150, "\n")
        assert not any(ends_unfinished(p) for p in pieces)

    def test_if_every_cut_is_unfinished_a_cut_is_still_made(self):
        units = [line(100, "of the"), line(100, "and")]
        pieces = cataloguer.cut_evenly(units, 150, "\n")
        assert len(pieces) == 2 and "\n".join(pieces).split("\n") == units

    def test_the_greedy_fallback_carries_an_unfinished_tail_into_the_next_piece(self):
        # Very uneven units force the fallback; the second unit ends unfinished and must not end a piece.
        units = [line(140, "x."), line(5, "of the"), line(140, "y.")]
        pieces = cataloguer.cut_evenly(units, 150, "\n")
        assert not any(ends_unfinished(p) for p in pieces)
        assert "\n".join(pieces).split("\n") == units
        assert all(len(p.split()) <= 150 for p in pieces)


class TestSplittingAnUtterance:
    def test_a_short_utterance_is_left_alone(self):
        assert cataloguer.split_utterance("A short one.", 150) == (["A short one."], None)

    def test_verse_is_cut_between_lines_and_not_after_an_unfinished_line(self):
        lines = [line(30, "the") if i % 3 == 0 else line(30, f"end{i},") for i in range(1, 13)]    # a speech of 360 words
        pieces, count = cataloguer.split_utterance("\n".join(lines), 150)
        assert count == len(pieces) >= 3
        assert not any(ends_unfinished(p) for p in pieces[:-1])
        assert "\n".join(pieces).split("\n") == lines

    def test_a_long_sentence_with_no_clause_breaks_is_chunked_without_cutting_on_a_dangling_word(self):
        text = " ".join(["word"] * 148 + ["of", "the"] + ["word"] * 148 + ["end."])
        pieces, count = cataloguer.split_utterance(text, 150)
        assert count == len(pieces) and " ".join(pieces) == text
        assert not any(ends_unfinished(p) for p in pieces)

    @pytest.mark.parametrize("seed", range(40))
    def test_whatever_the_lines_no_piece_ends_unfinished_or_runs_over(self, seed):
        rng = random.Random(seed)
        lines = [line(rng.randint(5, 60), rng.choice(["end.", "later,", "of the", "and", "so:", "done!", "at,"])) for _ in range(rng.randint(8, 30))]
        lines[-1] = line(rng.randint(5, 60), "the end.")
        pieces, _ = cataloguer.split_utterance("\n".join(lines), 150)
        assert " ".join(pieces).split() == " ".join(lines).split()           # every word kept, in order (line breaks may go)
        assert all(len(p.split()) <= 150 for p in pieces)
        assert not any(ends_unfinished(p) for p in pieces)


    def test_when_no_line_boundary_can_avoid_it_the_text_is_cut_at_words_and_every_word_is_kept(self):
        # Five lines in a row all end unfinished and the run is longer than the limit: only a cut at words can avoid "...and".
        lines = [line(40, "end."), line(30, "of the"), line(40, "and"), line(30, "so:"), line(40, "at,"), line(40, "the end.")]
        text = "\n".join(lines)
        pieces, count = cataloguer.split_utterance(text, 150)
        assert count == len(pieces) and " ".join(pieces).split() == text.split()
        assert not any(ends_unfinished(p) for p in pieces) and all(len(p.split()) <= 150 for p in pieces)
        assert all("\n" not in p for p in pieces)

    def test_the_line_breaks_are_kept_when_a_cut_between_lines_is_fine(self):
        lines = [line(60, "end."), line(60, "end."), line(60, "end."), line(60, "end.")]
        pieces, _ = cataloguer.split_utterance("\n".join(lines), 150)
        assert all("\n" in p for p in pieces) and "\n".join(pieces).split("\n") == lines


class TestFlaws:
    def test_it_counts_pieces_that_end_unfinished_or_run_over(self):
        assert cataloguer.flaws(["Fine.", "ends on the", "also fine!", "one two three four"], 3) == 2
        assert cataloguer.flaws([], 150) == 0


class TestTheEdges:
    def test_the_greedy_fallback_never_makes_an_empty_piece_when_the_first_unit_ends_unfinished(self):
        # Found by search: the units are uneven enough that the even cut is over the limit, so the fallback fills greedily, and the
        # first unit (111 words) ends on "the". Carrying it forward would leave an empty piece behind.
        units = [line(111, "the"), line(69, "end."), line(127, "so:")]
        pieces = cataloguer.cut_evenly(units, 150, "\n")
        assert all(p.strip() for p in pieces) and [len(p.split()) for p in pieces] == [111, 69, 127]
        assert "\n".join(pieces).split("\n") == units

    def test_flowed_chunks_are_near_equal_not_a_full_chunk_and_a_tiny_tail(self):
        lines = [line(100, "of the"), line(100, "and"), line(110, "to the end.")]            # 310 words, no line boundary is fine
        pieces, count = cataloguer.split_utterance("\n".join(lines), 150)
        sizes = sorted(len(p.split()) for p in pieces)
        assert count == 3 and sizes[-1] - sizes[0] <= 4, sizes
