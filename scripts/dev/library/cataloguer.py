#!/usr/bin/env python3
"""Turn ready/ utterances into passages: the units both oracle tracks use.

ready/ holds clean utterances, one paragraph each. A *passage* is an utterance sized
for the oracle's output (usually 15 to 50 words, at most 150) and carrying what a
consumer needs: where it came from, its shelf, and its chance of being drawn.

    Track A draws 3 to 5 passages at random as shelf exemplars.
    Track B trains on the same passages, using the same draw probabilities as sampling odds.

Rules, in order:
  1. Split. A utterance over --max-words (150) is cut into near-equal pieces ONLY AFTER A SENTENCE END:
     a full stop, question mark or exclamation mark, then any closing quotes or brackets (and the
     dash some old books print after it; `ends_sentence` in pom.library.endings). Prose is cut
     between sentences, verse and wrapped text between lines that end a sentence. So a passage ends
     at a sentence end, at the end of its paragraph (a blank line, where ready/ already divides
     utterances), or at the end of its record, and never mid-thought. A stretch with no sentence end
     in it is never cut, so a piece can come out over the limit; one over --oversize times the limit
     is dropped. The default is 1.0, so the 150-word ceiling holds and such a piece is dropped; raise
     it (1.5 keeps up to 225 words) to keep the longer ones whole.
  2. Merge (off by default). With --min-words N, short utterances join the next one in
     the same holding until they reach N words, without crossing --max-words.
  3. Drop unfinished pieces. A piece that stops before its thought is done (it ends on a colon or
     on a word like "the": see pom.library.endings) is dropped, because the oracle copies its
     exemplar's ending. Rule 1 cannot make one, so this is only the backstop; whole utterances
     that end this way are already dropped by normalize.py before ready/.
  4. Deduplicate. An exact repeat of a passage anywhere in the library is dropped; the kept
     copy records how many times it occurred.

A passage holds only facts about itself (holding, shelf, text). How likely it is to be
drawn depends on the whole library and on the draw weights in passages/circulation.json, so that is
computed at load time by the Bibliomancer (src/pom/library) and never stored here: adding one utterance, or
changing a weight, touches no passage file.

Output is deterministic: one JSONL file per holding under library/passages/<shelf>/<id>.jsonl.
Re-run after any change to ready/ or the manifest.

Usage:
    cataloguer.py                          write library/passages/ and print a report
    cataloguer.py --max-words 200          change the ceiling
    cataloguer.py --min-words 15           merge short utterances forward
    cataloguer.py --oversize 1.5           keep uncuttable pieces up to 1.5 times the ceiling (default 1.0: drop them)
    cataloguer.py --check                  report only; write nothing
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
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))
from pom.library import Circulation, ends_unfinished  # noqa: E402
from pom.library.endings import ends_sentence  # noqa: E402  (scripts may import the package, never the reverse)
from pom.log_setup import configure_logging  # noqa: E402

DEFAULT_OVERSIZE = 1.0       # a piece with no sentence end to cut at may run to this times the limit before it is dropped (1.0: the ceiling holds)
LIBRARY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "library")
READY = os.path.join(LIBRARY, "ready")
OUT = os.path.join(LIBRARY, "passages")

# Sentence ends: terminal punctuation (optionally closed by a quote or bracket), then a
# capital, quote or digit. Abbreviations are rejoined afterwards.
SENTENCE_BREAK = re.compile(
    r"""(?:(?<=[.!?])|(?<=[.!?]["')\]])|(?<=[.!?]["')\]]["')\]])|(?<=[.!?]--)|(?<=[.!?]["')\]]--)|(?<=[.!?]--["')\]]))\s+(?=["'(\[]?[A-Z0-9])""")
ABBREVIATION = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|St|Mt|No|Co|Rev|Hon|Gen|Col|Capt|Lieut|Sir|vs|etc|viz|cf|[A-Z])\.$")


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


def cut_evenly(units, limit, joiner):
    """Groups consecutive units into near-equal pieces, each within `limit` words, cutting only after a sentence end.

    A cut falls only after a unit that ends a sentence (`ends_sentence`: a full stop, question mark or exclamation mark, then any
    closing quotes or brackets), so no piece stops mid-thought. Tries k = ceil(total / limit) pieces, cutting at the allowed boundary
    nearest each even share of the total, which avoids a tiny leftover tail. If a piece still comes out over the limit (a few very
    uneven units), falls back to filling greedily. A run with no sentence end inside it is never cut, so a piece may come out over
    the limit; the caller decides what to do with those.
    """
    sizes = [words(u) for u in units]
    total = sum(sizes)
    k = math.ceil(total / limit)
    boundary, running = {}, 0
    for index, size in enumerate(sizes[:-1], start=1):
        running += size
        if ends_sentence(units[index - 1]):
            boundary[index] = running
    if not boundary:
        return [joiner.join(units)]
    cuts = []
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
            last = max((i for i, u in enumerate(current) if ends_sentence(u)), default=None)
            if last is not None:                            # cut after the last sentence end so far; the rest goes on into the next piece
                pieces.append(joiner.join(current[:last + 1]))
                current = current[last + 1:]
                size = sum(words(u) for u in current)
        current.append(unit)
        size += n
    if current:
        pieces.append(joiner.join(current))
    return pieces


def split_utterance(text, limit):
    """Cuts an utterance over `limit` words into near-equal pieces, only after a sentence end.

    Units are lines when the text has line breaks (verse, wrapped text), otherwise sentences. Returns (pieces, piece_count), where
    piece_count is None if no split was needed. A piece with no sentence end to cut at inside it comes out whole, over the limit.
    """
    if words(text) <= limit:
        return [text], None
    by_lines = "\n" in text.strip()
    units = [u for u in (text.split("\n") if by_lines else split_sentences(text)) if u.strip()]
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
        return [p.strip("\n") for p in re.split(r"\n\s*\n", handle.read()) if p.strip()]     # not the file's last newline: it would make prose look like verse


def build(limit, minimum, oversize=DEFAULT_OVERSIZE):
    """Passages every approved holding. Returns (records_by_holding, stats).

    A piece with no sentence end to cut at inside it can come out over `limit`; one over `oversize` times `limit` is dropped.
    """
    manifest = {}
    with open(os.path.join(LIBRARY, "holdings.csv"), newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["status"] == "approved":
                manifest[row["id"]] = row
    seen, records, stats = {}, {}, {"split": 0, "pieces": 0, "duplicates": 0, "over_limit": 0, "oversize": 0, "unfinished": 0}
    for path in sorted(os.path.join(READY, d, f)
                       for d in sorted(os.listdir(READY)) if os.path.isdir(os.path.join(READY, d))
                       for f in sorted(os.listdir(os.path.join(READY, d))) if f.endswith(".txt")):
        holding = os.path.basename(path)[:-4]
        if holding not in manifest:
            continue
        row = manifest[holding]
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
            if words(text) > limit * oversize:
                stats["oversize"] += 1
                continue
            if ends_unfinished(text):
                stats["unfinished"] += 1
                continue
            if text in seen:
                seen[text]["repeats"] += 1
                stats["duplicates"] += 1
                continue
            if words(text) > limit:
                stats["over_limit"] += 1
            record = {
                "id": f"{holding}#{len(out):05d}", "holding": holding, "shelf": row["shelf"],
                "text": text, "words": words(text),
                "repeats": 1, "sha1": hashlib.sha1(text.encode("utf-8")).hexdigest()[:12],
            }
            seen[text] = record
            out.append(record)
        records[holding] = out
    return records, stats


def target_shares(records, circulation_path):
    """Returns each shelf's normalized target share; drift in circulation.json is logged."""
    circulation = Circulation.from_file(circulation_path)
    layout = {}
    for holding, rs in records.items():
        if rs:
            layout.setdefault(rs[0]["shelf"], []).append(holding)
    circulation.check(layout)                      # logs unlisted or missing shelves and holdings
    return circulation.shares_for(layout)


def write(records):
    """Writes one JSONL file per holding and removes stale passage files from earlier runs."""
    wanted = set()
    for holding, rs in records.items():
        if not rs:
            continue
        directory = os.path.join(OUT, rs[0]["shelf"])
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{holding}.jsonl")
        wanted.add(path)
        with open(path, "w", encoding="utf-8") as handle:
            for record in rs:
                handle.write(json.dumps(record, ensure_ascii=True) + "\n")
    for directory, _, files in os.walk(OUT):
        for name in files:
            path = os.path.join(directory, name)
            if name.endswith(".jsonl") and path not in wanted:
                os.remove(path)


def report(records, stats, shares, limit, minimum, oversize=DEFAULT_OVERSIZE):
    """Prints counts, the length distribution, and the expected share of draws."""
    passages = [r for rs in records.values() for r in rs]
    lengths = sorted(c["words"] for c in passages)
    n = len(lengths)
    pct = lambda p: lengths[int(p * (n - 1))]
    print(f"{n} passages from {len(records)} holdings  (max {limit} words, "
          f"min-merge {'off' if minimum <= 0 else minimum})")
    print(f"split {stats['split']} long utterances into {stats['pieces']} pieces; "
          f"dropped {stats['oversize']} pieces over {limit * oversize:g} words, {stats['unfinished']} unfinished pieces and {stats['duplicates']} exact duplicates; {stats['over_limit']} kept whole over the limit")
    print("words per passage, percentiles:", {p: pct(p / 100) for p in (5, 25, 50, 75, 95, 99)},
          f"max {lengths[-1]}")
    near = sum(1 for w in lengths if limit * 0.9 <= w <= limit)
    print(f"passages within 10% of the ceiling: {near} ({100 * near / n:.1f}%)")
    print(f"\n{'shelf':<13}{'passages':>8}{'target share of draws':>24}")
    by_shelf = {}
    for c in passages:
        by_shelf.setdefault(c["shelf"], []).append(c)
    for shelf in sorted(by_shelf, key=lambda r: -shares[r]):
        print(f"{shelf:<13}{len(by_shelf[shelf]):>8}{shares[shelf]:>23.1%}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--max-words", type=int, default=150)
    parser.add_argument("--min-words", type=int, default=0)
    parser.add_argument("--oversize", type=float, default=DEFAULT_OVERSIZE, help="drop a piece with no sentence end to cut at that is more than this times --max-words")
    parser.add_argument("--circulation", default=os.path.join(LIBRARY, "passages", "circulation.json"))
    parser.add_argument("--check", action="store_true", help="report only; write nothing")
    args = parser.parse_args()
    configure_logging("WARNING")
    records, stats = build(args.max_words, args.min_words, args.oversize)
    shares = target_shares(records, args.circulation)
    if not args.check:
        write(records)
    report(records, stats, shares, args.max_words, args.min_words, args.oversize)


if __name__ == "__main__":
    main()
