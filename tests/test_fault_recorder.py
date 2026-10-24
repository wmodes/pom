"""Tests for pom.fault_recorder: recording problems as data, with a strict mode for development."""
import logging

import pytest

from pom.fault_recorder import Fault, FaultRecorder, Severity, strict_fault_recorder


class FakeError(Exception):
    """A module's own error type, raised in strict mode."""


def test_new_report_is_ok_and_empty():
    report = FaultRecorder()
    assert report.ok and len(report) == 0 and not report.has_critical
    assert report.faults == () and report.lines() == []


def test_warn_records_a_warning_without_raising():
    report = FaultRecorder()
    report.warn("bad weight, using 1.0")
    assert not report.ok and not report.has_critical
    assert report.warnings == ["bad weight, using 1.0"] and report.criticals == []


def test_critical_is_recorded_without_raising():
    report = FaultRecorder()
    report.critical("no chunks to draw from")
    assert report.has_critical and not report.ok
    assert report.criticals == ["no chunks to draw from"] and report.warnings == []


def test_issues_keep_order_and_severity():
    report = FaultRecorder()
    report.warn("a")
    report.critical("b")
    report.warn("c")
    assert [(i.severity, i.message) for i in report] == [
        (Severity.WARNING, "a"), (Severity.CRITICAL, "b"), (Severity.WARNING, "c")]


def test_lines_are_printable_with_the_severity():
    report = FaultRecorder()
    report.warn("careful")
    report.critical("broken")
    assert report.lines() == ["warning: careful", "critical: broken"]
    assert str(Fault(Severity.WARNING, "x")) == "warning: x"


def test_strict_raises_the_modules_error_at_once():
    report = FaultRecorder(strict=True, error=FakeError)
    with pytest.raises(FakeError, match="typo in weights"):
        report.warn("typo in weights")
    with pytest.raises(FakeError, match="nothing to say"):
        report.critical("nothing to say")
    assert report.ok   # nothing was kept; it raised instead


def test_strict_defaults_to_value_error():
    with pytest.raises(ValueError):
        FaultRecorder(strict=True).warn("x")


def test_lenient_never_raises_whatever_the_volume():
    report = FaultRecorder(limit=3)
    for i in range(1000):
        report.warn(f"w{i}")
        report.critical(f"c{i}")


def test_limit_keeps_the_first_issues_and_counts_the_rest():
    report = FaultRecorder(limit=3)
    for i in range(10):
        report.warn(f"w{i}")
    assert report.warnings == ["w0", "w1", "w2"]
    assert report.suppressed == 7 and len(report) == 10
    assert report.lines()[-1] == "... and 7 more"
    assert not report.ok


def test_a_critical_is_kept_even_after_warnings_filled_the_limit():
    report = FaultRecorder(limit=2)
    for i in range(5):
        report.warn(f"w{i}")
    report.critical("nothing to say")
    assert report.has_critical and report.criticals == ["nothing to say"]
    assert report.warnings == ["w0", "w1"] and report.suppressed == 3


def test_suppressed_issues_still_make_the_report_not_ok():
    report = FaultRecorder(limit=0)
    report.warn("only counted")
    assert report.faults == () and report.suppressed == 1 and not report.ok


def test_extend_merges_another_report():
    parent, child = FaultRecorder(), FaultRecorder()
    child.warn("child warning")
    child.critical("child critical")
    parent.warn("parent warning")
    parent.extend(child)
    assert parent.warnings == ["parent warning", "child warning"]
    assert parent.criticals == ["child critical"]


def test_extend_carries_over_the_childs_suppressed_count():
    parent, child = FaultRecorder(), FaultRecorder(limit=1)
    child.warn("kept")
    child.warn("dropped")
    parent.extend(child)
    assert parent.warnings == ["kept"] and parent.suppressed == 1


def test_extend_in_strict_mode_raises_on_the_first_issue():
    parent, child = FaultRecorder(strict=True, error=FakeError), FaultRecorder()
    child.warn("from child")
    with pytest.raises(FakeError, match="from child"):
        parent.extend(child)


def test_strict_report_helper():
    assert strict_fault_recorder(FakeError, strict=False).ok
    with pytest.raises(FakeError):
        strict_fault_recorder(FakeError, strict=True).warn("x")
    report = strict_fault_recorder(FakeError, strict=False, limit=1)
    report.warn("a")
    report.warn("b")
    assert report.suppressed == 1


# --- Logging ---------------------------------------------------------------------------

def test_no_logger_means_nothing_is_logged(caplog):
    with caplog.at_level(logging.DEBUG):
        FaultRecorder().warn("quiet")
    assert caplog.records == []


def test_issues_are_logged_at_the_matching_level(caplog):
    log = logging.getLogger("pom.test")
    report = FaultRecorder(logger=log)
    with caplog.at_level(logging.DEBUG, logger="pom.test"):
        report.warn("recovered")
        report.critical("cannot continue")
    assert [(r.levelno, r.message) for r in caplog.records] == [
        (logging.WARNING, "recovered"), (logging.CRITICAL, "cannot continue")]


def test_past_the_limit_one_line_says_the_rest_are_not_logged(caplog):
    report = FaultRecorder(limit=2, logger=logging.getLogger("pom.test"))
    with caplog.at_level(logging.DEBUG, logger="pom.test"):
        for i in range(6):
            report.warn(f"w{i}")
    messages = [r.message for r in caplog.records]
    assert messages[:2] == ["w0", "w1"]
    assert len(messages) == 3 and "more than 2 faults" in messages[2]


def test_a_critical_past_the_limit_is_still_logged(caplog):
    report = FaultRecorder(limit=1, logger=logging.getLogger("pom.test"))
    with caplog.at_level(logging.DEBUG, logger="pom.test"):
        report.warn("a")
        report.warn("b")
        report.critical("must see this")
    assert any(r.levelno == logging.CRITICAL and r.message == "must see this" for r in caplog.records)


def test_strict_raises_and_logs_nothing(caplog):
    report = FaultRecorder(strict=True, error=FakeError, logger=logging.getLogger("pom.test"))
    with caplog.at_level(logging.DEBUG, logger="pom.test"):
        with pytest.raises(FakeError):
            report.warn("x")
    assert caplog.records == []


def test_strict_report_helper_passes_the_logger_on(caplog):
    report = strict_fault_recorder(FakeError, strict=False, logger=logging.getLogger("pom.test"))
    with caplog.at_level(logging.DEBUG, logger="pom.test"):
        report.warn("via helper")
    assert [r.message for r in caplog.records] == ["via helper"]
