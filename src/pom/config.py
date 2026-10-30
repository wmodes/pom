"""Settings shared by the whole project.

One small object holds the settings every module may need, so a flag is set in one place instead
of being threaded through by hand. Today there are five:

    strict     Development and testing only. Every problem a module would recover from is raised
               instead, so a typo is loud while you work. Never set in the gallery.
    debug      Show every module's DEBUG log lines. A single module can override this flag with
               its own `DEBUG = ...` line (see pom/log_setup.py).
    log_level  How much the service logs to stdout (and so to journald).
    local_logs Also write the log to logs/pom.log (gitignored), for working on the Mac. Off on the Pi.
    [scorer]   A section (see ScorerSettings): the language model's address and timeout, the prompt
               that defines "interesting" and the score scale, the fragment_filter's score, how many content
               words a sentence needs to be sent to the model, and the circuit-breaker numbers. The
               entry point builds the ScorerModelClient and Scorer from `config.scorer`. Environment:
               POM_SCORER_URL.
    [oracle]   A section (see OracleSettings): what the oracle model is given and how it is reached. The
               `prompt` (with `{exemplars}` and `{hinge}` marking where those go), `exemplars_min` and
               `exemplars_max` (how many corpus chunks go into each pull of exemplars, 3 and 5 by default), the
               model's address and timeout, and the two settings of each request, `max_tokens` and
               `temperature`. The entry point builds the PromptBuilder and the OracleModelClient from
               `config.oracle` and passes `config.exemplar_range` to the ExemplarSelector, which never reads Config.
    [trigger]  A section (see TriggerSettings): when the Printer responds, when a score is above a bar worked out from the
               room's recent scores. `algorithm` picks how ("percentile", "decaying_quantile" or "moving_average"; each reads only
               its own settings), `starting_bar` is where the bar begins, `cooldown_seconds` how long after a trigger the next
               may come (60; 0 for none). The entry point builds the ConvoInterestTracker with `tracker_from_settings(config.trigger)`.
    [listening] A section (see ListeningSettings): how the Printer hears the room. The address and timeout of the
               whisper-server, the microphone's capture device and buffer, and the RollingCutter's `step`, `hold`,
               `rewind` and `max_buffer`, and the Listener's sound pass and caption memory. The defaults are the
               combination that worked in the room; change one at a time. The entry point builds the
               WhisperServerClient, MicSource and Listener from `config.listening`.
    [monitor]  A section (see MonitorSettings): the monitoring site, a web page of the last hour of triggers. Whether it
               runs, the address and port it listens on, its password (set it in config/pom.local.toml, never in the
               committed file), whether it shows the hinge text, and how long a trigger stays on the page.

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
_KEYS = {"strict", "debug", "log_level", "local_logs", "oracle.exemplars_min", "oracle.exemplars_max",
         "scorer.url", "scorer.timeout", "scorer.prompt_scoring", "scorer.prompt_output",
         "scorer.prompt_output_debug", "scorer.fragment_filter_score", "scorer.min_content_words", "scorer.failure_limit",
         "scorer.retry_seconds", "oracle.url", "oracle.timeout", "oracle.prompt", "oracle.max_tokens",
         "oracle.temperature", "trigger.min_scores", "trigger.window_seconds", "trigger.percentile",
         "trigger.cooldown_seconds", "trigger.algorithm", "trigger.starting_bar", "trigger.exclude_triggers",
         "trigger.half_life_seconds", "trigger.average_fraction", "trigger.average_half_life_scores",
         "trigger.average_floor_fraction", "listening.whisper_url", "listening.whisper_timeout", "listening.device",
         "listening.buffer_seconds", "listening.step", "listening.hold", "listening.rewind", "listening.max_buffer",
         "listening.sound_every_steps", "listening.sound_seconds", "listening.ambient_memory",
         "listening.consider_ambient_sounds", "monitor.enabled", "monitor.host", "monitor.port", "monitor.password",
         "monitor.show_hinges", "monitor.history_seconds"}
DEFAULT_EXEMPLAR_RANGE = (3, 5)
DEFAULT_SCORER_URL = "http://127.0.0.1:8098/v1/chat/completions"
DEFAULT_SCORER_TIMEOUT = 15.0
DEFAULT_SCORER_FRAGMENT_FILTER_SCORE = 1.0
DEFAULT_SCORER_MIN_CONTENT_WORDS = 1
DEFAULT_SCORER_FAILURE_LIMIT = 3
DEFAULT_SCORER_RETRY_SECONDS = 30.0
DEFAULT_ORACLE_URL = "http://127.0.0.1:8099/v1/chat/completions"
DEFAULT_ORACLE_TIMEOUT = 120.0
DEFAULT_ORACLE_MAX_TOKENS = 300
DEFAULT_ORACLE_TEMPERATURE = 0.8
DEFAULT_TRIGGER_PERCENTILE = 99.5
DEFAULT_TRIGGER_WINDOW_SECONDS = 300.0
DEFAULT_TRIGGER_MIN_SCORES = 20
DEFAULT_TRIGGER_COOLDOWN_SECONDS = 60.0
DEFAULT_TRIGGER_ALGORITHM = "percentile"
TRIGGER_ALGORITHMS = ("percentile", "decaying_quantile", "moving_average")
DEFAULT_TRIGGER_STARTING_BAR = 6.8
DEFAULT_TRIGGER_HALF_LIFE_SECONDS = 300.0
DEFAULT_TRIGGER_AVERAGE_FRACTION = 0.25
DEFAULT_TRIGGER_AVERAGE_HALF_LIFE_SCORES = 10.0
DEFAULT_TRIGGER_AVERAGE_FLOOR_FRACTION = 0.05
DEFAULT_LISTENING_WHISPER_URL = "http://127.0.0.1:8110"
DEFAULT_LISTENING_WHISPER_TIMEOUT = 180.0
DEFAULT_LISTENING_DEVICE = "plughw:0,0"
DEFAULT_LISTENING_BUFFER_SECONDS = 10.0
DEFAULT_LISTENING_STEP = 5.0
DEFAULT_LISTENING_HOLD = 2.0
DEFAULT_LISTENING_REWIND = 0.0
DEFAULT_LISTENING_MAX_BUFFER = 30.0
DEFAULT_LISTENING_SOUND_EVERY_STEPS = 3
DEFAULT_LISTENING_SOUND_SECONDS = 10.0
DEFAULT_LISTENING_AMBIENT_MEMORY = 6
DEFAULT_LISTENING_CONSIDER_AMBIENT_SOUNDS = False
DEFAULT_MONITOR_ENABLED = False
DEFAULT_MONITOR_HOST = "0.0.0.0"
DEFAULT_MONITOR_PORT = 8080
DEFAULT_MONITOR_SHOW_HINGES = True
DEFAULT_MONITOR_HISTORY_SECONDS = 3600.0
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
class OracleSettings:
    """The `[oracle]` section: how the oracle model is reached and what it is asked.

    Attributes:
        url: The llama-server chat-completions address.
        timeout: Seconds to wait for one portent. Writing is slow on the Pi, so this is long.
        prompt: The text the model is given, with `{exemplars}` and `{hinge}` marking where those go.
            Empty means none was set, and no portent can be asked for.
        max_tokens: The most the model may write (a portent is 15 to 150 words; this stops a runaway).
        temperature: How adventurous the model's word choice is; higher is more varied.
    """
    url: str = DEFAULT_ORACLE_URL
    timeout: float = DEFAULT_ORACLE_TIMEOUT
    prompt: str = ""
    max_tokens: int = DEFAULT_ORACLE_MAX_TOKENS
    temperature: float = DEFAULT_ORACLE_TEMPERATURE


@dataclass(frozen=True)
class TriggerSettings:
    """The `[trigger]` section: when the Printer responds.

    `algorithm` picks how the bar is worked out; each algorithm reads only its own settings, so one section can hold them all and
    switching is one line.

    Attributes:
        percentile: How much of the room's recent scores a score must beat to trigger, above 0 and at most 100. 100 means beating
            the best score; 99.5 beating all but about one in two hundred; 90 being in the top tenth. (percentile and decaying_quantile)
        window_seconds: How many seconds of recent scores the bar is drawn from, above 0 (300 is the last five minutes). Measured in
            time, not in scores, so it means the same in a busy room and a quiet one. (percentile)
        min_scores: The window always holds at least this many scores, and until this many have been heard the bar is the
            starting bar, a whole number of at least 2. (percentile)
        cooldown_seconds: How many seconds after a trigger before another score may trigger, 0 or more (0 is no cooldown). (all)
        starting_bar: The bar before the room has said enough to work one out, and where it begins; it fades as the room speaks.
            There is no silent warm-up. (all)
        exclude_triggers: True leaves a score that triggered out of what the bar is built from, so one zinger does not hold the bar up
            and shut out the next. (percentile and decaying_quantile)
        half_life_seconds: Seconds for a score to count half as much, above 0. (decaying_quantile)
        average_fraction: How far above the decaying average the bar sits, 0 or more (0.25 is 25% above). (moving_average)
        average_half_life_scores: How many more scores it takes for a score's influence on the average to halve, above 0.
            (moving_average)
        average_floor_fraction: How far up the 0 to 9 scale the floor sits; scores below it are ignored. From 0 up to but not
            including 1. (moving_average)
        algorithm: "percentile", "decaying_quantile" or "moving_average".
    """
    percentile: float = DEFAULT_TRIGGER_PERCENTILE
    window_seconds: float = DEFAULT_TRIGGER_WINDOW_SECONDS
    min_scores: int = DEFAULT_TRIGGER_MIN_SCORES
    cooldown_seconds: float = DEFAULT_TRIGGER_COOLDOWN_SECONDS
    starting_bar: float = DEFAULT_TRIGGER_STARTING_BAR
    exclude_triggers: bool = False
    half_life_seconds: float = DEFAULT_TRIGGER_HALF_LIFE_SECONDS
    average_fraction: float = DEFAULT_TRIGGER_AVERAGE_FRACTION
    average_half_life_scores: float = DEFAULT_TRIGGER_AVERAGE_HALF_LIFE_SCORES
    average_floor_fraction: float = DEFAULT_TRIGGER_AVERAGE_FLOOR_FRACTION
    algorithm: str = DEFAULT_TRIGGER_ALGORITHM


@dataclass(frozen=True)
class ListeningSettings:
    """The `[listening]` section: how the Printer hears the room.

    The defaults are the combination that worked well in the room (whisper `base.en` with its voice detection at 0.5,
    a round every 5 seconds); a test pins them. Change one at a time.

    Attributes:
        whisper_url: The whisper-server's address (POM_WHISPER_URL overrides it).
        whisper_timeout: Seconds to wait for one transcription, above 0.
        device: The ALSA capture device for the microphone, as `arecord -D` takes it.
        buffer_seconds: Sound held for the microphone before the oldest is dropped, above 0.
        step: Seconds of new audio between whisper runs, above 0.
        hold: A segment is kept once it ends this many seconds before the end of the buffer, 0 or more.
        rewind: Seconds before the last kept segment's end that the next run starts, 0 or more (0 is off).
        max_buffer: Seconds without a finished segment before one is forced out, above 0.
        sound_every_steps: After this many runs in a row with no speech, listen once for lyrics and captions;
            a whole number of at least 0, 0 for never.
        sound_seconds: How much recent audio that pass listens to, above 0.
        ambient_memory: How many recent captions of sounds to remember, a whole number of at least 1.
        consider_ambient_sounds: True lets captions of sounds like "(dog barks)" be scored and become hinges, as
            speech does. False (the default) leaves them unscored.
    """
    whisper_url: str = DEFAULT_LISTENING_WHISPER_URL
    whisper_timeout: float = DEFAULT_LISTENING_WHISPER_TIMEOUT
    device: str = DEFAULT_LISTENING_DEVICE
    buffer_seconds: float = DEFAULT_LISTENING_BUFFER_SECONDS
    step: float = DEFAULT_LISTENING_STEP
    hold: float = DEFAULT_LISTENING_HOLD
    rewind: float = DEFAULT_LISTENING_REWIND
    max_buffer: float = DEFAULT_LISTENING_MAX_BUFFER
    sound_every_steps: int = DEFAULT_LISTENING_SOUND_EVERY_STEPS
    sound_seconds: float = DEFAULT_LISTENING_SOUND_SECONDS
    ambient_memory: int = DEFAULT_LISTENING_AMBIENT_MEMORY
    consider_ambient_sounds: bool = DEFAULT_LISTENING_CONSIDER_AMBIENT_SOUNDS


@dataclass(frozen=True)
class MonitorSettings:
    """The `[monitor]` section: the monitoring site, a web page of the last hour of triggers.

    Attributes:
        enabled: True starts the site with the Printer.
        host: The address it listens on; 0.0.0.0 means every network interface, so anyone on the local network can reach it.
        port: The port it listens on, 1 to 65535.
        password: The site's password. The site does not start while it is empty; set it in config/pom.local.toml,
            which git ignores, never in pom.toml.
        show_hinges: True shows the hinge (what was said in the room) on the page; False shows numbers only.
        history_seconds: How long a trigger stays on the page, above 0 (3600 is an hour). Kept in memory only.
    """
    enabled: bool = DEFAULT_MONITOR_ENABLED
    host: str = DEFAULT_MONITOR_HOST
    port: int = DEFAULT_MONITOR_PORT
    password: str = ""
    show_hinges: bool = DEFAULT_MONITOR_SHOW_HINGES
    history_seconds: float = DEFAULT_MONITOR_HISTORY_SECONDS


class Config:
    """The project's shared settings, plus a report of anything that had to be recovered from.

    Attributes:
        strict: Raise on every recoverable problem (development and tests only).
        debug: Show DEBUG output from every module (a module may override it).
        log_level: A level name: DEBUG, INFO, WARNING, ERROR or CRITICAL.
        local_logs: Also write the log to a local file (development only).
        exemplar_range: (fewest, most) chunks per pull of exemplars, for the ExemplarSelector.
        scorer: The `[scorer]` section, as a ScorerSettings.
        oracle: The `[oracle]` section, as an OracleSettings (the exemplar range is `exemplar_range`).
        trigger: The `[trigger]` section, as a TriggerSettings.
        listening: The `[listening]` section, as a ListeningSettings.
        monitor: The `[monitor]` section, as a MonitorSettings.
        report: Problems found while loading (bad file, unknown key, wrong type).
    """

    def __init__(self, strict: bool = False, log_level: str = "INFO",
                 report: Optional[FaultRecorder] = None, debug: bool = False, local_logs: bool = False,
                 exemplar_range: tuple = DEFAULT_EXEMPLAR_RANGE, scorer: Optional[ScorerSettings] = None,
                 trigger: Optional[TriggerSettings] = None, oracle: Optional[OracleSettings] = None,
                 listening: Optional[ListeningSettings] = None, monitor: Optional[MonitorSettings] = None):
        """Use `Config.load`; this just holds already-validated values."""
        self.scorer = scorer if scorer is not None else ScorerSettings()
        self.oracle = oracle if oracle is not None else OracleSettings()
        self.trigger = trigger if trigger is not None else TriggerSettings()
        self.listening = listening if listening is not None else ListeningSettings()
        self.monitor = monitor if monitor is not None else MonitorSettings()
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
        low = _count(values.get("oracle.exemplars_min", DEFAULT_EXEMPLAR_RANGE[0]), "oracle.exemplars_min",
                     DEFAULT_EXEMPLAR_RANGE[0], report)
        high = _count(values.get("oracle.exemplars_max", DEFAULT_EXEMPLAR_RANGE[1]), "oracle.exemplars_max",
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
            report.warn(f"oracle.exemplars_min ({low}) is more than oracle.exemplars_max ({high}); swapping them")
            low, high = high, low
        if "POM_LOG_LEVEL" in environ:
            log_level = _level(environ["POM_LOG_LEVEL"], "POM_LOG_LEVEL", report)
        if "POM_SCORER_URL" in environ:
            scorer_url = _text(environ["POM_SCORER_URL"], "POM_SCORER_URL", DEFAULT_SCORER_URL, report)
        scorer = ScorerSettings(scorer_url, scorer_timeout, scorer_prompt, scorer_fragment_filter_score,
                                scorer_min_content, scorer_failure_limit, scorer_retry, scorer_output,
                                scorer_output_debug)
        oracle = OracleSettings(
            _text(values.get("oracle.url", DEFAULT_ORACLE_URL), "oracle.url", DEFAULT_ORACLE_URL, report),
            _number(values.get("oracle.timeout", DEFAULT_ORACLE_TIMEOUT), "oracle.timeout",
                    DEFAULT_ORACLE_TIMEOUT, 0.1, report),
            _text(values.get("oracle.prompt", ""), "oracle.prompt", "", report),
            _whole(values.get("oracle.max_tokens", DEFAULT_ORACLE_MAX_TOKENS), "oracle.max_tokens",
                   DEFAULT_ORACLE_MAX_TOKENS, 1, report),
            _number(values.get("oracle.temperature", DEFAULT_ORACLE_TEMPERATURE), "oracle.temperature",
                    DEFAULT_ORACLE_TEMPERATURE, 0.0, report))
        trigger = TriggerSettings(
            _percent(values.get("trigger.percentile", DEFAULT_TRIGGER_PERCENTILE),
                     "trigger.percentile", DEFAULT_TRIGGER_PERCENTILE, report),
            _positive(values.get("trigger.window_seconds", DEFAULT_TRIGGER_WINDOW_SECONDS),
                      "trigger.window_seconds", DEFAULT_TRIGGER_WINDOW_SECONDS, report),
            _whole(values.get("trigger.min_scores", DEFAULT_TRIGGER_MIN_SCORES),
                   "trigger.min_scores", DEFAULT_TRIGGER_MIN_SCORES, 2, report),
            _number(values.get("trigger.cooldown_seconds", DEFAULT_TRIGGER_COOLDOWN_SECONDS),
                    "trigger.cooldown_seconds", DEFAULT_TRIGGER_COOLDOWN_SECONDS, 0.0, report),
            _finite(values.get("trigger.starting_bar", DEFAULT_TRIGGER_STARTING_BAR), "trigger.starting_bar",
                    DEFAULT_TRIGGER_STARTING_BAR, report),
            _bool(values.get("trigger.exclude_triggers", False), "trigger.exclude_triggers", report),
            _positive(values.get("trigger.half_life_seconds", DEFAULT_TRIGGER_HALF_LIFE_SECONDS),
                      "trigger.half_life_seconds", DEFAULT_TRIGGER_HALF_LIFE_SECONDS, report),
            _number(values.get("trigger.average_fraction", DEFAULT_TRIGGER_AVERAGE_FRACTION),
                    "trigger.average_fraction", DEFAULT_TRIGGER_AVERAGE_FRACTION, 0.0, report),
            _positive(values.get("trigger.average_half_life_scores", DEFAULT_TRIGGER_AVERAGE_HALF_LIFE_SCORES),
                      "trigger.average_half_life_scores", DEFAULT_TRIGGER_AVERAGE_HALF_LIFE_SCORES, report),
            _below_one(values.get("trigger.average_floor_fraction", DEFAULT_TRIGGER_AVERAGE_FLOOR_FRACTION),
                       "trigger.average_floor_fraction", DEFAULT_TRIGGER_AVERAGE_FLOOR_FRACTION, report),
            _choice(values.get("trigger.algorithm", DEFAULT_TRIGGER_ALGORITHM), "trigger.algorithm",
                    DEFAULT_TRIGGER_ALGORITHM, TRIGGER_ALGORITHMS, report))
        whisper_url = _text(values.get("listening.whisper_url", DEFAULT_LISTENING_WHISPER_URL), "listening.whisper_url",
                            DEFAULT_LISTENING_WHISPER_URL, report)
        if "POM_WHISPER_URL" in environ:
            whisper_url = _text(environ["POM_WHISPER_URL"], "POM_WHISPER_URL", DEFAULT_LISTENING_WHISPER_URL, report)
        listening = ListeningSettings(
            whisper_url,
            _positive(values.get("listening.whisper_timeout", DEFAULT_LISTENING_WHISPER_TIMEOUT),
                      "listening.whisper_timeout", DEFAULT_LISTENING_WHISPER_TIMEOUT, report),
            _text(values.get("listening.device", DEFAULT_LISTENING_DEVICE), "listening.device",
                  DEFAULT_LISTENING_DEVICE, report),
            _positive(values.get("listening.buffer_seconds", DEFAULT_LISTENING_BUFFER_SECONDS),
                      "listening.buffer_seconds", DEFAULT_LISTENING_BUFFER_SECONDS, report),
            _positive(values.get("listening.step", DEFAULT_LISTENING_STEP), "listening.step",
                      DEFAULT_LISTENING_STEP, report),
            _number(values.get("listening.hold", DEFAULT_LISTENING_HOLD), "listening.hold",
                    DEFAULT_LISTENING_HOLD, 0.0, report),
            _number(values.get("listening.rewind", DEFAULT_LISTENING_REWIND), "listening.rewind",
                    DEFAULT_LISTENING_REWIND, 0.0, report),
            _positive(values.get("listening.max_buffer", DEFAULT_LISTENING_MAX_BUFFER), "listening.max_buffer",
                      DEFAULT_LISTENING_MAX_BUFFER, report),
            _whole(values.get("listening.sound_every_steps", DEFAULT_LISTENING_SOUND_EVERY_STEPS),
                   "listening.sound_every_steps", DEFAULT_LISTENING_SOUND_EVERY_STEPS, 0, report),
            _positive(values.get("listening.sound_seconds", DEFAULT_LISTENING_SOUND_SECONDS),
                      "listening.sound_seconds", DEFAULT_LISTENING_SOUND_SECONDS, report),
            _whole(values.get("listening.ambient_memory", DEFAULT_LISTENING_AMBIENT_MEMORY),
                   "listening.ambient_memory", DEFAULT_LISTENING_AMBIENT_MEMORY, 1, report),
            _bool(values.get("listening.consider_ambient_sounds", DEFAULT_LISTENING_CONSIDER_AMBIENT_SOUNDS),
                  "listening.consider_ambient_sounds", report))
        monitor = MonitorSettings(
            _bool(values.get("monitor.enabled", DEFAULT_MONITOR_ENABLED), "monitor.enabled", report),
            _text(values.get("monitor.host", DEFAULT_MONITOR_HOST), "monitor.host", DEFAULT_MONITOR_HOST, report),
            _port(values.get("monitor.port", DEFAULT_MONITOR_PORT), "monitor.port", DEFAULT_MONITOR_PORT, report),
            _text(values.get("monitor.password", ""), "monitor.password", "", report),
            _bool(values.get("monitor.show_hinges", DEFAULT_MONITOR_SHOW_HINGES), "monitor.show_hinges", report),
            _positive(values.get("monitor.history_seconds", DEFAULT_MONITOR_HISTORY_SECONDS), "monitor.history_seconds",
                      DEFAULT_MONITOR_HISTORY_SECONDS, report))
        return cls(strict, log_level, report, debug, local_logs, (low, high), scorer, trigger, oracle, listening, monitor)


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


def _positive(value, label: str, default: float, report: FaultRecorder) -> float:
    """A number above 0 (an int or float, not a boolean, and not infinity); otherwise the default."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 < value < float("inf"):
        return float(value)
    report.warn(f"{label} must be a number above 0, got {value!r}; using {default:g}")
    return default


def _percent(value, label: str, default: float, report: FaultRecorder) -> float:
    """A number above 0 and at most 100 (an int or float, not a boolean); otherwise the default."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 < value <= 100.0:
        return float(value)
    report.warn(f"{label} must be a number above 0 and at most 100, got {value!r}; using {default:g}")
    return default


def _finite(value, label: str, default: float, report: FaultRecorder) -> float:
    """Any finite number (an int or float, not a boolean); otherwise the default."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and float("-inf") < value < float("inf"):
        return float(value)
    report.warn(f"{label} must be a number, got {value!r}; using {default:g}")
    return default


def _below_one(value, label: str, default: float, report: FaultRecorder) -> float:
    """A number from 0 up to but not including 1 (an int or float, not a boolean); otherwise the default."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 <= value < 1.0:
        return float(value)
    report.warn(f"{label} must be a number from 0 up to but not including 1, got {value!r}; using {default:g}")
    return default


def _choice(value, label: str, default: str, choices, report: FaultRecorder) -> str:
    """One of the named choices (text, in the list); otherwise the default."""
    if isinstance(value, str) and value in choices:
        return value
    report.warn(f"{label} must be one of {', '.join(choices)}, got {value!r}; using {default}")
    return default


def _port(value, label: str, default: int, report: FaultRecorder) -> int:
    """A whole number from 1 to 65535 (not a boolean or a decimal); otherwise the default."""
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535:
        return value
    report.warn(f"{label} must be a whole number from 1 to 65535, got {value!r}; using {default}")
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
