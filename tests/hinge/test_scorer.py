"""Tests for pom.hinge.scorer: filter, model, fallback and circuit breaker, with fakes throughout."""
import logging

import pytest

from pom.hinge.scorer import (DEFAULT_FAILURE_LIMIT, DEFAULT_FLOOR, DEFAULT_RETRY_SECONDS,
                              FALLBACK_CEILING, ScoredFragment, Scorer, ScorerError, Source)
from pom.hinge.word_tagger import WordTagger
from pom.listening.fragmenter import Fragment, Kind

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


def words():
    return WordTagger(tagger=fake_tagger, lemmatizer=lambda word, pos: word, stopwords=STOP)


class FakeClient:
    """Stands in for the InterestClient: returns a canned score, or None, and records the calls."""

    def __init__(self, score=6.5, available=True):
        self.value = score
        self.available = available
        self.calls = []
        self.warmed = 0

    def score(self, text):
        self.calls.append(text)
        return self.value

    def warm_up(self):
        self.warmed += 1
        return self.value is not None


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def make(client=None, **options):
    options.setdefault("clock", Clock())
    options.setdefault("strict", False)
    return Scorer(client or FakeClient(), words(), **options)


def fragment(text, at=0.0):
    return Fragment(text=text, kind=Kind.SENTENCE, at=at)


class TestModel:
    def test_a_sentence_with_content_goes_to_the_model(self):
        client = FakeClient(7.25)
        result = make(client).score(fragment("The lamp hums."))
        assert (result.score, result.source) == (7.25, Source.MODEL)
        assert client.calls == ["The lamp hums."]

    def test_the_result_carries_the_fragment(self):
        f = fragment("The lamp hums.", at=12.5)
        result = make().score(f)
        assert isinstance(result, ScoredFragment) and result.fragment is f
        assert result.text == "The lamp hums."

    def test_score_text_makes_a_sentence_fragment(self):
        result = make().score_text("The lamp hums.", at=3.0)
        assert result.fragment.kind is Kind.SENTENCE and result.fragment.at == 3.0

    def test_score_many_keeps_the_order_given(self):
        client = FakeClient()
        scorer = make(client)
        texts = ["The lamp hums.", "My dog met Karen.", "A door in the basement."]
        results = scorer.score_many([fragment(t) for t in texts])
        assert [r.text for r in results] == texts and client.calls == texts

    def test_ranked_is_best_first_and_ties_keep_their_order(self):
        scores = {"The lamp hums.": 4.0, "My dog met Karen.": 8.0, "A door in the basement.": 4.0}

        class ByText(FakeClient):
            def score(self, text):
                return scores[text]

        ranked = make(ByText()).ranked([fragment(t) for t in scores])
        assert [r.text for r in ranked] == ["My dog met Karen.", "The lamp hums.", "A door in the basement."]

    def test_the_model_is_asked_about_one_fragment_at_a_time(self):
        client = FakeClient()
        make(client).score_many([fragment("The lamp hums."), fragment("A door in the basement.")])
        assert all(isinstance(call, str) for call in client.calls) and len(client.calls) == 2

    def test_counts_by_source(self):
        scorer = make()
        scorer.score_text("The lamp hums.")
        scorer.score_text("Yeah okay")
        assert scorer.counts() == {"model": 1, "filter": 1, "fallback": 0}


class TestFilter:
    def test_all_filler_is_not_sent_to_the_model(self):
        client = FakeClient()
        result = make(client).score_text("Yeah, okay.")
        assert (result.score, result.source) == (DEFAULT_FLOOR, Source.FILTER)
        assert client.calls == []

    def test_only_stopwords_and_numbers_is_filtered(self):
        client = FakeClient()
        assert make(client).score_text("It is the 40.").source is Source.FILTER
        assert client.calls == []

    def test_empty_text_is_filtered_and_is_not_a_failure(self):
        client = FakeClient(None)
        scorer = make(client, failure_limit=1)
        for text in ("", "   ", "..."):
            assert scorer.score_text(text).source is Source.FILTER
        assert client.calls == [] and not scorer.degraded

    def test_the_floor_is_configurable(self):
        assert make(floor=2.5).score_text("Yeah, okay.").score == 2.5

    def test_min_content_words_raises_the_bar(self):
        client = FakeClient()
        scorer = make(client, min_content_words=2)
        assert scorer.score_text("Lamp.").source is Source.FILTER
        assert scorer.score_text("The lamp hums.").source is Source.MODEL

    def test_zero_min_content_words_never_filters_text(self):
        client = FakeClient()
        scorer = make(client, min_content_words=0)
        assert scorer.score_text("Yeah, okay.").source is Source.MODEL
        assert scorer.score_text("").source is Source.FILTER          # nothing to ask about
        assert client.calls == ["Yeah, okay."]


class TestIsFiltered:
    def test_filler_and_empty_text_are_filtered(self):
        scorer = make()
        assert scorer.is_filtered("Yeah, okay.") and scorer.is_filtered("") and scorer.is_filtered("   ")

    def test_text_with_content_is_not(self):
        assert not make().is_filtered("The lamp hums.")

    def test_it_follows_min_content_words(self):
        assert make(min_content_words=2).is_filtered("Lamp.")
        assert not make(min_content_words=2).is_filtered("The lamp hums.")
        assert not make(min_content_words=0).is_filtered("Yeah, okay.")
        assert make(min_content_words=0).is_filtered("")

    def test_it_agrees_with_what_scoring_does(self):
        scorer = make()
        for text in ("Yeah, okay.", "The lamp hums.", "", "It is the 40.", "Lamp."):
            assert scorer.is_filtered(text) == (scorer.score_text(text).source is Source.FILTER)

    def test_asking_never_calls_the_model_or_counts(self):
        client = FakeClient()
        scorer = make(client)
        scorer.is_filtered("The lamp hums.")
        scorer.is_filtered("Yeah, okay.")
        assert client.calls == [] and scorer.counts() == {"model": 0, "filter": 0, "fallback": 0}


class TestFallback:
    def scorer_without_model(self, **options):
        return make(FakeClient(None), failure_limit=100, **options)

    def test_no_score_from_the_model_gives_a_fallback_score(self):
        result = self.scorer_without_model().score_text("The lamp hums.")
        assert result.source is Source.FALLBACK
        assert result.score == pytest.approx(DEFAULT_FLOOR + 2 * 0.35)

    def test_more_content_words_score_higher(self):
        scorer = self.scorer_without_model()
        short = scorer.score_text("The lamp hums.").score
        longer = scorer.score_text("The old brass lamp hums softly tonight.").score
        assert longer > short

    def test_names_numbers_and_questions_add_a_little(self):
        scorer = self.scorer_without_model()
        plain = scorer.score_text("My dog met them.").score
        assert scorer.score_text("My dog met Karen.").score == pytest.approx(plain + 0.8)
        assert scorer.score_text("I paid 40 dollars.").score == pytest.approx(DEFAULT_FLOOR + 2 * 0.35 + 0.4)
        assert scorer.score_text("The lamp hums?").score == pytest.approx(DEFAULT_FLOOR + 2 * 0.35 + 0.4)

    def test_only_so_many_content_words_names_and_numbers_count(self):
        scorer = self.scorer_without_model()
        ten = scorer.score_text("alpha beta gamma delta epsilon zeta eta theta iota kappa").score
        twelve = scorer.score_text("alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu").score
        assert twelve == pytest.approx(ten) and ten == pytest.approx(DEFAULT_FLOOR + 10 * 0.35)
        two_names = scorer.score_text("My dog met Karen Bob.").score
        three_names = scorer.score_text("My dog met Karen Bob Alice.").score
        assert three_names == pytest.approx(two_names + 0.35)         # the new word counts, not a third name
        two_numbers = scorer.score_text("Buy lamps 4 5.").score
        three_numbers = scorer.score_text("Buy lamps 4 5 6.").score
        assert three_numbers == pytest.approx(two_numbers)

    def test_the_fallback_is_capped(self):
        text = "Karen Bob saw 4 5 old brass lamps humming softly tonight inside many dusty rooms?"
        assert self.scorer_without_model().score_text(text).score == FALLBACK_CEILING

    def test_the_fallback_still_filters_filler_first(self):
        assert self.scorer_without_model().score_text("Yeah, okay.").source is Source.FILTER

    def test_the_floor_is_the_fallbacks_base(self):
        assert (self.scorer_without_model(floor=2.0).score_text("The lamp hums.").score
                == pytest.approx(2.0 + 0.7))


class TestCircuitBreaker:
    def test_a_few_failures_do_not_open_it(self):
        client = FakeClient(None)
        scorer = make(client)
        for _ in range(DEFAULT_FAILURE_LIMIT - 1):
            scorer.score_text("The lamp hums.")
        assert not scorer.degraded and scorer.report.ok
        scorer.score_text("The lamp hums.")
        assert len(client.calls) == DEFAULT_FAILURE_LIMIT

    def test_enough_failures_in_a_row_open_it_and_the_model_is_left_alone(self):
        client = FakeClient(None)
        scorer = make(client)
        for _ in range(DEFAULT_FAILURE_LIMIT):
            scorer.score_text("The lamp hums.")
        assert scorer.degraded and len(scorer.report.warnings) == 1
        calls = len(client.calls)
        for _ in range(10):
            assert scorer.score_text("The lamp hums.").source is Source.FALLBACK
        assert len(client.calls) == calls

    def test_after_the_wait_one_attempt_is_made(self):
        client, clock = FakeClient(None), Clock()
        scorer = make(client, clock=clock)
        for _ in range(DEFAULT_FAILURE_LIMIT):
            scorer.score_text("The lamp hums.")
        calls = len(client.calls)
        clock.now += DEFAULT_RETRY_SECONDS - 1
        scorer.score_text("The lamp hums.")
        assert len(client.calls) == calls                       # not yet
        clock.now += 1
        scorer.score_text("The lamp hums.")
        assert len(client.calls) == calls + 1                   # now

    def test_a_failed_retry_closes_the_door_again_at_once(self):
        client, clock = FakeClient(None), Clock()
        scorer = make(client, clock=clock)
        for _ in range(DEFAULT_FAILURE_LIMIT):
            scorer.score_text("The lamp hums.")
        clock.now += DEFAULT_RETRY_SECONDS
        scorer.score_text("The lamp hums.")                     # the retry, which fails
        calls = len(client.calls)
        scorer.score_text("The lamp hums.")
        assert len(client.calls) == calls and scorer.degraded

    def test_a_successful_retry_closes_the_breaker(self):
        client, clock = FakeClient(None), Clock()
        scorer = make(client, clock=clock)
        for _ in range(DEFAULT_FAILURE_LIMIT):
            scorer.score_text("The lamp hums.")
        client.value = 6.0
        clock.now += DEFAULT_RETRY_SECONDS
        assert scorer.score_text("The lamp hums.").source is Source.MODEL
        assert not scorer.degraded
        assert scorer.score_text("The lamp hums.").source is Source.MODEL

    def test_recovery_is_announced_once(self, caplog):
        client, clock = FakeClient(None), Clock()
        scorer = make(client, clock=clock)
        for _ in range(DEFAULT_FAILURE_LIMIT):
            scorer.score_text("The lamp hums.")
        client.value = 6.0
        clock.now += DEFAULT_RETRY_SECONDS
        with caplog.at_level(logging.INFO):
            for _ in range(3):
                scorer.score_text("The lamp hums.")
        assert caplog.text.count("answering again") == 1

    def test_the_defaults_are_three_failures_thirty_seconds_floor_one_and_one_content_word(self):
        client, clock = FakeClient(None), Clock()
        scorer = make(client, clock=clock)
        assert scorer.score_text("Yeah, okay.").score == 1.0                    # the floor
        assert scorer.score_text("Lamp.").source is Source.FALLBACK             # one content word is enough
        scorer.score_text("Lamp.")
        assert not scorer.degraded                                              # two failures: still closed
        scorer.score_text("Lamp.")
        assert scorer.degraded                                                  # the third opens it
        clock.now += 29
        assert scorer.degraded
        clock.now += 1
        assert not scorer.degraded                                              # thirty seconds later

    def test_a_success_resets_the_count_of_failures(self):
        client = FakeClient(None)
        scorer = make(client)
        for _ in range(DEFAULT_FAILURE_LIMIT - 1):
            scorer.score_text("The lamp hums.")
        client.value = 5.0
        scorer.score_text("The lamp hums.")
        client.value = None
        for _ in range(DEFAULT_FAILURE_LIMIT - 1):
            scorer.score_text("The lamp hums.")
        assert not scorer.degraded

    def test_the_limit_and_the_wait_are_settings(self):
        client, clock = FakeClient(None), Clock()
        scorer = make(client, clock=clock, failure_limit=1, retry_seconds=5)
        scorer.score_text("The lamp hums.")
        assert scorer.degraded
        clock.now += 5
        assert not scorer.degraded

    def test_filtered_fragments_do_not_count_as_failures(self):
        scorer = make(FakeClient(None), failure_limit=1)
        for _ in range(5):
            scorer.score_text("Yeah, okay.")
        assert not scorer.degraded


class TestUnavailableModel:
    def test_the_model_is_never_asked_and_everything_gets_a_fallback(self):
        client = FakeClient(available=False)
        scorer = make(client)
        assert scorer.score_text("The lamp hums.").source is Source.FALLBACK
        assert client.calls == [] and scorer.degraded
        assert len(scorer.report.warnings) == 1                 # said once, at construction

    def test_warm_up_is_false_and_does_not_call_the_client(self):
        client = FakeClient(available=False)
        assert make(client).warm_up() is False and client.warmed == 0


class TestWarmUp:
    def test_warm_up_passes_through(self):
        client = FakeClient()
        assert make(client).warm_up() is True and client.warmed == 1

    def test_a_failed_warm_up_is_false(self):
        assert make(FakeClient(None)).warm_up() is False


class TestSettings:
    @pytest.mark.parametrize("name,bad", [("floor", -1), ("floor", "x"), ("floor", True), ("floor", None),
                                          ("min_content_words", -1), ("min_content_words", 1.5),
                                          ("min_content_words", True), ("failure_limit", 0),
                                          ("failure_limit", 2.5), ("retry_seconds", -5), ("retry_seconds", "soon")])
    def test_bad_settings_fall_back_with_a_warning(self, name, bad):
        scorer = make(**{name: bad})
        assert len(scorer.report.warnings) == 1
        assert scorer.score_text("The lamp hums.").source is Source.MODEL     # still works

    def test_defaults_are_quiet(self):
        assert make().report.ok

    def test_strict_mode_raises_on_a_bad_setting(self):
        with pytest.raises(ScorerError):
            make(failure_limit=0, strict=True)

    def test_strict_mode_raises_when_the_breaker_opens(self):
        scorer = make(FakeClient(None), failure_limit=1, strict=True)
        with pytest.raises(ScorerError):
            scorer.score_text("The lamp hums.")


class TestPrivacy:
    def test_nothing_said_reaches_a_report_or_a_log(self, caplog):
        secret = "the funeral was held on a rainy tuesday"
        scorer = make(FakeClient(None), failure_limit=1)
        with caplog.at_level(logging.DEBUG):
            scorer.score_text(secret)
            make(FakeClient(5.0)).score_text(secret)
        assert all("funeral" not in message for message in scorer.report.warnings)
        assert "funeral" not in caplog.text and "rainy" not in caplog.text
