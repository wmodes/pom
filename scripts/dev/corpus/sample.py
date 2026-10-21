#!/usr/bin/env python3
"""Play with the Sampler: print pulls of random chunks, or measure the long-run register shares.

It builds the real Sampler from corpus/chunks/, corpus/chunks/weights.json and the sample range in
config/pom.toml (plus config/pom.local.toml), so what you see is what the oracle's exemplar draw
will do. Try a different weights file with --weights to see the effect before editing the real one.

Usage:
    sample.py                      one pull, sized by the config (3 to 5 chunks)
    sample.py --pulls 5            five pulls
    sample.py --count 8            pulls of exactly 8 chunks
    sample.py --seed 7             repeatable draws
    sample.py --stats 5000         draw 5000 pulls quietly and compare each register's share of
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
from pom.corpus import ChunkStore, Sampler, Weights  # noqa: E402
from pom.logs import configure_logging  # noqa: E402


def show_pulls(sampler, pulls, count):
    """Prints each pull: a header, then every chunk with its register and source."""
    for number in range(1, pulls + 1):
        chunks = sampler.draw(count)
        print(f"--- pull {number}: {len(chunks)} chunk(s)")
        for chunk in chunks:
            print(f"[{chunk.register} / {chunk.source}]")
            print(chunk.text)
            print()


def show_stats(sampler, pulls, count):
    """Draws many pulls without printing them, then compares each register's share to its target."""
    for _ in range(pulls):
        sampler.draw(count)
    counts = sampler.draw_counts()
    total = sum(counts.values())
    shares = sampler.register_shares()
    print(f"{pulls} pulls, {total} chunks drawn\n")
    print(f"{'register':<14}{'target':>8}{'drawn':>9}{'actual':>9}")
    for register in sorted(shares, key=lambda r: -shares[r]):
        actual = counts[register] / total if total else 0.0
        print(f"{register:<14}{shares[register]:>7.1%}{counts[register]:>9}{actual:>8.1%}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--pulls", type=int, default=1, help="how many pulls to print (default 1)")
    parser.add_argument("--count", type=int, default=None, help="chunks per pull (default: the config's range)")
    parser.add_argument("--seed", type=int, default=None, help="seed the random generator")
    parser.add_argument("--stats", type=int, metavar="PULLS", default=None,
                        help="draw this many pulls quietly and print the register shares")
    parser.add_argument("--weights", default=os.path.join(ROOT, "corpus", "chunks", "weights.json"))
    parser.add_argument("-v", "--verbose", action="store_true", help="use the config's log level")
    args = parser.parse_args()

    config = Config.load(os.path.join(ROOT, "config", "pom.toml"))
    configure_logging(config.log_level if args.verbose else "WARNING")
    store = ChunkStore.load(os.path.join(ROOT, "corpus", "chunks"))
    sampler = Sampler(store, Weights.from_file(args.weights), random.Random(args.seed),
                      samples=config.samples)
    if not sampler.ready:
        sys.exit("nothing to draw from; see the messages above")
    if args.stats is not None:
        show_stats(sampler, args.stats, args.count)
    else:
        show_pulls(sampler, args.pulls, args.count)


if __name__ == "__main__":
    main()
