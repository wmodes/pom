"""Tests for the TypewriterPort and the ConsoleTypewriter: typing paced to a serial port, and the keyboard coming back."""
import io
import os
import threading
import time

import pytest

from pom.output import ConsoleTypewriter, TypewriterPort, TypewriterPortError, TypingReport
from pom.output import typewriter_port


class FakeClock:
    """A clock the test controls: sleeping moves it on."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeSerial:
    """Stands in for the serial port: records what is written, hands out the bytes a test feeds as keys."""

    def __init__(self, clock=None, fail_write_after=None):
        self.clock = clock
        self.writes = []
        self.write_times = []
        self.incoming = bytearray()
        self.read_sizes = []
        self.resets = 0
        self.flushes = 0
        self.closed = False
        self.fail_write_after = fail_write_after
        self.events = []

    @property
    def in_waiting(self):
        return len(self.incoming)

    def write(self, data):
        if self.fail_write_after is not None and len(self.writes) >= self.fail_write_after:
            raise OSError("write failed")
        self.writes.append(data)
        self.write_times.append(self.clock.now if self.clock else None)
        self.events.append("write")

    def flush(self):
        self.flushes += 1

    def read(self, size=1):
        self.read_sizes.append(size)
        self.events.append("read")
        taken, self.incoming = bytes(self.incoming[:size]), self.incoming[size:]
        return taken

    def reset_input_buffer(self):
        self.resets += 1
        self.incoming.clear()

    def close(self):
        self.closed = True

    @property
    def written(self):
        return b"".join(self.writes)


def make(fake=None, clock=None, **settings):
    """A port over a fake serial and a fake clock. Returns (port, fake, clock, opened) with `opened` the list of open calls."""
    clock = clock or FakeClock()
    fake = fake or FakeSerial(clock)
    opened = []

    def opener(port, baud, flow):
        opened.append((port, baud, flow))
        return fake

    settings.setdefault("finish_wait_seconds", 0)
    port = TypewriterPort("/dev/ttyTEST", opener=opener, clock=clock, sleep=clock.sleep, **settings)
    return port, fake, clock, opened


# --- Typing -------------------------------------------------------------------------------

def test_the_text_is_sent_a_character_at_a_time_and_reported():
    port, fake, _, _ = make()
    report = port.type("Hi there")
    assert fake.written == b"Hi there" and report.characters == 8 and report.completed is True
    assert fake.flushes == 1 and port.report.ok


@pytest.mark.parametrize("setting, ending", [("cr", b"\r"), ("crlf", b"\r\n"), ("lf", b"\n"), ("lfcr", b"\n\r")])
def test_a_newline_is_sent_as_the_configured_line_ending(setting, ending):
    port, fake, _, _ = make(end_of_line=setting)
    port.type("a\nb")
    assert fake.written == b"a" + ending + b"b"


def test_the_default_line_ending_is_cr_lf_because_a_bare_lf_only_feeds_the_paper():
    port, fake, _, _ = make()
    port.type("a\nb")
    assert fake.written == b"a\r\nb"


def test_a_windows_or_old_mac_line_ending_in_the_text_is_one_newline():
    port, fake, _, _ = make()
    port.type("a\r\nb\rc")
    assert fake.written == b"a\r\nb\r\nc"


def test_text_is_sent_no_faster_than_the_characters_per_second():
    port, fake, clock, _ = make(characters_per_second=10)
    report = port.type("abcd")
    assert fake.write_times == pytest.approx([0.0, 0.1, 0.2, 0.3])
    assert report.seconds == pytest.approx(0.3)


def test_a_newline_takes_one_slot_like_a_character():
    port, fake, _, _ = make(characters_per_second=10)
    port.type("a\nb")
    assert fake.write_times == pytest.approx([0.0, 0.1, 0.2])


def test_the_pace_is_scheduled_from_the_start_so_slow_writes_do_not_add_up():
    clock = FakeClock()

    class SlowSerial(FakeSerial):
        def write(self, data):
            super().write(data)
            clock.now += 0.04          # each write itself takes time

    port, fake, _, _ = make(SlowSerial(clock), clock, characters_per_second=10)
    port.type("abc")
    assert fake.write_times == pytest.approx([0.0, 0.1, 0.2])


def test_it_waits_a_little_after_the_last_character_for_the_machine_to_finish():
    port, _, _, _ = make(characters_per_second=10, finish_wait_seconds=2)
    report = port.type("ab")
    assert report.seconds == pytest.approx(0.1 + 2)


def test_no_finish_wait_when_it_is_zero():
    port, _, clock, _ = make(characters_per_second=10, finish_wait_seconds=0)
    port.type("ab")
    assert clock.sleeps == pytest.approx([0.1])


def test_only_printable_ascii_and_newlines_are_sent():
    port, fake, _, _ = make()
    report = port.type("a\x1b[2Jb\x00c\x7fd\xe9e\t f")
    assert fake.written == b"a[2Jbcde f" and report.characters == 10


def test_an_escape_never_reaches_the_machine():
    port, fake, _, _ = make()
    port.type("\x1b[2J\x1bc")
    assert b"\x1b" not in fake.written


@pytest.mark.parametrize("text", ["", "\x1b\x00", None, 5, b"bytes"])
def test_text_with_nothing_to_type_is_complete_at_once_and_opens_nothing(text):
    port, fake, _, opened = make()
    assert port.type(text) == TypingReport(0, 0.0, True)
    assert opened == [] and fake.writes == []


def test_the_port_is_opened_on_the_first_use_with_its_settings_and_only_once():
    port, _, _, opened = make(baud=115200, flow_control="rts_cts")
    assert opened == []
    port.type("a")
    port.type("b")
    assert opened == [("/dev/ttyTEST", 115200, "rts_cts")]


def test_a_port_that_will_not_open_gives_an_incomplete_report_and_a_warning_never_an_exception():
    clock = FakeClock()
    port = TypewriterPort("/dev/none", opener=lambda *a: (_ for _ in ()).throw(OSError("no such device")),
                          clock=clock, sleep=clock.sleep)
    report = port.type("a secret portent")
    assert report == TypingReport(0, 0.0, False)
    assert len(port.report.warnings) == 1 and "/dev/none" in port.report.warnings[0] and "OSError" in port.report.warnings[0]


def test_a_port_that_would_not_open_is_tried_again_on_the_next_call():
    clock = FakeClock()
    fake = FakeSerial(clock)
    attempts = []

    def opener(port, baud, flow):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("not plugged in yet")
        return fake

    port = TypewriterPort("/dev/x", opener=opener, clock=clock, sleep=clock.sleep, finish_wait_seconds=0)
    assert port.type("a").completed is False
    assert port.type("a").completed is True and fake.written == b"a"


def test_a_write_that_fails_halfway_reports_how_far_it_got_and_closes_the_port():
    clock = FakeClock()
    first = FakeSerial(clock, fail_write_after=2)
    second = FakeSerial(clock)
    ports = [first, second]
    port = TypewriterPort("/dev/x", opener=lambda *a: ports.pop(0), clock=clock, sleep=clock.sleep, finish_wait_seconds=0)
    report = port.type("abcde")
    assert report.characters == 2 and report.completed is False and first.closed is True
    assert len(port.report.warnings) == 1 and "2 of 5" in port.report.warnings[0]
    assert port.type("xy").completed is True and second.written == b"xy"      # reopened


def test_neither_the_text_nor_the_keys_typed_are_ever_put_in_the_report():
    clock = FakeClock()
    port = TypewriterPort("/dev/x", opener=lambda *a: FakeSerial(clock, fail_write_after=1), clock=clock, sleep=clock.sleep)
    port.type("zebra-secret portent")
    assert all("zebra" not in line for line in port.report.lines())


def test_a_port_name_that_is_not_a_device_name_means_nothing_is_typed():
    clock = FakeClock()
    opened = []
    for name in ("", "   ", None, 7):
        port = TypewriterPort(name, opener=lambda *a: opened.append(a), clock=clock, sleep=clock.sleep)
        assert port.available is False and len(port.report.warnings) == 1
        assert port.type("a").completed is False
    assert opened == []


def test_a_good_port_name_is_available():
    assert make()[0].available is True


@pytest.mark.parametrize("setting, bad", [("baud", 1234), ("baud", "9600"), ("baud", True), ("flow_control", "x"),
                                          ("end_of_line", "x"), ("characters_per_second", 0), ("characters_per_second", -3),
                                          ("characters_per_second", True), ("finish_wait_seconds", -1),
                                          ("finish_wait_seconds", "soon")])
def test_a_bad_setting_means_the_default_with_a_warning(setting, bad):
    port, fake, clock, opened = make(**{setting: bad})
    assert len(port.report.warnings) == 1 and setting in port.report.warnings[0]
    report = port.type("ab\n")
    assert report.completed is True and opened == [("/dev/ttyTEST", 9600, "xon_xoff")]
    assert fake.written == b"ab\r\n"                                   # the default line ending
    if setting == "characters_per_second":
        assert fake.write_times == pytest.approx([0.0, 1 / 16, 2 / 16])
    if setting == "finish_wait_seconds":
        assert report.seconds == pytest.approx(2 / 16 + 2.0)


def test_strict_mode_raises_for_a_bad_setting_a_port_that_will_not_open_and_a_failed_write():
    with pytest.raises(TypewriterPortError):
        TypewriterPort("/dev/x", baud=5, strict=True)
    with pytest.raises(TypewriterPortError):
        TypewriterPort("", strict=True)
    with pytest.raises(TypewriterPortError):
        TypewriterPort("/dev/x", opener=lambda *a: (_ for _ in ()).throw(OSError()), strict=True).type("a")
    clock = FakeClock()
    with pytest.raises(TypewriterPortError):
        TypewriterPort("/dev/x", opener=lambda *a: FakeSerial(clock, fail_write_after=0), clock=clock,
                       sleep=clock.sleep, strict=True).type("a")


def test_close_closes_the_port_and_the_next_call_opens_it_again():
    port, fake, _, opened = make()
    port.type("a")
    port.close()
    port.close()                                  # closing twice is fine
    assert fake.closed is True
    port.type("b")
    assert len(opened) == 2


def test_closing_a_port_that_was_never_opened_or_that_fails_to_close_is_fine():
    make()[0].close()
    port, fake, _, _ = make()
    port.type("a")
    fake.close = lambda: (_ for _ in ()).throw(OSError("already gone"))
    port.close()


def test_the_real_serial_port_is_opened_with_8n1_and_the_chosen_flow_control(monkeypatch):
    seen = []
    monkeypatch.setattr("serial.Serial", lambda *args, **kwargs: seen.append((args, kwargs)) or object())
    typewriter_port._open_serial("/dev/ttyACM0", 9600, "xon_xoff")
    typewriter_port._open_serial("/dev/ttyACM0", 19200, "rts_cts")
    typewriter_port._open_serial("/dev/ttyACM0", 19200, "none")
    (args, kwargs), (_, rts), (_, none) = seen
    assert args == ("/dev/ttyACM0", 9600)
    assert (kwargs["bytesize"], kwargs["parity"], kwargs["stopbits"]) == (8, "N", 1)
    assert (kwargs["xonxoff"], kwargs["rtscts"]) == (True, False)
    assert (rts["xonxoff"], rts["rtscts"]) == (False, True) and (none["xonxoff"], none["rtscts"]) == (False, False)
    assert kwargs["timeout"] == typewriter_port._READ_SLICE_SECONDS


# --- The keyboard: lines typed back --------------------------------------------------------

def test_a_typed_line_is_returned_when_c_rtn_is_pressed_and_echoed_as_it_came():
    port, fake, _, _ = make()
    fake.incoming += b"hi\r"
    assert port.read_line(1) == "hi"
    assert fake.writes == [b"h", b"i", b"\r\n"]


def test_a_line_ends_at_lf_too_and_a_cr_lf_pair_ends_it_once():
    port, fake, _, _ = make(echoplex=False)
    fake.incoming += b"a\r\nb\nc\r"
    assert [port.read_line(1), port.read_line(1), port.read_line(1)] == ["a", "b", "c"]


def test_c_rtn_alone_gives_an_empty_line():
    port, fake, _, _ = make(echoplex=False)
    fake.incoming += b"\r"
    assert port.read_line(1) == ""


def test_backspace_is_echoed_and_edits_the_line():
    port, fake, _, _ = make()
    fake.incoming += b"abc\x08d\r"
    assert port.read_line(1) == "abd"
    assert fake.writes == [b"a", b"b", b"c", b"\b", b"d", b"\r\n"]


def test_delete_edits_the_line_like_backspace():
    port, fake, _, _ = make(echoplex=False)
    fake.incoming += b"ab\x7f\x7fc\r"
    assert port.read_line(1) == "c"


def test_backspace_on_an_empty_line_does_nothing_and_is_not_echoed():
    port, fake, _, _ = make()
    fake.incoming += b"\x08\x08a\r"
    assert port.read_line(1) == "a" and b"\b" not in fake.written


def test_control_characters_and_non_ascii_bytes_from_the_keyboard_are_ignored_and_not_echoed():
    port, fake, _, _ = make()
    fake.incoming += b"a\x01\x1b\xe9b\x03\r"
    assert port.read_line(1) == "ab"
    assert fake.written == b"ab\r\n"


def test_without_echoplex_nothing_is_echoed_but_the_line_still_comes_back():
    port, fake, _, _ = make(echoplex=False)
    fake.incoming += b"hello\x08\r"
    assert port.read_line(1) == "hell" and fake.writes == []


def test_the_echo_of_c_rtn_uses_the_configured_line_ending():
    port, fake, _, _ = make(end_of_line="lf")
    fake.incoming += b"a\r"
    port.read_line(1)
    assert fake.writes[-1] == b"\n"


def test_no_line_within_the_timeout_gives_none_and_a_half_typed_line_is_kept_for_the_next_call():
    port, fake, clock, _ = make()
    fake.incoming += b"ab"
    assert port.read_line(1) is None
    assert clock.now >= 1 and fake.written == b"ab"
    fake.incoming += b"c\r"
    assert port.read_line(1) == "abc"


def test_with_no_timeout_it_waits_until_a_line_arrives():
    port, fake, clock, _ = make(echoplex=False)
    chunks = [b"", b"", b"", b"yo\r"]
    fake.read = lambda size=1: chunks.pop(0) if chunks else b""
    assert port.read_line() == "yo" and chunks == []


def test_bytes_after_the_end_of_a_line_wait_for_the_next_call_without_another_read():
    port, fake, _, _ = make(echoplex=False)
    fake.incoming += b"one\rtwo\r"
    assert port.read_line(1) == "one"
    reads = len(fake.read_sizes)
    assert port.read_line(1) == "two" and len(fake.read_sizes) == reads


def test_it_reads_everything_waiting_at_once():
    port, fake, _, _ = make(echoplex=False)
    fake.incoming += b"hello\r"
    port.read_line(1)
    assert fake.read_sizes[0] == 6


def test_a_line_that_goes_on_for_ever_is_ended_at_500_characters():
    port, fake, _, _ = make(echoplex=False)
    fake.incoming += b"x" * 503 + b"\r"
    first, second = port.read_line(1), port.read_line(1)
    assert len(first) == 500 and second == "xxx" and typewriter_port.MAX_TYPED_LINE == 500


def test_a_line_ended_by_the_limit_is_echoed_a_line_ending_like_any_other():
    port, fake, _, _ = make()
    fake.incoming += b"x" * 500
    assert len(port.read_line(1)) == 500
    assert fake.written.endswith(b"x\r\n")


def test_a_port_that_will_not_open_gives_none_after_waiting_the_timeout_not_a_hot_loop():
    clock = FakeClock()
    port = TypewriterPort("/dev/none", opener=lambda *a: (_ for _ in ()).throw(OSError()), clock=clock, sleep=clock.sleep)
    assert port.read_line(3) is None
    assert clock.now == pytest.approx(3) and len(port.report.warnings) == 1


def test_waiting_for_ever_on_a_port_that_will_not_open_still_gives_up_each_time_after_a_pause():
    clock = FakeClock()
    port = TypewriterPort("/dev/none", opener=lambda *a: (_ for _ in ()).throw(OSError()), clock=clock, sleep=clock.sleep)
    assert port.read_line() is None and clock.now == pytest.approx(1.0)


def test_a_read_that_fails_gives_none_and_a_warning_and_the_port_is_reopened_next_time():
    clock = FakeClock()
    broken, good = FakeSerial(clock), FakeSerial(clock)
    broken.read = lambda size=1: (_ for _ in ()).throw(OSError("unplugged"))
    ports = [broken, good]
    port = TypewriterPort("/dev/x", opener=lambda *a: ports.pop(0), clock=clock, sleep=clock.sleep, echoplex=False)
    assert port.read_line(1) is None and broken.closed is True
    assert len(port.report.warnings) == 1 and "keyboard" in port.report.warnings[0]
    good.incoming += b"ok\r"
    assert port.read_line(1) == "ok"


def test_a_failed_echo_is_reported_once_and_the_line_still_comes_back():
    clock = FakeClock()
    fake = FakeSerial(clock, fail_write_after=0)
    port = TypewriterPort("/dev/x", opener=lambda *a: fake, clock=clock, sleep=clock.sleep)
    fake.incoming += b"abc\r"
    assert port.read_line(1) == "abc"
    assert len(port.report.warnings) == 1 and "echo" in port.report.warnings[0]


def test_the_keys_typed_are_never_put_in_the_report():
    clock = FakeClock()
    fake = FakeSerial(clock, fail_write_after=0)
    port = TypewriterPort("/dev/x", opener=lambda *a: fake, clock=clock, sleep=clock.sleep)
    fake.incoming += b"zebra-secret\r"
    port.read_line(1)
    assert all("zebra" not in line for line in port.report.lines())


def test_strict_mode_raises_for_a_failed_read_and_a_failed_echo():
    clock = FakeClock()
    fake = FakeSerial(clock)
    fake.read = lambda size=1: (_ for _ in ()).throw(OSError())
    with pytest.raises(TypewriterPortError):
        TypewriterPort("/dev/x", opener=lambda *a: fake, clock=clock, sleep=clock.sleep, strict=True).read_line(1)
    echo_fake = FakeSerial(clock, fail_write_after=0)
    echo_fake.incoming += b"a\r"
    with pytest.raises(TypewriterPortError):
        TypewriterPort("/dev/x", opener=lambda *a: echo_fake, clock=clock, sleep=clock.sleep, strict=True).read_line(1)


# --- No keys during a portent --------------------------------------------------------------

def test_keys_typed_during_a_portent_are_thrown_away_by_default():
    port, fake, _, _ = make()
    fake.incoming += b"junk"
    port.type("portent")
    assert fake.resets == 2 and port.read_line(0.5) is None


def test_bytes_already_read_and_a_half_typed_line_are_thrown_away_too():
    port, fake, _, _ = make(echoplex=False)
    fake.incoming += b"one\rtwo\rha"
    assert port.read_line(1) == "one"
    port.type("portent")
    fake.incoming += b"lf\r"
    assert port.read_line(1) == "lf"                      # not "two", and not "halflf"


def test_a_half_typed_line_is_thrown_away_when_a_portent_starts():
    port, fake, _, _ = make(echoplex=False)
    fake.incoming += b"ha"
    assert port.read_line(0.2) is None                    # "ha" is waiting for its C Rtn
    port.type("portent")
    fake.incoming += b"lf\r"
    assert port.read_line(1) == "lf"


def test_a_cr_before_the_portent_does_not_swallow_the_first_lf_after_it():
    port, fake, _, _ = make(echoplex=False)
    fake.incoming += b"a\r"
    port.read_line(1)
    port.type("p")
    fake.incoming += b"\nb\r"
    assert port.read_line(1) == ""                        # that LF is an empty line, not the second half of a CR+LF pair


def test_with_no_input_during_output_off_the_keys_are_kept_for_the_next_read():
    port, fake, _, _ = make(echoplex=False, no_input_during_output=False)
    fake.incoming += b"one\rtwo\rha"
    assert port.read_line(1) == "one"
    port.type("portent")
    fake.incoming += b"lf\r"
    assert fake.resets == 0
    assert port.read_line(1) == "two" and port.read_line(1) == "half"


def test_typing_when_the_port_will_not_open_does_not_touch_the_keys():
    clock = FakeClock()
    port = TypewriterPort("/dev/x", opener=lambda *a: (_ for _ in ()).throw(OSError()), clock=clock, sleep=clock.sleep)
    port._received.extend(b"keep")
    assert port.type("a").completed is False and bytes(port._received) == b"keep"


def test_a_reset_that_fails_does_not_stop_the_portent():
    port, fake, _, _ = make()
    fake.reset_input_buffer = lambda: (_ for _ in ()).throw(OSError())
    assert port.type("abc").completed is True


def test_a_portent_and_the_keyboard_never_use_the_line_at_the_same_time():
    fake = FakeSerial()
    gate = threading.Event()
    started = threading.Event()
    real_write = fake.write

    def slow_write(data):
        started.set()
        gate.wait(5)
        real_write(data)

    fake.write = slow_write
    port = TypewriterPort("/dev/x", opener=lambda *a: fake, characters_per_second=1e9, finish_wait_seconds=0,
                          echoplex=False, no_input_during_output=False)
    fake.incoming += b"k\r"
    results = {}
    typing = threading.Thread(target=lambda: results.setdefault("typed", port.type("ab")))
    typing.start()
    assert started.wait(5)
    listening = threading.Thread(target=lambda: results.setdefault("line", port.read_line(5)))
    listening.start()
    time.sleep(0.3)
    assert "read" not in fake.events                      # the keyboard waits while the portent is being typed
    gate.set()
    typing.join(5)
    listening.join(5)
    assert results["typed"].completed is True and results["line"] == "k"
    assert fake.events.index("read") > fake.events.index("write")


# --- The console stand-in -------------------------------------------------------------------

def console(**kwargs):
    stream = io.StringIO()
    kwargs.setdefault("characters_per_second", 1e9)
    return ConsoleTypewriter(stream, **kwargs), stream


def test_the_console_typewriter_types_on_the_stream_with_plain_newlines():
    typewriter, stream = console(input_stream=io.StringIO(""))
    report = typewriter.type("ab\ncd")
    assert stream.getvalue() == "ab\ncd" and report.completed is True and typewriter.report.ok


def test_the_console_typewriter_is_a_typewriter_port_with_the_same_filter():
    typewriter, stream = console(input_stream=io.StringIO(""))
    assert isinstance(typewriter, TypewriterPort)
    typewriter.type("a\x1bb")
    assert stream.getvalue() == "ab"


def test_the_console_typewriter_is_paced_like_the_real_one():
    clock = FakeClock()
    stream = io.StringIO()
    typewriter = ConsoleTypewriter(stream, input_stream=io.StringIO(""), characters_per_second=10, clock=clock,
                                   sleep=clock.sleep)
    assert typewriter.type("abcd").seconds == pytest.approx(0.3)


def test_the_console_typewriter_reads_lines_from_its_input_and_does_not_echo_by_default():
    typewriter, stream = console(input_stream=io.StringIO("hi there\nsecond\n"))
    assert typewriter.read_line(1) == "hi there" and stream.getvalue() == ""
    assert typewriter.read_line(1) == "second"


def test_the_console_typewriter_echoes_when_asked():
    typewriter, stream = console(input_stream=io.StringIO("hi\n"), echoplex=True)
    assert typewriter.read_line(1) == "hi" and stream.getvalue() == "hi\n"


def test_the_console_input_can_be_a_real_pipe_and_unread_keys_are_thrown_away():
    read_end, write_end = os.pipe()
    with os.fdopen(read_end) as keyboard:
        typewriter, _ = console(input_stream=keyboard)
        os.write(write_end, b"typed over it\n")
        typewriter.type("portent")                           # throws away what was typed
        os.write(write_end, b"after\n")
        assert typewriter.read_line(2) == "after"
        os.close(write_end)


def test_the_console_input_with_nothing_typed_gives_none_after_the_timeout():
    read_end, write_end = os.pipe()
    with os.fdopen(read_end) as keyboard:
        typewriter, _ = console(input_stream=keyboard)
        assert typewriter.read_line(0.3) is None
    os.close(write_end)


def test_a_console_with_no_keyboard_reads_nothing():
    sink = typewriter_port._StreamSink(io.StringIO(), None)
    assert sink.read(5) == b"" and sink.reset_input_buffer() is None
    sink.close()
