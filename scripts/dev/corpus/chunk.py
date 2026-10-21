#!/usr/bin/env python3
"""Turn ready/ utterances into chunks: the units both voice tracks use.

ready/ holds clean utterances, one paragraph each. A *chunk* is an utterance sized
for the oracle's output (usually 15 to 50 words, at most 150) and carrying what a
consumer needs: where it came from, its register, and its chance of being drawn.

    Track A draws 3 to 5 chunks at random as register exemplars.
    Track B trains on the same chunks, using the same draw probabilities as sampling odds.

Rules, in order:
  1. Split. A utterance over --max-words (150) is cut at sentence breaks (prose) or line
     breaks (verse and wrapped text) into near-equal pieces, never mid-sentence unless one
     sentence alone is over the limit, then at clause breaks, then at word boundaries.
  2. Merge (off by default). With --min-words N, short utterances join the next one in
     the same source until they reach N words, without crossing --max-words.
  3. Deduplicate. An exact repeat of a chunk anywhere in the corpus is dropped; the kept
     copy records how many times it occurred.

A chunk holds only facts about itself (source, register, text). How likely it is to be
drawn depends on the whole corpus and on the weights in chunks/weights.json, so that is
computed at load time by draw_weights.py and never stored here: adding one utterance, or
changing a weight, touches no chunk file.

Output is deterministic: one JSONL file per source under corpus/chunks/<register>/<id>.jsonl.
Re-run after any change to ready/ or the manifest.

Usage:
    chunk.py                          write corpus/chunks/ and print a report
    chunk.py --max-words 200          change the ceiling
    chunk.py --min-words 15           merge short utterances forward
    chunk.py --check                  report only; write nothing
"""
import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import draw_weights  # noqa: E402  (shares the corpus location and the weight math)

CORPUS = draw_weights.CORPUS
READY = os.path.join(CORPUS, "ready")
OUT = os.path.join(CORPUS, "chunks")

# Sentence ends: terminal punctuation (optionally closed by a quote or bracket), then a
# capital, quote or digit. Abbreviations are rejoined afterwards.
SENTENCE_BREAK = re.compile(
    r"""(?:(?<=[.!?])|(?<=[.!?]["')\]]))\s+(?=["'(\[]?[A-Z0-9])""")
ABBREVIATION = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|St|Mt|No|Co|Rev|Hon|Gen|Col|Capt|Lieut|Sir|vs|etc|viz|cf|[A-Z])\.$")
CLAUSE_BREAK = re.compile(r"(?<=[;:])\s+|(?<=,)\s+")


def words(text):
    """Number of whitespace-separated words."""
    return len(text.split())


def split_sentences(text):
    """Splits prose into sentences, rejoining pieces cut after an abbreviation or initial."""
    pieces = SENTENCE_BREAK.split(text)
    sentences = []
    for piece in pieces:
        if sentences and ABBREVIATION.search(sentences[-1]):
            sentences[-1] += " " + piece
        else:
            sentences.append(piece)
    return sentences


def explode(unit, limit):
    """Breaks one over-long unit into smaller ones: at clauses, then at word boundaries."""
    if words(unit) <= limit:
        return [unit]
    clauses = [c for c in CLAUSE_BREAK.split(unit) if c.strip()]
    if len(clauses) > 1:
        return [piece for clause in clauses for piece in explode(clause, limit)]
    tokens = unit.split()
    return [" ".join(tokens[i:i + limit]) for i in range(0, len(tokens), limit)]


def cut_evenly(units, limit, joiner):
    """Groups consecutive units into near-equal pieces, each within `limit` words.

    Tries k = ceil(total / limit) pieces, cutting at the unit boundary nearest each even
    share of the total, which avoids a tiny leftover tail. If any piece still comes out
    over the limit (a few very uneven units), falls back to filling greedily.
    """
    sizes = [words(u) for u in units]
    total = sum(sizes)
    k = math.ceil(total / limit)
    cuts, running, boundary = [], 0, {}
    for index, size in enumerate(sizes[:-1], start=1):
        running += size
        boundary[index] = running
    for j in range(1, k):
        goal = j * total / k
        cuts.append(min(boundary, key=lambda i: (abs(boundary[i] - goal), i)))
    cuts = sorted(set(cuts))
    edges = [0] + cuts + [len(units)]
    groups = [units[a:b] for a, b in zip(edges, edges[1:]) if b > a]
    if all(sum(words(u) for u in g) <= limit for g in groups):
        return [joiner.join(g) for g in groups]
    pieces, current, size = [], [], 0
    for unit, n in zip(units, sizes):
        if current and (size + n > limit or size >= total / k):
            pieces.append(joiner.join(current))
            current, size = [], 0
        current.append(unit)
        size += n
    if current:
        pieces.append(joiner.join(current))
    return pieces


def split_utterance(text, limit):
    """Cuts an utterance over `limit` words into near-equal pieces at natural breaks.

    Units are lines when the text has line breaks (verse, wrapped ads), otherwise
    sentences; a single over-long unit is first broken at clauses, then at words.
    Returns (pieces, piece_count), where piece_count is None if no split was needed.
    """
    if words(text) <= limit:
        return [text], None
    by_lines = "\n" in text
    units = text.split("\n") if by_lines else split_sentences(text)
    units = [u for unit in units if unit.strip() for u in explode(unit, limit)]
    pieces = [p for p in cut_evenly(units, limit, "\n" if by_lines else " ") if p.strip()]
    return pieces, len(pieces)


def merge_short(texts, minimum, limit):
    """Joins each utterance under `minimum` words to the ones after it (blank-line separated)."""
    if minimum <= 0:
        return texts
    merged, buffer = [], ""
    for text in texts:
        candidate = f"{buffer}\n\n{text}" if buffer else text
        if buffer and words(candidate) > limit:
            merged.append(buffer)
            candidate = text
        buffer = candidate
        if words(buffer) >= minimum:
            merged.append(buffer)
            buffer = ""
    if buffer:
        merged.append(buffer)
    return merged


def read_utterances(path):
    """The utterances (blank-line-separated paragraphs) of a ready/ file."""
    with open(path, encoding="utf-8") as handle:
        return [p for p in re.split(r"\n\s*\n", handle.read()) if p.strip()]


def build(limit, minimum):
    """Chunks every approved source. Returns (records_by_source, stats)."""
    manifest = {}
    with open(os.path.join(CORPUS, "sources.csv"), newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["status"] == "approved":
                manifest[row["id"]] = row
    seen, records, stats = {}, {}, {"split": 0, "pieces": 0, "duplicates": 0, "unsplittable": 0}
    for path in sorted(os.path.join(READY, d, f)
                       for d in sorted(os.listdir(READY)) if os.path.isdir(os.path.join(READY, d))
                       for f in sorted(os.listdir(os.path.join(READY, d))) if f.endswith(".txt")):
        source = os.path.basename(path)[:-4]
        if source not in manifest:
            continue
        row = manifest[source]
        texts = []
        for utterance in read_utterances(path):
            pieces, count = split_utterance(utterance, limit)
            if count:
                stats["split"] += 1
                stats["pieces"] += count
            texts.extend(pieces)
        texts = merge_short(texts, minimum, limit)
        out = []
        for text in texts:
            if text in seen:
                seen[text]["repeats"] += 1
                stats["duplicates"] += 1
                continue
            if words(text) > limit:
                stats["unsplittable"] += 1
            record = {
                "id": f"{source}#{len(out):05d}", "source": source, "register": row["form"],
                "text": text, "words": words(text),
                "repeats": 1, "sha1": hashlib.sha1(text.encode("utf-8")).hexdigest()[:12],
            }
            seen[text] = record
            out.append(record)
        records[source] = out
    return records, stats


def target_shares(records, weights_path):
    """Returns each register's normalized target share, failing early if one has no target."""
    registers, source_weights = draw_weights.load_weights(weights_path)
    sources = [{"id": s, "register": rs[0]["register"], "weight": source_weights.get(s, 1.0),
                "count": len(rs)} for s, rs in records.items() if rs]
    for note in draw_weights.unlisted(sources, registers, source_weights):
        print(f"warning: {note}", file=sys.stderr)
    return draw_weights.draw_probabilities(sources, registers)[1]


def write(records):
    """Writes one JSONL file per source and removes stale chunk files from earlier runs."""
    wanted = set()
    for source, rs in records.items():
        if not rs:
            continue
        directory = os.path.join(OUT, rs[0]["register"])
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{source}.jsonl")
        wanted.add(path)
        with open(path, "w", encoding="utf-8") as handle:
            for record in rs:
                handle.write(json.dumps(record, ensure_ascii=True) + "\n")
    for directory, _, files in os.walk(OUT):
        for name in files:
            path = os.path.join(directory, name)
            if name.endswith(".jsonl") and path not in wanted:
                os.remove(path)


def report(records, stats, shares, limit, minimum):
    """Prints counts, the length distribution, and the expected share of draws."""
    chunks = [r for rs in records.values() for r in rs]
    lengths = sorted(c["words"] for c in chunks)
    n = len(lengths)
    pct = lambda p: lengths[int(p * (n - 1))]
    print(f"{n} chunks from {len(records)} sources  (max {limit} words, "
          f"min-merge {'off' if minimum <= 0 else minimum})")
    print(f"split {stats['split']} long utterances into {stats['pieces']} pieces; "
          f"dropped {stats['duplicates']} exact duplicates; {stats['unsplittable']} still over the limit")
    print("words per chunk, percentiles:", {p: pct(p / 100) for p in (5, 25, 50, 75, 95, 99)},
          f"max {lengths[-1]}")
    near = sum(1 for w in lengths if limit * 0.9 <= w <= limit)
    print(f"chunks within 10% of the ceiling: {near} ({100 * near / n:.1f}%)")
    print(f"\n{'register':<13}{'chunks':>8}{'target share of draws':>24}")
    by_register = {}
    for c in chunks:
        by_register.setdefault(c["register"], []).append(c)
    for register in sorted(by_register, key=lambda r: -shares[r]):
        print(f"{register:<13}{len(by_register[register]):>8}{shares[register]:>23.1%}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--max-words", type=int, default=150)
    parser.add_argument("--min-words", type=int, default=0)
    parser.add_argument("--weights", default=os.path.join(CORPUS, "chunks", "weights.json"))
    parser.add_argument("--check", action="store_true", help="report only; write nothing")
    args = parser.parse_args()
    records, stats = build(args.max_words, args.min_words)
    shares = target_shares(records, args.weights)
    if not args.check:
        write(records)
    report(records, stats, shares, args.max_words, args.min_words)


if __name__ == "__main__":
    main()
