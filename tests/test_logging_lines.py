"""Every log line in the project goes through the one path: a module's `report` (a ReportRecorder), so every line is subject to the log's muting.

A module that calls its logger directly (`_log.info(...)`) or reaches for a raw logger would be a second way to log, outside that path. This test
fails if one appears. The exceptions are listed here, each with its reason, and only these:
  * the bookkeeper itself (log_setup, log_muter, log_recorder) cannot report through a recorder that depends on it;
  * the monitoring site's request log, which is written from inside werkzeug's request handler class, an object of the library's, not ours.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "pom"
DIRECT = re.compile(r"\b_log\.(debug|info|warning|warn|error|critical|exception|log)\(|\blogging\.getLogger\([^)]*\)\.(debug|info|warning|error|critical|exception|log)\(")
ALLOWED = {("monitor/monitor_site.py", "_log.debug('%s \"%s\" %s', self.address_string(), self.requestline, code)")}


def direct_calls():
    found = []
    for path in sorted(SRC.rglob("*.py")):
        relative = path.relative_to(SRC).as_posix()
        if relative.startswith("bookkeeper/"):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            if DIRECT.search(line) and (relative, line.strip()) not in ALLOWED:
                found.append(f"{relative}:{number}: {line.strip()}")
    return found


def test_no_module_logs_outside_its_report():
    assert direct_calls() == [], "log through `self.report` (debug, info, warn, error, critical), not the logger directly"


def test_the_one_allowed_exception_is_still_there_so_the_list_does_not_go_stale():
    text = (SRC / "monitor" / "monitor_site.py").read_text(encoding="utf-8")
    assert all(line in text for _, line in ALLOWED)
