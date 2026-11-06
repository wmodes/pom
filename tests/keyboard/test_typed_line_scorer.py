"""Tests for pom.keyboard.typed_line_scorer: a typed line in, a multiplied and judged Attempt out.

The Scorer is a small fake (what it does is tested elsewhere); the BarKeeper is the real one over a PercentileBar, because
how the typed line meets the room's bar is the point.
"""
import logging

import pytest

from pom.fault_recorder import FaultRecorder
from pom.keyboard import TypedLineScorer, TypedLineScorerError
from pom.qualifying import Attempt, BarKeeper, PercentileBar, Ruling
from pom.scoring import ScoredBy, ScoredClip
from pom.keyboard.typed_line_scorer import DEFAULT_SCORE_MULTIPLIER, TYPED


class FakeScorer:
    """Scores a text with the number it was told (3.0 if it was not told) and keeps a record of what it was asked."""

    def __init__(self, scores=None, scored_by=ScoredBy.MODEL):
        self.scores, self.scored_by, self.asked, self.report = dict(scores or {}), scored_by, [], FaultRecorder()

    def score_text(self, text, heard_at=0.0):
        from pom.listening.take_cutter import Clip, ClipLevel
        self.asked.append((text, heard_at))
        return ScoredClip(Clip(text, ClipLevel.SENTENCE, heard_at), self.scores.get(text, 3.0), self.scored_by)


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


def bar_keeper(bar_level=6.0, cooldown=60.0):
    """A BarKeeper whose bar sits at `bar_level` (the starting line, with a window that will not fill)."""
    return BarKeeper(PercentileBar(100.0, 300.0, 20, bar_level), cooldown)


def typed(scores=None, bar_level=6.0, cooldown=60.0, now=100.0, **options):
    scorer, made_bar_keeper, clock = FakeScorer(scores), bar_keeper(bar_level, cooldown), Clock(now)
    return TypedLineScorer(scorer, made_bar_keeper, clock, **options), scorer, made_bar_keeper, clock


class TestTheDefaults:
    def test_the_score_multiplier_starts_at_one_and_a_half(self):
        assert DEFAULT_SCORE_MULTIPLIER == 1.5

    def test_the_kind_is_typed(self):
        assert TYPED == "typed"


class TestTheSentence:
    def test_it_is_an_attempt_of_kind_typed_with_the_text_and_the_time(self):
        made, _, _, _ = typed({"Is anyone there?": 4.0}, now=42.5)
        sentence = made.score("Is anyone there?")
        assert isinstance(sentence, Attempt) and sentence.kind == "typed"
        assert (sentence.text, sentence.heard_at, sentence.scored_by) == ("Is anyone there?", 42.5, ScoredBy.MODEL)

    def test_the_text_is_trimmed(self):
        made, scorer, _, _ = typed()
        assert made.score("  hello there \n").text == "hello there" and scorer.asked == [("hello there", 100.0)]

    def test_the_scorer_is_asked_at_the_clock_time(self):
        made, scorer, _, clock = typed()
        made.score("one")
        clock.now = 130.0
        made.score("two")
        assert scorer.asked == [("one", 100.0), ("two", 130.0)]

    def test_the_bar_and_window_are_shown_as_they_stood_before_the_line(self):
        made, _, _, _ = typed(bar_level=6.0)
        sentence = made.score("anything")
        assert sentence.bar == 6.0 and sentence.window_size == 0

    def test_counted_is_the_bar_keepers_count(self):
        made, _, _, _ = typed(bar_influence=1.0)
        assert made.score("a").counted == 1 and made.score("b").counted == 2


class TestTheScoreMultiplier:
    def test_the_score_is_multiplied_by_one_and_a_half_to_begin_with(self):
        made, _, _, _ = typed({"x": 4.0})
        assert made.score("x").score == 6.0

    def test_the_score_multiplier_can_be_set(self):
        made, _, _, _ = typed({"x": 4.0}, score_multiplier=2)
        assert made.score("x").score == 8.0

    def test_the_product_is_not_capped_at_nine(self):
        made, _, _, _ = typed({"x": 9.0})
        assert made.score("x").score == 13.5

    def test_a_score_multiplier_of_one_is_no_bonus(self):
        made, _, _, _ = typed({"x": 4.0}, score_multiplier=1)
        assert made.score("x").score == 4.0

    def test_a_score_multiplier_of_zero_is_allowed(self):
        made, _, _, _ = typed({"x": 9.0}, score_multiplier=0)
        assert made.score("x").score == 0.0 and made.report.ok

    def test_a_score_multiplier_can_carry_a_dull_line_over_the_bar(self):
        made, _, _, _ = typed({"x": 4.5}, bar_level=6.0)
        assert made.score("x").is_hinge        # 4.5 * 1.5 = 6.75

    def test_without_the_score_multiplier_the_same_line_does_not_qualify(self):
        made, _, _, _ = typed({"x": 4.5}, bar_level=6.0, score_multiplier=1)
        assert not made.score("x").is_hinge


class TestAgainstTheBar:
    def test_a_line_that_clears_the_bar_is_a_hinge(self):
        made, _, _, _ = typed({"x": 5.0}, bar_level=6.0)
        sentence = made.score("x")
        assert sentence.is_hinge and sentence.ruling is Ruling.QUALIFIED

    def test_a_line_that_does_not_is_only_judged_and_is_not_a_hinge(self):
        made, _, _, _ = typed({"hello": 1.0}, bar_level=6.0)
        sentence = made.score("hello")
        assert not sentence.is_hinge and sentence.ruling is Ruling.DID_NOT_QUALIFY

    def test_a_line_exactly_at_the_bar_is_not_a_hinge(self):
        made, _, _, _ = typed({"x": 4.0}, bar_level=6.0)    # 4.0 * 1.5 = 6.0
        assert not made.score("x").is_hinge

    def test_typed_lines_in_a_row_all_qualify_because_there_is_no_cooldown(self):
        made, _, _, _ = typed({"x": 9.0})
        assert [made.score("x").is_hinge for _ in range(3)] == [True, True, True]

    def test_a_typed_hinge_does_not_start_the_rooms_cooldown(self):
        made, _, shared, _ = typed({"x": 9.0})
        made.score("x")
        assert shared.rule(9.0, 101.0) is Ruling.QUALIFIED

    def test_the_rooms_cooldown_does_not_hold_a_typed_line_back(self):
        made, _, shared, _ = typed({"x": 9.0})
        shared.rule(9.0, 99.0)
        assert made.score("x").is_hinge


class TestWhatTheBarIsHanded:
    """Every typed line, above the bar or below it, goes to the bar as the bar plus the influence of the way up to its (multiplied) score."""

    def test_by_default_a_line_goes_in_as_the_bar_itself_and_is_counted(self):
        made, _, shared, _ = typed({"x": 2.0})
        made.score("x")
        assert shared.counted == 1 and shared.window_size_at(100.0) == 1

    def test_a_hinge_is_counted_too(self):
        made, _, shared, _ = typed({"x": 9.0})
        made.score("x")
        assert shared.counted == 1

    def test_a_line_below_the_bar_is_judged_did_not_qualify(self):
        made, _, _, _ = typed({"x": 2.0}, bar_influence=1.0)
        assert made.score("x").ruling is Ruling.DID_NOT_QUALIFY

    def test_the_influence_is_what_the_bar_keeper_is_given(self):
        keeper = self.recording_keeper()
        TypedLineScorer(FakeScorer({"x": 4.0}), keeper, Clock(), score_multiplier=2, bar_influence=0.25).score("x")
        assert keeper.calls == [(8.0, 0.25)]

    def test_with_no_influence_given_a_line_goes_in_as_the_bar_itself(self):
        keeper = self.recording_keeper()
        TypedLineScorer(FakeScorer({"x": 4.0}), keeper, Clock()).score("x")
        assert keeper.calls == [(6.0, 0.0)]

    @staticmethod
    def recording_keeper():
        class Recording:
            def __init__(self):
                self.calls = []
            def bar_at(self, now): return 6.0
            def window_size_at(self, now): return 0
            counted = 0
            def rule_typed(self, score, now, bar_influence=0.0):
                self.calls.append((score, bar_influence))
                return Ruling.DID_NOT_QUALIFY
        return Recording()


class TestTheBarInfluence:
    @pytest.mark.parametrize("influence", [1.1, 2, -0.1, 0])
    def test_the_influence_is_not_limited_to_zero_to_one(self, influence):
        made, _, _, _ = typed({"x": 2.0}, bar_influence=influence)
        assert made.report.ok and made.score("x").score == 3.0

    @pytest.mark.parametrize("bad", ["1", None, True, [1], float("nan"), float("inf")])
    def test_an_influence_that_is_not_a_number_is_nothing_with_a_warning(self, bad):
        made, _, shared, _ = typed({"x": 2.0}, bar_influence=bad)
        made.score("x")
        assert len(made.report.warnings) == 1 and made._bar_influence == 0.0


class TestWhenThereIsNoScore:
    def test_a_blank_line_is_unscored_and_the_scorer_is_not_asked(self):
        made, scorer, _, _ = typed()
        sentence = made.score("   \n")
        assert (sentence.score, sentence.scored_by, sentence.ruling, sentence.text) == (None, ScoredBy.UNSCORED, None, "")
        assert scorer.asked == []

    def test_a_line_that_is_not_text_is_unscored(self):
        made, scorer, _, _ = typed()
        assert made.score(None).score is None and scorer.asked == []

    def test_a_line_the_scorer_cannot_score_is_ignored_by_the_bar_keeper(self):
        class Unable(FakeScorer):
            def score_text(self, text, heard_at=0.0):
                result = super().score_text(text, heard_at)
                return ScoredClip(result.clip, None, ScoredBy.UNSCORED)
        made = TypedLineScorer(Unable(), bar_keeper(), Clock())
        sentence = made.score("hello")
        assert (sentence.score, sentence.ruling, sentence.is_hinge) == (None, Ruling.NO_MARK, False)

    def test_the_clip_filters_score_is_multiplied_like_any_other(self):
        class Filtered(FakeScorer):
            def score_text(self, text, heard_at=0.0):
                result = super().score_text(text, heard_at)
                return ScoredClip(result.clip, 1.0, ScoredBy.CLIP_FILTER)
        sentence = TypedLineScorer(Filtered(), bar_keeper(), Clock()).score("hello")
        assert (sentence.score, sentence.scored_by, sentence.is_hinge) == (1.5, ScoredBy.CLIP_FILTER, False)


class TestTheScoreMultiplierIsChecked:
    @pytest.mark.parametrize("bad", [-1, -0.1, "1.5", None, True, [1.5], float("nan"), float("inf")])
    def test_a_bad_score_multiplier_is_the_default_with_a_warning(self, bad):
        made, _, _, _ = typed({"x": 4.0}, score_multiplier=bad)
        assert made.score("x").score == 6.0 and len(made.report.warnings) == 1

    def test_strict_raises_instead(self):
        with pytest.raises(TypedLineScorerError):
            typed(score_multiplier=-1, strict=True)

    def test_a_good_score_multiplier_gives_no_warning(self):
        assert typed(score_multiplier=2)[0].report.ok


class TestWhatIsKept:
    def test_the_typed_text_is_never_logged(self, caplog):
        made, _, _, _ = typed({"the secret words": 9.0})
        with caplog.at_level(logging.DEBUG):
            made.score("the secret words")
        assert "secret" not in caplog.text

    def test_the_typed_text_is_never_in_the_report(self):
        made, _, _, _ = typed(score_multiplier=-1)
        made.score("the secret words")
        assert "secret" not in " ".join(made.report.lines())
