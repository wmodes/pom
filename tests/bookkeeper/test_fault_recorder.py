"""Tests for pom.bookkeeper.fault_recorder: recording problems as data, with a strict mode for development."""
import logging

import pytest

from pom.bookkeeper.fault_recorder import Fault, FaultRecorder, Severity, strict_fault_recorder


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
    report.critical("no passages to draw from")
    assert report.has_critical and not report.ok
    assert report.criticals == ["no passages to draw from"] and report.warnings == []


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


def test_every_fault_is_logged_even_past_the_limit_and_the_recorder_mutes_nothing(caplog):
    report = FaultRecorder(limit=2, logger=logging.getLogger("pom.test"))
    with caplog.at_level(logging.DEBUG, logger="pom.test"):
        for i in range(6):
            report.warn(f"w{i}")
    assert [r.message for r in caplog.records] == [f"w{i}" for i in range(6)]       # the log's LogMuter mutes repeats, not the recorder


def test_past_the_limit_the_list_for_reading_keeps_the_first_ones_and_counts_the_rest():
    report = FaultRecorder(limit=2, logger=logging.getLogger("pom.test"))
    for i in range(6):
        report.warn(f"w{i}")
    assert report.warnings == ["w0", "w1"] and report.suppressed == 4
    assert report.lines()[-1] == "... and 4 more"


def test_a_new_kind_of_fault_past_the_limit_is_still_logged(caplog):
    report = FaultRecorder(limit=1, logger=logging.getLogger("pom.test"))
    with caplog.at_level(logging.DEBUG, logger="pom.test"):
        for _ in range(5):
            report.warn("whisper did not answer")
        report.warn("whisper's reply was not text")
    assert caplog.records[-1].message == "whisper's reply was not text"


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


# --- the five ways to say something: debug, info, warn, error, critical -------------------------------------

LEVEL_CASES = [("debug", logging.DEBUG), ("info", logging.INFO), ("warn", logging.WARNING), ("error", logging.ERROR), ("critical", logging.CRITICAL)]


@pytest.mark.parametrize("method, level", LEVEL_CASES)
def test_each_way_of_saying_something_logs_at_its_own_level_through_the_recorders_logger(caplog, method, level):
    report = FaultRecorder(logger=logging.getLogger("pom.test"))
    with caplog.at_level(logging.DEBUG, logger="pom.test"):
        getattr(report, method)("something happened")
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [(level, "something happened")]


@pytest.mark.parametrize("method, level", LEVEL_CASES)
def test_each_takes_percent_arguments_like_a_logger_does(caplog, method, level):
    report = FaultRecorder(logger=logging.getLogger("pom.test"))
    with caplog.at_level(logging.DEBUG, logger="pom.test"):
        getattr(report, method)("scored %d clips in %.1f s", 3, 2.5)
    assert caplog.records[0].getMessage() == "scored 3 clips in 2.5 s"


@pytest.mark.parametrize("method", ["debug", "info", "warn", "error", "critical"])
def test_a_message_that_does_not_fit_its_arguments_is_logged_as_written_and_never_raises(caplog, method):
    report = FaultRecorder(logger=logging.getLogger("pom.test"))
    with caplog.at_level(logging.DEBUG, logger="pom.test"):
        getattr(report, method)("needs a number %d", "not a number")
    assert caplog.records[0].getMessage() == "needs a number %d"


def test_debug_and_info_are_only_logged_so_they_are_not_faults(caplog):
    report = FaultRecorder(logger=logging.getLogger("pom.test"))
    with caplog.at_level(logging.DEBUG, logger="pom.test"):
        for _ in range(100):
            report.debug("d")
            report.info("i")
    assert report.ok and len(report) == 0 and report.lines() == [] and report.suppressed == 0 and not report.has_critical


@pytest.mark.parametrize("method", ["debug", "info"])
def test_debug_and_info_do_not_raise_in_strict_mode_and_are_still_logged(caplog, method):
    report = FaultRecorder(strict=True, error=FakeError, logger=logging.getLogger("pom.test"))
    with caplog.at_level(logging.DEBUG, logger="pom.test"):
        getattr(report, method)("fine")
    assert [r.getMessage() for r in caplog.records] == ["fine"]


@pytest.mark.parametrize("method", ["debug", "info"])
def test_debug_and_info_with_no_logger_do_nothing(caplog, method):
    with caplog.at_level(logging.DEBUG):
        getattr(FaultRecorder(), method)("quiet")
    assert caplog.records == []


def test_error_is_a_fault_kept_and_counted_like_a_warning_but_not_a_critical():
    report = FaultRecorder(limit=2)
    report.error("a request failed and was skipped")
    report.error("another")
    report.error("a third")
    assert not report.ok and report.errors == ["a request failed and was skipped", "another"] and report.suppressed == 1
    assert report.warnings == [] and report.criticals == [] and not report.has_critical
    assert report.lines()[0] == "error: a request failed and was skipped" and report.lines()[-1] == "... and 1 more"


def test_error_raises_in_strict_mode_and_logs_nothing(caplog):
    report = FaultRecorder(strict=True, error=FakeError, logger=logging.getLogger("pom.test"))
    with caplog.at_level(logging.DEBUG, logger="pom.test"):
        with pytest.raises(FakeError):
            report.error("x")
    assert caplog.records == []


def test_the_message_of_a_fault_kept_for_reading_is_the_formatted_one():
    report = FaultRecorder()
    report.warn("got %r, using %d", "abc", 5)
    report.error("tried %d times", 3)
    assert report.warnings == ["got 'abc', using 5"] and report.errors == ["tried 3 times"]


def test_the_lines_go_through_the_log_muter_like_any_other_but_debug_is_never_muted():
    import io
    from pom.bookkeeper.log_muter import LogMuter
    stream = io.StringIO()
    logger = logging.getLogger("pom.test.muted")
    logger.propagate = False
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.addFilter(LogMuter(2, 100, 300.0))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        report = FaultRecorder(logger=logger)
        for _ in range(5):
            report.info("same")
            report.error("same")
            report.debug("same")
    finally:
        logger.removeHandler(handler)
    text = stream.getvalue()
    assert text.count("INFO same") == 2 and text.count("ERROR same") == 2 and text.count("DEBUG same") == 5
    assert text.count("LOGMUTE: Last message repeating and muted") == 2


@pytest.mark.parametrize("method", ["debug", "info", "warn", "error", "critical"])
def test_a_message_with_no_arguments_is_left_as_written_like_a_logger_does(caplog, method):
    report = FaultRecorder(logger=logging.getLogger("pom.test"))
    with caplog.at_level(logging.DEBUG, logger="pom.test"):
        getattr(report, method)("100%% sure")
    assert caplog.records[0].getMessage() == "100%% sure"
