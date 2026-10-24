"""Tests for pom.log_setup: one-time stdout logging setup that never fails and never duplicates."""
import io
import logging

import pytest

from pom.log_setup import BACKUPS, LOG_FILE, LOGGER_NAME, MAX_BYTES, configure_logging, get_logger


@pytest.fixture(autouse=True)
def clean_logger():
    """Leaves the pom logger as it was found, so tests do not leak handlers into each other."""
    logger = logging.getLogger(LOGGER_NAME)
    saved = (list(logger.handlers), logger.level, logger.propagate)
    yield
    for handler in list(logger.handlers):
        if handler not in saved[0]:
            handler.close()          # release any log file a test opened
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


# --- The local log file ---------------------------------------------------------------

def read_log(directory):
    return (directory / LOG_FILE).read_text(encoding="utf-8")


def test_local_logs_off_writes_no_file_and_makes_no_folder(tmp_path):
    folder = tmp_path / "logs"
    configure_logging("INFO", io.StringIO(), local_logs=False, log_dir=folder)
    logging.getLogger("pom.x").warning("nothing on disk")
    assert not folder.exists()


def test_local_logs_writes_the_log_to_a_file_as_well_as_the_stream(tmp_path):
    folder, stream = tmp_path / "logs", io.StringIO()
    configure_logging("INFO", stream, local_logs=True, log_dir=folder)
    logging.getLogger("pom.corpus.weights").warning("bad weight")
    assert "WARNING pom.corpus.weights: bad weight" in stream.getvalue()
    assert "WARNING pom.corpus.weights: bad weight" in read_log(folder)


def test_the_file_has_timestamps_but_the_stream_does_not(tmp_path):
    folder, stream = tmp_path / "logs", io.StringIO()
    configure_logging("INFO", stream, local_logs=True, log_dir=folder)
    logging.getLogger("pom.x").warning("stamped")
    assert stream.getvalue() == "WARNING pom.x: stamped\n"
    import re
    assert re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} WARNING pom\.x: stamped\n", read_log(folder))


def test_the_folder_is_created_if_missing_including_parents(tmp_path):
    folder = tmp_path / "deep" / "er" / "logs"
    configure_logging("INFO", io.StringIO(), local_logs=True, log_dir=folder)
    logging.getLogger("pom.x").warning("made it")
    assert "made it" in read_log(folder)


def test_the_file_obeys_the_level_and_the_debug_flag(tmp_path):
    folder = tmp_path / "logs"
    configure_logging("WARNING", io.StringIO(), local_logs=True, log_dir=folder)
    logging.getLogger("pom.x").info("too quiet")
    logging.getLogger("pom.x").warning("loud enough")
    text = read_log(folder)
    assert "too quiet" not in text and "loud enough" in text
    configure_logging("WARNING", io.StringIO(), debug=True, local_logs=True, log_dir=folder)
    logging.getLogger("pom.x").debug("now visible")
    assert "now visible" in read_log(folder)


def test_a_module_debug_override_reaches_the_file(tmp_path):
    folder = tmp_path / "logs"
    configure_logging("INFO", io.StringIO(), local_logs=True, log_dir=folder)
    get_logger("pom.demo.filed", True)
    try:
        logging.getLogger("pom.demo.filed").debug("module debug")
    finally:
        get_logger("pom.demo.filed", None)
    assert "module debug" in read_log(folder)


def test_calling_twice_does_not_duplicate_lines_in_the_file(tmp_path):
    folder = tmp_path / "logs"
    configure_logging("INFO", io.StringIO(), local_logs=True, log_dir=folder)
    configure_logging("INFO", io.StringIO(), local_logs=True, log_dir=folder)
    logging.getLogger("pom.x").warning("only once")
    assert read_log(folder).count("only once") == 1


def test_turning_local_logs_off_again_stops_writing_to_the_file(tmp_path):
    folder = tmp_path / "logs"
    configure_logging("INFO", io.StringIO(), local_logs=True, log_dir=folder)
    configure_logging("INFO", io.StringIO(), local_logs=False)
    logging.getLogger("pom.x").warning("after switching off")
    assert "after switching off" not in read_log(folder)


def test_the_file_keeps_earlier_runs_by_appending(tmp_path):
    folder = tmp_path / "logs"
    configure_logging("INFO", io.StringIO(), local_logs=True, log_dir=folder)
    logging.getLogger("pom.x").warning("first run")
    configure_logging("INFO", io.StringIO(), local_logs=True, log_dir=folder)
    logging.getLogger("pom.x").warning("second run")
    text = read_log(folder)
    assert "first run" in text and "second run" in text


def test_the_file_rotates_instead_of_growing_forever(tmp_path):
    folder = tmp_path / "logs"
    logger = configure_logging("INFO", io.StringIO(), local_logs=True, log_dir=folder)
    handler = next(h for h in logger.handlers if getattr(h, "baseFilename", None))
    assert (handler.maxBytes, handler.backupCount) == (MAX_BYTES, BACKUPS)


def test_an_unwritable_location_does_not_crash_and_warns_on_the_stream(tmp_path):
    blocker = tmp_path / "logs"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    stream = io.StringIO()
    configure_logging("INFO", stream, local_logs=True, log_dir=blocker)   # must not raise
    logging.getLogger("pom.x").warning("still logging")
    assert "cannot open the local log file" in stream.getvalue()
    assert "continuing without it" in stream.getvalue()
    assert "still logging" in stream.getvalue()


def test_the_default_folder_is_logs_and_is_gitignored():
    from pathlib import Path
    from pom.log_setup import LOG_DIR
    assert LOG_DIR == "logs"
    ignored = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "logs/" in ignored


def test_reconfiguring_closes_the_old_log_file(tmp_path):
    """A handler that is only removed, not closed, leaks a file descriptor on every reload."""
    folder = tmp_path / "logs"
    logger = configure_logging("INFO", io.StringIO(), local_logs=True, log_dir=folder)
    old = next(h for h in logger.handlers if getattr(h, "baseFilename", None))
    assert old.stream is not None
    configure_logging("INFO", io.StringIO(), local_logs=False)
    assert old.stream is None   # closed
