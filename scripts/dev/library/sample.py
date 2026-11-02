#!/usr/bin/env python3
"""Play with the Bibliomancer: print pulls of random passages, or measure the long-run shelf shares.

It builds the real Bibliomancer from library/passages/, library/passages/circulation.json and the exemplar range in
config/pom.toml (plus config/pom.local.toml), so what you see is what the oracle's exemplar draw
will do. Try a different weights file with --weights to see the effect before editing the real one.

Usage:
    sample.py                      one pull, sized by the config (3 to 5 passages)
    sample.py --pulls 5            five pulls
    sample.py --count 8            pulls of exactly 8 passages
    sample.py --seed 7             repeatable draws
    sample.py --stats 5000         draw 5000 pulls quietly and compare each shelf's share of
                                   draws with its target
    sample.py --weights my.json    use another weights file
    sample.py -v                   show the config's log level (the default shows warnings only)
"""
import argparse
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "..", "..")
sys.path.insert(0, os.path.join(ROOT, "src"))    # scripts may import pom

from pom.config import Config  # noqa: E402
from pom.library import Catalog, Circulation, Bibliomancer  # noqa: E402
from pom.log_setup import configure_logging  # noqa: E402


def show_pulls(selector, pulls, count):
    """Prints each pull: a header, then every passage with its shelf and holding."""
    for number in range(1, pulls + 1):
        passages = selector.draw(count)
        print(f"--- pull {number}: {len(passages)} passage(s)")
        for passage in passages:
            print(f"[{passage.shelf} / {passage.holding}]")
            print(passage.text)
            print()


def show_stats(selector, pulls, count):
    """Draws many pulls without printing them, then compares each shelf's share to its target."""
    for _ in range(pulls):
        selector.draw(count)
    counts = selector.draw_counts()
    total = sum(counts.values())
    shares = selector.shelf_shares()
    print(f"{pulls} pulls, {total} passages drawn\n")
    print(f"{'shelf':<14}{'target':>8}{'drawn':>9}{'actual':>9}")
    for shelf in sorted(shares, key=lambda r: -shares[r]):
        actual = counts[shelf] / total if total else 0.0
        print(f"{shelf:<14}{shares[shelf]:>7.1%}{counts[shelf]:>9}{actual:>8.1%}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--pulls", type=int, default=1, help="how many pulls to print (default 1)")
    parser.add_argument("--count", type=int, default=None, help="passages per pull (default: the config's range)")
    parser.add_argument("--seed", type=int, default=None, help="seed the random generator")
    parser.add_argument("--stats", type=int, metavar="PULLS", default=None,
                        help="draw this many pulls quietly and print the shelf shares")
    parser.add_argument("--weights", default=os.path.join(ROOT, "library", "passages", "circulation.json"))
    parser.add_argument("-v", "--verbose", action="store_true", help="use the config's log level")
    args = parser.parse_args()

    config = Config.load(os.path.join(ROOT, "config", "pom.toml"))
    configure_logging(config.log_level if args.verbose else "WARNING")
    store = Catalog.load(os.path.join(ROOT, "library", "passages"))
    selector = Bibliomancer(store, Circulation.from_file(args.weights), random.Random(args.seed),
                      exemplar_range=config.exemplar_range)
    if not selector.ready:
        sys.exit("nothing to draw from; see the messages above")
    if args.stats is not None:
        show_stats(selector, args.stats, args.count)
    else:
        show_pulls(selector, args.pulls, args.count)


if __name__ == "__main__":
    main()
