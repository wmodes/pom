"""Tests for pom.logs: one-time stdout logging setup that never fails and never duplicates."""
import io
import logging

import pytest

from pom.logs import LOGGER_NAME, configure_logging


@pytest.fixture(autouse=True)
def clean_logger():
    """Leaves the pom logger as it was found, so tests do not leak handlers into each other."""
    logger = logging.getLogger(LOGGER_NAME)
    saved = (list(logger.handlers), logger.level, logger.propagate)
    yield
    logger.handlers[:] = saved[0]
    logger.setLevel(saved[1])
    logger.propagate = saved[2]


def test_writes_pom_log_lines_to_the_stream_without_a_timestamp():
    stream = io.StringIO()
    configure_logging("INFO", stream)
    logging.getLogger("pom.corpus.weights").warning("bad weight")
    assert stream.getvalue() == "WARNING pom.corpus.weights: bad weight\n"


def test_level_filters_lower_messages():
    stream = io.StringIO()
    configure_logging("WARNING", stream)
    logging.getLogger("pom.x").info("chatter")
    logging.getLogger("pom.x").warning("real")
    assert "chatter" not in stream.getvalue() and "real" in stream.getvalue()


def test_level_names_are_case_insensitive():
    stream = io.StringIO()
    configure_logging("debug", stream)
    logging.getLogger("pom.x").debug("detail")
    assert "detail" in stream.getvalue()


@pytest.mark.parametrize("bad", ["LOUD", "", None, 42, "warn!"])
def test_an_unknown_level_falls_back_to_info_and_never_raises(bad):
    stream = io.StringIO()
    logger = configure_logging(bad, stream)
    assert logger.level == logging.INFO


def test_calling_twice_does_not_duplicate_lines():
    stream = io.StringIO()
    configure_logging("INFO", stream)
    configure_logging("INFO", stream)
    logging.getLogger("pom.x").warning("once")
    assert stream.getvalue().count("once") == 1


def test_reconfiguring_moves_output_to_the_new_stream():
    first, second = io.StringIO(), io.StringIO()
    configure_logging("INFO", first)
    configure_logging("INFO", second)
    logging.getLogger("pom.x").warning("moved")
    assert first.getvalue() == "" and "moved" in second.getvalue()


def test_other_loggers_are_left_alone():
    root = logging.getLogger()
    before = (root.level, list(root.handlers))
    configure_logging("DEBUG", io.StringIO())
    assert (root.level, list(root.handlers)) == before
    assert logging.getLogger("somebody.else").handlers == []


def test_pom_lines_do_not_also_go_through_the_root_logger(caplog):
    configure_logging("INFO", io.StringIO())
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("pom.x").warning("only once")
    assert caplog.records == []   # propagate is off, so the root logger never sees it


def test_defaults_to_stdout(capsys):
    configure_logging("INFO")
    logging.getLogger("pom.x").warning("to stdout")
    assert "to stdout" in capsys.readouterr().out
