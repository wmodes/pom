#!/usr/bin/env python3
"""Play with the TerminalPort: format text for the typewriter and type it, paced, on a stand-in or the real machine.

By default it types on the terminal at the typewriter's pace (the ConsoleTerminal, which is the real port code writing
to the screen), so you can watch what the machine would do. With --port it types on a real serial port instead. The text
goes through the PortentFormatter first, with [printing] from config/pom.toml, and the pacing and serial settings are
[terminal] there. The formatter and the terminal are the Printer's own, asked of the PrinterBuilder. The code is
src/pom/printing/terminal_port.py; this is only a window onto it.

Usage:
    type_text_on_typewriter.py "The lighthouse keeper counts the ships that never arrive."
    printf 'Before.\n```\nA quote.\n```\nAfter.' | type_text_on_typewriter.py
    type_text_on_typewriter.py --fast "..."                  # no waiting
    type_text_on_typewriter.py --port /dev/ttyACM0 "Hello"   # the real machine (nothing is typed if the port will not open)
    type_text_on_typewriter.py --reply 30 "A portent."       # then wait 30 s for a line typed back (on the screen: type it and press Enter)
"""
import argparse
import dataclasses
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from pom.bookkeeper import Bookkeeper  # noqa: E402
from pom.config import Config  # noqa: E402
from pom.printer_builder import Options, PrinterBuilder  # noqa: E402

CONFIG_PATH = str(ROOT / "config" / "pom.toml")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("text", nargs="*", help="the text to type (read from stdin if omitted)")
    parser.add_argument("--port", help="a real serial port to type on, instead of the screen: auto, part of the terminal's USB name, or a device path (e.g. /dev/ttyACM0)")
    parser.add_argument("--fast", action="store_true", help="do not wait: no pacing and no wait at the end")
    parser.add_argument("--cps", type=float, help="characters a second (default: [terminal] in the config)")
    parser.add_argument("--reply", type=float, metavar="SECONDS", help="after typing, wait this long for a line typed back and show it")
    args = parser.parse_args()

    config = Config.load(CONFIG_PATH)
    bookkeeper = Bookkeeper(strict=config.strict)
    bookkeeper.set_up("WARNING", debug=config.debug)
    if args.fast:
        config.terminal = dataclasses.replace(config.terminal, characters_per_second=1e9, finish_wait_seconds=0.0)
    elif args.cps:
        config.terminal = dataclasses.replace(config.terminal, characters_per_second=args.cps)
    if args.port:
        config.terminal = dataclasses.replace(config.terminal, port=args.port)
    builder = PrinterBuilder(config, Options(config_path=CONFIG_PATH, terminal="serial" if args.port else "console"), root=ROOT, bookkeeper=bookkeeper)

    raw = " ".join(args.text) if args.text else sys.stdin.read()
    text = builder.formatter.format(raw)
    typewriter = builder.terminal

    done = typewriter.transmit(text)
    print(f"--- {done.characters} characters in {done.seconds:.1f} s, {'all typed' if done.completed else 'NOT all typed'}")
    if args.reply:
        typed_back = typewriter.read_line(args.reply)
        print(f"--- typed back: {typed_back!r}" if typed_back is not None else "--- nothing typed back")
    # the terminal's problems were logged as they happened, above


if __name__ == "__main__":
    main()
