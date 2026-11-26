"""The bookkeeper module: keeping the Printer's books. What a module says (the LogRecorder: five modes, each logged at its level), the faults it keeps for reading (the LogBook), where everything it logs goes (log_setup), and the muting of a message that keeps repeating (log_muter)."""
from pom.bookkeeper.fault import Fault, Severity
from pom.bookkeeper.log_book import LogBook
from pom.bookkeeper.log_recorder import LogRecorder, strict_log_recorder
from pom.bookkeeper.log_muter import LogMuter
from pom.bookkeeper.log_setup import configure_logging, get_logger

__all__ = ["Fault", "LogBook", "LogRecorder", "Severity", "strict_log_recorder", "LogMuter", "configure_logging", "get_logger"]
