"""Tests for pom.hinge.interest_client: scoring one sentence through a fake llama-server."""
import logging
import math

import pytest

from pom.hinge.interest_client import (DEFAULT_INTERPRET_TIMEOUT, DEFAULT_TIMEOUT, INTERPRET_MAX_TOKENS,
                                       USER_TEMPLATE, WARM_UP_SENTENCE, InterestClient, InterestClientError,
                                       Interpretation, expected_digit, parse_written_score)

URL = "http://127.0.0.1:8098/v1/chat/completions"
PROMPT = "You score how interesting a sentence is."


def reply(*candidates):
    """A llama-server style reply whose first token has these (token, probability) candidates."""
    return {"choices": [{"logprobs": {"content": [{"top_logprobs": [
        {"token": token, "logprob": math.log(p)} for token, p in candidates]}]}}]}


class FakePost:
    """Records every request and returns canned replies (or raises)."""

    def __init__(self, result=None):
        self.result = result if result is not None else reply(("7", 1.0))
        self.calls = []

    def __call__(self, url, body, timeout):
        self.calls.append((url, body, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def client(post=None, **options):
    options.setdefault("strict", False)
    return InterestClient(URL, PROMPT, post=post or FakePost(), **options)


class TestExpectedDigit:
    def test_a_certain_digit_is_that_digit(self):
        assert expected_digit(reply(("7", 1.0))) == pytest.approx(7.0)

    def test_probabilities_weight_the_digits(self):
        assert expected_digit(reply(("2", 0.5), ("8", 0.5))) == pytest.approx(5.0)

    def test_unequal_weights(self):
        assert expected_digit(reply(("2", 0.75), ("6", 0.25))) == pytest.approx(3.0)

    def test_weights_are_normalised_over_the_digits_found(self):
        assert expected_digit(reply(("4", 0.2), ("8", 0.2), ("The", 0.6))) == pytest.approx(6.0)

    def test_whitespace_around_a_digit_is_ignored(self):
        assert expected_digit(reply((" 5", 0.5), ("5 ", 0.5))) == pytest.approx(5.0)

    def test_the_same_digit_in_two_spellings_adds_up(self):
        assert expected_digit(reply(("3", 0.25), (" 3", 0.25), ("9", 0.5))) == pytest.approx(6.0)

    def test_zero_and_longer_numbers_are_not_digits(self):
        assert expected_digit(reply(("0", 0.5), ("10", 0.4), ("5", 0.1))) == pytest.approx(5.0)

    def test_no_digit_gives_none(self):
        assert expected_digit(reply(("The", 0.9), ("Sure", 0.1))) is None

    @pytest.mark.parametrize("bad", [None, {}, [], "text", {"choices": []},
                                     {"choices": [{}]}, {"choices": [{"logprobs": None}]},
                                     {"choices": [{"logprobs": {"content": []}}]},
                                     {"choices": [{"logprobs": {"content": [{"top_logprobs": [{"token": 5}]}]}}]},
                                     {"choices": [{"logprobs": {"content": [{"top_logprobs": [{"token": "5", "logprob": "x"}]}]}}]}])
    def test_malformed_replies_give_none(self, bad):
        assert expected_digit(bad) is None


class TestScore:
    def test_returns_the_expected_digit(self):
        assert client(FakePost(reply(("4", 0.5), ("6", 0.5)))).score("a sentence") == pytest.approx(5.0)

    def test_request_is_built_as_the_experiment_built_it(self):
        post = FakePost()
        client(post, timeout=9.0).score("  The lamp hums.  ")
        url, body, timeout = post.calls[0]
        assert url == URL and timeout == 9.0
        assert body["messages"] == [{"role": "system", "content": PROMPT},
                                    {"role": "user", "content": USER_TEMPLATE.format(sentence="The lamp hums.")}]
        assert USER_TEMPLATE == "Rate this line: {sentence}"
        assert body["max_tokens"] == 1 and body["temperature"] == 0
        assert body["logprobs"] is True and body["top_logprobs"] == 20
        assert body["chat_template_kwargs"] == {"enable_thinking": False}

    def test_one_request_per_sentence_with_no_other_text(self):
        post = FakePost()
        c = client(post)
        c.score("first sentence")
        c.score("second sentence")
        sent = [call[1]["messages"][1]["content"] for call in post.calls]
        assert len(post.calls) == 2
        assert "second" not in sent[0] and "first" not in sent[1]

    def test_records_how_long_the_request_took(self):
        c = client()
        assert c.last_seconds is None
        c.score("anything")
        assert c.last_seconds is not None and c.last_seconds >= 0

    @pytest.mark.parametrize("text", ["", "   ", None, 42])
    def test_empty_or_non_text_is_not_sent(self, text):
        post = FakePost()
        assert client(post).score(text) is None
        assert post.calls == []

    def test_prompt_is_stripped_but_otherwise_sent_whole(self):
        post = FakePost()
        InterestClient(URL, "\n  Line one.\nLine two.\n\n", post=post).score("x")
        assert post.calls[0][1]["messages"][0]["content"] == "Line one.\nLine two."


class TestRecovery:
    def test_a_server_error_gives_none_and_a_warning(self):
        c = client(FakePost(ConnectionRefusedError("refused")))
        assert c.score("hello there") is None
        assert len(c.report.warnings) == 1 and c.last_seconds is not None

    def test_any_exception_is_caught(self):
        for error in (TimeoutError(), OSError("x"), ValueError("x"), RuntimeError("x")):
            assert client(FakePost(error)).score("hello") is None

    def test_a_reply_without_a_digit_gives_none_and_a_warning(self):
        c = client(FakePost(reply(("Sorry", 1.0))))
        assert c.score("hello") is None
        assert len(c.report.warnings) == 1

    def test_the_client_keeps_working_after_a_failure(self):
        post = FakePost(OSError("down"))
        c = client(post)
        assert c.score("one") is None
        post.result = reply(("3", 1.0))
        assert c.score("two") == pytest.approx(3.0)

    def test_strict_mode_raises(self):
        with pytest.raises(InterestClientError):
            client(FakePost(OSError("down")), strict=True).score("hello")
        with pytest.raises(InterestClientError):
            client(FakePost(reply(("Sorry", 1.0))), strict=True).score("hello")

    def test_nothing_said_in_the_room_reaches_a_report_or_log(self, caplog):
        secret = "my secret sentence about the funeral"
        c = client(FakePost(OSError(f"failed on {secret}")))
        with caplog.at_level(logging.DEBUG):
            c.score(secret)
            client(FakePost()).score(secret)
        assert all(secret not in message for message in c.report.warnings)
        assert secret not in caplog.text and "funeral" not in caplog.text


class TestSetup:
    @pytest.mark.parametrize("url", ["", "ftp://x", "file:///etc/passwd", "127.0.0.1:8098", None, 8098])
    def test_only_http_addresses_are_accepted(self, url):
        post = FakePost()
        c = InterestClient(url, PROMPT, post=post)
        assert not c.available and len(c.report.warnings) == 1
        assert c.score("hello") is None and post.calls == []

    def test_https_is_accepted(self):
        assert InterestClient("https://example.test/v1/chat/completions", PROMPT, post=FakePost()).available

    @pytest.mark.parametrize("prompt", ["", "   \n", None])
    def test_no_prompt_means_the_model_is_never_asked(self, prompt):
        post = FakePost()
        c = InterestClient(URL, prompt, post=post)
        assert not c.available and len(c.report.warnings) == 1
        assert c.score("hello") is None and post.calls == []

    def test_a_good_setup_is_available_and_quiet(self):
        c = client()
        assert c.available and c.report.ok

    @pytest.mark.parametrize("timeout", [0, -1, "slow", None, True])
    def test_a_bad_timeout_falls_back_to_the_default(self, timeout):
        post = FakePost()
        c = InterestClient(URL, PROMPT, timeout=timeout, post=post)
        c.score("hello")
        assert post.calls[0][2] == DEFAULT_TIMEOUT
        assert len(c.report.warnings) == 1

    def test_strict_mode_raises_on_a_bad_setup(self):
        with pytest.raises(InterestClientError):
            InterestClient("nonsense", PROMPT, post=FakePost(), strict=True)
        with pytest.raises(InterestClientError):
            InterestClient(URL, "", post=FakePost(), strict=True)


class TestWarmUp:
    def test_warm_up_sends_one_throwaway_sentence(self):
        post = FakePost()
        assert client(post).warm_up() is True
        assert len(post.calls) == 1
        assert post.calls[0][1]["messages"][1]["content"] == USER_TEMPLATE.format(sentence=WARM_UP_SENTENCE)

    def test_warm_up_reports_failure_without_raising(self):
        assert client(FakePost(OSError("down"))).warm_up() is False


# --- The debug view's request ----------------------------------------------------------

DEBUG_PROMPT = "You score a sentence and say why."


def written(text, first_token=None):
    """A llama-server style reply that holds written text, and (if given) the first token's candidates."""
    choice = {"message": {"role": "assistant", "content": text}}
    if first_token is not None:
        choice["logprobs"] = {"content": [{"top_logprobs": [
            {"token": token, "logprob": math.log(p)} for token, p in first_token]}]}
    return {"choices": [choice]}


def interpreter(text="7.89, a vivid image, a death", **options):
    post = FakePost(written(text))
    return InterestClient(URL, PROMPT, post=post, debug_prompt=DEBUG_PROMPT, **options), post


class TestInterpret:
    def test_returns_the_written_reply(self):
        c, _ = interpreter("  7.89, a vivid image, a death \n")
        assert c.interpret("The lamp hums.").text == "7.89, a vivid image, a death"

    def test_the_score_comes_from_the_same_run_as_the_words(self):
        c, post = interpreter()
        post.result = written("6.00 | The lamp hums. | image", first_token=[("5", 0.5), ("7", 0.5)])
        result = c.interpret("The lamp hums.")
        assert result == Interpretation("6.00 | The lamp hums. | image", pytest.approx(6.0))
        assert len(post.calls) == 1                          # one request gave both

    def test_with_no_probabilities_in_the_reply_there_is_text_but_no_score(self):
        c, _ = interpreter("6.00 | words")
        assert c.interpret("The lamp hums.") == Interpretation("6.00 | words", None)

    def test_a_first_token_that_is_not_a_digit_gives_text_but_no_score(self):
        c, post = interpreter()
        post.result = written("The sentence is dull.", first_token=[("The", 0.9), ("This", 0.1)])
        assert c.interpret("hello there").score is None

    def test_request_uses_the_debug_prompt_and_asks_for_words(self):
        c, post = interpreter(interpret_timeout=33.0)
        c.interpret("  The lamp hums.  ")
        url, body, timeout = post.calls[0]
        assert url == URL and timeout == 33.0
        assert body["messages"] == [{"role": "system", "content": DEBUG_PROMPT},
                                    {"role": "user", "content": USER_TEMPLATE.format(sentence="The lamp hums.")}]
        assert body["max_tokens"] == INTERPRET_MAX_TOKENS and body["temperature"] == 0
        assert body["logprobs"] is True and body["top_logprobs"] == 20          # for the score from the same run
        assert body["chat_template_kwargs"] == {"enable_thinking": False}

    def test_the_debug_prompt_is_stripped_but_otherwise_sent_whole(self):
        post = FakePost(written("7.0"))
        InterestClient(URL, PROMPT, post=post, debug_prompt="\n  Line one.\nLine two.\n\n").interpret("hello there")
        assert post.calls[0][1]["messages"][0]["content"] == "Line one.\nLine two."

    def test_the_scoring_prompt_is_not_sent(self):
        c, post = interpreter()
        c.interpret("hello there")
        assert PROMPT not in post.calls[0][1]["messages"][0]["content"]

    def test_the_wait_is_longer_than_for_a_score(self):
        assert DEFAULT_INTERPRET_TIMEOUT > DEFAULT_TIMEOUT
        c, post = interpreter()
        c.interpret("hello there")
        assert post.calls[0][2] == DEFAULT_INTERPRET_TIMEOUT

    def test_without_a_debug_prompt_it_does_nothing(self):
        post = FakePost(written("7.0"))
        c = InterestClient(URL, PROMPT, post=post)
        assert not c.can_interpret and c.interpret("hello there") is None and post.calls == []

    def test_can_interpret_needs_a_usable_client_and_a_debug_prompt(self):
        assert interpreter()[0].can_interpret
        assert not InterestClient("nonsense", PROMPT, post=FakePost(), debug_prompt=DEBUG_PROMPT).can_interpret

    @pytest.mark.parametrize("text", ["", "   ", None, 7])
    def test_empty_or_non_text_is_not_sent(self, text):
        c, post = interpreter()
        assert c.interpret(text) is None and post.calls == []

    def test_a_failure_gives_none_and_a_warning(self):
        c = InterestClient(URL, PROMPT, post=FakePost(TimeoutError()), debug_prompt=DEBUG_PROMPT)
        assert c.interpret("hello there") is None and len(c.report.warnings) == 1
        assert c.last_seconds is not None

    @pytest.mark.parametrize("bad", [None, {}, {"choices": []}, {"choices": [{}]}, {"choices": [{"message": {}}]},
                                     {"choices": [{"message": {"content": None}}]}, written("   ")])
    def test_a_reply_without_text_gives_none(self, bad):
        c, _ = interpreter()
        c._post = FakePost(bad)
        assert c.interpret("hello there") is None

    def test_strict_mode_raises_on_a_failure(self):
        c = InterestClient(URL, PROMPT, post=FakePost(OSError("down")), debug_prompt=DEBUG_PROMPT, strict=True)
        with pytest.raises(InterestClientError):
            c.interpret("hello there")

    def test_a_bad_interpret_timeout_falls_back_with_a_warning(self):
        for bad in (0, -5, "slow", None, True):
            c, post = interpreter(interpret_timeout=bad)
            c.interpret("hello there")
            assert post.calls[0][2] == DEFAULT_INTERPRET_TIMEOUT and len(c.report.warnings) == 1

    def test_what_was_said_never_reaches_a_report_or_a_log(self, caplog):
        secret = "my secret sentence about the funeral"
        c = InterestClient(URL, PROMPT, post=FakePost(OSError(f"failed on {secret}")), debug_prompt=DEBUG_PROMPT)
        with caplog.at_level(logging.DEBUG):
            c.interpret(secret)
            interpreter("7.0, a funeral")[0].interpret(secret)
        assert all("funeral" not in w for w in c.report.warnings)
        assert "funeral" not in caplog.text


class TestParseWrittenScore:
    @pytest.mark.parametrize("text,expected", [("7.89, a vivid image", 7.89), ("  3", 3.0), ("1.00", 1.0),
                                               ("9.99, extremely", 9.99), ("5.5 because", 5.5)])
    def test_the_leading_number_is_read(self, text, expected):
        assert parse_written_score(text) == pytest.approx(expected)

    @pytest.mark.parametrize("text", ["", "a vivid image, 7.89", "Score: 7", "0.5", "10", "12.3, odd", None, 7, "-4"])
    def test_anything_else_is_none(self, text):
        assert parse_written_score(text) is None
