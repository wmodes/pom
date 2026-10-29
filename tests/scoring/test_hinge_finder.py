"""Tests for pom.scoring.hinge_finder: sound in, hinges out.

The Listener and the Scorer are small fakes (what they do is tested elsewhere); the SegmentFragmenter and the
ConvoInterestTracker are the real ones, because how the finder joins them is the point.
"""
import logging

from pom.fault_recorder import FaultRecorder
from pom.listening import FragmentLevel, HeardSegment, SegmentFragmenter
from pom.listening.segment_fragmenter import Fragment
from pom.scoring import ConvoInterestTracker, HingeFinder, ScoreEffect, ScoredBy, ScoredFragment, ScoredSentence

QUIET = 2.0
LOUD = 9.0


class FakeListener:
    """Hears the segments it was given, then ends."""

    def __init__(self, segments):
        self.segments, self.closed, self.report = list(segments), 0, FaultRecorder()

    def hear(self):
        yield from self.segments

    def close(self):
        self.closed += 1


class FakeScorer:
    """Scores a text with the number it was told (3.0 if it was not told), and keeps a record of what it was asked."""

    def __init__(self, scores=None, scored_by=ScoredBy.MODEL):
        self.scores, self.scored_by, self.asked, self.report = dict(scores or {}), scored_by, [], FaultRecorder()
        self.fragments = []

    def score(self, fragment):
        self.fragments.append(fragment)
        self.asked.append((fragment.text, fragment.heard_at))
        return ScoredFragment(fragment, self.scores.get(fragment.text, 3.0), self.scored_by)

    def score_text(self, text, heard_at=0.0):
        return self.score(Fragment(text, FragmentLevel.SENTENCE, heard_at))


class FixedFragmenter:
    """Cuts every text into the fragments it was given."""

    def __init__(self, fragments):
        self.fragments, self.report = fragments, FaultRecorder()

    def split(self, text, heard_at=0.0):
        return list(self.fragments)


def segment(words="", ends_at=10.0, kind="speech", ambient=()):
    return HeardSegment(words if words else "", kind if words else "", tuple(ambient), ends_at)


def tracker(min_scores=3, cooldown=60.0):
    return ConvoInterestTracker(percentile=100.0, window_seconds=300.0, min_scores=min_scores, cooldown_seconds=cooldown)


def finder(segments, scores=None, consider_ambient_sounds=False, fragmenter=None, scored_by=ScoredBy.MODEL, **tracking):
    listener, scorer = FakeListener(segments), FakeScorer(scores, scored_by)
    made = HingeFinder(listener, fragmenter or SegmentFragmenter(), scorer, tracker(**tracking), consider_ambient_sounds)
    return made, listener, scorer


def found(made):
    return list(made.find())


class TestWhatIsYielded:
    def test_each_sentence_is_yielded_in_order_with_its_text_and_time(self):
        made, _, _ = finder([segment("It is late. The dog barks.", ends_at=12.5)])
        sentences = found(made)
        assert [(s.text, s.heard_at) for s in sentences] == [("It is late.", 12.5), ("The dog barks.", 12.5)]
        assert all(isinstance(s, ScoredSentence) for s in sentences)

    def test_the_kind_of_the_words_is_passed_on(self):
        made, _, _ = finder([segment("Hello there.", kind="speech"), segment("Sing along.", ends_at=20.0, kind="lyrics")])
        assert [s.kind for s in found(made)] == ["speech", "lyrics"]

    def test_the_score_and_what_made_it_are_passed_on(self):
        made, _, _ = finder([segment("A strange line.")], scores={"A strange line.": 6.5})
        sentence = found(made)[0]
        assert sentence.score == 6.5 and sentence.scored_by is ScoredBy.MODEL

    def test_a_segment_with_nothing_in_it_yields_nothing(self):
        made, _, scorer = finder([segment()])
        assert found(made) == [] and scorer.asked == []

    def test_only_whole_sentences_are_scored(self):
        fragments = [Fragment("whole sentence here", FragmentLevel.SENTENCE), Fragment("a clause", FragmentLevel.CLAUSE),
                     Fragment("a phrase", FragmentLevel.PHRASE)]
        made, _, scorer = finder([segment("whole sentence here, a clause")], fragmenter=FixedFragmenter(fragments))
        assert [s.text for s in found(made)] == ["whole sentence here"]
        assert [text for text, _ in scorer.asked] == ["whole sentence here"]

    def test_the_scorer_is_given_the_fragment_the_fragmenter_cut(self):
        fragment = Fragment("whole sentence here", FragmentLevel.SENTENCE, heard_at=5.0, sentence=2)
        made, _, scorer = finder([segment("whole sentence here")], fragmenter=FixedFragmenter([fragment]))
        found(made)
        assert scorer.fragments == [fragment] and scorer.fragments[0] is fragment

    def test_a_segment_with_no_words_is_not_cut_or_scored_at_all(self):
        fragment = Fragment("should not appear", FragmentLevel.SENTENCE)
        made, _, scorer = finder([segment("")], fragmenter=FixedFragmenter([fragment]))
        assert found(made) == [] and scorer.asked == []

    def test_what_made_the_score_is_passed_on(self):
        made, _, _ = finder([segment("Hello there.")], scored_by=ScoredBy.FRAGMENT_FILTER)
        assert found(made)[0].scored_by is ScoredBy.FRAGMENT_FILTER

    def test_a_scored_sentence_cannot_be_changed(self):
        made, _, _ = finder([segment("Hello there.")])
        sentence = found(made)[0]
        try:
            sentence.text = "other"
        except AttributeError:
            return
        raise AssertionError("a ScoredSentence should be frozen")

    def test_the_scorer_is_given_the_time_the_words_ended(self):
        made, _, scorer = finder([segment("Hello there.", ends_at=33.0)])
        found(made)
        assert scorer.asked == [("Hello there.", 33.0)]

    def test_the_finding_ends_when_the_sound_ends(self):
        made, _, _ = finder([])
        assert found(made) == []


class TestTheTrigger:
    SEGMENTS = [segment("One.", 10.0), segment("Two.", 20.0), segment("Three.", 30.0), segment("Four.", 40.0)]

    def scored(self, **extra):
        scores = {"One.": QUIET, "Two.": QUIET, "Three.": QUIET, "Four.": LOUD}
        made, _, _ = finder(self.SEGMENTS, scores=scores, **extra)
        return found(made)

    def test_early_scores_are_counted_and_a_high_one_after_them_is_the_hinge(self):
        sentences = self.scored()
        assert [s.effect for s in sentences] == [ScoreEffect.COUNTED] * 3 + [ScoreEffect.TRIGGERED]
        assert [s.is_hinge for s in sentences] == [False, False, False, True]

    def test_the_hinge_is_the_sentence_word_for_word(self):
        assert [s.text for s in self.scored() if s.is_hinge] == ["Four."]

    def test_the_bar_and_the_window_are_as_they_stood_before_the_score_joined(self):
        last = self.scored()[-1]
        assert last.window_size == 3 and last.trigger_level == QUIET

    def test_the_first_score_meets_an_empty_window_and_no_bar(self):
        first = self.scored()[0]
        assert first.window_size == 0 and first.trigger_level is None

    def test_the_count_includes_the_score_just_made(self):
        assert [s.counted for s in self.scored()] == [1, 2, 3, 4]

    def test_a_second_high_score_inside_the_cooldown_is_not_a_hinge(self):
        segments = self.SEGMENTS + [segment("Five.", 50.0)]
        made, _, _ = finder(segments, scores={"One.": QUIET, "Two.": QUIET, "Three.": QUIET, "Four.": LOUD, "Five.": LOUD + 0.5})
        last = found(made)[-1]
        assert last.effect is ScoreEffect.COOLING_DOWN and not last.is_hinge

    def test_a_high_score_after_the_cooldown_is_a_hinge_again(self):
        segments = self.SEGMENTS + [segment("Five.", 200.0)]
        made, _, _ = finder(segments, scores={"One.": QUIET, "Two.": QUIET, "Three.": QUIET, "Four.": LOUD, "Five.": LOUD + 0.5})
        assert found(made)[-1].is_hinge

    def test_a_sentence_that_could_not_be_scored_is_ignored_by_the_trigger(self):
        made, _, _ = finder([segment("Hello there.")], scored_by=ScoredBy.UNSCORED, scores={"Hello there.": None})
        sentence = found(made)[0]
        assert sentence.score is None and sentence.effect is ScoreEffect.IGNORED and not sentence.is_hinge

    def test_nothing_triggers_before_the_trigger_has_seen_enough_scores(self):
        made, _, _ = finder([segment("One.", 10.0), segment("Two.", 20.0)], scores={"One.": QUIET, "Two.": LOUD})
        assert not any(s.is_hinge for s in found(made))


class TestAmbientSounds:
    CAPTIONS = segment("", ends_at=15.0, ambient=("(dog barks)",))

    def test_a_caption_is_yielded_but_not_scored_by_default(self):
        made, _, scorer = finder([self.CAPTIONS])
        sentence = found(made)[0]
        assert (sentence.text, sentence.kind) == ("(dog barks)", "ambient")
        assert sentence.score is None and sentence.scored_by is ScoredBy.UNSCORED and sentence.effect is None
        assert scorer.asked == []

    def test_a_caption_that_is_not_considered_is_never_seen_by_the_trigger(self):
        made, _, _ = finder([self.CAPTIONS, segment("Hello there.", 25.0)])
        sentences = found(made)
        assert sentences[0].counted == 0 and sentences[1].counted == 1

    def test_a_caption_that_is_not_considered_is_never_a_hinge(self):
        made, _, _ = finder([self.CAPTIONS], scores={"(dog barks)": LOUD})
        assert not found(made)[0].is_hinge

    def test_a_considered_caption_is_scored_at_the_time_it_was_heard(self):
        made, _, scorer = finder([self.CAPTIONS], scores={"(dog barks)": 4.0}, consider_ambient_sounds=True)
        sentence = found(made)[0]
        assert scorer.asked == [("(dog barks)", 15.0)]
        assert (sentence.kind, sentence.score, sentence.scored_by) == ("ambient", 4.0, ScoredBy.MODEL)
        assert sentence.effect is ScoreEffect.COUNTED and sentence.counted == 1

    def test_a_considered_caption_can_be_the_hinge(self):
        segments = [segment("One.", 10.0), segment("Two.", 20.0), segment("Three.", 30.0),
                    segment("", ends_at=40.0, ambient=("(a gunshot)",))]
        made, _, _ = finder(segments, scores={"One.": QUIET, "Two.": QUIET, "Three.": QUIET, "(a gunshot)": LOUD},
                            consider_ambient_sounds=True)
        hinges = [s for s in found(made) if s.is_hinge]
        assert [(h.text, h.kind) for h in hinges] == [("(a gunshot)", "ambient")]

    def test_captions_come_before_the_words_of_the_same_segment(self):
        made, _, _ = finder([segment("Hello there.", 10.0, ambient=("(door slams)",))])
        assert [s.text for s in found(made)] == ["(door slams)", "Hello there."]

    def test_every_caption_of_a_segment_is_yielded(self):
        made, _, _ = finder([segment("", 10.0, ambient=("(door slams)", "(music)"))])
        assert [s.text for s in found(made)] == ["(door slams)", "(music)"]

    def test_the_words_are_scored_whether_or_not_sounds_are_considered(self):
        for flag in (False, True):
            made, _, scorer = finder([segment("Hello there.")], consider_ambient_sounds=flag)
            assert [s.score for s in found(made)] == [3.0]


class TestClosingAndReporting:
    def test_closing_closes_the_listener(self):
        made, listener, _ = finder([])
        made.close()
        assert listener.closed == 1

    def test_closing_twice_is_safe(self):
        made, listener, _ = finder([])
        made.close()
        made.close()
        assert listener.closed == 2

    def test_the_report_lines_gather_the_four_parts_problems(self):
        listener, scorer = FakeListener([]), FakeScorer()
        fragmenter, the_tracker = SegmentFragmenter(), tracker()
        listener.report.warn("listener trouble")
        scorer.report.warn("scorer trouble")
        fragmenter.report.warn("fragmenter trouble")
        the_tracker.report.warn("tracker trouble")
        lines = HingeFinder(listener, fragmenter, scorer, the_tracker).report_lines()
        for word in ("listener", "fragmenter", "scorer", "tracker"):
            assert any(f"{word} trouble" in line for line in lines)

    def test_there_is_nothing_to_report_when_all_went_well(self):
        made, _, _ = finder([segment("Hello there.")])
        found(made)
        assert made.report_lines() == []


class TestNothingSaidInTheRoomIsKept:
    def test_what_was_heard_reaches_neither_the_log_nor_the_report(self, caplog):
        caplog.set_level(logging.DEBUG)
        made, _, _ = finder([segment("My secret is the neighbor.", 10.0, ambient=("(a secret gunshot)",))],
                            consider_ambient_sounds=True)
        found(made)
        text = caplog.text + "\n".join(made.report_lines())
        assert "secret" not in text and "neighbor" not in text and "gunshot" not in text
