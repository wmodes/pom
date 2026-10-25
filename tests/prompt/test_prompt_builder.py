"""Tests for pom.prompt.prompt_builder: the hinge and the exemplars go into Wes's prompt, nothing else is added.

The ExemplarSelector is a small fake where only the PromptBuilder is under test, and a real one with a seeded
random generator where the point is that the same hinge can get the same exemplars.
"""
import hashlib
import logging
import random

import pytest

from pom.corpus import Chunk, ChunkStore, DrawWeights, ExemplarSelector
from pom.prompt import OraclePrompt, PromptBuilder, PromptBuilderError

PROMPT = "Speak as the oracle.\n\nExamples:\n{exemplars}\n\nThey said: {hinge}\nYour portent:"


def chunk(index, text=None):
    return Chunk(f"book#{index}", "book", "verse", text or f"chunk {index}", 2)


class FakeSelector:
    """Hands back the chunks it was given, and counts the draws."""

    def __init__(self, chunks):
        self.chunks, self.draws = list(chunks), 0

    def draw(self, count=None):
        self.draws += 1
        return list(self.chunks)


def builder(prompt=PROMPT, chunks=(chunk(1), chunk(2)), strict=False):
    selector = FakeSelector(chunks)
    return PromptBuilder(prompt, selector, strict=strict), selector


class TestBuilding:
    def test_the_hinge_and_exemplars_go_where_the_prompt_marks_them(self):
        prompt = builder()[0].build("The dog barks at ghosts.")
        assert prompt.text == ("Speak as the oracle.\n\nExamples:\nchunk 1\n\nchunk 2\n\n"
                               "They said: The dog barks at ghosts.\nYour portent:")

    def test_the_result_carries_the_exemplars_it_used(self):
        prompt = builder()[0].build("A hinge.")
        assert isinstance(prompt, OraclePrompt)
        assert prompt.exemplars == (chunk(1), chunk(2))

    def test_a_hinge_is_trimmed_but_otherwise_left_word_for_word(self):
        text = builder("{hinge}")[0].build("  Hello,  there!\n").text
        assert text == "Hello,  there!"

    def test_a_chunk_keeps_its_own_line_breaks(self):
        text = builder("{exemplars} {hinge}", chunks=[chunk(1, "line one\nline two")])[0].build("x").text
        assert text == "line one\nline two x"

    def test_every_place_is_filled(self):
        text = builder("{hinge} / {hinge} / {exemplars} / {exemplars}", chunks=[chunk(1)])[0].build("h").text
        assert text == "h / h / chunk 1 / chunk 1"

    def test_the_prompt_is_stripped_of_surrounding_blank_lines(self):
        assert builder("\n\n  {hinge}  \n\n")[0].build("h").text == "h"

    def test_two_builds_do_not_affect_each_other(self):
        built, _ = builder()
        assert built.build("first").text != built.build("second").text
        assert built.build("first").text == built.build("first").text


class TestBracesAreJustText:
    def test_other_braces_in_the_prompt_are_left_alone(self):
        text = builder('Reply as JSON {"portent": "..."} about {hinge}')[0].build("h").text
        assert text == 'Reply as JSON {"portent": "..."} about h'

    def test_a_hinge_that_looks_like_a_place_is_not_filled_in_again(self):
        text = builder(PROMPT)[0].build("{exemplars} {hinge}").text
        assert text.endswith("They said: {exemplars} {hinge}\nYour portent:")

    def test_a_chunk_that_looks_like_a_place_is_not_filled_in_again(self):
        text = builder("{exemplars} then {hinge}", chunks=[chunk(1, "see {hinge}")])[0].build("real").text
        assert text == "see {hinge} then real"

    def test_a_hinge_with_curly_braces_is_kept(self):
        assert builder("{hinge}")[0].build("a {b} c").text == "a {b} c"


class TestExemplars:
    def test_a_prompt_without_the_exemplars_place_draws_none(self):
        built, selector = builder("Only this: {hinge}")
        prompt = built.build("h")
        assert selector.draws == 0 and prompt.exemplars == () and prompt.text == "Only this: h"
        assert built.report.ok

    def test_each_build_draws_once(self):
        built, selector = builder()
        built.build("a")
        built.build("b")
        assert selector.draws == 2

    def test_nothing_to_draw_still_builds_and_is_reported(self):
        built, _ = builder(chunks=[])
        prompt = built.build("h")
        assert prompt.text.startswith("Speak as the oracle.\n\nExamples:\n\n\nThey said: h")
        assert prompt.exemplars == () and len(built.report.warnings) == 1

    def test_nothing_to_draw_raises_in_strict_mode(self):
        built, _ = builder(chunks=[], strict=True)
        with pytest.raises(PromptBuilderError):
            built.build("h")


class TestBadInput:
    @pytest.mark.parametrize("hinge", ["", "   \n", None, 42])
    def test_an_empty_or_odd_hinge_gives_none_and_a_warning(self, hinge):
        built, selector = builder()
        assert built.build(hinge) is None
        assert len(built.report.warnings) == 1 and selector.draws == 0

    @pytest.mark.parametrize("prompt", ["", "  \n ", None, 7])
    def test_no_prompt_means_unavailable(self, prompt):
        built, selector = builder(prompt)
        assert not built.available and built.build("h") is None
        assert len(built.report.warnings) == 1 and selector.draws == 0

    def test_a_prompt_without_the_hinge_place_is_reported(self):
        built, _ = builder("Examples: {exemplars}")
        assert built.available and len(built.report.warnings) == 1
        assert "hinge" in built.report.warnings[0]

    def test_a_good_prompt_is_quiet(self):
        assert builder()[0].report.ok

    def test_strict_mode_raises_for_the_bad_inputs(self):
        with pytest.raises(PromptBuilderError):
            builder("", strict=True)
        with pytest.raises(PromptBuilderError):
            builder("no places", strict=True)
        with pytest.raises(PromptBuilderError):
            builder(strict=True)[0].build("")

    def test_the_prompt_can_be_built_again_after_a_bad_hinge(self):
        built, _ = builder()
        assert built.build("") is None
        assert built.build("fine") is not None


class TestPrivacy:
    def test_the_hinge_never_reaches_the_report_or_the_log(self, caplog):
        caplog.set_level(logging.DEBUG)
        built, _ = builder(chunks=[])
        built.build("a secret about the neighbor")
        built.build("   ")
        assert "secret" not in caplog.text and "neighbor" not in caplog.text
        assert all("secret" not in line for line in built.report.lines())


def real_selector(seed=None):
    chunks = [Chunk(f"book#{i}", "book", "verse", f"verse number {i}", 3) for i in range(200)]
    weights = DrawWeights.from_dict({"registers": {"verse": {}}})
    rng = random.Random(seed)
    return ExemplarSelector(ChunkStore.from_chunks(chunks), weights, rng), rng


def seed_of(hinge):
    """A seed that is the same on every run (unlike hash())."""
    return int(hashlib.sha256(hinge.encode()).hexdigest()[:8], 16)


class TestRepeatableWithASeededSelector:
    def test_reseeding_with_the_hinge_gives_the_same_exemplars_every_time(self):
        selector, rng = real_selector()
        built = PromptBuilder(PROMPT, selector)
        runs = []
        for _ in range(3):
            rng.seed(seed_of("The dog barks at ghosts."))
            runs.append(built.build("The dog barks at ghosts."))
        assert runs[0] == runs[1] == runs[2] and runs[0].exemplars

    def test_a_different_hinge_gets_different_exemplars(self):
        selector, rng = real_selector()
        built = PromptBuilder(PROMPT, selector)
        rng.seed(seed_of("one"))
        first = built.build("one")
        rng.seed(seed_of("two"))
        assert built.build("two").exemplars != first.exemplars

    def test_unseeded_draws_differ_between_builds(self):
        selector, _ = real_selector()
        built = PromptBuilder(PROMPT, selector)
        assert len({built.build("same").text for _ in range(10)}) > 1

    def test_the_exemplar_count_follows_the_selector_range(self):
        selector, rng = real_selector()
        built = PromptBuilder(PROMPT, selector)
        counts = {len(built.build("x").exemplars) for _ in range(60)}
        assert counts <= set(range(3, 6)) and len(counts) > 1
