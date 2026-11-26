"""The bookkeeper module: everything about the log, in one place.

The Bookkeeper (one instance, built by the entry point) sets up the log and hands each class its logger, a LogRecorder (Python's logger, plus
strict mode); the LogMuter mutes a message that keeps repeating; log_setup is the setup itself. No class keeps what it said: it is in the log.
"""
from pom.bookkeeper.bookkeeper import Bookkeeper
from pom.bookkeeper.log_muter import LogMuter
from pom.bookkeeper.log_recorder import LogRecorder
from pom.bookkeeper.log_setup import configure_logging, get_logger

__all__ = ["Bookkeeper", "LogMuter", "LogRecorder", "configure_logging", "get_logger"]
