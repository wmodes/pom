"""The bookkeeper module: everything about the log. The Bookkeeper (one instance, built by the entry point) sets up the log and hands each class its
logger, a LogRecorder (Python's logger plus strict mode); the LogMuter mutes a message that keeps repeating; log_setup is the setup itself.

Until every class has moved to `logger_for`, the old per-class recorder is still here as ReportRecorder (with its LogBook, Fault and Severity);
it goes when the last class has moved."""
from pom.bookkeeper.bookkeeper import Bookkeeper
from pom.bookkeeper.fault import Fault, Severity
from pom.bookkeeper.log_book import LogBook
from pom.bookkeeper.log_muter import LogMuter
from pom.bookkeeper.log_recorder import LogRecorder
from pom.bookkeeper.log_setup import configure_logging, get_logger
from pom.bookkeeper.report_recorder import ReportRecorder, strict_report_recorder

__all__ = ["Bookkeeper", "Fault", "LogBook", "LogMuter", "LogRecorder", "ReportRecorder", "Severity", "configure_logging", "get_logger",
           "strict_report_recorder"]
