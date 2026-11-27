#!/usr/bin/env python3
"""Play with the whole Printer except the typewriter: a room (or a recording) goes in, portents come out on the screen.

This IS the Printer: it is built by the PrinterBuilder, the same wiring as `python -m pom`, and run by the real Conductor, so the floor, the
turns, the pause and skip-ahead, the boot slip and the cooldown all behave as they do on the Pi. The only differences are what this script
adds: the portents are typed on the screen (the console terminal) instead of the typewriter, each attempt is printed as it is heard, and,
with --mac-mic, a different microphone.

Like the Printer it does one thing at a time: while the oracle writes and the portent is typed it is not listening, and the sound that goes by
meanwhile is not heard. A recording keeps playing through a turn, so with `--speed 0` it reaches its end during the first turn (that portent
is still written and typed; then the run is over); use `--speed 1` to `--speed 6` to hear more of it.

Three servers must be running first (addresses are in config/pom.toml):
    whisper-server -m ggml-base.en.bin --vad -vm ggml-silero-v6.2.0.bin -vt 0.5 --port 8110 -t 4
    the scorer model's llama-server (scorer.url), and the oracle model's llama-server (oracle.url).
The oracle's can be started with:
    llama-server -hf unsloth/Qwen3.5-9B-GGUF:Q4_K_M --port 8099 --ctx-size 4096 -ngl 99 --jinja
(scripts/dev/pipeline/run_printer_with_servers.py starts all three.)

Nothing you say is kept. The words are shown here and nowhere else, so do not redirect this output to a file.

Usage:
    listening_to_portent.py --mac-mic                            # this Mac's system default input
    listening_to_portent.py --mac-mic "MacBook Pro Microphone"   # a particular input (ffmpeg -f avfoundation -list_devices true -i "")
    listening_to_portent.py                                      # the Pi's microphone (arecord, listening.device)
    listening_to_portent.py --wav meeting.wav --speed 6          # a 16 kHz mono recording, six times real time
    listening_to_portent.py --mac-mic --max-portents 1           # stop after the first portent
    listening_to_portent.py --mac-mic --monitor                  # also serve the monitoring site (see below)

With --monitor the Printer's own MonitorSite runs, as it does on the Pi: open the address it prints, with any user name and the password from
[monitor] in config/pom.local.toml (git ignores that file; the site will not start without a password). Without --monitor there is no site,
whatever `[monitor] enabled` says. The site stops when the run does.
"""
import argparse
import dataclasses
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from pom.bookkeeper import Bookkeeper                                                               # noqa: E402
from pom.conductor import TurnOutcome                                                               # noqa: E402
from pom.config import Config                                                                       # noqa: E402
from pom.printer_builder import Options, PrinterBuilder, answers                                    # noqa: E402
from pom.qualifying import Ruling                                                                   # noqa: E402

CONFIG_PATH = ROOT / "config" / "pom.toml"


def stamp(seconds: float) -> str:
    """A place in the audio stream as +hh:mm:ss."""
    whole = int(seconds)
    return f"+{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"


def standing(attempt) -> str:
    """Where a score stands, for the end of its line."""
    if attempt.ruling is Ruling.NO_MARK or attempt.bar is None:
        return "[no score]"
    return (f"[window {attempt.window_size} scores, bar {attempt.bar:.2f}" +
            ("; held" if attempt.ruling is Ruling.HELD else "") + "]")


def show_attempt(attempt) -> None:
    """One attempt, as it is heard: when, what kind, its score, who scored it, the words, and where the score stands."""
    when = stamp(attempt.heard_at)
    if attempt.score is None and attempt.ruling is None:
        print(f"\n[{when}] room tone, not scored: {attempt.text}", flush=True)
        return
    shown = f"{attempt.score:5.2f}" if attempt.score is not None else "  -  "
    print(f"\n[{when}] {attempt.kind:<6} {shown}  {attempt.scored_by.value:<16} {attempt.text}   {standing(attempt)}", flush=True)
    if attempt.is_hinge:
        print(f"\n  >>> HINGE: {attempt.text}")
        print("  >>> if it gets the floor, the oracle writes and the portent is typed below; the Printer is not listening meanwhile ...", flush=True)


def show_turn(result) -> None:
    """How a turn ended: numbers only (the portent itself was typed on the screen just above)."""
    print(f"\n  ({result.outcome.value}: {result.words} words, written in {result.writing_seconds:.1f} s"
          f"{f' after {result.write_attempts} tries' if result.write_attempts > 1 else ''})", flush=True)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", default=str(CONFIG_PATH), help="the config file to read")
    parser.add_argument("--wav", help="play this 16 kHz mono recording instead of listening to a microphone")
    parser.add_argument("--speed", type=float, default=1.0, help="recording speed: 1 is real time, 0 is as fast as possible")
    parser.add_argument("--mac-mic", nargs="?", const="default", metavar="DEVICE",
                        help="listen to this Mac's microphone through ffmpeg: the system default input, or the named one")
    parser.add_argument("--max-portents", type=int, default=0, help="stop after this many portents (0 means keep going)")
    parser.add_argument("--monitor", action="store_true", help="also serve the monitoring site, as the Printer does")
    return parser


def make_builder(args, config: Config, bookkeeper: Bookkeeper, on_attempt, on_turn) -> PrinterBuilder:
    """The Printer's own builder for this run: the portents typed on the screen, the site only with --monitor, the watchers handed in."""
    config.monitor = dataclasses.replace(config.monitor, enabled=args.monitor)       # the site runs only when asked for here
    options = Options(config_path=args.config, wav=args.wav, speed=args.speed, mac_mic=args.mac_mic, terminal="console")
    return PrinterBuilder(config, options, root=ROOT, bookkeeper=bookkeeper, on_attempt=on_attempt, on_turn=on_turn)


def main() -> None:
    args = make_parser().parse_args()
    if args.wav and args.mac_mic:
        sys.exit("give --wav or --mac-mic, not both")

    bookkeeper = Bookkeeper()
    bookkeeper.set_up("WARNING")
    config = Config.load(args.config)
    if args.monitor and not config.monitor.password:
        sys.exit("--monitor needs a password: put  password = \"...\"  under [monitor] in config/pom.local.toml (git ignores that file)")
    listening, scoring, oracle = config.listening, config.scorer, config.oracle

    portents = []
    printer = None

    def turned(attempt, result) -> None:
        show_turn(result)
        if result.outcome is TurnOutcome.PRINTED:
            portents.append(result)
            if args.max_portents and len(portents) >= args.max_portents and printer is not None:
                printer.stop()

    builder = make_builder(args, config, bookkeeper, show_attempt, turned)
    if not builder.whisper.ping():
        sys.exit(f"the whisper-server is not answering at {listening.whisper_url}; see the top of this file for how to start it")
    if not answers(scoring.url):
        sys.exit(f"the scorer model's llama-server is not answering at {scoring.url}")
    if not answers(oracle.url):
        sys.exit(f"the oracle model's llama-server is not answering at {oracle.url}; see the top of this file for how to start it")
    if not builder.selector.ready:
        sys.exit("nothing to draw exemplars from; see the messages above")

    printer = builder.printer
    where = (f"the recording {args.wav} at speed {args.speed:g}" if args.wav else
             f"this Mac's microphone ({args.mac_mic})" if args.mac_mic is not None else f"the microphone ({listening.device})")
    print("listening_to_portent: what you say is shown here and kept nowhere. Do not redirect this output to a file.")
    print(f"  listening to {where}; whisper at {listening.whisper_url}; scorer at {scoring.url}; oracle at {oracle.url}")
    print(f"  qualifying ({config.qualifying.algorithm}): a score above {builder.bar_keeper.describe()}")
    print(f"  room tone {'is' if listening.score_room_tone else 'is not'} scored")
    if printer.site is not None:
        monitor = config.monitor
        print(f"  monitoring site: port {monitor.port} on {'this machine' if monitor.host == '0.0.0.0' else monitor.host}  "
              f"(any user name; the password is [monitor] password; hinges {'shown' if monitor.show_hinges else 'hidden'})")
    print("  Listening (the boot slip is typed first, as on the Pi). ctrl-C quits.", flush=True)

    def stop_cleanly(signum, frame) -> None:
        printer.stop()
    signal.signal(signal.SIGINT, stop_cleanly)
    signal.signal(signal.SIGTERM, stop_cleanly)
    printer.run()                                       # every part's problems are logged as they happen
    print(f"\n  the run is over: {len(portents)} portent{'' if len(portents) == 1 else 's'} typed", flush=True)


if __name__ == "__main__":
    main()
