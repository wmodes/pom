"""Tests for pom.listening.wav_source: a recording played as if it were the microphone."""
import struct
import wave

import pytest

from pom.listening.audio_format import FRAME_BYTES, FRAME_SECONDS, SAMPLE_RATE
from pom.listening.wav_source import DEFAULT_END_PAUSE_SECONDS, WavSource, WavSourceError

SAMPLES_PER_FRAME = FRAME_BYTES // 2
SILENCE = b"\x00" * FRAME_BYTES


def frame_of(value: int) -> bytes:
    """One frame in which every sample has this value, so frames can be told apart."""
    return struct.pack("<h", value) * SAMPLES_PER_FRAME


def make_wav(path, data: bytes, rate=SAMPLE_RATE, channels=1, width=2):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(data)
    return path


@pytest.fixture
def three_frames(tmp_path):
    return make_wav(tmp_path / "three.wav", frame_of(1) + frame_of(2) + frame_of(3))


def play(source):
    return list(source.frames())


class TestTheFrames:
    def test_every_frame_of_the_recording_comes_out_in_order(self, three_frames):
        frames = play(WavSource(three_frames, speed=0, end_pause_seconds=0))
        assert frames == [frame_of(1), frame_of(2), frame_of(3)]

    def test_each_frame_is_thirty_milliseconds(self, three_frames):
        assert all(len(f) == FRAME_BYTES for f in play(WavSource(three_frames, speed=0)))

    def test_a_pause_of_silence_follows_the_recording(self, three_frames):
        frames = play(WavSource(three_frames, speed=0))
        assert frames[:3] == [frame_of(1), frame_of(2), frame_of(3)]
        assert frames[3:] == [SILENCE] * 40                                    # 1.2 s of 30 ms frames

    def test_the_default_pause_is_one_point_two_seconds(self):
        assert DEFAULT_END_PAUSE_SECONDS == 1.2

    @pytest.mark.parametrize("pause,count", [(0, 0), (0.3, 10), (0.03, 1), (0.9, 30)])
    def test_the_pause_is_a_setting(self, three_frames, pause, count):
        frames = play(WavSource(three_frames, speed=0, end_pause_seconds=pause))
        assert len(frames) == 3 + count

    def test_a_last_piece_shorter_than_a_frame_is_dropped(self, tmp_path):
        path = make_wav(tmp_path / "ragged.wav", frame_of(1) + frame_of(2) + frame_of(9)[:400])
        assert play(WavSource(path, speed=0, end_pause_seconds=0)) == [frame_of(1), frame_of(2)]

    def test_an_empty_recording_is_just_the_pause(self, tmp_path):
        path = make_wav(tmp_path / "empty.wav", b"")
        assert play(WavSource(path, speed=0)) == [SILENCE] * 40

    def test_the_length_is_known(self, three_frames):
        assert WavSource(three_frames).seconds == pytest.approx(3 * FRAME_SECONDS)

    def test_a_path_or_a_string_will_do(self, three_frames):
        assert play(WavSource(str(three_frames), speed=0, end_pause_seconds=0)) == \
            play(WavSource(three_frames, speed=0, end_pause_seconds=0))


class TestThePace:
    def waits(self, path, speed):
        waited = []
        play(WavSource(path, speed=speed, sleep=waited.append))
        return waited

    def test_real_time_waits_one_frame_after_each_frame(self, three_frames):
        assert self.waits(three_frames, 1) == pytest.approx([FRAME_SECONDS] * 3)

    def test_double_speed_waits_half_as_long(self, three_frames):
        assert self.waits(three_frames, 2) == pytest.approx([FRAME_SECONDS / 2] * 3)

    def test_a_speed_of_zero_never_waits(self, three_frames):
        assert self.waits(three_frames, 0) == []

    def test_the_silence_at_the_end_is_not_waited_for(self, three_frames):
        assert len(self.waits(three_frames, 1)) == 3

    @pytest.mark.parametrize("bad", [-1, "fast", None, True, float("nan"), float("inf")])
    def test_a_bad_speed_means_real_time_with_a_warning(self, three_frames, bad):
        waited = []
        source = WavSource(three_frames, speed=bad, sleep=waited.append)
        play(source)
        assert waited == pytest.approx([FRAME_SECONDS] * 3)
        assert len(source.report.warnings) == 1 and "speed" in source.report.warnings[0]

    @pytest.mark.parametrize("bad", [-1, "long", None, True, float("nan")])
    def test_a_bad_pause_means_the_default_with_a_warning(self, three_frames, bad):
        source = WavSource(three_frames, speed=0, end_pause_seconds=bad)
        assert len(play(source)) == 3 + 40
        assert len(source.report.warnings) == 1 and "end_pause_seconds" in source.report.warnings[0]


class TestARecordingThatCannotBePlayed:
    @pytest.mark.parametrize("rate,channels,width", [(8000, 1, 2), (44100, 1, 2), (SAMPLE_RATE, 2, 2), (SAMPLE_RATE, 1, 1)])
    def test_the_wrong_format_is_critical_and_plays_nothing(self, tmp_path, rate, channels, width):
        path = make_wav(tmp_path / "odd.wav", b"\x00" * 4000, rate=rate, channels=channels, width=width)
        source = WavSource(path, speed=0)
        assert play(source) == [] and source.report.has_critical and source.seconds == 0.0
        assert "ffmpeg" in source.report.criticals[0] and "16 kHz mono 16-bit" in source.report.criticals[0]

    def test_a_missing_file_is_critical_and_plays_nothing(self, tmp_path):
        source = WavSource(tmp_path / "nothing.wav", speed=0)
        assert play(source) == [] and source.report.has_critical

    def test_a_file_that_is_not_a_wav_is_critical(self, tmp_path):
        (tmp_path / "notes.txt").write_text("not audio")
        source = WavSource(tmp_path / "notes.txt", speed=0)
        assert play(source) == [] and source.report.has_critical

    @pytest.mark.parametrize("path", [None, 5, ""])
    def test_a_path_that_is_not_a_path_is_critical(self, path):
        source = WavSource(path, speed=0)
        assert play(source) == [] and source.report.has_critical

    def test_strict_mode_raises_on_a_missing_file(self, tmp_path):
        with pytest.raises(WavSourceError, match="cannot open"):
            WavSource(tmp_path / "nothing.wav", strict=True)

    def test_strict_mode_raises_on_the_wrong_format(self, tmp_path):
        path = make_wav(tmp_path / "odd.wav", b"\x00" * 400, rate=8000)
        with pytest.raises(WavSourceError, match="16 kHz"):
            WavSource(path, strict=True)

    def test_strict_mode_raises_on_a_bad_setting(self, three_frames):
        with pytest.raises(WavSourceError, match="speed"):
            WavSource(three_frames, speed=-1, strict=True)

    def test_what_the_report_says_does_not_include_the_path(self, tmp_path):
        source = WavSource(tmp_path / "private-meeting.wav")
        assert "private-meeting" not in " ".join(source.report.lines())


class TestClosing:
    def test_closing_twice_is_fine(self, three_frames):
        source = WavSource(three_frames)
        source.close()
        source.close()

    def test_a_closed_source_plays_nothing(self, three_frames):
        source = WavSource(three_frames, speed=0)
        source.close()
        assert play(source) == []

    def test_closing_while_playing_stops_it_without_the_pause(self, three_frames):
        source = WavSource(three_frames, speed=0)
        got = []
        for frame in source.frames():
            got.append(frame)
            if len(got) == 2:
                source.close()
        assert got == [frame_of(1), frame_of(2)]

    def test_a_source_that_could_not_open_can_be_closed(self, tmp_path):
        WavSource(tmp_path / "nothing.wav").close()
