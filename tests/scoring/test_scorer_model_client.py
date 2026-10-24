"""Tests for pom.scoring.scorer_model_client: scoring one fragment through a fake llama-server."""
import logging

import pytest

from pom.scoring.scorer_model_client import (DEFAULT_EXPLAIN_TIMEOUT, DEFAULT_TIMEOUT, EXPLAIN_MAX_TOKENS,
                                       USER_TEMPLATE, WARM_UP_FRAGMENT, ScorerModelClient, ScorerModelClientError,
                                       Explanation, parse_written_score)

URL = "http://127.0.0.1:8098/v1/chat/completions"
PROMPT = "You score how interesting a fragment is."


def reply(text):
    """A llama-server style reply that holds this text."""
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


NOT_GIVEN = object()      # so a test can hand FakePost None as a (bad) reply


class FakePost:
    """Records every request and returns canned replies (or raises)."""

    def __init__(self, result=NOT_GIVEN):
        self.result = reply("7") if result is NOT_GIVEN else result
        self.calls = []

    def __call__(self, url, body, timeout):
        self.calls.append((url, body, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def client(post=None, **options):
    options.setdefault("strict", False)
    return ScorerModelClient(URL, PROMPT, post=post or FakePost(), **options)


class TestReadingTheDigit:
    @pytest.mark.parametrize("text,expected", [("7", 7.0), ("0", 0.0), ("9", 9.0), (" 5", 5.0), ("5\n", 5.0), ("7.", 7.0)])
    def test_the_digit_the_model_wrote_is_the_score(self, text, expected):
        assert client(FakePost(reply(text))).score("a fragment") == pytest.approx(expected)

    @pytest.mark.parametrize("text", ["7.5", "7.89", "85", "10", "01", "Sorry", "The fragment is dull.", "-4", "", "   "])
    def test_anything_but_a_single_digit_is_not_a_score(self, text):
        c = client(FakePost(reply(text)))
        assert c.score("a fragment") is None and len(c.report.warnings) == 1

    @pytest.mark.parametrize("bad", [None, {}, [], "text", {"choices": []}, {"choices": [{}]}, {"choices": [{"message": {}}]},
                                     {"choices": [{"message": {"content": None}}]}, {"choices": [{"message": {"content": 7}}]}])
    def test_malformed_replies_are_not_a_score(self, bad):
        c = client(FakePost(bad))
        assert c.score("a fragment") is None and len(c.report.warnings) == 1


class TestScore:
    def test_request_is_built_as_the_experiment_built_it(self):
        post = FakePost()
        client(post, timeout=9.0).score("  The lamp hums.  ")
        url, body, timeout = post.calls[0]
        assert url == URL and timeout == 9.0
        assert body["messages"] == [{"role": "system", "content": PROMPT},
                                    {"role": "user", "content": USER_TEMPLATE.format(fragment_text="The lamp hums.")}]
        assert USER_TEMPLATE == "Rate this line: {fragment_text}"
        assert body["max_tokens"] == 1 and body["temperature"] == 0
        assert "logprobs" not in body and "top_logprobs" not in body      # the digit is read from the reply text
        assert body["chat_template_kwargs"] == {"enable_thinking": False}

    def test_one_request_per_sentence_with_no_other_text(self):
        post = FakePost()
        c = client(post)
        c.score("first fragment")
        c.score("second fragment")
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
        ScorerModelClient(URL, "\n  Line one.\nLine two.\n\n", post=post).score("x")
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
        c = client(FakePost(reply("Sorry")))
        assert c.score("hello") is None
        assert len(c.report.warnings) == 1

    def test_the_client_keeps_working_after_a_failure(self):
        post = FakePost(OSError("down"))
        c = client(post)
        assert c.score("one") is None
        post.result = reply("3")
        assert c.score("two") == pytest.approx(3.0)

    def test_strict_mode_raises(self):
        with pytest.raises(ScorerModelClientError):
            client(FakePost(OSError("down")), strict=True).score("hello")
        with pytest.raises(ScorerModelClientError):
            client(FakePost(reply("Sorry")), strict=True).score("hello")

    def test_nothing_said_in_the_room_reaches_a_report_or_log(self, caplog):
        secret = "my secret fragment about the funeral"
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
        c = ScorerModelClient(url, PROMPT, post=post)
        assert not c.available and len(c.report.warnings) == 1
        assert c.score("hello") is None and post.calls == []

    def test_https_is_accepted(self):
        assert ScorerModelClient("https://example.test/v1/chat/completions", PROMPT, post=FakePost()).available

    @pytest.mark.parametrize("prompt", ["", "   \n", None])
    def test_no_prompt_means_the_model_is_never_asked(self, prompt):
        post = FakePost()
        c = ScorerModelClient(URL, prompt, post=post)
        assert not c.available and len(c.report.warnings) == 1
        assert c.score("hello") is None and post.calls == []

    def test_a_good_setup_is_available_and_quiet(self):
        c = client()
        assert c.available and c.report.ok

    @pytest.mark.parametrize("timeout", [0, -1, "slow", None, True])
    def test_a_bad_timeout_falls_back_to_the_default(self, timeout):
        post = FakePost()
        c = ScorerModelClient(URL, PROMPT, timeout=timeout, post=post)
        c.score("hello")
        assert post.calls[0][2] == DEFAULT_TIMEOUT
        assert len(c.report.warnings) == 1

    def test_strict_mode_raises_on_a_bad_setup(self):
        with pytest.raises(ScorerModelClientError):
            ScorerModelClient("nonsense", PROMPT, post=FakePost(), strict=True)
        with pytest.raises(ScorerModelClientError):
            ScorerModelClient(URL, "", post=FakePost(), strict=True)


class TestWarmUp:
    def test_warm_up_sends_one_throwaway_sentence(self):
        post = FakePost()
        assert client(post).warm_up() is True
        assert len(post.calls) == 1
        assert post.calls[0][1]["messages"][1]["content"] == USER_TEMPLATE.format(fragment_text=WARM_UP_FRAGMENT)

    def test_warm_up_reports_failure_without_raising(self):
        assert client(FakePost(OSError("down"))).warm_up() is False


# --- The debug view's request ----------------------------------------------------------

EXPLAIN_PROMPT = "You score a fragment and say why."


def explainer(text="7 | a vivid image | death", **options):
    post = FakePost(reply(text))
    return ScorerModelClient(URL, PROMPT, post=post, explain_prompt=EXPLAIN_PROMPT, **options), post


class TestExplain:
    def test_returns_the_written_reply(self):
        c, _ = explainer("  7 | a vivid image | death \n")
        assert c.explain("The lamp hums.").text == "7 | a vivid image | death"

    def test_the_score_is_the_digit_the_reply_starts_with(self):
        c, post = explainer()
        post.result = reply("6 | The lamp hums. | image")
        result = c.explain("The lamp hums.")
        assert result == Explanation("6 | The lamp hums. | image", 6.0)
        assert len(post.calls) == 1                          # one request gave both

    def test_a_reply_that_does_not_start_with_a_digit_gives_text_but_no_score(self):
        c, post = explainer()
        post.result = reply("The fragment is dull.")
        assert c.explain("hello there") == Explanation("The fragment is dull.", None)

    def test_a_decimal_is_text_but_no_score(self):
        c, _ = explainer("6.00 | words")
        assert c.explain("The lamp hums.") == Explanation("6.00 | words", None)

    def test_request_uses_the_explain_prompt_and_asks_for_words(self):
        c, post = explainer(explain_timeout=33.0)
        c.explain("  The lamp hums.  ")
        url, body, timeout = post.calls[0]
        assert url == URL and timeout == 33.0
        assert body["messages"] == [{"role": "system", "content": EXPLAIN_PROMPT},
                                    {"role": "user", "content": USER_TEMPLATE.format(fragment_text="The lamp hums.")}]
        assert body["max_tokens"] == EXPLAIN_MAX_TOKENS and body["temperature"] == 0
        assert "logprobs" not in body and "top_logprobs" not in body
        assert body["chat_template_kwargs"] == {"enable_thinking": False}

    def test_the_explain_prompt_is_stripped_but_otherwise_sent_whole(self):
        post = FakePost(reply("7.0"))
        ScorerModelClient(URL, PROMPT, post=post, explain_prompt="\n  Line one.\nLine two.\n\n").explain("hello there")
        assert post.calls[0][1]["messages"][0]["content"] == "Line one.\nLine two."

    def test_the_scoring_prompt_is_not_sent(self):
        c, post = explainer()
        c.explain("hello there")
        assert PROMPT not in post.calls[0][1]["messages"][0]["content"]

    def test_the_wait_is_longer_than_for_a_score(self):
        assert DEFAULT_EXPLAIN_TIMEOUT > DEFAULT_TIMEOUT
        c, post = explainer()
        c.explain("hello there")
        assert post.calls[0][2] == DEFAULT_EXPLAIN_TIMEOUT

    def test_without_a_explain_prompt_it_does_nothing(self):
        post = FakePost(reply("7.0"))
        c = ScorerModelClient(URL, PROMPT, post=post)
        assert not c.can_explain and c.explain("hello there") is None and post.calls == []

    def test_can_explain_needs_a_usable_client_and_a_explain_prompt(self):
        assert explainer()[0].can_explain
        assert not ScorerModelClient("nonsense", PROMPT, post=FakePost(), explain_prompt=EXPLAIN_PROMPT).can_explain

    @pytest.mark.parametrize("text", ["", "   ", None, 7])
    def test_empty_or_non_text_is_not_sent(self, text):
        c, post = explainer()
        assert c.explain(text) is None and post.calls == []

    def test_a_failure_gives_none_and_a_warning(self):
        c = ScorerModelClient(URL, PROMPT, post=FakePost(TimeoutError()), explain_prompt=EXPLAIN_PROMPT)
        assert c.explain("hello there") is None and len(c.report.warnings) == 1
        assert c.last_seconds is not None

    @pytest.mark.parametrize("bad", [None, {}, {"choices": []}, {"choices": [{}]}, {"choices": [{"message": {}}]},
                                     {"choices": [{"message": {"content": None}}]}, reply("   ")])
    def test_a_reply_without_text_gives_none(self, bad):
        c, _ = explainer()
        c._post = FakePost(bad)
        assert c.explain("hello there") is None

    def test_strict_mode_raises_on_a_failure(self):
        c = ScorerModelClient(URL, PROMPT, post=FakePost(OSError("down")), explain_prompt=EXPLAIN_PROMPT, strict=True)
        with pytest.raises(ScorerModelClientError):
            c.explain("hello there")

    def test_a_bad_explain_timeout_falls_back_with_a_warning(self):
        for bad in (0, -5, "slow", None, True):
            c, post = explainer(explain_timeout=bad)
            c.explain("hello there")
            assert post.calls[0][2] == DEFAULT_EXPLAIN_TIMEOUT and len(c.report.warnings) == 1

    def test_what_was_said_never_reaches_a_report_or_a_log(self, caplog):
        secret = "my secret fragment about the funeral"
        c = ScorerModelClient(URL, PROMPT, post=FakePost(OSError(f"failed on {secret}")), explain_prompt=EXPLAIN_PROMPT)
        with caplog.at_level(logging.DEBUG):
            c.explain(secret)
            explainer("7 | a funeral")[0].explain(secret)
        assert all("funeral" not in w for w in c.report.warnings)
        assert "funeral" not in caplog.text


class TestParseWrittenScore:
    @pytest.mark.parametrize("text,expected", [("7 | a vivid image | death", 7.0), ("  3", 3.0), ("0 | nothing", 0.0),
                                               ("9, extremely", 9.0), ("5 because", 5.0), ("7.", 7.0)])
    def test_the_leading_digit_is_read(self, text, expected):
        assert parse_written_score(text) == pytest.approx(expected)

    @pytest.mark.parametrize("text", ["", "a vivid image, 7", "Score: 7", "7.89, a vivid image", "7.5", "10", "85 | two digits",
                                      "12, odd", None, 7, "-4"])
    def test_anything_else_is_none(self, text):
        assert parse_written_score(text) is None
