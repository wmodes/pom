"""Settings shared by the whole project.

One small object holds the settings every module may need, so a flag is set in one place instead
of being threaded through by hand. Today there are three:

    strict     Development and testing only. Every problem a module would recover from is raised
               instead, so a typo is loud while you work. Never set in the gallery.
    debug      Show every module's DEBUG log lines. A single module can override this flag with
               its own `DEBUG = ...` line (see pom/logs.py).
    log_level  How much the service logs to stdout (and so to journald).

Where values come from, highest priority first:
    1. Environment variables: POM_STRICT and POM_DEBUG (1/true/yes/on or 0/false/no/off) and
       POM_LOG_LEVEL.
       These suit a systemd unit (`Environment=POM_LOG_LEVEL=DEBUG`).
    2. A TOML file (config/pom.toml by default), optional.
    3. Built-in defaults: strict off, INFO.

Design decisions, and why:
  * Config obeys the project principle: it never raises. A missing file is normal (no warning), an
    unreadable or invalid one, an unknown key, or a wrong type each fall back to the default and
    are recorded in `config.report`.
  * Modules do not read Config themselves. The entry point builds one Config and passes its values
    (for example `strict=config.strict`) into the modules, so there is no hidden global state and
    any module can still be tested alone with its own `strict` argument.
  * Python 3.11's standard-library `tomllib` reads the file, so there is no dependency to install.
"""
import os
import tomllib
from pathlib import Path
from typing import Mapping, Optional

from pom.logs import get_logger
from pom.report import Report

DEFAULT_PATH = Path("config") / "pom.toml"
_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_KEYS = {"strict", "debug", "log_level"}
DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)


class Config:
    """The project's shared settings, plus a report of anything that had to be recovered from.

    Attributes:
        strict: Raise on every recoverable problem (development and tests only).
        debug: Show DEBUG output from every module (a module may override it).
        log_level: A level name: DEBUG, INFO, WARNING, ERROR or CRITICAL.
        report: Problems found while loading (bad file, unknown key, wrong type).
    """

    def __init__(self, strict: bool = False, log_level: str = "INFO",
                 report: Optional[Report] = None, debug: bool = False):
        """Use `Config.load`; this just holds already-validated values."""
        self.strict = strict
        self.debug = debug
        self.log_level = log_level
        self.report = report if report is not None else Report(logger=_log)

    @classmethod
    def load(cls, path=None, environ: Optional[Mapping[str, str]] = None) -> "Config":
        """Reads the settings from a file and the environment.

        Args:
            path: The TOML file. None means no file; a path that does not exist is fine.
            environ: The environment to read; defaults to os.environ (tests pass a dict).

        Returns:
            A Config. Any problem becomes a warning in `config.report`; this never raises.
        """
        environ = os.environ if environ is None else environ
        report = Report(logger=_log)
        values = {}
        if path is not None:
            values = _read_file(Path(path), report)

        strict = _bool(values.get("strict", False), "strict", report)
        debug = _bool(values.get("debug", False), "debug", report)
        log_level = _level(values.get("log_level", "INFO"), "log_level", report)

        if "POM_STRICT" in environ:
            strict = _bool(environ["POM_STRICT"], "POM_STRICT", report)
        if "POM_DEBUG" in environ:
            debug = _bool(environ["POM_DEBUG"], "POM_DEBUG", report)
        if "POM_LOG_LEVEL" in environ:
            log_level = _level(environ["POM_LOG_LEVEL"], "POM_LOG_LEVEL", report)
        return cls(strict, log_level, report, debug)


def _read_file(path: Path, report: Report) -> dict:
    """The file's known top-level keys; {} if it is missing (fine) or bad (warned)."""
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        report.warn(f"cannot read config file {path}: {error}; using defaults")
        return {}
    unknown = sorted(k for k in document if k not in _KEYS)
    if unknown:
        report.warn(f"config file {path} has unknown key(s): {', '.join(unknown)}; ignoring them")
    return {k: v for k, v in document.items() if k in _KEYS}


def _bool(value, label: str, report: Report) -> bool:
    """A boolean from a TOML bool or an on/off style string; otherwise False with a warning."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    report.warn(f"{label} must be true or false, got {value!r}; using false")
    return False


def _level(value, label: str, report: Report) -> str:
    """A log level name, upper-cased; otherwise INFO with a warning."""
    text = str(value).strip().upper()
    if text in _LEVELS:
        return text
    report.warn(f"{label} must be one of {', '.join(sorted(_LEVELS))}, got {value!r}; using INFO")
    return "INFO"
