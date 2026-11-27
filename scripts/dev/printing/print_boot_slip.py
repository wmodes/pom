#!/usr/bin/env python3
"""Play with the BootSlipper: type the boot slip the way the Printer does when it comes online, on the screen or a real port.

It asks the PrinterBuilder for the Printer's own BootSlipper (with its FloorKeeper, terminal, PortentFormatter and AddressFinder), built from
config/pom.toml ([boot_slip], [printing], [terminal], [conductor]), and runs it once, in the foreground, so you can watch what the typewriter would
type: the slip's words with this machine's real address filled in. The code is src/pom/conductor/boot_slipper.py; this is only a window onto it.

Usage:
    print_boot_slip.py                       # types the slip on the screen at the typewriter's pace
    print_boot_slip.py --fast                # no waiting
    print_boot_slip.py --no-network          # as if the machine had no address (the slip shows 127.0.0.1)
    print_boot_slip.py --address 10.41.0.1   # as if the machine had this address (repeat for more than one)
    print_boot_slip.py --port /dev/ttyACM0   # on the real machine (waits for it to be online)
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


class FixedAddresses:
    """Stands in for the AddressFinder: the addresses given on the command line (none for --no-network)."""

    def __init__(self, addresses: list):
        self.addresses = list(addresses)

    def find(self) -> list:
        return list(self.addresses)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", default=str(ROOT / "config" / "pom.toml"), help="the config file to read")
    parser.add_argument("--port", help="a real serial port to type on, instead of the screen: auto, part of the terminal's USB name, or a device path (e.g. /dev/ttyACM0)")
    parser.add_argument("--fast", action="store_true", help="do not wait: no pacing, no wait at the end, no waits before typing")
    parser.add_argument("--no-network", action="store_true", help="pretend the machine has no address")
    parser.add_argument("--address", action="append", help="pretend the machine has this address (repeat for more)")
    return parser


def make_builder(args, config: Config, bookkeeper: Bookkeeper = None) -> PrinterBuilder:
    """The PrinterBuilder for this run: the screen or the given port, and the command line's addresses and waits in place of the config's."""
    if args.fast:
        config.terminal = dataclasses.replace(config.terminal, characters_per_second=1e9, finish_wait_seconds=0.0)
        config.boot_slip = dataclasses.replace(config.boot_slip, address_grace_seconds=0.0)
        config.conductor = dataclasses.replace(config.conductor, check_seconds=0.0)   # no Conductor runs here, so no wait for it to shut the ears
    if args.port:
        config.terminal = dataclasses.replace(config.terminal, port=args.port)
    builder = PrinterBuilder(config, Options(config_path=args.config, terminal="serial" if args.port else "console"), root=ROOT, bookkeeper=bookkeeper)
    if args.no_network:
        builder.address_finder = FixedAddresses([])
    elif args.address:
        builder.address_finder = FixedAddresses(args.address)
    return builder


def main():
    args = make_parser().parse_args()
    bookkeeper = Bookkeeper()
    bookkeeper.set_up("INFO")
    config = Config.load(args.config)
    builder = make_builder(args, config, bookkeeper)
    slipper = builder.boot_slipper
    if slipper is None:
        sys.exit("the boot slip is off: [boot_slip] enabled = false in the config")

    print(f"--- addresses found: {builder.address_finder.find() or 'none'}")
    print("--- the slip:")
    typed = slipper.run()
    print(f"--- {'all typed' if typed else 'NOT typed'}")
    # the terminal's problems were logged as they happened, above


if __name__ == "__main__":
    main()
