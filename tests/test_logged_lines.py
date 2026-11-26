"""Tests for tests/conftest.py's `logged` fixture (LoggedLines): the one way a test reads the log."""
import logging

import pytest

NAME = "pom.test.logged_lines"


@pytest.fixture
def built_with_a_warning():
    """A fixture that logs while it builds, as a fixture that builds a class with a bad setting does."""
    logging.getLogger(NAME).warning("a bad setting, said while the fixture built the object")
    return NAME


def test_lines_logged_while_a_fixture_built_the_object_are_read_too(logged, built_with_a_warning):
    assert logged.of(built_with_a_warning).warnings() == ["a bad setting, said while the fixture built the object"]
    assert not logged.of(built_with_a_warning).ok()


def test_every_level_is_caught_from_the_start_debug_included(logged):
    logger = logging.getLogger(NAME)
    logger.debug("d %d", 1)
    logger.info("i")
    logger.warning("w")
    logger.error("e")
    logger.critical("c")
    assert (logged.debugs(), logged.infos(), logged.warnings(), logged.errors(), logged.criticals()) == (["d 1"], ["i"], ["w"], ["e"], ["c"])
    assert logged.messages() == ["d 1", "i", "w", "e", "c"] and logged.text() == "d 1\ni\nw\ne\nc"
    assert logged.problems() == ["w", "e", "c"] and logged.lines() == ["warning: w", "error: e", "critical: c"]


def test_of_reads_one_logger_and_the_loggers_under_it_only(logged):
    logging.getLogger(NAME).info("mine")
    logging.getLogger(NAME + ".part").info("my part's")
    logging.getLogger(NAME + "_other").info("not mine")
    assert logged.of(NAME).messages() == ["mine", "my part's"]
