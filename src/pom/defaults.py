"""The defaults of every setting: the shadow of config/pom.toml. Not a user-serviceable part; settings are changed in pom.toml.

Every setting the Printer has is declared here exactly once: its name, its type, its default, and the rule its value must follow
(`setting(default, rule)`, see setting_rules.py). The classes mirror pom.toml: `ProjectSettings` is the top-level keys, and each
`[section]` is one frozen settings class (`[conductor]` is ConductorSettings), listed in SECTIONS by its name in the file.

config.py reads pom.toml (and pom.local.toml) over these: a good value replaces the default, and a bad one, an unknown key, or (soon) a
missing key is a warning, never a stop. The entry point hands each class the section objects it reads.

Design decisions, and why:
  * One place for every default. A default written anywhere else is a second copy, and copies drift (a comment once said the oracle's
    timeout was 120 s when it was 240).
  * The classes follow pom.toml's sections, not the code's packages: a section may be read by several packages, and one package may read
    several sections. Nothing assumes they line up.
  * The field's name is the setting's name, and `ConductorSettings().freshness_seconds` is its default: there is no second name for a
    default.
  * What each setting does, and what changes if it goes up or down, is said in pom.toml, where people read it; the notes here are for code.
"""
from dataclasses import dataclass

from pom.setting_rules import (above_zero, any_number, at_least, below_one, count, flag, list_of_numbers, list_of_text, log_level,
                               nonblank_text, not_negative, one_of, only_characters, percent, port_number, setting, text, whole_at_least,
                               web_address, whole_one_of)

BAR_ALGORITHMS = ("percentile", "decaying_quantile", "moving_average")
# Seven of the nine ASCII characters most print wheels lack (< and > are the others, and are always typed as they are); some terminals build
# them from periods ("period graphics"), and the PortentFormatter swaps each for a look-alike unless kept.
COMPOSED_CHARACTERS = "\\^`{|}~"
TERMINAL_BAUD_RATES = (50, 75, 110, 134, 150, 200, 300, 600, 1200, 1800, 2400, 4800, 9600, 19200, 38400, 57600, 76800, 115200, 230400)
TERMINAL_DATA_BITS = (5, 6, 7, 8)
TERMINAL_PARITIES = ("none", "even", "odd")
TERMINAL_STOP_BITS = (1, 2)
TERMINAL_FLOW_CONTROLS = ("xon_xoff", "rts_cts", "none")
TERMINAL_END_OF_LINES = ("cr", "crlf", "lf", "lfcr")
TERMINAL_MODES = ("serial", "console", "none")


def _join_prompt(*parts: str) -> str:
    """Joins prompt parts with a blank line between, leaving out empty ones; "" if the first is empty."""
    if not parts[0].strip():
        return ""
    return "\n\n".join(part.strip() for part in parts if part.strip())


@dataclass(frozen=True)
class ProjectSettings:
    """The top-level keys of pom.toml, before any `[section]`.

    Attributes:
        strict: Raise on every recoverable problem (development and tests only).
        debug: Show DEBUG output from every module (a module may override it with its own DEBUG line).
        log_level: A level name: DEBUG, INFO, WARNING, ERROR or CRITICAL.
        local_logs: Also write the log to a local file (development only).
        logmute_after_repeats: How many lines of one message are logged before it is muted, a whole number of at least 1.
        logmute_remind_every_repeats: Once a message is muted, one line in this many is logged as a reminder, a whole number of at least 1.
        logmute_quiet_seconds: How long a muted message must be silent before it is logged again, above 0.
    """
    strict: bool = setting(False, flag())
    debug: bool = setting(False, flag())
    log_level: str = setting("INFO", log_level())
    local_logs: bool = setting(False, flag())
    logmute_after_repeats: int = setting(50, count())
    logmute_remind_every_repeats: int = setting(500, count())
    logmute_quiet_seconds: float = setting(300.0, above_zero())


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
        warmup_poll_seconds: While the scorer's server is not up yet, how often to ask whether it is (and the pause between failed warm-up requests),
            above 0. At start-up the Printer sends the model one throwaway request once its server answers, so the first real score is fast.
        warmup_attempts: How many warm-up requests may fail, with the server up, before the warm-up gives up, a whole number of at least 1.
        prompt_output: How the model is told to reply when it is scoring (a bare number).
        prompt_output_debug: How it is told to reply when explaining a score (the number and what
            content earned it). Only the debug view uses this.

    The system message the Scorer sends is `prompt` (scoring plus output), and the one for the
    debug view is `prompt_debug` (scoring plus the debug output); each is the two parts joined
    by a blank line.
    """
    url: str = setting("http://127.0.0.1:8098/v1/chat/completions", web_address())
    timeout: float = setting(30.0, at_least(0.1))
    prompt_scoring: str = setting("", text())
    clip_filter_score: float = setting(1.0, at_least(0.0))
    min_content_words: int = setting(1, whole_at_least(0))
    failure_limit: int = setting(3, whole_at_least(1))
    retry_seconds: float = setting(30.0, at_least(0.0))
    prompt_output: str = setting("", text())
    prompt_output_debug: str = setting("", text())
    warmup_poll_seconds: float = setting(5.0, above_zero())
    warmup_attempts: int = setting(3, whole_at_least(1))

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
        exemplars_min: The fewest library passages in each pull of exemplars, a whole number of at least 1.
        exemplars_max: The most library passages in each pull of exemplars, a whole number of at least 1 (more than `exemplars_min`, or the two
            are swapped with a warning).
    """
    url: str = setting("http://127.0.0.1:8099/v1/chat/completions", web_address())
    timeout: float = setting(240.0, at_least(0.1))
    prompt: str = setting("", text())
    max_tokens: int = setting(300, whole_at_least(1))
    temperature: float = setting(0.8, at_least(0.0))
    exemplar_redraws: int = setting(50, whole_at_least(0))
    exemplars_min: int = setting(3, count())
    exemplars_max: int = setting(5, count())


@dataclass(frozen=True)
class QualifyingSettings:
    """The `[qualifying]` section: when the Printer responds.

    `algorithm` picks how the bar is worked out; each algorithm reads only its own settings, so one section can hold them all and
    switching is one line.

    Attributes:
        bar_percentile: How much of the room's recent scores a score must beat to qualify, above 0 and at most 100. 100 means beating
            the best score; 99.5 beating all but about one in two hundred; 90 being in the top tenth. (percentile and decaying_quantile)
        window_seconds: How many seconds of recent scores the bar is drawn from, above 0 (300 is the last five minutes). Measured in
            time, not in scores, so it means the same in a busy room and a quiet one. (percentile)
        min_scores: The window always holds at least this many scores, and until this many have been heard the bar is the
            starting bar, a whole number of at least 2. (percentile)
        cooldown_seconds: How many seconds after a qualifier before another score may qualify, 0 or more (0 is no cooldown). (all)
        starting_bar: The bar before the room has said enough to work one out, and where it begins; it fades as the room speaks.
            There is no silent warm-up. (all)
        qualifier_bar_influence: How much a score that stood above the bar (a qualifier, or one held by the cooldown) influences the bar: it
            goes in as the bar plus this much of the way up to the score (1 is the score itself, 0 is the bar itself, 0.5 half way). Any number,
            not limited to 0 to 1 (an experiment may want 1.1 or -0.1). (all)
        half_life_seconds: Seconds for a score to count half as much, above 0. (decaying_quantile)
        average_fraction: How far above the decaying average the bar sits, 0 or more (0.25 is 25% above). (moving_average)
        average_half_life_scores: How many more scores it takes for a score's influence on the average to halve, above 0.
            (moving_average)
        average_floor_fraction: How far up the 0 to 9 scale the floor sits; scores below it are ignored. From 0 up to but not
            including 1. (moving_average)
        algorithm: "percentile", "decaying_quantile" or "moving_average".
        typed_lines_score_multiplier: What a typed line's score is multiplied by before it meets the bar, 0 or more (1 is no bonus). A typed
            line is someone speaking to the Printer directly, so its score is multiplied; the product is not capped at 9. (all)
        typed_lines_bar_influence: The same dial for a typed line, above the bar or below it: it goes in as the bar plus this much of the way up
            to its (multiplied) score. Any number, not limited to 0 to 1. A typed line never has a cooldown. (all)
        anchor_score: The value of each tick, the steady stand-in score that runs under the room so that silence lowers the bar: when the room goes quiet,
            the bar settles toward this level. Any number. (decaying_quantile)
        anchor_interval_minutes: Minutes between ticks, above 0. Shorter pulls the bar down faster in silence, and a little lower during talk.
            (decaying_quantile)
        discard_scores_older_than_minutes: Minutes after which the bar drops a score, or a tick, from its memory, above 0. Once every real score is
            dropped the bar is made of the ticks alone, so this is how long a crowd's high can be held at most. (decaying_quantile)
        starting_bar_weight: How many scores' worth of weight the starting bar carries when the bar first begins, above 0. It fades on the same half-life
            as any score, so the bar slides from the starting bar to what the room really does. (decaying_quantile)
    """
    bar_percentile: float = setting(99.5, percent())
    window_seconds: float = setting(300.0, above_zero())
    min_scores: int = setting(20, whole_at_least(2))
    cooldown_seconds: float = setting(60.0, at_least(0.0))
    starting_bar: float = setting(6.8, any_number())
    qualifier_bar_influence: float = setting(0.5, any_number())
    half_life_seconds: float = setting(300.0, above_zero())
    average_fraction: float = setting(0.25, at_least(0.0))
    average_half_life_scores: float = setting(10.0, above_zero())
    average_floor_fraction: float = setting(0.05, below_one())
    algorithm: str = setting("percentile", one_of(BAR_ALGORITHMS))
    typed_lines_score_multiplier: float = setting(1.5, at_least(0.0))
    typed_lines_bar_influence: float = setting(0.0, any_number())
    anchor_score: float = setting(5.85, any_number())
    anchor_interval_minutes: float = setting(1.0, above_zero())
    discard_scores_older_than_minutes: float = setting(120.0, above_zero())
    starting_bar_weight: float = setting(20.0, above_zero())


@dataclass(frozen=True)
class ListeningSettings:
    """The `[listening]` section: how the Printer hears the room.

    The defaults are the combination that worked well in the room (whisper `base.en` with its voice detection at 0.5,
    a round every 5 seconds); a test pins them. Change one at a time.

    Attributes:
        whisper_url: The whisper-server's address (POM_WHISPER_URL overrides it).
        whisper_timeout: Seconds to wait for one transcription, above 0.
        device: The ALSA capture device for the microphone, as `arecord -D` takes it, or `auto` (the default) to find the microphone by asking
            `arecord -l` each time the Printer starts listening.
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
        max_clip_words: The most words one clip may have, a whole number of at least 1. Whisper's punctuation is what cuts speech into sentences,
            clauses and phrases; a run of words with none is cut into consecutive windows of this many words, so a hinge is always a bounded size.
        device_lookup_seconds: How long to wait for `arecord -l` when `device` is `auto`, above 0. Up: a slow sound system is waited for; down: a stuck
            listing is given up on sooner (the keyword is then handed to arecord, which fails, and the lookup is tried again at the next start).
    """
    whisper_url: str = setting("http://127.0.0.1:8110", web_address())
    whisper_timeout: float = setting(180.0, above_zero())
    device: str = setting("auto", text())
    buffer_seconds: float = setting(10.0, above_zero())
    cut_every_seconds: float = setting(5.0, above_zero())
    hold_back_seconds: float = setting(2.0, at_least(0.0))
    rewind_seconds: float = setting(0.0, at_least(0.0))
    max_cut_seconds: float = setting(30.0, above_zero())
    room_tone_pass_every: int = setting(3, whole_at_least(0))
    room_tone_pass_seconds: float = setting(10.0, above_zero())
    room_tone_memory: int = setting(6, whole_at_least(1))
    score_room_tone: bool = setting(False, flag())
    max_clip_words: int = setting(40, whole_at_least(1))
    device_lookup_seconds: float = setting(5.0, above_zero())


@dataclass(frozen=True)
class MonitorSettings:
    """The `[monitor]` section: the monitoring site, a web page of the last hour of turns.

    Attributes:
        enabled: True starts the site with the Printer (it still waits for a password).
        host: The address it listens on; 0.0.0.0 means every network interface, so anyone on the local network can reach it.
        port: The port it listens on, 1 to 65535.
        password: The site's password. The site does not start while it is empty; set it in config/pom.local.toml,
            which git ignores, never in pom.toml.
        show_hinges: True shows the hinge (what was said in the room) on the page; False shows numbers only.
        history_seconds: How long a turn stays on the page, above 0 (3600 is an hour). Kept in memory only. A turn also stays while its qualifier dot
            is still on the chart, however old it is; the tiles count only turns within this many seconds.
        mic_down_after_seconds: How long without any sound arriving before the mic light goes down, above 0. Longer forgives a hiccup; shorter
            reports a silent microphone sooner.
        service_down_after_seconds: How long a server's (or the printer's) last check stays good, above 0: with no fresh answer for this long its
            light goes down. Must be longer than `service_check_seconds` (the default is three checks).
        service_check_seconds: How often the lights for the servers and the printer (whisper, scorer, oracle, printer) are checked, above 0, so the page's
            polling does not hammer the servers.
        password_on_localhost: True asks for the password of every request, including one from the Printer itself (its own display). False lets a
            request that comes from the Printer's own machine (127.0.0.1 or ::1, and not through a proxy) in without one; everyone else still needs it.
        poll_seconds: How often the open page asks for news and redraws itself (the chart, the status tiles and the turns), above 0. A shorter time
            makes the page and graph move more smoothly and costs a little more work for the Pi.
        chart_notebook_max_entries: The most entries (attempts and ticks) the chart's notebook keeps, a whole number of at least 1. It is a safety limit: age
            (the chart's own `window_minutes` of drawn time) normally ends an entry first. Too low and the left of the chart is cut short in a busy room; too high only lets the
            notebook grow larger.
        hinge_snippet_chars: How many characters of a hinge the page's list of turns shows before cutting it with an ellipsis; the whole hinge is on the turn's own
            page. A whole number of at least 1. Higher shows more of each hinge and makes the list rows longer; lower makes the list tighter and shows less.
        history_max_turns: The most turns the page keeps, a whole number of at least 1. It is a safety limit: age (`history_seconds`) normally ends a turn first. Too
            low and a busy room's turns are dropped before they are old; too high only lets the history grow larger.
    """
    enabled: bool = setting(True, flag())
    host: str = setting("0.0.0.0", text())
    port: int = setting(8080, port_number())
    password: str = setting("", text())
    show_hinges: bool = setting(True, flag(when_unreadable=False))   # an unreadable flag leans toward privacy: the hinges are hidden
    history_seconds: float = setting(3600.0, above_zero())
    service_check_seconds: float = setting(15.0, above_zero())
    poll_seconds: float = setting(5.0, above_zero())
    password_on_localhost: bool = setting(True, flag())
    chart_notebook_max_entries: int = setting(5000, whole_at_least(1))
    hinge_snippet_chars: int = setting(90, whole_at_least(1))
    history_max_turns: int = setting(500, whole_at_least(1))
    mic_down_after_seconds: float = setting(5.0, above_zero())
    service_down_after_seconds: float = setting(45.0, above_zero())


@dataclass(frozen=True)
class DisplaySettings:
    """The `[display]` section: the kiosk, which shows the monitoring page full-screen on a monitor plugged into the Printer.

    Attributes:
        browser: The command that starts the browser, a name on the path or a full path (Chromium).
        browser_flags: The flags the browser is started with, a list of text (it may be empty). The profile directory and the page's address are
            added after them.
        browser_profile_dir: Where the browser keeps its own settings and cache, a folder of its own so the kiosk never shares them with anything
            else; `~` means the home directory.
    """
    browser: str = setting("chromium", nonblank_text())
    browser_flags: tuple = setting(("--kiosk", "--noerrdialogs", "--disable-infobars", "--disable-session-crashed-bubble", "--no-first-run",
                                 "--ozone-platform=wayland", "--password-store=basic"), list_of_text())
    browser_profile_dir: str = setting("~/.config/pom-kiosk", nonblank_text())


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
    paper_width_inches: float = setting(8.5, above_zero())
    margin_inches: float = setting(1.0, at_least(0.0))
    characters_per_inch: float = setting(10.0, above_zero())
    quote_indent: int = setting(4, whole_at_least(0))
    linefeeds_after_portent: int = setting(4, whole_at_least(0))
    keep_composed_characters: str = setting("", only_characters(COMPOSED_CHARACTERS))
    ascii_printwheel: bool = setting(False, flag())

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
        port: `auto`, or a name: any part of the USB name of the serial device or of its path (see DeviceFinder). A name not found falls
            back to whatever is there.
        port_search: Where `auto` and a name look for serial devices: a glob such as /dev/serial/by-id/*.
        baud: The speed, one of TERMINAL_BAUD_RATES. Only the CadetWriter's RS-232 port uses it; USB ignores it.
        data_bits: Bits in each character, one of TERMINAL_DATA_BITS (7 or 8 are the usual). Must match the terminal's setting; a mismatch
            prints wrong characters or none.
        parity: "none", "even" or "odd": the check bit sent with each character. Must match the terminal's setting.
        stop_bits: Bits that end each character, 1 or 2. Must match the terminal's setting.
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
        max_typed_line_characters: A line typed on the typewriter's keyboard is ended at this many characters, so a stuck key cannot grow it
            without limit. A whole number of at least 1.
        refused_character_retry_seconds: The pause before a character the machine refused (a full buffer, a held machine) is sent again, above 0.
        mode: "serial" (the real typewriter, the default), "console" (types on the screen at the same pace, for development) or
            "none" (no printer: a turn is write, record, done, so the Printer can be watched with nothing attached).
    """
    port: str = setting("auto", text())
    baud: int = setting(9600, whole_one_of(TERMINAL_BAUD_RATES, "a standard baud rate (such as 9600 or 115200)"))
    flow_control: str = setting("xon_xoff", one_of(TERMINAL_FLOW_CONTROLS))
    end_of_line: str = setting("crlf", one_of(TERMINAL_END_OF_LINES))
    characters_per_second: float = setting(16.0, above_zero())
    finish_wait_seconds: float = setting(2.0, at_least(0.0))
    echoplex: bool = setting(True, flag())
    type_during_printing: bool = setting(False, flag())
    stall_patience_seconds: float = setting(600.0, above_zero())
    reconnect_seconds: float = setting(2.0, above_zero())
    mode: str = setting("serial", one_of(TERMINAL_MODES))
    max_typed_line_characters: int = setting(500, whole_at_least(1))
    refused_character_retry_seconds: float = setting(1.0, above_zero())
    data_bits: int = setting(8, whole_one_of(TERMINAL_DATA_BITS))
    parity: str = setting("none", one_of(TERMINAL_PARITIES))
    stop_bits: int = setting(1, whole_one_of(TERMINAL_STOP_BITS))
    port_search: str = setting("/dev/serial/by-id/*", nonblank_text())


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
    writing_seconds: float = setting(300.0, above_zero())
    printing_seconds: float = setting(180.0, above_zero())
    recovering_seconds: float = setting(3.0, at_least(0.0))
    freshness_seconds: float = setting(360.0, above_zero())
    write_attempts: int = setting(3, whole_at_least(1))
    write_pause_seconds: float = setting(10.0, at_least(0.0))
    write_budget_seconds: float = setting(270.0, above_zero())
    print_pause_seconds: float = setting(10.0, above_zero())
    restart_pause_seconds: float = setting(5.0, at_least(0.0))
    check_seconds: float = setting(1.0, above_zero())


@dataclass(frozen=True)
class BootSlipSettings:
    """The `[boot_slip]` section: the short slip the typewriter types when the Printer and the typewriter are both online.

    Attributes:
        enabled: True types the slip; False types nothing at start-up.
        text: The slip's words. `{address}` is replaced by the machine's IPv4 address (127.0.0.1 if it has none) and `{hostname}` by its name.
        address_grace_seconds: Once the typewriter is online, how long to wait for the machine to get an address before typing without one. 0 or more.
        retry_seconds: How often to ask whether the typewriter is online, and whether the floor is free. 0 or more.
    """
    enabled: bool = setting(True, flag())
    text: str = setting("CADETWRITER - IBM-1620\nMODE ......... TERMINAL\nSELF TEST .... OK\nLINE 1 ....... {address}\nON LINE", text())
    address_grace_seconds: float = setting(15.0, at_least(0.0))
    retry_seconds: float = setting(5.0, at_least(0.0))


@dataclass(frozen=True)
class ChartSettings:
    """The `[chart]` section: the monitoring page's chart of the room's scores and the bar.

    Every number that shapes the chart is here. Colors are the page's own (they follow its light and dark theme).

    Attributes:
        window_minutes: How much time the chart shows, back from now, above 0. A long silence is drawn short (see `longest_silence_minutes`), so
            the chart reaches back further in real time than this after one.
        gridline_every_minutes: A faint vertical line this often, above 0. The time axis says so in words ("Intervals at approximately 5 min"): the
            intervals are approximate because a silence is drawn short.
        score_max: The top of the score scale, above 0 (the bottom is 0). A score outside the scale is drawn at the edge.
        score_gridlines: The scores that get a horizontal line and a label, a list of numbers.
        drawing_width: The drawing's own width, in the units its coordinates use (it scales to the width of the page), above 0.
        drawing_height: The drawing's own height, above 0.
        margin_left: The space left of the plot, 0 or more (the score labels sit there).
        margin_right: The space right of the plot, 0 or more.
        margin_top: The space above the plot, 0 or more.
        margin_bottom: The space below the plot, 0 or more (the time labels sit there).
        attempt_dot_radius: The radius of the dot for an attempt, above 0.
        qualifier_dot_radius: The radius of the dot for a qualifier, above 0.
        bar_line_width: The width of the line that is the bar, above 0.
        longest_silence_minutes: A silence (a stretch with no attempt) longer than this is drawn this wide, with its real length written
            over it, above 0. Higher shows longer silences at their true size; lower squeezes them more and fits more of the room's history in.
        break_mark_size: The height of the zigzag on each edge of a silence, the engineer's break mark, above 0.
        break_gap_width: The width of the clear gap between the two break marks, in the drawing's units, above 0. It holds the words, so it should be
            wide enough for them; the rest of the silence shows on either side. A silence narrower than this is all gap.
    """
    window_minutes: float = setting(30.0, above_zero())
    gridline_every_minutes: float = setting(5.0, above_zero())
    score_max: float = setting(9.0, above_zero())
    score_gridlines: tuple = setting((0.0, 3.0, 6.0, 9.0), list_of_numbers())
    drawing_width: float = setting(720.0, above_zero())
    drawing_height: float = setting(150.0, above_zero())
    margin_left: float = setting(24.0, not_negative())
    margin_right: float = setting(6.0, not_negative())
    margin_top: float = setting(8.0, not_negative())
    margin_bottom: float = setting(18.0, not_negative())
    attempt_dot_radius: float = setting(1.8, above_zero())
    qualifier_dot_radius: float = setting(4.0, above_zero())
    bar_line_width: float = setting(1.6, above_zero())
    longest_silence_minutes: float = setting(5.0, above_zero())
    break_mark_size: float = setting(6.0, above_zero())
    break_gap_width: float = setting(64.0, above_zero())


# Each [section] of pom.toml and the class that holds it, by its name in the file (which is also its name on Config: config.conductor).
SECTIONS = {"scorer": ScorerSettings, "oracle": OracleSettings, "qualifying": QualifyingSettings, "listening": ListeningSettings,
            "monitor": MonitorSettings, "display": DisplaySettings, "printing": PrintingSettings, "terminal": TerminalSettings,
            "conductor": ConductorSettings, "boot_slip": BootSlipSettings, "chart": ChartSettings}
