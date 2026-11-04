#!/usr/bin/env python3
"""Play with the BootSlipper: type the boot slip the way the Printer does when it comes online, on the screen or a real port.

It builds the real BootSlipper, FloorKeeper, AddressFinder and PortentFormatter from config/pom.toml ([boot_slip], [printing], [terminal])
and runs it once, in the foreground, so you can watch what the typewriter would type: the slip's words with this machine's real address
filled in. The code is src/pom/conductor/boot_slipper.py; this is only a window onto it.

Usage:
    print_boot_slip.py                       # types the slip on the screen at the typewriter's pace
    print_boot_slip.py --fast                # no waiting
    print_boot_slip.py --no-network          # as if the machine had no address (the slip shows 127.0.0.1)
    print_boot_slip.py --address 10.41.0.1   # as if the machine had this address (repeat for more than one)
    print_boot_slip.py --port /dev/ttyACM0   # on the real machine (waits for it to be online)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))

from pom.config import Config  # noqa: E402
from pom.conductor import AddressFinder, BootSlipper, FloorKeeper  # noqa: E402
from pom.log_setup import configure_logging  # noqa: E402
from pom.printing import ConsoleTerminal, PortentFormatter, TerminalPort  # noqa: E402

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "config", "pom.toml")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", help="a real serial port to type on, instead of the screen (e.g. /dev/ttyACM0)")
    parser.add_argument("--fast", action="store_true", help="do not wait: no pacing, no wait at the end, no waits before typing")
    parser.add_argument("--no-network", action="store_true", help="pretend the machine has no address")
    parser.add_argument("--address", action="append", help="pretend the machine has this address (repeat for more)")
    args = parser.parse_args()

    configure_logging("INFO")
    config = Config.load(CONFIG_PATH)
    printing, terminal_settings, slip, conductor = config.printing, config.terminal, config.boot_slip, config.conductor
    cps = 1e9 if args.fast else terminal_settings.characters_per_second
    finish_wait = 0.0 if args.fast else terminal_settings.finish_wait_seconds

    if args.port:
        terminal = TerminalPort(args.port, terminal_settings.baud, terminal_settings.flow_control, terminal_settings.end_of_line, cps,
                                finish_wait, terminal_settings.echoplex, terminal_settings.type_during_printing)
    else:
        terminal = ConsoleTerminal(characters_per_second=cps, finish_wait_seconds=finish_wait,
                                   type_during_printing=terminal_settings.type_during_printing)
    formatter = PortentFormatter(printing.line_width, printing.quote_indent, printing.linefeeds_after_portent,
                                 printing.composed_characters_kept)
    floor = FloorKeeper(conductor.writing_seconds, conductor.printing_seconds, conductor.recovering_seconds)
    if args.no_network:
        find = lambda: []                                    # noqa: E731
    elif args.address:
        find = lambda: list(args.address)                    # noqa: E731
    else:
        find = AddressFinder().find
    ears_shut = 0.0 if args.fast else conductor.check_seconds + 1.0

    print(f"--- addresses found: {find() or 'none'}")
    slipper = BootSlipper(floor, terminal, formatter, slip.text, find, grace_seconds=0.0 if args.fast else slip.address_grace_seconds,
                          retry_seconds=slip.retry_seconds, ears_shut_seconds=ears_shut)
    print("--- the slip:")
    typed = slipper.run()
    print(f"--- {'all typed' if typed else 'NOT typed'}")
    for line in terminal.report.lines():
        print(f"    {line}")


if __name__ == "__main__":
    main()
