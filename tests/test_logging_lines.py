"""Every log line in the project goes through the one path: a class's logger, handed out by the Bookkeeper (`self.logger = bookkeeper.logger_for(...)`),
so every line is subject to the log's muting, and no class keeps what it said.

A class that logged some other way (a module-level `_log.info(...)`, a raw `logging.getLogger(...)`, or the old per-class `report`) would be a second
path, outside it. This test fails if one appears. The exceptions are listed here, each with its reason, and only these:
  * the bookkeeper itself (log_setup, log_muter, the LogRecorder and the Bookkeeper) is the path;
  * the monitoring site's request log, which is written from inside werkzeug's request handler class, an object of the library's, not ours.
"""
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
