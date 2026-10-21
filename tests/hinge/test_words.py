"""Tests for pom.hinge.words: reading text as words, with fakes so no NLTK data is needed."""
import pytest

from pom.hinge.words import DEFAULT_FILLERS, Word, WordTools, WordToolsError

STOP = {"the", "my", "a", "about", "i", "you", "and"}


def fake_tagger(tokens):
    """Tags capitalized non-first words NNP, digits CD, 'uh' UH, 'ate' VBD, else NN."""
    tags = []
    for index, token in enumerate(tokens):
        if token.isdigit():
            tags.append("CD")
        elif token.lower() == "uh":
            tags.append("UH")
        elif token.lower() == "ate":
            tags.append("VBD")
        elif token[0].isupper() and index > 0:
            tags.append("NNP")
        else:
            tags.append("NN")
    return list(zip(tokens, tags))


def fake_lemmatizer(word, pos):
    return {"ate": "eat", "dogs": "dog"}.get(word, word)


def tools(**overrides):
    options = dict(tagger=fake_tagger, lemmatizer=fake_lemmatizer, stopwords=STOP)
    options.update(overrides)
    return WordTools(**options)


def by_text(words):
    return {word.text: word for word in words}


class TestAnalyze:
    def test_words_in_order_with_case_kept(self):
        words = tools().analyze("My dog ate Karen's sandwich")
        assert [w.text for w in words] == ["My", "dog", "ate", "Karen's", "sandwich"]

    def test_lemma_is_lowercase_base_form(self):
        found = by_text(tools().analyze("The dogs ate"))
        assert found["dogs"].lemma == "dog"
        assert found["ate"].lemma == "eat"
        assert found["The"].lemma == "the"

    def test_stopwords_are_not_content(self):
        found = by_text(tools().analyze("the dog"))
        assert found["the"].is_stopword and not found["the"].content
        assert found["dog"].content

    def test_proper_nouns_are_names(self):
        found = by_text(tools().analyze("my dog met Karen"))
        assert found["Karen"].is_name
        assert not found["dog"].is_name

    def test_numbers_by_digit_tag_and_word(self):
        found = by_text(tools().analyze("I paid 40 dollars for seven things and 3rd"))
        assert found["40"].is_number and not found["40"].content
        assert found["seven"].is_number
        assert found["3rd"].is_number
        assert not found["dollars"].is_number

    def test_fillers_from_list_and_from_interjection_tag(self):
        found = by_text(tools().analyze("uh okay the dog"))
        assert found["uh"].is_filler          # tagged UH and in the list
        assert found["okay"].is_filler
        assert not found["dog"].is_filler

    def test_interjection_tag_alone_makes_filler(self):
        tagger = lambda tokens: [(t, "UH") for t in tokens]
        assert tools(tagger=tagger, fillers=()).analyze("whoa")[0].is_filler

    def test_phrase_fillers_mark_every_word_of_the_phrase(self):
        words = tools().analyze("the dog you know ate")
        flags = {w.text: w.is_filler for w in words}
        assert flags["you"] and flags["know"]
        assert not flags["dog"] and not flags["ate"]

    def test_phrase_filler_needs_the_whole_phrase(self):
        found = by_text(tools().analyze("you went and I know"))
        assert not found["you"].is_filler and not found["know"].is_filler

    def test_filler_is_never_a_name(self):
        tagger = lambda tokens: [(t, "NNP") for t in tokens]    # "Uh" mis-tagged as a name
        found = by_text(tools(tagger=tagger).analyze("Uh Karen"))
        assert not found["Uh"].is_name and found["Uh"].is_filler
        assert found["Karen"].is_name

    def test_apostrophes_and_hyphens_stay_inside_words(self):
        words = tools().analyze("don't be well-known, it’s fine")
        assert [w.text for w in words] == ["don't", "be", "well-known", "it’s", "fine"]

    def test_punctuation_only_and_empty_give_nothing(self):
        assert tools().analyze("?!... --") == []
        assert tools().analyze("") == []

    def test_content_words_drops_the_rest(self):
        texts = [w.text for w in tools().content_words("uh the dog ate 40 sandwiches")]
        assert texts == ["dog", "ate", "sandwiches"]

    def test_wordnet_pos_follows_the_tag(self):
        assert Word("x", "x", "VBD").wordnet_pos == "v"
        assert Word("x", "x", "NN").wordnet_pos == "n"
        assert Word("x", "x", "JJ").wordnet_pos == "a"
        assert Word("x", "x", "RB").wordnet_pos == "r"
        assert Word("x", "x", "DT").wordnet_pos is None
        assert Word("x", "x").wordnet_pos is None

    def test_word_is_immutable(self):
        with pytest.raises(AttributeError):
            Word("x", "x").text = "y"


class TestRecovery:
    def test_non_text_gives_empty_and_a_warning(self):
        t = tools()
        assert t.analyze(42) == []
        assert len(t.report) == 1

    def test_none_is_quietly_empty(self):
        t = tools()
        assert t.analyze(None) == []
        assert len(t.report) == 0

    def test_non_text_raises_in_strict_mode(self):
        with pytest.raises(WordToolsError):
            tools(strict=True).analyze(42)

    def test_tagger_that_fails_leaves_words_untagged(self):
        def broken(tokens):
            raise RuntimeError("tagger exploded")
        t = tools(tagger=broken)
        words = t.analyze("My dog")
        assert [w.tag for w in words] == ["", ""]
        assert len(t.report) == 1

    def test_lemmatizer_that_fails_falls_back_to_the_word(self):
        def broken(word, pos):
            raise RuntimeError("no")
        assert tools(lemmatizer=broken).analyze("Dogs")[0].lemma == "dogs"

    def test_lemmas_are_cached(self):
        calls = []
        def counting(word, pos):
            calls.append(word)
            return word
        t = tools(lemmatizer=counting)
        t.analyze("dog dog dog")
        assert calls == ["dog"]

    def test_cache_keeps_part_of_speech_apart(self):
        # "saw" the tool and "saw" the past of see must not share one cached answer
        tagger = lambda tokens: [(t, "NN" if i == 0 else "VBD") for i, t in enumerate(tokens)]
        lemmatizer = lambda word, pos: "saw" if pos == "n" else "see"
        first, second = tools(tagger=tagger, lemmatizer=lemmatizer).analyze("saw saw")
        assert (first.lemma, second.lemma) == ("saw", "see")

    def test_no_tagger_guesses_names_from_capitals_but_not_the_first_word(self, tmp_path):
        t = WordTools(nltk_data=str(tmp_path), lemmatizer=fake_lemmatizer, stopwords=STOP)
        assert not t.tagging
        found = by_text(t.analyze("Karen met Bob and I left"))
        assert not found["Karen"].is_name       # sentence start: could be anything
        assert found["Bob"].is_name
        assert not found["I"].is_name
        assert all(w.tag == "" for w in found.values())

    def test_missing_nltk_data_is_reported_not_raised(self, tmp_path):
        t = WordTools(nltk_data=str(tmp_path))
        assert t.analyze("Karen kept dogs")             # still works on plain rules
        assert len(t.report) >= 1

    def test_missing_nltk_data_raises_in_strict_mode(self, tmp_path):
        with pytest.raises(WordToolsError):
            WordTools(nltk_data=str(tmp_path), strict=True)


def test_default_fillers_are_lowercase_and_unique():
    assert all(f == f.lower() for f in DEFAULT_FILLERS)
    assert len(set(DEFAULT_FILLERS)) == len(DEFAULT_FILLERS)


def test_real_nltk_data_if_present():
    from pathlib import Path
    data = Path(__file__).resolve().parents[2] / "data" / "nltk"
    if not data.exists():
        pytest.skip("NLTK data not set up (scripts/setup/setup-nltk-data.sh)")
    t = WordTools(nltk_data=str(data))
    assert t.tagging and len(t.report) == 0
    found = by_text(t.analyze("Karen kept complaining about the parking"))
    assert found["Karen"].is_name
    assert found["complaining"].lemma == "complain"
    assert found["the"].is_stopword
