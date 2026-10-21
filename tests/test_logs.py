"""Tests for pom.logs: one-time stdout logging setup that never fails and never duplicates."""
import io
import logging

import pytest

from pom.logs import LOGGER_NAME, configure_logging, get_logger


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


# --- The debug flag: project-wide, with a per-module override ---------------------------

@pytest.fixture
def module_logger():
    """A throwaway module logger under pom, reset afterwards."""
    name = "pom.demo.module"
    yield name
    logging.getLogger(name).setLevel(logging.NOTSET)


def emit(name):
    logging.getLogger(name).debug("debug line")
    logging.getLogger(name).info("info line")


def test_debug_is_hidden_by_default(module_logger):
    stream = io.StringIO()
    configure_logging("INFO", stream)
    get_logger(module_logger)
    emit(module_logger)
    assert "debug line" not in stream.getvalue() and "info line" in stream.getvalue()


def test_the_project_debug_flag_shows_debug_everywhere(module_logger):
    stream = io.StringIO()
    configure_logging("INFO", stream, debug=True)
    get_logger(module_logger)          # follows the project flag
    emit(module_logger)
    assert "debug line" in stream.getvalue()


def test_a_module_can_turn_debug_on_when_the_project_flag_is_off(module_logger):
    stream = io.StringIO()
    configure_logging("INFO", stream, debug=False)
    get_logger(module_logger, True)
    emit(module_logger)
    assert "debug line" in stream.getvalue()


def test_only_the_overriding_module_gets_debug(module_logger):
    stream = io.StringIO()
    configure_logging("INFO", stream, debug=False)
    get_logger(module_logger, True)
    logging.getLogger("pom.other.module").debug("other debug")
    emit(module_logger)
    assert "other debug" not in stream.getvalue() and "debug line" in stream.getvalue()


def test_a_module_can_stay_quiet_when_the_project_flag_is_on(module_logger):
    stream = io.StringIO()
    configure_logging("INFO", stream, debug=True)
    get_logger(module_logger, False)
    emit(module_logger)
    assert "debug line" not in stream.getvalue() and "info line" in stream.getvalue()


def test_reconfiguring_keeps_a_modules_override(module_logger):
    get_logger(module_logger, True)             # the module is imported first...
    stream = io.StringIO()
    configure_logging("WARNING", stream, debug=False)   # ...then the entry point configures
    emit(module_logger)
    assert "debug line" in stream.getvalue()


def test_none_means_follow_the_project_again(module_logger):
    get_logger(module_logger, True)
    get_logger(module_logger, None)
    stream = io.StringIO()
    configure_logging("INFO", stream, debug=False)
    emit(module_logger)
    assert "debug line" not in stream.getvalue()


def test_get_logger_returns_the_named_logger():
    assert get_logger("pom.some.name").name == "pom.some.name"
