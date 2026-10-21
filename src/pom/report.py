"""Problems found while loading or running, reported as data instead of raised.

DESIGN PRINCIPLE (project-wide): the Printer must never stop because of a typo. A hand-edited
file with a bad number, one corrupt line in a corpus file, a missing optional file: none of
these may crash the oracle in a gallery. A module that meets a problem it can recover from
recovers (it falls back to a default, skips the bad entry) and records what it did here, so a
person can fix it later. Nothing is printed or raised at the point of the problem.

Two severities:
  * WARNING:  recovered. The module carried on with a default or skipped something.
  * CRITICAL: the module cannot do its job at all (for example, there are no chunks to draw
              from). It still does not raise; it returns an empty result and says so, and the
              Conductor decides how to degrade (stay quiet and keep listening, say so in the log).

STRICT MODE exists for development: with strict=True every issue is raised immediately as the
module's own error type. Tests and dev tools run strict so a typo is loud while you work; the
runtime never does.

A Report keeps the first `limit` issues and counts the rest, so one corrupt 70,000-line file
cannot flood the log or exhaust memory. Critical issues are always kept.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Optional


class Severity(Enum):
    """How bad an issue is."""
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Issue:
    """One recorded problem.

    Attributes:
        severity: WARNING (recovered) or CRITICAL (cannot do its job).
        message: A short human-readable description, naming the file or entry if there is one.
    """
    severity: Severity
    message: str

    def __str__(self) -> str:
        return f"{self.severity.value}: {self.message}"


class Report:
    """A collection of issues from one module or one operation.

    Args:
        strict: If true, every issue is raised at once as `error` (development only).
        error: The exception type raised in strict mode; defaults to ValueError.
        limit: How many issues to keep; further ones are only counted.
    """

    def __init__(self, strict: bool = False, error: type = ValueError, limit: int = 50):
        self._strict = strict
        self._error = error
        self._limit = limit
        self._issues: list[Issue] = []
        self._suppressed = 0

    # --- Recording ------------------------------------------------------------------

    def warn(self, message: str) -> None:
        """Records a recovered problem."""
        self._add(Issue(Severity.WARNING, message))

    def critical(self, message: str) -> None:
        """Records a problem the module could not recover from."""
        self._add(Issue(Severity.CRITICAL, message))

    def extend(self, other: "Report") -> None:
        """Adds every issue from another report (for example a sub-module's)."""
        for issue in other.issues:
            self._add(issue)
        self._suppressed += other.suppressed

    def _add(self, issue: Issue) -> None:
        """Raises in strict mode; otherwise keeps the issue, or counts it past the limit."""
        if self._strict:
            raise self._error(issue.message)
        # A critical issue is always kept: it is rare, and it must never be hidden behind
        # a flood of warnings.
        if issue.severity is Severity.CRITICAL or len(self._issues) < self._limit:
            self._issues.append(issue)
        else:
            self._suppressed += 1

    # --- Reading --------------------------------------------------------------------

    @property
    def issues(self) -> tuple[Issue, ...]:
        """The kept issues, in the order they were found."""
        return tuple(self._issues)

    @property
    def warnings(self) -> list[str]:
        """Messages of the WARNING issues."""
        return [i.message for i in self._issues if i.severity is Severity.WARNING]

    @property
    def criticals(self) -> list[str]:
        """Messages of the CRITICAL issues."""
        return [i.message for i in self._issues if i.severity is Severity.CRITICAL]

    @property
    def suppressed(self) -> int:
        """How many issues were not kept because the limit was reached."""
        return self._suppressed

    @property
    def ok(self) -> bool:
        """True when nothing at all was reported."""
        return not self._issues and not self._suppressed

    @property
    def has_critical(self) -> bool:
        """True if any CRITICAL issue was recorded. Criticals are never suppressed."""
        return any(i.severity is Severity.CRITICAL for i in self._issues)

    def lines(self) -> list[str]:
        """The report as printable lines, ending with a count of any suppressed issues."""
        out = [str(issue) for issue in self._issues]
        if self._suppressed:
            out.append(f"... and {self._suppressed} more")
        return out

    def __iter__(self) -> Iterator[Issue]:
        return iter(self._issues)

    def __len__(self) -> int:
        return len(self._issues) + self._suppressed

    def __repr__(self) -> str:
        return f"Report({len(self)} issue(s){', critical' if self.has_critical else ''})"


def strict_report(error: type, strict: bool, limit: Optional[int] = None) -> Report:
    """A Report for a module: strict raises `error`, lenient collects. Small convenience."""
    return Report(strict=strict, error=error, **({} if limit is None else {"limit": limit}))
