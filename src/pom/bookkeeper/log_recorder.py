"""The LogRecorder: a class's logger. It is Python's own logger, through `logging.LoggerAdapter` (Python's wrapper for "a logger plus a little"),
with one addition: strict mode.

A class gets one from the Bookkeeper (`self.logger = bookkeeper.logger_for(__name__, ItsError)`) and uses it exactly like a Python logger:
`self.logger.debug/info/warning/error/critical/exception(message, *args)`, with Python's own lazy %-formatting. It keeps nothing: what was said
is in the log, and a message that keeps repeating is muted there, in one place (pom.bookkeeper.log_muter).

STRICT MODE exists for development and tests: with strict on, `warning`, `error`, `critical` and `exception` raise the class's own strict
exception instead of logging, so a problem is loud while you work. `debug` and `info` are never raised. The running Printer is never strict.
"""
import logging


def _formatted(message, args: tuple) -> str:
    """The message with its %-arguments filled in, for a strict exception. One that does not fit its arguments is kept as written."""
    if not args:
        return str(message)
    try:
        return str(message) % args
    except Exception:
        return str(message)


class LogRecorder(logging.LoggerAdapter):
    """A class's logger: Python's logger, plus strict mode.

    Args:
        logger: The Python logger it writes through (named for the class's module).
        strict_exception: The exception strict mode raises: the class's own error type.
        strict: Raise the strict exception for warning and above, instead of logging (development and tests only).
    """

    def __init__(self, logger: logging.Logger, strict_exception: type = ValueError, strict: bool = False):
        super().__init__(logger, {})
        self._strict_exception = strict_exception
        self._strict = strict

    def log(self, level, msg, *args, **kwargs):
        """Every mode comes through here (that is how LoggerAdapter is built). Strict mode raises for warning and above; otherwise Python logs."""
        if self._strict and level >= logging.WARNING:
            raise self._strict_exception(_formatted(msg, args))
        super().log(level, msg, *args, **kwargs)
