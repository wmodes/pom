#!/usr/bin/env python3
"""Makes sample-large.txt, the long sample for the Pi benchmark.

Part of experiments/pi-benchmark. Draws sentences from the corpus (corpus/ready/<register>/*.txt),
the same number from every register, with a fixed seed so the file is the same every time. The
corpus is written text, not overheard speech, but for a load test what matters is that the
sentences are varied and of ordinary speech length (30 to 140 characters, one complete sentence).
The result is committed, so the Pi benchmark needs no corpus on the Pi.

Run from anywhere on the Mac:
    benchmark_pi_make_sample.py [--count 300] [--seed 7]

It uses only the standard library.
"""
import argparse
import random
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CORPUS_READY = HERE.parents[1] / "corpus" / "ready"
OUTPUT = HERE / "sample-large.txt"


def sentences_in(path: Path):
    """Complete sentences of speech length in one corpus file."""
    for paragraph in path.read_text(encoding="utf-8").splitlines():
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            if 30 <= len(sentence) <= 140 and sentence[0].isupper() and sentence[-1] in ".!?":
                yield sentence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--count", type=int, default=300, help="sentences in total (default 300)")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    registers = sorted(folder for folder in CORPUS_READY.iterdir() if folder.is_dir())
    if not registers:
        raise SystemExit(f"no registers found under {CORPUS_READY}")
    rng = random.Random(args.seed)
    per_register = max(1, args.count // len(registers))
    chosen = []
    for folder in registers:
        pool = sorted({s for path in sorted(folder.glob("*.txt")) for s in sentences_in(path)})
        chosen += rng.sample(pool, min(per_register, len(pool)))
    rng.shuffle(chosen)                      # mix the registers, as a room would
    OUTPUT.write_text("\n".join(chosen) + "\n", encoding="utf-8")
    print(f"wrote {len(chosen)} sentences from {len(registers)} registers to {OUTPUT.name}")


if __name__ == "__main__":
    main()
