"""Tests for pom.listening.mic_source, using small stand-ins for `arecord`."""
import stat
import struct
import sys
import threading
import time

import pytest

from pom.listening.audio_format import FRAME_BYTES
from pom.listening.mic_source import DEFAULT_DEVICE, MicSource, MicSourceError

SAMPLES_PER_FRAME = FRAME_BYTES // 2


def frame_of(value: int) -> bytes:
    return struct.pack("<h", value) * SAMPLES_PER_FRAME


def value_of(frame: bytes) -> int:
    return struct.unpack("<h", frame[:2])[0]


def fake_arecord(tmp_path, body: str, name="arecord"):
    """A stand-in `arecord`: a Python script that writes the audio the body describes to its standard output."""
    script = tmp_path / name
    script.write_text(
        f"#!{sys.executable}\n"
        "import struct, sys, time\n"
        f"FRAME = {FRAME_BYTES}\n"
        "def frame(v):\n    return struct.pack('<h', v) * (FRAME // 2)\n"
        "def emit(*values, extra=b''):\n"
        "    for v in values:\n        sys.stdout.buffer.write(frame(v))\n"
        "    sys.stdout.buffer.write(extra)\n    sys.stdout.buffer.flush()\n"
        f"{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def wait_for_the_reader(source, seconds=5.0):
    source._reader.join(seconds)           # the stand-in has finished and every frame is in the buffer
    assert not source._reader.is_alive()


def collect(source):
    return list(source.frames())


class TestWhatItRuns:
    def test_arecord_is_started_for_16khz_mono_16bit_raw(self, tmp_path):
        record = tmp_path / "argv.txt"
        program = fake_arecord(tmp_path, f"open({str(record)!r}, 'w').write(' '.join(sys.argv[1:]))\nemit(1)")
        source = MicSource(arecord=program)
        wait_for_the_reader(source)
        source.close()
        assert record.read_text() == f"-q -D {DEFAULT_DEVICE} -f S16_LE -r 16000 -c 1 -t raw"

    def test_the_device_is_a_setting(self, tmp_path):
        record = tmp_path / "argv.txt"
        program = fake_arecord(tmp_path, f"open({str(record)!r}, 'w').write(' '.join(sys.argv[1:]))\nemit(1)")
        source = MicSource("hw:2,0", arecord=program)
        wait_for_the_reader(source)
        source.close()
        assert "-D hw:2,0 " in record.read_text()

    @pytest.mark.parametrize("bad", [None, "", "   ", 5, ["hw:0"]])
    def test_a_bad_device_means_the_default_with_a_warning(self, tmp_path, bad):
        record = tmp_path / "argv.txt"
        program = fake_arecord(tmp_path, f"open({str(record)!r}, 'w').write(' '.join(sys.argv[1:]))\nemit(1)")
        source = MicSource(bad, arecord=program)
        wait_for_the_reader(source)
        source.close()
        assert f"-D {DEFAULT_DEVICE} " in record.read_text()
        assert len(source.report.warnings) == 1 and "device" in source.report.warnings[0]


class TestTheFrames:
    def test_frames_arrive_whole_and_in_order(self, tmp_path):
        source = MicSource(arecord=fake_arecord(tmp_path, "emit(1, 2, 3, 4, 5, 6)"))
        frames = collect(source)
        assert [value_of(f) for f in frames] == [1, 2, 3, 4, 5, 6] and all(len(f) == FRAME_BYTES for f in frames)

    def test_a_last_piece_shorter_than_a_frame_is_dropped(self, tmp_path):
        source = MicSource(arecord=fake_arecord(tmp_path, "emit(1, 2, extra=frame(9)[:500])"))
        assert [value_of(f) for f in collect(source)] == [1, 2]

    def test_sound_that_arrives_in_odd_sized_pieces_is_cut_into_whole_frames(self, tmp_path):
        body = ("data = frame(1) + frame(2) + frame(3)\n"
                "for i in range(0, len(data), 700):\n    sys.stdout.buffer.write(data[i:i + 700]); sys.stdout.buffer.flush(); time.sleep(0.01)")
        source = MicSource(arecord=fake_arecord(tmp_path, body))
        assert [value_of(f) for f in collect(source)] == [1, 2, 3]

    def test_frames_can_be_taken_slowly_without_losing_any(self, tmp_path):
        source = MicSource(arecord=fake_arecord(tmp_path, "emit(*range(1, 51))"))
        got = []
        for frame in source.frames():
            got.append(value_of(frame))
            time.sleep(0.002)
        assert got == list(range(1, 51)) and source.dropped_frames == 0

    def test_after_the_sound_ends_frames_ends_at_once(self, tmp_path):
        source = MicSource(arecord=fake_arecord(tmp_path, "emit(1)"))
        collect(source)
        assert collect(source) == []

    def test_the_microphone_stopping_by_itself_is_a_critical_fault(self, tmp_path):
        source = MicSource(arecord=fake_arecord(tmp_path, "emit(1, 2)"))
        collect(source)
        assert source.report.has_critical and "stopped" in source.report.criticals[0]

    def test_arecord_dying_at_once_gives_no_frames_and_a_critical_fault(self, tmp_path):
        source = MicSource(arecord=fake_arecord(tmp_path, "sys.exit(1)"))
        assert collect(source) == [] and source.report.has_critical


class TestWhenTheListenerFallsBehind:
    def test_the_oldest_frames_are_dropped_and_the_newest_kept_in_order(self, tmp_path):
        source = MicSource(buffer_seconds=0.3, arecord=fake_arecord(tmp_path, "emit(*range(1, 201))"))      # room for 10 frames
        wait_for_the_reader(source)
        got = [value_of(f) for f in collect(source)]
        assert got == list(range(192, 201))                              # ten places: nine frames and the end marker
        assert source.dropped_frames == 191

    def test_dropping_is_warned_about_once_for_one_stretch(self, tmp_path):
        source = MicSource(buffer_seconds=0.3, arecord=fake_arecord(tmp_path, "emit(*range(1, 201))"))
        wait_for_the_reader(source)
        assert len([w for w in source.report.warnings if "dropping" in w]) == 1

    def test_a_second_stretch_of_dropping_is_warned_about_again(self):
        source = MicSource(buffer_seconds=0.06, arecord="no-such-program-anywhere")            # no microphone: two places
        for n in range(4):
            source._put(frame_of(n))                                      # the first stretch: two frames are dropped
        assert len([w for w in source.report.warnings if "dropping" in w]) == 1
        source._queue.get_nowait()                                         # the listener catches up a little
        source._put(frame_of(10))                                         # room for it: the stretch is over
        for n in range(11, 14):
            source._put(frame_of(n))                                      # the second stretch
        assert len([w for w in source.report.warnings if "dropping" in w]) == 2

    def test_no_dropping_when_the_buffer_is_big_enough(self, tmp_path):
        source = MicSource(buffer_seconds=10, arecord=fake_arecord(tmp_path, "emit(*range(1, 201))"))
        wait_for_the_reader(source)
        assert len(collect(source)) == 200 and source.dropped_frames == 0
        assert not any("dropping" in w for w in source.report.warnings)

    @pytest.mark.parametrize("bad", [0, -1, "10", None, True, float("nan"), float("inf")])
    def test_a_bad_buffer_means_the_default_with_a_warning(self, tmp_path, bad):
        source = MicSource(buffer_seconds=bad, arecord=fake_arecord(tmp_path, "emit(*range(1, 401))"))
        wait_for_the_reader(source)
        got = collect(source)
        assert len(got) == 332 and value_of(got[-1]) == 400 and source.dropped_frames == 68   # ten seconds is 333 places
        assert any("buffer_seconds" in w for w in source.report.warnings)


class TestClosing:
    def test_closing_ends_a_wait_for_sound(self, tmp_path):
        source = MicSource(arecord=fake_arecord(tmp_path, "emit(1)\ntime.sleep(60)"))
        got = []
        listener = threading.Thread(target=lambda: got.extend(collect(source)))
        listener.start()
        time.sleep(0.3)
        source.close()
        listener.join(5)
        assert not listener.is_alive() and [value_of(f) for f in got] == [1]

    def test_closing_on_purpose_is_not_a_fault(self, tmp_path):
        source = MicSource(arecord=fake_arecord(tmp_path, "emit(1)\ntime.sleep(60)"))
        listener = threading.Thread(target=lambda: collect(source))
        listener.start()
        time.sleep(0.3)
        source.close()
        listener.join(5)
        assert source.report.ok

    def test_closing_twice_is_fine(self, tmp_path):
        source = MicSource(arecord=fake_arecord(tmp_path, "emit(1)\ntime.sleep(60)"))
        source.close()
        source.close()

    def test_a_closed_source_gives_no_frames(self, tmp_path):
        source = MicSource(arecord=fake_arecord(tmp_path, "emit(1)\ntime.sleep(60)"))
        source.close()
        assert collect(source) == []

    def test_close_stops_arecord(self, tmp_path):
        source = MicSource(arecord=fake_arecord(tmp_path, "emit(1)\ntime.sleep(60)"))
        process = source._process
        source.close()
        assert process.poll() is not None


class TestNoMicrophone:
    def test_no_arecord_is_critical_and_gives_no_frames(self):
        source = MicSource(arecord="no-such-program-anywhere")
        assert collect(source) == [] and source.report.has_critical and "arecord" in source.report.criticals[0]

    def test_a_program_that_cannot_run_is_critical(self, tmp_path):
        not_runnable = tmp_path / "arecord"
        not_runnable.write_text("not a program")
        not_runnable.chmod(0o755)
        source = MicSource(arecord=str(not_runnable))
        assert collect(source) == [] and source.report.has_critical

    @pytest.mark.parametrize("bad", [None, 5, ["arecord"]])
    def test_a_program_name_that_is_not_text_is_critical(self, bad):
        source = MicSource(arecord=bad)
        assert collect(source) == [] and source.report.has_critical

    def test_closing_a_source_that_never_started_is_fine(self):
        MicSource(arecord="no-such-program-anywhere").close()


class TestStrictMode:
    def test_a_missing_arecord_raises(self):
        with pytest.raises(MicSourceError, match="arecord"):
            MicSource(arecord="no-such-program-anywhere", strict=True)

    def test_a_bad_setting_raises(self, tmp_path):
        with pytest.raises(MicSourceError, match="device"):
            MicSource("", arecord=fake_arecord(tmp_path, "emit(1)"), strict=True)
        with pytest.raises(MicSourceError, match="buffer_seconds"):
            MicSource(buffer_seconds=0, arecord=fake_arecord(tmp_path, "emit(1)"), strict=True)

    def test_a_fault_found_in_the_reader_thread_is_raised_when_the_sound_ends(self, tmp_path):
        source = MicSource(strict=True, arecord=fake_arecord(tmp_path, "emit(1, 2)"))
        with pytest.raises(MicSourceError, match="stopped sending"):
            collect(source)

    def test_in_strict_mode_dropping_does_not_hang_the_listener(self, tmp_path):
        source = MicSource(strict=True, buffer_seconds=0.3, arecord=fake_arecord(tmp_path, "emit(*range(1, 201))"))
        wait_for_the_reader(source)
        with pytest.raises(MicSourceError):
            collect(source)

    def test_what_the_report_says_never_includes_sound(self, tmp_path):
        source = MicSource(buffer_seconds=0.3, arecord=fake_arecord(tmp_path, "emit(*range(1, 201))"))
        wait_for_the_reader(source)
        collect(source)
        assert all(len(line) < 200 for line in source.report.lines())
