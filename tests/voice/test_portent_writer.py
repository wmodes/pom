"""Tests for pom.voice.portent_writer: a hinge goes in, a portent comes out.

The PromptBuilder and the OracleModelClient are the real ones, with a small fake selector and a fake llama-server,
so these tests check how the writer joins them. Where the point is repeatable exemplars, the selector is a real
ExemplarSelector with a random generator the writer is given.
"""
import logging
import random

import pytest

from pom.corpus import Chunk, ChunkStore, DrawWeights, ExemplarSelector
from pom.prompt import PromptBuilder
from pom.voice import OracleModelClient, Portent, PortentWriter, PortentWriterError

URL = "http://127.0.0.1:8099/v1/chat/completions"
PROMPT = "Speak as the oracle.\n\n{exemplars}\n\n{hinge}"


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


class FakeSelector:
    """Hands back the chunks it was given, and counts the draws."""

    def __init__(self, chunks):
        self.chunks, self.draws = list(chunks), 0

    def draw(self, count=None):
        self.draws += 1
        return list(self.chunks)


def chunk(index):
    return Chunk(f"book#{index}", "book", "verse", f"chunk {index}", 2)


def writer(post=None, prompt=PROMPT, chunks=(chunk(1), chunk(2)), rng=None, strict=False, selector=None):
    """A writer over the real builder and client, with a fake server. Returns the writer, the server and the selector."""
    post = post if post is not None else FakePost()
    selector = selector if selector is not None else FakeSelector(chunks)
    return PortentWriter(PromptBuilder(prompt, selector), OracleModelClient(URL, post=post), rng, strict), post, selector


def real_selector(seed=None):
    chunks = [Chunk(f"book#{i}", "book", "verse", f"verse number {i}", 3) for i in range(200)]
    weights = DrawWeights.from_dict({"registers": {"verse": {}}})
    rng = random.Random(seed)
    return ExemplarSelector(ChunkStore.from_chunks(chunks), weights, rng), rng


class TestWriting:
    def test_the_portent_is_what_the_model_wrote(self):
        portent = writer()[0].write("The dog barks at ghosts.")
        assert isinstance(portent, Portent)
        assert portent.text == "The ghosts keep the hours."

    def test_the_model_is_shown_the_hinge_and_the_exemplars(self):
        written, post, _ = writer()
        written.write("The dog barks at ghosts.")
        prompt = post.calls[0][1]["messages"][0]["content"]
        assert "The dog barks at ghosts." in prompt
        assert "chunk 1" in prompt and "chunk 2" in prompt

    def test_the_portent_carries_the_exemplars_it_was_shown(self):
        assert writer()[0].write("A hinge.").exemplars == (chunk(1), chunk(2))

    def test_the_model_is_asked_once_for_each_write(self):
        written, post, _ = writer()
        written.write("one")
        written.write("two")
        assert len(post.calls) == 2

    def test_the_portent_says_how_long_the_model_took(self):
        portent = writer()[0].write("A hinge.")
        assert isinstance(portent.seconds, float) and portent.seconds >= 0

    def test_the_seconds_are_the_ones_the_client_measured(self, monkeypatch):
        clock = iter([100.0, 112.5])
        monkeypatch.setattr("pom.voice.oracle_model_client.time.perf_counter", lambda: next(clock))
        assert writer()[0].write("A hinge.").seconds == 12.5

    def test_a_portent_that_finished_is_not_cut_off(self):
        assert writer()[0].write("A hinge.").cut_off is False

    def test_a_portent_that_hit_the_limit_is_cut_off(self):
        assert writer(FakePost(reply(finish="length")))[0].write("A hinge.").cut_off is True

    def test_being_cut_off_once_does_not_stick_to_the_next_portent(self):
        written, post, _ = writer(FakePost(reply(finish="length")))
        assert written.write("one").cut_off is True
        post.result = reply(finish="stop")
        assert written.write("two").cut_off is False

    def test_the_words_are_counted(self):
        assert writer(FakePost(reply("one two  three\nfour")))[0].write("h").words == 4

    def test_a_portent_cannot_be_changed(self):
        portent = writer()[0].write("A hinge.")
        with pytest.raises(AttributeError):
            portent.text = "other"


class TestWhenThereIsNoPortent:
    def test_an_empty_hinge_gives_none_and_the_model_is_not_asked(self):
        written, post, selector = writer()
        assert written.write("   ") is None
        assert post.calls == [] and selector.draws == 0

    def test_a_missing_prompt_gives_none_and_the_model_is_not_asked(self):
        written, post, _ = writer(prompt="")
        assert written.write("A hinge.") is None
        assert post.calls == []

    def test_a_model_that_is_down_gives_none(self):
        assert writer(FakePost(ConnectionError("down")))[0].write("A hinge.") is None

    def test_a_model_that_writes_nothing_gives_none(self):
        assert writer(FakePost(reply("   ")))[0].write("A hinge.") is None

    def test_the_part_that_met_the_problem_says_so_in_the_report_lines(self):
        written, _, _ = writer(FakePost(ConnectionError("down")))
        written.write("A hinge.")
        assert any("did not answer" in line for line in written.report_lines())

    def test_an_empty_hinge_is_in_the_report_lines(self):
        written, _, _ = writer()
        written.write("")
        assert any("hinge is empty" in line for line in written.report_lines())

    def test_a_good_write_after_a_bad_one_still_works(self):
        written, post, _ = writer(FakePost(ConnectionError("down")))
        assert written.write("one") is None
        post.result = reply()
        assert written.write("two").text == "The ghosts keep the hours."

    def test_there_is_nothing_to_report_when_all_went_well(self):
        written, _, _ = writer()
        written.write("A hinge.")
        assert written.report_lines() == []


class TestRepeatableExemplars:
    def test_the_same_hinge_and_seed_show_the_same_exemplars(self):
        selector, rng = real_selector()
        written = writer(selector=selector, rng=rng)[0]
        first = written.write("The dog barks.", seed=7).exemplars
        second = written.write("The dog barks.", seed=7).exemplars
        assert first == second and len(first) >= 1

    def test_a_different_seed_shows_different_exemplars(self):
        selector, rng = real_selector()
        written = writer(selector=selector, rng=rng)[0]
        assert written.write("The dog barks.", seed=1).exemplars != written.write("The dog barks.", seed=2).exemplars

    def test_a_seed_of_zero_is_a_seed(self):
        selector, rng = real_selector(seed=99)
        portent = writer(selector=selector, rng=rng)[0].write("x", seed=0)
        reference, _ = real_selector(seed=0)
        assert [c.text for c in portent.exemplars] == [c.text for c in reference.draw()]

    def test_the_seed_is_set_before_the_exemplars_are_drawn(self):
        selector, rng = real_selector(seed=123)
        again, rng2 = real_selector(seed=5)
        by_seed = [c.text for c in writer(selector=again, rng=rng2)[0].write("x", seed=123).exemplars]
        assert by_seed == [c.text for c in selector.draw()]

    def test_without_a_seed_the_generator_is_left_alone(self):
        rng = random.Random(4)
        before = rng.getstate()
        writer(rng=rng)[0].write("A hinge.")              # a fake selector draws nothing at random
        assert rng.getstate() == before

    def test_without_a_seed_the_draws_stay_random(self):
        selector, rng = real_selector()
        written = writer(selector=selector, rng=rng)[0]
        draws = {tuple(c.text for c in written.write("x").exemplars) for _ in range(5)}
        assert len(draws) > 1


class TestASeedThatCannotBeUsed:
    def test_a_seed_without_a_generator_is_recorded_and_the_portent_is_still_written(self):
        written, _, _ = writer()
        assert written.write("A hinge.", seed=3).text == "The ghosts keep the hours."
        assert any("seed" in line for line in written.report.lines())

    def test_no_seed_without_a_generator_is_no_problem(self):
        written, _, _ = writer()
        written.write("A hinge.")
        assert written.report.lines() == []

    def test_strict_raises_for_it(self):
        with pytest.raises(PortentWriterError):
            writer(strict=True)[0].write("A hinge.", seed=3)

    def test_the_report_lines_include_the_writers_own_problem(self):
        written, _, _ = writer()
        written.write("A hinge.", seed=3)
        assert any("seed" in line for line in written.report_lines())


class TestNothingSaidInTheRoomIsKept:
    def test_neither_the_hinge_nor_the_portent_reaches_the_report_or_the_log(self, caplog):
        caplog.set_level(logging.DEBUG)
        written, _, _ = writer(FakePost(reply("the neighbor keeps secret hours")))
        written.write("my secret about the ghosts", seed=1)
        written.write("   ")
        text = caplog.text + "\n".join(written.report_lines())
        assert "secret" not in text and "neighbor" not in text and "ghosts" not in text
