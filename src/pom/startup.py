"""Startup: everything that has to be done, once, when the Printer starts.

The Printer is a stack of separate programs (whisper for hearing, a language model for scoring, another
for speaking) and some hardware (a microphone with a gain setting, a typewriter on a serial port). None of
it is ready the moment the service starts. This module runs the one-time preparation in order and reports
what happened, so the Conductor can start listening knowing exactly what is and is not working.

THIS IS A STUB. The runner (`Startup`) is built and tested. The duties themselves are listed in
`planned_steps()`, each a `PlannedStep` that reports itself as skipped until someone writes the real
step. Replace them one at a time; the runner and its tests do not change.

The duties, in order (see `planned_steps` for each one's detail):
    1. config-report      say what the config loader had to correct
    2. data-check         the corpus chunks, the weights file and the NLTK data are present
    3. mic-gain           set the microphone's capture gain from config (Pi only; it resets on reboot)
    4. audio-device       a capture device exists
    5. transcriber        whisper is present and answering
    6. scorer-server      the Scorer's llama-server answers
    7. scorer-warm-up     load the Scorer's model into memory (12 to 28 s the first time)
    8. voice-model        the voice model's server answers
    9. typewriter         the typewriter's serial port is present (Pi only)
   10. summary            one log line of readiness, counts only

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A step that raises is caught and recorded as
    a failure; the remaining steps still run, and `run` returns a result rather than raising. The
    Conductor reads `result.ready` and decides how to degrade (keep listening, stay quiet, say so in
    the log). `strict=True` raises StartupError at the first failed step, for tests and dev tools.
  * A step is critical or not. A failed critical step (no microphone, no corpus) is a CRITICAL fault and
    makes `ready` false; a failed optional step (the Scorer's server, which has a fallback) is a
    WARNING and the Printer carries on.
  * A step may also be skipped: it does not apply here (a Pi-only step on the Mac) or is not written
    yet. Skipping is not a problem and is never reported as one.
  * Steps are objects with a `run` method, so each is testable alone and any can be replaced or reordered
    without touching the runner. The clock is injected, so timings are testable.
  * Nothing that was said in the room can appear here; notes and logs hold names, counts and seconds.
"""
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable

from pom.log_setup import get_logger
from pom.fault_recorder import FaultRecorder

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)


class StartupError(RuntimeError):
    """Raised for a failed step, but only in strict mode (development and tests)."""


class Status(Enum):
    """How a step ended."""
    DONE = "done"
    SKIPPED = "skipped"      # does not apply here, or is not written yet
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Outcome:
    """What a step returns: how it ended, and a short note (never anything that was said)."""
    status: Status
    note: str = ""


@dataclass(frozen=True, slots=True)
class StepResult:
    """One step's outcome as the runner recorded it."""
    name: str
    status: Status
    note: str
    seconds: float
    critical: bool


class Step:
    """One startup duty. Subclass it, set `name` and `critical`, and write `run`.

    `run` returns an Outcome. It may also raise: the runner treats that as a failure.
    """
    name = "unnamed"
    critical = False

    def run(self) -> Outcome:
        raise NotImplementedError


class PlannedStep(Step):
    """A duty that is not written yet. Always skipped, with its description as the note."""

    def __init__(self, name: str, description: str, critical: bool = False):
        self.name, self.description, self.critical = name, description, critical

    def run(self) -> Outcome:
        return Outcome(Status.SKIPPED, f"planned, not written yet: {self.description}")


@dataclass(frozen=True)
class StartupResult:
    """What startup found.

    Attributes:
        results: Each step's result, in the order run.
        report: The problems: a WARNING for a failed optional step, a CRITICAL for a failed critical one.
    """
    results: tuple
    report: FaultRecorder

    @property
    def ready(self) -> bool:
        """False if any critical step failed; the Conductor decides what that means."""
        return not self.report.has_critical

    @property
    def failed(self) -> tuple:
        """The results of the steps that failed."""
        return tuple(r for r in self.results if r.status is Status.FAILED)

    @property
    def skipped(self) -> tuple:
        """The results of the steps that were skipped."""
        return tuple(r for r in self.results if r.status is Status.SKIPPED)

    def summary(self) -> str:
        """One line: how many steps were done, skipped and failed, and whether the Printer is ready."""
        counts = {status: sum(1 for r in self.results if r.status is status) for status in Status}
        return (f"startup: {counts[Status.DONE]} done, {counts[Status.SKIPPED]} skipped, "
                f"{counts[Status.FAILED]} failed; {'ready' if self.ready else 'NOT READY'}")


class Startup:
    """Runs the startup steps in order and collects what happened."""

    def __init__(self, steps: Iterable[Step], clock: Callable[[], float] = time.monotonic,
                 strict: bool = False):
        """Args:
            steps: The duties, in the order to run them.
            clock: Returns seconds; injected so tests need no waiting.
            strict: Raise StartupError at the first failed step instead of recording it.
        """
        self._steps = list(steps)
        self._clock, self._strict = clock, strict

    def run(self) -> StartupResult:
        """Runs every step. Never raises (except in strict mode); a failed step does not stop the rest."""
        report = FaultRecorder(strict=self._strict, error=StartupError, logger=_log)
        results = []
        for step in self._steps:
            started = self._clock()
            try:
                outcome = step.run()
                if not isinstance(outcome, Outcome):
                    outcome = Outcome(Status.FAILED, f"returned {type(outcome).__name__}, not an Outcome")
            except Exception as error:               # a broken step must not stop the others
                outcome = Outcome(Status.FAILED, f"raised {type(error).__name__}")
            result = StepResult(step.name, outcome.status, outcome.note, self._clock() - started, step.critical)
            results.append(result)
            self._record(result, report)
        final = StartupResult(tuple(results), report)
        _log.info(final.summary())
        return final

    @staticmethod
    def _record(result: StepResult, report: FaultRecorder) -> None:
        """Logs a step's result, and files a failure in the report at the right severity."""
        _log.info("step %s: %s in %.1f s%s", result.name, result.status.value, result.seconds,
                  f" ({result.note})" if result.note else "")
        if result.status is Status.FAILED:
            message = f"startup step '{result.name}' failed" + (f": {result.note}" if result.note else "")
            (report.critical if result.critical else report.warn)(message)


def planned_steps() -> list:
    """The startup duties, in order, each still a placeholder. See the module docstring."""
    return [
        PlannedStep("config-report", "log what the config loader had to correct (config.report)"),
        PlannedStep("data-check", "the corpus chunks, weights.json and the NLTK data are present; the "
                    "Sampler cannot draw without chunks", critical=True),
        PlannedStep("mic-gain", "set the microphone's capture gain from config (24 of 30 worked on the "
                    "ATR4697-USB); Pi only, and it resets on every reboot"),
        PlannedStep("audio-device", "a capture device exists; without ears there is nothing to do",
                    critical=True),
        PlannedStep("transcriber", "whisper's binary or server and model are present and answering",
                    critical=True),
        PlannedStep("scorer-server", "the Scorer's llama-server answers on the configured address "
                    "(start it if config says to); the Scorer has a fallback, so this is not critical"),
        PlannedStep("scorer-warm-up", "Scorer.warm_up(): the first request loads the model into memory "
                    "and takes 12 to 28 s on the Pi, so do it before the first real sentence"),
        PlannedStep("voice-model", "the voice model's server answers; without a voice nothing can be "
                    "said", critical=True),
        PlannedStep("typewriter", "the Wheelwriter's serial port is present; Pi only", critical=True),
        PlannedStep("summary", "log one line of readiness: counts and seconds only, never anything heard"),
    ]
