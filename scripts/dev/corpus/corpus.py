#!/usr/bin/env python3
"""Fetch and clean the corpus sources listed in corpus/sources.csv.

The manifest is the single source of truth. This script never edits it; it
reads it, enforces the rights rule, and writes only under corpus/raw/ (not
committed), corpus/draft/<form>/ and corpus/ready/<form>/ (both committed).

Stages, in order. Only ready/ is safe to use as training, few-shot or
retrieval data; raw/ and draft/ are not.
    fetch    -> raw/<id>/                 untouched download
    clean    -> draft/<form>/<id>.txt     machine-cleaned, awaiting human review
    promote  -> ready/<form>/<id>.txt     only if approved, public-domain/CC0
                                          and reviewed (manifest column)

Usage:
    scripts/dev/corpus/corpus.py add --id ID --form F --title T --rights R --basis B --url U
    scripts/dev/corpus/corpus.py set ID column=value ...   # edit markers, flags, status
    scripts/dev/corpus/corpus.py process ID         # fetch, clean, audit, sample; stops before promote
    scripts/dev/corpus/corpus.py status             # what is on disk, per source
    scripts/dev/corpus/corpus.py fetch [--id ID]    # download approved sources
    scripts/dev/corpus/corpus.py inspect --id ID    # landmarks + edges, to pick trim markers
    scripts/dev/corpus/corpus.py inspect --id ID --trimmed   # edges after the markers
    scripts/dev/corpus/corpus.py clean [--id ID]    # raw -> draft
    scripts/dev/corpus/corpus.py review [--id ID]   # review report: edges, samples, junk checks
    scripts/dev/corpus/corpus.py promote [--id ID]  # reviewed drafts -> ready

Trim markers (manifest columns start_marker / end_marker): the exact text of a
whole line, optionally followed by '#N' to pick the Nth occurrence. start_marker
is where kept text begins (inclusive; default occurrence 1). end_marker is where
it stops (exclusive; default occurrence is the LAST). Use '#-1' for the last.
A marker that is not found is an error, never silently ignored.

Supported hosts: Project Gutenberg (plain text) and Wikisource (page subtree).
Runs on the Mac with the standard library only. Be polite: requests are
identified and rate-limited, and anything already on disk is skipped unless
--force is given.
"""
import argparse
import csv
import json
from collections import Counter
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

# Sibling module; the explicit path makes it importable even under `python3 -I`.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import normalize  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CORPUS = ROOT / "corpus"
MANIFEST = CORPUS / "sources.csv"

# Wikimedia's policy asks for contact info in the User-Agent; we give the repo URL.
USER_AGENT = ("printer-of-mysteries-corpus/0.1 "
              "(https://github.com/wmodes/pom; private art project; rate-limited)")
REQUEST_DELAY_S = 2.0
MAX_ATTEMPTS = 5

# The README rule: only cleared text may be fetched, whatever the status.
# 'owner-cleared' is NOT public domain: the project owner decided to use the text
# (for example IBM's Wheelwriter manuals) and accepted the risk. Such text must not
# be published; `publish-check` guards that.
OPEN_RIGHTS = {"public-domain", "cc0", "owner-cleared"}
FETCHABLE_STATUSES = {"approved", "fetched", "processed"}


# Columns added after the manifest was first written. read_manifest adds any that are
# missing (blank), so older manifests upgrade themselves the next time they are saved.
# (column, insert after, default)
LATE_COLUMNS = [("weight", "reviewed", "")]


def read_manifest():
    """Reads sources.csv, returning (column names in file order, list of row dicts)."""
    with MANIFEST.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields, rows = list(reader.fieldnames), list(reader)
    for column, after, default in LATE_COLUMNS:
        if column not in fields:
            fields.insert(fields.index(after) + 1, column)
            for row in rows:
                row[column] = default
    return fields, rows


def load_manifest():
    """Reads sources.csv into a list of row dicts."""
    return read_manifest()[1]


def save_manifest(fields, rows):
    """Writes sources.csv atomically (temp file, then replace), keeping column order."""
    temp = MANIFEST.with_suffix(".csv.tmp")
    with temp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(MANIFEST)


RIGHTS_VALUES = {"public-domain", "cc0", "owner-cleared", "in-copyright", "unknown"}
STATUS_VALUES = {"candidate", "approved", "excluded"}


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


def clean_gutenberg(raw_dir, row):
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


def book_order(title):
    """Reading order for a Wikisource volume: Preface, Introduction, then the rest."""
    if "/Preface" in title:
        return (0, natural_key(title))
    if "/Introduction" in title:
        return (1, natural_key(title))
    return (2, natural_key(title))


def strip_wikisource_nav(body):
    """Removes the page-navigation residue Wikisource renders around each page.

    Each subpage begins with 'translated by <name>' and the page's own title,
    and carries previous/next links shown as a short label (often a Roman
    numeral) next to an arrow. None of that is the book's text.
    """
    # \s, not a literal space: the rendered HTML uses non-breaking spaces.
    body = re.sub(r"\A\s*translated\s+by\s[^\n]*\n[^\n]*\n", "", body)
    # Site boilerplate shown on pages that are not fully proofread.
    body = re.sub(r"^A scan-backed, verifiable version[^\n]*\n", "", body, flags=re.M)
    body = re.sub(r"^If you would like to help, please see Help:[^\n]*\n", "", body,
                  flags=re.M)
    body = re.sub(r"^[IVXLC]{1,6}[ \t]*\n[ \t]*\n[ \t]*[←→][ \t]*\n", "", body, flags=re.M)
    return re.sub(r"^[ \t]*[←→][ \t]*$", "", body, flags=re.M)


def clean_wikisource(raw_dir, row):
    """Joins the subpages, each under a heading, in reading order.

    The fetch order is alphabetical, which would put the hexagrams before the
    preface and introduction, so the pages are re-sorted here.
    """
    pages = []
    for page in raw_dir.glob("*.txt"):
        title, _, body = page.read_text(encoding="utf-8").partition("\n\n")
        pages.append((title, body))
    parts = []
    for title, body in sorted(pages, key=lambda tb: book_order(tb[0])):
        heading = title.split("/", 1)[-1] if "/" in title else title
        parts.append(f"=== {heading} ===\n\n{strip_wikisource_nav(body).strip()}")
    return tidy("\n\n".join(parts))


# --- PDF (direct file URLs ending in .pdf) ------------------------------------

# Below this many words per page, a PDF is treated as an image-only scan and OCR'd.
MIN_WORDS_PER_PAGE = 40


def fetch_pdf(row, raw_dir):
    """Downloads the PDF as raw/<id>/source.pdf."""
    (raw_dir / "source.pdf").write_bytes(http_get(row["url"]))


def pdf_page_count(pdf):
    """Reads the page count from pdfinfo (poppler)."""
    info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=True)
    return int(re.search(r"^Pages:\s+(\d+)", info.stdout, re.M).group(1))


def ocr_pdf(pdf, cache):
    """OCRs a scanned PDF with tesseract and caches the result next to the PDF.

    OCR is slow, so the text is cached in raw/ (ignored by git). The output
    depends on the tesseract version, which is noted in documentation/corpus-notes.md.
    """
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", "300", "-gray", "-png", str(pdf),
                        str(Path(tmp) / "p")], check=True)
        pages = []
        for image in sorted(Path(tmp).glob("p-*.png")):
            result = subprocess.run(["tesseract", str(image), "-", "--psm", "3"],
                                    capture_output=True, text=True, check=True)
            pages.append(result.stdout.strip())
    text = "\n\n".join(pages)
    cache.write_text(text, encoding="utf-8")
    return text


def clean_pdf(raw_dir, row):
    """Extracts text from the PDF, using OCR when the text layer is absent or bad.

    The manifest's `extract` column decides: `ocr` forces OCR (for scans whose
    embedded text layer is scrambled), `layer` forces the embedded text, and blank
    means auto: use the text layer unless it is nearly empty.
    """
    pdf = raw_dir / "source.pdf"
    mode = (row.get("extract") or "").strip().lower()
    if mode == "ocr":
        return tidy(ocr_pdf(pdf, raw_dir / "ocr.txt").replace("\f", "\n\n"))
    text = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True,
                          check=True).stdout
    if mode != "layer" and len(text.split()) < MIN_WORDS_PER_PAGE * pdf_page_count(pdf):
        text = ocr_pdf(pdf, raw_dir / "ocr.txt")
    return tidy(text.replace("\f", "\n\n"))


# --- Plain text (direct file URLs ending in .txt) ----------------------------

def fetch_text(row, raw_dir):
    """Downloads a plain-text file, such as an Internet Archive *_djvu.txt OCR dump."""
    (raw_dir / "source.txt").write_bytes(http_get(row["url"]))


def clean_text(raw_dir, row):
    """Decodes the text file and normalizes line endings (the markers do the rest)."""
    raw = (raw_dir / "source.txt").read_text(encoding="utf-8-sig", errors="replace")
    return tidy(raw.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n\n"))


# --- Shared -----------------------------------------------------------------

def tidy(text):
    """Trims trailing spaces and collapses runs of blank lines to one blank line."""
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


# --- Inbox (human-supplied files that cannot be downloaded) --------------------

def fetch_inbox(row, raw_dir):
    """Copies a human-supplied file from corpus/inbox/ into raw/<id>/ as source.<ext>.

    For text that has no public URL, such as a shared Google Doc or original writing
    from a contributor. The inbox file is committed, since it cannot be refetched.
    Set the manifest url to 'inbox/<filename>'.
    """
    source = CORPUS / row["url"]
    if not source.is_file():
        sys.exit(f"{row['id']}: inbox file not found: {source}")
    shutil.copyfile(source, raw_dir / f"source{source.suffix.lower()}")


def handler_for(row):
    """Returns (fetch_fn, clean_fn) for a row's URL, or exits if unsupported."""
    path = urllib.parse.urlparse(row["url"]).path.lower()
    if row["url"].startswith("inbox/"):
        return fetch_inbox, (clean_pdf if path.endswith(".pdf") else clean_text)
    if path.endswith(".pdf"):
        return fetch_pdf, clean_pdf
    if path.endswith(".txt"):
        return fetch_text, clean_text
    if "gutenberg.org" in row["url"]:
        return fetch_gutenberg, clean_gutenberg
    if "wikisource.org" in row["url"]:
        return fetch_wikisource, clean_wikisource
    sys.exit(f"No handler for {row['id']} ({row['url']}).")


def raw_dir_for(row):
    return CORPUS / "raw" / row["id"]


def draft_path_for(row):
    return CORPUS / "draft" / row["form"] / f"{row['id']}.txt"


def ready_path_for(row):
    return CORPUS / "ready" / row["form"] / f"{row['id']}.txt"


# Junk that appears in many sources and is never part of the text itself.
ILLUSTRATION = re.compile(r"\[Illustration[^\]]*\]", re.I)


def apply_global_rules(text):
    """Removes universal non-text junk: [Illustration ...] markers."""
    return ILLUSTRATION.sub("", text)


def find_marker_line(lines, marker, default_occurrence):
    """Finds the line index for a trim marker, or exits if it is not found.

    Args:
        lines: The text split into lines.
        marker: Exact whole-line text, optionally 'text#N' (N may be -1 for last).
        default_occurrence: Which occurrence to use when no '#N' is given.
    """
    occurrence = default_occurrence
    match = re.fullmatch(r"(.*)#(-?\d+)", marker)
    if match:
        marker, occurrence = match.group(1), int(match.group(2))
    hits = [i for i, line in enumerate(lines) if line.strip() == marker.strip()]
    if not hits:
        sys.exit(f"Trim marker not found: {marker!r}")
    try:
        return hits[occurrence - 1] if occurrence > 0 else hits[occurrence]
    except IndexError:
        sys.exit(f"Trim marker {marker!r} has only {len(hits)} occurrence(s); "
                 f"asked for #{occurrence}")


def apply_trim(text, row):
    """Cuts front and back matter using the row's start_marker and end_marker."""
    lines = text.split("\n")
    start, end = 0, len(lines)
    if row.get("start_marker"):
        start = find_marker_line(lines, row["start_marker"], 1)
    if row.get("end_marker"):
        end = find_marker_line(lines, row["end_marker"], -1)
    if end <= start:
        sys.exit(f"{row['id']}: end_marker comes before start_marker")
    return "\n".join(lines[start:end])


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


def build_text(row, trim=True, standardize=True, stats=None, audit=None):
    """Produces a source's cleaned text from raw/, deterministically.

    The one place cleaning happens, shared by `clean` and `inspect` so what you
    inspect is exactly what gets written. No timestamps or randomness, so
    re-running on the same raw/ and manifest gives byte-identical output.

    Order: extract -> global junk rules -> trim -> standardize. Trim runs before
    standardizing so markers match the lines as extracted.

    Args:
        row: The manifest row.
        trim: Apply start_marker/end_marker. `inspect` passes False to show the
            untrimmed text that markers are chosen against.
        standardize: Apply the standardization rules (normalize.py) after trimming.
        stats: Optional Counter that the standardization rules add their counts to.
        audit: Optional dict; the rules record samples of what they removed in it.
    """
    raw_dir = raw_dir_for(row)
    if not raw_dir.exists():
        sys.exit(f"{row['id']} is not fetched yet (run fetch).")
    text = apply_global_rules(handler_for(row)[1](raw_dir, row))
    if trim:
        text = apply_trim(text, row)
    text = tidy(text)
    if standardize:
        flags = set((row.get("normalize") or "").split())
        drops = [re.compile(p.strip()) for p in (row.get("drop") or "").split(";;") if p.strip()]
        text = normalize.standardize(text, flags, stats if stats is not None else Counter(),
                                     audit=audit, drop_patterns=drops)
    return text


# Lines that usually mark the edge of front or back matter. A hint for choosing
# trim markers, not a rule: the human decides.
LANDMARK = re.compile(
    r"^\W*(CONTENTS|TABLE OF CONTENTS|PREFACE|INTRODUCTION|FOREWORD|INDEX|APPENDIX|"
    r"FINIS|THE END|END OF|CHAPTER (I|1|ONE)\b|PART (I|1|ONE)\b|BOOK (I|1|ONE)\b|"
    r"SECTION (I|1)\b|TRANSCRIBER|ERRATA|BIBLIOGRAPHY|ADVERTISEMENT|FOOTNOTES?|"
    r"NOTES?\b|ILLUSTRATIONS|LIST OF)", re.I)


def cmd_inspect(args):
    """Shows what's needed to choose start_marker and end_marker for a source.

    Prints the landmark-looking lines of the untrimmed text with the marker
    string to paste into sources.csv ('text' if the line is unique, otherwise
    'text#N' for its Nth exact occurrence), plus the first and last lines. With
    --trimmed, shows the edges after the current markers are applied instead.
    """
    row = next((r for r in load_manifest() if r["id"] == args.id), None)
    if row is None:
        sys.exit(f"No source with id '{args.id}'.")
    # Untrimmed view is what markers are chosen against, so leave it unstandardized.
    lines = build_text(row, trim=args.trimmed, standardize=args.trimmed).split("\n")
    print(f"{row['id']}: {len(lines)} lines "
          f"({'trimmed' if args.trimmed else 'untrimmed'})")
    if not args.trimmed:
        counts = {}
        print("\nlandmarks (line  marker-to-paste  [which occurrence]):")
        seen = {}
        for line in lines:
            counts[line.strip()] = counts.get(line.strip(), 0) + 1
        for number, line in enumerate(lines, start=1):
            text = line.strip()
            # Headings, not prose: skip lines that start in lowercase.
            if text and not text[0].islower() and LANDMARK.match(line) and len(text) <= 70:
                seen[text] = seen.get(text, 0) + 1
                marker = text if counts[text] == 1 else f"{text}#{seen[text]}"
                print(f"  {number:>6}  {marker}   [{seen[text]} of {counts[text]}]")
    nonblank = [(n, l) for n, l in enumerate(lines, start=1) if l.strip()]
    print(f"\nfirst {args.edge} lines:")
    for n, l in nonblank[:args.edge]:
        print(f"  {n:>6}: {l.strip()[:90]}")
    print(f"\nlast {args.edge} lines:")
    for n, l in nonblank[-args.edge:]:
        print(f"  {n:>6}: {l.strip()[:90]}")


# Things that should not survive trimming. Each is counted per draft; a nonzero
# count is a prompt to look, not proof of a problem (a novel may say "copyright").
REVIEW_CHECKS = {
    "[Illustration]": re.compile(r"\[Illustration", re.I),
    "transcriber": re.compile(r"transcriber", re.I),
    "Project Gutenberg": re.compile(r"project gutenberg", re.I),
    "Footnote N:": re.compile(r"^Footnote \d+:", re.M),
    "contents/index heading": re.compile(r"^\W*(TABLE OF )?(CONTENTS|INDEX)\W*$", re.M),
    "bare number lines": re.compile(r"^\s*\d{1,4}\s*$", re.M),
    "copyright/(c)": re.compile(r"copyright|©", re.I),
}


def review_report(row):
    """Prints one draft's review report: stats, junk checks, edges, and samples.

    Samples are taken at fixed fractions of the text (25%, 50%, 75%), not at
    random, so the same draft always shows the same excerpts.
    """
    text = draft_path_for(row).read_text(encoding="utf-8")
    lines = text.split("\n")
    words = len(text.split())
    non_ascii = {}
    for ch in text:
        if ord(ch) > 127:
            non_ascii[ch] = non_ascii.get(ch, 0) + 1
    print(f"\n{'=' * 78}\n{row['id']}  [{row['form']}]  reviewed={row['reviewed'] or '-'}")
    print(f"{words} words, {len(lines)} lines, "
          f"{sum(non_ascii.values())} non-ASCII chars "
          f"({', '.join(f'{c}x{n}' for c, n in sorted(non_ascii.items(), key=lambda kv: -kv[1])[:8]) or 'none'})")
    flags = {name: len(rx.findall(text)) for name, rx in REVIEW_CHECKS.items()}
    print("checks: " + ", ".join(f"{k}={v}" for k, v in flags.items()))
    nonblank = [l.strip() for l in lines if l.strip()]
    print("--- first lines:")
    for l in nonblank[:4]:
        print(f"    {l[:96]}")
    for pct in (25, 50, 75):
        start = len(lines) * pct // 100
        print(f"--- sample at {pct}% (line {start}):")
        shown = [l.strip() for l in lines[start:start + 14] if l.strip()][:5]
        for l in shown:
            print(f"    {l[:96]}")
    print("--- last lines:")
    for l in nonblank[-4:]:
        print(f"    {l[:96]}")


def cmd_review(args):
    """Prints a review report for one draft or all drafts. Never marks anything reviewed."""
    rows = [r for r in load_manifest()
            if (not args.id or r["id"] == args.id) and draft_path_for(r).exists()]
    if not rows:
        sys.exit("No drafts to review (run clean first, or check --id).")
    for row in rows:
        review_report(row)
    print("\nSign-off is manual: when satisfied, set reviewed=yes in sources.csv.")


def cmd_clean(args):
    for row in select_rows(load_manifest(), args.id):
        if not raw_dir_for(row).exists():
            print(f"miss   {row['id']} (not fetched yet)")
            continue
        stats = Counter()
        audit = {} if args.audit else None
        trimmed_words = len(build_text(row, standardize=False).split())
        cleaned = build_text(row, stats=stats, audit=audit)
        if len(cleaned.strip()) < 200:
            print(f"EMPTY  {row['id']}: cleaning produced almost nothing; not written")
            continue
        out = draft_path_for(row)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(cleaned, encoding="utf-8")
        print(f"clean  {row['id']} -> {out.relative_to(ROOT)} "
              f"({out.stat().st_size / 1024:.0f} KB, {trimmed_words} -> "
              f"{len(cleaned.split())} words)")
        if args.verbose or args.audit:
            print("         " + ", ".join(f"{k}: {v}" for k, v in sorted(stats.items()) if v))
        for rule, samples in sorted((audit or {}).items()):
            step = max(1, len(samples) // args.audit)
            print(f"         -- removed by '{rule}' ({len(samples)} recorded), a sample:")
            for sample in samples[::step][:args.audit]:
                print(f"            {sample}")


def cmd_promote(args):
    """Copies reviewed drafts into ready/, refusing anything that isn't cleared.

    A source is promoted only if it is approved, openly licensed, marked
    reviewed in the manifest, and has a draft. Everything else is reported with
    the reason, so ready/ can be trusted without checking.
    """
    for row in load_manifest():
        if args.id and row["id"] != args.id:
            continue
        reasons = []
        if row["status"] != "approved":
            reasons.append(f"status is '{row['status']}'")
        if row["rights"] not in OPEN_RIGHTS:
            reasons.append(f"rights are '{row['rights']}'")
        if row["reviewed"].strip().lower() != "yes":
            reasons.append("not reviewed")
        if not draft_path_for(row).exists():
            reasons.append("no draft (run clean)")
        if reasons:
            if args.id or row["status"] == "approved":
                print(f"refuse {row['id']}: {', '.join(reasons)}")
            continue
        out = ready_path_for(row)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(draft_path_for(row), out)
        print(f"ready  {row['id']} -> {out.relative_to(ROOT)}")


def cmd_publish_check(_args):
    """Fails if text that is not public domain is tracked by git.

    Run this before making the repository public or sharing it. Any source whose
    rights are 'owner-cleared' (or anything other than public-domain/cc0) must
    not have its text in a committed file.
    """
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "corpus"],
                             capture_output=True, text=True, check=True).stdout.split("\n")
    offenders = []
    for row in load_manifest():
        if row["rights"] in ("public-domain", "cc0"):
            continue
        for path in tracked:
            if f"/{row['id']}." in path or f"/{row['id']}/" in path:
                offenders.append((row["id"], row["rights"], path))
    if offenders:
        print("NOT SAFE TO PUBLISH: committed text that is not public domain:")
        for source_id, rights, path in offenders:
            print(f"  {path}  ({source_id}, rights={rights})")
        sys.exit(1)
    print("OK: no non-public-domain corpus text is tracked by git.")


def validate_row_values(values):
    """Checks manifest values that have a fixed vocabulary; exits with a message if wrong."""
    if "rights" in values and values["rights"] not in RIGHTS_VALUES:
        sys.exit(f"rights must be one of: {', '.join(sorted(RIGHTS_VALUES))}")
    if "status" in values and values["status"] not in STATUS_VALUES:
        sys.exit(f"status must be one of: {', '.join(sorted(STATUS_VALUES))}")
    if "reviewed" in values and values["reviewed"] not in ("", "yes"):
        sys.exit("reviewed must be 'yes' or blank")


def check_cleared(row):
    """Refuses approved or reviewed rows whose rights are not cleared for use."""
    if (row["status"] == "approved" or row["reviewed"] == "yes") and row["rights"] not in OPEN_RIGHTS:
        sys.exit(f"{row['id']}: cannot be approved or reviewed with rights '{row['rights']}'.")


def cmd_add(args):
    """Appends a manifest row, so new sources never need a hand-edited CSV or scratch script."""
    fields, rows = read_manifest()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", args.id):
        sys.exit("id must be lowercase letters, digits and hyphens, e.g. poetry-pope-essay-man-1733")
    if any(r["id"] == args.id for r in rows):
        sys.exit(f"A source with id '{args.id}' already exists. Use `set` to change it.")
    row = {f: "" for f in fields}
    row.update(id=args.id, form=args.form, title=args.title, author=args.author, year=args.year,
               rights=args.rights, rights_basis=args.basis, url=args.url, status=args.status,
               notes=args.notes)
    validate_row_values(row)
    check_cleared(row)
    rows.append(row)
    save_manifest(fields, rows)
    print(f"added  {args.id} (status {args.status}, rights {args.rights})")
    print(f"next   corpus.py process {args.id}" if args.status == "approved"
          else "next   confirm the rights, then: corpus.py set {id} status=approved".format(id=args.id))


def cmd_set(args):
    """Changes manifest columns for one source: corpus.py set ID column=value [...]."""
    fields, rows = read_manifest()
    row = next((r for r in rows if r["id"] == args.id), None)
    if row is None:
        sys.exit(f"No source with id '{args.id}'.")
    changes = {}
    for pair in args.assignments:
        column, sep, value = pair.partition("=")
        if not sep or column not in fields or column == "id":
            sys.exit(f"Bad assignment '{pair}'. Use column=value; columns: "
                     f"{', '.join(f for f in fields if f != 'id')}")
        changes[column] = value
    validate_row_values(changes)
    updated = {**row, **changes}
    check_cleared(updated)
    for column, value in changes.items():
        print(f"set    {args.id}.{column}: {row[column]!r} -> {value!r}")
    row.update(changes)
    save_manifest(fields, rows)
    if changes.get("reviewed") == "yes":
        print("note   reviewed=yes is the human sign-off; promote will now copy this draft to ready/")


def show_utterances(row, count):
    """Prints the first, a middle, and the last few utterances of a source's draft."""
    path = draft_path_for(row)
    paragraphs = [p for p in re.split(r"\n\s*\n", path.read_text(encoding="utf-8")) if p.strip()]
    middle = len(paragraphs) // 2
    print(f"\n{len(paragraphs)} utterances. First, middle and last few:")
    for label, chunk in (("first", paragraphs[:count]),
                         ("middle", paragraphs[middle:middle + count]),
                         ("last", paragraphs[-count:])):
        print(f"  -- {label}")
        for p in chunk:
            print("     " + p.replace("\n", " / ")[:140])


def cmd_process(args):
    """Takes one source from manifest row to a reviewed-ready draft in one command.

    Runs fetch (if not fetched), clean with a standardization audit, the edge
    inspection, and a sample of utterances. It stops before promotion: the markers,
    flags and audit need a human read, and `reviewed` is a human sign-off.
    """
    row = next((r for r in load_manifest() if r["id"] == args.id), None)
    if row is None:
        sys.exit(f"No source with id '{args.id}'. Add it first with `add`.")
    if row["status"] != "approved":
        sys.exit(f"{args.id} is '{row['status']}'. Confirm its rights, then: "
                 f"corpus.py set {args.id} status=approved")
    if not raw_dir_for(row).exists():
        cmd_fetch(argparse.Namespace(id=args.id, force=False))
    if not (row["start_marker"] or row["end_marker"]):
        print("\nnote: no trim markers yet. The first run shows the whole text; use the landmarks "
              "below to choose start_marker and end_marker, then re-run.")
        cmd_inspect(argparse.Namespace(id=args.id, trimmed=False, edge=args.edge))
    cmd_clean(argparse.Namespace(id=args.id, verbose=True, audit=args.audit))
    cmd_inspect(argparse.Namespace(id=args.id, trimmed=True, edge=args.edge))
    show_utterances(row, args.edge)
    print(f"\nnext   adjust with `set {args.id} start_marker=... end_marker=... normalize=... drop=...` "
          f"and re-run; when it reads right, set reviewed=yes and run promote.")


def source_weight(row):
    """A source's relative sampling weight: the `weight` column, or 1.0 if blank or invalid."""
    try:
        return float(row.get("weight") or 1.0)
    except ValueError:
        return 1.0


def cmd_mix(args):
    """Shows how much each source and form contributes to the mix, after weights.

    Effective words = words in ready/ x weight. Use it to keep one flavor (say, the
    Bible) from overwhelming the rest: change a source's `weight`, run this again.
    """
    stats = []
    for row in load_manifest():
        path = ready_path_for(row)
        if row["status"] != "approved" or not path.exists():
            continue
        paragraphs = [p for p in re.split(r"\n\s*\n", path.read_text(encoding="utf-8")) if p.strip()]
        words = sum(len(p.split()) for p in paragraphs)
        stats.append((row, len(paragraphs), words, words * source_weight(row)))
    total_u = sum(s[1] for s in stats)
    total_w = sum(s[3] for s in stats)
    if not stats or not total_w:
        sys.exit("Nothing in ready/ yet.")
    print(f"{'source':<46} {'wt':>4} {'utter.':>7} {'words':>8} {'effective':>9}")
    for row, utterances, words, effective in sorted(stats, key=lambda s: -s[3]):
        flag = "  <-- over 15%" if effective / total_w > 0.15 else ""
        print(f"{row['id']:<46} {source_weight(row):>4.1f} {utterances:>7} {words:>8} "
              f"{effective / total_w:>8.1%}{flag}")
    print(f"\n{'by form':<46} {'':>4} {'utter.':>7} {'words':>8} {'effective':>9}")
    forms = {}
    for row, utterances, words, effective in stats:
        entry = forms.setdefault(row["form"], [0, 0, 0.0])
        entry[0] += utterances; entry[1] += words; entry[2] += effective
    for form, (utterances, words, effective) in sorted(forms.items(), key=lambda kv: -kv[1][2]):
        print(f"{form:<46} {'':>4} {utterances:>7} {words:>8} {effective / total_w:>8.1%}")
    if args.prefix:
        group = [s for s in stats if s[0]["id"].startswith(args.prefix)]
        print(f"\nids starting '{args.prefix}': {len(group)} sources, "
              f"{sum(s[2] for s in group)} words, "
              f"{sum(s[3] for s in group) / total_w:.1%} of the weighted mix")
    print(f"\nTOTAL: {total_u} utterances, {sum(s[2] for s in stats)} words, "
          f"{total_w:.0f} effective words")


def cmd_status(_args):
    print(f"{'id':<42} {'status':<9} {'reviewed':<9} raw draft ready")
    for row in load_manifest():
        flag = lambda path: "y" if path.exists() else "-"
        print(f"{row['id']:<42} {row['status']:<9} {row['reviewed'] or '-':<9} "
              f"  {flag(raw_dir_for(row))}    {flag(draft_path_for(row))}     "
              f"{flag(ready_path_for(row))}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, fn in (("fetch", cmd_fetch), ("clean", cmd_clean), ("promote", cmd_promote)):
        p = sub.add_parser(name)
        p.add_argument("--id", help="restrict to one source id")
        if name == "fetch":
            p.add_argument("--force", action="store_true", help="refetch even if present")
        if name == "clean":
            p.add_argument("-v", "--verbose", action="store_true",
                           help="also print what each standardization rule changed")
            p.add_argument("--audit", type=int, default=0, metavar="N",
                           help="print N sample fragments each rule removed, to check for lost content")
        p.set_defaults(func=fn)
    p = sub.add_parser("inspect", help="show landmarks and edges to choose trim markers")
    p.add_argument("--id", required=True, help="source id")
    p.add_argument("--trimmed", action="store_true",
                   help="show the edges after the current markers instead")
    p.add_argument("--edge", type=int, default=6, help="lines to show at each edge")
    p.set_defaults(func=cmd_inspect)
    p = sub.add_parser("review", help="one-screen review report per draft")
    p.add_argument("--id", help="restrict to one source id")
    p.set_defaults(func=cmd_review)
    sub.add_parser("publish-check", help="fail if non-public-domain text is committed"
                   ).set_defaults(func=cmd_publish_check)
    p = sub.add_parser("add", help="append a source row to the manifest")
    p.add_argument("--id", required=True)
    p.add_argument("--form", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--author", default="")
    p.add_argument("--year", default="")
    p.add_argument("--rights", required=True, choices=sorted(RIGHTS_VALUES))
    p.add_argument("--basis", required=True, help="why we believe the rights claim")
    p.add_argument("--url", required=True, help="source URL, or inbox/<file>")
    p.add_argument("--status", default="candidate", choices=sorted(STATUS_VALUES))
    p.add_argument("--notes", default="")
    p.set_defaults(func=cmd_add)
    p = sub.add_parser("set", help="change manifest columns: set ID column=value ...")
    p.add_argument("id")
    p.add_argument("assignments", nargs="+", metavar="column=value")
    p.set_defaults(func=cmd_set)
    p = sub.add_parser("mix", help="share of each source and form in the weighted mix")
    p.add_argument("--prefix", help="also total the sources whose id starts with this (e.g. bible-)")
    p.set_defaults(func=cmd_mix)
    p = sub.add_parser("process", help="fetch, clean, audit and sample one source (stops before promote)")
    p.add_argument("id")
    p.add_argument("--audit", type=int, default=8, metavar="N", help="audit samples per rule")
    p.add_argument("--edge", type=int, default=4, help="lines/utterances to show at each edge")
    p.set_defaults(func=cmd_process)
    sub.add_parser("status").set_defaults(func=cmd_status)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
