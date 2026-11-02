#!/usr/bin/env python3
"""Play with the TerminalPort: format text for the typewriter and type it, paced, on a stand-in or the real machine.

By default it types on the terminal at the typewriter's pace (the ConsoleTerminal, which is the real port code writing
to the screen), so you can watch what the machine would do. With --port it types on a real serial port instead. The text
goes through the PortentFormatter first, with [printing] from config/pom.toml, and the pacing and serial settings are
[terminal] there. The code is src/pom/output/terminal_port.py; this is only a window onto it.

Usage:
    type_text_on_typewriter.py "The lighthouse keeper counts the ships that never arrive."
    printf 'Before.\n```\nA quote.\n```\nAfter.' | type_text_on_typewriter.py
    type_text_on_typewriter.py --fast "..."                  # no waiting
    type_text_on_typewriter.py --port /dev/ttyACM0 "Hello"   # the real machine (nothing is typed if the port will not open)
    type_text_on_typewriter.py --reply 30 "A portent."       # then wait 30 s for a line typed back (on the screen: type it and press Enter)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))

from pom.config import Config  # noqa: E402
from pom.log_setup import configure_logging  # noqa: E402
from pom.output import ConsoleTerminal, PortentFormatter, TerminalPort  # noqa: E402

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "config", "pom.toml")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("text", nargs="*", help="the text to type (read from stdin if omitted)")
    parser.add_argument("--port", help="a real serial port to type on, instead of the screen (e.g. /dev/ttyACM0)")
    parser.add_argument("--fast", action="store_true", help="do not wait: no pacing and no wait at the end")
    parser.add_argument("--cps", type=float, help="characters a second (default: [terminal] in the config)")
    parser.add_argument("--reply", type=float, metavar="SECONDS", help="after typing, wait this long for a line typed back and show it")
    args = parser.parse_args()

    configure_logging("WARNING")
    config = Config.load(CONFIG_PATH)
    printing, terminal = config.printing, config.terminal
    cps = 1e9 if args.fast else (args.cps or terminal.characters_per_second)
    finish_wait = 0.0 if args.fast else terminal.finish_wait_seconds

    raw = " ".join(args.text) if args.text else sys.stdin.read()
    text = PortentFormatter(printing.line_width, printing.quote_indent, printing.linefeeds_after_portent,
                               printing.composed_characters_kept).format(raw)
    if args.port:
        typewriter = TerminalPort(args.port, terminal.baud, terminal.flow_control, terminal.end_of_line, cps, finish_wait,
                                    terminal.echoplex, terminal.type_during_printing)
    else:
        typewriter = ConsoleTerminal(characters_per_second=cps, finish_wait_seconds=finish_wait,
                                       type_during_printing=terminal.type_during_printing)

    done = typewriter.transmit(text)
    print(f"--- {done.characters} characters in {done.seconds:.1f} s, {'all typed' if done.completed else 'NOT all typed'}")
    if args.reply:
        typed_back = typewriter.read_line(args.reply)
        print(f"--- typed back: {typed_back!r}" if typed_back is not None else "--- nothing typed back")
    for line in typewriter.report.lines():
        print(f"    {line}")


if __name__ == "__main__":
    main()
