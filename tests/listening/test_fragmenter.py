"""Tests for pom.listening.fragmenter: cutting transcript text into sentence, clause and phrase
fragments, all levels together.

The module follows the project principle that nothing may stop the oracle: odd input gives an empty
list or a default, never an exception, unless strict mode (development and tests) is on.
"""
import logging
import random
import string

import pytest

from pom.listening import Fragment, Fragmenter, FragmenterError, Kind


def cut(text, **kwargs):
    """The (kind, text) pairs for a piece of text, in order."""
    return [(f.kind.value, f.text) for f in Fragmenter(**kwargs).split(text)]


def texts(text, kind=None):
    return [f.text for f in Fragmenter().split(text) if kind is None or f.kind.value == kind]


# --- Sentences -------------------------------------------------------------------------

def test_a_plain_sentence_is_one_sentence_fragment():
    assert cut("My dog ate the couch.") == [("sentence", "My dog ate the couch.")]


@pytest.mark.parametrize("text,expected", [
    ("It rained. We stayed in.", ["It rained.", "We stayed in."]),
    ("Did you see that? I did!", ["Did you see that?", "I did!"]),
    ("What?! No way.", ["What?!", "No way."]),
    ("One. Two. Three.", ["One.", "Two.", "Three."]),
])
def test_sentences_split_at_terminal_punctuation(text, expected):
    assert texts(text, "sentence") == expected


def test_text_without_any_terminator_is_one_sentence():
    assert cut("so anyway I was thinking about it") == [("sentence", "so anyway I was thinking about it")]


def test_the_period_before_a_closing_quote_stays_with_its_sentence():
    assert texts('She said "stop it." Then she left.', "sentence") == ['She said "stop it."', "Then she left."]


@pytest.mark.parametrize("text", ["Dr. Smith is here.", "Mr. and Mrs. Jones came.", "Ask St. Paul.",
                                  "Use e.g. a ladder.", "It is big vs. small.", "J. R. R. Tolkien wrote it."])
def test_abbreviations_and_initials_do_not_end_a_sentence(text):
    assert texts(text, "sentence") == [text]


@pytest.mark.parametrize("text", ["The price is 3.5 dollars.", "We met at 10.30 sharp.", "Version 2.0 is out."])
def test_decimals_are_not_sentence_ends(text):
    assert texts(text, "sentence") == [text]


def test_no_is_a_sentence_not_an_abbreviation():
    assert texts("No. I won't do it.", "sentence") == ["No.", "I won't do it."]


def test_the_pronoun_i_and_the_word_a_still_end_a_sentence():
    assert texts("So did I. Then we left.", "sentence") == ["So did I.", "Then we left."]


def test_an_ellipsis_is_a_pause_not_a_sentence_end():
    assert texts("Well... maybe so.", "sentence") == ["Well... maybe so."]
    assert texts("Well… maybe so.", "sentence") == ["Well… maybe so."]


def test_an_ellipsis_at_the_very_end_does_end_the_sentence():
    assert texts("I just thought...", "sentence") == ["I just thought..."]


def test_extra_whitespace_and_newlines_are_normalized():
    assert texts("  It   rained.\n\n We  stayed in.  ", "sentence") == ["It rained.", "We stayed in."]


# --- Clauses ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,clauses", [
    ("We left early; the rain started.", ["We left early", "the rain started."]),
    ("There was one problem: the door was locked.", ["There was one problem", "the door was locked."]),
    ("I saw him — the tall one — yesterday.", ["I saw him", "the tall one", "yesterday."]),
    ("I saw him \u2013 the tall one \u2013 yesterday.", ["I saw him", "the tall one", "yesterday."]),
    ("I saw him - the tall one - yesterday.", ["I saw him", "the tall one", "yesterday."]),
    ("I saw him -- the tall one -- yesterday.", ["I saw him", "the tall one", "yesterday."]),
    ("It was fine—mostly.", ["It was fine", "mostly."]),
    ("I don't know... maybe.", ["I don't know", "maybe."]),
])
def test_clauses_split_at_semicolons_colons_dashes_and_ellipses(text, clauses):
    assert texts(text, "clause") == clauses


@pytest.mark.parametrize("text", ["Meet me at 10:30 sharp.", "A well-known author wrote it.",
                                  "The e-mail came back.", "Call 555-1234 now."])
def test_times_and_hyphenated_words_are_not_clause_breaks(text):
    assert texts(text, "clause") == []
    assert texts(text, "sentence") == [text]


def test_a_sentence_with_no_clause_break_has_no_clause_fragments():
    assert [f for f in Fragmenter().split("My dog ate the couch.") if f.kind is Kind.CLAUSE] == []


# --- Phrases ---------------------------------------------------------------------------

def test_phrases_split_at_commas():
    assert texts("Apples, oranges, and pears.", "phrase") == ["Apples", "oranges", "and pears."]


def test_thousands_separators_are_not_phrase_breaks():
    assert texts("It cost 1,000 dollars.", "phrase") == []


def test_leading_and_trailing_separators_are_trimmed():
    assert all(not f.text[0] in ",;:-" and not f.text[-1] in ",;:-" for f in Fragmenter().split(
        "Well, ; so, - okay: fine, ; then."))


def test_an_unbalanced_quote_mark_is_trimmed_from_the_edge_of_a_piece():
    pieces = texts('He said, "run away, now," and ran.', "phrase")
    assert "run away" in pieces                       # the opening quote was trimmed
    assert not any(p.startswith('"') or p.endswith('"') for p in pieces if p.count('"') % 2)


def test_a_balanced_quotation_keeps_its_quote_marks():
    assert texts('She said "stop it" firmly.', "sentence") == ['She said "stop it" firmly.']
    assert texts('"Stop it", she said, firmly.', "phrase")[0] == '"Stop it"'


# --- All levels together ---------------------------------------------------------------

def test_the_dog_example_offers_the_sentence_and_its_phrases():
    assert cut("Honestly, my dog ate the couch yesterday.") == [
        ("sentence", "Honestly, my dog ate the couch yesterday."),
        ("phrase", "Honestly"),
        ("phrase", "my dog ate the couch yesterday."),
    ]


def test_all_three_levels_come_depth_first_in_speech_order():
    assert cut("We left; it rained, hard.") == [
        ("sentence", "We left; it rained, hard."),
        ("clause", "We left"),
        ("clause", "it rained, hard."),
        ("phrase", "it rained"),
        ("phrase", "hard."),
    ]


def test_a_piece_that_repeats_a_higher_level_is_not_repeated():
    fragments = Fragmenter().split("We left early; it rained.")
    assert [f.text for f in fragments] == ["We left early; it rained.", "We left early", "it rained."]


def test_a_repeated_word_is_offered_once_whatever_its_case():
    """"Okay, okay." has two identical phrases; the second adds nothing as a hinge."""
    assert cut("Okay, okay.") == [("sentence", "Okay, okay."), ("phrase", "Okay")]
    assert texts("No, no, no, never.", "phrase") == ["No", "never."]


def test_a_clause_with_no_comma_has_no_phrases_of_its_own():
    kinds = [f.kind for f in Fragmenter().split("We left; it rained.")]
    assert Kind.PHRASE not in kinds


def test_a_single_word_is_a_valid_fragment():
    assert cut("Yeah.") == [("sentence", "Yeah.")]
    assert ("phrase", "Honestly") in cut("Honestly, it was fine.")


def test_each_fragment_knows_its_sentence_index():
    fragments = Fragmenter().split("First one, ok. Second one. Third; done.")
    assert [(f.sentence, f.text) for f in fragments if f.kind is Kind.SENTENCE] == [
        (0, "First one, ok."), (1, "Second one."), (2, "Third; done.")]
    assert {f.sentence for f in fragments if f.text == "ok."} == {0}


def test_a_realistic_transcript():
    fragments = Fragmenter().split(
        "Uh, so, like, I don't know... maybe? Yeah. Dr. Smith said we leave at 10:30; bring snacks - lots of them, okay?")
    sentences = [f.text for f in fragments if f.kind is Kind.SENTENCE]
    assert sentences == ["Uh, so, like, I don't know... maybe?", "Yeah.",
                         "Dr. Smith said we leave at 10:30; bring snacks - lots of them, okay?"]
    assert "Dr. Smith said we leave at 10:30" in [f.text for f in fragments if f.kind is Kind.CLAUSE]
    assert "bring snacks" in [f.text for f in fragments if f.kind is Kind.CLAUSE]
    assert "like" in [f.text for f in fragments if f.kind is Kind.PHRASE]


def test_curly_quotes_and_apostrophes_survive():
    assert texts("Don’t say “hello,” she said.", "sentence") == [
        "Don’t say “hello,” she said."]


# --- Timestamps and size ---------------------------------------------------------------

def test_the_timestamp_is_stamped_on_every_fragment():
    fragments = Fragmenter().split("One, two. Three.", at=42.5)
    assert fragments and {f.at for f in fragments} == {42.5}


def test_the_timestamp_defaults_to_zero():
    assert {f.at for f in Fragmenter().split("Hello there.")} == {0.0}


def test_fragments_report_their_word_count():
    assert Fragment("one two three", Kind.PHRASE).words == 3


def test_fragments_are_immutable():
    import dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        Fragmenter().split("Hi there.")[0].text = "changed"


def test_text_without_punctuation_is_cut_into_windows_of_max_words():
    words = [f"w{i}" for i in range(25)]
    fragments = Fragmenter(max_words=10).split(" ".join(words))
    assert [f.words for f in fragments] == [10, 10, 5]
    assert " ".join(f.text for f in fragments) == " ".join(words)
    assert {f.kind for f in fragments} == {Kind.SENTENCE}


def test_no_fragment_is_longer_than_max_words():
    long = ", ".join(" ".join(f"w{i}x{j}" for j in range(30)) for i in range(5)) + "."
    assert all(f.words <= 12 for f in Fragmenter(max_words=12).split(long))


def test_the_default_limit_keeps_a_normal_sentence_whole():
    sentence = " ".join(f"word{i}" for i in range(39)) + "."
    assert [f.text for f in Fragmenter().split(sentence)] == [sentence]


# --- Odd input never raises ------------------------------------------------------------

@pytest.mark.parametrize("text", ["", "   ", "\n\t", "...", "-- ; , :", "!!!", "——"])
def test_empty_or_punctuation_only_text_gives_nothing(text):
    assert Fragmenter().split(text) == []


@pytest.mark.parametrize("bad", [None, 5, 3.5, b"bytes", ["a list"], {"a": 1}])
def test_text_that_is_not_a_string_gives_nothing(bad):
    assert Fragmenter().split(bad) == []


def test_non_text_other_than_none_is_reported():
    fragmenter = Fragmenter()
    fragmenter.split(None)
    assert fragmenter.report.ok                       # None is just "nothing heard"
    fragmenter.split(5)
    assert any("expects text" in w for w in fragmenter.report.warnings)


def test_a_number_alone_is_a_fragment():
    assert cut("42") == [("sentence", "42")]


def test_random_junk_never_raises_and_respects_the_limits():
    rng = random.Random(7)
    alphabet = string.ascii_letters + " " * 8 + ".,;:!?-—…\"'()0123456789\n"
    fragmenter = Fragmenter(max_words=8)
    for _ in range(3000):
        junk = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 120)))
        for fragment in fragmenter.split(junk):
            assert fragment.text and fragment.text == fragment.text.strip()
            assert 1 <= fragment.words <= 8
            assert any(c.isalnum() for c in fragment.text)


def test_splitting_is_repeatable():
    text = "Well, I think so; maybe not... Dr. Who? Yes!"
    assert Fragmenter().split(text, at=1.0) == Fragmenter().split(text, at=1.0)


# --- Settings: lenient by default, strict for development -----------------------------

def test_the_default_limit_is_forty_words():
    assert Fragmenter().max_words == 40


@pytest.mark.parametrize("bad", [0, -1, "10", None, True, 2.5])
def test_a_bad_limit_falls_back_to_the_default_with_a_warning(bad):
    fragmenter = Fragmenter(max_words=bad)
    assert fragmenter.max_words == 40
    assert any("max_words must be" in w for w in fragmenter.report.warnings)


@pytest.mark.parametrize("bad", [0, -1, "10", None, True, 2.5])
def test_strict_raises_for_a_bad_limit(bad):
    with pytest.raises(FragmenterError, match="max_words must be"):
        Fragmenter(max_words=bad, strict=True)


def test_strict_raises_for_text_that_is_not_a_string():
    with pytest.raises(FragmenterError, match="expects text"):
        Fragmenter(strict=True).split(5)


def test_strict_accepts_normal_use():
    assert Fragmenter(strict=True).split("Fine, thanks.")


def test_a_custom_limit_is_used():
    assert Fragmenter(max_words=7).max_words == 7


# --- Logging ---------------------------------------------------------------------------

def test_problems_are_logged_so_they_reach_journald(caplog):
    with caplog.at_level(logging.WARNING, logger="pom.listening.fragmenter"):
        Fragmenter(max_words=0)
    assert any(r.levelno == logging.WARNING and "max_words must be" in r.message for r in caplog.records)


def test_a_split_is_logged_at_debug_when_the_module_flag_is_on(caplog):
    from pom.log_setup import get_logger
    get_logger("pom.listening.fragmenter", True)
    try:
        fragmenter = Fragmenter()
        with caplog.at_level(logging.DEBUG):
            fragmenter.split("One, two. Three.")
    finally:
        get_logger("pom.listening.fragmenter", None)
    assert any(r.levelno == logging.DEBUG and "2 sentence(s)" in r.message for r in caplog.records)


def test_a_split_is_not_logged_when_the_module_flag_is_off(caplog):
    from pom.log_setup import get_logger
    get_logger("pom.listening.fragmenter", False)
    try:
        fragmenter = Fragmenter()
        with caplog.at_level(logging.DEBUG):
            fragmenter.split("One, two. Three.")
    finally:
        get_logger("pom.listening.fragmenter", None)
    assert [r for r in caplog.records if r.levelno == logging.DEBUG] == []


def test_a_window_made_only_of_punctuation_is_not_emitted():
    """Long text can leave a window with no letters or digits; that is no hinge."""
    text = " ".join(["word"] * 3 + ["--"] * 9 + ["end"])
    for fragment in Fragmenter(max_words=3).split(text):
        assert any(c.isalnum() for c in fragment.text)
