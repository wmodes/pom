#!/usr/bin/env python3
"""Per-utterance draw probabilities that balance the corpus by register.

The oracle draws a few exemplar chunks at random, and the corpus is lopsided: a
register with 30,000 short chunks would swamp one with 600. The weights below give
every chunk a chance of being drawn so that, over many draws, each register gets its
*target share* while any combination of draws stays possible (five obituaries in a row
is rare, never impossible).

These odds are computed from the corpus as it stands, at load time, and never stored in
the chunk files: a chunk's chance depends on its register's total, so only that
register's numbers move when a chunk is added. A sampler can use the same math as two
steps per draw (a register by its share, then a chunk within it by source weight);
the odds are identical to one weighted pool and every combination is still possible.

Conceptually: every utterance goes in one pot. A register with few utterances
gets a heavier weight per utterance, so a tile from a small pot is more likely
than a tile from a big one. The target shares are the thumb on the scale:
edit corpus/register-targets.csv (relative numbers, normalized here), re-run, and
read the table.

    probability(chunk) = register_share * source_weight / register_total

where register_total is the sum of source_weight over every utterance in that
register, and source_weight is the source's entry under its register in weights.json (absent = 1.0). Within
a register, then, a bigger source still draws more often, nudged by its weight.

All the knobs are in one file, corpus/chunks/weights.json: a tree of registers, each with a
relative share and a list of its sources with a weight each. Nothing else depends on those numbers, so editing
them never means touching a chunk. This script needs only chunks/ and that file, which is
all the Pi has to carry.

Usage:
    draw_weights.py                  print the expected shares and per-chunk odds
    draw_weights.py --weights FILE   use another weights file
"""
import argparse
import json
import os
import sys

CORPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "corpus")
DRAWS = 5      # the exemplar count used for the "all from one register" odds


def load_weights(path):
    """Returns (registers, sources): relative register shares and per-source weights.

    The file is a tree, register -> {share, sources: {id: weight}}. A source that is not
    listed counts 1.0.
    """
    with open(path, encoding="utf-8") as handle:
        doc = json.load(handle)
    registers, sources = {}, {}
    for name, entry in doc.get("registers", {}).items():
        registers[name] = float(entry.get("share", 1.0))
        for source, weight in entry.get("sources", {}).items():
            sources[source] = float(weight)
    return registers, sources


def count_chunks(path):
    """Counts the chunks (one JSON line each) in a chunks/ file."""
    with open(path, encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def read_sources(source_weights):
    """Returns one dict per chunks/<register>/<id>.jsonl file, with its multiplier from weights.json.

    The register comes from the folder name, so the manifest is not needed at run time.
    A weight listed for a source that has no chunk file is an error (usually a typo).
    """
    sources = []
    directory = os.path.join(CORPUS, "chunks")
    for register in sorted(os.listdir(directory)):
        folder = os.path.join(directory, register)
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.endswith(".jsonl"):
                source = name[:-len(".jsonl")]
                sources.append({"id": source, "register": register,
                                "weight": source_weights.get(source, 1.0),
                                "count": count_chunks(os.path.join(folder, name))})
    unknown = set(source_weights) - {s["id"] for s in sources}
    if unknown:
        sys.exit(f"weights.json lists sources with no chunks: {', '.join(sorted(unknown))}")
    return sources


def unlisted(sources, registers, source_weights):
    """Returns warnings for registers and sources the weights file does not mention.

    They still work (each counts 1.0), but a source missing from the file usually means it
    was added after the file was last edited, so the warning is how that drift shows up.
    """
    notes = []
    missing_registers = sorted({s["register"] for s in sources} - set(registers))
    if missing_registers:
        notes.append(f"{len(missing_registers)} register(s) not in weights.json, counted 1.0: "
                     f"{', '.join(missing_registers)}")
    missing_sources = sorted(s["id"] for s in sources if s["id"] not in source_weights)
    if missing_sources:
        notes.append(f"{len(missing_sources)} source(s) not in weights.json, counted 1.0: "
                     f"{', '.join(missing_sources)}")
    return notes


def draw_probabilities(sources, targets):
    """Computes each source's per-utterance draw probability.

    Args:
        sources: Dicts with `register`, `weight` and `count`, as from read_sources().
        targets: {register: relative target share}; a register left out counts 1.0.

    Returns:
        (per_chunk, shares): per_chunk maps source id to the chance of drawing
        any one of its chunks; shares maps register to its normalized target share.
    """
    present = {s["register"] for s in sources}
    targets = {r: targets.get(r, 1.0) for r in present}      # an unlisted register counts 1.0
    total = sum(targets.values())
    shares = {r: targets[r] / total for r in present}
    register_total = {}
    for s in sources:
        register_total[s["register"]] = register_total.get(s["register"], 0.0) + s["weight"] * s["count"]
    per_chunk = {s["id"]: shares[s["register"]] * s["weight"] / register_total[s["register"]]
                     for s in sources}
    return per_chunk, shares


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--weights", default=os.path.join(CORPUS, "chunks", "weights.json"))
    args = parser.parse_args()

    registers, source_weights = load_weights(args.weights)
    sources = read_sources(source_weights)
    for note in unlisted(sources, registers, source_weights):
        print(f"warning: {note}", file=sys.stderr)
    per_chunk, shares = draw_probabilities(sources, registers)
    total_chunks = sum(s["count"] for s in sources)
    average = 1.0 / total_chunks

    print(f"{len(sources)} sources, {total_chunks} chunks; "
          f"an unweighted draw would give each chunk a 1 in {total_chunks} chance.\n")
    print(f"{'register':<13}{'target':>8}{'chunks':>12}{'odds vs flat':>14}"
          f"{'all ' + str(DRAWS) + ' alike':>14}")
    by_register = {}
    for s in sources:
        by_register.setdefault(s["register"], []).append(s)
    for register in sorted(shares, key=lambda r: -shares[r]):
        members = by_register[register]
        count = sum(s["count"] for s in members)
        mean_probability = shares[register] / count
        print(f"{register:<13}{shares[register]:>7.1%}{count:>12}"
              f"{mean_probability / average:>13.1f}x{shares[register] ** DRAWS:>14.1e}")
    print("\n'odds vs flat': how much likelier an average utterance of that register is than "
          "in a flat draw.\n'all 5 alike': chance that one pull of 5 comes entirely from that register.")


if __name__ == "__main__":
    main()
