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
  * EVERYTHING IS BUILT BY THE PrinterBuilder (pom.printer_builder), which contacts no server, so the whole wiring can be built and inspected
    in a test, and the testbenches build their parts the same way. `build_printer` is the builder's whole Printer. `main` only parses the
    options, builds, installs the signal handlers (SIGTERM from systemd and ctrl-C both stop it cleanly) and runs.
  * NOTHING SAID IS KEPT. The monitoring site shows what the config lets it (`show_hinges`) from memory; nothing is logged or written.
    The log holds counts, labels and seconds.
  * The microphone can be unplugged: the room is built again by the Conductor each time it ends. A recording ends the run instead.
"""
import argparse
import signal
import sys
from pathlib import Path
from typing import Optional

from pom.bookkeeper.bookkeeper import Bookkeeper
from pom.bookkeeper.log_muter import LogMuter
from pom.config import Config
from pom.printer_builder import DEFAULT_CONFIG, ROOT, Options, Printer, PrinterBuilder

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module


def build_printer(config: Config, options: Optional[Options] = None, root: Path = ROOT, bookkeeper: Optional[Bookkeeper] = None) -> Printer:
    """Builds every part from `config` and `options` and joins them in a Conductor (the PrinterBuilder's whole Printer). Contacts no server and opens no
    port or microphone."""
    return PrinterBuilder(config, options, root=root, bookkeeper=bookkeeper).printer


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
    bookkeeper = Bookkeeper(LogMuter(config.logmute_after_repeats, config.logmute_remind_every_repeats, config.logmute_quiet_seconds),
                            strict=config.strict)
    bookkeeper.set_up(config.log_level, debug=config.debug, local_logs=config.local_logs)
    logger = bookkeeper.logger_for(__name__, debug=DEBUG)
    for line in config.problems.lines():               # the config was read before the log existed, so its problems are logged now
        logger.warning("config: %s", line)
    printer = build_printer(config, options, bookkeeper=bookkeeper)

    def stop_cleanly(signum, frame) -> None:
        logger.info("signal %s: stopping", signum)
        printer.stop()
    signal.signal(signal.SIGTERM, stop_cleanly)
    signal.signal(signal.SIGINT, stop_cleanly)
    logger.info("the Printer starts (terminal: %s)", printer.parts["mode"])
    printer.run()                                    # every problem was logged when it happened; nothing is logged a second time here
    return 0


if __name__ == "__main__":
    sys.exit(main())
