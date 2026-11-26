"""Shared test helpers.

`logged`: what the code under test logged, read from pytest's `caplog` (Python's standard way for a test to read the log). Every class says what
it has to say through its logger (from the Bookkeeper) and keeps nothing, so a test that wants to know what a class said reads the log. It covers
lines logged while a fixture was building the object, as well as during the test itself.
"""
import logging

import pytest

_MODE = {logging.WARNING: "warning", logging.ERROR: "error", logging.CRITICAL: "critical"}


class LoggedLines:
    """The log lines a test produced, by mode, optionally only those from one logger name (and the loggers under it)."""

    def __init__(self, caplog, name=None):
        self._caplog = caplog
        self._name = name

    def of(self, thing):
        """The lines one object said: those from its logger's name. `thing` is the object (anything with a `logger`) or a logger name."""
        name = thing if isinstance(thing, str) else thing.logger.logger.name
        return LoggedLines(self._caplog, name)

    def _records(self, name):
        name = name if name is not None else self._name
        records = self._caplog.get_records("setup") + self._caplog.get_records("call")
        return [r for r in records if name is None or r.name == name or r.name.startswith(name + ".")]

    def _messages(self, level, name):
        return [r.getMessage() for r in self._records(name) if r.levelno == level]

    def infos(self, name=None):
        """The `info` lines."""
        return self._messages(logging.INFO, name)

    def warnings(self, name=None):
        """The `warning` lines."""
        return self._messages(logging.WARNING, name)

    def errors(self, name=None):
        """The `error` lines."""
        return self._messages(logging.ERROR, name)

    def criticals(self, name=None):
        """The `critical` lines."""
        return self._messages(logging.CRITICAL, name)

    def problems(self, name=None):
        """Every line at `warning` or above."""
        return [r.getMessage() for r in self._records(name) if r.levelno >= logging.WARNING]

    def ok(self, name=None):
        """True when nothing at `warning` or above was logged."""
        return not self.problems(name)

    def lines(self, name=None):
        """The problems as `mode: text` lines."""
        return [f"{_MODE.get(r.levelno, 'critical')}: {r.getMessage()}" for r in self._records(name) if r.levelno >= logging.WARNING]


@pytest.fixture
def logged(caplog):
    """What the code under test logged (see LoggedLines)."""
    caplog.set_level(logging.INFO)
    return LoggedLines(caplog)
