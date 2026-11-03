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
               that defines "interesting" and the score scale, the clip_filter's score, how many content
               words a sentence needs to be sent to the model, and the circuit-breaker numbers. The
               entry point builds the ScorerModelClient and Scorer from `config.scorer`. Environment:
               POM_SCORER_URL.
    [oracle]   A section (see OracleSettings): what the oracle model is given and how it is reached. The
               `prompt` (with `{exemplars}` and `{hinge}` marking where those go), `exemplars_min` and
               `exemplars_max` (how many library passages go into each pull of exemplars, 3 and 5 by default), the
               model's address and timeout, and the two settings of each request, `max_tokens` and
               `temperature`. The entry point builds the PromptBuilder and the OracleModelClient from
               `config.oracle` and passes `config.exemplar_range` to the Bibliomancer, which never reads Config.
    [qualifying]  A section (see QualifyingSettings): when the Printer responds, when a score is above a bar worked out from the
               room's recent scores. `algorithm` picks how ("percentile", "decaying_quantile" or "moving_average"; each reads only
               its own settings), `starting_bar` is where the bar begins, `cooldown_seconds` how long after a qualifier the next
               may come (60; 0 for none), `typed_handicap` what a typed line's score is multiplied by (1.5) and
               `typed_lines_affect_bar` whether a typed line feeds the bar (false). The entry point builds the BarKeeper with `bar_keeper_from_settings(config.qualifying)`.
    [listening] A section (see ListeningSettings): how the Printer hears the room. The address and timeout of the
               whisper-server, the microphone's capture device and buffer, and the RollingCutter's `cut_every_seconds`, `hold_back_seconds`,
               `rewind_seconds` and `max_cut_seconds`, and the Eavesdropper's room-tone pass and room-tone memory. The defaults are the
               combination that worked in the room; change one at a time. The entry point builds the
               WhisperModelClient, MicDeck and Eavesdropper from `config.listening`.
    [monitor]  A section (see MonitorSettings): the monitoring site, a web page of the last hour of turns. Whether it
               runs, the address and port it listens on, its password (set it in config/pom.local.toml, never in the
               committed file), whether it shows the hinge text, and how long a turn stays on the page.
    [printing]   A section (see PrintingSettings): how a portent is set on the page. The paper's width, the margins and the
               print wheel's characters per inch give the line width; `quote_indent` indents a quotation and
               `linefeeds_after_portent` is how far the paper moves on after a portent, and
               `keep_composed_characters` and `ascii_printwheel` say which characters most wheels lack are typed as they are.
    [conductor] A section (see ConductorSettings): how a turn is run and how long each part may take (the watchdog's limits, how
               long a portent stays worth printing, how often to ask the oracle again). Every limit errs long on purpose, and
               the limits are checked against the settings they depend on (a warning, never a change).
    [terminal] A section (see TerminalSettings): the serial connection to the typewriter, which acts as a terminal. The port,
               baud rate, flow control, line ending, the pace of the typing, the wait at the end, and how the keyboard is handled
               (`echoplex`, `type_during_printing`). The entry point builds the
               TerminalPort from `config.terminal`. The entry point
               builds the PortentFormatter from `config.printing`.

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
         "scorer.prompt_output_debug", "scorer.clip_filter_score", "scorer.min_content_words", "scorer.failure_limit",
         "scorer.retry_seconds", "oracle.url", "oracle.timeout", "oracle.prompt", "oracle.max_tokens",
         "oracle.temperature", "oracle.exemplar_redraws", "qualifying.min_scores", "qualifying.window_seconds", "qualifying.percentile",
         "qualifying.cooldown_seconds", "qualifying.algorithm", "qualifying.starting_bar", "qualifying.exclude_qualifiers",
         "qualifying.half_life_seconds", "qualifying.average_fraction", "qualifying.average_half_life_scores",
         "qualifying.average_floor_fraction", "qualifying.typed_handicap", "qualifying.typed_lines_affect_bar",
         "listening.whisper_url", "listening.whisper_timeout", "listening.device",
         "listening.buffer_seconds", "listening.cut_every_seconds", "listening.hold_back_seconds", "listening.rewind_seconds", "listening.max_cut_seconds",
         "listening.room_tone_pass_every", "listening.room_tone_pass_seconds", "listening.room_tone_memory",
         "listening.score_room_tone", "monitor.enabled", "monitor.host", "monitor.port", "monitor.password",
         "monitor.show_hinges", "monitor.history_seconds", "printing.paper_width_inches", "printing.margin_inches",
         "printing.characters_per_inch", "printing.quote_indent", "printing.linefeeds_after_portent",
         "printing.keep_composed_characters", "printing.ascii_printwheel", "terminal.port", "terminal.baud",
         "terminal.flow_control", "terminal.end_of_line", "terminal.characters_per_second",
         "terminal.finish_wait_seconds", "terminal.echoplex", "terminal.type_during_printing",
         "terminal.stall_patience_seconds", "terminal.reconnect_seconds", "terminal.mode",
         "conductor.writing_seconds", "conductor.printing_seconds", "conductor.recovering_seconds", "conductor.freshness_seconds",
         "conductor.write_attempts", "conductor.write_pause_seconds", "conductor.write_budget_seconds",
         "conductor.print_pause_seconds", "conductor.restart_pause_seconds", "conductor.check_seconds"}
DEFAULT_EXEMPLAR_RANGE = (3, 5)
DEFAULT_SCORER_URL = "http://127.0.0.1:8098/v1/chat/completions"
DEFAULT_SCORER_TIMEOUT = 15.0
DEFAULT_SCORER_CLIP_FILTER_SCORE = 1.0
DEFAULT_SCORER_MIN_CONTENT_WORDS = 1
DEFAULT_SCORER_FAILURE_LIMIT = 3
DEFAULT_SCORER_RETRY_SECONDS = 30.0
DEFAULT_ORACLE_URL = "http://127.0.0.1:8099/v1/chat/completions"
DEFAULT_ORACLE_TIMEOUT = 240.0
DEFAULT_ORACLE_MAX_TOKENS = 300
DEFAULT_ORACLE_EXEMPLAR_REDRAWS = 50
DEFAULT_ORACLE_TEMPERATURE = 0.8
DEFAULT_QUALIFYING_PERCENTILE = 99.5
DEFAULT_QUALIFYING_WINDOW_SECONDS = 300.0
DEFAULT_QUALIFYING_MIN_SCORES = 20
DEFAULT_QUALIFYING_COOLDOWN_SECONDS = 60.0
DEFAULT_QUALIFYING_ALGORITHM = "percentile"
BAR_ALGORITHMS = ("percentile", "decaying_quantile", "moving_average")
DEFAULT_QUALIFYING_STARTING_BAR = 6.8
DEFAULT_QUALIFYING_HALF_LIFE_SECONDS = 300.0
DEFAULT_QUALIFYING_AVERAGE_FRACTION = 0.25
DEFAULT_QUALIFYING_AVERAGE_HALF_LIFE_SCORES = 10.0
DEFAULT_QUALIFYING_AVERAGE_FLOOR_FRACTION = 0.05
DEFAULT_QUALIFYING_TYPED_HANDICAP = 1.5
DEFAULT_LISTENING_WHISPER_URL = "http://127.0.0.1:8110"
DEFAULT_LISTENING_WHISPER_TIMEOUT = 180.0
DEFAULT_LISTENING_DEVICE = "plughw:0,0"
DEFAULT_LISTENING_BUFFER_SECONDS = 10.0
DEFAULT_LISTENING_CUT_EVERY_SECONDS = 5.0
DEFAULT_LISTENING_HOLD_BACK_SECONDS = 2.0
DEFAULT_LISTENING_REWIND_SECONDS = 0.0
DEFAULT_LISTENING_MAX_CUT_SECONDS = 30.0
DEFAULT_LISTENING_ROOM_TONE_PASS_EVERY = 3
DEFAULT_LISTENING_ROOM_TONE_PASS_SECONDS = 10.0
DEFAULT_LISTENING_ROOM_TONE_MEMORY = 6
DEFAULT_LISTENING_SCORE_ROOM_TONE = False
DEFAULT_MONITOR_ENABLED = False
DEFAULT_MONITOR_HOST = "0.0.0.0"
DEFAULT_MONITOR_PORT = 8080
DEFAULT_MONITOR_SHOW_HINGES = True
DEFAULT_MONITOR_HISTORY_SECONDS = 3600.0
DEFAULT_PRINTING_PAPER_WIDTH_INCHES = 8.5
DEFAULT_PRINTING_MARGIN_INCHES = 1.0
DEFAULT_PRINTING_CHARACTERS_PER_INCH = 10.0
DEFAULT_PRINTING_QUOTE_INDENT = 4
DEFAULT_PRINTING_LINEFEEDS_AFTER_PORTENT = 4
DEFAULT_PRINTING_KEEP_COMPOSED_CHARACTERS = ""
DEFAULT_PRINTING_ASCII_PRINTWHEEL = False
# Seven of the nine ASCII characters most print wheels lack (< and > are the others, and are always typed as they are); the
# CadetWriter builds them from periods ("period graphics"), and the PortentFormatter swaps each for a look-alike unless kept.
COMPOSED_CHARACTERS = "\\^`{|}~"
DEFAULT_TERMINAL_PORT = "/dev/ttyACM0"
DEFAULT_TERMINAL_BAUD = 9600
TERMINAL_BAUD_RATES = (50, 75, 110, 134, 150, 200, 300, 600, 1200, 1800, 2400, 4800, 9600, 19200, 38400, 57600,
                       76800, 115200, 230400)
DEFAULT_TERMINAL_FLOW_CONTROL = "xon_xoff"
TERMINAL_FLOW_CONTROLS = ("xon_xoff", "rts_cts", "none")
DEFAULT_TERMINAL_END_OF_LINE = "crlf"
TERMINAL_END_OF_LINES = ("cr", "crlf", "lf", "lfcr")
DEFAULT_TERMINAL_CHARACTERS_PER_SECOND = 16.0
DEFAULT_TERMINAL_FINISH_WAIT_SECONDS = 2.0
DEFAULT_TERMINAL_ECHOPLEX = True
DEFAULT_TERMINAL_TYPE_DURING_PRINTING = False
DEFAULT_TERMINAL_STALL_PATIENCE_SECONDS = 600.0
DEFAULT_TERMINAL_RECONNECT_SECONDS = 2.0
DEFAULT_TERMINAL_MODE = "serial"
TERMINAL_MODES = ("serial", "console", "none")
DEFAULT_CONDUCTOR_WRITING_SECONDS = 300.0
DEFAULT_CONDUCTOR_PRINTING_SECONDS = 180.0
DEFAULT_CONDUCTOR_RECOVERING_SECONDS = 3.0
DEFAULT_CONDUCTOR_FRESHNESS_SECONDS = 360.0
DEFAULT_CONDUCTOR_WRITE_ATTEMPTS = 3
DEFAULT_CONDUCTOR_WRITE_PAUSE_SECONDS = 10.0
DEFAULT_CONDUCTOR_WRITE_BUDGET_SECONDS = 270.0
DEFAULT_CONDUCTOR_PRINT_PAUSE_SECONDS = 10.0
DEFAULT_CONDUCTOR_RESTART_PAUSE_SECONDS = 5.0
DEFAULT_CONDUCTOR_CHECK_SECONDS = 1.0
MIN_PRINTING_LINE_WIDTH = 20
DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)


@dataclass(frozen=True)
class ScorerSettings:
    """The `[scorer]` section: how the Scorer reaches its language model and behaves around it.

    Attributes:
        url: The llama-server chat-completions address.
        timeout: Seconds to wait for one score.
        prompt_scoring: What "interesting" means and what the numbers mean, from the config file.
            Empty means none was set, and the Scorer then leaves every clip unscored.
        clip_filter_score: The score given to clips the clip_filter keeps from the model.
        min_content_words: Clips with fewer content words skip the model (0 = never skip).
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
    clip_filter_score: float = DEFAULT_SCORER_CLIP_FILTER_SCORE
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
        exemplar_redraws: How many times one pick of an exemplar that stops before its thought is done (it ends on a colon or on a
            word like "the") is drawn again before it is used anyway; 0 turns the redraw off.
    """
    url: str = DEFAULT_ORACLE_URL
    timeout: float = DEFAULT_ORACLE_TIMEOUT
    prompt: str = ""
    max_tokens: int = DEFAULT_ORACLE_MAX_TOKENS
    temperature: float = DEFAULT_ORACLE_TEMPERATURE
    exemplar_redraws: int = DEFAULT_ORACLE_EXEMPLAR_REDRAWS


@dataclass(frozen=True)
class QualifyingSettings:
    """The `[qualifying]` section: when the Printer responds.

    `algorithm` picks how the bar is worked out; each algorithm reads only its own settings, so one section can hold them all and
    switching is one line.

    Attributes:
        percentile: How much of the room's recent scores a score must beat to qualify, above 0 and at most 100. 100 means beating
            the best score; 99.5 beating all but about one in two hundred; 90 being in the top tenth. (percentile and decaying_quantile)
        window_seconds: How many seconds of recent scores the bar is drawn from, above 0 (300 is the last five minutes). Measured in
            time, not in scores, so it means the same in a busy room and a quiet one. (percentile)
        min_scores: The window always holds at least this many scores, and until this many have been heard the bar is the
            starting bar, a whole number of at least 2. (percentile)
        cooldown_seconds: How many seconds after a qualifier before another score may qualify, 0 or more (0 is no cooldown). (all)
        starting_bar: The bar before the room has said enough to work one out, and where it begins; it fades as the room speaks.
            There is no silent warm-up. (all)
        exclude_qualifiers: True leaves a score that qualified out of what the bar is built from, so one zinger does not hold the bar up
            and shut out the next. (percentile and decaying_quantile)
        half_life_seconds: Seconds for a score to count half as much, above 0. (decaying_quantile)
        average_fraction: How far above the decaying average the bar sits, 0 or more (0.25 is 25% above). (moving_average)
        average_half_life_scores: How many more scores it takes for a score's influence on the average to halve, above 0.
            (moving_average)
        average_floor_fraction: How far up the 0 to 9 scale the floor sits; scores below it are ignored. From 0 up to but not
            including 1. (moving_average)
        algorithm: "percentile", "decaying_quantile" or "moving_average".
        typed_handicap: What a typed line's score is multiplied by before it meets the bar, 0 or more (1 is no bonus). A typed
            line is someone speaking to the Printer directly, so its score is multiplied; the product is not capped at 9. (all)
        typed_lines_affect_bar: True lets a typed line's score affect the bar, feeding what it is built from like the room's own scores. False keeps
            the bar a picture of the room alone. A typed line never has a cooldown either way. (all)
    """
    percentile: float = DEFAULT_QUALIFYING_PERCENTILE
    window_seconds: float = DEFAULT_QUALIFYING_WINDOW_SECONDS
    min_scores: int = DEFAULT_QUALIFYING_MIN_SCORES
    cooldown_seconds: float = DEFAULT_QUALIFYING_COOLDOWN_SECONDS
    starting_bar: float = DEFAULT_QUALIFYING_STARTING_BAR
    exclude_qualifiers: bool = False
    half_life_seconds: float = DEFAULT_QUALIFYING_HALF_LIFE_SECONDS
    average_fraction: float = DEFAULT_QUALIFYING_AVERAGE_FRACTION
    average_half_life_scores: float = DEFAULT_QUALIFYING_AVERAGE_HALF_LIFE_SCORES
    average_floor_fraction: float = DEFAULT_QUALIFYING_AVERAGE_FLOOR_FRACTION
    algorithm: str = DEFAULT_QUALIFYING_ALGORITHM
    typed_handicap: float = DEFAULT_QUALIFYING_TYPED_HANDICAP
    typed_lines_affect_bar: bool = False


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
        cut_every_seconds: Seconds of new audio between whisper runs, above 0.
        hold_back_seconds: A segment is kept once it ends this many seconds before the end of the buffer, 0 or more.
        rewind_seconds: Seconds before the last kept segment's end that the next run starts, 0 or more (0 is off).
        max_cut_seconds: Seconds without a finished segment before one is forced out, above 0.
        room_tone_pass_every: After this many runs in a row with no speech, listen once for lyrics and room tone;
            a whole number of at least 0, 0 for never.
        room_tone_pass_seconds: How much recent audio that pass listens to, above 0.
        room_tone_memory: How many recent bits of room tone to remember, a whole number of at least 1.
        score_room_tone: True lets room tone like "(dog barks)" be scored and become hinges, as
            speech does. False (the default) leaves them unscored.
    """
    whisper_url: str = DEFAULT_LISTENING_WHISPER_URL
    whisper_timeout: float = DEFAULT_LISTENING_WHISPER_TIMEOUT
    device: str = DEFAULT_LISTENING_DEVICE
    buffer_seconds: float = DEFAULT_LISTENING_BUFFER_SECONDS
    cut_every_seconds: float = DEFAULT_LISTENING_CUT_EVERY_SECONDS
    hold_back_seconds: float = DEFAULT_LISTENING_HOLD_BACK_SECONDS
    rewind_seconds: float = DEFAULT_LISTENING_REWIND_SECONDS
    max_cut_seconds: float = DEFAULT_LISTENING_MAX_CUT_SECONDS
    room_tone_pass_every: int = DEFAULT_LISTENING_ROOM_TONE_PASS_EVERY
    room_tone_pass_seconds: float = DEFAULT_LISTENING_ROOM_TONE_PASS_SECONDS
    room_tone_memory: int = DEFAULT_LISTENING_ROOM_TONE_MEMORY
    score_room_tone: bool = DEFAULT_LISTENING_SCORE_ROOM_TONE


@dataclass(frozen=True)
class MonitorSettings:
    """The `[monitor]` section: the monitoring site, a web page of the last hour of turns.

    Attributes:
        enabled: True starts the site with the Printer.
        host: The address it listens on; 0.0.0.0 means every network interface, so anyone on the local network can reach it.
        port: The port it listens on, 1 to 65535.
        password: The site's password. The site does not start while it is empty; set it in config/pom.local.toml,
            which git ignores, never in pom.toml.
        show_hinges: True shows the hinge (what was said in the room) on the page; False shows numbers only.
        history_seconds: How long a turn stays on the page, above 0 (3600 is an hour). Kept in memory only.
    """
    enabled: bool = DEFAULT_MONITOR_ENABLED
    host: str = DEFAULT_MONITOR_HOST
    port: int = DEFAULT_MONITOR_PORT
    password: str = ""
    show_hinges: bool = DEFAULT_MONITOR_SHOW_HINGES
    history_seconds: float = DEFAULT_MONITOR_HISTORY_SECONDS


@dataclass(frozen=True)
class PrintingSettings:
    """The `[printing]` section: how a portent is set on the page.

    Attributes:
        paper_width_inches: The paper's width, above 0 (8.5 for US letter).
        margin_inches: The margin on each side, 0 or more. The margins plus a usable width of at least 20 characters must fit the paper.
        characters_per_inch: The print wheel's pitch, above 0: 10, 12 or 15 on the Wheelwriter (10 is the widest letters,
            so the shortest line, and fits any wheel; the machine's pitch switch must match the wheel).
        quote_indent: How many spaces a quotation is indented, 0 or more.
        linefeeds_after_portent: How many line feeds are typed after a portent, 0 or more: the last line's own, then blank lines.
        keep_composed_characters: Which of \\ ^ ` { | } ~ (characters most wheels lack, which the CadetWriter builds from periods:
            slow and dotty) are typed as they are; the rest are swapped for a look-alike. Empty by default. < and > are lacked
            too but are always typed as they are, so they are not listed.
        ascii_printwheel: True if the wheel is a rare ASCII one with all nine; then every one is typed as it is.
    """
    paper_width_inches: float = DEFAULT_PRINTING_PAPER_WIDTH_INCHES
    margin_inches: float = DEFAULT_PRINTING_MARGIN_INCHES
    characters_per_inch: float = DEFAULT_PRINTING_CHARACTERS_PER_INCH
    quote_indent: int = DEFAULT_PRINTING_QUOTE_INDENT
    linefeeds_after_portent: int = DEFAULT_PRINTING_LINEFEEDS_AFTER_PORTENT
    keep_composed_characters: str = DEFAULT_PRINTING_KEEP_COMPOSED_CHARACTERS
    ascii_printwheel: bool = DEFAULT_PRINTING_ASCII_PRINTWHEEL

    @property
    def line_width(self) -> int:
        """How many characters fit on a line: the width between the margins times the pitch, rounded down."""
        return int((self.paper_width_inches - 2 * self.margin_inches) * self.characters_per_inch + 1e-9)

    @property
    def composed_characters_kept(self) -> str:
        """The composed characters to type as they are: all seven with an ASCII print wheel, else the ones listed."""
        return COMPOSED_CHARACTERS if self.ascii_printwheel else self.keep_composed_characters


@dataclass(frozen=True)
class TerminalSettings:
    """The `[terminal]` section: the serial connection to the typewriter, which acts as a terminal.

    Attributes:
        port: The serial device (/dev/ttyACM0 for the CadetWriter's USB port).
        baud: The speed, one of TERMINAL_BAUD_RATES. Only the CadetWriter's RS-232 port uses it; USB ignores it.
        flow_control: "xon_xoff", "rts_cts" or "none". Match the CadetWriter's setting; without it a flood loses text.
        end_of_line: What a newline is sent as: "cr", "crlf", "lf" or "lfcr". Must match the CadetWriter's "receive
            end-of-line" setting (its default is crlf); a bare LF only feeds the paper.
        characters_per_second: The fastest the text is sent, above 0 (16 is a little under the Wheelwriter's speed).
        finish_wait_seconds: How long to wait after the last character for the typewriter to finish, 0 or more.
        echoplex: True to echo each key typed on the typewriter back to it (the old terminal name for the host echoing what it
            receives). The CadetWriter must then be in full duplex (its default); with False it must be in half duplex, which
            echoes locally. A mismatch types every character twice, or none.
        type_during_printing: False (the default) throws away keys pressed while a portent is being typed, so nothing typed over
            it appears after it; True keeps them for the next line read.
        stall_patience_seconds: Once a portent is being typed, how long to wait for a typewriter that stopped taking characters
            (held, out of paper) or went away (unplugged), above 0. It goes on where it stopped when it comes back.
        reconnect_seconds: How often a lost port is tried again, above 0.
        mode: "serial" (the real typewriter, the default), "console" (types on the screen at the same pace, for development) or
            "none" (no printer: a turn is write, record, done, so the Printer can be watched with nothing attached).
    """
    port: str = DEFAULT_TERMINAL_PORT
    baud: int = DEFAULT_TERMINAL_BAUD
    flow_control: str = DEFAULT_TERMINAL_FLOW_CONTROL
    end_of_line: str = DEFAULT_TERMINAL_END_OF_LINE
    characters_per_second: float = DEFAULT_TERMINAL_CHARACTERS_PER_SECOND
    finish_wait_seconds: float = DEFAULT_TERMINAL_FINISH_WAIT_SECONDS
    echoplex: bool = DEFAULT_TERMINAL_ECHOPLEX
    type_during_printing: bool = DEFAULT_TERMINAL_TYPE_DURING_PRINTING
    stall_patience_seconds: float = DEFAULT_TERMINAL_STALL_PATIENCE_SECONDS
    reconnect_seconds: float = DEFAULT_TERMINAL_RECONNECT_SECONDS
    mode: str = DEFAULT_TERMINAL_MODE


@dataclass(frozen=True)
class ConductorSettings:
    """The `[conductor]` section: how a turn is run, and how long each part of it may take. Every limit errs long on purpose.

    Attributes:
        writing_seconds: The watchdog's limit for the oracle model to write one portent. Must be longer than `oracle.timeout`.
        printing_seconds: The watchdog's limit for formatting and typing one portent, waiting for the printer included (the wait sends a
            heartbeat, which restarts this clock). Must cover the longest portent at the terminal's pace.
        recovering_seconds: How long the ears stay shut after a turn, for the terminal's last sounds to die away. 0 or more.
        freshness_seconds: How long after a hinge its portent is still worth printing; a portent that waits longer for the printer is
            dropped (the room has moved on).
        write_attempts: The most times to ask the oracle for a portent in one turn, a whole number of at least 1.
        write_pause_seconds: The wait between two attempts to write. 0 or more.
        write_budget_seconds: The time all the writing in a turn may take; another attempt is made only if a whole one still fits.
            Keep it under `writing_seconds`.
        print_pause_seconds: The wait between two tries at a terminal that cannot print yet.
        restart_pause_seconds: The wait before the room (a microphone that stopped) or the keyboard is started again. 0 or more.
        check_seconds: How often the Conductor looks at the floor to open the ears again and at its threads.
    """
    writing_seconds: float = DEFAULT_CONDUCTOR_WRITING_SECONDS
    printing_seconds: float = DEFAULT_CONDUCTOR_PRINTING_SECONDS
    recovering_seconds: float = DEFAULT_CONDUCTOR_RECOVERING_SECONDS
    freshness_seconds: float = DEFAULT_CONDUCTOR_FRESHNESS_SECONDS
    write_attempts: int = DEFAULT_CONDUCTOR_WRITE_ATTEMPTS
    write_pause_seconds: float = DEFAULT_CONDUCTOR_WRITE_PAUSE_SECONDS
    write_budget_seconds: float = DEFAULT_CONDUCTOR_WRITE_BUDGET_SECONDS
    print_pause_seconds: float = DEFAULT_CONDUCTOR_PRINT_PAUSE_SECONDS
    restart_pause_seconds: float = DEFAULT_CONDUCTOR_RESTART_PAUSE_SECONDS
    check_seconds: float = DEFAULT_CONDUCTOR_CHECK_SECONDS


class Config:
    """The project's shared settings, plus a report of anything that had to be recovered from.

    Attributes:
        strict: Raise on every recoverable problem (development and tests only).
        debug: Show DEBUG output from every module (a module may override it).
        log_level: A level name: DEBUG, INFO, WARNING, ERROR or CRITICAL.
        local_logs: Also write the log to a local file (development only).
        exemplar_range: (fewest, most) passages per pull of exemplars, for the Bibliomancer.
        scorer: The `[scorer]` section, as a ScorerSettings.
        oracle: The `[oracle]` section, as an OracleSettings (the exemplar range is `exemplar_range`).
        qualifying: The `[qualifying]` section, as a QualifyingSettings.
        listening: The `[listening]` section, as a ListeningSettings.
        monitor: The `[monitor]` section, as a MonitorSettings.
        printing: The `[printing]` section, as a PrintingSettings.
        terminal: The `[terminal]` section, as a TerminalSettings.
        conductor: The `[conductor]` section, as a ConductorSettings.
        report: Problems found while loading (bad file, unknown key, wrong type).
    """

    def __init__(self, strict: bool = False, log_level: str = "INFO",
                 report: Optional[FaultRecorder] = None, debug: bool = False, local_logs: bool = False,
                 exemplar_range: tuple = DEFAULT_EXEMPLAR_RANGE, scorer: Optional[ScorerSettings] = None,
                 qualifying: Optional[QualifyingSettings] = None, oracle: Optional[OracleSettings] = None,
                 listening: Optional[ListeningSettings] = None, monitor: Optional[MonitorSettings] = None,
                 printing: Optional[PrintingSettings] = None, terminal: Optional[TerminalSettings] = None,
                 conductor: Optional[ConductorSettings] = None):
        """Use `Config.load`; this just holds already-validated values."""
        self.scorer = scorer if scorer is not None else ScorerSettings()
        self.oracle = oracle if oracle is not None else OracleSettings()
        self.qualifying = qualifying if qualifying is not None else QualifyingSettings()
        self.listening = listening if listening is not None else ListeningSettings()
        self.monitor = monitor if monitor is not None else MonitorSettings()
        self.printing = printing if printing is not None else PrintingSettings()
        self.terminal = terminal if terminal is not None else TerminalSettings()
        self.conductor = conductor if conductor is not None else ConductorSettings()
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
        scorer_clip_filter_score = _number(
            values.get("scorer.clip_filter_score", DEFAULT_SCORER_CLIP_FILTER_SCORE),
            "scorer.clip_filter_score", DEFAULT_SCORER_CLIP_FILTER_SCORE, 0.0, report)
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
        scorer = ScorerSettings(scorer_url, scorer_timeout, scorer_prompt, scorer_clip_filter_score,
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
                    DEFAULT_ORACLE_TEMPERATURE, 0.0, report),
            _whole(values.get("oracle.exemplar_redraws", DEFAULT_ORACLE_EXEMPLAR_REDRAWS), "oracle.exemplar_redraws",
                   DEFAULT_ORACLE_EXEMPLAR_REDRAWS, 0, report))
        qualifying = QualifyingSettings(
            _percent(values.get("qualifying.percentile", DEFAULT_QUALIFYING_PERCENTILE),
                     "qualifying.percentile", DEFAULT_QUALIFYING_PERCENTILE, report),
            _positive(values.get("qualifying.window_seconds", DEFAULT_QUALIFYING_WINDOW_SECONDS),
                      "qualifying.window_seconds", DEFAULT_QUALIFYING_WINDOW_SECONDS, report),
            _whole(values.get("qualifying.min_scores", DEFAULT_QUALIFYING_MIN_SCORES),
                   "qualifying.min_scores", DEFAULT_QUALIFYING_MIN_SCORES, 2, report),
            _number(values.get("qualifying.cooldown_seconds", DEFAULT_QUALIFYING_COOLDOWN_SECONDS),
                    "qualifying.cooldown_seconds", DEFAULT_QUALIFYING_COOLDOWN_SECONDS, 0.0, report),
            _finite(values.get("qualifying.starting_bar", DEFAULT_QUALIFYING_STARTING_BAR), "qualifying.starting_bar",
                    DEFAULT_QUALIFYING_STARTING_BAR, report),
            _bool(values.get("qualifying.exclude_qualifiers", False), "qualifying.exclude_qualifiers", report),
            _positive(values.get("qualifying.half_life_seconds", DEFAULT_QUALIFYING_HALF_LIFE_SECONDS),
                      "qualifying.half_life_seconds", DEFAULT_QUALIFYING_HALF_LIFE_SECONDS, report),
            _number(values.get("qualifying.average_fraction", DEFAULT_QUALIFYING_AVERAGE_FRACTION),
                    "qualifying.average_fraction", DEFAULT_QUALIFYING_AVERAGE_FRACTION, 0.0, report),
            _positive(values.get("qualifying.average_half_life_scores", DEFAULT_QUALIFYING_AVERAGE_HALF_LIFE_SCORES),
                      "qualifying.average_half_life_scores", DEFAULT_QUALIFYING_AVERAGE_HALF_LIFE_SCORES, report),
            _below_one(values.get("qualifying.average_floor_fraction", DEFAULT_QUALIFYING_AVERAGE_FLOOR_FRACTION),
                       "qualifying.average_floor_fraction", DEFAULT_QUALIFYING_AVERAGE_FLOOR_FRACTION, report),
            _choice(values.get("qualifying.algorithm", DEFAULT_QUALIFYING_ALGORITHM), "qualifying.algorithm",
                    DEFAULT_QUALIFYING_ALGORITHM, BAR_ALGORITHMS, report),
            _number(values.get("qualifying.typed_handicap", DEFAULT_QUALIFYING_TYPED_HANDICAP),
                    "qualifying.typed_handicap", DEFAULT_QUALIFYING_TYPED_HANDICAP, 0.0, report),
            _bool(values.get("qualifying.typed_lines_affect_bar", False), "qualifying.typed_lines_affect_bar", report))
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
            _positive(values.get("listening.cut_every_seconds", DEFAULT_LISTENING_CUT_EVERY_SECONDS), "listening.cut_every_seconds",
                      DEFAULT_LISTENING_CUT_EVERY_SECONDS, report),
            _number(values.get("listening.hold_back_seconds", DEFAULT_LISTENING_HOLD_BACK_SECONDS), "listening.hold_back_seconds",
                    DEFAULT_LISTENING_HOLD_BACK_SECONDS, 0.0, report),
            _number(values.get("listening.rewind_seconds", DEFAULT_LISTENING_REWIND_SECONDS), "listening.rewind_seconds",
                    DEFAULT_LISTENING_REWIND_SECONDS, 0.0, report),
            _positive(values.get("listening.max_cut_seconds", DEFAULT_LISTENING_MAX_CUT_SECONDS), "listening.max_cut_seconds",
                      DEFAULT_LISTENING_MAX_CUT_SECONDS, report),
            _whole(values.get("listening.room_tone_pass_every", DEFAULT_LISTENING_ROOM_TONE_PASS_EVERY),
                   "listening.room_tone_pass_every", DEFAULT_LISTENING_ROOM_TONE_PASS_EVERY, 0, report),
            _positive(values.get("listening.room_tone_pass_seconds", DEFAULT_LISTENING_ROOM_TONE_PASS_SECONDS),
                      "listening.room_tone_pass_seconds", DEFAULT_LISTENING_ROOM_TONE_PASS_SECONDS, report),
            _whole(values.get("listening.room_tone_memory", DEFAULT_LISTENING_ROOM_TONE_MEMORY),
                   "listening.room_tone_memory", DEFAULT_LISTENING_ROOM_TONE_MEMORY, 1, report),
            _bool(values.get("listening.score_room_tone", DEFAULT_LISTENING_SCORE_ROOM_TONE),
                  "listening.score_room_tone", report))
        monitor = MonitorSettings(
            _bool(values.get("monitor.enabled", DEFAULT_MONITOR_ENABLED), "monitor.enabled", report),
            _text(values.get("monitor.host", DEFAULT_MONITOR_HOST), "monitor.host", DEFAULT_MONITOR_HOST, report),
            _port(values.get("monitor.port", DEFAULT_MONITOR_PORT), "monitor.port", DEFAULT_MONITOR_PORT, report),
            _text(values.get("monitor.password", ""), "monitor.password", "", report),
            _bool(values.get("monitor.show_hinges", DEFAULT_MONITOR_SHOW_HINGES), "monitor.show_hinges", report),
            _positive(values.get("monitor.history_seconds", DEFAULT_MONITOR_HISTORY_SECONDS), "monitor.history_seconds",
                      DEFAULT_MONITOR_HISTORY_SECONDS, report))
        printing = PrintingSettings(
            _positive(values.get("printing.paper_width_inches", DEFAULT_PRINTING_PAPER_WIDTH_INCHES),
                      "printing.paper_width_inches", DEFAULT_PRINTING_PAPER_WIDTH_INCHES, report),
            _number(values.get("printing.margin_inches", DEFAULT_PRINTING_MARGIN_INCHES), "printing.margin_inches",
                    DEFAULT_PRINTING_MARGIN_INCHES, 0.0, report),
            _positive(values.get("printing.characters_per_inch", DEFAULT_PRINTING_CHARACTERS_PER_INCH),
                      "printing.characters_per_inch", DEFAULT_PRINTING_CHARACTERS_PER_INCH, report),
            _whole(values.get("printing.quote_indent", DEFAULT_PRINTING_QUOTE_INDENT), "printing.quote_indent",
                   DEFAULT_PRINTING_QUOTE_INDENT, 0, report),
            _whole(values.get("printing.linefeeds_after_portent", DEFAULT_PRINTING_LINEFEEDS_AFTER_PORTENT),
                   "printing.linefeeds_after_portent", DEFAULT_PRINTING_LINEFEEDS_AFTER_PORTENT, 0, report),
            _composed(values.get("printing.keep_composed_characters", DEFAULT_PRINTING_KEEP_COMPOSED_CHARACTERS),
                      "printing.keep_composed_characters", DEFAULT_PRINTING_KEEP_COMPOSED_CHARACTERS, report),
            _bool(values.get("printing.ascii_printwheel", DEFAULT_PRINTING_ASCII_PRINTWHEEL), "printing.ascii_printwheel", report))
        if printing.line_width < MIN_PRINTING_LINE_WIDTH:
            report.warn(f"printing: the paper, margins and pitch leave a line of {printing.line_width} characters, under "
                        f"{MIN_PRINTING_LINE_WIDTH}; using the default paper, margins and pitch")
            printing = PrintingSettings(quote_indent=printing.quote_indent,
                                    linefeeds_after_portent=printing.linefeeds_after_portent,
                                    keep_composed_characters=printing.keep_composed_characters,
                                    ascii_printwheel=printing.ascii_printwheel)
        terminal = TerminalSettings(
            _text(values.get("terminal.port", DEFAULT_TERMINAL_PORT), "terminal.port", DEFAULT_TERMINAL_PORT, report),
            _baud(values.get("terminal.baud", DEFAULT_TERMINAL_BAUD), "terminal.baud", DEFAULT_TERMINAL_BAUD, report),
            _choice(values.get("terminal.flow_control", DEFAULT_TERMINAL_FLOW_CONTROL), "terminal.flow_control",
                    DEFAULT_TERMINAL_FLOW_CONTROL, TERMINAL_FLOW_CONTROLS, report),
            _choice(values.get("terminal.end_of_line", DEFAULT_TERMINAL_END_OF_LINE), "terminal.end_of_line",
                    DEFAULT_TERMINAL_END_OF_LINE, TERMINAL_END_OF_LINES, report),
            _positive(values.get("terminal.characters_per_second", DEFAULT_TERMINAL_CHARACTERS_PER_SECOND),
                      "terminal.characters_per_second", DEFAULT_TERMINAL_CHARACTERS_PER_SECOND, report),
            _number(values.get("terminal.finish_wait_seconds", DEFAULT_TERMINAL_FINISH_WAIT_SECONDS),
                    "terminal.finish_wait_seconds", DEFAULT_TERMINAL_FINISH_WAIT_SECONDS, 0.0, report),
            _bool(values.get("terminal.echoplex", DEFAULT_TERMINAL_ECHOPLEX), "terminal.echoplex", report,
                  DEFAULT_TERMINAL_ECHOPLEX),
            _bool(values.get("terminal.type_during_printing", DEFAULT_TERMINAL_TYPE_DURING_PRINTING),
                  "terminal.type_during_printing", report, DEFAULT_TERMINAL_TYPE_DURING_PRINTING),
            _positive(values.get("terminal.stall_patience_seconds", DEFAULT_TERMINAL_STALL_PATIENCE_SECONDS),
                      "terminal.stall_patience_seconds", DEFAULT_TERMINAL_STALL_PATIENCE_SECONDS, report),
            _positive(values.get("terminal.reconnect_seconds", DEFAULT_TERMINAL_RECONNECT_SECONDS),
                      "terminal.reconnect_seconds", DEFAULT_TERMINAL_RECONNECT_SECONDS, report),
            _choice(values.get("terminal.mode", DEFAULT_TERMINAL_MODE), "terminal.mode", DEFAULT_TERMINAL_MODE, TERMINAL_MODES, report))
        conductor = ConductorSettings(
            _positive(values.get("conductor.writing_seconds", DEFAULT_CONDUCTOR_WRITING_SECONDS), "conductor.writing_seconds",
                      DEFAULT_CONDUCTOR_WRITING_SECONDS, report),
            _positive(values.get("conductor.printing_seconds", DEFAULT_CONDUCTOR_PRINTING_SECONDS), "conductor.printing_seconds",
                      DEFAULT_CONDUCTOR_PRINTING_SECONDS, report),
            _number(values.get("conductor.recovering_seconds", DEFAULT_CONDUCTOR_RECOVERING_SECONDS), "conductor.recovering_seconds",
                    DEFAULT_CONDUCTOR_RECOVERING_SECONDS, 0.0, report),
            _positive(values.get("conductor.freshness_seconds", DEFAULT_CONDUCTOR_FRESHNESS_SECONDS), "conductor.freshness_seconds",
                      DEFAULT_CONDUCTOR_FRESHNESS_SECONDS, report),
            _whole(values.get("conductor.write_attempts", DEFAULT_CONDUCTOR_WRITE_ATTEMPTS), "conductor.write_attempts",
                   DEFAULT_CONDUCTOR_WRITE_ATTEMPTS, 1, report),
            _number(values.get("conductor.write_pause_seconds", DEFAULT_CONDUCTOR_WRITE_PAUSE_SECONDS), "conductor.write_pause_seconds",
                    DEFAULT_CONDUCTOR_WRITE_PAUSE_SECONDS, 0.0, report),
            _positive(values.get("conductor.write_budget_seconds", DEFAULT_CONDUCTOR_WRITE_BUDGET_SECONDS),
                      "conductor.write_budget_seconds", DEFAULT_CONDUCTOR_WRITE_BUDGET_SECONDS, report),
            _positive(values.get("conductor.print_pause_seconds", DEFAULT_CONDUCTOR_PRINT_PAUSE_SECONDS), "conductor.print_pause_seconds",
                      DEFAULT_CONDUCTOR_PRINT_PAUSE_SECONDS, report),
            _number(values.get("conductor.restart_pause_seconds", DEFAULT_CONDUCTOR_RESTART_PAUSE_SECONDS),
                    "conductor.restart_pause_seconds", DEFAULT_CONDUCTOR_RESTART_PAUSE_SECONDS, 0.0, report),
            _positive(values.get("conductor.check_seconds", DEFAULT_CONDUCTOR_CHECK_SECONDS), "conductor.check_seconds",
                      DEFAULT_CONDUCTOR_CHECK_SECONDS, report))
        _check_the_limits_fit(conductor, oracle, terminal, report)
        return cls(strict, log_level, report, debug, local_logs, (low, high), scorer, qualifying, oracle, listening, monitor,
                   printing, terminal, conductor)


# A portent is at most this many characters per token (4 is typical English; 5 leaves room), for the printing time worked out below.
_CHARACTERS_PER_TOKEN = 5


def _check_the_limits_fit(conductor: "ConductorSettings", oracle: "OracleSettings", terminal: "TerminalSettings", report: FaultRecorder) -> None:
    """Warns when the Conductor's limits do not fit the settings they depend on. The values are kept: this only tells the person.

    A watchdog shorter than the thing it watches cuts off healthy turns, which is worse than a limit that is too long, so each warning
    names the limit that is too short.
    """
    if conductor.writing_seconds <= oracle.timeout:
        report.warn(f"conductor.writing_seconds ({conductor.writing_seconds:g}) should be longer than oracle.timeout ({oracle.timeout:g}), "
                    f"or the watchdog can cut off a turn the oracle is still allowed to finish")
    if conductor.write_budget_seconds >= conductor.writing_seconds:
        report.warn(f"conductor.write_budget_seconds ({conductor.write_budget_seconds:g}) should be shorter than "
                    f"conductor.writing_seconds ({conductor.writing_seconds:g}), or a retry can run into the watchdog")
    if conductor.write_budget_seconds < oracle.timeout + conductor.write_pause_seconds:
        report.warn(f"conductor.write_budget_seconds ({conductor.write_budget_seconds:g}) is shorter than oracle.timeout "
                    f"({oracle.timeout:g}) plus conductor.write_pause_seconds ({conductor.write_pause_seconds:g}), so no second attempt to "
                    f"write can ever fit")
    longest_print = oracle.max_tokens * _CHARACTERS_PER_TOKEN / terminal.characters_per_second
    if terminal.mode != "none" and conductor.printing_seconds < longest_print:
        report.warn(f"conductor.printing_seconds ({conductor.printing_seconds:g}) is shorter than the time to type the longest portent "
                    f"({longest_print:.0f} s for {oracle.max_tokens} tokens at {terminal.characters_per_second:g} characters a second)")


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


def _bool(value, label: str, report: FaultRecorder, default: bool = False) -> bool:
    """A boolean from a TOML bool or an on/off style string; otherwise `default` (False unless given) with a warning."""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    report.warn(f"{label} must be true or false, got {value!r}; using {'true' if default else 'false'}")
    return default


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


def _composed(value, label: str, default: str, report: FaultRecorder) -> str:
    """Text made only of the composed characters (COMPOSED_CHARACTERS); any other character is a mistake and is left out."""
    if not isinstance(value, str):
        report.warn(f"{label} must be text, got {value!r}; using the default")
        return default
    kept = "".join(dict.fromkeys(c for c in value if c in COMPOSED_CHARACTERS))
    if kept != value:
        report.warn(f"{label} may hold only the characters {COMPOSED_CHARACTERS}, got {value!r}; keeping {kept!r}")
    return kept


def _baud(value, label: str, default: int, report: FaultRecorder) -> int:
    """One of the standard baud rates (a whole number; True is 1 and no baud rate); otherwise the default."""
    if isinstance(value, int) and value in TERMINAL_BAUD_RATES:
        return value
    report.warn(f"{label} must be a standard baud rate (such as 9600 or 115200), got {value!r}; using {default}")
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
