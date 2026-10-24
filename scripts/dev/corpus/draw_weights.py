#!/usr/bin/env python3
"""Print the draw odds the Sampler will use, so a weights.json edit can be checked at a glance.

The oracle draws a few exemplar chunks at random. The corpus is lopsided, so the Sampler weights
every chunk to give each register its target share over many draws (see
src/pom/corpus/sampler.py for the math). This script builds the real Sampler from corpus/chunks/
and corpus/chunks/weights.json and reports, per register, its target share, its chunk count, how
much likelier an average chunk of that register is than in a flat draw, and the chance that one
pull of five comes entirely from that register.

The logic lives in the pom package; this is only the report. Warnings (an unlisted source, a
weight for a source with no chunks) are logged to stdout by the package.

Usage:
    draw_weights.py                  print the odds
    draw_weights.py --weights FILE   use another weights file
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "src"))   # scripts may import pom

from pom.corpus import ChunkStore, Sampler, Weights  # noqa: E402
from pom.log_setup import configure_logging  # noqa: E402

CORPUS = os.path.join(HERE, "..", "..", "..", "corpus")
PULL = 5      # the pull size used for the "all alike" column


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--weights", default=os.path.join(CORPUS, "chunks", "weights.json"))
    args = parser.parse_args()

    configure_logging("WARNING")
    store = ChunkStore.load(os.path.join(CORPUS, "chunks"))
    sampler = Sampler(store, Weights.from_file(args.weights))
    if not sampler.ready:
        sys.exit("nothing to draw from; see the messages above")
    shares = sampler.register_shares()
    total = sum(store.count(r) for r in shares)

    print(f"{len(store.layout()) and sum(len(s) for s in store.layout().values())} sources, "
          f"{total} chunks; a flat draw would give each chunk a 1 in {total} chance.\n")
    print(f"{'register':<13}{'target':>8}{'chunks':>12}{'odds vs flat':>14}{'all ' + str(PULL) + ' alike':>14}")
    for register in sorted(shares, key=lambda r: -shares[r]):
        count = store.count(register)
        print(f"{register:<13}{shares[register]:>7.1%}{count:>12}"
              f"{shares[register] / count * total:>13.1f}x{shares[register] ** PULL:>14.1e}")
    print("\n'odds vs flat': how much likelier an average chunk of that register is than in a flat draw."
          f"\n'all {PULL} alike': chance that one pull of {PULL} comes entirely from that register.")


if __name__ == "__main__":
    main()
