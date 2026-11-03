"""Tests for pom.conductor.hinge_finder: sound in, hinges out.

The Eavesdropper and the Scorer are small fakes (what they do is tested elsewhere); the TakeCutter and the
BarKeeper are the real ones, because how the finder joins them is the point.
"""
import logging

from pom.fault_recorder import FaultRecorder
from pom.listening import ClipLevel, Take, TakeCutter
from pom.listening.take_cutter import Clip
from pom.conductor import HingeFinder
from pom.qualifying import Attempt, BarKeeper, PercentileBar, Ruling
from pom.scoring import ScoredBy, ScoredClip

QUIET = 2.0
LOUD = 9.0


class FakeEavesdropper:
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
        self.clips = []

    def score(self, clip):
        self.clips.append(clip)
        self.asked.append((clip.text, clip.heard_at))
        return ScoredClip(clip, self.scores.get(clip.text, 3.0), self.scored_by)

    def score_text(self, text, heard_at=0.0):
        return self.score(Clip(text, ClipLevel.SENTENCE, heard_at))


class FixedTakeCutter:
    """Cuts every text into the clips it was given."""

    def __init__(self, clips):
        self.clips, self.report = clips, FaultRecorder()

    def split(self, text, heard_at=0.0):
        return list(self.clips)


def segment(words="", ends_at=10.0, kind="speech", room_tone=()):
    return Take(words if words else "", kind if words else "", tuple(room_tone), ends_at)


def bar_keeper(min_scores=3, cooldown=60.0):
    """A BarKeeper over a PercentileBar whose starting line is out of reach (1000), so nothing qualifies until `min_scores` scores are heard."""
    return BarKeeper(PercentileBar(100.0, 300.0, min_scores, 1000.0), cooldown)


def finder(segments, scores=None, score_room_tone=False, take_cutter=None, scored_by=ScoredBy.MODEL, **tracking):
    eavesdropper, scorer = FakeEavesdropper(segments), FakeScorer(scores, scored_by)
    made = HingeFinder(eavesdropper, take_cutter or TakeCutter(), scorer, bar_keeper(**tracking), score_room_tone)
    return made, eavesdropper, scorer


def found(made):
    return list(made.find())


class TestWhatIsYielded:
    def test_each_sentence_is_yielded_in_order_with_its_text_and_time(self):
        made, _, _ = finder([segment("It is late. The dog barks.", ends_at=12.5)])
        sentences = found(made)
        assert [(s.text, s.heard_at) for s in sentences] == [("It is late.", 12.5), ("The dog barks.", 12.5)]
        assert all(isinstance(s, Attempt) for s in sentences)

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
        clips = [Clip("whole sentence here", ClipLevel.SENTENCE), Clip("a clause", ClipLevel.CLAUSE),
                     Clip("a phrase", ClipLevel.PHRASE)]
        made, _, scorer = finder([segment("whole sentence here, a clause")], take_cutter=FixedTakeCutter(clips))
        assert [s.text for s in found(made)] == ["whole sentence here"]
        assert [text for text, _ in scorer.asked] == ["whole sentence here"]

    def test_the_scorer_is_given_the_clip_the_take_cutter_cut(self):
        clip = Clip("whole sentence here", ClipLevel.SENTENCE, heard_at=5.0, sentence=2)
        made, _, scorer = finder([segment("whole sentence here")], take_cutter=FixedTakeCutter([clip]))
        found(made)
        assert scorer.clips == [clip] and scorer.clips[0] is clip

    def test_a_segment_with_no_words_is_not_cut_or_scored_at_all(self):
        clip = Clip("should not appear", ClipLevel.SENTENCE)
        made, _, scorer = finder([segment("")], take_cutter=FixedTakeCutter([clip]))
        assert found(made) == [] and scorer.asked == []

    def test_what_made_the_score_is_passed_on(self):
        made, _, _ = finder([segment("Hello there.")], scored_by=ScoredBy.CLIP_FILTER)
        assert found(made)[0].scored_by is ScoredBy.CLIP_FILTER

    def test_an_attempt_cannot_be_changed(self):
        made, _, _ = finder([segment("Hello there.")])
        sentence = found(made)[0]
        try:
            sentence.text = "other"
        except AttributeError:
            return
        raise AssertionError("an Attempt should be frozen")

    def test_the_scorer_is_given_the_time_the_words_ended(self):
        made, _, scorer = finder([segment("Hello there.", ends_at=33.0)])
        found(made)
        assert scorer.asked == [("Hello there.", 33.0)]

    def test_the_finding_ends_when_the_sound_ends(self):
        made, _, _ = finder([])
        assert found(made) == []


class TestQualifying:
    SEGMENTS = [segment("One.", 10.0), segment("Two.", 20.0), segment("Three.", 30.0), segment("Four.", 40.0)]

    def scored(self, **extra):
        scores = {"One.": QUIET, "Two.": QUIET, "Three.": QUIET, "Four.": LOUD}
        made, _, _ = finder(self.SEGMENTS, scores=scores, **extra)
        return found(made)

    def test_early_scores_are_counted_and_a_high_one_after_them_is_the_hinge(self):
        sentences = self.scored()
        assert [s.ruling for s in sentences] == [Ruling.DID_NOT_QUALIFY] * 3 + [Ruling.QUALIFIED]
        assert [s.is_hinge for s in sentences] == [False, False, False, True]

    def test_the_hinge_is_the_sentence_word_for_word(self):
        assert [s.text for s in self.scored() if s.is_hinge] == ["Four."]

    def test_the_bar_and_the_window_are_as_they_stood_before_the_score_joined(self):
        last = self.scored()[-1]
        assert last.window_size == 3 and last.bar == QUIET

    def test_the_first_score_meets_an_empty_window_and_the_starting_line(self):
        first = self.scored()[0]
        assert first.window_size == 0 and first.bar == 1000.0

    def test_the_count_includes_the_score_just_made(self):
        assert [s.counted for s in self.scored()] == [1, 2, 3, 4]

    def test_a_second_high_score_inside_the_cooldown_is_not_a_hinge(self):
        segments = self.SEGMENTS + [segment("Five.", 50.0)]
        made, _, _ = finder(segments, scores={"One.": QUIET, "Two.": QUIET, "Three.": QUIET, "Four.": LOUD, "Five.": LOUD + 0.5})
        last = found(made)[-1]
        assert last.ruling is Ruling.HELD and not last.is_hinge

    def test_a_high_score_after_the_cooldown_is_a_hinge_again(self):
        segments = self.SEGMENTS + [segment("Five.", 200.0)]
        made, _, _ = finder(segments, scores={"One.": QUIET, "Two.": QUIET, "Three.": QUIET, "Four.": LOUD, "Five.": LOUD + 0.5})
        assert found(made)[-1].is_hinge

    def test_a_sentence_that_could_not_be_scored_is_ignored_by_the_bar_keeper(self):
        made, _, _ = finder([segment("Hello there.")], scored_by=ScoredBy.UNSCORED, scores={"Hello there.": None})
        sentence = found(made)[0]
        assert sentence.score is None and sentence.ruling is Ruling.NO_MARK and not sentence.is_hinge

    def test_nothing_qualifies_before_the_bar_keeper_has_seen_enough_scores(self):
        made, _, _ = finder([segment("One.", 10.0), segment("Two.", 20.0)], scores={"One.": QUIET, "Two.": LOUD})
        assert not any(s.is_hinge for s in found(made))


class TestRoomToneSounds:
    CAPTIONS = segment("", ends_at=15.0, room_tone=("(dog barks)",))

    def test_a_caption_is_yielded_but_not_scored_by_default(self):
        made, _, scorer = finder([self.CAPTIONS])
        sentence = found(made)[0]
        assert (sentence.text, sentence.kind) == ("(dog barks)", "room_tone")
        assert sentence.score is None and sentence.scored_by is ScoredBy.UNSCORED and sentence.ruling is None
        assert scorer.asked == []

    def test_a_caption_that_is_not_considered_is_never_seen_by_the_bar_keeper(self):
        made, _, _ = finder([self.CAPTIONS, segment("Hello there.", 25.0)])
        sentences = found(made)
        assert sentences[0].counted == 0 and sentences[1].counted == 1

    def test_a_caption_that_is_not_considered_is_never_a_hinge(self):
        made, _, _ = finder([self.CAPTIONS], scores={"(dog barks)": LOUD})
        assert not found(made)[0].is_hinge

    def test_a_considered_caption_is_scored_at_the_time_it_was_heard(self):
        made, _, scorer = finder([self.CAPTIONS], scores={"(dog barks)": 4.0}, score_room_tone=True)
        sentence = found(made)[0]
        assert scorer.asked == [("(dog barks)", 15.0)]
        assert (sentence.kind, sentence.score, sentence.scored_by) == ("room_tone", 4.0, ScoredBy.MODEL)
        assert sentence.ruling is Ruling.DID_NOT_QUALIFY and sentence.counted == 1

    def test_a_considered_caption_can_be_the_hinge(self):
        segments = [segment("One.", 10.0), segment("Two.", 20.0), segment("Three.", 30.0),
                    segment("", ends_at=40.0, room_tone=("(a gunshot)",))]
        made, _, _ = finder(segments, scores={"One.": QUIET, "Two.": QUIET, "Three.": QUIET, "(a gunshot)": LOUD},
                            score_room_tone=True)
        hinges = [s for s in found(made) if s.is_hinge]
        assert [(h.text, h.kind) for h in hinges] == [("(a gunshot)", "room_tone")]

    def test_captions_come_before_the_words_of_the_same_segment(self):
        made, _, _ = finder([segment("Hello there.", 10.0, room_tone=("(door slams)",))])
        assert [s.text for s in found(made)] == ["(door slams)", "Hello there."]

    def test_every_caption_of_a_segment_is_yielded(self):
        made, _, _ = finder([segment("", 10.0, room_tone=("(door slams)", "(music)"))])
        assert [s.text for s in found(made)] == ["(door slams)", "(music)"]

    def test_the_words_are_scored_whether_or_not_sounds_are_considered(self):
        for flag in (False, True):
            made, _, scorer = finder([segment("Hello there.")], score_room_tone=flag)
            assert [s.score for s in found(made)] == [3.0]


class TestClosingAndReporting:
    def test_closing_closes_the_eavesdropper(self):
        made, eavesdropper, _ = finder([])
        made.close()
        assert eavesdropper.closed == 1

    def test_closing_twice_is_safe(self):
        made, eavesdropper, _ = finder([])
        made.close()
        made.close()
        assert eavesdropper.closed == 2

    def test_the_report_lines_gather_the_four_parts_problems(self):
        eavesdropper, scorer = FakeEavesdropper([]), FakeScorer()
        take_cutter, the_bar_keeper = TakeCutter(), bar_keeper()
        eavesdropper.report.warn("eavesdropper trouble")
        scorer.report.warn("scorer trouble")
        take_cutter.report.warn("take cutter trouble")
        the_bar_keeper.report.warn("BarKeeper trouble")
        lines = HingeFinder(eavesdropper, take_cutter, scorer, the_bar_keeper).report_lines()
        for word in ("eavesdropper", "take cutter", "scorer", "BarKeeper"):
            assert any(f"{word} trouble" in line for line in lines)

    def test_there_is_nothing_to_report_when_all_went_well(self):
        made, _, _ = finder([segment("Hello there.")])
        found(made)
        assert made.report_lines() == []


class TestNothingSaidInTheRoomIsKept:
    def test_what_was_heard_reaches_neither_the_log_nor_the_report(self, caplog):
        caplog.set_level(logging.DEBUG)
        made, _, _ = finder([segment("My secret is the neighbor.", 10.0, room_tone=("(a secret gunshot)",))],
                            score_room_tone=True)
        found(made)
        text = caplog.text + "\n".join(made.report_lines())
        assert "secret" not in text and "neighbor" not in text and "gunshot" not in text
