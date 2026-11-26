"""Tests for pom.bookkeeper.log_muter: the same message, repeated, is let through a few times, then muted, with a LOGMUTE line saying so.

The muter is tested the way a handler uses it (`filter(record)`), with a fake clock, so no test waits for time to pass."""
import logging
import threading
import time

import pytest

from pom.bookkeeper.log_muter import LogMuter

MUTING = "LOGMUTE: Last message repeating and muted"


class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def record(message="the microphone stopped", name="pom.mic", level=logging.WARNING, *args):
    return logging.LogRecord(name, level, "mic.py", 1, message, args, None)


def made(after=3, remind=4, quiet=300.0):
    clock = FakeClock()
    return LogMuter(after, remind, quiet, clock=clock), clock


def sent(muter, message="the microphone stopped", name="pom.mic", level=logging.WARNING, *args):
    """Sends one record through the muter; returns what a handler would print (its text lines), or None if it was dropped."""
    rec = record(message, name, level, *args)
    return rec.getMessage().split("\n") if muter.filter(rec) else None


def test_it_is_a_logging_filter():
    assert isinstance(LogMuter(3, 4, 300.0), logging.Filter)


# --- the first lines go through, then the muting ------------------------------------------------

def test_the_first_lines_before_the_limit_go_through_untouched():
    muter, _ = made(after=3)
    assert sent(muter) == ["the microphone stopped"]
    assert sent(muter) == ["the microphone stopped"]


def test_the_line_that_reaches_the_limit_goes_through_with_the_muting_line_under_it():
    muter, _ = made(after=3)
    sent(muter), sent(muter)
    assert sent(muter) == ["the microphone stopped", MUTING]


def test_after_the_limit_the_same_message_is_dropped():
    muter, _ = made(after=3, remind=100)
    for _ in range(3):
        sent(muter)
    assert [sent(muter) for _ in range(5)] == [None] * 5


def test_a_limit_of_one_lets_the_message_through_once_and_mutes_it():
    muter, _ = made(after=1, remind=100)
    assert sent(muter) == ["the microphone stopped", MUTING]
    assert sent(muter) is None


def test_the_first_line_of_a_line_that_goes_through_is_always_the_original_text():
    muter, _ = made(after=2, remind=1)
    for _ in range(6):
        out = sent(muter)
        assert out is None or out[0] == "the microphone stopped"


# --- the reminders -------------------------------------------------------------------------------

def test_every_so_many_dropped_lines_one_goes_through_with_the_count_so_far():
    muter, _ = made(after=3, remind=4)
    for _ in range(3):
        sent(muter)
    assert [sent(muter) for _ in range(3)] == [None, None, None]                 # repeats 1, 2, 3
    assert sent(muter) == ["the microphone stopped", "LOGMUTE: Last message repeated 4 times so far, still muted"]
    assert [sent(muter) for _ in range(3)] == [None, None, None]                 # repeats 5, 6, 7
    assert sent(muter) == ["the microphone stopped", "LOGMUTE: Last message repeated 8 times so far, still muted"]


def test_a_reminder_of_one_lets_every_repeat_through_with_its_count():
    muter, _ = made(after=1, remind=1)
    sent(muter)
    assert sent(muter) == ["the microphone stopped", "LOGMUTE: Last message repeated 1 times so far, still muted"]
    assert sent(muter) == ["the microphone stopped", "LOGMUTE: Last message repeated 2 times so far, still muted"]


# --- the quiet spell -----------------------------------------------------------------------------

def test_after_a_quiet_spell_the_next_line_goes_through_with_how_many_were_dropped():
    muter, clock = made(after=3, remind=100, quiet=300.0)
    for _ in range(3 + 7):
        sent(muter)
    clock.advance(300.0)
    assert sent(muter) == ["the microphone stopped", "LOGMUTE: Last message repeated 7 times"]


def test_a_gap_just_short_of_the_quiet_spell_does_not_end_the_muting():
    muter, clock = made(after=2, remind=100, quiet=300.0)
    for _ in range(4):
        sent(muter)
    clock.advance(299.9)
    assert sent(muter) is None


def test_every_repeat_restarts_the_quiet_clock_even_a_dropped_one():
    muter, clock = made(after=2, remind=1000, quiet=300.0)
    for _ in range(3):
        sent(muter)
    for _ in range(10):                      # 10 repeats, 200 s apart: 2000 s in all, and never 300 s quiet
        clock.advance(200.0)
        assert sent(muter) is None


def test_after_the_quiet_spell_the_counting_starts_again():
    muter, clock = made(after=3, remind=100, quiet=300.0)
    for _ in range(6):
        sent(muter)
    clock.advance(300.0)
    sent(muter)                                                                  # the first of a new run (it carries the old run's count)
    assert sent(muter) == ["the microphone stopped"]                             # the second
    assert sent(muter) == ["the microphone stopped", MUTING]                     # the third reaches the limit again
    assert sent(muter) is None


def test_a_quiet_spell_after_a_run_that_never_reached_the_limit_adds_nothing():
    muter, clock = made(after=5)
    sent(muter), sent(muter)
    clock.advance(1000.0)
    assert sent(muter) == ["the microphone stopped"]


def test_a_quiet_spell_after_the_muting_but_before_any_drop_adds_nothing():
    muter, clock = made(after=2, remind=100)
    sent(muter), sent(muter)                 # the muting line went out, nothing was dropped
    clock.advance(1000.0)
    assert sent(muter) == ["the microphone stopped"]


# --- what counts as the same message -------------------------------------------------------------

def test_messages_that_differ_only_in_their_numbers_are_the_same_message():
    muter, _ = made(after=3, remind=100)
    assert sent(muter, "Microphone not found after 49s") == ["Microphone not found after 49s"]
    assert sent(muter, "Microphone not found after 50s") == ["Microphone not found after 50s"]
    assert sent(muter, "Microphone not found after 51s") == ["Microphone not found after 51s", MUTING]
    assert sent(muter, "Microphone not found after 52s") is None


def test_the_line_that_goes_through_prints_its_own_numbers():
    muter, clock = made(after=2, remind=100, quiet=60.0)
    sent(muter, "Microphone not found after 50s")
    sent(muter, "Microphone not found after 51s")                                # the second line reaches the limit
    for n in range(52, 101):                                                     # 49 more, all dropped
        sent(muter, f"Microphone not found after {n}s")
    clock.advance(60.0)
    assert sent(muter, "Microphone not found after 101s") == ["Microphone not found after 101s", "LOGMUTE: Last message repeated 49 times"]


def test_messages_with_different_words_are_different_messages():
    muter, _ = made(after=1, remind=100)
    assert sent(muter, "the microphone stopped") is not None
    assert sent(muter, "the terminal stopped") is not None
    assert sent(muter, "the microphone stopped") is None


def test_the_same_text_from_another_logger_is_another_message():
    muter, _ = made(after=1, remind=100)
    assert sent(muter, name="pom.mic") is not None
    assert sent(muter, name="pom.terminal") is not None
    assert sent(muter, name="pom.mic") is None


def test_the_same_text_at_another_level_is_another_message():
    muter, _ = made(after=1, remind=100)
    assert sent(muter, level=logging.WARNING) is not None
    assert sent(muter, level=logging.CRITICAL) is not None
    assert sent(muter, level=logging.WARNING) is None


def test_the_text_is_the_formatted_message_so_percent_arguments_count():
    muter, _ = made(after=1, remind=100)
    assert sent(muter, "no card %s (try %d)", "pom.x", logging.WARNING, "alpha", 1) == ["no card alpha (try 1)", MUTING]
    assert sent(muter, "no card %s (try %d)", "pom.x", logging.WARNING, "alpha", 2) is None
    assert sent(muter, "no card %s (try %d)", "pom.x", logging.WARNING, "beta", 3) is not None     # a different word is a different message


def test_a_muted_message_does_not_hold_back_other_messages():
    muter, _ = made(after=1, remind=100)
    sent(muter, "one"), sent(muter, "one")
    assert sent(muter, "two") is not None


# --- debug is never muted ------------------------------------------------------------------------

def test_debug_lines_are_never_muted_however_many_there_are():
    muter, _ = made(after=1, remind=1)
    assert all(sent(muter, "scored a clip of 5 words", level=logging.DEBUG) == ["scored a clip of 5 words"] for _ in range(50))


@pytest.mark.parametrize("level", [logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL])
def test_every_level_above_debug_is_muted(level):
    muter, _ = made(after=1, remind=100)
    sent(muter, level=level)
    assert sent(muter, level=level) is None


def test_the_record_keeps_its_own_level():
    muter, _ = made(after=1, remind=100)
    rec = record(level=logging.CRITICAL)
    assert muter.filter(rec) is True and rec.levelno == logging.CRITICAL and rec.levelname == "CRITICAL"


# --- one record through several handlers ---------------------------------------------------------

def test_a_record_that_passes_through_two_handlers_is_counted_once_and_gets_the_same_answer():
    muter, clock = made(after=2, remind=100)
    first = record()
    assert muter.filter(first) and muter.filter(first)                           # the stream handler and the file handler
    second = record()
    assert muter.filter(second) and second.getMessage().endswith(MUTING)
    assert muter.filter(second) and second.getMessage().count("LOGMUTE") == 1      # asked again: the note is not added twice
    third = record()
    assert not muter.filter(third) and not muter.filter(third)
    clock.advance(300.0)
    assert sent(muter) == ["the microphone stopped", "LOGMUTE: Last message repeated 1 times"]


# --- it never gets in the way --------------------------------------------------------------------

def test_a_record_that_cannot_be_formatted_goes_through():
    muter, _ = made()
    broken = record("needs %d", "pom.x", logging.WARNING, "not a number")
    assert muter.filter(broken) is True


def test_a_record_whose_text_raises_goes_through_every_time():
    class Bad(logging.LogRecord):
        def getMessage(self):
            raise RuntimeError("no")
    muter, _ = made(after=1, remind=100)
    for _ in range(5):
        assert muter.filter(Bad("pom.x", logging.WARNING, "f.py", 1, "x", (), None)) is True


# --- the table stays small -----------------------------------------------------------------------

def test_messages_that_were_never_muted_are_forgotten_after_a_quiet_spell():
    muter, clock = made(after=5, quiet=300.0)
    for word in ("a", "b", "c", "d"):
        sent(muter, f"stopped {word}")
    assert muter.tracked == 4
    clock.advance(301.0)
    sent(muter, "stopped e")
    assert muter.tracked == 1


def test_a_muted_message_is_remembered_until_it_comes_back_because_it_holds_the_count():
    muter, clock = made(after=2, remind=100, quiet=300.0)
    sent(muter), sent(muter), sent(muter)                    # muted, and one line dropped
    clock.advance(10000.0)
    sent(muter, "something else")
    assert muter.tracked == 2
    assert sent(muter) == ["the microphone stopped", "LOGMUTE: Last message repeated 1 times"]


def test_a_muted_message_with_nothing_dropped_is_forgotten_with_the_others():
    muter, clock = made(after=1, remind=100, quiet=300.0)
    sent(muter)                                              # muted, but nothing dropped
    clock.advance(301.0)
    sent(muter, "something else")
    assert muter.tracked == 1


# --- threads -------------------------------------------------------------------------------------

def test_many_threads_sending_the_same_message_let_through_exactly_the_limit():
    def slow_clock():
        time.sleep(0.0002)                    # a slow clock stretches the time spent counting, so threads that are not kept apart overlap
        return time.monotonic()

    muter = LogMuter(10, 100000, 300.0, clock=slow_clock)
    passed = []

    def worker():
        for _ in range(40):
            passed.append(muter.filter(record()))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert passed.count(True) == 10 and passed.count(False) == 8 * 40 - 10


def test_two_threads_are_never_counting_at_the_same_time():
    """The clock is read while a line is being counted, so a clock that notices company shows whether the threads are kept apart."""
    gate = threading.Lock()
    inside = []
    most_at_once = [0]

    def watching_clock():
        with gate:
            inside.append(1)
            most_at_once[0] = max(most_at_once[0], len(inside))
        time.sleep(0.002)                     # long enough for the other threads to arrive
        with gate:
            inside.pop()
        return time.monotonic()

    muter = LogMuter(10, 100000, 300.0, clock=watching_clock)
    threads = [threading.Thread(target=lambda: [muter.filter(record()) for _ in range(5)]) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert most_at_once[0] == 1


# --- when the table is cleared out ---------------------------------------------------------------

def test_a_message_that_went_quiet_with_nothing_dropped_is_let_through_plainly_even_when_no_sweep_has_forgotten_it():
    muter, clock = made(after=5, quiet=300.0)
    clock.advance(100.0)
    sent(muter, "stopped a")                                 # seen at 100
    clock.advance(200.0)
    sent(muter, "stopped b")                                 # at 300 the first sweep runs; a is only 200 old, so it is kept
    clock.advance(200.0)                                     # at 500 no sweep is due (200 since the last), and a is 400 quiet
    assert sent(muter, "stopped a") == ["stopped a"]


def test_the_table_is_cleared_out_at_most_once_a_quiet_spell():
    muter, clock = made(after=5, quiet=300.0)
    clock.advance(100.0)
    sent(muter, "stopped a")
    clock.advance(200.0)
    sent(muter, "stopped b")                                 # a sweep runs now; a is kept
    clock.advance(150.0)
    sent(muter, "stopped c")                                 # only 150 since the last sweep: none now, so quiet a is still held
    assert muter.tracked == 3


def test_everything_quiet_is_forgotten_exactly_one_quiet_spell_later():
    muter, clock = made(after=5, quiet=300.0)
    sent(muter, "stopped a"), sent(muter, "stopped b")
    clock.advance(300.0)                                     # exactly: the sweep is due and both are exactly quiet
    sent(muter, "stopped c")
    assert muter.tracked == 1


def test_a_sweep_keeps_a_message_that_is_not_quiet_yet_and_the_count_it_has():
    muter, clock = made(after=3, quiet=300.0)
    clock.advance(250.0)
    sent(muter, "stopped a"), sent(muter, "stopped a")       # two lines of it, 50 s before the sweep
    clock.advance(50.0)
    sent(muter, "stopped b")                                 # the sweep runs: a is only 50 old and must keep its count
    clock.advance(1.0)
    assert sent(muter, "stopped a") == ["stopped a", MUTING]


# --- When the muter itself fails ----------------------------------------------------------

def broken_record():
    """A record whose text cannot be built (a %d given a word), so the muter fails on it."""
    return logging.LogRecord("pom.mic", logging.WARNING, "mic.py", 1, "count %d", ("many",), None)


def test_a_line_the_muter_fails_on_goes_through():
    muter, _ = made()
    assert muter.filter(broken_record()) is True


def test_the_muter_s_own_failure_is_written_once_to_stderr_naming_only_the_kind_of_error_as_python_s_logging_does(capsys):
    """Python's logging reports its own failures on stderr, not through the log (which would pass back through the part that failed)."""
    muter, _ = made()
    for _ in range(3):
        muter.filter(broken_record())
    assert capsys.readouterr().err == "WARNING pom.bookkeeper.log_muter: the log muter failed (TypeError); lines go through unmuted\n"


def test_the_muter_s_failure_is_not_sent_through_the_log(caplog):
    muter, _ = made()
    with caplog.at_level(logging.DEBUG):
        muter.filter(broken_record())
    assert caplog.records == []
