"""Tests for pom.bookkeeper.bookkeeper: the one instance that sets up the log and hands each class its logger."""
import io
import logging
import re
import threading

import pytest

from pom.bookkeeper.bookkeeper import Bookkeeper
from pom.bookkeeper.log_muter import LogMuter
from pom.bookkeeper.log_recorder import LogRecorder
from pom.bookkeeper.log_setup import LOGGER_NAME


class TrialError(Exception):
    """A class's own strict exception."""


@pytest.fixture(autouse=True)
def clean_logger():
    """Leaves the pom logger as it was found, so set_up in one test does not leak into the next."""
    logger = logging.getLogger(LOGGER_NAME)
    saved = (list(logger.handlers), logger.level, logger.propagate)
    yield
    for handler in list(logger.handlers):
        if handler not in saved[0]:
            handler.close()
    logger.handlers[:] = saved[0]
    logger.setLevel(saved[1])
    logger.propagate = saved[2]


def test_logger_for_hands_back_a_log_recorder_under_the_name_it_was_asked_for(caplog):
    logger = Bookkeeper().logger_for("pom.conductor.turn_taker", TrialError)
    assert isinstance(logger, LogRecorder)
    with caplog.at_level(logging.INFO, logger="pom.conductor.turn_taker"):
        logger.info("a turn began")
    assert [(r.name, r.getMessage()) for r in caplog.records] == [("pom.conductor.turn_taker", "a turn began")]


def test_logger_for_passes_the_strict_exception_and_strict_mode_on():
    with pytest.raises(TrialError):
        Bookkeeper().logger_for("pom.test.a", TrialError, strict=True).warning("x")
    Bookkeeper().logger_for("pom.test.b", TrialError).warning("x")                    # not strict: no raise


class TestStrictIsSetOnceForTheProjectLikeDebug:
    """The entry point builds the Bookkeeper with the `strict` setting; a class's own `strict=` (None by default) follows it or overrides it."""

    def test_a_strict_bookkeeper_hands_every_class_a_strict_logger(self):
        with pytest.raises(TrialError):
            Bookkeeper(strict=True).logger_for("pom.test.a", TrialError).warning("x")

    def test_a_class_that_asks_for_none_follows_the_project(self):
        with pytest.raises(TrialError):
            Bookkeeper(strict=True).logger_for("pom.test.a", TrialError, strict=None).warning("x")
        Bookkeeper(strict=False).logger_for("pom.test.b", TrialError, strict=None).warning("x")      # not strict: no raise

    def test_a_class_that_asks_for_false_is_not_strict_in_a_strict_project(self):
        Bookkeeper(strict=True).logger_for("pom.test.a", TrialError, strict=False).warning("x")      # no raise

    def test_a_class_that_asks_for_true_is_strict_in_a_project_that_is_not(self):
        with pytest.raises(TrialError):
            Bookkeeper(strict=False).logger_for("pom.test.a", TrialError, strict=True).warning("x")

    def test_a_bookkeeper_is_not_strict_unless_told(self):
        Bookkeeper().logger_for("pom.test.a", TrialError).warning("x")                               # no raise

    def test_a_part_that_takes_no_strict_of_its_own_follows_the_project(self):
        """The ScorerWarmer, like the HingeFinder and the BootSlipper, has no `strict=`: before, nothing could make it strict."""
        from pom.defaults import ScorerSettings
        from pom.scoring import ScorerWarmer
        warmer = ScorerWarmer(None, lambda: True, ScorerSettings(), bookkeeper=Bookkeeper(strict=True))
        with pytest.raises(ValueError):
            warmer.logger.warning("x")

    def test_no_class_turns_strict_off_by_default(self):
        """A `strict` default of False would make that class deaf to POM_STRICT; every one is None, "follow the project"."""
        from pathlib import Path
        import pom
        source = Path(pom.__file__).parent
        deaf = [str(path.relative_to(source)) for path in source.rglob("*.py") if path.name not in ("log_recorder.py", "bookkeeper.py")      # the project's own default, and the resolved value
                and re.search(r"strict(: [\w\[\]]+)? ?= ?False", path.read_text(encoding="utf-8"))]
        assert deaf == []

    def test_a_part_built_with_its_own_strict_none_follows_the_project(self):
        from pom.defaults import MonitorSettings
        from pom.monitor import TurnHistory, TurnHistoryError
        history = TurnHistory(MonitorSettings(), bookkeeper=Bookkeeper(strict=True))
        with pytest.raises(TurnHistoryError):
            history.logger.warning("x")


def test_a_module_s_debug_line_set_to_true_shows_its_debug_lines_whatever_the_project_level():
    stream = io.StringIO()
    keeper = Bookkeeper()
    keeper.set_up("INFO", stream)
    keeper.logger_for("pom.test.loud", TrialError, debug=True).debug("shown")
    keeper.logger_for("pom.test.default", TrialError).debug("hidden")
    assert "shown" in stream.getvalue() and "hidden" not in stream.getvalue()


def test_a_module_s_debug_line_set_to_false_hides_its_debug_lines_even_when_the_project_shows_debug():
    stream = io.StringIO()
    keeper = Bookkeeper()
    keeper.set_up("DEBUG", stream, debug=True)
    keeper.logger_for("pom.test.hushed", TrialError, debug=False).debug("hidden")
    keeper.logger_for("pom.test.normal", TrialError).debug("shown")
    assert "shown" in stream.getvalue() and "hidden" not in stream.getvalue()


def test_set_up_writes_the_lines_of_every_class_to_the_stream_in_the_project_format():
    stream = io.StringIO()
    keeper = Bookkeeper()
    keeper.set_up("INFO", stream)
    keeper.logger_for("pom.listening.mic_deck", TrialError).critical("the microphone stopped sending sound")
    keeper.logger_for("pom.conductor.conductor", TrialError).info("the floor is free")
    assert stream.getvalue() == ("CRITICAL pom.listening.mic_deck: the microphone stopped sending sound\n"
                                 "INFO pom.conductor.conductor: the floor is free\n")


def test_set_up_puts_the_muter_on_the_log_so_every_class_s_repeats_are_muted_in_one_place():
    stream = io.StringIO()
    keeper = Bookkeeper(LogMuter(2, 100, 300.0))
    keeper.set_up("INFO", stream)
    first, second = keeper.logger_for("pom.a", TrialError), keeper.logger_for("pom.b", TrialError)
    for _ in range(5):
        first.warning("not found after %ds", 3)
        second.warning("not found after %ds", 4)
    text = stream.getvalue()
    assert text.count("WARNING pom.a: not found after") == 2 and text.count("WARNING pom.b: not found after") == 2
    assert text.count("LOGMUTE: Last message repeating and muted") == 2


def test_without_a_muter_nothing_is_muted():
    stream = io.StringIO()
    keeper = Bookkeeper()
    keeper.set_up("INFO", stream)
    for _ in range(5):
        keeper.logger_for("pom.a", TrialError).warning("same")
    assert stream.getvalue().count("same") == 5


def test_one_bookkeeper_serves_many_threads_and_the_muter_lets_through_exactly_the_limit():
    stream = io.StringIO()
    keeper = Bookkeeper(LogMuter(10, 100000, 300.0))
    keeper.set_up("INFO", stream)
    loggers = [keeper.logger_for("pom.threads", TrialError) for _ in range(8)]

    def worker(logger):
        for _ in range(100):
            logger.warning("the same line from every thread")

    threads = [threading.Thread(target=worker, args=(logger,)) for logger in loggers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert stream.getvalue().count("WARNING pom.threads: the same line from every thread") == 10


def test_the_project_debug_setting_shows_every_module_s_debug_lines_even_with_the_log_level_at_info():
    stream = io.StringIO()
    keeper = Bookkeeper()
    keeper.set_up("INFO", stream, debug=True)
    keeper.logger_for("pom.test.any", TrialError).debug("shown by the project setting")
    assert "shown by the project setting" in stream.getvalue()


def test_a_module_whose_debug_line_is_none_leaves_its_logger_s_level_as_it_is():
    logger = logging.getLogger("pom.test.left_alone")
    logger.setLevel(logging.ERROR)
    try:
        Bookkeeper().logger_for("pom.test.left_alone", TrialError)                       # a class built later must not undo a level set elsewhere
        assert logger.level == logging.ERROR
    finally:
        logger.setLevel(logging.NOTSET)
