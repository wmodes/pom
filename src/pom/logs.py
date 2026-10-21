"""Logging setup for the whole project, done once by the entry point.

The Printer will run as a systemd service, so everything it logs goes to stdout and journald
collects it (`journalctl -u pom`). Modules never configure handlers; they ask `get_logger` for a
logger, and the entry point calls `configure_logging` once.

THE DEBUG FLAG. There is one flag, `debug`, with two places to set it:
  * Project-wide: `debug = true` in config/pom.toml (or POM_DEBUG=1). Every module then logs its
    DEBUG lines.
  * Per module: each module has a line `DEBUG = None` near the top. None means "follow the
    project flag"; set it to True to see this module's DEBUG output even when the project flag is
    off, or to False to keep it quiet even when the project flag is on. Edit that one line while
    chasing a tricky problem in one module, and put it back to None afterwards.

Design decisions, and why:
  * stdout, not a file: journald does the storage, rotation and timestamping.
  * No timestamp in the line: journald adds one, and a second would be noise.
  * Start-up order for the entry point: call `configure_logging()` once with defaults, load the
    Config (so any config problem is logged in the same format), then call `configure_logging`
    again with the Config's values. Calling it twice is safe.
  * Only the `pom` logger is configured. The root logger and other libraries are left alone.
  * Idempotent: calling it twice (a test, a reload) must not print every line twice.
  * Per-module overrides survive reconfiguration: `configure_logging` sets only the `pom`
    logger's level, and a module's own logger keeps the level `get_logger` gave it.
  * It cannot fail: an unknown level name falls back to INFO instead of raising, in keeping with
    the rule that nothing may stop the oracle over a typo.
"""
import logging
import sys
from typing import Optional, TextIO

LOGGER_NAME = "pom"
FORMAT = "%(levelname)s %(name)s: %(message)s"
_MARK = "_pom_handler"


def configure_logging(level: str = "INFO", stream: Optional[TextIO] = None,
                      debug: bool = False) -> logging.Logger:
    """Sends the `pom` logger's output to a stream (stdout by default).

    Args:
        level: A level name such as "INFO" or "debug". Anything unrecognized means INFO.
        stream: Where to write; defaults to the current sys.stdout.
        debug: The project-wide debug flag. True shows DEBUG output from every module (except
            any module that has overridden the flag to False).

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
    logger.setLevel(logging.DEBUG if debug
                    else resolved if isinstance(resolved, int) else logging.INFO)
    logger.propagate = False   # the pom handler is the only output; no duplicates via root
    return logger


def get_logger(name: str, debug: Optional[bool] = None) -> logging.Logger:
    """A module's logger, with the module's own override of the debug flag applied.

    Use it at the top of a module: `DEBUG = None` then `_log = get_logger(__name__, DEBUG)`.

    Args:
        name: The module's `__name__`.
        debug: None follows the project flag; True shows this module's DEBUG output whatever the
            project flag says; False hides it (INFO and up still show).
    """
    logger = logging.getLogger(name)
    if debug is True:
        logger.setLevel(logging.DEBUG)
    elif debug is False:
        logger.setLevel(logging.INFO)
    else:
        logger.setLevel(logging.NOTSET)   # inherit from the `pom` logger
    return logger
