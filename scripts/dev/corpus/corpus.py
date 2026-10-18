#!/usr/bin/env python3
"""Fetch and clean the corpus sources listed in corpus/sources.csv.

The manifest is the single source of truth. This script never edits it; it
reads it, enforces the rights rule, and writes only under corpus/raw/ (not
committed) and corpus/processed/<form>/ (committed).

Usage:
    scripts/dev/corpus/corpus.py status             # what is on disk, per source
    scripts/dev/corpus/corpus.py fetch [--id ID]    # download approved sources to raw/
    scripts/dev/corpus/corpus.py clean [--id ID]    # raw/ -> processed/<form>/<id>.txt

Supported hosts: Project Gutenberg (plain text) and Wikisource (page subtree).
Runs on the Mac with the standard library only. Be polite: requests are
identified and rate-limited, and anything already on disk is skipped unless
--force is given.
"""
import argparse
import csv
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "corpus"
MANIFEST = CORPUS / "sources.csv"

# Wikimedia's policy asks for contact info in the User-Agent; we give the repo URL.
USER_AGENT = ("printer-of-mysteries-corpus/0.1 "
              "(https://github.com/wmodes/pom; private art project; rate-limited)")
REQUEST_DELAY_S = 2.0
MAX_ATTEMPTS = 5

# The README rule: only openly licensed text may be fetched, whatever the status.
OPEN_RIGHTS = {"public-domain", "cc0"}
FETCHABLE_STATUSES = {"approved", "fetched", "processed"}


def load_manifest():
    """Reads sources.csv into a list of row dicts."""
    with MANIFEST.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def select_rows(rows, only_id):
    """Returns fetchable rows (optionally just one), refusing rights violations.

    Args:
        rows: Manifest rows.
        only_id: A source id to restrict to, or None for all fetchable rows.

    Raises:
        SystemExit: If a fetchable row is not openly licensed, or the id is unknown.
    """
    chosen = []
    for row in rows:
        if only_id and row["id"] != only_id:
            continue
        if row["status"] not in FETCHABLE_STATUSES:
            continue
        if row["rights"] not in OPEN_RIGHTS:
            sys.exit(f"REFUSING {row['id']}: status '{row['status']}' but rights "
                     f"'{row['rights']}'. Fix the manifest.")
        chosen.append(row)
    if only_id and not chosen:
        sys.exit(f"No fetchable source with id '{only_id}' (check status and rights).")
    return chosen


def http_get(url):
    """GETs a URL with an identifying User-Agent, then pauses to be polite.

    Retries on 429/503, waiting as long as the server's Retry-After asks (or an
    exponential backoff if it gives none).
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            time.sleep(REQUEST_DELAY_S)
            return data
        except urllib.error.HTTPError as err:
            if err.code not in (429, 503) or attempt == MAX_ATTEMPTS:
                raise
            retry_after = err.headers.get("Retry-After", "")
            wait = int(retry_after) if retry_after.isdigit() else 5 * 2 ** attempt
            print(f"  server said {err.code}; waiting {wait}s (attempt {attempt})",
                  file=sys.stderr)
            time.sleep(wait)


# --- Project Gutenberg ------------------------------------------------------

def gutenberg_book_id(url):
    """Extracts the ebook number from a gutenberg.org/ebooks/<n> URL."""
    match = re.search(r"gutenberg\.org/ebooks/(\d+)", url)
    return match.group(1) if match else None


def fetch_gutenberg(row, raw_dir):
    """Downloads the plain-text edition of a Gutenberg ebook."""
    book = gutenberg_book_id(row["url"])
    target = raw_dir / f"pg{book}.txt"
    url = f"https://www.gutenberg.org/cache/epub/{book}/pg{book}.txt"
    target.write_bytes(http_get(url))


def clean_gutenberg(raw_dir):
    """Strips Gutenberg's licence header/footer and normalizes whitespace."""
    raw = next(raw_dir.glob("pg*.txt")).read_text(encoding="utf-8-sig")
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    start = re.search(r"^\*\*\* ?START OF (THE|THIS) PROJECT GUTENBERG.*?\*\*\*\s*$",
                      text, re.M)
    end = re.search(r"^\*\*\* ?END OF (THE|THIS) PROJECT GUTENBERG", text, re.M)
    if start:
        text = text[start.end():]
    if end:
        text = text[:end.start() - (start.end() if start else 0)]
    # Drop the leading "Produced by ..." transcriber credit paragraph, if any.
    text = re.sub(r"\A\s*Produced by[^\n]*(\n[^\n]+)*\n\s*\n", "", text)
    return tidy(text)


# --- Wikisource -------------------------------------------------------------

def wikisource_api(**params):
    """Calls the Wikisource MediaWiki API and returns parsed JSON."""
    query = urllib.parse.urlencode({**params, "format": "json"})
    return json.loads(http_get(f"https://en.wikisource.org/w/api.php?{query}"))


def natural_key(title):
    """Sorts 'Hexagram 2' before 'Hexagram 10'."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", title)]


class _WikisourceText(HTMLParser):
    """Turns a rendered Wikisource page into plain text.

    Wikisource marks navigation and headers as 'ws-noexport'; those, plus style
    and script blocks, edit links and footnote markers, are skipped. Block-level
    tags become line breaks so verse and paragraphs keep their shape.
    """

    VOID = {"br", "hr", "img", "meta", "link", "input", "wbr"}
    SKIP_TAGS = {"style", "script"}
    SKIP_CLASSES = {"ws-noexport", "mw-editsection", "reference", "noprint"}
    BREAK_TAGS = {"p", "div", "br", "li", "tr", "blockquote", "dd", "dt",
                  "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._stack = []   # (tag, is_skipped) for each open non-void element
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.BREAK_TAGS:
            self.parts.append("\n")
        if tag in self.VOID:
            return
        classes = set((dict(attrs).get("class") or "").split())
        skipped = tag in self.SKIP_TAGS or bool(classes & self.SKIP_CLASSES)
        self._stack.append((tag, skipped))
        self._skip_depth += skipped

    def handle_endtag(self, tag):
        while self._stack:
            open_tag, skipped = self._stack.pop()
            self._skip_depth -= skipped
            if open_tag == tag:
                break
        if tag in self.BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def wikisource_html_to_text(html):
    """Converts rendered Wikisource HTML to plain text (see _WikisourceText)."""
    parser = _WikisourceText()
    parser.feed(html)
    return "".join(parser.parts)


def fetch_wikisource(row, raw_dir):
    """Downloads every subpage under a Wikisource title as numbered text files."""
    root = urllib.parse.unquote(row["url"].rsplit("/wiki/", 1)[1]).replace("_", " ")
    pages = wikisource_api(action="query", list="allpages", apprefix=root + "/",
                           apnamespace=0, aplimit=500)["query"]["allpages"]
    titles = sorted((p["title"] for p in pages if "/Plate " not in p["title"]),
                    key=natural_key)
    for number, title in enumerate(titles):
        # action=parse renders transcluded proofread pages; the plain-text
        # extracts API returns nothing for these wrapper pages.
        html = wikisource_api(action="parse", page=title, prop="text",
                              disablelimitreport=1, formatversion=2)["parse"]["text"]
        text = wikisource_html_to_text(html)
        (raw_dir / f"{number:03d}.txt").write_text(f"{title}\n\n{text}\n",
                                                   encoding="utf-8")


def clean_wikisource(raw_dir):
    """Joins the numbered subpages, each under a heading, in page order."""
    parts = []
    for page in sorted(raw_dir.glob("*.txt")):
        title, _, body = page.read_text(encoding="utf-8").partition("\n\n")
        heading = title.split("/", 1)[-1] if "/" in title else title
        parts.append(f"=== {heading} ===\n\n{body.strip()}")
    return tidy("\n\n".join(parts))


# --- Shared -----------------------------------------------------------------

def tidy(text):
    """Trims trailing spaces and collapses runs of blank lines to one blank line."""
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def handler_for(row):
    """Returns (fetch_fn, clean_fn) for a row's host, or exits if unsupported."""
    if "gutenberg.org" in row["url"]:
        return fetch_gutenberg, clean_gutenberg
    if "wikisource.org" in row["url"]:
        return fetch_wikisource, clean_wikisource
    sys.exit(f"No handler for {row['id']} ({row['url']}).")


def raw_dir_for(row):
    return CORPUS / "raw" / row["id"]


def processed_path_for(row):
    return CORPUS / "processed" / row["form"] / f"{row['id']}.txt"


def cmd_fetch(args):
    """Downloads each approved source into a temp dir, then renames it into place.

    The rename keeps a half-finished download from ever looking complete, and a
    failure on one source is reported without stopping the rest.
    """
    failures = []
    for row in select_rows(load_manifest(), args.id):
        raw_dir = raw_dir_for(row)
        if raw_dir.exists() and not args.force:
            print(f"skip   {row['id']} (already in raw/)")
            continue
        partial = raw_dir.with_name(raw_dir.name + ".partial")
        if partial.exists():
            shutil.rmtree(partial)
        partial.mkdir(parents=True)
        try:
            handler_for(row)[0](row, partial)
        except Exception as err:  # keep going; report at the end
            print(f"FAIL   {row['id']}: {err}")
            failures.append(row["id"])
            continue
        if raw_dir.exists():
            shutil.rmtree(raw_dir)
        partial.rename(raw_dir)
        print(f"fetch  {row['id']}")
    if failures:
        sys.exit(f"{len(failures)} failed: {', '.join(failures)}")


def cmd_clean(args):
    for row in select_rows(load_manifest(), args.id):
        raw_dir = raw_dir_for(row)
        if not raw_dir.exists():
            print(f"miss   {row['id']} (not fetched yet)")
            continue
        cleaned = handler_for(row)[1](raw_dir)
        if len(cleaned.strip()) < 200:
            print(f"EMPTY  {row['id']}: cleaning produced almost nothing; not written")
            continue
        out = processed_path_for(row)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(cleaned, encoding="utf-8")
        print(f"clean  {row['id']} -> {out.relative_to(ROOT)} "
              f"({out.stat().st_size / 1024:.0f} KB)")


def cmd_status(_args):
    for row in load_manifest():
        raw = raw_dir_for(row).exists()
        done = processed_path_for(row).exists()
        print(f"{row['id']:<42} {row['status']:<9} raw={'y' if raw else '-'} "
              f"processed={'y' if done else '-'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, fn in (("fetch", cmd_fetch), ("clean", cmd_clean)):
        p = sub.add_parser(name)
        p.add_argument("--id", help="restrict to one source id")
        p.add_argument("--force", action="store_true", help="refetch even if present")
        p.set_defaults(func=fn)
    sub.add_parser("status").set_defaults(func=cmd_status)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
