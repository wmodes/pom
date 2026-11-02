"""Tests for pom.listening.eavesdropper, with a scripted audio source and a stand-in for whisper."""
import logging

import pytest

from pom.listening.audio_format import BYTES_PER_SECOND, FRAME_BYTES, FRAME_SECONDS
from pom.listening.eavesdropper import (DEFAULT_ROOM_TONE_MEMORY, DEFAULT_ROOM_TONE_PASS_EVERY, DEFAULT_ROOM_TONE_PASS_SECONDS, Take,
                                    Eavesdropper, EavesdropperError)
from pom.listening.rolling_cutter import RollingCutter, RollingCutterError
from pom.listening.whisper_model_client import Segment

NOTE = "♪"


class ScriptedSource:
    """Gives out `seconds` of silence in frames, then ends. Remembers whether it was closed."""

    def __init__(self, seconds):
        self.count, self.closed, self.closes = round(seconds / FRAME_SECONDS), False, 0

    def frames(self):
        for _ in range(self.count):
            if self.closed:
                return
            yield bytes(FRAME_BYTES)

    def close(self):
        self.closed = True
        self.closes += 1


class FakeWhisper:
    """`segments` answers each round with the next reply; `text` (the room-tone pass) answers from `texts`."""

    def __init__(self, *rounds, texts=(), failing=()):
        self.rounds, self.texts, self.failing = list(rounds), list(texts), set(failing)
        self.calls, self.text_calls, self.error = [], [], None

    def segments(self, pcm, offset_s=0.0, vad=True):
        number = len(self.calls)
        self.calls.append((offset_s, len(pcm) / BYTES_PER_SECOND))
        if number in self.failing:
            self.error = "URLError"
            return []
        self.error = None
        return list(self.rounds[number]) if number < len(self.rounds) else []

    def text(self, pcm, vad=True):
        self.text_calls.append((len(pcm) / BYTES_PER_SECOND, vad))
        number = len(self.text_calls) - 1
        return self.texts[number] if number < len(self.texts) else ""


def seg(start, end, text):
    return Segment(start, end, text)


def hear(eavesdropper):
    return list(eavesdropper.hear())


def make(seconds, *rounds, texts=(), failing=(), **options):
    source, whisper = ScriptedSource(seconds), FakeWhisper(*rounds, texts=texts, failing=failing)
    return Eavesdropper(source, whisper, **options), source, whisper


class TestWhatIsHeard:
    def test_speech_comes_out_with_where_it_ended(self):
        eavesdropper, _, _ = make(12, [seg(0.5, 3.0, "Hello there.")], [seg(4.0, 6.5, "How are you?")])
        assert hear(eavesdropper) == [Take("Hello there.", "speech", (), 3.0),
                                  Take("How are you?", "speech", (), 6.5)]

    def test_rounds_happen_every_step_and_once_more_at_the_end(self):
        eavesdropper, _, whisper = make(12.0)
        hear(eavesdropper)
        assert whisper.calls == [(0.0, pytest.approx(5.0)), (3.0, pytest.approx(7.0)), (8.0, pytest.approx(4.0))]
        # at 5 s, at 10 s, and the last of the 12 s at the end; a quiet buffer is trimmed to its last two seconds each time

    def test_the_audio_is_cut_as_the_cutter_cuts_it(self):
        eavesdropper, _, whisper = make(12, [seg(0.5, 3.0, "Hello there.")], [])
        hear(eavesdropper)
        assert whisper.calls[0] == (0.0, 5.0) and whisper.calls[1] == (3.0, 7.0)

    def test_what_whisper_had_not_yet_finished_comes_out_at_the_end(self):
        late = seg(3.5, 4.8, "Just as it ended.")
        eavesdropper, _, _ = make(5.0, [late], [late])
        assert [h.words for h in hear(eavesdropper)] == ["Just as it ended."]

    def test_silence_gives_nothing(self):
        eavesdropper, _, _ = make(12)
        assert hear(eavesdropper) == []

    def test_nothing_here_marks_give_nothing(self):
        eavesdropper, _, _ = make(12, [seg(0.5, 3.0, "[BLANK_AUDIO]")])
        assert hear(eavesdropper) == []

    def test_a_source_that_gives_no_sound_gives_nothing_and_asks_nothing(self):
        eavesdropper, source, whisper = make(0)
        assert hear(eavesdropper) == [] and whisper.calls == []


class TestRepeats:
    def test_the_same_words_twice_in_a_row_are_heard_once(self):
        eavesdropper, _, _ = make(12, [seg(0.5, 3.0, "Thank you.")], [seg(4.0, 6.0, "Thank you.")])
        assert [h.words for h in hear(eavesdropper)] == ["Thank you."]

    def test_the_same_words_after_other_words_are_heard_again(self):
        eavesdropper, _, _ = make(17, [seg(0.5, 3.0, "Yes.")], [seg(4.0, 6.0, "No.")], [seg(9.0, 11.0, "Yes.")])
        assert [h.words for h in hear(eavesdropper)] == ["Yes.", "No.", "Yes."]

    def test_the_same_caption_twice_in_a_row_is_reported_once(self):
        eavesdropper, _, _ = make(12, [seg(0.5, 3.0, "(dog barks)")], [seg(4.0, 6.0, "(dog barks)")])
        assert hear(eavesdropper) == [Take("", "", ("(dog barks)",), 3.0)]

    def test_a_caption_that_comes_back_after_another_is_reported_again(self):
        eavesdropper, _, _ = make(17, [seg(0.5, 3.0, "(dog barks)")], [seg(4.0, 6.0, "(door slams)")],
                              [seg(9.0, 11.0, "(dog barks)")])
        assert [h.room_tone for h in hear(eavesdropper)] == [("(dog barks)",), ("(door slams)",), ("(dog barks)",)]

    def test_words_and_captions_are_judged_separately(self):
        eavesdropper, _, _ = make(12, [seg(0.5, 3.0, "(dog barks) Hello.")], [seg(4.0, 6.0, "(door slams) Hello.")])
        got = hear(eavesdropper)
        assert got == [Take("Hello.", "speech", ("(dog barks)",), 3.0), Take("", "", ("(door slams)",), 6.0)]


class TestCaptionsAndLyrics:
    def test_captions_are_kept_apart_from_the_words(self):
        eavesdropper, _, _ = make(12, [seg(0.5, 3.0, "(dog barks) Hello there.")])
        assert hear(eavesdropper) == [Take("Hello there.", "speech", ("(dog barks)",), 3.0)]

    def test_a_caption_alone_is_reported_with_no_words(self):
        eavesdropper, _, _ = make(12, [seg(0.5, 3.0, "[Music]")])
        assert hear(eavesdropper) == [Take("", "", ("[Music]",), 3.0)]

    def test_lyrics_are_words_with_the_music_symbols_taken_out(self):
        eavesdropper, _, _ = make(12, [seg(0.5, 3.0, f"{NOTE} la la la {NOTE}")])
        assert hear(eavesdropper) == [Take("la la la", "lyrics", (), 3.0)]

    def test_recent_captions_are_remembered_oldest_first(self):
        eavesdropper, _, _ = make(17, [seg(0.5, 3.0, "(dog barks)")], [seg(4.0, 6.0, "(door slams)")], [seg(9.0, 11.0, "[Music]")])
        hear(eavesdropper)
        assert eavesdropper.recent_room_tone == ("(dog barks)", "(door slams)", "[Music]")

    def test_only_the_last_few_captions_are_remembered(self):
        rounds = [[seg(5.0 * n + 0.5, 5.0 * n + 2.0, f"(sound {n})")] for n in range(5)]
        eavesdropper, _, _ = make(27, *rounds, room_tone_memory=3)
        hear(eavesdropper)
        assert eavesdropper.recent_room_tone == ("(sound 2)", "(sound 3)", "(sound 4)")

    def test_a_caption_repeated_straight_away_is_remembered_once(self):
        eavesdropper, _, _ = make(12, [seg(0.5, 3.0, "(dog barks)")], [seg(4.0, 6.0, "(dog barks)")])
        hear(eavesdropper)
        assert eavesdropper.recent_room_tone == ("(dog barks)",)

    def test_nothing_is_remembered_before_anything_is_heard(self):
        assert make(5)[0].recent_room_tone == ()


class TestTheSoundPass:
    def test_after_three_rounds_with_no_speech_it_listens_for_lyrics_and_captions(self):
        eavesdropper, _, whisper = make(17, texts=[f"(crowd cheers) {NOTE} la la {NOTE}"])
        got = hear(eavesdropper)
        assert got == [Take("la la", "lyrics", ("(crowd cheers)",), pytest.approx(15.0, abs=0.05))]
        assert whisper.text_calls == [(pytest.approx(2.0), False)]             # voice detector off; see the next test

    def test_plain_text_from_the_room_tone_pass_is_thrown_away(self):
        eavesdropper, _, _ = make(17, texts=["Hello there, I invented this."])
        assert hear(eavesdropper) == []

    def test_the_pass_comes_every_third_quiet_round(self):
        eavesdropper, _, whisper = make(32, texts=[f"{NOTE} one {NOTE}", f"{NOTE} two {NOTE}"])
        got = hear(eavesdropper)
        assert [h.words for h in got] == ["one", "two"] and len(whisper.text_calls) == 2

    def test_speech_starts_the_count_again(self):
        rounds = [[], [seg(5.5, 7.0, "Hello.")], [], [], []]
        eavesdropper, _, whisper = make(27, *rounds, texts=[f"{NOTE} la {NOTE}"])
        got = hear(eavesdropper)
        assert [h.words for h in got] == ["Hello.", "la"] and len(whisper.text_calls) == 1
        assert got[1].ends_at == pytest.approx(25.0, abs=0.05)                  # three quiet rounds after the speech, not after the start

    def test_it_is_not_tried_while_speech_is_being_heard(self):
        rounds = [[seg(5.0 * n + 0.5, 5.0 * n + 2.0, f"Something said number {n}.")] for n in range(4)]
        eavesdropper, _, whisper = make(22, *rounds, texts=[f"{NOTE} la {NOTE}"] * 3)
        got = hear(eavesdropper)
        assert whisper.text_calls == [] and all(h.kind == "speech" for h in got) and len(got) == 4

    def test_it_can_be_switched_off(self):
        eavesdropper, _, whisper = make(32, texts=[f"{NOTE} la {NOTE}"] * 5, room_tone_pass_every=0)
        assert hear(eavesdropper) == [] and whisper.text_calls == []

    def test_it_listens_to_at_most_as_many_seconds_as_asked(self):
        eavesdropper, _, whisper = make(17, texts=[""], room_tone_pass_seconds=1.0)
        hear(eavesdropper)
        assert whisper.text_calls == [(pytest.approx(1.0), False)]

    def test_known_limit_it_only_hears_what_the_cutter_still_holds_about_two_seconds(self):
        # A stretch with no speech is trimmed to its last two seconds before the pass runs, so asking for ten seconds
        # still gives about two. The hearing-debug experiment behaves the same way.
        eavesdropper, _, whisper = make(17, texts=[""], room_tone_pass_seconds=10.0)
        hear(eavesdropper)
        assert whisper.text_calls == [(pytest.approx(2.0), False)]

    def test_it_is_not_tried_while_whisper_is_not_answering(self):
        eavesdropper, _, whisper = make(32, failing=range(0, 10), texts=[f"{NOTE} la {NOTE}"] * 5)
        hear(eavesdropper)
        assert whisper.text_calls == []

    def test_the_defaults_are_the_known_good_ones(self):
        assert (DEFAULT_ROOM_TONE_PASS_EVERY, DEFAULT_ROOM_TONE_PASS_SECONDS, DEFAULT_ROOM_TONE_MEMORY) == (3, 10.0, 6)


class TestWhenWhisperIsDown:
    def test_it_is_warned_about_once_for_a_whole_outage(self):
        eavesdropper, _, _ = make(22, failing=[0, 1, 2])
        hear(eavesdropper)
        assert len(eavesdropper.report.warnings) == 1 and "not answering" in eavesdropper.report.warnings[0]
        assert "URLError" in eavesdropper.report.warnings[0]

    def test_a_second_outage_is_warned_about_again(self):
        eavesdropper, _, _ = make(32, failing=[0, 3])
        hear(eavesdropper)
        assert len(eavesdropper.report.warnings) == 2

    def test_the_audio_is_kept_and_heard_when_whisper_comes_back(self):
        eavesdropper, _, whisper = make(12, [], [seg(0.5, 3.0, "Said while whisper was down.")], failing=[0])
        assert [h.words for h in hear(eavesdropper)] == ["Said while whisper was down."]
        assert whisper.calls[1] == (0.0, 10.0)

    def test_coming_back_is_not_a_warning(self):
        eavesdropper, _, _ = make(22, failing=[0])
        hear(eavesdropper)
        assert len(eavesdropper.report.warnings) == 1


class TestStopping:
    def test_the_source_is_closed_when_the_sound_ends(self):
        eavesdropper, source, _ = make(6)
        hear(eavesdropper)
        assert source.closed

    def test_closing_stops_the_listening_and_still_gives_the_last_round(self):
        eavesdropper, source, _ = make(60, [seg(0.5, 3.0, "Heard before I stopped.")])
        got = []
        for item in eavesdropper.hear():
            got.append(item)
            eavesdropper.close()
        assert [h.words for h in got] == ["Heard before I stopped."] and source.closed

    def test_stopping_the_loop_early_still_closes_the_source(self):
        eavesdropper, source, _ = make(60, [seg(0.5, 3.0, "One.")])
        generator = eavesdropper.hear()
        assert next(generator).words == "One."
        generator.close()
        assert source.closed

    def test_closing_twice_is_fine(self):
        eavesdropper, source, _ = make(5)
        eavesdropper.close()
        eavesdropper.close()
        assert source.closed


class TestSettings:
    @pytest.mark.parametrize("name,bad", [("room_tone_pass_every", -1), ("room_tone_pass_every", 2.5), ("room_tone_pass_every", "3"),
                                          ("room_tone_pass_every", None), ("room_tone_pass_every", True),
                                          ("room_tone_pass_seconds", 0), ("room_tone_pass_seconds", -5), ("room_tone_pass_seconds", "10"),
                                          ("room_tone_pass_seconds", None), ("room_tone_pass_seconds", float("nan")), ("room_tone_pass_seconds", True),
                                          ("room_tone_pass_seconds", float("inf")),
                                          ("room_tone_memory", 0), ("room_tone_memory", 2.5), ("room_tone_memory", "6"),
                                          ("room_tone_memory", None), ("room_tone_memory", True)])
    def test_a_bad_setting_means_the_default_with_a_warning(self, name, bad):
        eavesdropper = make(5, **{name: bad})[0]
        assert len(eavesdropper.report.warnings) == 1 and name in eavesdropper.report.warnings[0]

    def test_zero_room_tone_passes_is_allowed(self):
        assert make(5, room_tone_pass_every=0)[0].report.ok

    def test_the_cutters_settings_are_checked_too_and_reported_here(self):
        eavesdropper = make(5, cut_every_seconds=0)[0]
        assert len(eavesdropper.report.warnings) == 1 and "cut_every_seconds" in eavesdropper.report.warnings[0]

    def test_the_hold_is_passed_to_the_cutter(self):
        close_to_the_end = seg(3.5, 4.4, "Close to the end.")
        eavesdropper, _, whisper = make(12, [close_to_the_end], hold_back_seconds=0.5)          # ends 0.6 s before the end of the first round
        assert [h.words for h in hear(eavesdropper)] == ["Close to the end."]
        assert whisper.calls[1][0] == 4.4                                      # kept at the first round, so the next starts there
        eavesdropper, _, whisper = make(12, [close_to_the_end], [close_to_the_end])    # the default hold of 2 s keeps it back
        assert [h.words for h in hear(eavesdropper)] == ["Close to the end."]
        assert whisper.calls[1][0] == 0.0                                      # held back at the first round: audio kept

    def test_the_rewind_is_passed_to_the_cutter(self):
        eavesdropper, _, whisper = make(12, [seg(0.5, 3.0, "A.")], [], rewind_seconds=1.0)
        hear(eavesdropper)
        assert whisper.calls[1][0] == 2.0

    def test_the_buffer_limit_is_passed_to_the_cutter(self):
        replies = [[seg(0.5, 5.0 * n, "A long thought.")] for n in range(1, 4)]
        eavesdropper, _, whisper = make(11.5, *replies, max_cut_seconds=10.0)
        assert [h.words for h in hear(eavesdropper)] == ["A long thought."]
        assert whisper.calls[2][0] == 10.0                                     # forced out at 10 s, so the last round starts there

    def test_a_cutter_can_be_given_instead_of_made(self):
        whisper = FakeWhisper()
        cutter = RollingCutter(whisper, cut_every_seconds=2.0)
        eavesdropper = Eavesdropper(ScriptedSource(5.0), whisper, cutter=cutter)
        hear(eavesdropper)
        assert len(whisper.calls) == 3          # the given cutter's step of 2 s: rounds at 2 s and 4 s, and the last;
        # the Eavesdropper's own cutter (step 5 s) would have made two

    def test_the_cutters_settings_are_passed_on(self):
        eavesdropper, _, whisper = make(5, [], cut_every_seconds=2.0)
        hear(eavesdropper)
        assert len(whisper.calls) >= 3                                          # rounds at 2 s and 4 s, and the last

    @pytest.mark.parametrize("name,bad", [("room_tone_pass_every", -1), ("room_tone_pass_seconds", 0), ("room_tone_memory", 0)])
    def test_strict_mode_raises_on_a_bad_setting(self, name, bad):
        with pytest.raises(EavesdropperError):
            make(5, strict=True, **{name: bad})

    def test_strict_mode_raises_on_a_bad_cutter_setting_with_the_cutters_error(self):
        with pytest.raises(RollingCutterError, match="cut_every_seconds"):
            make(5, strict=True, cut_every_seconds=0)

    def test_good_settings_are_quiet(self):
        assert make(5)[0].report.ok


class TestPrivacy:
    def test_what_was_said_never_gets_into_the_report_or_the_log(self, caplog):
        with caplog.at_level(logging.DEBUG):
            eavesdropper, _, _ = make(12, [seg(0.5, 3.0, "abracadabra says the room")], failing=[1])
            hear(eavesdropper)
        assert "abracadabra" not in " ".join(eavesdropper.report.lines()) and "abracadabra" not in caplog.text


class TestWithARealCutterAndSorter:
    def test_a_longer_conversation_comes_out_in_order(self):
        rounds = [[seg(0.5, 3.0, "First thing said.")],
                  [seg(4.0, 6.0, "Second thing,"), seg(6.2, 7.5, "still going.")],
                  [seg(11.0, 13.0, "(dog barks)")],
                  [seg(15.0, 17.0, f"{NOTE} la la {NOTE}")]]
        eavesdropper, _, _ = make(25, *rounds)
        got = hear(eavesdropper)
        assert [(h.words, h.kind, h.room_tone) for h in got] == [
            ("First thing said.", "speech", ()), ("Second thing, still going.", "speech", ()),
            ("", "", ("(dog barks)",)), ("la la", "lyrics", ())]
        assert [h.ends_at for h in got] == sorted(h.ends_at for h in got)
