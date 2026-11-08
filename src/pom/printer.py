"""The Printer: the entry point. It builds every part from one Config and runs the Conductor until it is told to stop.

    python -m pom                       # the Pi: the microphone (listening.device), the typewriter ([terminal] port)
    python -m pom --mac-mic             # this Mac's microphone through ffmpeg
    python -m pom --wav meeting.wav     # a 16 kHz mono recording, in real time (--speed 0 for as fast as possible)
    python -m pom --terminal none       # no printer: listen, write, show everything on the monitoring site
    python -m pom --terminal console    # type on the screen at the typewriter's pace

On the Pi it runs as a service (see the systemd unit in the deployment notes). What it needs is the three servers on the Pi itself, which a
service cannot count on being up first: whisper-server (`listening.whisper_url`), and one llama-server each for the scorer model
(`scorer.url`) and the oracle model (`oracle.url`).

Design decisions, and why:
  * IT STARTS WHATEVER STATE THE WORLD IS IN. At boot the servers, the microphone and the printer come up in no particular order, and
    nobody is there to restart anything. So a server that does not answer yet is a line in the log, not a reason to exit: the whisper
    client, the scorer and the oracle client each recover when their server arrives, and a turn waits for the printer. The only things
    that stop it are the ones it cannot work around: no password for a monitoring site you asked for is a warning and no site, not a stop.
  * ONE CONFIG. `Config.load` is called once and every setting comes from it; the parts never read it. The command-line options only choose
    where sound comes from and what is printed on, and only for this run.
  * EVERYTHING IS BUILT IN `build_printer`, a plain function that contacts no server, so the whole wiring can be built and inspected in a
    test. `main` only parses the options, builds, installs the signal handlers (SIGTERM from systemd and ctrl-C both stop it cleanly) and
    runs.
  * NOTHING SAID IS KEPT. The monitoring site shows what the config lets it (`show_hinges`) from memory; nothing is logged or written.
    The log holds counts, labels and seconds.
  * The microphone can be unplugged: the room is built again by the Conductor each time it ends. A recording ends the run instead.
"""
import argparse
import logging
import random
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pom.conductor import AddressFinder, BootSlipper, Conductor, FloorKeeper, HingeFinder, KeyboardWatcher, TurnTaker
from pom.config import Config
from pom.keyboard import TypedLineScorer
from pom.library import Bibliomancer, Catalog, Circulation
from pom.listening import (Eavesdropper, MacMicDeck, MicDeck, TakeCutter, WavDeck, WhisperModelClient)
from pom.log_setup import configure_logging, get_logger
from pom.monitor import MonitorSite, MonitorStatus, ChartNotebook, TurnHistory, silence_edge_seconds
from pom.printing import ConsoleTerminal, PortentFormatter, TerminalPort
from pom.qualifying import bar_keeper_from_settings
from pom.scoring import ClipScorer, ScorerModelClient, ScorerWarmer, WordTagger
from pom.writing import OracleModelClient, PortentWriter, PromptBuilder

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "pom.toml"
PASSAGES = ROOT / "library" / "passages"
NLTK_DATA = ROOT / "data" / "nltk"
DEFAULT_SERVICE_CHECK_SECONDS = 15.0   # the monitoring page's service lights are checked at most this often (the fallback; `[monitor] service_check_seconds`)


@dataclass(frozen=True)
class Options:
    """What a run chooses for itself, apart from the config.

    Attributes:
        config_path: The config file to read.
        wav: A 16 kHz mono recording to play instead of a microphone, or None.
        speed: The recording's speed: 1 is real time, 0 is as fast as possible.
        mac_mic: This Mac's microphone through ffmpeg: "default" or a device name; None to use the Pi's (or the recording).
        terminal: "serial", "console" or "none", overriding `[terminal] mode`; None keeps the config's.
        keyboard: False leaves the typewriter's keyboard unwatched even with a real terminal.
    """
    config_path: str = str(DEFAULT_CONFIG)
    wav: Optional[str] = None
    speed: float = 1.0
    mac_mic: Optional[str] = None
    terminal: Optional[str] = None
    keyboard: bool = True


def answers(url: str) -> bool:
    """True if the llama-server behind this chat address says it is healthy."""
    parts = urllib.parse.urlparse(url)
    try:
        with urllib.request.urlopen(f"{parts.scheme}://{parts.netloc}/health", timeout=3):
            return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def reachable(url: str) -> bool:
    """True if something answers an HTTP request at this address (the whisper-server's front page, for instance)."""
    try:
        with urllib.request.urlopen(url, timeout=3):
            return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


class ServiceWatch:
    """Which servers answer, checked at most every `every` seconds so the monitoring page's polling does not hammer them.

    Each check says True (up), False (down), or None (there is nothing to check: no terminal is configured)."""

    def __init__(self, checks: dict, every: float = DEFAULT_SERVICE_CHECK_SECONDS, clock=time.monotonic):
        self._checks, self._every, self._clock, self._at, self._last = checks, every, clock, None, {}

    def __call__(self) -> dict:
        now = self._clock()
        if self._at is None or now - self._at >= self._every:
            self._last, self._at = {name: _safely(check) for name, check in self._checks.items()}, now
        return dict(self._last)


def _safely(check) -> bool:
    try:
        answer = check()
        return None if answer is None else bool(answer)
    except Exception:                                    # a check that fails is a server that does not answer
        return False


class Printer:
    """The whole Printer, built: run it, stop it, ask it how it is.

    Attributes:
        conductor: The Conductor that runs everything.
        history: The TurnHistory the monitoring site reads (always made; shown only if the site runs).
        site: The MonitorSite, or None if it is not enabled or has no password.
        terminal: The TerminalPort or ConsoleTerminal, or None when there is no printer.
        floor: The FloorKeeper.
        parts: The other parts by name (for a test or a testbench to look at).
    """

    def __init__(self, conductor: Conductor, history: TurnHistory, site: Optional[MonitorSite], terminal, floor: FloorKeeper, parts: dict,
                 services, warmer: Optional[ScorerWarmer] = None, slipper: Optional[BootSlipper] = None):
        self.conductor, self.history, self.site, self.terminal, self.floor, self.parts = conductor, history, site, terminal, floor, parts
        self._services, self._warmer, self._slipper = services, warmer, slipper

    def run(self) -> None:
        """Starts the monitoring site (if any), the scorer's warm-up and the boot slip, runs the Conductor until it stops, then stops them and closes the terminal. Blocks."""
        if self.site is not None and not self.site.start(self.parts["monitor"].host, self.parts["monitor"].port):
            _log.warning("the monitoring site did not start (%s); going on without it", "; ".join(self.site.report.lines()))
            self.site = None
        elif self.site is not None:
            _log.info("monitoring site listening on port %s", self.site.port)
        _log.info("services: %s", ", ".join(f"{name} {'up' if up else 'NOT answering'}" for name, up in self._services().items()))
        if self._warmer is not None:
            self._warmer.start()                         # in the background: the Printer listens at once, whatever state the servers are in
        if self._slipper is not None:
            self._slipper.start()                        # in the background: waits for the typewriter, then types the boot slip
        try:
            self.conductor.run()
        finally:
            if self._slipper is not None:
                self._slipper.stop()
            if self._warmer is not None:
                self._warmer.stop()
            if self.site is not None:
                self.site.stop()
            close = getattr(self.terminal, "close", None)
            if close is not None:
                close()

    def stop(self) -> None:
        """Stops the Printer. Safe from a signal handler or another thread, and safe to repeat."""
        self.conductor.stop()

    def report_lines(self) -> list:
        """Every problem the parts have recorded, as lines to show."""
        return self.conductor.report_lines()


def build_printer(config: Config, options: Optional[Options] = None, root: Path = ROOT) -> Printer:
    """Builds every part from `config` and `options` and joins them in a Conductor. Contacts no server and opens no port or microphone."""
    options = options or Options()
    listening, qualifying, scoring, oracle = config.listening, config.qualifying, config.scorer, config.oracle
    terminal_settings, conductor_settings, monitor = config.terminal, config.conductor, config.monitor
    mode = options.terminal or terminal_settings.mode
    started = time.monotonic()

    # listening: a fresh deck, Eavesdropper and finder each time the room is built (a microphone that comes back is heard)
    whisper = WhisperModelClient(listening.whisper_url, listening.whisper_timeout)
    nltk_dir = root / "data" / "nltk"
    scorer_client = ScorerModelClient(scoring.url, scoring.prompt, scoring.timeout, explain_prompt=scoring.prompt_debug)
    scorer = ClipScorer(scorer_client, WordTagger(nltk_data=str(nltk_dir) if nltk_dir.exists() else None),
                        clip_filter_score=scoring.clip_filter_score, min_content_words=scoring.min_content_words,
                        failure_limit=scoring.failure_limit, retry_seconds=scoring.retry_seconds)
    warmer = ScorerWarmer(scorer, lambda: answers(scoring.url), poll_seconds=scoring.warmup_poll_seconds,
                          attempts=scoring.warmup_attempts)     # sends the scorer one throwaway request once its server is up
    stream_started_at = 0.0           # the stream time at which listening starts: the first room's clock begins here, and the bar begins here too
    bar_keeper = bar_keeper_from_settings(qualifying, stream_started_at)
    take_cutter = TakeCutter(max_words=listening.max_clip_words)

    def make_deck():
        if options.wav:
            return WavDeck(options.wav, speed=options.speed)
        if options.mac_mic is not None:
            return MacMicDeck(options.mac_mic, buffer_seconds=listening.buffer_seconds)
        return MicDeck(listening.device, buffer_seconds=listening.buffer_seconds)

    last_room = {"ears": None}     # the Eavesdropper of the room that ran last, so the next one's clock can carry on from it

    def make_room():
        # A room built again (after the last one failed) carries the stream clock on from where the old one stopped, plus the real seconds since, so stream time
        # keeps matching real time. Starting again at 0 would leave the BarKeeper, which never lets time run backwards, holding still until the new clock caught up.
        old = last_room["ears"]
        ears = Eavesdropper(make_deck(), whisper, listening.cut_every_seconds, listening.hold_back_seconds, listening.rewind_seconds,
                            listening.max_cut_seconds, listening.room_tone_pass_every, listening.room_tone_pass_seconds,
                            listening.room_tone_memory, start_seconds=old.stream_time_at(time.monotonic()) if old is not None else stream_started_at, wall=time.monotonic)
        last_room["ears"] = ears
        return HingeFinder(ears, take_cutter, scorer, bar_keeper, listening.score_room_tone), ears

    # writing and printing
    passages = root / "library" / "passages"
    rng = random.Random()                                  # one generator for the Bibliomancer and the writer, so a draw can be seeded
    selector = Bibliomancer(Catalog.load(passages), Circulation.from_file(passages / "circulation.json"), rng,
                            exemplar_range=config.exemplar_range, redraw_limit=oracle.exemplar_redraws)
    writer = PortentWriter(PromptBuilder(oracle.prompt, selector),
                           OracleModelClient(oracle.url, oracle.timeout, oracle.max_tokens, oracle.temperature), rng)
    printing = config.printing
    formatter = PortentFormatter(printing.line_width, printing.quote_indent, printing.linefeeds_after_portent,
                                 printing.composed_characters_kept)
    terminal = _make_terminal(mode, terminal_settings)

    # the turn
    history = TurnHistory(monitor.history_seconds)
    # the chart's notebook writes down the ticks too (the bar's own `ticks_between`), except across a silence, which the chart draws as a break
    # (it keeps what the chart can show, `[chart] window_minutes` of drawn time, and no more)
    notebook = ChartNotebook(config.chart.window_minutes * 60.0, monitor.chart_notebook_max_entries, ticks_source=bar_keeper.ticks_between,
                             longest_silence_seconds=config.chart.longest_silence_minutes * 60.0, began_at=stream_started_at,
                             head_seconds=silence_edge_seconds(config.chart))
    floor = FloorKeeper(conductor_settings.writing_seconds, conductor_settings.printing_seconds, conductor_settings.recovering_seconds)
    turn_taker = TurnTaker(writer, formatter, terminal, history, freshness_seconds=conductor_settings.freshness_seconds,
                           write_attempts=conductor_settings.write_attempts, write_pause_seconds=conductor_settings.write_pause_seconds,
                           write_attempt_seconds=oracle.timeout, write_budget_seconds=conductor_settings.write_budget_seconds,
                           print_pause_seconds=conductor_settings.print_pause_seconds)

    # the boot slip: types the Pi's address when the typewriter is online (only if there is a terminal to type on)
    slip = config.boot_slip
    slipper = None
    if terminal is not None and slip.enabled:
        slipper = BootSlipper(floor, terminal, formatter, slip.text, AddressFinder().find, grace_seconds=slip.address_grace_seconds,
                              retry_seconds=slip.retry_seconds, ears_shut_seconds=conductor_settings.check_seconds + 1.0)

    # the keyboard: only a real typewriter has one
    keyboard = None
    if terminal is not None and mode == "serial" and options.keyboard:
        typed = TypedLineScorer(scorer, bar_keeper, lambda: time.monotonic() - started, qualifying.typed_lines_score_multiplier,
                                qualifying.typed_lines_bar_influence)
        keyboard = KeyboardWatcher(terminal, typed)

    # what the monitoring site is told: numbers only, from every attempt
    snapshot = {"heard": 0, "scored": 0}

    def stream_now() -> float:
        """The stream time right now: the listening side's clock, which runs through silence (before the first room is built, the stream's start)."""
        ears = last_room["ears"]
        return ears.stream_time_at(time.monotonic()) if ears is not None else stream_started_at

    def note(attempt) -> None:
        snapshot["heard"] += 1
        snapshot["scored"] += attempt.score is not None
        if attempt.score is not None:
            notebook.add(attempt.score, attempt.bar, attempt.heard_at, attempt.is_hinge)

    def status_now() -> MonitorStatus:
        """The numbers on the page's tiles as of this moment: the bar is read at the stream's clock, so it keeps moving through silence."""
        now = stream_now()
        return MonitorStatus(bar=bar_keeper.bar_at(now), window_size=bar_keeper.window_size_at(now),
                             attempts_heard=snapshot["heard"], attempts_scored=snapshot["scored"],
                             services=services(), algorithm=qualifying.algorithm, bar_description=bar_keeper.describe())

    services = ServiceWatch({"whisper": lambda: reachable(listening.whisper_url), "scorer": lambda: answers(scoring.url),
                             "oracle": lambda: answers(oracle.url),
                             "terminal": lambda: None if terminal is None else terminal.online(wait=False)}, every=monitor.service_check_seconds)
    site = None
    if monitor.enabled:
        if not monitor.password:
            _log.warning("the monitoring site is enabled but has no password ([monitor] password in config/pom.local.toml); it will not run")
        else:
            site = MonitorSite(history, monitor.password, monitor.show_hinges, clock=time.time, notebook=notebook, chart=config.chart,
                               poll_seconds=monitor.poll_seconds, stream_now=stream_now,
                               stream_began_at=stream_started_at, password_on_localhost=monitor.password_on_localhost,
                               status_source=status_now)

    def turned(attempt, result) -> None:
        _log.info("turn over: %s", result.outcome.value)

    conductor = Conductor(floor, turn_taker, make_room, keyboard, on_attempt=note, on_turn=turned, restart_room=not options.wav,
                          restart_pause_seconds=conductor_settings.restart_pause_seconds, check_seconds=conductor_settings.check_seconds)
    parts = {"whisper": whisper, "scorer": scorer, "bar_keeper": bar_keeper, "selector": selector, "writer": writer, "formatter": formatter,
             "turn_taker": turn_taker, "notebook": notebook, "monitor": monitor, "keyboard": keyboard, "mode": mode, "take_cutter": take_cutter,
             "warmer": warmer, "boot_slipper": slipper}
    return Printer(conductor, history, site, terminal, floor, parts, services, warmer, slipper)


def _make_terminal(mode: str, settings):
    """The terminal for this mode: the real serial port, the console stand-in, or None for no printer."""
    if mode == "none":
        return None
    if mode == "console":
        return ConsoleTerminal(characters_per_second=settings.characters_per_second)
    return TerminalPort(settings.port, settings.baud, settings.flow_control, settings.end_of_line, settings.characters_per_second,
                        settings.finish_wait_seconds, settings.echoplex, settings.type_during_printing, settings.stall_patience_seconds,
                        settings.reconnect_seconds, max_typed_line_characters=settings.max_typed_line_characters,
                        refused_character_retry_seconds=settings.refused_character_retry_seconds,
                        wait_log_reminder_seconds=settings.wait_log_reminder_seconds)


def parse_options(argv=None) -> Options:
    """The command line as Options."""
    parser = argparse.ArgumentParser(prog="python -m pom", description=__doc__.split("\n")[0])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="the config file to read")
    parser.add_argument("--wav", help="play this 16 kHz mono recording instead of listening to a microphone (the run ends with it)")
    parser.add_argument("--speed", type=float, default=1.0, help="recording speed: 1 is real time, 0 is as fast as possible")
    parser.add_argument("--mac-mic", nargs="?", const="default", metavar="DEVICE",
                        help="listen to this Mac's microphone through ffmpeg: the system default input, or the named one")
    parser.add_argument("--terminal", choices=("serial", "console", "none"),
                        help="what to print on, instead of [terminal] mode: serial (the typewriter), console (the screen), none (no printer)")
    parser.add_argument("--no-keyboard", action="store_true", help="do not watch the typewriter's keyboard")
    args = parser.parse_args(argv)
    if args.wav and args.mac_mic is not None:
        parser.error("give --wav or --mac-mic, not both")
    return Options(args.config, args.wav, args.speed, args.mac_mic, args.terminal, not args.no_keyboard)


def main(argv=None) -> int:
    """Reads the options and the config, builds the Printer and runs it until it is stopped. Returns the exit code."""
    options = parse_options(argv)
    config = Config.load(options.config_path)
    configure_logging(config.log_level, debug=config.debug, local_logs=config.local_logs)
    for line in config.report.lines():
        logging.getLogger("pom").warning("config: %s", line)
    printer = build_printer(config, options)

    def stop_cleanly(signum, frame) -> None:
        _log.info("signal %s: stopping", signum)
        printer.stop()
    signal.signal(signal.SIGTERM, stop_cleanly)
    signal.signal(signal.SIGINT, stop_cleanly)
    _log.info("the Printer starts (terminal: %s)", printer.parts["mode"])
    printer.run()
    for line in printer.report_lines():
        _log.warning("report: %s", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
