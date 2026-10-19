#!/usr/bin/env python3
"""Search Project Gutenberg's complete catalog offline, to find corpus candidates.

Gutenberg publishes its whole catalog as one CSV. Searching that locally is more
reliable than the public search APIs, which time out and rate-limit. The CSV is
cached under corpus/raw/_catalog/ (gitignored, like all of raw/).

Usage:
    scripts/dev/corpus/gutenberg_catalog.py update               # download the catalog
    scripts/dev/corpus/gutenberg_catalog.py search REGEX [...]   # title, author or subject
    scripts/dev/corpus/gutenberg_catalog.py authors REGEX        # author field only
    add --limit N to show more than 12 hits per pattern

Notes:
- Only English-language text is searched.
- The 'rel' column is the year Gutenberg RELEASED the ebook, not the year the work
  was published. Check the publication year on the ebook's title page before
  recording it in sources.csv.
- Gutenberg only distributes works it has cleared for the US, so a hit is a strong
  hint of public-domain status, not a legal opinion. Record the basis in the manifest.
"""
import argparse
import csv
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "corpus" / "raw" / "_catalog" / "pg_catalog.csv"
CATALOG_URL = "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv"
USER_AGENT = ("printer-of-mysteries-corpus/0.1 "
              "(https://github.com/wmodes/pom; private art project; rate-limited)")


def cmd_update(_args):
    """Downloads the catalog CSV (about 20 MB)."""
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(CATALOG_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        CATALOG.write_bytes(resp.read())
    print(f"saved {CATALOG.relative_to(ROOT)} ({CATALOG.stat().st_size / 1e6:.1f} MB)")


def load_english_texts():
    """Reads the cached catalog, keeping English-language text entries."""
    if not CATALOG.exists():
        sys.exit("No catalog cached. Run: gutenberg_catalog.py update")
    with CATALOG.open(newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh)
                if r["Type"] == "Text" and r["Language"] == "en"]


def show(rows, label, limit):
    print(f"\n### {label}  ({len(rows)} hits, showing up to {limit})")
    for r in rows[:limit]:
        print(f"{r['Text#']:>6}  {r['Title'][:78]} | {r['Authors'][:46]} | rel {r['Issued'][:4]}")


def cmd_search(args):
    rows = load_english_texts()
    for pattern in args.patterns:
        rx = re.compile(pattern, re.I)
        hits = [r for r in rows if rx.search(r["Title"]) or rx.search(r["Authors"])
                or rx.search(r["Subjects"])]
        show(hits, f"/{pattern}/", args.limit)


def cmd_authors(args):
    rx = re.compile(args.pattern, re.I)
    show([r for r in load_english_texts() if rx.search(r["Authors"])],
         f"author /{args.pattern}/", args.limit)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("update").set_defaults(func=cmd_update)
    p = sub.add_parser("search")
    p.add_argument("patterns", nargs="+", help="regex(es) matched against title/author/subject")
    p.add_argument("--limit", type=int, default=12)
    p.set_defaults(func=cmd_search)
    p = sub.add_parser("authors")
    p.add_argument("pattern", help="regex matched against the author field")
    p.add_argument("--limit", type=int, default=12)
    p.set_defaults(func=cmd_authors)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
