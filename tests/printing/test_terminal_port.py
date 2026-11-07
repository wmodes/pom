"""Tests for the TerminalPort and the ConsoleTerminal: typing paced to a serial port, and the keyboard coming back."""
import io
import logging
import os
import threading
import time

import pytest

from pom.printing import ConsoleTerminal, TerminalPort, TerminalPortError, PrintResult
from pom.printing import terminal_port


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
    port = TerminalPort("/dev/ttyTEST", opener=opener, clock=clock, sleep=clock.sleep, **settings)
    return port, fake, clock, opened


# --- Typing -------------------------------------------------------------------------------

def test_the_text_is_sent_a_character_at_a_time_and_reported():
    port, fake, _, _ = make()
    report = port.transmit("Hi there")
    assert fake.written == b"Hi there" and report.characters == 8 and report.completed is True
    assert fake.flushes == 1 and port.report.ok


@pytest.mark.parametrize("setting, ending", [("cr", b"\r"), ("crlf", b"\r\n"), ("lf", b"\n"), ("lfcr", b"\n\r")])
def test_a_newline_is_sent_as_the_configured_line_ending(setting, ending):
    port, fake, _, _ = make(end_of_line=setting)
    port.transmit("a\nb")
    assert fake.written == b"a" + ending + b"b"


def test_the_default_line_ending_is_cr_lf_because_a_bare_lf_only_feeds_the_paper():
    port, fake, _, _ = make()
    port.transmit("a\nb")
    assert fake.written == b"a\r\nb"


def test_a_windows_or_old_mac_line_ending_in_the_text_is_one_newline():
    port, fake, _, _ = make()
    port.transmit("a\r\nb\rc")
    assert fake.written == b"a\r\nb\r\nc"


def test_text_is_sent_no_faster_than_the_characters_per_second():
    port, fake, clock, _ = make(characters_per_second=10)
    report = port.transmit("abcd")
    assert fake.write_times == pytest.approx([0.0, 0.1, 0.2, 0.3])
    assert report.seconds == pytest.approx(0.3)


def test_a_newline_takes_one_slot_like_a_character():
    port, fake, _, _ = make(characters_per_second=10)
    port.transmit("a\nb")
    assert fake.write_times == pytest.approx([0.0, 0.1, 0.2])


def test_the_pace_is_scheduled_from_the_start_so_slow_writes_do_not_add_up():
    clock = FakeClock()

    class SlowSerial(FakeSerial):
        def write(self, data):
            super().write(data)
            clock.now += 0.04          # each write itself takes time

    port, fake, _, _ = make(SlowSerial(clock), clock, characters_per_second=10)
    port.transmit("abc")
    assert fake.write_times == pytest.approx([0.0, 0.1, 0.2])


def test_it_waits_a_little_after_the_last_character_for_the_machine_to_finish():
    port, _, _, _ = make(characters_per_second=10, finish_wait_seconds=2)
    report = port.transmit("ab")
    assert report.seconds == pytest.approx(0.1 + 2)


def test_no_finish_wait_when_it_is_zero():
    port, _, clock, _ = make(characters_per_second=10, finish_wait_seconds=0)
    port.transmit("ab")
    assert clock.sleeps == pytest.approx([0.1])


def test_only_printable_ascii_and_newlines_are_sent():
    port, fake, _, _ = make()
    report = port.transmit("a\x1b[2Jb\x00c\x7fd\xe9e\t f")
    assert fake.written == b"a[2Jbcde f" and report.characters == 10


def test_an_escape_never_reaches_the_machine():
    port, fake, _, _ = make()
    port.transmit("\x1b[2J\x1bc")
    assert b"\x1b" not in fake.written


@pytest.mark.parametrize("text", ["", "\x1b\x00", None, 5, b"bytes"])
def test_text_with_nothing_to_type_is_complete_at_once_and_opens_nothing(text):
    port, fake, _, opened = make()
    assert port.transmit(text) == PrintResult(0, 0.0, True)
    assert opened == [] and fake.writes == []


def test_the_port_is_opened_on_the_first_use_with_its_settings_and_only_once():
    port, _, _, opened = make(baud=115200, flow_control="rts_cts")
    assert opened == []
    port.transmit("a")
    port.transmit("b")
    assert opened == [("/dev/ttyTEST", 115200, "rts_cts")]


def test_a_port_that_will_not_open_gives_an_incomplete_report_and_a_warning_never_an_exception():
    clock = FakeClock()
    port = TerminalPort("/dev/none", opener=lambda *a: (_ for _ in ()).throw(OSError("no such device")),
                          clock=clock, sleep=clock.sleep)
    report = port.transmit("a secret portent")
    assert report == PrintResult(0, 0.0, False)
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

    port = TerminalPort("/dev/x", opener=opener, clock=clock, sleep=clock.sleep, finish_wait_seconds=0)
    assert port.transmit("a").completed is False
    assert port.transmit("a").completed is True and fake.written == b"a"


def test_a_write_that_fails_halfway_is_waited_for_and_typing_goes_on_where_it_stopped():
    clock = FakeClock()
    first = FakeSerial(clock, fail_write_after=2)
    second = FakeSerial(clock)
    ports = [first, second]
    port = TerminalPort("/dev/x", opener=lambda *a: ports.pop(0), clock=clock, sleep=clock.sleep, finish_wait_seconds=0)
    report = port.transmit("abcde")
    assert report.characters == 5 and report.completed is True and first.closed is True
    assert first.written == b"ab" and second.written == b"cde"                # nothing repeated, nothing missed
    assert len(port.report.warnings) == 1 and "2 of 5" in port.report.warnings[0]
    assert port.transmit("xy").completed is True and second.written == b"cdexy"


def test_neither_the_text_nor_the_keys_typed_are_ever_put_in_the_report():
    clock = FakeClock()
    port = TerminalPort("/dev/x", opener=lambda *a: FakeSerial(clock, fail_write_after=1), clock=clock, sleep=clock.sleep)
    port.transmit("zebra-secret portent")
    assert all("zebra" not in line for line in port.report.lines())


def test_a_port_name_that_is_not_a_device_name_means_nothing_is_typed():
    clock = FakeClock()
    opened = []
    for name in ("", "   ", None, 7):
        port = TerminalPort(name, opener=lambda *a: opened.append(a), clock=clock, sleep=clock.sleep)
        assert port.available is False and len(port.report.warnings) == 1
        assert port.transmit("a").completed is False
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
    report = port.transmit("ab\n")
    assert report.completed is True and opened == [("/dev/ttyTEST", 9600, "xon_xoff")]
    assert fake.written == b"ab\r\n"                                   # the default line ending
    if setting == "characters_per_second":
        assert fake.write_times == pytest.approx([0.0, 1 / 16, 2 / 16])
    if setting == "finish_wait_seconds":
        assert report.seconds == pytest.approx(2 / 16 + 2.0)


def test_strict_mode_raises_for_a_bad_setting_a_port_that_will_not_open_and_a_failed_write():
    with pytest.raises(TerminalPortError):
        TerminalPort("/dev/x", baud=5, strict=True)
    with pytest.raises(TerminalPortError):
        TerminalPort("", strict=True)
    with pytest.raises(TerminalPortError):
        TerminalPort("/dev/x", opener=lambda *a: (_ for _ in ()).throw(OSError()), strict=True).transmit("a")
    clock = FakeClock()
    with pytest.raises(TerminalPortError):
        TerminalPort("/dev/x", opener=lambda *a: FakeSerial(clock, fail_write_after=0), clock=clock,
                       sleep=clock.sleep, strict=True).transmit("a")


def test_close_closes_the_port_and_the_next_call_opens_it_again():
    port, fake, _, opened = make()
    port.transmit("a")
    port.close()
    port.close()                                  # closing twice is fine
    assert fake.closed is True
    port.transmit("b")
    assert len(opened) == 2


def test_closing_a_port_that_was_never_opened_or_that_fails_to_close_is_fine():
    make()[0].close()
    port, fake, _, _ = make()
    port.transmit("a")
    fake.close = lambda: (_ for _ in ()).throw(OSError("already gone"))
    port.close()


def test_the_real_serial_port_is_opened_with_8n1_and_the_chosen_flow_control(monkeypatch):
    seen = []
    monkeypatch.setattr("serial.Serial", lambda *args, **kwargs: seen.append((args, kwargs)) or object())
    terminal_port._open_serial("/dev/ttyACM0", 9600, "xon_xoff")
    terminal_port._open_serial("/dev/ttyACM0", 19200, "rts_cts")
    terminal_port._open_serial("/dev/ttyACM0", 19200, "none")
    (args, kwargs), (_, rts), (_, none) = seen
    assert args == ("/dev/ttyACM0", 9600)
    assert (kwargs["bytesize"], kwargs["parity"], kwargs["stopbits"]) == (8, "N", 1)
    assert (kwargs["xonxoff"], kwargs["rtscts"]) == (True, False)
    assert (rts["xonxoff"], rts["rtscts"]) == (False, True) and (none["xonxoff"], none["rtscts"]) == (False, False)
    assert kwargs["timeout"] == terminal_port._READ_SLICE_SECONDS


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


def test_a_line_that_goes_on_for_ever_is_ended_at_the_limit():
    port, fake, _, _ = make(echoplex=False)
    fake.incoming += b"x" * 503 + b"\r"
    first, second = port.read_line(1), port.read_line(1)
    assert len(first) == terminal_port.DEFAULT_MAX_TYPED_LINE_CHARACTERS == 500 and second == "xxx"


def test_the_typed_line_limit_is_a_setting():
    port, fake, _, _ = make(echoplex=False, max_typed_line_characters=10)
    fake.incoming += b"y" * 25 + b"\r"
    assert [len(port.read_line(1)) for _ in range(3)] == [10, 10, 5]


@pytest.mark.parametrize("option,bad", [("max_typed_line_characters", 0), ("max_typed_line_characters", 2.5), ("max_typed_line_characters", True),
                                        ("max_typed_line_characters", "500"), ("refused_character_retry_seconds", 0),
                                        ("refused_character_retry_seconds", -1), ("refused_character_retry_seconds", "1"),
                                        ("wait_log_reminder_seconds", 0), ("wait_log_reminder_seconds", None), ("wait_log_reminder_seconds", True)])
def test_a_bad_limit_means_the_default_with_a_warning(option, bad):
    port = TerminalPort("/dev/x", **{option: bad})
    assert len(port.report.warnings) == 1 and option in port.report.warnings[0]


@pytest.mark.parametrize("option", ["max_typed_line_characters", "refused_character_retry_seconds", "wait_log_reminder_seconds"])
def test_strict_mode_raises_on_a_bad_limit(option):
    with pytest.raises(TerminalPortError):
        TerminalPort("/dev/x", strict=True, **{option: 0})


def test_a_line_ended_by_the_limit_is_echoed_a_line_ending_like_any_other():
    port, fake, _, _ = make()
    fake.incoming += b"x" * 500
    assert len(port.read_line(1)) == 500
    assert fake.written.endswith(b"x\r\n")


def test_a_port_that_will_not_open_gives_none_after_waiting_the_timeout_not_a_hot_loop():
    clock = FakeClock()
    port = TerminalPort("/dev/none", opener=lambda *a: (_ for _ in ()).throw(OSError()), clock=clock, sleep=clock.sleep)
    assert port.read_line(3) is None
    assert clock.now == pytest.approx(3) and len(port.report.warnings) == 1


def test_waiting_for_ever_on_a_port_that_will_not_open_still_gives_up_each_time_after_a_pause():
    clock = FakeClock()
    port = TerminalPort("/dev/none", opener=lambda *a: (_ for _ in ()).throw(OSError()), clock=clock, sleep=clock.sleep)
    assert port.read_line() is None and clock.now == pytest.approx(1.0)


def test_a_read_that_fails_gives_none_and_a_warning_and_the_port_is_reopened_next_time():
    clock = FakeClock()
    broken, good = FakeSerial(clock), FakeSerial(clock)
    broken.read = lambda size=1: (_ for _ in ()).throw(OSError("unplugged"))
    ports = [broken, good]
    port = TerminalPort("/dev/x", opener=lambda *a: ports.pop(0), clock=clock, sleep=clock.sleep, echoplex=False)
    assert port.read_line(1) is None and broken.closed is True
    assert len(port.report.warnings) == 1 and "keyboard" in port.report.warnings[0]
    good.incoming += b"ok\r"
    assert port.read_line(1) == "ok"


def test_a_failed_echo_is_reported_once_and_the_line_still_comes_back():
    clock = FakeClock()
    fake = FakeSerial(clock, fail_write_after=0)
    port = TerminalPort("/dev/x", opener=lambda *a: fake, clock=clock, sleep=clock.sleep)
    fake.incoming += b"abc\r"
    assert port.read_line(1) == "abc"
    assert len(port.report.warnings) == 1 and "echo" in port.report.warnings[0]


def test_the_keys_typed_are_never_put_in_the_report():
    clock = FakeClock()
    fake = FakeSerial(clock, fail_write_after=0)
    port = TerminalPort("/dev/x", opener=lambda *a: fake, clock=clock, sleep=clock.sleep)
    fake.incoming += b"zebra-secret\r"
    port.read_line(1)
    assert all("zebra" not in line for line in port.report.lines())


def test_strict_mode_raises_for_a_failed_read_and_a_failed_echo():
    clock = FakeClock()
    fake = FakeSerial(clock)
    fake.read = lambda size=1: (_ for _ in ()).throw(OSError())
    with pytest.raises(TerminalPortError):
        TerminalPort("/dev/x", opener=lambda *a: fake, clock=clock, sleep=clock.sleep, strict=True).read_line(1)
    echo_fake = FakeSerial(clock, fail_write_after=0)
    echo_fake.incoming += b"a\r"
    with pytest.raises(TerminalPortError):
        TerminalPort("/dev/x", opener=lambda *a: echo_fake, clock=clock, sleep=clock.sleep, strict=True).read_line(1)


# --- No keys during a portent --------------------------------------------------------------

def test_keys_typed_during_a_portent_are_thrown_away_by_default():
    port, fake, _, _ = make()
    fake.incoming += b"junk"
    port.transmit("portent")
    assert fake.resets == 2 and port.read_line(0.5) is None


def test_bytes_already_read_and_a_half_typed_line_are_thrown_away_too():
    port, fake, _, _ = make(echoplex=False)
    fake.incoming += b"one\rtwo\rha"
    assert port.read_line(1) == "one"
    port.transmit("portent")
    fake.incoming += b"lf\r"
    assert port.read_line(1) == "lf"                      # not "two", and not "halflf"


def test_a_half_typed_line_is_thrown_away_when_a_portent_starts():
    port, fake, _, _ = make(echoplex=False)
    fake.incoming += b"ha"
    assert port.read_line(0.2) is None                    # "ha" is waiting for its C Rtn
    port.transmit("portent")
    fake.incoming += b"lf\r"
    assert port.read_line(1) == "lf"


def test_a_cr_before_the_portent_does_not_swallow_the_first_lf_after_it():
    port, fake, _, _ = make(echoplex=False)
    fake.incoming += b"a\r"
    port.read_line(1)
    port.transmit("p")
    fake.incoming += b"\nb\r"
    assert port.read_line(1) == ""                        # that LF is an empty line, not the second half of a CR+LF pair


def test_with_type_during_printing_on_the_keys_are_kept_for_the_next_read():
    port, fake, _, _ = make(echoplex=False, type_during_printing=True)
    fake.incoming += b"one\rtwo\rha"
    assert port.read_line(1) == "one"
    port.transmit("portent")
    fake.incoming += b"lf\r"
    assert fake.resets == 0
    assert port.read_line(1) == "two" and port.read_line(1) == "half"


def test_typing_when_the_port_will_not_open_does_not_touch_the_keys():
    clock = FakeClock()
    port = TerminalPort("/dev/x", opener=lambda *a: (_ for _ in ()).throw(OSError()), clock=clock, sleep=clock.sleep)
    port._received.extend(b"keep")
    assert port.transmit("a").completed is False and bytes(port._received) == b"keep"


def test_a_reset_that_fails_does_not_stop_the_portent():
    port, fake, _, _ = make()
    fake.reset_input_buffer = lambda: (_ for _ in ()).throw(OSError())
    assert port.transmit("abc").completed is True


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
    port = TerminalPort("/dev/x", opener=lambda *a: fake, characters_per_second=1e9, finish_wait_seconds=0,
                          echoplex=False, type_during_printing=True)
    fake.incoming += b"k\r"
    results = {}
    typing = threading.Thread(target=lambda: results.setdefault("typed", port.transmit("ab")))
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
    return ConsoleTerminal(stream, **kwargs), stream


def test_the_console_typewriter_types_on_the_stream_with_plain_newlines():
    typewriter, stream = console(input_stream=io.StringIO(""))
    report = typewriter.transmit("ab\ncd")
    assert stream.getvalue() == "ab\ncd" and report.completed is True and typewriter.report.ok


def test_the_console_terminal_is_a_terminal_port_with_the_same_filter():
    typewriter, stream = console(input_stream=io.StringIO(""))
    assert isinstance(typewriter, TerminalPort)
    typewriter.transmit("a\x1bb")
    assert stream.getvalue() == "ab"


def test_the_console_typewriter_is_paced_like_the_real_one():
    clock = FakeClock()
    stream = io.StringIO()
    typewriter = ConsoleTerminal(stream, input_stream=io.StringIO(""), characters_per_second=10, clock=clock,
                                   sleep=clock.sleep)
    assert typewriter.transmit("abcd").seconds == pytest.approx(0.3)


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
        typewriter.transmit("portent")                           # throws away what was typed
        os.write(write_end, b"after\n")
        assert typewriter.read_line(2) == "after"
        os.close(write_end)


def test_the_console_input_is_kept_when_typing_during_output_is_on():
    read_end, write_end = os.pipe()
    with os.fdopen(read_end) as keyboard:
        typewriter, _ = console(input_stream=keyboard, type_during_printing=True)
        os.write(write_end, b"kept\n")
        typewriter.transmit("portent")                           # does not throw away what was typed
        assert typewriter.read_line(2) == "kept"
        os.close(write_end)


def test_the_console_input_with_nothing_typed_gives_none_after_the_timeout():
    read_end, write_end = os.pipe()
    with os.fdopen(read_end) as keyboard:
        typewriter, _ = console(input_stream=keyboard)
        assert typewriter.read_line(0.3) is None
    os.close(write_end)


def test_a_console_with_no_keyboard_reads_nothing():
    sink = terminal_port._StreamSink(io.StringIO(), None)
    assert sink.read(5) == b"" and sink.reset_input_buffer() is None
    sink.close()


# --- Staying with the portent: stalls, a lost port, a port that comes back under another name --------------------

class SerialTimeoutException(OSError):
    """Named like pyserial's write timeout, which is how a held or out-of-paper machine looks under flow control."""


class ScriptedSerial(FakeSerial):
    """A FakeSerial whose write calls fail as scripted: `failures` maps the number of the write call (from 0) to the error raised."""

    def __init__(self, clock=None, failures=None):
        super().__init__(clock)
        self.attempts = 0
        self.failures = dict(failures or {})

    def write(self, data):
        number = self.attempts
        self.attempts += 1
        if number in self.failures:
            raise self.failures[number]
        super().write(data)


def stalled_port(failures, **settings):
    """A port over one ScriptedSerial that never needs reopening. Returns (port, serial, clock, opened, beats)."""
    clock = FakeClock()
    serial_port = ScriptedSerial(clock, failures)
    opened = []
    settings.setdefault("finish_wait_seconds", 0)
    port = TerminalPort("/dev/ttyTEST", opener=lambda *a: opened.append(a) or serial_port, clock=clock, sleep=clock.sleep, **settings)
    return port, serial_port, clock, opened


class TestAHeldMachine:
    """The machine stops taking characters (hold button, no paper, busy): wait, send the same character again, repeat nothing."""

    def test_the_same_character_is_sent_again_and_nothing_is_repeated_or_lost(self):
        port, serial_port, _, opened = stalled_port({3: SerialTimeoutException("held"), 4: SerialTimeoutException("held"),
                                                     5: SerialTimeoutException("held")})
        report = port.transmit("abcdef")
        assert serial_port.written == b"abcdef" and report.completed is True and report.characters == 6
        assert len(opened) == 1 and serial_port.closed is False             # the port was kept open

    def test_a_python_timeout_is_a_stall_too(self):
        port, serial_port, _, opened = stalled_port({1: TimeoutError("slow")})
        assert port.transmit("abc").completed is True and serial_port.written == b"abc" and len(opened) == 1

    def test_it_is_one_warning_for_the_whole_stall_and_a_line_in_the_log_when_typing_goes_on(self, caplog):
        port, _, _, _ = stalled_port({n: SerialTimeoutException("held") for n in range(2, 12)})
        with caplog.at_level(logging.INFO, logger="pom.printing.terminal_port"):
            port.transmit("abcdef")
        assert len(port.report.warnings) == 1 and "stopped taking characters" in port.report.warnings[0] and "2 of 6" in port.report.warnings[0]
        assert any("typing again after" in r.getMessage() for r in caplog.records)

    def test_a_long_stall_is_mentioned_in_the_log_every_minute(self, caplog):
        port, _, _, _ = stalled_port({n: SerialTimeoutException("held") for n in range(2, 140)})
        with caplog.at_level(logging.WARNING, logger="pom.printing.terminal_port"):
            port.transmit("abcdef")
        reminders = [r.getMessage() for r in caplog.records if "still not typing" in r.getMessage()]
        assert len(reminders) == 2 and "2 of 6 characters" in reminders[0]

    def test_the_reminder_interval_is_a_setting(self, caplog):
        port, _, _, _ = stalled_port({n: SerialTimeoutException("held") for n in range(2, 140)}, wait_log_reminder_seconds=30)
        with caplog.at_level(logging.WARNING, logger="pom.printing.terminal_port"):
            port.transmit("abcdef")
        assert len([r for r in caplog.records if "still not typing" in r.getMessage()]) == 4          # 138 s of stall, a reminder every 30

    def test_the_pause_before_a_refused_character_is_sent_again_is_a_setting(self):
        failures = {n: SerialTimeoutException("held") for n in range(2, 12)}                           # ten refusals in a row
        slow, slow_serial, _, _ = stalled_port(dict(failures), refused_character_retry_seconds=5)
        quick, quick_serial, _, _ = stalled_port(dict(failures))
        for port in (slow, quick):
            port.transmit("abcd")
        wait = lambda s: s.write_times[2] - s.write_times[1]
        assert wait(slow_serial) > 45 and wait(quick_serial) < 15

    def test_the_heartbeat_is_called_while_waiting(self):
        port, _, _, _ = stalled_port({n: SerialTimeoutException("held") for n in range(1, 8)})
        beats = []
        port.transmit("abcd", still_alive=lambda: beats.append(1))
        assert len(beats) == 7

    def test_a_heartbeat_that_fails_does_not_end_the_wait(self):
        port, serial_port, _, _ = stalled_port({1: SerialTimeoutException("held")})

        def broken():
            raise RuntimeError("beat broke")
        assert port.transmit("abc", still_alive=broken).completed is True and serial_port.written == b"abc"

    def test_the_pacing_starts_again_from_the_moment_it_goes_on(self):
        port, serial_port, clock, _ = stalled_port({n: SerialTimeoutException("held") for n in range(2, 62)})
        port.transmit("abcd")
        gap = serial_port.write_times[2] - serial_port.write_times[1]
        assert gap > 50                                                       # the long wait
        assert serial_port.write_times[3] - serial_port.write_times[2] == pytest.approx(1 / 16)       # then the usual pace, not a flood

    def test_a_machine_held_for_ever_is_given_up_after_the_patience_with_how_far_it_got(self):
        port, serial_port, clock, _ = stalled_port({n: SerialTimeoutException("held") for n in range(2, 100000)}, stall_patience_seconds=300)
        report = port.transmit("abcdef")
        assert report.characters == 2 and report.completed is False and serial_port.closed is True
        assert 300 <= clock.now < 310
        assert len(port.report.criticals) == 1 and "300 s" in port.report.criticals[0] and "2 of 6" in port.report.criticals[0]

    def test_the_next_text_after_giving_up_opens_the_port_again(self):
        clock = FakeClock()
        ports = [ScriptedSerial(clock, {n: SerialTimeoutException("held") for n in range(0, 100000)}), ScriptedSerial(clock)]
        port = TerminalPort("/dev/x", opener=lambda *a: ports.pop(0), clock=clock, sleep=clock.sleep, finish_wait_seconds=0,
                            stall_patience_seconds=30)
        assert port.transmit("abc").completed is False
        assert port.transmit("xyz").completed is True

    def test_neither_the_text_nor_the_state_of_the_wait_leaks_words(self, caplog):
        port, _, _, _ = stalled_port({n: SerialTimeoutException("held") for n in range(2, 200)}, stall_patience_seconds=100)
        with caplog.at_level(logging.DEBUG):
            port.transmit("zebra-secret portent")
        assert "zebra" not in caplog.text and all("zebra" not in line for line in port.report.lines())


class TestAPortThatIsLost:
    """The cable is pulled, or the port fails: close it, open it again every few seconds, go on from the first character not sent."""

    def test_it_waits_for_the_port_to_come_back_and_goes_on_where_it_stopped(self):
        clock = FakeClock()
        first = ScriptedSerial(clock, {3: OSError("unplugged")})
        second = ScriptedSerial(clock)
        attempts = []

        def opener(name, baud, flow):
            attempts.append(name)
            if len(attempts) == 1:
                return first
            if len(attempts) <= 4:
                raise OSError("not there yet")
            return second
        port = TerminalPort("/dev/x", opener=opener, clock=clock, sleep=clock.sleep, finish_wait_seconds=0, reconnect_seconds=2.0)
        report = port.transmit("abcdefg")
        assert report.completed is True and first.written == b"abc" and second.written == b"defg"
        assert len(attempts) == 5 and clock.sleeps.count(2.0) == 4          # a try every 2 s

    def test_one_warning_for_the_stretch_not_one_per_try(self):
        clock = FakeClock()
        first, second = ScriptedSerial(clock, {1: OSError("unplugged")}), ScriptedSerial(clock)
        tries = []

        def opener(*a):
            tries.append(1)
            if len(tries) == 1:
                return first
            if len(tries) < 10:
                raise OSError("gone")
            return second
        port = TerminalPort("/dev/x", opener=opener, clock=clock, sleep=clock.sleep, finish_wait_seconds=0)
        port.transmit("abc")
        assert len(port.report.warnings) == 1 and "port was lost" in port.report.warnings[0]

    def test_a_port_that_never_comes_back_is_given_up_after_the_patience(self):
        clock = FakeClock()
        first = ScriptedSerial(clock, {2: OSError("unplugged")})
        opened = []

        def opener(*a):
            opened.append(1)
            if len(opened) == 1:
                return first
            raise OSError("gone")
        port = TerminalPort("/dev/x", opener=opener, clock=clock, sleep=clock.sleep, finish_wait_seconds=0, stall_patience_seconds=120)
        report = port.transmit("abcdef")
        assert report.characters == 2 and report.completed is False
        assert len(port.report.criticals) == 1 and "120 s" in port.report.criticals[0]
        assert 120 <= clock.now < 126

    def test_the_heartbeat_is_called_each_time_it_tries_again(self):
        clock = FakeClock()
        first, second = ScriptedSerial(clock, {1: OSError("unplugged")}), ScriptedSerial(clock)
        tries = []

        def opener(*a):
            tries.append(1)
            if len(tries) == 1:
                return first
            if len(tries) <= 5:
                raise OSError("gone")
            return second
        port = TerminalPort("/dev/x", opener=opener, clock=clock, sleep=clock.sleep, finish_wait_seconds=0)
        beats = []
        port.transmit("abc", still_alive=lambda: beats.append(1))
        assert len(beats) == 5

    def test_a_stall_that_turns_into_a_lost_port_is_handled(self):
        clock = FakeClock()
        first = ScriptedSerial(clock, {2: SerialTimeoutException("held"), 3: OSError("then unplugged")})
        second = ScriptedSerial(clock)
        ports = [first, second]
        port = TerminalPort("/dev/x", opener=lambda *a: ports.pop(0), clock=clock, sleep=clock.sleep, finish_wait_seconds=0)
        report = port.transmit("abcde")
        assert report.completed is True and first.written == b"ab" and second.written == b"cde"

    def test_a_failure_at_the_last_flush_is_reported_with_every_character_counted(self):
        clock = FakeClock()
        serial_port = FakeSerial(clock)
        serial_port.flush = lambda: (_ for _ in ()).throw(OSError("flush failed"))
        port = TerminalPort("/dev/x", opener=lambda *a: serial_port, clock=clock, sleep=clock.sleep, finish_wait_seconds=0)
        report = port.transmit("abc")
        assert report.characters == 3 and report.completed is False and serial_port.closed is True

    def test_in_strict_mode_the_first_failure_raises(self):
        clock = FakeClock()
        port = TerminalPort("/dev/x", opener=lambda *a: ScriptedSerial(clock, {1: OSError("x")}), clock=clock, sleep=clock.sleep,
                            strict=True)
        with pytest.raises(TerminalPortError):
            port.transmit("abc")


class TestThePortNameAsAPattern:
    def test_the_first_match_in_sorted_order_is_opened(self):
        clock = FakeClock()
        seen = []
        port = TerminalPort("/dev/ttyACM*", opener=lambda name, *a: seen.append(name) or FakeSerial(clock), clock=clock, sleep=clock.sleep,
                            finish_wait_seconds=0, lister=lambda pattern: ["/dev/ttyACM1", "/dev/ttyACM0"])
        port.transmit("a")
        assert seen == ["/dev/ttyACM0"]

    def test_with_several_matches_the_first_in_sorted_order_is_chosen_whatever_order_they_come_in(self):
        clock = FakeClock()
        seen = []
        port = TerminalPort("/dev/ttyACM*", opener=lambda name, *a: seen.append(name) or FakeSerial(clock), clock=clock, sleep=clock.sleep,
                            finish_wait_seconds=0, lister=lambda pattern: ["/dev/ttyACM2", "/dev/ttyACM0", "/dev/ttyACM1"])
        port.transmit("a")
        assert seen == ["/dev/ttyACM0"]

    def test_a_printer_that_comes_back_under_another_name_is_found(self):
        clock = FakeClock()
        names = [["/dev/ttyACM0"]]
        first, second = ScriptedSerial(clock, {1: OSError("unplugged")}), ScriptedSerial(clock)
        seen = []

        def opener(name, *a):
            seen.append(name)
            return first if len(seen) == 1 else second

        def lister(pattern):
            if len(seen) == 1:
                names[0] = ["/dev/ttyACM1"]                # replugged: the device has a new number
            return names[0]
        port = TerminalPort("/dev/ttyACM*", opener=opener, clock=clock, sleep=clock.sleep, finish_wait_seconds=0, lister=lister)
        report = port.transmit("abcd")
        assert seen == ["/dev/ttyACM0", "/dev/ttyACM1"] and report.completed is True and second.written == b"bcd"

    def test_a_pattern_with_no_match_means_the_port_will_not_open_with_one_warning(self):
        clock = FakeClock()
        port = TerminalPort("/dev/serial/by-id/usb-*", opener=lambda *a: FakeSerial(clock), clock=clock, sleep=clock.sleep,
                            lister=lambda pattern: [])
        for _ in range(3):
            assert port.transmit("a") == PrintResult(0, 0.0, False)
        assert len(port.report.warnings) == 1 and "would not open" in port.report.warnings[0]

    def test_a_lookup_that_fails_is_the_same_as_no_match(self):
        clock = FakeClock()
        port = TerminalPort("/dev/ttyACM*", opener=lambda *a: FakeSerial(clock), clock=clock, sleep=clock.sleep,
                            lister=lambda pattern: (_ for _ in ()).throw(OSError("glob broke")))
        assert port.transmit("a").completed is False

    def test_a_plain_name_is_opened_as_it_is_without_any_lookup(self):
        clock = FakeClock()
        seen = []
        port = TerminalPort("/dev/ttyACM0", opener=lambda name, *a: seen.append(name) or FakeSerial(clock), clock=clock,
                            sleep=clock.sleep, finish_wait_seconds=0, lister=lambda pattern: (_ for _ in ()).throw(AssertionError("no lookup")))
        port.transmit("a")
        assert seen == ["/dev/ttyACM0"]


class TestAPortThatWillNotOpen:
    def test_it_is_one_warning_however_many_tries_and_a_line_in_the_log_when_it_opens(self, caplog):
        clock = FakeClock()
        up = []

        def opener(*a):
            if not up:
                raise OSError("not plugged in")
            return FakeSerial(clock)
        port = TerminalPort("/dev/x", opener=opener, clock=clock, sleep=clock.sleep, finish_wait_seconds=0)
        for _ in range(5):
            assert port.transmit("a").completed is False
        assert len(port.report.warnings) == 1
        up.append(1)
        with caplog.at_level(logging.INFO, logger="pom.printing.terminal_port"):
            assert port.transmit("a").completed is True
        assert any("is open again" in r.getMessage() for r in caplog.records)

    def test_a_second_stretch_of_trouble_is_a_new_warning(self):
        clock = FakeClock()
        state = {"up": False}

        def opener(*a):
            if not state["up"]:
                raise OSError("no")
            return FakeSerial(clock)
        port = TerminalPort("/dev/x", opener=opener, clock=clock, sleep=clock.sleep, finish_wait_seconds=0)
        port.transmit("a")
        state["up"] = True
        port.transmit("a")
        port.close()
        state["up"] = False
        port.transmit("a")
        assert len(port.report.warnings) == 2


class TestOnline:
    """`online()` asks whether the typewriter is there without typing anything or complaining."""

    def test_an_open_port_is_online_and_nothing_is_typed(self):
        port, fake, _, opened = make()
        assert port.online() is True and fake.written == b"" and len(opened) == 1

    def test_a_port_that_will_not_open_is_offline_quietly_however_often_it_is_asked(self):
        def opener(*a):
            raise OSError("not plugged in")
        port = TerminalPort("/dev/x", opener=opener, finish_wait_seconds=0)
        assert [port.online() for _ in range(5)] == [False] * 5
        assert port.report.ok

    def test_it_turns_online_when_the_typewriter_is_plugged_in(self):
        clock = FakeClock()
        state = {"up": False}

        def opener(*a):
            if not state["up"]:
                raise OSError("no")
            return FakeSerial(clock)
        port = TerminalPort("/dev/x", opener=opener, clock=clock, sleep=clock.sleep, finish_wait_seconds=0)
        assert port.online() is False
        state["up"] = True
        assert port.online() is True

    def test_a_port_name_that_cannot_be_used_is_never_online(self):
        port = TerminalPort("", opener=lambda *a: FakeSerial(), finish_wait_seconds=0)
        assert port.online() is False


class TestOnlineWithoutWaiting:
    """The monitoring page asks `online(wait=False)`: typing holds the port for minutes, and the page must not stall behind it."""

    @staticmethod
    def busy(port):
        """Holds the port's lock from another thread, as a portent being typed does; returns a function that lets go."""
        held, release = threading.Event(), threading.Event()

        def hold():
            with port._lock:
                held.set()
                release.wait(5)
        thread = threading.Thread(target=hold)
        thread.start()
        assert held.wait(2)
        return lambda: (release.set(), thread.join(2))

    def test_while_a_portent_is_being_typed_it_answers_true_at_once(self):
        port, _, _, opened = make()
        let_go = self.busy(port)
        try:
            started = time.monotonic()
            assert port.online(wait=False) is True and time.monotonic() - started < 0.5
            assert opened == []                                        # it did not touch the port
        finally:
            let_go()

    def test_while_typing_it_answers_true_even_for_a_port_that_could_never_open(self):
        port = TerminalPort("", opener=lambda *a: FakeSerial(), finish_wait_seconds=0)
        let_go = self.busy(port)
        try:
            assert port.online(wait=False) is True
        finally:
            let_go()

    def test_with_the_wait_it_waits_for_the_typing_to_finish(self):
        port, _, _, _ = make()
        let_go = self.busy(port)
        answers = []
        asker = threading.Thread(target=lambda: answers.append(port.online()))
        asker.start()
        asker.join(0.3)
        still_waiting = asker.is_alive() and answers == []
        let_go()
        asker.join(2)
        assert still_waiting and answers == [True]

    def test_when_nothing_is_typing_it_asks_the_port_like_online(self):
        port, fake, _, opened = make()
        assert port.online(wait=False) is True and len(opened) == 1 and fake.written == b""

    def test_a_port_that_will_not_open_is_offline_when_nothing_is_typing(self):
        def opener(*a):
            raise OSError("not plugged in")
        port = TerminalPort("/dev/x", opener=opener, finish_wait_seconds=0)
        assert port.online(wait=False) is False and port.report.ok

    def test_a_port_name_that_cannot_be_used_is_offline_when_nothing_is_typing(self):
        assert TerminalPort("", opener=lambda *a: FakeSerial(), finish_wait_seconds=0).online(wait=False) is False

    @pytest.mark.parametrize("wait", [True, False])
    def test_it_never_leaves_the_port_locked(self, wait):
        port, _, _, _ = make()
        port.online(wait=wait)
        assert port._lock.acquire(timeout=0.5)
        port._lock.release()

    def test_it_never_leaves_the_port_locked_when_the_opener_raises_something_odd(self):
        class Odd(Exception):
            pass

        def opener(*a):
            raise Odd("odd")
        port = TerminalPort("/dev/x", opener=opener, finish_wait_seconds=0)
        assert port.online(wait=False) is False
        assert port._lock.acquire(timeout=0.5)
        port._lock.release()

    def test_the_default_waits(self):
        import inspect
        assert inspect.signature(TerminalPort.online).parameters["wait"].default is True


@pytest.mark.parametrize("setting", ["stall_patience_seconds", "reconnect_seconds"])
@pytest.mark.parametrize("bad", [0, -1, "x", None, True, float("nan")])
def test_a_bad_patience_or_reconnect_is_the_default_with_a_warning(setting, bad):
    port, _, _, _ = make(**{setting: bad})
    assert len(port.report.warnings) == 1 and setting in port.report.warnings[0]
