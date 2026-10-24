"""Tests for pom.hinge.fallback_scorer: the shelved word-counting scorer. Delete this file with the module."""
import pytest

from pom.hinge.fallback_scorer import CEILING, DEFAULT_BASE, FallbackScorer
from pom.hinge.word_tagger import WordTagger

STOP = {"the", "a", "my", "i", "you", "and", "is", "it", "to", "of", "me"}


def fake_tagger(tokens):
    """Digits CD; capitalized words after the first NNP; everything else NN."""
    tags = []
    for index, token in enumerate(tokens):
        if token.isdigit():
            tags.append("CD")
        elif token[0].isupper() and index > 0:
            tags.append("NNP")
        else:
            tags.append("NN")
    return list(zip(tokens, tags))


def score(text, **options):
    words = WordTagger(tagger=fake_tagger, lemmatizer=lambda word, pos: word, stopwords=STOP).tag(text)
    return FallbackScorer(**options).score(text, words)


def test_a_sentence_scores_the_base_plus_a_little_per_content_word():
    assert score("The lamp hums.") == pytest.approx(DEFAULT_BASE + 2 * 0.35)


def test_more_content_words_score_higher():
    assert score("The old brass lamp hums softly tonight.") > score("The lamp hums.")


def test_names_numbers_and_questions_add_a_little():
    plain = score("My dog met them.")
    assert score("My dog met Karen.") == pytest.approx(plain + 0.8)
    assert score("I paid 40 dollars.") == pytest.approx(DEFAULT_BASE + 2 * 0.35 + 0.4)
    assert score("The lamp hums?") == pytest.approx(DEFAULT_BASE + 2 * 0.35 + 0.4)


def test_only_so_many_content_words_names_and_numbers_count():
    ten = score("alpha beta gamma delta epsilon zeta eta theta iota kappa")
    twelve = score("alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu")
    assert twelve == pytest.approx(ten) and ten == pytest.approx(DEFAULT_BASE + 10 * 0.35)
    assert score("My dog met Karen Bob Alice.") == pytest.approx(score("My dog met Karen Bob.") + 0.35)  # a word, not a third name
    assert score("Buy lamps 4 5 6.") == pytest.approx(score("Buy lamps 4 5."))


def test_the_score_is_capped():
    text = "Karen Bob saw 4 5 old brass lamps humming softly tonight inside many dusty rooms?"
    assert score(text) == CEILING


def test_the_base_is_a_setting():
    assert score("The lamp hums.", base=2.0) == pytest.approx(2.0 + 0.7)


def test_empty_text_scores_the_base():
    assert score("") == DEFAULT_BASE
