#!/usr/bin/env python3
"""Audit corpus text for source artifacts. Read-only.

`ready/` is meant to hold clean utterances with no trace of their source. This
script measures how well that holds, file by file, so a person can read the
worst offenders instead of trusting the standardizer.

Usage:
    audit.py                       one row per file: share of utterances tripping each check
    audit.py ID CHECK [N]          show N examples of one check in one file (ID is a prefix)
    audit.py --dir draft ...       audit draft/ instead of ready/

The checks are deliberately blunt. A rate above zero is a prompt to look, not a
verdict: digits are normal in horoscopes and furniture words are normal in a
printing manual. Rates that should be zero everywhere: nonascii, lower_start,
leading_label, url_email.
"""
import argparse
import glob
import os
import re
import sys
from collections import Counter

CORPUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "corpus")

CHECKS = {
    "nonascii": lambda p: bool(re.search(r"[^\x20-\x7e\n]", p)),
    "lower_start": lambda p: bool(re.match(r"[a-z]", p)),
    "no_end_punct": lambda p: not re.search(r"[.!?\"')\]:;,-]\s*$", p.rstrip()),
    "digits": lambda p: bool(re.search(r"\d", p)),
    "allcaps": lambda p: len(re.findall(r"[A-Za-z]", p)) > 6 and p.upper() == p,
    "brackets": lambda p: bool(re.search(r"[\[\]{}<>]", p)),
    "markup": lambda p: bool(re.search(r"[_*^|~#]", p)),
    "url_email": lambda p: bool(re.search(r"https?:|www\.|@\w|\.com\b|\.org\b", p, re.I)),
    "furniture": lambda p: bool(re.search(
        r"\b(chapter|chap\.|page|pp?\.|ibid|footnote|illustration|gutenberg|transcriber|"
        r"copyright|fig\.|figure|table of|contents|index|appendix|preface|vol\.|sect\.|section)\b",
        p, re.I)),
    "leading_label": lambda p: bool(re.match(
        r"^\s*(\(?\d{1,4}[.):]|[IVXLC]{1,8}[.)]|[A-Z]\.\s|\(?[a-z]\)|Q\.|A\.)\s", p)),
    "trailing_num": lambda p: bool(re.search(r"\s\d{1,4}\s*$", p)),
    "short": lambda p: len(p.split()) < 4,
    "long": lambda p: len(p.split()) > 300,
    "double_space": lambda p: "  " in p,
    "dash_lead": lambda p: bool(re.match(r"^[-=.,;:]", p)),
    "ellipsis": lambda p: bool(re.search(r"\.\s?\.\s?\.|\.{4,}", p)),
}


def read_utterances(path):
    """Returns a file's utterances (blank-line-separated paragraphs)."""
    with open(path, encoding="utf-8") as handle:
        return [p for p in re.split(r"\n\s*\n", handle.read()) if p.strip()]


def table(files):
    """Prints one row per file plus the paragraphs that appear more than once."""
    names = list(CHECKS)
    print(f"{'file':<46}{'utt':>6}{'w/utt':>6} " + " ".join(f"{n[:6]:>6}" for n in names))
    seen, where = Counter(), {}
    for path in files:
        paras = read_utterances(path)
        base = os.path.basename(path)[:-4]
        for p in paras:
            seen[p] += 1
            where.setdefault(p, base)
        words = sum(len(p.split()) for p in paras)
        rates = [f"{100 * sum(CHECKS[n](p) for p in paras) / max(1, len(paras)):6.1f}" for n in names]
        print(f"{base[:46]:<46}{len(paras):>6}{words / max(1, len(paras)):>6.0f} " + " ".join(rates))
    dups = sorted(((p, c) for p, c in seen.items() if c > 1), key=lambda x: -x[1])
    print(f"\nparagraphs that occur more than once across the corpus: {len(dups)}")
    for p, c in dups[:15]:
        print(f"  x{c} [{where[p]}] {p.replace(chr(10), ' / ')[:100]}")


def examples(files, prefix, check, count):
    """Prints up to `count` evenly spaced utterances from one file that trip one check."""
    matches = [f for f in files if os.path.basename(f).startswith(prefix)]
    if not matches:
        sys.exit(f"no file starting with {prefix!r}")
    paras = read_utterances(matches[0])
    hits = [p for p in paras if CHECKS[check](p)]
    print(f"{os.path.basename(matches[0])} / {check}: {len(hits)} of {len(paras)}")
    for p in hits[::max(1, len(hits) // count)][:count]:
        print(" *", p.replace("\n", " / ")[:240])


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("id", nargs="?", help="file id prefix, to show examples")
    parser.add_argument("check", nargs="?", choices=list(CHECKS))
    parser.add_argument("count", nargs="?", type=int, default=12)
    parser.add_argument("--dir", choices=("ready", "draft"), default="ready")
    args = parser.parse_args()
    files = sorted(glob.glob(os.path.join(CORPUS, args.dir, "*", "*.txt")))
    if args.id and args.check:
        examples(files, args.id, args.check, args.count)
    else:
        table(files)


if __name__ == "__main__":
    main()
