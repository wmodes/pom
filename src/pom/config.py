"""Reads the Printer's settings: config/pom.toml, laid over the defaults in defaults.py.

Every setting is declared once, in defaults.py, with its default and the rule its value must follow. This module only reads: it lays the
file (and the per-machine file, and a few environment variables) over those defaults, checks each value against its rule, and builds one
`Config`, holding a settings object for the top-level keys (`config.project`) and one for each `[section]` (`config.conductor`, ...).
The entry point hands each class the section objects it reads; no class reads Config itself.

Where values come from, highest priority first:
    1. Environment variables (see ENVIRONMENT): POM_STRICT, POM_DEBUG and POM_LOCAL_LOGS (1/true/yes/on or 0/false/no/off),
       POM_LOG_LEVEL, POM_EXEMPLARS_MIN / POM_EXEMPLARS_MAX (whole numbers), POM_SCORER_URL and POM_WHISPER_URL. These suit a systemd unit
       (`Environment=POM_LOG_LEVEL=DEBUG`).
    2. A per-machine override file next to the main one (config/pom.local.toml), optional and gitignored. Use it on the Mac to keep debug
       logging on without touching what the Pi gets.
    3. The TOML file (config/pom.toml by default), optional. It is committed, so it holds the values the Pi runs with.
    4. The defaults in defaults.py.

Design decisions, and why:
  * Config obeys the project principle: it never raises. Every fallback to a default is said: a setting the file does not set (a line
    commented out of pom.toml takes its default), a value that breaks its rule, a file that is missing or cannot be read (one warning for
    the whole file), and an unknown key (ignored). They are held in `config.problems`, which the entry point logs once the log exists (the
    log's own settings are in this file, so it cannot log as it reads). A missing per-machine file is normal and says nothing; with no
    file asked for at all (a test, a tool) nothing is said about unset settings.
  * One loop reads every setting the same way, walking the fields defaults.py declares; there is no list of keys to keep in step by hand.
  * A few rules span several settings (the oracle's exemplar range, the printed line's width, the chart's margins, the Conductor's limits
    against the oracle's timeout, the lights' timing). They are checked here, after each section is read; they warn, and only the first
    three change a value.
  * Python 3.11's standard-library `tomllib` reads the file, so there is no dependency to install.
"""
import dataclasses
import os
import tomllib
from pathlib import Path
from typing import Mapping, Optional

from pom.defaults import (SECTIONS, BootSlipSettings, ChartSettings, ConductorSettings, DisplaySettings, ListeningSettings, MonitorSettings,
                          OracleSettings, PrintingSettings, ProjectSettings, QualifyingSettings, ScorerSettings, TerminalSettings)
from pom.setting_rules import SettingCorrected, SettingRejected, rule_of

DEFAULT_PATH = Path("config") / "pom.toml"
# Each environment variable the reader honours, and the setting it sets (it wins over both files).
ENVIRONMENT = {"POM_STRICT": "strict", "POM_DEBUG": "debug", "POM_LOCAL_LOGS": "local_logs", "POM_LOG_LEVEL": "log_level",
               "POM_EXEMPLARS_MIN": "oracle.exemplars_min", "POM_EXEMPLARS_MAX": "oracle.exemplars_max",
               "POM_SCORER_URL": "scorer.url", "POM_WHISPER_URL": "listening.whisper_url"}
# Every key a config file may hold: the top-level settings by name, the rest as "section.name". Worked out from defaults.py.
KNOWN_KEYS = frozenset([f.name for f in dataclasses.fields(ProjectSettings)]
                       + [f"{section}.{f.name}" for section, settings in SECTIONS.items() for f in dataclasses.fields(settings)])
MIN_PRINTING_LINE_WIDTH = 20
# A portent is at most this many characters per token (4 is typical English; 5 leaves room), for the printing time worked out below.
_CHARACTERS_PER_TOKEN = 5


class ConfigProblems:
    """What reading the config had to correct, held until the log exists.

    The config is read before the log is set up (the log's own settings are in it), so its problems cannot be logged as they are found. They are
    held here, as plain messages, and the entry point logs each once the log exists (as `config: ...` lines). Every one is a warning: a bad
    value was replaced by its default, an unknown key was ignored. It holds strings only, never a value it was not given.
    """

    def __init__(self):
        self._messages: list = []

    def add(self, message: str) -> None:
        """Holds one problem."""
        self._messages.append(message)

    @property
    def warnings(self) -> list:
        """The problems, in the order they were found (a copy)."""
        return list(self._messages)

    @property
    def ok(self) -> bool:
        """True when reading the config found nothing to correct."""
        return not self._messages

    def lines(self) -> list:
        """The problems as `warning: text` lines, for the log."""
        return [f"warning: {message}" for message in self._messages]


class Config:
    """The Printer's settings, as read: the top-level keys (`project`) and one settings object per `[section]`, plus what had to be corrected.

    Attributes:
        project: The top-level keys, as a ProjectSettings. `strict`, `debug`, `log_level`, `local_logs` and the three `logmute_` settings
            are also read straight from the Config.
        scorer, oracle, qualifying, listening, monitor, display, printing, terminal, conductor, boot_slip, chart: Each `[section]`, as its
            settings class from defaults.py.
        problems: What reading had to correct (a bad file, an unknown key, a value that broke its rule).
    """

    def __init__(self, project: Optional[ProjectSettings] = None, problems: Optional[ConfigProblems] = None,
                 scorer: Optional[ScorerSettings] = None, oracle: Optional[OracleSettings] = None,
                 qualifying: Optional[QualifyingSettings] = None, listening: Optional[ListeningSettings] = None,
                 monitor: Optional[MonitorSettings] = None, display: Optional[DisplaySettings] = None,
                 printing: Optional[PrintingSettings] = None, terminal: Optional[TerminalSettings] = None,
                 conductor: Optional[ConductorSettings] = None, boot_slip: Optional[BootSlipSettings] = None,
                 chart: Optional[ChartSettings] = None):
        """Use `Config.load`; this holds already-read settings (anything not given is all defaults)."""
        self.project = project if project is not None else ProjectSettings()
        self.problems = problems if problems is not None else ConfigProblems()
        self.scorer = scorer if scorer is not None else ScorerSettings()
        self.oracle = oracle if oracle is not None else OracleSettings()
        self.qualifying = qualifying if qualifying is not None else QualifyingSettings()
        self.listening = listening if listening is not None else ListeningSettings()
        self.monitor = monitor if monitor is not None else MonitorSettings()
        self.display = display if display is not None else DisplaySettings()
        self.printing = printing if printing is not None else PrintingSettings()
        self.terminal = terminal if terminal is not None else TerminalSettings()
        self.conductor = conductor if conductor is not None else ConductorSettings()
        self.boot_slip = boot_slip if boot_slip is not None else BootSlipSettings()
        self.chart = chart if chart is not None else ChartSettings()

    @property
    def strict(self) -> bool:
        """The `strict` setting."""
        return self.project.strict

    @property
    def debug(self) -> bool:
        """The `debug` setting."""
        return self.project.debug

    @property
    def log_level(self) -> str:
        """The `log_level` setting."""
        return self.project.log_level

    @property
    def local_logs(self) -> bool:
        """The `local_logs` setting."""
        return self.project.local_logs

    @property
    def logmute_after_repeats(self) -> int:
        """The `logmute_after_repeats` setting."""
        return self.project.logmute_after_repeats

    @property
    def logmute_remind_every_repeats(self) -> int:
        """The `logmute_remind_every_repeats` setting."""
        return self.project.logmute_remind_every_repeats

    @property
    def logmute_quiet_seconds(self) -> float:
        """The `logmute_quiet_seconds` setting."""
        return self.project.logmute_quiet_seconds

    @property
    def exemplar_range(self) -> tuple:
        """(fewest, most) library passages per pull of exemplars: the oracle's `exemplars_min` and `exemplars_max`, for the Bibliomancer."""
        return (self.oracle.exemplars_min, self.oracle.exemplars_max)

    @classmethod
    def load(cls, path=None, environ: Optional[Mapping[str, str]] = None) -> "Config":
        """Reads the settings from a file and the environment, over the defaults.

        Args:
            path: The TOML file. None means no file; a path that does not exist is fine. A file named like it with `.local` before the
                extension (pom.local.toml) overrides it.
            environ: The environment to read; defaults to os.environ (tests pass a dict).

        Returns:
            A Config. Any problem becomes a warning in `config.problems`; this never raises.
        """
        environ = os.environ if environ is None else environ
        problems = ConfigProblems()
        table, file_was_read = {}, False
        if path is not None:
            path = Path(path)
            main = _read_file(path, problems)
            if not path.exists():
                problems.add(f"config file {path} was not found; every setting takes its default")
            file_was_read = main is not None
            table = main or {}
            table.update(_read_file(path.with_name(f"{path.stem}.local{path.suffix}"), problems) or {})
        labels = {}                                     # a value from the environment is named by its variable in a warning
        for variable, key in ENVIRONMENT.items():
            if variable in environ:
                table[key], labels[key] = environ[variable], variable
        project = _read_settings(ProjectSettings, "", table, labels, problems, file_was_read)
        sections = {name: _read_settings(settings, name, table, labels, problems, file_was_read) for name, settings in SECTIONS.items()}
        _check_across_settings(sections, problems)
        return cls(project, problems, **sections)


def _read_settings(settings_class, section: str, table: Mapping, labels: Mapping, problems: ConfigProblems, unset_is_said: bool):
    """One settings object: each field the file sets and its rule accepts, and the default for the rest, with a warning for a bad value and,
    when a file was read (`unset_is_said`), for a setting it does not set."""
    values = {}
    for settings_field in dataclasses.fields(settings_class):
        key = f"{section}.{settings_field.name}" if section else settings_field.name
        if key not in table:
            if unset_is_said:
                problems.add(f"{key} is not set; using {_shown(settings_field.default)}")
            continue
        value, rule, label = table[key], rule_of(settings_field), labels.get(key, key)
        try:
            values[settings_field.name] = rule.read(value)
        except SettingCorrected as corrected:
            values[settings_field.name] = corrected.kept
            problems.add(f"{label} {corrected.how}")
        except SettingRejected:
            fallback = settings_field.default if rule.when_unreadable is None else rule.when_unreadable
            if fallback != settings_field.default:
                values[settings_field.name] = fallback
            problems.add(f"{label} must be {rule.wanted}, got {value!r}; using {_shown(fallback)}")
    return settings_class(**values)


def _shown(default) -> str:
    """A default as a warning says it: true/false, a number without trailing zeros, a list in brackets, text in quotes."""
    if isinstance(default, bool):
        return "true" if default else "false"
    if isinstance(default, float):
        return f"{default:g}"
    if isinstance(default, tuple):
        return str(list(default))
    if isinstance(default, str):
        return repr(default)
    return str(default)


def _check_across_settings(sections: dict, problems: ConfigProblems) -> None:
    """The rules that span several settings, checked once each section is read. The first three replace values (in `sections`); all warn."""
    scorer, oracle = sections["scorer"], sections["oracle"]
    if scorer.prompt_scoring.strip() and not scorer.prompt_output.strip():
        problems.add("scorer.prompt_output is empty, so the model is not told how to reply")
    if oracle.exemplars_min > oracle.exemplars_max:
        problems.add(f"oracle.exemplars_min ({oracle.exemplars_min}) is more than oracle.exemplars_max ({oracle.exemplars_max}); swapping them")
        sections["oracle"] = dataclasses.replace(oracle, exemplars_min=oracle.exemplars_max, exemplars_max=oracle.exemplars_min)
    printing, page = sections["printing"], PrintingSettings()
    if printing.line_width < MIN_PRINTING_LINE_WIDTH:
        problems.add(f"printing: the paper, margins and pitch leave a line of {printing.line_width} characters, under "
                     f"{MIN_PRINTING_LINE_WIDTH}; using the default paper, margins and pitch")
        sections["printing"] = dataclasses.replace(printing, paper_width_inches=page.paper_width_inches, margin_inches=page.margin_inches,
                                                   characters_per_inch=page.characters_per_inch)
    chart, drawing = sections["chart"], ChartSettings()
    if chart.drawing_width - chart.margin_left - chart.margin_right <= 0 or chart.drawing_height - chart.margin_top - chart.margin_bottom <= 0:
        problems.add("chart: the margins leave no room for the plot; using the default drawing size and margins")
        sections["chart"] = dataclasses.replace(chart, drawing_width=drawing.drawing_width, drawing_height=drawing.drawing_height,
                                                margin_left=drawing.margin_left, margin_right=drawing.margin_right,
                                                margin_top=drawing.margin_top, margin_bottom=drawing.margin_bottom)
    _check_the_limits_fit(sections["conductor"], sections["oracle"], sections["terminal"], problems)
    _check_the_lights_fit(sections["monitor"], problems)


def _check_the_limits_fit(conductor: ConductorSettings, oracle: OracleSettings, terminal: TerminalSettings, problems: ConfigProblems) -> None:
    """Warns when the Conductor's limits do not fit the settings they depend on. The values are kept: this only tells the person.

    A watchdog shorter than the thing it watches cuts off healthy turns, which is worse than a limit that is too long, so each warning
    names the limit that is too short.
    """
    if conductor.writing_seconds <= oracle.timeout:
        problems.add(f"conductor.writing_seconds ({conductor.writing_seconds:g}) should be longer than oracle.timeout ({oracle.timeout:g}), "
                     f"or the watchdog can cut off a turn the oracle is still allowed to finish")
    if conductor.write_budget_seconds >= conductor.writing_seconds:
        problems.add(f"conductor.write_budget_seconds ({conductor.write_budget_seconds:g}) should be shorter than "
                     f"conductor.writing_seconds ({conductor.writing_seconds:g}), or a retry can run into the watchdog")
    if conductor.write_budget_seconds < oracle.timeout + conductor.write_pause_seconds:
        problems.add(f"conductor.write_budget_seconds ({conductor.write_budget_seconds:g}) is shorter than oracle.timeout "
                     f"({oracle.timeout:g}) plus conductor.write_pause_seconds ({conductor.write_pause_seconds:g}), so no second attempt to "
                     f"write can ever fit")
    longest_print = oracle.max_tokens * _CHARACTERS_PER_TOKEN / terminal.characters_per_second
    if terminal.mode != "none" and conductor.printing_seconds < longest_print:
        problems.add(f"conductor.printing_seconds ({conductor.printing_seconds:g}) is shorter than the time to type the longest portent "
                     f"({longest_print:.0f} s for {oracle.max_tokens} tokens at {terminal.characters_per_second:g} characters a second)")


def _check_the_lights_fit(monitor: MonitorSettings, problems: ConfigProblems) -> None:
    """Warns about a light that would go down between two checks: a check's answer must stay good longer than the time to the next check."""
    if monitor.service_down_after_seconds <= monitor.service_check_seconds:
        problems.add(f"monitor.service_down_after_seconds ({monitor.service_down_after_seconds:g}) should be longer than monitor.service_check_seconds "
                     f"({monitor.service_check_seconds:g}), or a light goes down between two checks")


def _read_file(path: Path, problems: ConfigProblems) -> Optional[dict]:
    """The file's keys, flattened to "section.name"; None if it is missing (said by the caller where it matters) or cannot be read (warned).
    Unknown keys are warned (and never read)."""
    if not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as error:
        problems.add(f"cannot read config file {path}: {error}; using defaults")
        return None
    document = _flatten(document)
    unknown = sorted(k for k in document if k not in KNOWN_KEYS)
    if unknown:
        problems.add(f"config file {path} has unknown key(s): {', '.join(unknown)}; ignoring them")
    return document


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
