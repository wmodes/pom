"""Tests for pom.listening.rolling_cutter, with a stand-in for whisper that answers with what each test tells it to."""
import threading

import pytest

from pom.listening.audio_format import BYTES_PER_SECOND
from pom.listening.rolling_cutter import (DEFAULT_HOLD, DEFAULT_MAX_BUFFER, DEFAULT_REWIND, DEFAULT_STEP, RollingCutter,
                                          RollingCutterError, join_unfinished)
from pom.listening.whisper_server_client import Segment


class FakeWhisper:
    """Answers each call with the next reply in the list, and remembers what it was asked (offset, seconds, vad)."""

    def __init__(self, *replies, failing=()):
        self.replies, self.calls, self.pcms, self.failing, self.error = list(replies), [], [], set(failing), None

    def segments(self, pcm, offset_s=0.0, vad=True):
        number = len(self.calls)
        self.calls.append((offset_s, len(pcm) / BYTES_PER_SECOND, vad))
        self.pcms.append(pcm)
        if number in self.failing:
            self.error = "URLError"
            return []
        self.error = None
        return list(self.replies[number]) if number < len(self.replies) else []


def audio(seconds: float) -> bytes:
    return bytes(int(seconds * BYTES_PER_SECOND))


def seg(start, end, text):
    return Segment(start, end, text)


def numbered_seconds(first: int, count: int) -> bytes:
    """Audio in which every sample of stream second n has the value n + 1, so what whisper is given can be identified."""
    return b"".join(bytes([n + 1, 0]) * (BYTES_PER_SECOND // 2) for n in range(first, first + count))


class TestWhenARoundIsDue:
    def test_nothing_is_due_before_a_step_of_audio(self):
        cutter = RollingCutter(FakeWhisper())
        cutter.feed(audio(4.99))
        assert not cutter.due()

    def test_a_round_is_due_after_a_step(self):
        cutter = RollingCutter(FakeWhisper())
        cutter.feed(audio(5.0))
        assert cutter.due()

    def test_the_next_round_is_a_step_after_the_last(self):
        cutter = RollingCutter(FakeWhisper())
        cutter.feed(audio(5.0))
        cutter.cut()
        assert not cutter.due()
        cutter.feed(audio(4.99))
        assert not cutter.due()
        cutter.feed(audio(0.01))
        assert cutter.due()

    def test_the_step_is_a_setting(self):
        cutter = RollingCutter(FakeWhisper(), step=2.0)
        cutter.feed(audio(2.0))
        assert cutter.due()

    def test_total_seconds_counts_everything_received(self):
        cutter = RollingCutter(FakeWhisper())
        cutter.feed(audio(1.5))
        cutter.feed(audio(0.5))
        assert cutter.total_seconds == pytest.approx(2.0)

    def test_total_seconds_still_counts_audio_that_was_trimmed_away(self):
        cutter = RollingCutter(FakeWhisper([seg(0.5, 3.0, "Hello there.")]))
        cutter.feed(audio(5.0))
        cutter.cut()
        assert cutter.total_seconds == pytest.approx(5.0)


class TestKeepingFinishedSegments:
    def test_a_segment_that_ended_well_before_the_end_is_kept(self):
        cutter = RollingCutter(FakeWhisper([seg(0.5, 3.0, "Hello there.")]))
        cutter.feed(audio(5.0))
        assert cutter.cut() == [seg(0.5, 3.0, "Hello there.")]

    def test_the_window_whisper_is_asked_about_is_from_the_start_to_the_step(self):
        whisper = FakeWhisper()
        cutter = RollingCutter(whisper)
        cutter.feed(audio(6.0))
        cutter.cut()
        assert whisper.calls == [(0.0, 5.0, True)]                         # the sixth second waits for the next round

    def test_a_segment_ending_within_the_hold_waits_for_the_next_round(self):
        late = seg(3.5, 4.8, "A late one.")
        whisper = FakeWhisper([late], [late])
        cutter = RollingCutter(whisper)
        cutter.feed(audio(5.0))
        assert cutter.cut() == []
        cutter.feed(audio(5.0))
        assert cutter.cut() == [late]
        assert whisper.calls[1][:2] == (0.0, 10.0)                         # the audio was kept for the second round

    def test_a_segment_ending_exactly_at_the_hold_is_kept(self):
        cutter = RollingCutter(FakeWhisper([seg(0.0, 3.0, "Right at the line.")]))
        cutter.feed(audio(5.0))
        assert len(cutter.cut()) == 1

    def test_the_hold_is_a_setting(self):
        cutter = RollingCutter(FakeWhisper([seg(0.5, 4.5, "Close to the end.")]), hold=0.5)
        cutter.feed(audio(5.0))
        assert len(cutter.cut()) == 1

    def test_the_next_round_starts_where_the_last_kept_segment_ended(self):
        whisper = FakeWhisper([seg(0.5, 3.0, "Hello there.")], [])
        cutter = RollingCutter(whisper)
        cutter.feed(audio(5.0))
        cutter.cut()
        cutter.feed(audio(5.0))
        cutter.cut()
        assert whisper.calls[1][:2] == (3.0, 7.0)

    def test_the_final_round_keeps_everything_whisper_found(self):
        late = seg(3.5, 4.8, "Late.")
        cutter = RollingCutter(FakeWhisper([late]))
        cutter.feed(audio(5.0))
        assert cutter.cut(final=True) == [late]

    def test_the_final_round_uses_all_the_audio_not_just_the_step(self):
        whisper = FakeWhisper()
        cutter = RollingCutter(whisper)
        cutter.feed(audio(7.3))
        cutter.cut(final=True)
        assert whisper.calls[0][1] == pytest.approx(7.3)

    def test_vad_is_passed_on(self):
        for vad in (True, False):
            whisper = FakeWhisper()
            cutter = RollingCutter(whisper, vad=vad)
            cutter.feed(audio(5.0))
            cutter.cut()
            assert whisper.calls[0][2] is vad


class TestJoiningUnfinishedSegments:
    def test_a_segment_without_a_sentence_end_is_joined_to_the_next(self):
        found = [seg(0.5, 1.5, "Hello there"), seg(1.6, 3.0, "friend.")]
        assert join_unfinished(found) == [seg(0.5, 3.0, "Hello there friend.")]

    def test_a_finished_segment_is_left_alone(self):
        found = [seg(0.0, 1.0, "One."), seg(2.0, 3.0, "Two?"), seg(4.0, 5.0, "Three!")]
        assert join_unfinished(found) == found

    def test_a_chain_of_unfinished_segments_becomes_one(self):
        found = [seg(0.0, 1.0, "a"), seg(1.0, 2.0, "b"), seg(2.0, 3.0, "c.")]
        assert join_unfinished(found) == [seg(0.0, 3.0, "a b c.")]

    @pytest.mark.parametrize("ending", ['."', ".'", ".)", ".]", "?\"", "!'"])
    def test_a_sentence_end_may_be_followed_by_a_closing_mark(self, ending):
        assert len(join_unfinished([seg(0, 1, "He said hi" + ending), seg(2, 3, "Next.")])) == 2

    def test_a_joined_segment_may_grow_to_exactly_the_limit(self):
        assert join_unfinished([seg(0.0, 20.0, "a"), seg(21.0, 25.0, "b.")]) == [seg(0.0, 25.0, "a b.")]

    def test_a_joined_segment_does_not_grow_past_the_limit(self):
        found = [seg(0.0, 20.0, "a"), seg(21.0, 30.0, "b.")]
        assert join_unfinished(found) == found
        assert join_unfinished(found, longest=40.0) == [seg(0.0, 30.0, "a b.")]

    def test_nothing_gives_nothing(self):
        assert join_unfinished([]) == []

    def test_the_cutter_joins_what_whisper_split(self):
        cutter = RollingCutter(FakeWhisper([seg(0.5, 1.5, "Hello there"), seg(1.6, 3.0, "friend.")]))
        cutter.feed(audio(5.0))
        assert cutter.cut() == [seg(0.5, 3.0, "Hello there friend.")]


class TestWhatWhisperIsGiven:
    def test_the_first_round_gets_the_first_step_of_audio(self):
        whisper = FakeWhisper()
        cutter = RollingCutter(whisper)
        cutter.feed(numbered_seconds(0, 6))
        cutter.cut()
        assert whisper.pcms[0] == numbered_seconds(0, 5)

    def test_a_round_gets_audio_only_up_to_its_step_even_when_more_has_arrived(self):
        whisper = FakeWhisper([seg(0.5, 3.0, "A.")], [])
        cutter = RollingCutter(whisper)
        cutter.feed(numbered_seconds(0, 5))
        cutter.cut()
        cutter.feed(numbered_seconds(5, 6))                                # eleven seconds in all, the round is for ten
        cutter.cut()
        assert whisper.pcms[1] == numbered_seconds(3, 7)

    def test_whisper_is_never_given_half_a_sample(self):
        whisper = FakeWhisper()
        cutter = RollingCutter(whisper)
        cutter.feed(audio(5.0) + b"\x01")                                  # an odd byte on the end, as a pipe might leave
        cutter.cut(final=True)
        assert len(whisper.pcms[0]) == len(audio(5.0))

    def test_audio_before_the_last_kept_segment_is_let_go(self):
        cutter = RollingCutter(FakeWhisper([seg(0.5, 3.0, "A.")]))
        cutter.feed(numbered_seconds(0, 5))
        cutter.cut()
        assert cutter.recent(100.0) == numbered_seconds(3, 2)

    def test_after_a_kept_segment_the_next_round_starts_at_its_end(self):
        whisper = FakeWhisper([seg(0.5, 3.0, "A.")], [])
        cutter = RollingCutter(whisper)
        cutter.feed(numbered_seconds(0, 5))
        cutter.cut()
        cutter.feed(numbered_seconds(5, 5))
        cutter.cut()
        assert whisper.pcms[1] == numbered_seconds(3, 7)                   # seconds 3 to 9, nothing earlier

    def test_after_noise_the_next_round_starts_two_seconds_before_the_end(self):
        whisper = FakeWhisper([], [])
        cutter = RollingCutter(whisper)
        cutter.feed(numbered_seconds(0, 5))
        cutter.cut()
        cutter.feed(numbered_seconds(5, 5))
        cutter.cut()
        assert whisper.pcms[1] == numbered_seconds(3, 7)

    def test_with_a_rewind_the_next_round_starts_that_much_earlier(self):
        whisper = FakeWhisper([seg(0.5, 3.0, "A.")], [])
        cutter = RollingCutter(whisper, rewind=1.0)
        cutter.feed(numbered_seconds(0, 5))
        cutter.cut()
        cutter.feed(numbered_seconds(5, 5))
        cutter.cut()
        assert whisper.pcms[1] == numbered_seconds(2, 8)

    def test_a_rewind_longer_than_the_audio_so_far_starts_at_the_beginning(self):
        whisper = FakeWhisper([seg(0.5, 3.0, "A.")], [])
        cutter = RollingCutter(whisper, rewind=10.0)
        cutter.feed(numbered_seconds(0, 5))
        cutter.cut()
        cutter.feed(numbered_seconds(5, 5))
        cutter.cut()
        assert whisper.calls[1][:2] == (0.0, 10.0) and whisper.pcms[1] == numbered_seconds(0, 10)

    def test_the_rounds_after_a_failure_still_start_where_they_should(self):
        whisper = FakeWhisper([seg(0.5, 3.0, "A.")], [], [], failing=[1])
        cutter = RollingCutter(whisper)
        for first in (0, 5, 10):
            cutter.feed(numbered_seconds(first, 5))
            cutter.cut()
        assert whisper.pcms[2] == numbered_seconds(3, 12)                  # the failed round's audio was kept

    def test_the_last_of_several_kept_segments_decides_where_the_next_round_starts(self):
        whisper = FakeWhisper([seg(0.5, 1.5, "A."), seg(2.0, 3.0, "B.")], [])
        cutter = RollingCutter(whisper)
        cutter.feed(numbered_seconds(0, 5))
        assert [s.text for s in cutter.cut()] == ["A.", "B."]
        cutter.feed(numbered_seconds(5, 5))
        cutter.cut()
        assert whisper.calls[1][:2] == (3.0, 7.0)


class TestNoSpeech:
    def test_a_buffer_with_no_speech_is_trimmed_to_its_last_two_seconds(self):
        whisper = FakeWhisper([], [])
        cutter = RollingCutter(whisper)
        cutter.feed(audio(5.0))
        assert cutter.cut() == []
        cutter.feed(audio(5.0))
        cutter.cut()
        assert whisper.calls[1][:2] == (3.0, 7.0)

    def test_a_round_too_short_to_be_worth_asking_about_asks_nothing(self):
        whisper = FakeWhisper()
        cutter = RollingCutter(whisper)
        cutter.feed(audio(0.9))
        assert cutter.cut(final=True) == [] and whisper.calls == []

    def test_a_round_of_exactly_a_second_is_asked(self):
        whisper = FakeWhisper()
        cutter = RollingCutter(whisper)
        cutter.feed(audio(1.0))
        cutter.cut(final=True)
        assert len(whisper.calls) == 1


class TestAVeryLongThought:
    def test_a_thought_that_never_finishes_is_forced_out_at_the_buffer_limit(self):
        # whisper keeps hearing one segment that runs to the end of whatever it is given
        replies = [[seg(0.5, 5.0 * n, "A long thought.")] for n in range(1, 7)]
        cutter = RollingCutter(FakeWhisper(*replies))
        got = []
        for _ in range(6):
            cutter.feed(audio(5.0))
            got.append(cutter.cut())
        assert got[:5] == [[]] * 5 and got[5] == [seg(0.5, 30.0, "A long thought.")]
        assert cutter.forced == 1

    def test_at_the_limit_only_the_finished_segments_are_kept_when_there_are_some(self):
        late = seg(28.5, 29.9, "Held back.")
        cutter = RollingCutter(FakeWhisper([seg(1.0, 5.0, "Done."), late]), step=30.0)
        cutter.feed(audio(30.0))
        assert [s.text for s in cutter.cut()] == ["Done."] and cutter.forced == 0

    def test_the_limit_is_a_setting(self):
        replies = [[seg(0.5, 5.0 * n, "Long.")] for n in range(1, 4)]
        cutter = RollingCutter(FakeWhisper(*replies), max_buffer=10.0)
        results = []
        for _ in range(2):
            cutter.feed(audio(5.0))
            results.append(cutter.cut())
        assert results == [[], [seg(0.5, 10.0, "Long.")]] and cutter.forced == 1

    def test_nothing_is_forced_when_segments_finish_normally(self):
        cutter = RollingCutter(FakeWhisper([seg(0.5, 3.0, "Fine.")]))
        cutter.feed(audio(5.0))
        cutter.cut()
        assert cutter.forced == 0


class TestRewinding:
    def test_with_a_rewind_the_next_round_starts_before_the_last_segments_end(self):
        whisper = FakeWhisper([seg(0.5, 3.0, "First.")], [])
        cutter = RollingCutter(whisper, rewind=1.0)
        cutter.feed(audio(5.0))
        cutter.cut()
        cutter.feed(audio(5.0))
        cutter.cut()
        assert whisper.calls[1][:2] == (2.0, 8.0)

    def test_what_was_heard_again_in_the_rewind_is_dropped(self):
        whisper = FakeWhisper([seg(0.5, 3.0, "First."), seg(3.2, 4.0, "Second.")],
                              [seg(2.0, 3.0, "First."), seg(3.2, 4.0, "Second.")])
        cutter = RollingCutter(whisper, rewind=1.0)
        cutter.feed(audio(5.0))
        assert [s.text for s in cutter.cut()] == ["First."]
        cutter.feed(audio(5.0))
        assert [s.text for s in cutter.cut()] == ["Second."]

    def test_a_segment_ending_within_a_fifth_of_a_second_of_the_last_is_dropped(self):
        whisper = FakeWhisper([seg(0.5, 3.0, "First.")], [seg(2.5, 3.2, "Almost the same."), seg(4.0, 4.5, "New.")])
        cutter = RollingCutter(whisper)
        cutter.feed(audio(5.0))
        cutter.cut()
        cutter.feed(audio(5.0))
        assert [s.text for s in cutter.cut()] == ["New."]

    def test_without_a_rewind_the_default_is_zero(self):
        assert DEFAULT_REWIND == 0.0


class TestWhenWhisperDoesNotAnswer:
    def test_a_failed_round_gives_nothing_and_keeps_the_audio(self):
        whisper = FakeWhisper([], [seg(0.5, 3.0, "Hello.")], failing=[0])
        cutter = RollingCutter(whisper)
        cutter.feed(audio(5.0))
        assert cutter.cut() == []
        cutter.feed(audio(5.0))
        assert cutter.cut() == [seg(0.5, 3.0, "Hello.")]
        assert whisper.calls[1][:2] == (0.0, 10.0)

    def test_a_failed_round_is_not_asked_again_until_the_next_step(self):
        cutter = RollingCutter(FakeWhisper(failing=[0]))
        cutter.feed(audio(5.0))
        cutter.cut()
        assert not cutter.due()

    def test_a_client_that_raises_is_a_warning_not_a_crash(self):
        class Broken:
            error = None

            def segments(self, pcm, offset_s=0.0, vad=True):
                raise RuntimeError("a bug in the client")

        cutter = RollingCutter(Broken())
        cutter.feed(audio(5.0))
        assert cutter.cut() == []
        assert len(cutter.report.warnings) == 1 and "RuntimeError" in cutter.report.warnings[0]

    def test_a_failed_round_still_says_how_long_it_took(self):
        ticks = iter([10.0, 10.5])
        cutter = RollingCutter(FakeWhisper(failing=[0]), clock=lambda: next(ticks))
        cutter.feed(audio(5.0))
        cutter.cut()
        assert cutter.took == pytest.approx(0.5)


class TestTimingWhisper:
    def test_took_is_how_long_the_round_took(self):
        ticks = iter([100.0, 100.4])
        cutter = RollingCutter(FakeWhisper(), clock=lambda: next(ticks))
        cutter.feed(audio(5.0))
        cutter.cut()
        assert cutter.took == pytest.approx(0.4)

    def test_took_starts_at_zero(self):
        assert RollingCutter(FakeWhisper()).took == 0.0


class TestRecentAudio:
    def test_recent_gives_the_last_seconds(self):
        cutter = RollingCutter(FakeWhisper())
        cutter.feed(b"\x01\x00" * (BYTES_PER_SECOND // 2))
        cutter.feed(b"\x02\x00" * (BYTES_PER_SECOND // 2))
        last = cutter.recent(1.0)
        assert len(last) == BYTES_PER_SECOND and last[-2:] == b"\x02\x00"
        assert cutter.recent(0.5) == b"\x02\x00" * (BYTES_PER_SECOND // 4)

    def test_recent_gives_less_when_there_is_less(self):
        cutter = RollingCutter(FakeWhisper())
        cutter.feed(audio(2.0))
        assert len(cutter.recent(10.0)) == len(audio(2.0))

    @pytest.mark.parametrize("seconds", [0, -3, None, "5", float("nan")])
    def test_recent_of_nothing_is_nothing(self, seconds):
        cutter = RollingCutter(FakeWhisper())
        cutter.feed(audio(2.0))
        assert cutter.recent(seconds) == b""

    def test_recent_never_splits_a_sample(self):
        cutter = RollingCutter(FakeWhisper())
        cutter.feed(b"\x01\x00" * 100)
        assert len(cutter.recent(0.0001)) % 2 == 0 and len(cutter.recent(0.0001)) <= 4


class TestFeeding:
    @pytest.mark.parametrize("not_audio", [None, "text", 5, [1, 2], {}])
    def test_what_is_not_bytes_is_ignored(self, not_audio):
        cutter = RollingCutter(FakeWhisper())
        cutter.feed(not_audio)
        assert cutter.total_seconds == 0.0

    @pytest.mark.parametrize("make", [bytes, bytearray, memoryview])
    def test_any_kind_of_bytes_will_do(self, make):
        cutter = RollingCutter(FakeWhisper())
        cutter.feed(make(audio(1.0)))
        assert cutter.total_seconds == pytest.approx(1.0)

    def test_audio_can_be_fed_while_a_round_is_cut(self):
        whisper = FakeWhisper()
        cutter = RollingCutter(whisper)
        stop = threading.Event()

        def feeder():
            for _ in range(2000):
                cutter.feed(audio(0.03))
            stop.set()

        thread = threading.Thread(target=feeder)
        thread.start()
        cuts = 0
        while not stop.is_set() or cutter.due():
            if cutter.due():
                cutter.cut()
                cuts += 1
        thread.join()
        assert cutter.total_seconds == pytest.approx(60.0, abs=0.01) and cuts >= 11


class TestSettings:
    def test_the_defaults_are_the_known_good_ones(self):
        assert (DEFAULT_STEP, DEFAULT_HOLD, DEFAULT_REWIND, DEFAULT_MAX_BUFFER) == (5.0, 2.0, 0.0, 30.0)
        assert RollingCutter(FakeWhisper()).report.ok

    @pytest.mark.parametrize("name,bad", [("step", 0), ("step", -1), ("step", "5"), ("step", None), ("step", True),
                                          ("step", float("nan")), ("step", float("inf")),
                                          ("hold", -1), ("hold", "2"), ("hold", None), ("hold", True),
                                          ("rewind", -0.5), ("rewind", "0"), ("rewind", None), ("rewind", True),
                                          ("max_buffer", 0), ("max_buffer", -5), ("max_buffer", "30"), ("max_buffer", None)])
    def test_a_bad_setting_means_the_default_with_a_warning(self, name, bad):
        cutter = RollingCutter(FakeWhisper(), **{name: bad})
        assert len(cutter.report.warnings) == 1 and name in cutter.report.warnings[0]
        defaults = {"step": DEFAULT_STEP, "hold": DEFAULT_HOLD, "rewind": DEFAULT_REWIND, "max_buffer": DEFAULT_MAX_BUFFER}
        assert getattr(cutter, "_" + name) == defaults[name]

    @pytest.mark.parametrize("name,low", [("hold", 0), ("rewind", 0)])
    def test_zero_is_allowed_where_it_makes_sense(self, name, low):
        assert RollingCutter(FakeWhisper(), **{name: low}).report.ok

    @pytest.mark.parametrize("name,bad", [("step", 0), ("hold", -1), ("rewind", -1), ("max_buffer", 0)])
    def test_strict_mode_raises_on_a_bad_setting(self, name, bad):
        with pytest.raises(RollingCutterError, match=name):
            RollingCutter(FakeWhisper(), strict=True, **{name: bad})

    def test_strict_mode_raises_when_the_client_raises(self):
        class Broken:
            error = None

            def segments(self, *args, **kwargs):
                raise RuntimeError("bug")

        cutter = RollingCutter(Broken(), strict=True)
        cutter.feed(audio(5.0))
        with pytest.raises(RollingCutterError, match="RuntimeError"):
            cutter.cut()
