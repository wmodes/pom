#!/usr/bin/env python3
"""Play with the PortentFormatter: see how text is made printable and wrapped for the typewriter.

Give it text as arguments, or pipe text in, and it prints the text as the typewriter would type it, between a ruler and
the line's right edge, with the line count. The line width and quote indent come from [printing] in config/pom.toml
(paper, margins and print wheel pitch) unless you give them. The formatting is src/pom/printing/portent_formatter.py;
this is only a window onto it.

Usage:
    format_text_for_typewriter.py "The lighthouse keeper counts the ships that never arrive, and writes them down."
    printf 'Before.\n```\nA quoted line.\n```\nAfter.' | format_text_for_typewriter.py
    format_text_for_typewriter.py --cpi 12 "..."       # a 12 pitch wheel: 78 characters on 8.5 inch paper with 1 inch margins
    format_text_for_typewriter.py --width 40 --indent 6 "..."
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))

from pom.config import COMPOSED_CHARACTERS, Config, PrintingSettings  # noqa: E402
from pom.log_setup import configure_logging  # noqa: E402
from pom.printing import PortentFormatter  # noqa: E402

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "config", "pom.toml")


def ruler(width: int) -> str:
    """A line of `width` characters, a tick every ten, to read the line's length against."""
    return "".join("|" if (i + 1) % 10 == 0 else "-" for i in range(width))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("text", nargs="*", help="the text to format (read from stdin if omitted)")
    parser.add_argument("--width", type=int, help="characters per line (default: worked out from [printing] in the config)")
    parser.add_argument("--cpi", type=float, help="the wheel's characters per inch, instead of the config's")
    parser.add_argument("--indent", type=int, help="spaces to indent a quotation (default: from the config)")
    parser.add_argument("--feeds", type=int, help="line feeds after the portent (default: from the config)")
    parser.add_argument("--keep", help="the composed characters to type as they are, for example '<>' (default: from the config)")
    parser.add_argument("--ascii-wheel", action="store_true", help="the wheel has all of ASCII: nothing is swapped")
    args = parser.parse_args()

    configure_logging("WARNING")
    printing = Config.load(CONFIG_PATH).printing
    if args.cpi:
        printing = PrintingSettings(printing.paper_width_inches, printing.margin_inches, args.cpi, printing.quote_indent)
    width = args.width or printing.line_width
    indent = printing.quote_indent if args.indent is None else args.indent

    raw = " ".join(args.text) if args.text else sys.stdin.read()
    feeds = printing.linefeeds_after_portent if args.feeds is None else args.feeds
    keep = printing.composed_characters_kept if args.keep is None else args.keep
    if args.ascii_wheel:
        keep = COMPOSED_CHARACTERS
    typed = PortentFormatter(width, indent, feeds, keep).format(raw)
    body = typed.rstrip("\n")
    lines = body.split("\n") if body else []
    print(ruler(width))
    if body:
        print(body)
        print("\n" * (len(typed) - len(body) - 1), end="")     # the rest of the line feeds, as the paper would move
    print(ruler(width))
    print(f"{len(lines)} lines, longest {max((len(line) for line in lines), default=0)} of {width} characters, "
          f"quotes indented {indent}, {len(typed) - len(body)} line feeds after")


if __name__ == "__main__":
    main()
