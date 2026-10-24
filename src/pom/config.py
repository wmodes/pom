"""Settings shared by the whole project.

One small object holds the settings every module may need, so a flag is set in one place instead
of being threaded through by hand. Today there are five:

    strict     Development and testing only. Every problem a module would recover from is raised
               instead, so a typo is loud while you work. Never set in the gallery.
    debug      Show every module's DEBUG log lines. A single module can override this flag with
               its own `DEBUG = ...` line (see pom/log_setup.py).
    log_level  How much the service logs to stdout (and so to journald).
    local_logs Also write the log to logs/pom.log (gitignored), for working on the Mac. Off on the Pi.
    exemplars_min, exemplars_max
               How many corpus chunks go into each pull of exemplars (3 and 5 by default). The
               entry point passes `config.exemplar_range` to the ExemplarSelector, which never reads Config.
    [scorer]   A section (see ScorerSettings): the language model's address and timeout, the prompt
               that defines "interesting" and the score scale, the fragment_filter's score, how many content
               words a sentence needs to be sent to the model, and the circuit-breaker numbers. The
               entry point builds the ScorerModelClient and Scorer from `config.scorer`. Environment:
               POM_SCORER_URL.
    [trigger]  A section (see TriggerSettings): when the Printer responds. For now only
               `floor_fraction`, how far up the score range the floor sits (0.05, so 0.45 on the 0 to 9
               scale); scores below the floor are ignored. The entry point builds the
               ConvoInterestTracker from `config.trigger`.

Where values come from, highest priority first:
    1. Environment variables: POM_STRICT, POM_DEBUG and POM_LOCAL_LOGS (1/true/yes/on or
       0/false/no/off), POM_LOG_LEVEL, and POM_EXEMPLARS_MIN / POM_EXEMPLARS_MAX (whole numbers).
       These suit a systemd unit (`Environment=POM_LOG_LEVEL=DEBUG`).
    2. A per-machine override file next to the main one (config/pom.local.toml), optional and
       gitignored. Use it on the Mac to keep debug logging on without touching what the Pi gets.
    3. The TOML file (config/pom.toml by default), optional. It is committed, so it holds the safe
       defaults the Pi runs with.
    4. Built-in defaults: strict off, INFO.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from pom.log_setup import get_logger
from pom.fault_recorder import FaultRecorder

DEFAULT_PATH = Path("config") / "pom.toml"
_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_KEYS = {"strict", "debug", "log_level", "local_logs", "exemplars_min", "exemplars_max",
         "scorer.url", "scorer.timeout", "scorer.prompt_scoring", "scorer.prompt_output",
         "scorer.prompt_output_debug", "scorer.fragment_filter_score", "scorer.min_content_words", "scorer.failure_limit",
         "scorer.retry_seconds", "trigger.floor_fraction"}
DEFAULT_EXEMPLAR_RANGE = (3, 5)
DEFAULT_SCORER_URL = "http://127.0.0.1:8098/v1/chat/completions"
DEFAULT_SCORER_TIMEOUT = 15.0
DEFAULT_SCORER_FRAGMENT_FILTER_SCORE = 1.0
DEFAULT_SCORER_MIN_CONTENT_WORDS = 1
DEFAULT_SCORER_FAILURE_LIMIT = 3
DEFAULT_SCORER_RETRY_SECONDS = 30.0
DEFAULT_TRIGGER_FLOOR_FRACTION = 0.05
DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)


@dataclass(frozen=True)
class ScorerSettings:
    """The `[scorer]` section: how the Scorer reaches its language model and behaves around it.

    Attributes:
        url: The llama-server chat-completions address.
        timeout: Seconds to wait for one score.
        prompt_scoring: What "interesting" means and what the numbers mean, from the config file.
            Empty means none was set, and the Scorer then leaves every fragment unscored.
        fragment_filter_score: The score given to fragments the fragment_filter keeps from the model.
        min_content_words: Fragments with fewer content words skip the model (0 = never skip).
        failure_limit: Model failures in a row that make the Scorer leave the model alone.
        retry_seconds: How long it leaves the model alone before trying again.
        prompt_output: How the model is told to reply when it is scoring (a bare number).
        prompt_output_debug: How it is told to reply when explaining a score (the number and what
            content earned it). Only the debug view uses this.

    The system message the Scorer sends is `prompt` (scoring plus output), and the one for the
    debug view is `prompt_debug` (scoring plus the debug output); each is the two parts joined
    by a blank line.
    """
    url: str = DEFAULT_SCORER_URL
    timeout: float = DEFAULT_SCORER_TIMEOUT
    prompt_scoring: str = ""
    fragment_filter_score: float = DEFAULT_SCORER_FRAGMENT_FILTER_SCORE
    min_content_words: int = DEFAULT_SCORER_MIN_CONTENT_WORDS
    failure_limit: int = DEFAULT_SCORER_FAILURE_LIMIT
    retry_seconds: float = DEFAULT_SCORER_RETRY_SECONDS
    prompt_output: str = ""
    prompt_output_debug: str = ""

    @property
    def prompt(self) -> str:
        """The system message for scoring: the scoring part and the output part, or "" if no scoring part."""
        return _join_prompt(self.prompt_scoring, self.prompt_output)

    @property
    def prompt_debug(self) -> str:
        """The system message for explaining a score: the scoring part and the debug output part."""
        return _join_prompt(self.prompt_scoring, self.prompt_output_debug)


@dataclass(frozen=True)
class TriggerSettings:
    """The `[trigger]` section: when the Printer responds.

    Attributes:
        floor_fraction: How far up the score range the floor sits, from 0 up to but not including 1.
            Scores below the floor are ignored.
    """
    floor_fraction: float = DEFAULT_TRIGGER_FLOOR_FRACTION


class Config:
    """The project's shared settings, plus a report of anything that had to be recovered from.

    Attributes:
        strict: Raise on every recoverable problem (development and tests only).
        debug: Show DEBUG output from every module (a module may override it).
        log_level: A level name: DEBUG, INFO, WARNING, ERROR or CRITICAL.
        local_logs: Also write the log to a local file (development only).
        exemplar_range: (fewest, most) chunks per pull of exemplars, for the ExemplarSelector.
        scorer: The `[scorer]` section, as a ScorerSettings.
        trigger: The `[trigger]` section, as a TriggerSettings.
        report: Problems found while loading (bad file, unknown key, wrong type).
    """

    def __init__(self, strict: bool = False, log_level: str = "INFO",
                 report: Optional[FaultRecorder] = None, debug: bool = False, local_logs: bool = False,
                 exemplar_range: tuple = DEFAULT_EXEMPLAR_RANGE, scorer: Optional[ScorerSettings] = None,
                 trigger: Optional[TriggerSettings] = None):
        """Use `Config.load`; this just holds already-validated values."""
        self.scorer = scorer if scorer is not None else ScorerSettings()
        self.trigger = trigger if trigger is not None else TriggerSettings()
        self.strict = strict
        self.debug = debug
        self.local_logs = local_logs
        self.exemplar_range = tuple(exemplar_range)
        self.log_level = log_level
        self.report = report if report is not None else FaultRecorder(logger=_log)

    @classmethod
    def load(cls, path=None, environ: Optional[Mapping[str, str]] = None) -> "Config":
        """Reads the settings from a file and the environment.

        Args:
            path: The TOML file. None means no file; a path that does not exist is fine. A file
                named like it with `.local` before the extension (pom.local.toml) overrides it.
            environ: The environment to read; defaults to os.environ (tests pass a dict).

        Returns:
            A Config. Any problem becomes a warning in `config.report`; this never raises.
        """
        environ = os.environ if environ is None else environ
        report = FaultRecorder(logger=_log)
        values = {}
        if path is not None:
            path = Path(path)
            values = _read_file(path, report)
            values.update(_read_file(path.with_name(f"{path.stem}.local{path.suffix}"), report))

        strict = _bool(values.get("strict", False), "strict", report)
        debug = _bool(values.get("debug", False), "debug", report)
        local_logs = _bool(values.get("local_logs", False), "local_logs", report)
        low = _count(values.get("exemplars_min", DEFAULT_EXEMPLAR_RANGE[0]), "exemplars_min",
                     DEFAULT_EXEMPLAR_RANGE[0], report)
        high = _count(values.get("exemplars_max", DEFAULT_EXEMPLAR_RANGE[1]), "exemplars_max",
                      DEFAULT_EXEMPLAR_RANGE[1], report)
        log_level = _level(values.get("log_level", "INFO"), "log_level", report)
        scorer_url = _text(values.get("scorer.url", DEFAULT_SCORER_URL), "scorer.url", DEFAULT_SCORER_URL, report)
        scorer_timeout = _number(values.get("scorer.timeout", DEFAULT_SCORER_TIMEOUT), "scorer.timeout",
                                 DEFAULT_SCORER_TIMEOUT, 0.1, report)
        scorer_prompt = _text(values.get("scorer.prompt_scoring", ""), "scorer.prompt_scoring", "", report)
        scorer_output = _text(values.get("scorer.prompt_output", ""), "scorer.prompt_output", "", report)
        scorer_output_debug = _text(values.get("scorer.prompt_output_debug", ""),
                                    "scorer.prompt_output_debug", "", report)
        if scorer_prompt.strip() and not scorer_output.strip():
            report.warn("scorer.prompt_output is empty, so the model is not told how to reply")
        scorer_fragment_filter_score = _number(
            values.get("scorer.fragment_filter_score", DEFAULT_SCORER_FRAGMENT_FILTER_SCORE),
            "scorer.fragment_filter_score", DEFAULT_SCORER_FRAGMENT_FILTER_SCORE, 0.0, report)
        scorer_min_content = _whole(values.get("scorer.min_content_words", DEFAULT_SCORER_MIN_CONTENT_WORDS),
                                    "scorer.min_content_words", DEFAULT_SCORER_MIN_CONTENT_WORDS, 0, report)
        scorer_failure_limit = _whole(values.get("scorer.failure_limit", DEFAULT_SCORER_FAILURE_LIMIT),
                                      "scorer.failure_limit", DEFAULT_SCORER_FAILURE_LIMIT, 1, report)
        scorer_retry = _number(values.get("scorer.retry_seconds", DEFAULT_SCORER_RETRY_SECONDS),
                               "scorer.retry_seconds", DEFAULT_SCORER_RETRY_SECONDS, 0.0, report)

        if "POM_STRICT" in environ:
            strict = _bool(environ["POM_STRICT"], "POM_STRICT", report)
        if "POM_DEBUG" in environ:
            debug = _bool(environ["POM_DEBUG"], "POM_DEBUG", report)
        if "POM_LOCAL_LOGS" in environ:
            local_logs = _bool(environ["POM_LOCAL_LOGS"], "POM_LOCAL_LOGS", report)
        if "POM_EXEMPLARS_MIN" in environ:
            low = _count(environ["POM_EXEMPLARS_MIN"], "POM_EXEMPLARS_MIN",
                         DEFAULT_EXEMPLAR_RANGE[0], report)
        if "POM_EXEMPLARS_MAX" in environ:
            high = _count(environ["POM_EXEMPLARS_MAX"], "POM_EXEMPLARS_MAX",
                          DEFAULT_EXEMPLAR_RANGE[1], report)
        if low > high:
            report.warn(f"exemplars_min ({low}) is more than exemplars_max ({high}); swapping them")
            low, high = high, low
        if "POM_LOG_LEVEL" in environ:
            log_level = _level(environ["POM_LOG_LEVEL"], "POM_LOG_LEVEL", report)
        if "POM_SCORER_URL" in environ:
            scorer_url = _text(environ["POM_SCORER_URL"], "POM_SCORER_URL", DEFAULT_SCORER_URL, report)
        scorer = ScorerSettings(scorer_url, scorer_timeout, scorer_prompt, scorer_fragment_filter_score,
                                scorer_min_content, scorer_failure_limit, scorer_retry, scorer_output,
                                scorer_output_debug)
        trigger = TriggerSettings(_fraction(values.get("trigger.floor_fraction", DEFAULT_TRIGGER_FLOOR_FRACTION),
                                            "trigger.floor_fraction", DEFAULT_TRIGGER_FLOOR_FRACTION, report))
        return cls(strict, log_level, report, debug, local_logs, (low, high), scorer, trigger)


def _read_file(path: Path, report: FaultRecorder) -> dict:
    """The file's known top-level keys; {} if it is missing (fine) or bad (warned)."""
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        report.warn(f"cannot read config file {path}: {error}; using defaults")
        return {}
    document = _flatten(document)
    unknown = sorted(k for k in document if k not in _KEYS)
    if unknown:
        report.warn(f"config file {path} has unknown key(s): {', '.join(unknown)}; ignoring them")
    return {k: v for k, v in document.items() if k in _KEYS}


def _join_prompt(*parts: str) -> str:
    """Joins prompt parts with a blank line between, leaving out empty ones; "" if the first is empty."""
    if not parts[0].strip():
        return ""
    return "\n\n".join(part.strip() for part in parts if part.strip())


def _flatten(document: dict) -> dict:
    """Turns a `[section]` table into dotted keys ("scorer.url"), so the main and local files merge key by key."""
    flat = {}
    for key, value in document.items():
        if isinstance(value, dict):
            for inner, inner_value in value.items():
                flat[f"{key}.{inner}"] = inner_value
        else:
            flat[key] = value
    return flat


def _bool(value, label: str, report: FaultRecorder) -> bool:
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


def _count(value, label: str, default: int, report: FaultRecorder) -> int:
    """A whole number of at least 1 (a TOML integer or digits in a string); otherwise the default.

    Booleans and decimals are rejected, since `true` or `2.5` is almost certainly a typo.
    """
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    report.warn(f"{label} must be a whole number of at least 1, got {value!r}; using {default}")
    return default


def _number(value, label: str, default: float, minimum: float, report: FaultRecorder) -> float:
    """A number of at least `minimum` (an int or float, not a boolean); otherwise the default."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= minimum:
        return float(value)
    report.warn(f"{label} must be a number of at least {minimum:g}, got {value!r}; using {default:g}")
    return default


def _fraction(value, label: str, default: float, report: FaultRecorder) -> float:
    """A number from 0 up to (not including) 1 (an int or float, not a boolean); otherwise the default."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 <= value < 1.0:
        return float(value)
    report.warn(f"{label} must be a number from 0 up to but not including 1, got {value!r}; using {default:g}")
    return default


def _whole(value, label: str, default: int, minimum: int, report: FaultRecorder) -> int:
    """A whole number of at least `minimum` (not a boolean or a decimal); otherwise the default."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= minimum:
        return value
    report.warn(f"{label} must be a whole number of at least {minimum}, got {value!r}; using {default}")
    return default


def _text(value, label: str, default: str, report: FaultRecorder) -> str:
    """Text; anything else (a number, a list) is a mistake and gives the default."""
    if isinstance(value, str):
        return value
    report.warn(f"{label} must be text, got {value!r}; using the default")
    return default


def _level(value, label: str, report: FaultRecorder) -> str:
    """A log level name, upper-cased; otherwise INFO with a warning."""
    text = str(value).strip().upper()
    if text in _LEVELS:
        return text
    report.warn(f"{label} must be one of {', '.join(sorted(_LEVELS))}, got {value!r}; using INFO")
    return "INFO"
