"""The PrinterBuilder: builds the Printer's parts from one Config, so the Printer, the testbenches and the tests all build them the same way.

    builder = PrinterBuilder(config, Options(wav="meeting.wav"))
    builder.scorer           # the scorer, built the way the Printer builds it (and only what it needs)
    builder.printer          # the whole Printer, every part joined in a Conductor

Each part is a property that is built the first time it is asked for and then kept (`functools.cached_property`), so asking for one part
builds that part and the parts it needs and nothing else, and every part that needs, say, the BarKeeper gets the same one. A testbench that
wants only the scorer asks for `builder.scorer` instead of copying how it is made, so when a part's wiring changes there is one place to change
it and every script follows. A part can be replaced by setting it before it is first asked for (`builder.circulation = Circulation.from_file(...)`),
which is how a cached_property is meant to be overridden; every part built after that uses the replacement.

Design decisions, and why:
  * IT CONTACTS NOTHING. Building is plain construction: no server is asked, no port or microphone is opened, so the whole wiring can be built
    and inspected in a test. What runs is `Printer.run`.
  * ONE CONFIG. Every setting comes from the Config it is handed; the parts never read it. `Options` choose, for one run, where sound comes from
    and what is printed on.
  * THE STREAM CLOCK STARTS WHEN THE BUILDER IS MADE. The Timekeeper is built in the constructor, not when first asked for, so stream time runs on
    real seconds from the Printer's start whichever part asks for it first.
  * A TESTBENCH CAN WATCH. `on_attempt(attempt)` and `on_turn(attempt, result)`, if given, are called after the Printer's own handling of each
    attempt and each turn, so a testbench can print what it sees (the Printer itself never logs what was said) and stop after a few portents.
  * NOTHING SAID IS KEPT. The monitoring site shows what the config lets it (`show_hinges`) from memory; nothing is logged or written.
"""
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Callable, Optional

from pom.bookkeeper.bookkeeper import Bookkeeper
from pom.conductor import AddressFinder, BootSlipper, Conductor, FloorKeeper, HingeFinder, KeyboardWatcher, NoSoundSource, TurnTaker
from pom.config import Config
from pom.keyboard import TypedLineScorer
from pom.library import Bibliomancer, Catalog, Circulation
from pom.listening import CaptureCardFinder, Eavesdropper, MacMicDeck, MicDeck, TakeCutter, WavDeck, WhisperModelClient
from pom.monitor import ChartNotebook, Light, MonitorSite, MonitorStatus, ServicePoller, StatusBoard, TurnHistory, silence_shape
from pom.printing import ConsoleTerminal, PortentFormatter, TerminalPort, TerminalPortFinder
from pom.qualifying import bar_keeper_from_settings
from pom.scoring import ClipScorer, ScorerModelClient, ScorerWarmer, WordTagger
from pom.timekeeper import Timekeeper
from pom.writing import OracleModelClient, PortentWriter, PromptBuilder

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "pom.toml"
STREAM_STARTED_AT = 0.0          # the stream time at which the Printer starts: a point on the timeline, not a setting


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


def health(url: str) -> Light:
    """How the llama-server behind this chat address is: UP if its health address says ok, WAITING if it says 503 (it answers but is still loading its
    model), DOWN if it does not answer or says anything else."""
    parts = urllib.parse.urlparse(url)
    try:
        with urllib.request.urlopen(f"{parts.scheme}://{parts.netloc}/health", timeout=3):
            return Light.UP
    except urllib.error.HTTPError as error:              # (an HTTPError is a URLError, so it is caught first)
        return Light.WAITING if error.code == 503 else Light.DOWN
    except (urllib.error.URLError, OSError, ValueError):
        return Light.DOWN


def answers(url: str) -> bool:
    """True if the llama-server behind this chat address says it is healthy and ready."""
    return health(url) is Light.UP


def reachable(url: str) -> bool:
    """True if something answers an HTTP request at this address (the whisper-server's front page, for instance)."""
    try:
        with urllib.request.urlopen(url, timeout=3):
            return True
    except (urllib.error.URLError, OSError, ValueError):
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
                 services, warmer: Optional[ScorerWarmer] = None, slipper: Optional[BootSlipper] = None, poller: Optional[ServicePoller] = None,
                 bookkeeper: Optional[Bookkeeper] = None):
        self.conductor, self.history, self.site, self.terminal, self.floor, self.parts = conductor, history, site, terminal, floor, parts
        self._services, self._warmer, self._slipper, self._poller = services, warmer, slipper, poller
        self.logger = (bookkeeper or Bookkeeper()).logger_for("pom.printer", debug=DEBUG)

    def run(self) -> None:
        """Starts the monitoring site (if any), the status poller, the scorer's warm-up and the boot slip, runs the Conductor until it stops, then stops them and closes the terminal. Blocks."""
        if self.site is not None and not self.site.start(self.parts["monitor"].host, self.parts["monitor"].port):
            self.logger.warning("the monitoring site did not start (it logged why, just above); going on without it")
            self.site = None
        elif self.site is not None:
            self.logger.info("monitoring site listening on port %s", self.site.port)
        if self._poller is not None:
            self._poller.poll_once()                     # one round now, so the line below says how things are; then it goes on in its own thread
            self._poller.start()
        self.logger.info("services: %s", ", ".join(f"{name} {light}" for name, light in self._services().items()))
        if self._warmer is not None:
            self._warmer.start()                         # in the background: the Printer listens at once, whatever state the servers are in
        if self._slipper is not None:
            self._slipper.start()                        # in the background: waits for the typewriter, then types the boot slip
        try:
            self.conductor.run()
        finally:
            if self._poller is not None:
                self._poller.stop()
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


class PrinterBuilder:
    """Builds the Printer's parts from one Config: each is a property, built the first time it is asked for and then kept.

    Attributes:
        config: The Config every setting comes from.
        options: What this run chooses (where sound comes from, what is printed on).
        root: The repository root, where the library and the word tagger's data are found.
        bookkeeper: The Bookkeeper every part gets its logger from.
        timekeeper: The stream's one clock, started when the builder is made.
    """

    def __init__(self, config: Config, options: Optional[Options] = None, root: Path = ROOT, bookkeeper: Optional[Bookkeeper] = None,
                 on_attempt: Optional[Callable] = None, on_turn: Optional[Callable] = None):
        """Gets ready to build; builds nothing yet except the stream clock.

        Args:
            config: The Config every setting comes from.
            options: What this run chooses; None for the defaults.
            root: The repository root.
            bookkeeper: The one the entry point built; None for a plain one (a test or a tool).
            on_attempt: Called with each attempt after the Printer's own handling of it, or None (for a testbench that watches).
            on_turn: Called with each attempt and its turn's result after the Printer's own handling, or None.
        """
        self.config = config
        self.options = options or Options()
        self.root = root
        self.bookkeeper = bookkeeper or Bookkeeper()
        self.logger = self.bookkeeper.logger_for("pom.printer", debug=DEBUG)
        self._on_attempt, self._on_turn = on_attempt, on_turn
        self._tally = {"heard": 0, "scored": 0}            # what the monitoring site is told: numbers only, from every attempt
        # the stream's one clock: every part that needs "now" (the rooms, typed lines, the page) is handed `timekeeper.now`, so they share one timeline
        self.timekeeper = Timekeeper(started_at=STREAM_STARTED_AT, wall=time.monotonic)

    @property
    def mode(self) -> str:
        """What is printed on this run: the option if given, else `[terminal] mode`."""
        return self.options.terminal or self.config.terminal.mode

    # --- listening and scoring ---------------------------------------------------------------------------------------------------------

    @cached_property
    def whisper(self) -> WhisperModelClient:
        return WhisperModelClient(self.config.listening, bookkeeper=self.bookkeeper)

    @cached_property
    def scorer_client(self) -> ScorerModelClient:
        """The scorer model's client (the one the ClipScorer asks)."""
        scoring = self.config.scorer
        return ScorerModelClient(scoring, explain_prompt=scoring.prompt_debug, bookkeeper=self.bookkeeper)

    @cached_property
    def word_tagger(self) -> WordTagger:
        """The word tagger, with the project's own word data when it is there."""
        nltk_dir = self.root / "data" / "nltk"
        return WordTagger(nltk_data=str(nltk_dir) if nltk_dir.exists() else None, bookkeeper=self.bookkeeper)

    @cached_property
    def scorer(self) -> ClipScorer:
        """The ClipScorer, on the scorer model's client and the word tagger."""
        return ClipScorer(self.scorer_client, self.word_tagger, self.config.scorer, bookkeeper=self.bookkeeper)

    @cached_property
    def warmer(self) -> ScorerWarmer:
        """Sends the scorer one throwaway request once its server is up."""
        scoring = self.config.scorer
        return ScorerWarmer(self.scorer, lambda: answers(scoring.url), scoring, bookkeeper=self.bookkeeper)

    @cached_property
    def bar_keeper(self):
        return bar_keeper_from_settings(self.config.qualifying, STREAM_STARTED_AT, bookkeeper=self.bookkeeper, stream_now=self.timekeeper.now)

    @cached_property
    def take_cutter(self) -> TakeCutter:
        return TakeCutter(self.config.listening, bookkeeper=self.bookkeeper)

    @cached_property
    def board(self) -> StatusBoard:
        """Every light on the monitoring page, kept in one place. The microphone posts to it for itself as sound arrives; a poller asks the servers
        and the printer. NO NEWS IS DOWN: a light whose last post is too old shows down (see pom/monitor/status_board.py). A recording has no microphone."""
        monitor = self.config.monitor
        board = StatusBoard(bookkeeper=self.bookkeeper)
        if not self.options.wav:
            board.expect("mic", monitor.mic_down_after_seconds)
        for name in ("whisper", "scorer", "oracle", "printer"):
            board.expect(name, monitor.service_down_after_seconds)
        return board

    @cached_property
    def card_finder(self) -> CaptureCardFinder:
        """Turns `auto` into the microphone's card, looked up again at each start of listening."""
        return CaptureCardFinder(self.config.listening, bookkeeper=self.bookkeeper)

    def sound_arrived(self) -> None:
        """What the microphone calls as sound arrives: its light on the board is up."""
        self.board.post("mic", Light.UP)

    def make_deck(self):
        """A fresh deck for a room: the recording, this Mac's microphone, or the microphone found now."""
        listening, options = self.config.listening, self.options
        if options.wav:
            return WavDeck(options.wav, speed=options.speed, bookkeeper=self.bookkeeper)
        if options.mac_mic is not None:
            return MacMicDeck(options.mac_mic, listening=listening, heard=self.sound_arrived, bookkeeper=self.bookkeeper)
        device = self.card_finder.resolve(listening.device)
        if not self.card_finder.found:
            raise NoSoundSource("no sound card was found")       # the finder has logged it; arecord is not started for a card that is not there
        return MicDeck(device, listening=listening, heard=self.sound_arrived, bookkeeper=self.bookkeeper)

    def make_room(self):
        """A fresh room (deck, Eavesdropper and hinge finder), the Conductor's `room_factory`: returns (hinge finder, ears).

        Each room (the first, or one built again after the last failed) begins where the stream clock stands and the clock then follows it, so stream
        time keeps matching real time. Starting again at 0 would leave the BarKeeper, which never lets time run backwards, holding still until the new
        clock caught up.
        """
        listening = self.config.listening
        ears = Eavesdropper(self.make_deck(), self.whisper, listening, start_seconds=self.timekeeper.now(), wall=time.monotonic, bookkeeper=self.bookkeeper)
        self.timekeeper.follow(ears)
        return HingeFinder(ears, self.take_cutter, self.scorer, self.bar_keeper, listening, bookkeeper=self.bookkeeper,
                           stream_now=self.timekeeper.now), ears

    # --- writing and printing ----------------------------------------------------------------------------------------------------------

    @cached_property
    def rng(self) -> random.Random:
        """One generator for the Bibliomancer and the writer, so a draw can be seeded."""
        return random.Random()

    @cached_property
    def catalog(self) -> Catalog:
        """The library's passages."""
        return Catalog.load(self.root / "library" / "passages", bookkeeper=self.bookkeeper)

    @cached_property
    def circulation(self) -> Circulation:
        """The shelves' shares of the draw (`library/passages/circulation.json`)."""
        return Circulation.from_file(self.root / "library" / "passages" / "circulation.json", bookkeeper=self.bookkeeper)

    @cached_property
    def selector(self) -> Bibliomancer:
        """The Bibliomancer, on the library's passages and their circulation."""
        return Bibliomancer(self.catalog, self.circulation, self.rng, oracle=self.config.oracle, bookkeeper=self.bookkeeper)

    @cached_property
    def writer(self) -> PortentWriter:
        oracle = self.config.oracle
        return PortentWriter(PromptBuilder(oracle, self.selector, bookkeeper=self.bookkeeper), OracleModelClient(oracle, bookkeeper=self.bookkeeper), self.rng,
                             bookkeeper=self.bookkeeper)

    @cached_property
    def formatter(self) -> PortentFormatter:
        return PortentFormatter(self.config.printing)

    @cached_property
    def terminal(self):
        """The terminal for this run's mode: the real serial port (found by name, looked up again each time it opens), the console stand-in, or None for no printer."""
        mode, settings = self.mode, self.config.terminal
        if mode == "none":
            return None
        if mode == "console":
            return ConsoleTerminal(settings, bookkeeper=self.bookkeeper)
        return TerminalPort(settings, finder=TerminalPortFinder(settings, bookkeeper=self.bookkeeper), bookkeeper=self.bookkeeper)

    # --- the turn ----------------------------------------------------------------------------------------------------------------------

    @cached_property
    def notebook(self) -> ChartNotebook:
        """The chart's notebook. It writes down the ticks too (the bar's own `ticks_between`), except across a silence, which the chart draws as a break
        (it keeps what the chart can show, `[chart] window_minutes` of drawn time, and no more)."""
        chart = self.config.chart
        return ChartNotebook(chart, self.config.monitor, ticks_source=self.bar_keeper.ticks_between, silence_shape=silence_shape(chart),
                             began_at=STREAM_STARTED_AT, bookkeeper=self.bookkeeper, stream_now=self.timekeeper.now)

    @cached_property
    def history(self) -> TurnHistory:
        """A turn is kept past `history_seconds` while its qualifier dot is still on the chart, which after a long silence reaches back hours."""
        return TurnHistory(self.config.monitor, keep_heard_since=lambda: self.notebook.earliest_at, bookkeeper=self.bookkeeper,
                           stream_now=self.timekeeper.now)

    @cached_property
    def floor(self) -> FloorKeeper:
        return FloorKeeper(self.config.conductor, bookkeeper=self.bookkeeper)

    @cached_property
    def turn_taker(self) -> TurnTaker:
        return TurnTaker(self.writer, self.formatter, self.terminal, self.history, conductor=self.config.conductor, oracle=self.config.oracle,
                         bookkeeper=self.bookkeeper)

    @cached_property
    def address_finder(self) -> AddressFinder:
        """Finds the machine's addresses for the boot slip. A testbench sets its own (anything with `find()`) to show the slip with no network."""
        return AddressFinder(bookkeeper=self.bookkeeper)

    @cached_property
    def boot_slipper(self) -> Optional[BootSlipper]:
        """Types the Pi's address when the typewriter is online; None when there is no terminal to type on or the slip is off."""
        slip = self.config.boot_slip
        if self.terminal is None or not slip.enabled:
            return None
        return BootSlipper(self.floor, self.terminal, self.formatter, self.address_finder.find, boot_slip=slip,
                           ears_shut_seconds=self.config.conductor.check_seconds + 1.0, bookkeeper=self.bookkeeper)

    @cached_property
    def typed_line_scorer(self) -> TypedLineScorer:
        """Scores a line typed on the keyboard, on the same scorer, BarKeeper and stream clock as the room."""
        return TypedLineScorer(self.scorer, self.bar_keeper, self.timekeeper.now, self.config.qualifying, bookkeeper=self.bookkeeper)

    @cached_property
    def keyboard(self) -> Optional[KeyboardWatcher]:
        """The typewriter's keyboard, watched: only a real typewriter has one (and `--no-keyboard` leaves it unwatched)."""
        if self.terminal is None or self.mode != "serial" or not self.options.keyboard:
            return None
        return KeyboardWatcher(self.terminal, self.typed_line_scorer, bookkeeper=self.bookkeeper)

    # --- the monitoring site -----------------------------------------------------------------------------------------------------------

    def note(self, attempt) -> None:
        """The Conductor's `on_attempt`: counts the attempt for the page's tiles, writes a scored one into the chart's notebook, then tells a watcher."""
        self._tally["heard"] += 1
        self._tally["scored"] += attempt.score is not None
        if attempt.score is not None:
            self.notebook.add(attempt.score, attempt.bar, attempt.heard_at, attempt.is_hinge)
        if self._on_attempt is not None:
            self._on_attempt(attempt)

    def status_now(self) -> MonitorStatus:
        """The numbers on the page's tiles as of this moment: the bar is read at the stream's clock, so it keeps moving through silence."""
        now = self.timekeeper.now()
        bar_keeper = self.bar_keeper
        return MonitorStatus(bar=bar_keeper.bar_at(now), window_size=bar_keeper.window_size_at(now),
                             attempts_heard=self._tally["heard"], attempts_scored=self._tally["scored"],
                             services=self.board.lights(), algorithm=self.config.qualifying.algorithm, bar_description=bar_keeper.describe())

    @cached_property
    def poller(self) -> ServicePoller:
        listening, scoring, oracle = self.config.listening, self.config.scorer, self.config.oracle
        terminal = self.terminal
        return ServicePoller(self.board, {"whisper": lambda: reachable(listening.whisper_url), "scorer": lambda: health(scoring.url),
                                          "oracle": lambda: health(oracle.url),
                                          "printer": lambda: None if terminal is None else terminal.online(wait=False)}, self.config.monitor)

    @cached_property
    def site(self) -> Optional[MonitorSite]:
        """The monitoring site, or None if it is off or has no password (a warning, not a stop)."""
        monitor = self.config.monitor
        if not monitor.enabled:
            return None
        if not monitor.password:
            self.logger.warning("the monitoring site is enabled but has no password ([monitor] password in config/pom.local.toml); it will not run")
            return None
        return MonitorSite(self.history, monitor, self.config.chart, clock=time.time, notebook=self.notebook, stream_now=self.timekeeper.now,
                           stream_began_at=STREAM_STARTED_AT, status_source=self.status_now, bookkeeper=self.bookkeeper)

    # --- the whole Printer -------------------------------------------------------------------------------------------------------------

    def turned(self, attempt, result) -> None:
        """The Conductor's `on_turn`: logs how the turn ended, then tells a watcher."""
        self.logger.info("turn over: %s", result.outcome.value)
        if self._on_turn is not None:
            self._on_turn(attempt, result)

    @cached_property
    def conductor(self) -> Conductor:
        return Conductor(self.floor, self.turn_taker, self.make_room, self.keyboard, on_attempt=self.note, on_turn=self.turned,
                         restart_room=not self.options.wav, conductor_settings=self.config.conductor, bookkeeper=self.bookkeeper)

    @cached_property
    def printer(self) -> Printer:
        """Every part, joined in a Conductor."""
        parts = {"whisper": self.whisper, "scorer": self.scorer, "bar_keeper": self.bar_keeper, "selector": self.selector, "writer": self.writer,
                 "formatter": self.formatter, "turn_taker": self.turn_taker, "notebook": self.notebook, "monitor": self.config.monitor,
                 "keyboard": self.keyboard, "mode": self.mode, "take_cutter": self.take_cutter, "warmer": self.warmer,
                 "boot_slipper": self.boot_slipper, "board": self.board, "poller": self.poller, "timekeeper": self.timekeeper}
        return Printer(self.conductor, self.history, self.site, self.terminal, self.floor, parts, self.board.lights, self.warmer, self.boot_slipper,
                       self.poller, self.bookkeeper)
