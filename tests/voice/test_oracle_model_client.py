"""Tests for pom.voice.oracle_model_client: asking for a portent through a fake llama-server."""
import logging

import pytest

from pom.voice import OracleModelClient, OracleModelClientError
from pom.voice.oracle_model_client import DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE, DEFAULT_TIMEOUT

URL = "http://127.0.0.1:8099/v1/chat/completions"
PROMPT = "Speak as the oracle.\n\nThey said: the dog barks at ghosts."


def reply(text="The ghosts keep the hours.", finish="stop"):
    """A llama-server style reply."""
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": finish}]}


class FakePost:
    """Records every request and returns a canned reply (or raises)."""

    def __init__(self, result=None):
        self.result = result if result is not None else reply()
        self.calls = []

    def __call__(self, url, body, timeout):
        self.calls.append((url, body, timeout))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def client(post=None, **options):
    options.setdefault("strict", False)
    return OracleModelClient(URL, post=post or FakePost(), **options)


class TestAskingForAPortent:
    def test_the_portent_is_what_the_model_wrote(self):
        assert client().portent(PROMPT) == "The ghosts keep the hours."

    def test_whitespace_around_the_portent_is_trimmed_and_the_rest_left_alone(self):
        post = FakePost(reply("\n  Line one.\n\nLine  two.  \n"))
        assert client(post).portent(PROMPT) == "Line one.\n\nLine  two."

    def test_the_prompt_goes_as_one_user_message_word_for_word(self):
        post = FakePost()
        client(post).portent(PROMPT)
        url, body, _ = post.calls[0]
        assert url == URL and body["messages"] == [{"role": "user", "content": PROMPT}]

    def test_the_request_carries_the_settings(self):
        post = FakePost()
        client(post, timeout=30, max_tokens=200, temperature=1.1).portent(PROMPT)
        _, body, timeout = post.calls[0]
        assert (body["max_tokens"], body["temperature"], timeout) == (200, 1.1, 30)

    def test_the_defaults_are_used_when_nothing_is_given(self):
        post = FakePost()
        client(post).portent(PROMPT)
        _, body, timeout = post.calls[0]
        assert (body["max_tokens"], body["temperature"], timeout) == (DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE, DEFAULT_TIMEOUT)
        assert (DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE, DEFAULT_TIMEOUT) == (300, 0.8, 120.0)

    def test_thinking_is_turned_off_for_models_that_would_think(self):
        post = FakePost()
        client(post).portent(PROMPT)
        assert post.calls[0][1]["chat_template_kwargs"] == {"enable_thinking": False}

    def test_the_prompt_is_not_trimmed_or_changed(self):
        post = FakePost()
        client(post).portent("  x  \n")
        assert post.calls[0][1]["messages"][0]["content"] == "  x  \n"

    def test_a_temperature_of_zero_is_sent_as_zero(self):
        post = FakePost()
        client(post, temperature=0).portent(PROMPT)
        assert post.calls[0][1]["temperature"] == 0

    def test_a_good_client_is_quiet(self):
        c = client()
        c.portent(PROMPT)
        assert c.report.ok and c.available

    def test_the_time_taken_is_recorded(self):
        c = client()
        assert c.last_seconds is None
        c.portent(PROMPT)
        assert c.last_seconds is not None and c.last_seconds >= 0


class TestWhenThereIsNothingToAsk:
    @pytest.mark.parametrize("prompt", ["", "   \n", None, 5])
    def test_an_empty_or_odd_prompt_is_not_sent(self, prompt):
        post = FakePost()
        assert client(post).portent(prompt) is None
        assert post.calls == []


class TestWhenTheModelFails:
    @pytest.mark.parametrize("result", [
        OSError("connection refused"), TimeoutError("slow"), ValueError("not json"),
        {}, {"choices": []}, {"choices": [{}]}, {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": None}}]}, "not a dict"])
    def test_a_dead_or_odd_server_gives_none_and_a_warning(self, result):
        c = client(FakePost(result))
        assert c.portent(PROMPT) is None
        assert len(c.report.warnings) == 1 and c.last_seconds is not None

    @pytest.mark.parametrize("text", ["", "   \n"])
    def test_an_empty_reply_gives_none_and_a_warning(self, text):
        c = client(FakePost(reply(text)))
        assert c.portent(PROMPT) is None
        assert len(c.report.warnings) == 1 and "empty" in c.report.warnings[0]

    def test_it_works_again_after_a_failure(self):
        post = FakePost(OSError("down"))
        c = client(post)
        assert c.portent(PROMPT) is None
        post.result = reply("Back again.")
        assert c.portent(PROMPT) == "Back again."

    def test_strict_mode_raises_instead(self):
        with pytest.raises(OracleModelClientError):
            client(FakePost(OSError("down")), strict=True).portent(PROMPT)
        with pytest.raises(OracleModelClientError):
            client(FakePost(reply("")), strict=True).portent(PROMPT)


class TestCutOff:
    def test_a_portent_that_hit_the_limit_is_returned_and_flagged(self):
        c = client(FakePost(reply("It was a dark and", finish="length")))
        assert c.portent(PROMPT) == "It was a dark and" and c.last_cut_off is True

    def test_a_finished_portent_is_not_flagged(self):
        c = client()
        c.portent(PROMPT)
        assert c.last_cut_off is False

    def test_the_flag_is_reset_by_the_next_portent(self):
        post = FakePost(reply("Cut", finish="length"))
        c = client(post)
        c.portent(PROMPT)
        post.result = reply("Whole.")
        c.portent(PROMPT)
        assert c.last_cut_off is False


class TestSettings:
    @pytest.mark.parametrize("url", ["", "ftp://x/y", "file:///etc/passwd", "127.0.0.1:8099", None, 5])
    def test_a_bad_address_means_the_model_is_never_asked(self, url):
        post = FakePost()
        c = OracleModelClient(url, post=post)
        assert not c.available and c.portent(PROMPT) is None and post.calls == []
        assert len(c.report.warnings) == 1

    def test_https_is_accepted(self):
        assert OracleModelClient("https://example.test/v1/chat/completions").available

    @pytest.mark.parametrize("name,value", [
        ("timeout", 0), ("timeout", -1), ("timeout", "slow"), ("timeout", True), ("timeout", None),
        ("max_tokens", 0), ("max_tokens", 2.5), ("max_tokens", "300"), ("max_tokens", True),
        ("temperature", -0.1), ("temperature", "hot"), ("temperature", True), ("temperature", None)])
    def test_a_bad_setting_is_replaced_by_its_default_with_a_warning(self, name, value):
        post = FakePost()
        c = client(post, **{name: value})
        c.portent(PROMPT)
        _, body, timeout = post.calls[0]
        assert (body["max_tokens"], body["temperature"], timeout) == (DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE, DEFAULT_TIMEOUT)
        assert len(c.report.warnings) == 1 and f"oracle {name}" in c.report.warnings[0]

    def test_the_smallest_allowed_settings_are_accepted(self):
        post = FakePost()
        c = client(post, timeout=0.1, max_tokens=1, temperature=0)
        c.portent(PROMPT)
        _, body, timeout = post.calls[0]
        assert (body["max_tokens"], body["temperature"], timeout) == (1, 0, 0.1) and c.report.ok

    def test_strict_mode_raises_for_a_bad_setting(self):
        with pytest.raises(OracleModelClientError):
            client(max_tokens=0, strict=True)
        with pytest.raises(OracleModelClientError):
            OracleModelClient("nope", strict=True)


class TestPrivacy:
    def test_neither_the_prompt_nor_the_portent_reaches_the_report_or_the_log(self, caplog):
        caplog.set_level(logging.DEBUG)
        c = client()
        c.portent("They said: a secret about the neighbor.")
        client(FakePost(OSError("a secret about the neighbor"))).portent("They said: a secret about the neighbor.")
        failed = client(FakePost(OSError("down")))
        failed.portent("They said: a secret about the neighbor.")
        assert "secret" not in caplog.text and "neighbor" not in caplog.text and "ghosts" not in caplog.text
        assert all("secret" not in line and "ghosts" not in line for line in failed.report.lines())
