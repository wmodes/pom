"""Tests for pom.startup: the startup runner and the list of planned duties."""
import logging

import pytest

import pom.startup as startup_module
from pom.startup import (Outcome, PlannedStep, Startup, StartupError, StartupResult, Status, Step,
                         StepResult, planned_steps)


class Ticking:
    """A clock that moves forward 1.5 s every time it is read."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 1.5
        return self.now


class Fake(Step):
    """A step that does what it is told: return an outcome, or raise."""

    def __init__(self, name, outcome=None, critical=False, error=None, log=None):
        self.name, self.critical = name, critical
        self._outcome, self._error, self._log = outcome or Outcome(Status.DONE), error, log

    def run(self):
        if self._log is not None:
            self._log.append(self.name)
        if self._error is not None:
            raise self._error
        return self._outcome


def run(steps, **options):
    options.setdefault("clock", Ticking())
    return Startup(steps, **options).run()


class TestRunning:
    def test_steps_run_in_order_and_each_result_is_kept(self):
        order = []
        result = run([Fake("zeta", log=order), Fake("mic", log=order), Fake("alpha", log=order)])
        assert order == ["zeta", "mic", "alpha"]
        assert [r.name for r in result.results] == ["zeta", "mic", "alpha"]

    def test_a_result_holds_status_note_time_and_criticality(self):
        result = run([Fake("mic", Outcome(Status.DONE, "gain set to 24"), critical=True)])
        assert result.results[0] == StepResult("mic", Status.DONE, "gain set to 24", 1.5, True)

    def test_every_step_is_timed_from_its_own_start(self):
        result = run([Fake("a"), Fake("b"), Fake("c")])
        assert [r.seconds for r in result.results] == [1.5, 1.5, 1.5]

    def test_an_empty_list_is_ready(self):
        result = run([])
        assert result.ready and result.results == () and result.report.ok

    def test_a_clean_run_is_ready_and_quiet(self):
        result = run([Fake("a"), Fake("b")])
        assert result.ready and result.report.ok and result.failed == ()

    def test_skipped_steps_are_not_a_problem(self):
        result = run([Fake("pi-only", Outcome(Status.SKIPPED, "not on a Pi"), critical=True)])
        assert result.ready and result.report.ok and len(result.skipped) == 1


class TestFailures:
    def test_a_failed_optional_step_is_a_warning_and_the_printer_is_still_ready(self):
        result = run([Fake("scorer", Outcome(Status.FAILED, "no answer"))])
        assert result.ready and len(result.report.warnings) == 1 and not result.report.criticals
        assert "scorer" in result.report.warnings[0] and "no answer" in result.report.warnings[0]

    def test_a_failed_critical_step_is_critical_and_the_printer_is_not_ready(self):
        result = run([Fake("mic", Outcome(Status.FAILED, "no device"), critical=True)])
        assert not result.ready and len(result.report.criticals) == 1 and not result.report.warnings

    def test_the_steps_after_a_failure_still_run(self):
        order = []
        result = run([Fake("a", Outcome(Status.FAILED), critical=True, log=order), Fake("b", log=order)])
        assert order == ["a", "b"] and not result.ready and result.results[1].status is Status.DONE

    def test_a_step_that_raises_is_a_failure_not_a_crash(self):
        result = run([Fake("a", error=RuntimeError("boom")), Fake("b")])
        assert result.results[0].status is Status.FAILED and result.results[1].status is Status.DONE
        assert result.results[0].note == "raised RuntimeError"

    def test_the_text_of_an_exception_is_not_recorded(self):
        result = run([Fake("a", error=ValueError("something said in the room"))])
        assert "room" not in result.results[0].note and all("room" not in w for w in result.report.warnings)

    def test_a_step_that_returns_the_wrong_thing_is_a_failure(self):
        result = run([Fake("a", outcome="done")])
        assert result.results[0].status is Status.FAILED and "str" in result.results[0].note

    def test_a_base_step_that_is_not_written_is_a_failure(self):
        result = run([Step()])
        assert result.results[0].status is Status.FAILED

    def test_failed_lists_only_the_failures(self):
        result = run([Fake("a"), Fake("b", Outcome(Status.FAILED)), Fake("c", Outcome(Status.SKIPPED))])
        assert [r.name for r in result.failed] == ["b"] and [r.name for r in result.skipped] == ["c"]

    def test_strict_mode_raises_at_the_first_failure(self):
        order = []
        with pytest.raises(StartupError):
            run([Fake("a", Outcome(Status.FAILED), log=order), Fake("b", log=order)], strict=True)
        assert order == ["a"]


class TestSummary:
    def test_summary_counts_and_says_ready(self):
        text = run([Fake("a"), Fake("b", Outcome(Status.SKIPPED)), Fake("c", Outcome(Status.FAILED))]).summary()
        assert text == "startup: 1 done, 1 skipped, 1 failed; ready"

    def test_summary_says_when_it_is_not_ready(self):
        text = run([Fake("a", Outcome(Status.FAILED), critical=True)]).summary()
        assert text.endswith("NOT READY")

    def test_the_summary_and_each_step_are_logged(self, caplog):
        with caplog.at_level(logging.INFO, logger="pom.startup"):
            run([Fake("mic"), Fake("scorer", Outcome(Status.FAILED, "no answer"))])
        assert "step mic: done" in caplog.text and "step scorer: failed" in caplog.text
        assert "startup: 1 done, 0 skipped, 1 failed; ready" in caplog.text


class TestPlannedSteps:
    NAMES = ["config-report", "data-check", "mic-gain", "audio-device", "transcriber", "scorer-server",
             "scorer-warm-up", "voice-model", "typewriter", "summary"]

    def test_the_planned_duties_in_order(self):
        assert [step.name for step in planned_steps()] == self.NAMES

    def test_which_duties_are_critical(self):
        critical = {step.name for step in planned_steps() if step.critical}
        assert critical == {"data-check", "audio-device", "transcriber", "voice-model", "typewriter"}

    def test_every_planned_step_has_a_description(self):
        assert all(isinstance(step, PlannedStep) and len(step.description) > 20 for step in planned_steps())

    def test_running_the_stub_skips_everything_and_is_ready(self):
        result = run(planned_steps())
        assert len(result.skipped) == len(self.NAMES) and result.ready and result.report.ok
        assert all(r.note.startswith("planned, not written yet") for r in result.results)

    def test_the_module_docstring_lists_every_duty(self):
        assert all(name in startup_module.__doc__ for name in self.NAMES)

    def test_each_call_gives_fresh_steps(self):
        assert planned_steps()[0] is not planned_steps()[0]
