"""Tests for pom.listening.mac_mic_source, using small stand-ins for `ffmpeg`.

The buffering, dropping and fault rules are MicSource's and are tested in test_mic_source; these tests cover what the
Mac version changes: the command it runs, its default device, and the wording of its faults.
"""
import pytest

from pom.listening import MacMicSource, MicSource, MicSourceError
from pom.listening.mac_mic_source import DEFAULT_MAC_DEVICE
from tests.listening.test_mic_source import collect, fake_arecord, value_of, wait_for_the_reader


def fake_ffmpeg(tmp_path, body):
    return fake_arecord(tmp_path, body, name="ffmpeg")


def recorded_arguments(tmp_path, **settings):
    """The arguments a MacMicSource gave the stand-in ffmpeg."""
    record = tmp_path / "argv.txt"
    program = fake_ffmpeg(tmp_path, f"open({str(record)!r}, 'w').write('|'.join(sys.argv[1:]))\nemit(1)")
    source = MacMicSource(ffmpeg=program, **settings)
    wait_for_the_reader(source)
    source.close()
    return record.read_text().split("|")


class TestWhatItRuns:
    def test_ffmpeg_reads_the_systems_default_input_as_16khz_mono_16bit_raw(self, tmp_path):
        assert recorded_arguments(tmp_path) == ["-nostdin", "-hide_banner", "-loglevel", "error", "-f", "avfoundation",
                                                "-i", ":default", "-ar", "16000", "-ac", "1", "-f", "s16le", "pipe:1"]

    def test_the_default_device_is_the_systems_default_input(self):
        assert DEFAULT_MAC_DEVICE == "default"

    def test_a_device_name_picks_that_input(self, tmp_path):
        arguments = recorded_arguments(tmp_path, device="MacBook Pro Microphone")
        assert arguments[arguments.index("-i") + 1] == ":MacBook Pro Microphone"

    def test_it_is_a_mic_source(self):
        assert issubclass(MacMicSource, MicSource)

    def test_the_arecord_source_is_unchanged(self, tmp_path):
        record = tmp_path / "argv.txt"
        program = fake_arecord(tmp_path, f"open({str(record)!r}, 'w').write(' '.join(sys.argv[1:]))\nemit(1)")
        source = MicSource(arecord=program)
        wait_for_the_reader(source)
        source.close()
        assert record.read_text().startswith("-q -D ")


class TestHearing:
    def test_frames_come_out_in_order(self, tmp_path):
        source = MacMicSource(ffmpeg=fake_ffmpeg(tmp_path, "emit(1, 2, 3)"))
        wait_for_the_reader(source)
        assert [value_of(frame) for frame in collect(source)] == [1, 2, 3]
        source.close()


class TestFaults:
    def test_a_missing_ffmpeg_is_critical_and_says_how_to_get_it(self):
        source = MacMicSource(ffmpeg="no-such-program-anywhere")
        assert collect(source) == []
        text = " ".join(source.report.lines())
        assert "ffmpeg was not found" in text and "brew install ffmpeg" in text
        assert "arecord" not in text and "Pi" not in text

    def test_a_missing_ffmpeg_raises_in_strict_mode(self):
        with pytest.raises(MicSourceError):
            MacMicSource(ffmpeg="no-such-program-anywhere", strict=True)

    def test_an_ffmpeg_that_stops_is_reported_by_name(self, tmp_path):
        source = MacMicSource(ffmpeg=fake_ffmpeg(tmp_path, "emit(1)"))
        wait_for_the_reader(source)
        collect(source)
        assert any("ffmpeg ended" in line for line in source.report.lines())
        source.close()

    def test_a_bad_device_falls_back_to_the_default_and_names_it(self, tmp_path):
        record = tmp_path / "argv.txt"
        program = fake_ffmpeg(tmp_path, f"open({str(record)!r}, 'w').write('|'.join(sys.argv[1:]))\nemit(1)")
        source = MacMicSource(device="  ", ffmpeg=program)
        wait_for_the_reader(source)
        source.close()
        assert ":default" in record.read_text().split("|")
        text = " ".join(source.report.lines())
        assert "using default" in text and "plughw" not in text

    def test_an_ffmpeg_that_cannot_be_started_is_reported_by_name(self, tmp_path):
        program = tmp_path / "ffmpeg"
        program.write_text("#!/no/such/interpreter\n")
        program.chmod(0o755)
        source = MacMicSource(ffmpeg=str(program))
        assert collect(source) == []
        assert any("ffmpeg could not be started" in line for line in source.report.lines())


class TestTheBuffer:
    def test_the_buffer_seconds_setting_is_used(self, tmp_path):
        source = MacMicSource(ffmpeg=fake_ffmpeg(tmp_path, "emit(*range(40))"), buffer_seconds=0.09)
        wait_for_the_reader(source)
        assert source.dropped_frames > 0
        source.close()

    def test_a_roomy_buffer_drops_nothing(self, tmp_path):
        source = MacMicSource(ffmpeg=fake_ffmpeg(tmp_path, "emit(*range(40))"))
        wait_for_the_reader(source)
        assert source.dropped_frames == 0
        source.close()
