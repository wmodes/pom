"""Tests for pom.listening.audio_format: the numbers every listening module shares."""
from pom.listening import audio_format


def test_it_is_the_audio_whisper_takes():
    assert audio_format.SAMPLE_RATE == 16000 and audio_format.SAMPLE_BYTES == 2        # 16 kHz, 16-bit, mono


def test_a_second_of_audio_is_thirty_two_thousand_bytes():
    assert audio_format.BYTES_PER_SECOND == 32000


def test_a_frame_is_thirty_milliseconds_and_holds_a_whole_number_of_samples():
    assert audio_format.FRAME_MS == 30 and audio_format.FRAME_SECONDS == 0.03
    assert audio_format.FRAME_BYTES == 960
    assert audio_format.FRAME_BYTES % audio_format.SAMPLE_BYTES == 0


def test_the_frame_and_the_second_agree():
    assert audio_format.FRAME_BYTES == round(audio_format.BYTES_PER_SECOND * audio_format.FRAME_SECONDS)
