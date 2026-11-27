"""Tests for pom.scoring.scorer_model_client: scoring one clip through a fake llama-server."""
import math

import pytest

from pom.defaults import ScorerSettings
from pom.scoring.scorer_model_client import (DEFAULT_EXPLAIN_TIMEOUT, EXPLAIN_MAX_TOKENS,
                                       USER_TEMPLATE, WARM_UP_CLIP, ScorerModelClient, ScorerModelClientError,
                                       Explanation, score_from_scorecard, parse_written_score)

URL = "http://127.0.0.1:8098/v1/chat/completions"
PROMPT = "You score how interesting a clip is."
SCORER = ScorerSettings(url=URL, prompt_scoring=PROMPT)


def reply(*candidates):
    """A llama-server style reply whose first token has these (token, probability) candidates."""
    return {"choices": [{"logprobs": {"content": [{"top_logprobs": [
        {"token": token, "logprob": math.log(p)} for token, p in candidates]}]}}]}


class FakeComms:
    """A ModelComms that records every request and returns canned replies (or raises)."""

    def __init__(self, result=None):
        self.result = result if result is not None else reply(("7", 1.0))
        self.address = URL
        self.calls = []

    def post_json(self, body, timeout_seconds, path=""):
        self.calls.append((self.address, body, timeout_seconds))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def client(comms=None, timeout=None, **options):
    options.setdefault("strict", False)
    settings = SCORER if timeout is None else ScorerSettings(url=URL, prompt_scoring=PROMPT, timeout=timeout)
    return ScorerModelClient(settings, comms=comms or FakeComms(), **options)


class TestExpectedDigit:
    def test_a_certain_digit_is_that_digit(self):
        assert score_from_scorecard(reply(("7", 1.0))) == pytest.approx(7.0)

    def test_probabilities_weight_the_digits(self):
        assert score_from_scorecard(reply(("2", 0.5), ("8", 0.5))) == pytest.approx(5.0)

    def test_unequal_weights(self):
        assert score_from_scorecard(reply(("2", 0.75), ("6", 0.25))) == pytest.approx(3.0)

    def test_weights_are_normalised_over_the_digits_found(self):
        assert score_from_scorecard(reply(("4", 0.2), ("8", 0.2), ("The", 0.6))) == pytest.approx(6.0)

    def test_whitespace_around_a_digit_is_ignored(self):
        assert score_from_scorecard(reply((" 5", 0.5), ("5 ", 0.5))) == pytest.approx(5.0)

    def test_the_same_digit_in_two_spellings_adds_up(self):
        assert score_from_scorecard(reply(("3", 0.25), (" 3", 0.25), ("9", 0.5))) == pytest.approx(6.0)

    def test_zero_counts_as_a_digit(self):
        assert score_from_scorecard(reply(("0", 0.5), ("8", 0.5))) == pytest.approx(4.0)
        assert score_from_scorecard(reply(("0", 1.0))) == pytest.approx(0.0)

    def test_longer_numbers_are_not_digits(self):
        assert score_from_scorecard(reply(("10", 0.4), ("85", 0.4), ("5", 0.2))) == pytest.approx(5.0)

    def test_two_digit_tokens_that_are_inside_the_digit_string_are_not_digits(self):
        assert score_from_scorecard(reply(("01", 0.3), ("12", 0.3), ("89", 0.2), ("5", 0.2))) == pytest.approx(5.0)

    def test_no_digit_gives_none(self):
        assert score_from_scorecard(reply(("The", 0.9), ("Sure", 0.1))) is None

    @pytest.mark.parametrize("bad", [None, {}, [], "text", {"choices": []},
                                     {"choices": [{}]}, {"choices": [{"logprobs": None}]},
                                     {"choices": [{"logprobs": {"content": []}}]},
                                     {"choices": [{"logprobs": {"content": [{"top_logprobs": [{"token": 5}]}]}}]},
                                     {"choices": [{"logprobs": {"content": [{"top_logprobs": [{"token": "5", "logprob": "x"}]}]}}]}])
    def test_malformed_replies_give_none(self, bad):
        assert score_from_scorecard(bad) is None


class TestScore:
    def test_returns_the_score_from_the_probabilities(self):
        assert client(FakeComms(reply(("4", 0.5), ("6", 0.5)))).score("a clip") == pytest.approx(5.0)

    def test_request_is_built_as_the_experiment_built_it(self):
        comms = FakeComms()
        client(comms, timeout=9.0).score("  The lamp hums.  ")
        url, body, timeout = comms.calls[0]
        assert url == URL and timeout == 9.0
        assert body["messages"] == [{"role": "system", "content": PROMPT},
                                    {"role": "user", "content": USER_TEMPLATE.format(clip_text="The lamp hums.")}]
        assert USER_TEMPLATE == "Rate this line: {clip_text}"
        assert body["max_tokens"] == 1 and body["temperature"] == 0
        assert body["logprobs"] is True and body["top_logprobs"] == 20
        assert body["chat_template_kwargs"] == {"enable_thinking": False}

    def test_one_request_per_sentence_with_no_other_text(self):
        comms = FakeComms()
        c = client(comms)
        c.score("first clip")
        c.score("second clip")
        sent = [call[1]["messages"][1]["content"] for call in comms.calls]
        assert len(comms.calls) == 2
        assert "second" not in sent[0] and "first" not in sent[1]

    def test_records_how_long_the_request_took(self):
        c = client()
        assert c.last_seconds is None
        c.score("anything")
        assert c.last_seconds is not None and c.last_seconds >= 0

    @pytest.mark.parametrize("text", ["", "   ", None, 42])
    def test_empty_or_non_text_is_not_sent(self, text):
        comms = FakeComms()
        assert client(comms).score(text) is None
        assert comms.calls == []

    def test_prompt_is_stripped_but_otherwise_sent_whole(self):
        comms = FakeComms()
        ScorerModelClient(ScorerSettings(url=URL, prompt_scoring="\n  Line one.\nLine two.\n\n"), comms=comms).score("x")
        assert comms.calls[0][1]["messages"][0]["content"] == "Line one.\nLine two."


class TestRecovery:
    def test_a_server_error_gives_none_and_a_warning(self, logged):
        c = client(FakeComms(ConnectionRefusedError("refused")))
        assert c.score("hello there") is None
        assert len(logged.of(c).warnings()) == 1 and c.last_seconds is not None

    def test_any_exception_is_caught(self):
        for error in (TimeoutError(), OSError("x"), ValueError("x"), RuntimeError("x")):
            assert client(FakeComms(error)).score("hello") is None

    def test_a_reply_without_a_digit_gives_none_and_a_warning(self, logged):
        c = client(FakeComms(reply(("Sorry", 1.0))))
        assert c.score("hello") is None
        assert len(logged.of(c).warnings()) == 1

    def test_the_client_keeps_working_after_a_failure(self):
        comms = FakeComms(OSError("down"))
        c = client(comms)
        assert c.score("one") is None
        comms.result = reply(("3", 1.0))
        assert c.score("two") == pytest.approx(3.0)

    def test_strict_mode_raises(self):
        with pytest.raises(ScorerModelClientError):
            client(FakeComms(OSError("down")), strict=True).score("hello")
        with pytest.raises(ScorerModelClientError):
            client(FakeComms(reply(("Sorry", 1.0))), strict=True).score("hello")

    def test_nothing_said_in_the_room_reaches_a_report_or_log(self, logged):
        secret = "my secret clip about the funeral"
        c = client(FakeComms(OSError(f"failed on {secret}")))
        c.score(secret)
        client(FakeComms()).score(secret)
        assert all(secret not in message for message in logged.of(c).warnings())
        assert secret not in logged.text() and "funeral" not in logged.text()


class TestSetup:
    def test_a_client_must_be_handed_its_comms(self):
        """The entry point builds one ModelComms per model and hands it in; a client never builds its own."""
        with pytest.raises(TypeError):
            ScorerModelClient(SCORER)

    def test_every_request_goes_through_the_comms_it_was_handed(self):
        comms = FakeComms()
        c = client(comms)
        c.score("hello")
        assert c.comms is comms and len(comms.calls) == 1

    def test_https_is_accepted(self):
        assert ScorerModelClient(ScorerSettings(url="https://example.test/v1/chat/completions", prompt_scoring=PROMPT), comms=FakeComms()).available

    @pytest.mark.parametrize("prompt", ["", "   \n"])
    def test_no_prompt_means_the_model_is_never_asked(self, prompt, logged):
        comms = FakeComms()
        c = ScorerModelClient(ScorerSettings(url=URL, prompt_scoring=prompt), comms=comms)
        assert not c.available and len(logged.of(c).warnings()) == 1
        assert c.score("hello") is None and comms.calls == []

    def test_a_good_setup_is_available_and_quiet(self, logged):
        c = client()
        assert c.available and logged.of(c).ok()

    def test_strict_mode_raises_on_a_bad_setup(self):
        with pytest.raises(ScorerModelClientError):
            ScorerModelClient(ScorerSettings(url=URL, prompt_scoring=""), comms=FakeComms(), strict=True)


class TestWarmUp:
    def test_warm_up_sends_one_throwaway_sentence(self):
        comms = FakeComms()
        assert client(comms).warm_up() is True
        assert len(comms.calls) == 1
        assert comms.calls[0][1]["messages"][1]["content"] == USER_TEMPLATE.format(clip_text=WARM_UP_CLIP)

    def test_warm_up_reports_failure_without_raising(self):
        assert client(FakeComms(OSError("down"))).warm_up() is False


# --- The debug view's request ----------------------------------------------------------

EXPLAIN_PROMPT = "You score a clip and say why."


def written(text, first_token=None):
    """A llama-server style reply that holds written text, and (if given) the first token's candidates."""
    choice = {"message": {"role": "assistant", "content": text}}
    if first_token is not None:
        choice["logprobs"] = {"content": [{"top_logprobs": [
            {"token": token, "logprob": math.log(p)} for token, p in first_token]}]}
    return {"choices": [choice]}


def explainer(text="7.89, a vivid image, a death", **options):
    comms = FakeComms(written(text))
    return ScorerModelClient(SCORER, comms=comms, explain_prompt=EXPLAIN_PROMPT, **options), comms


class TestExplain:
    def test_returns_the_written_reply(self):
        c, _ = explainer("  7.89, a vivid image, a death \n")
        assert c.explain("The lamp hums.").text == "7.89, a vivid image, a death"

    def test_the_score_comes_from_the_same_run_as_the_words(self):
        c, comms = explainer()
        comms.result = written("6.00 | The lamp hums. | image", first_token=[("5", 0.5), ("7", 0.5)])
        result = c.explain("The lamp hums.")
        assert result == Explanation("6.00 | The lamp hums. | image", pytest.approx(6.0))
        assert len(comms.calls) == 1                          # one request gave both

    def test_with_no_probabilities_in_the_reply_there_is_text_but_no_score(self):
        c, _ = explainer("6.00 | words")
        assert c.explain("The lamp hums.") == Explanation("6.00 | words", None)

    def test_a_first_token_that_is_not_a_digit_gives_text_but_no_score(self):
        c, comms = explainer()
        comms.result = written("The clip is dull.", first_token=[("The", 0.9), ("This", 0.1)])
        assert c.explain("hello there").score is None

    def test_request_uses_the_explain_prompt_and_asks_for_words(self):
        c, comms = explainer(explain_timeout=33.0)
        c.explain("  The lamp hums.  ")
        url, body, timeout = comms.calls[0]
        assert url == URL and timeout == 33.0
        assert body["messages"] == [{"role": "system", "content": EXPLAIN_PROMPT},
                                    {"role": "user", "content": USER_TEMPLATE.format(clip_text="The lamp hums.")}]
        assert body["max_tokens"] == EXPLAIN_MAX_TOKENS and body["temperature"] == 0
        assert body["logprobs"] is True and body["top_logprobs"] == 20          # for the score from the same run
        assert body["chat_template_kwargs"] == {"enable_thinking": False}

    def test_the_explain_prompt_is_stripped_but_otherwise_sent_whole(self):
        comms = FakeComms(written("7.0"))
        ScorerModelClient(SCORER, comms=comms, explain_prompt="\n  Line one.\nLine two.\n\n").explain("hello there")
        assert comms.calls[0][1]["messages"][0]["content"] == "Line one.\nLine two."

    def test_the_scoring_prompt_is_not_sent(self):
        c, comms = explainer()
        c.explain("hello there")
        assert PROMPT not in comms.calls[0][1]["messages"][0]["content"]

    def test_the_wait_is_longer_than_for_a_score(self):
        assert DEFAULT_EXPLAIN_TIMEOUT > SCORER.timeout
        c, comms = explainer()
        c.explain("hello there")
        assert comms.calls[0][2] == DEFAULT_EXPLAIN_TIMEOUT

    def test_without_a_explain_prompt_it_does_nothing(self):
        comms = FakeComms(written("7.0"))
        c = ScorerModelClient(SCORER, comms=comms)
        assert not c.can_explain and c.explain("hello there") is None and comms.calls == []

    def test_can_explain_with_a_usable_client_and_a_explain_prompt(self):
        assert explainer()[0].can_explain

    @pytest.mark.parametrize("text", ["", "   ", None, 7])
    def test_empty_or_non_text_is_not_sent(self, text):
        c, comms = explainer()
        assert c.explain(text) is None and comms.calls == []

    def test_a_failure_gives_none_and_a_warning(self, logged):
        c = ScorerModelClient(SCORER, comms=FakeComms(TimeoutError()), explain_prompt=EXPLAIN_PROMPT)
        assert c.explain("hello there") is None and len(logged.of(c).warnings()) == 1
        assert c.last_seconds is not None

    @pytest.mark.parametrize("bad", [None, {}, {"choices": []}, {"choices": [{}]}, {"choices": [{"message": {}}]},
                                     {"choices": [{"message": {"content": None}}]}, written("   ")])
    def test_a_reply_without_text_gives_none(self, bad):
        c, _ = explainer()
        c.comms = FakeComms(bad)
        assert c.explain("hello there") is None

    def test_strict_mode_raises_on_a_failure(self):
        c = ScorerModelClient(SCORER, comms=FakeComms(OSError("down")), explain_prompt=EXPLAIN_PROMPT, strict=True)
        with pytest.raises(ScorerModelClientError):
            c.explain("hello there")

    def test_a_bad_explain_timeout_falls_back_with_a_warning(self, logged):
        for count, bad in enumerate((0, -5, "slow", None, True), start=1):
            c, comms = explainer(explain_timeout=bad)
            c.explain("hello there")
            assert comms.calls[0][2] == DEFAULT_EXPLAIN_TIMEOUT and len(logged.of(c).warnings()) == count     # one more warning for each bad value

    def test_what_was_said_never_reaches_a_report_or_a_log(self, logged):
        secret = "my secret clip about the funeral"
        c = ScorerModelClient(SCORER, comms=FakeComms(OSError(f"failed on {secret}")), explain_prompt=EXPLAIN_PROMPT)
        c.explain(secret)
        explainer("7.0, a funeral")[0].explain(secret)
        assert all("funeral" not in w for w in logged.of(c).warnings())
        assert "funeral" not in logged.text()


class TestParseWrittenScore:
    @pytest.mark.parametrize("text,expected", [("7 | a vivid image | death", 7.0), ("  3", 3.0), ("0 | nothing", 0.0),
                                               ("9, extremely", 9.0), ("5 because", 5.0)])
    def test_the_leading_digit_is_read(self, text, expected):
        assert parse_written_score(text) == pytest.approx(expected)

    @pytest.mark.parametrize("text", ["", "a vivid image, 7", "Score: 7", "7.89, a vivid image", "7.", "10", "85 | two digits",
                                      "12, odd", None, 7, "-4"])
    def test_anything_else_is_none(self, text):
        assert parse_written_score(text) is None
