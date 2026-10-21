"""Logging setup for the whole project, done once by the entry point.

The Printer will run as a systemd service, so everything it logs goes to stdout and journald
collects it (`journalctl -u pom`). Modules only ever call `logging.getLogger(__name__)`; they never
configure handlers. The entry point calls `configure_logging` once.

Design decisions, and why:
  * stdout, not a file: journald does the storage, rotation and timestamping.
  * No timestamp in the line: journald adds one, and a second would be noise.
  * Start-up order for the entry point: call `configure_logging()` once with defaults, load the
    Config (so any config problem is logged in the same format), then call `configure_logging`
    again with `config.log_level`. Calling it twice is safe.
  * Only the `pom` logger is configured. The root logger and other libraries are left alone.
  * Idempotent: calling it twice (a test, a reload) must not print every line twice.
  * It cannot fail: an unknown level name falls back to INFO instead of raising, in keeping with
    the rule that nothing may stop the oracle over a typo.
"""
import logging
import sys
from typing import Optional, TextIO

LOGGER_NAME = "pom"
FORMAT = "%(levelname)s %(name)s: %(message)s"
_MARK = "_pom_handler"


def configure_logging(level: str = "INFO", stream: Optional[TextIO] = None) -> logging.Logger:
    """Sends the `pom` logger's output to a stream (stdout by default) at the given level.

    Args:
        level: A level name such as "INFO" or "debug". Anything unrecognized means INFO.
        stream: Where to write; defaults to the current sys.stdout.

    Returns:
        The configured `pom` logger.
    """
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        if getattr(handler, _MARK, False):
            logger.removeHandler(handler)
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(logging.Formatter(FORMAT))
    setattr(handler, _MARK, True)
    logger.addHandler(handler)
    resolved = logging.getLevelName(str(level).upper())
    logger.setLevel(resolved if isinstance(resolved, int) else logging.INFO)
    logger.propagate = False   # the pom handler is the only output; no duplicates via root
    return logger
