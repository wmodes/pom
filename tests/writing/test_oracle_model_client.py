"""Tests for pom.writing.oracle_model_client: asking for a portent through a fake llama-server."""

import pytest

from pom.defaults import OracleSettings
from pom.writing import OracleModelClient, OracleModelClientError

URL = "http://127.0.0.1:8099/v1/chat/completions"
PROMPT = "Speak as the oracle.\n\nThey said: the dog barks at ghosts."


def reply(text="The ghosts keep the hours.", finish="stop"):
    """A llama-server style reply."""
    return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": finish}]}


class FakeComms:
    """A ModelComms that records every request and returns a canned reply (or raises)."""

    def __init__(self, result=None):
        self.result = result if result is not None else reply()
        self.address = URL
        self.calls = []

    def post_json(self, body, timeout_seconds, path=""):
        self.calls.append((self.address, body, timeout_seconds))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


ORACLE = OracleSettings(url=URL)


def client(comms=None, timeout=None, max_tokens=None, temperature=None, **options):
    """A client of the oracle at URL; a setting given here changes only that one."""
    options.setdefault("strict", False)
    changed = {name: value for name, value in (("timeout", timeout), ("max_tokens", max_tokens), ("temperature", temperature)) if value is not None}
    return OracleModelClient(OracleSettings(url=URL, **changed), comms=comms or FakeComms(), **options)


class TestAskingForAPortent:
    def test_the_portent_is_what_the_model_wrote(self):
        assert client().consult(PROMPT) == "The ghosts keep the hours."

    def test_whitespace_around_the_portent_is_trimmed_and_the_rest_left_alone(self):
        comms = FakeComms(reply("\n  Line one.\n\nLine  two.  \n"))
        assert client(comms).consult(PROMPT) == "Line one.\n\nLine  two."

    def test_the_prompt_goes_as_one_user_message_word_for_word(self):
        comms = FakeComms()
        client(comms).consult(PROMPT)
        url, body, _ = comms.calls[0]
        assert url == URL and body["messages"] == [{"role": "user", "content": PROMPT}]

    def test_the_request_carries_the_settings(self):
        comms = FakeComms()
        client(comms, timeout=30, max_tokens=200, temperature=1.1).consult(PROMPT)
        _, body, timeout = comms.calls[0]
        assert (body["max_tokens"], body["temperature"], timeout) == (200, 1.1, 30)

    def test_the_defaults_are_used_when_nothing_is_given(self):
        comms = FakeComms()
        client(comms).consult(PROMPT)
        _, body, timeout = comms.calls[0]
        assert (body["max_tokens"], body["temperature"], timeout) == (ORACLE.max_tokens, ORACLE.temperature, ORACLE.timeout)
        assert (ORACLE.max_tokens, ORACLE.temperature, ORACLE.timeout) == (300, 0.8, 240.0)

    def test_thinking_is_turned_off_for_models_that_would_think(self):
        comms = FakeComms()
        client(comms).consult(PROMPT)
        assert comms.calls[0][1]["chat_template_kwargs"] == {"enable_thinking": False}

    def test_the_prompt_is_not_trimmed_or_changed(self):
        comms = FakeComms()
        client(comms).consult("  x  \n")
        assert comms.calls[0][1]["messages"][0]["content"] == "  x  \n"

    def test_a_temperature_of_zero_is_sent_as_zero(self):
        comms = FakeComms()
        client(comms, temperature=0).consult(PROMPT)
        assert comms.calls[0][1]["temperature"] == 0

    def test_a_good_client_is_quiet(self, logged):
        c = client()
        c.consult(PROMPT)
        assert logged.of(c).ok()

    def test_the_time_taken_is_recorded(self):
        c = client()
        assert c.last_seconds is None
        c.consult(PROMPT)
        assert c.last_seconds is not None and c.last_seconds >= 0


class TestWhenThereIsNothingToAsk:
    @pytest.mark.parametrize("prompt", ["", "   \n", None, 5])
    def test_an_empty_or_odd_prompt_is_not_sent(self, prompt):
        comms = FakeComms()
        assert client(comms).consult(prompt) is None
        assert comms.calls == []


class TestWhenTheModelFails:
    @pytest.mark.parametrize("result", [
        OSError("connection refused"), TimeoutError("slow"), ValueError("not json"),
        {}, {"choices": []}, {"choices": [{}]}, {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": None}}]}, "not a dict"])
    def test_a_dead_or_odd_server_gives_none_and_a_warning(self, result, logged):
        c = client(FakeComms(result))
        assert c.consult(PROMPT) is None
        assert len(logged.of(c).warnings()) == 1 and c.last_seconds is not None

    @pytest.mark.parametrize("text", ["", "   \n"])
    def test_an_empty_reply_gives_none_and_a_warning(self, text, logged):
        c = client(FakeComms(reply(text)))
        assert c.consult(PROMPT) is None
        assert len(logged.of(c).warnings()) == 1 and "empty" in logged.of(c).warnings()[0]

    def test_it_works_again_after_a_failure(self):
        comms = FakeComms(OSError("down"))
        c = client(comms)
        assert c.consult(PROMPT) is None
        comms.result = reply("Back again.")
        assert c.consult(PROMPT) == "Back again."

    def test_strict_mode_raises_instead(self):
        with pytest.raises(OracleModelClientError):
            client(FakeComms(OSError("down")), strict=True).consult(PROMPT)
        with pytest.raises(OracleModelClientError):
            client(FakeComms(reply("")), strict=True).consult(PROMPT)


class TestCutOff:
    def test_a_portent_that_hit_the_limit_is_returned_and_flagged(self):
        c = client(FakeComms(reply("It was a dark and", finish="length")))
        assert c.consult(PROMPT) == "It was a dark and" and c.last_cut_off is True

    def test_a_finished_portent_is_not_flagged(self):
        c = client()
        c.consult(PROMPT)
        assert c.last_cut_off is False

    def test_the_flag_is_reset_by_the_next_portent(self):
        comms = FakeComms(reply("Cut", finish="length"))
        c = client(comms)
        c.consult(PROMPT)
        comms.result = reply("Whole.")
        c.consult(PROMPT)
        assert c.last_cut_off is False


class TestSettings:
    def test_the_smallest_allowed_settings_are_accepted(self, logged):
        comms = FakeComms()
        c = client(comms, timeout=0.1, max_tokens=1, temperature=0)
        c.consult(PROMPT)
        _, body, timeout = comms.calls[0]
        assert (body["max_tokens"], body["temperature"], timeout) == (1, 0, 0.1) and logged.of(c).ok()

    def test_a_client_must_be_handed_its_comms(self):
        """The entry point builds one ModelComms per model and hands it in; a client never builds its own."""
        with pytest.raises(TypeError):
            OracleModelClient(ORACLE)

    def test_every_request_goes_through_the_comms_it_was_handed(self):
        comms = FakeComms()
        c = client(comms)
        c.consult(PROMPT)
        assert c.comms is comms and len(comms.calls) == 1


class TestPrivacy:
    def test_neither_the_prompt_nor_the_portent_reaches_the_report_or_the_log(self, logged):
        c = client()
        c.consult("They said: a secret about the neighbor.")
        client(FakeComms(OSError("a secret about the neighbor"))).consult("They said: a secret about the neighbor.")
        failed = client(FakeComms(OSError("down")))
        failed.consult("They said: a secret about the neighbor.")
        assert "secret" not in logged.text() and "neighbor" not in logged.text() and "ghosts" not in logged.text()
        assert all("secret" not in line and "ghosts" not in line for line in logged.of(failed).lines())
