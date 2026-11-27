"""The Bookkeeper: the one instance, built by the entry point, that sets up the log and hands each class its logger.

It follows Python's own logging pattern (one setup at the start, then a named logger for each module, like `logging.getLogger(__name__)`) with
two additions: strict mode, in each class's LogRecorder, and the muting of a message that keeps repeating, by the LogMuter it attaches to the log.

The entry point builds it once from the config, calls `set_up`, and hands the same instance to every class it builds; a class asks it once for its
logger: `self.logger = bookkeeper.logger_for(__name__, ItsError)`. It holds no state that changes: the log's handlers are Python's, and the only
shared, changing state is the LogMuter's, which owns its own lock. So one Bookkeeper can be used from any number of threads.
"""
import logging
from pathlib import Path
from typing import Optional, TextIO, Union

from pom.bookkeeper.log_recorder import LogRecorder
from pom.bookkeeper.log_setup import LOG_DIR, configure_logging, get_logger


class Bookkeeper:
    """Sets up the log once, and hands each class its logger.

    Args:
        muter: The LogMuter to put on the log (built by the entry point from the `logmute_` settings), or None to mute nothing.
        strict: The project-wide `strict` setting (development and tests only): every logger it hands out raises on a warning, unless
            the class asked for its own. Like `debug`, it is set once, here, by the entry point.
    """

    def __init__(self, muter: Optional[logging.Filter] = None, strict: bool = False):
        self._muter = muter
        self._strict = strict

    def set_up(self, level: str = "INFO", stream: Optional[TextIO] = None, debug: bool = False, local_logs: bool = False,
               log_dir: Union[str, Path] = LOG_DIR) -> logging.Logger:
        """Sends the log to the stream (stdout by default) and, if asked, a local file, with the muter on each. Done once, by the entry point.

        Args:
            level: The `log_level` setting: which modes the log shows.
            stream: Where to write; the current stdout if not given.
            debug: The project-wide `debug` setting: show every module's debug lines.
            local_logs: The `local_logs` setting: also write the log to a file under `log_dir`.
            log_dir: Where the local log file goes.

        Returns:
            The project's top logger.
        """
        return configure_logging(level, stream, debug=debug, local_logs=local_logs, log_dir=log_dir, muter=self._muter)

    def logger_for(self, name: str, strict_exception: type = ValueError, debug: Optional[bool] = None, strict: Optional[bool] = None) -> LogRecorder:
        """A class's logger.

        Args:
            name: The module's `__name__`; the log shows it on every line.
            strict_exception: The class's own error type, raised in strict mode.
            debug: The module's `DEBUG` line: None follows the project setting (the logger's level is left as it is, so a class built later
                never undoes a level set elsewhere), True shows its debug lines, False hides them.
            strict: Strict mode for this class (development and tests only): None (the default) follows the project's `strict` setting,
                the one this Bookkeeper was built with; True or False overrides it for this class, as a module's `DEBUG` line does for debug.
        """
        logger = logging.getLogger(name) if debug is None else get_logger(name, debug)
        return LogRecorder(logger, strict_exception, self._strict if strict is None else strict)
