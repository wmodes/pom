"""Every log line in the project goes through the one path: a class's logger, handed out by the Bookkeeper (`self.logger = bookkeeper.logger_for(...)`),
so every line is subject to the log's muting, and no class keeps what it said.

A class that logged some other way (a module-level `_log.info(...)`, a raw `logging.getLogger(...)`, or the old per-class `report`) would be a second
path, outside it. This test fails if one appears. The exceptions are listed here, each with its reason, and only these:
  * the bookkeeper itself (log_setup, log_muter, the LogRecorder and the Bookkeeper) is the path;
  * the monitoring site's request log, which is written from inside werkzeug's request handler class, an object of the library's, not ours.
"""
import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "pom"
OTHER_PATHS = re.compile(r"\b_log\.(debug|info|warning|warn|error|critical|exception|log)\("
                         r"|\blogging\.getLogger\([^)]*\)\.(debug|info|warning|error|critical|exception|log)\("
                         r"|\bself\.report\b|\bReportRecorder\b")
ALLOWED = {("monitor/monitor_site.py", "_log.debug('%s \"%s\" %s', self.address_string(), self.requestline, code)")}


def other_paths():
    found = []
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        if relative.startswith("bookkeeper/"):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            if OTHER_PATHS.search(line) and (relative, line.strip()) not in ALLOWED:
                found.append(f"{relative}:{number}: {line.strip()}")
    return found


def test_no_class_logs_outside_the_logger_the_bookkeeper_handed_it():
    assert other_paths() == [], "log through `self.logger` (from bookkeeper.logger_for), not the logger directly or a report"


def test_the_one_allowed_exception_is_still_there_so_the_list_does_not_go_stale():
    text = (SRC / "monitor" / "monitor_site.py").read_text(encoding="utf-8")
    assert all(line in text for _, line in ALLOWED)


LEVELS = {"debug", "info", "warning", "error", "critical", "exception"}


def f_string_messages():
    """Logger calls whose message is an f-string (read with ast, so a docstring's example never counts)."""
    found = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LEVELS
                    and node.args and isinstance(node.args[0], ast.JoinedStr)):
                found.append(f"{path.relative_to(SRC).as_posix()}:{node.lineno}")
    return found


def test_every_log_line_is_written_in_python_s_lazy_percent_style():
    """One style for every line: `logger.warning("got %r", value)`, never `logger.warning(f"got {value!r}")` (Python's own pattern: the text is
    built only if the line is shown)."""
    assert f_string_messages() == [], "write the message with %s/%r/%g and pass the values after it"


TESTS = Path(__file__).resolve().parent


def test_every_test_reads_the_log_through_logged_except_the_bookkeeper_s_own():
    """One way for a test to read the log: the `logged` fixture (tests/conftest.py). The bookkeeper's tests test the logging itself, so they read
    pytest's `caplog` directly; no other test does."""
    readers = [path.relative_to(TESTS).as_posix() for path in sorted(TESTS.rglob("test_*.py"))
               if "bookkeeper" not in path.parts and path.name != "test_logging_lines.py" and "caplog" in path.read_text(encoding="utf-8")]
    assert readers == [], "read the log with the `logged` fixture"
