"""Tests for pom.bookkeeper.log_recorder: a class's logger. It is Python's own logger (through `logging.LoggerAdapter`), plus strict mode."""
import logging

import pytest

from pom.bookkeeper.log_recorder import LogRecorder

NAME = "pom.test.recorder"


class TrialError(Exception):
    """A class's own strict exception."""


def recorder(strict=False, strict_exception=TrialError):
    return LogRecorder(logging.getLogger(NAME), strict_exception, strict)


MODES = [("debug", logging.DEBUG), ("info", logging.INFO), ("warning", logging.WARNING), ("error", logging.ERROR),
         ("critical", logging.CRITICAL)]


def test_it_is_pythons_logger_adapter():
    assert isinstance(recorder(), logging.LoggerAdapter)


@pytest.mark.parametrize("mode, level", MODES)
def test_each_mode_logs_at_its_own_level_under_the_class_s_logger_name(caplog, mode, level):
    with caplog.at_level(logging.DEBUG, logger=NAME):
        getattr(recorder(), mode)("scored %d clips in %.1f s", 3, 2.5)
    assert [(r.name, r.levelno, r.getMessage()) for r in caplog.records] == [(NAME, level, "scored 3 clips in 2.5 s")]


def test_exception_logs_at_error_with_the_traceback(caplog):
    with caplog.at_level(logging.DEBUG, logger=NAME):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            recorder().exception("the turn failed")
    assert caplog.records[0].levelno == logging.ERROR and caplog.records[0].exc_info is not None


def test_a_message_with_no_arguments_is_left_as_written_like_any_python_logger(caplog):
    with caplog.at_level(logging.DEBUG, logger=NAME):
        recorder().info("100%% sure")
    assert caplog.records[0].getMessage() == "100%% sure"


def test_lines_below_the_logger_s_level_are_not_logged(caplog):
    with caplog.at_level(logging.WARNING, logger=NAME):
        recorder().info("quiet")
        recorder().debug("quieter")
    assert caplog.records == []


# --- strict mode -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["warning", "error", "critical", "exception"])
def test_strict_mode_raises_the_class_s_strict_exception_for_warning_and_above_and_logs_nothing(caplog, mode):
    with caplog.at_level(logging.DEBUG, logger=NAME):
        with pytest.raises(TrialError, match="got 'abc', using 5"):
            getattr(recorder(strict=True), mode)("got %r, using %d", "abc", 5)
    assert caplog.records == []


@pytest.mark.parametrize("mode", ["debug", "info"])
def test_strict_mode_never_raises_for_debug_or_info_and_still_logs(caplog, mode):
    with caplog.at_level(logging.DEBUG, logger=NAME):
        getattr(recorder(strict=True), mode)("fine")
    assert [r.getMessage() for r in caplog.records] == ["fine"]


def test_strict_mode_raises_even_when_the_logger_would_not_show_the_line():
    logger = logging.getLogger(NAME + ".quiet")
    logger.setLevel(logging.CRITICAL + 1)
    with pytest.raises(TrialError):
        LogRecorder(logger, TrialError, True).warning("a problem is a problem whatever the log shows")


def test_a_strict_message_that_does_not_fit_its_arguments_is_raised_as_written():
    with pytest.raises(TrialError, match="needs a number %d"):
        recorder(strict=True).warning("needs a number %d", "not a number")


def test_without_strict_mode_nothing_raises(caplog):
    with caplog.at_level(logging.DEBUG, logger=NAME):
        for mode, _ in MODES:
            getattr(recorder(), mode)("said")
    assert len(caplog.records) == 5


def test_the_strict_exception_defaults_to_value_error():
    with pytest.raises(ValueError):
        LogRecorder(logging.getLogger(NAME), strict=True).warning("x")
