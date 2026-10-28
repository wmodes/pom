"""Tests for pom.listening.whisper_text_sorter: words, ambient captions and nothing-here marks."""
import dataclasses
import random

import pytest

from pom.listening.whisper_text_sorter import (KIND_LYRICS, KIND_NONE, KIND_SPEECH, MUSIC_SYMBOL, WhisperText,
                                               WhisperTextSorter)

NOTE = MUSIC_SYMBOL


@pytest.fixture
def sort():
    return WhisperTextSorter().sort


class TestSpeech:
    def test_plain_speech_is_all_words(self, sort):
        assert sort("Hello there, how are you?") == WhisperText("Hello there, how are you?", (), KIND_SPEECH)

    def test_whitespace_is_tidied(self, sort):
        assert sort("  Hello \n  there\t ").words == "Hello there"

    def test_an_empty_reply_is_nothing(self, sort):
        assert sort("") == WhisperText("", (), KIND_NONE)
        assert sort("   \n ") == WhisperText("", (), KIND_NONE)

    @pytest.mark.parametrize("not_text", [None, 5, 2.5, b"hello", ["hello"], {"a": 1}, True])
    def test_anything_that_is_not_a_string_is_an_empty_reply(self, sort, not_text):
        assert sort(not_text) == WhisperText("", (), KIND_NONE)


class TestNothingHereMarks:
    @pytest.mark.parametrize("mark", ["[BLANK_AUDIO]", "(silence)", "[ Silence ]", "(SILENT)", "[no audio]",
                                      "(no speech)", "[blank audio]", "( Blank_Audio )"])
    def test_a_mark_on_its_own_is_nothing(self, sort, mark):
        assert sort(mark) == WhisperText("", (), KIND_NONE)

    def test_a_mark_is_dropped_and_not_kept_as_a_caption(self, sort):
        heard = sort("hello [BLANK_AUDIO] there")
        assert heard.words == "hello there" and heard.ambient == ()

    def test_two_marks_in_a_row_are_nothing(self, sort):
        assert sort("[BLANK_AUDIO] (silence)") == WhisperText("", (), KIND_NONE)

    def test_a_caption_that_only_mentions_silence_is_still_a_caption(self, sort):
        assert sort("(deafening silence)").ambient == ("(deafening silence)",)


class TestAmbientCaptions:
    def test_a_caption_is_kept_apart_from_the_words(self, sort):
        heard = sort("(dog barks) hello there")
        assert heard.words == "hello there" and heard.ambient == ("(dog barks)",) and heard.kind == KIND_SPEECH

    def test_square_brackets_count_too(self, sort):
        heard = sort("[Music] and then we left")
        assert heard.words == "and then we left" and heard.ambient == ("[Music]",)

    def test_a_caption_alone_leaves_no_words_and_no_kind(self, sort):
        assert sort("(dog barks)") == WhisperText("", ("(dog barks)",), KIND_NONE)

    def test_a_captions_inner_spacing_is_tidied(self, sort):
        assert sort("(dog   barks\n loudly)").ambient == ("(dog barks loudly)",)

    def test_each_caption_is_kept_once_in_the_order_it_first_came(self, sort):
        heard = sort("(dog barks) one [Music] two (dog barks) three (door slams) [Music]")
        assert heard.ambient == ("(dog barks)", "[Music]", "(door slams)")
        assert heard.words == "one two three"

    def test_only_the_innermost_brackets_are_a_caption(self, sort):
        heard = sort("(a (b) c)")
        assert heard.ambient == ("(b)",) and heard.words == "(a c)"

    def test_the_captions_are_a_tuple(self, sort):
        assert isinstance(sort("(dog barks)").ambient, tuple)

    def test_a_caption_with_a_music_symbol_stays_a_caption(self, sort):
        heard = sort(f"[{NOTE} dramatic music {NOTE}] hello")
        assert heard.ambient == (f"[{NOTE} dramatic music {NOTE}]",) and heard.words == "hello"


class TestLyrics:
    def test_the_symbol_is_the_eighth_note_whisper_writes(self, sort):
        assert MUSIC_SYMBOL == "\u266a"
        assert sort("\u266a la la \u266a") == WhisperText("la la", (), KIND_LYRICS)

    def test_words_between_music_symbols_are_lyrics(self, sort):
        assert sort(f"{NOTE} la la la {NOTE}") == WhisperText("la la la", (), KIND_LYRICS)

    def test_the_music_symbols_are_removed(self, sort):
        assert NOTE not in sort(f"{NOTE} twinkle twinkle {NOTE} {NOTE} little star {NOTE}").words

    def test_two_sung_phrases_are_both_kept(self, sort):
        assert sort(f"{NOTE} twinkle twinkle {NOTE} {NOTE} little star {NOTE}").words == "twinkle twinkle little star"

    def test_speech_around_lyrics_makes_the_kind_speech(self, sort):
        heard = sort(f"he said {NOTE} hello world {NOTE} and left")
        assert heard.words == "he said hello world and left" and heard.kind == KIND_SPEECH

    def test_a_lone_music_symbol_is_dropped_and_the_rest_is_speech(self, sort):
        assert sort(f"{NOTE} hello") == WhisperText("hello", (), KIND_SPEECH)

    def test_music_symbols_with_nothing_between_are_nothing(self, sort):
        assert sort(f"{NOTE} {NOTE}") == WhisperText("", (), KIND_NONE)
        assert sort(NOTE) == WhisperText("", (), KIND_NONE)

    def test_a_caption_and_lyrics_are_kept_apart(self, sort):
        heard = sort(f"(crowd cheers) {NOTE} la la {NOTE}")
        assert heard == WhisperText("la la", ("(crowd cheers)",), KIND_LYRICS)


class TestWithoutTheVoiceDetector:
    def test_plain_text_is_thrown_away(self, sort):
        assert sort("hello there", plain_text=False) == WhisperText("", (), KIND_NONE)

    def test_lyrics_are_kept(self, sort):
        assert sort(f"hello {NOTE} la la {NOTE} there", plain_text=False) == WhisperText("la la", (), KIND_LYRICS)

    def test_captions_are_kept(self, sort):
        assert sort("(dog barks) hello", plain_text=False) == WhisperText("", ("(dog barks)",), KIND_NONE)

    def test_a_doubled_symbol_still_opens_the_lyric_that_follows(self, sort):
        assert sort(f"{NOTE}{NOTE} la la {NOTE}", plain_text=False) == WhisperText("la la", (), KIND_LYRICS)

    def test_a_pair_of_symbols_around_only_space_is_not_a_lyric(self, sort):
        assert sort(f"{NOTE} la la {NOTE} {NOTE} {NOTE}", plain_text=False) == WhisperText("la la", (), KIND_LYRICS)
        assert sort(f"{NOTE} {NOTE} {NOTE} {NOTE}", plain_text=False) == WhisperText("", (), KIND_NONE)

    def test_nothing_here_marks_are_still_dropped(self, sort):
        assert sort("[BLANK_AUDIO]", plain_text=False) == WhisperText("", (), KIND_NONE)

    def test_the_default_keeps_plain_text(self, sort):
        assert sort("hello").words == "hello" and sort("hello", plain_text=True).words == "hello"


class TestTheResult:
    def test_it_cannot_be_changed(self, sort):
        heard = sort("hello")
        with pytest.raises(dataclasses.FrozenInstanceError):
            heard.words = "goodbye"

    def test_the_sorter_has_no_memory_between_calls(self):
        sorter = WhisperTextSorter()
        first = sorter.sort("(dog barks) hello")
        sorter.sort(f"{NOTE} la {NOTE}")
        assert sorter.sort("(dog barks) hello") == first


class TestWhateverWhisperWrites:
    PIECES = ["hello", "there", "(dog barks)", "[Music]", "[BLANK_AUDIO]", "(silence)", NOTE, f"{NOTE} la la {NOTE}",
              "(", ")", "[", "]", " ", "  ", "\n", "you", "I'm sorry", "(a (b) c)"]

    @pytest.mark.parametrize("plain_text", [True, False])
    def test_the_sorted_pieces_obey_the_rules_for_any_text(self, sort, plain_text):
        rng = random.Random(7)
        for _ in range(400):
            raw = " ".join(rng.choice(self.PIECES) for _ in range(rng.randint(0, 10)))
            heard = sort(raw, plain_text=plain_text)
            assert NOTE not in heard.words                                           # the symbols are always removed
            assert heard.words == " ".join(heard.words.split())                      # tidy
            assert len(set(heard.ambient)) == len(heard.ambient)                     # each caption once
            assert (heard.kind == KIND_NONE) == (heard.words == "")                  # a kind exactly when there are words
            assert heard.kind in (KIND_SPEECH, KIND_LYRICS, KIND_NONE)
            if not plain_text:
                assert heard.kind in (KIND_LYRICS, KIND_NONE)
