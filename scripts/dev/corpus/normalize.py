"""Turn a trimmed source into a list of clean utterances, one per paragraph.

Goal: `ready/` should hold examples of the kind of thing the oracle should say, with
no trace of where they came from. So the output of this module is plain text in
which every utterance is its own paragraph, separated from the next by one blank
line. Verse keeps its line breaks inside a paragraph; a stanza is a paragraph.

What is removed, in general terms:
  - source furniture: headings, section and chapter labels, speaker names and stage
    directions, jest and list numbers, page numbers, running heads, footnotes,
    cross-references ("see page 14"), tables, rule lines, markup
  - typography a typewriter cannot print (curly quotes, dashes, accents, symbols)
  - paragraphs that are not usable utterances (OCR garbage, fragments)
What is kept: the utterances themselves, lightly standardized.

Applied by corpus.py after trimming (so trim markers still match the extracted
lines). Every rule is deterministic and counts what it changed into a Counter. When
an `audit` dict is passed, rules also record samples of what they removed, so a
human can check that real content was not lost.

Per-source behavior comes from flags in the manifest `normalize` column
(space-separated), and an optional `drop` column of regexes (see corpus.py):

    unwrap        join hard-wrapped prose lines into paragraphs (not for verse)
    entries       start a new paragraph at each =Term.=-- dictionary entry
    speeches      play text: keep speech bodies, drop speaker names, stage directions
    couplets      split long runs of rhymed verse into sentence-ended groups of couplets
    glosses       remove an edition's right-margin glosses, asterisks and <n> note anchors
    summaries     drop the editorial summary paragraph under each "Chapter N" heading
    indented      keep only mostly-indented blocks (an anthology whose verse is indented and
                  whose introductions, headnotes and notes are flush-left prose)
    shortlines    keep only blocks of short lines (verse whose neighbors are wrapped prose);
                  combine with `indented` to keep blocks that satisfy either test
    linenos       verse: strip line numbers left at the end of lines ("chasing, 561")
    titles        verse: drop a poem's title line that sits directly above its first line
    unbrace       keep an editor's [inserted words] but drop the brackets around them
    spellcheck    OCR sources: drop paragraphs containing a word that is neither in the system
                  dictionary nor repeated in the source (needs /usr/share/dict/words)
    unbracket     remove an editor's [bracketed notes] and the *asterisks* that point to them
    dictionary    "Term--meaning" entries (a dream dictionary) become "Term: meaning"
    keep-accents  keep accented letters instead of folding them to ASCII
"""
import re
import unicodedata

KEEP_AUDIT = 400    # samples recorded per rule


def note(audit, rule, text):
    """Records a removed fragment for the audit report (only when auditing)."""
    if audit is not None:
        samples = audit.setdefault(rule, [])
        if len(samples) < KEEP_AUDIT:
            samples.append(text.strip().replace("\n", " / ")[:120])


# --- 1. Characters ---------------------------------------------------------------

# Removed outright: zero-width and direction marks, byte-order mark, soft hyphen.
TITLE_LINE = re.compile(r"^[A-Z][A-Za-z']*(?: (?:[A-Z][A-Za-z']*|a|an|the|of|on|in|to|and|for|at))*$")


def strip_title_line(paragraph):
    """Drops a poem's title that sits directly above its first line (no blank line between).

    A title is a short Title Case line with no punctuation; verse lines in these
    editions almost always end in punctuation or start in lowercase.
    """
    first, _, rest = paragraph.partition("\n")
    if rest and len(first.split()) <= 6 and TITLE_LINE.match(first) and not first.endswith(
            (" a", " an", " the", " of", " on", " in", " to", " and", " for", " at")):
        return rest
    return paragraph


def unbrace_paragraph(paragraph):
    """Keeps an editor's [inserted words] but drops the brackets themselves."""
    return re.sub(r"[\[\]]", "", paragraph)


INVISIBLE = re.compile("[​-‏‪-‮⁠﻿­]")

# Typography replaced with typewriter-safe ASCII.
TYPOGRAPHY = {
    "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
    "‘": "'", "’": "'", "‚": "'", "′": "'", "″": '"',
    "—": "--", "―": "--", "–": "-", "−": "-", "‐": "-",
    "‑": "-", "…": "...", "•": "-", "·": "-", "×": "x",
    "½": "1/2", "¼": "1/4", "¾": "3/4", "§": "section ",
    "¶": "", "†": "", "‡": "", "©": "(c)", "®": "",
    "¢": "", "£": "GBP ", "→": "", "←": "", "↑": "",
    "↓": "", "°": " degrees",
}
LIGATURES = {"æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
             "ﬁ": "fi", "ﬂ": "fl", "ß": "ss"}

# Astrological symbols become words so nothing the printwheel cannot print is left.
SYMBOLS = {
    "♈": "Aries", "♉": "Taurus", "♊": "Gemini", "♋": "Cancer",
    "♌": "Leo", "♍": "Virgo", "♎": "Libra", "♏": "Scorpio",
    "♐": "Sagittarius", "♑": "Capricorn", "♒": "Aquarius", "♓": "Pisces",
    "☉": "Sun", "☽": "Moon", "☿": "Mercury", "♀": "Venus",
    "♂": "Mars", "♃": "Jupiter", "♄": "Saturn", "♅": "Uranus",
    "♆": "Neptune", "♇": "Pluto", "☌": "conjunction", "☍": "opposition",
    "△": "trine", "□": "square", "⚹": "sextile",
    "☊": "Dragon's Head", "☋": "Dragon's Tail", "⊕": "Earth",
}
# Hexagram and trigram glyphs, box-drawing, Chinese characters: nothing a printwheel has.
PICTOGRAMS = re.compile("[☰-☷䷀-䷿一-鿿─-╿▀-◿]")


def fold_characters(text, keep_accents, stats):
    """Typography to ASCII, ligatures spelled out, symbols to words, accents folded."""
    non_ascii_before = sum(ord(c) > 127 for c in text)
    text = INVISIBLE.sub("", text.replace(" ", " ").replace("\f", "\n\n"))
    for table in (TYPOGRAPHY, LIGATURES, SYMBOLS):
        for char, replacement in table.items():
            if char in text:
                text = text.replace(char, f" {replacement} " if table is SYMBOLS else replacement)
    text = re.sub(r"\(\s*\)", "", PICTOGRAMS.sub("", text))   # and parentheses they emptied
    # A symbol often follows the word it stands for ("Sun [sun glyph]"), which would double it.
    names = "|".join(re.escape(n) for n in SYMBOLS.values())
    text = re.sub(rf"\b({names})(?:\s+\1\b)+", r"\1", text)
    if not keep_accents:
        decomposed = unicodedata.normalize("NFD", text)
        text = unicodedata.normalize(
            "NFC", "".join(c for c in decomposed if not unicodedata.combining(c)))
    stats["non-ASCII chars converted"] += non_ascii_before - sum(ord(c) > 127 for c in text)
    stats["non-ASCII chars left"] = sum(ord(c) > 127 for c in text)
    return text


# --- 2. Markup debris ------------------------------------------------------------

def drop_generated_headings(text, stats, audit):
    """Drops the '=== heading ===' lines the pipeline itself inserts between pages."""
    def gone(match):
        note(audit, "generated headings", match.group(0))
        stats["headings dropped"] += 1
        return ""
    return re.sub(r"^=== .* ===[ \t]*$", gone, text, flags=re.M)


def drop_chapter_summaries(text, stats, audit):
    """Drops each 'Chapter N' heading and the editorial summary paragraph under it.

    Some editions put a one-sentence argument ("All wisdom is from God...") under every
    chapter heading, before verse 1. It is the editor's, not the text's.
    """
    def gone(match):
        note(audit, "chapter summaries", match.group(0))
        stats["chapter summaries dropped"] += 1
        return ""
    return re.sub(r"^[^\n]*\bChapter \d+[ \t]*\n\n(?!\d{1,3}:\d)(?:[^\n]+\n)+\n", gone, text,
                  flags=re.M)


def strip_glosses(text, stats):
    """Removes an edition's margin glosses and note anchors (Chaucer's `*sweet`, `<1>`).

    A gloss sits at the right margin after a run of spaces ("...swoot*,    *sweet"); the
    glossed word carries a trailing asterisk. Anchors like <1> point at end notes.
    """
    text, a = re.subn(r"[ \t]{2,}\*[^\n]*$", "", text, flags=re.M)
    text, b = re.subn(r"<\w{1,3}>", "", text)
    text, c = re.subn(r"\*", "", text)
    # Numbered end-notes, "[19. Gestes: histories, exploits; ...]", possibly over several lines.
    text, d = re.subn(r"^\[\d{1,3}\.[^\]]{0,400}\]", "", text, flags=re.M)
    stats["glosses and anchors removed"] += a + b + c + d
    return text


def strip_markup(text, entries, stats):
    """Removes emphasis markers, footnote anchors and bodies, and page markers."""
    count = 0
    if entries:
        # =Term.=-- dictionary entries: one paragraph per entry, "Term. To dream ..."
        text, n = re.subn(r"\s*=([^=\n]{1,60}?)\.?=--\s*",
                          lambda m: f"\n\n{m.group(1)}. ", text)
        count += n
    # Bracketed footnote bodies, which may span lines, and bare footnote body lines.
    text, n = re.subn(r"\[Footnote [^\]]*\]", "", text); count += n
    text, n = re.subn(r"^[ \t]*\[\d{1,3}\][ \t].*$", "", text, flags=re.M); count += n
    # Anchors and page markers.
    for pattern in (r"\[\d{1,3}\]", r"\[[A-Z]\]", r"\[\*+\]", r"\{\d{1,4}\}",
                    r"\[Pg\s*\d+\]", r"\[Page\s*\d+\]"):
        text, n = re.subn(pattern, "", text); count += n
    # Superscript notation (A^1, cm^{2}) keeps its character, loses the caret.
    for pattern in (r"\^\{([^}]*)\}", r"\^(\w)"):
        text, n = re.subn(pattern, r"\1", text); count += n
    # Emphasis markers: _italic_, =bold=, ++strike++.
    for pattern in (r"\+\+([^+\n]+)\+\+", r"=([^=\n]+)=", r"_([^_\n]+)_"):
        text, n = re.subn(pattern, r"\1", text); count += n
    text, n = re.subn(r"_+", "", text); count += n          # leftover stray underscores
    stats["markup removed"] += count
    return text


# --- 3. Page furniture -----------------------------------------------------------

PAGE_CODE = r"(?:\d{1,4}(?:-\d{1,3})?|[ivxlc]{1,6}|[A-Z]-\d{1,3})"


def drop_running_heads(text, stats, audit):
    """Drops running heads and folios, found as repeated text around a page code.

    A line like "1-2 User's Guide" or "Basic Operations 1-1" repeats on every page
    with only the page code changing. If the same remaining text occurs at least
    four times beside a page code, every such line is page furniture.
    """
    lines = text.split("\n")
    keys = []
    for line in lines:
        s = line.strip()
        key = None
        if 3 <= len(s) <= 60:
            m = re.fullmatch(rf"({PAGE_CODE})\s+(.{{3,50}})", s)
            n = re.fullmatch(rf"(.{{3,50}}?)\s+({PAGE_CODE})", s)
            rest = (m.group(2) if m else n.group(1) if n else "").lower()
            if len(re.findall(r"[a-z]", rest)) >= 3:
                key = rest
        keys.append(key)
    counts = {}
    for key in keys:
        if key:
            counts[key] = counts.get(key, 0) + 1
    kept = []
    for line, key in zip(lines, keys):
        if key and counts[key] >= 4:
            stats["running heads dropped"] += 1
            note(audit, "running heads", line)
            continue
        kept.append(line)
    return "\n".join(kept)


def strip_numeration(text, stats, audit):
    """Removes bare number lines (page numbers, sonnet numbers) and numbers trailing verse."""
    n = 0

    def counted(label):
        def repl(match):
            note(audit, label, match.group(0))
            return ""
        return repl

    text, k = re.subn(r"^[ \t]*\d{1,5}[ \t]*$", counted("bare number lines"), text, flags=re.M)
    n += k
    # Line numbers set far to the right of verse: "...convey'd;        1192".
    text, k = re.subn(r"[ \t]{3,}\d{1,5}[ \t]*$", counted("trailing line numbers"), text,
                      flags=re.M)
    n += k
    stats["numeration removed"] += n
    return text


def drop_decoration(text, stats, audit):
    """Drops rule lines (* * * * *, -----, =====), tables, and dotted leaders."""
    n = 0

    def rule_line(match):
        note(audit, "rule lines", match.group(0))
        return ""

    text, k = re.subn(r"^[ \t]*(?:[*\-=_~#.+|]\s*){3,}$", rule_line, text, flags=re.M)
    n += k
    # Table blocks: three or more consecutive lines heavy with | or box-drawing chars.
    out, block = [], []

    def is_table_line(line):
        return line.count("|") >= 2

    def flush():
        nonlocal n
        if len(block) >= 3:
            n += len(block)
            note(audit, "table blocks", f"[{len(block)} lines] {block[0]}")
        else:
            out.extend(block)
        block.clear()

    for line in text.split("\n"):
        if is_table_line(line):
            block.append(line)
        else:
            flush()
            out.append(line)
    flush()
    text = "\n".join(out)
    text, k = re.subn(r"(?:[ \t]?\.){4,}", " ", text); n += k   # dotted leaders
    stats["decoration dropped"] += n
    return text


# --- 4. Structure: text into paragraphs ------------------------------------------

LIST_START = re.compile(
    r"^(\d{1,3}[.)]|\d{1,3}:\d{1,3}(?::\d{1,3})?\.?|\([a-z0-9]{1,3}\)|[a-z]\)|[-*]|section\s)\s")


def looks_like_heading(line):
    """True for a short line that is (almost) all capitals, e.g. 'MODERN SUPERSTITIONS, &c.'."""
    s = line.strip()
    letters = [c for c in s if c.isalpha()]
    if not s or len(s) > 70 or len(letters) < 3:
        return False
    return sum(c.isupper() for c in letters) / len(letters) >= 0.85


def split_prose_block(block, stats):
    """Turns one blank-line-delimited block into one or more paragraphs.

    Hard-wrapped prose lines are joined (a hyphen at a line break is removed when
    the next word continues in lowercase). A list item or an ALL-CAPS heading
    starts a new paragraph. Indented lines (two or more leading spaces) are verse
    or quotation and keep their own lines inside the current paragraph.
    """
    paragraphs, current = [], []
    for line in block.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        starts_new = bool(LIST_START.match(stripped)) or looks_like_heading(stripped)
        # Indented lines are verse, unless they hang under a list item ("3. Be calm;
        # reassure the" / "   mother."), where they just continue the item.
        indented = line.startswith("  ") and not LIST_START.match(current[0] if current else "")
        # An indented line that starts in lowercase, right after a line with no closing
        # punctuation, is a hanging-indent continuation, not verse.
        if (indented and current and re.match(r"[a-z]", stripped)
                and not re.search(r"[.!?:;\"')]$", current[-1])):
            indented = False
        if not current or starts_new:
            if current:
                paragraphs.append(current)
            current = [stripped]
        elif indented or current[-1] == "":
            current.append(stripped)
        elif looks_like_heading(current[-1]):
            paragraphs.append(current)
            current = [stripped]
        else:
            prev = current[-1]
            if re.search(r"[A-Za-z]-$", prev) and re.match(r"[a-z]", stripped):
                current[-1] = prev[:-1] + stripped
            else:
                current[-1] = prev + " " + stripped
            stats["lines joined"] += 1
    if current:
        paragraphs.append(current)
    return ["\n".join(p) for p in paragraphs]


def mostly_indented(block):
    """True when most lines of a block start with two or more spaces (an indented verse block)."""
    lines = [l for l in block.split("\n") if l.strip()]
    return bool(lines) and sum(l.startswith("  ") for l in lines) / len(lines) >= 0.6


def short_lined(block, max_mean=56):
    """True for a block of two or more lines whose average length is short: verse, not prose."""
    lines = [l.strip() for l in block.split("\n") if l.strip()]
    return len(lines) >= 2 and sum(len(l) for l in lines) / len(lines) <= max_mean


SPEAKER = re.compile(r"^[A-Z][A-Z0-9 '&,-]{1,38}\.$")
CAST_LINE = re.compile(r"^[A-Z][A-Z0-9 .'&-]{2,}(?:,\s|\.$|$)")
# "BERTRAM, Count of Rossillon." / "LAFEW, an old Lord.": every word of the name part is
# capitalized, then a short description. Speech lines rarely look like this and are longer.
CAST_DESCRIBED = re.compile(
    r"^(?:[A-Z][A-Za-z.'-]*\s){0,4}[A-Z][A-Za-z.'-]*,\s+(?:a|an|the|son|daughter|brother|sister|"
    r"friend|servant|attendant|officer|messenger|lord|lady|duke|earl|king|queen|general|captain|"
    r"page|citizen|soldier|clown|ghost|spirit|suitor|uncle|nephew|cousin|wife|husband|in love|"
    r"beloved|bastard|steward|physician|priest|friar|gentleman|merchant|Count|Duke|Earl|Lord|"
    r"Lady|King|Queen|Prince)\b[^,]{0,60}\.?$")
# Structure of the printed play: dropped at any length (a Contents or cast list can be long).
STRUCTURE_START = re.compile(
    r"^(Dramatis|ACT\b|SCENE\b|Scene [IVX]+\.|PROLOGUE\b|EPILOGUE\b|INDUCTION\b|THE END\b|"
    r"Contents\b)", re.I)
# Stage directions: dropped only if short, so verse that merely starts with a cue word
# ("Music to hear, why hear'st thou music sadly?" is Sonnet 8) is kept.
STAGE_START = re.compile(
    r"^(Enter|Exit|Exeunt|Re-enter|Flourish|Alarum|Sennet|Music|Trumpets|Thunder)\b", re.I)


def speeches_from_block(block, stats, audit):
    """Keeps the body of a speech and drops its speaker name; drops stage directions."""
    lines = [l.rstrip() for l in block.split("\n") if l.strip()]
    if not lines:
        return []
    first = lines[0].strip()
    is_short = len(lines) <= 5
    is_structure = bool(STRUCTURE_START.match(first)) and not SPEAKER.match(first)
    is_direction = is_short and ((first.startswith("[") and lines[-1].rstrip().endswith("]")) or
                                 (STAGE_START.match(first) and not SPEAKER.match(first)))
    if is_structure or is_direction:
        note(audit, "stage directions", block)
        stats["stage directions dropped"] += 1
        return []
    # A cast list: several lines that each look like "NAME, a description" or a CAPS name.
    # A real speech has speech text after its speaker label; a cast list has more names.
    candidates = lines[1:] if SPEAKER.match(first) else lines
    cast_lines = sum(1 for l in candidates
                     if CAST_LINE.match(l.strip()) or CAST_DESCRIBED.match(l.strip()))
    if len(candidates) >= 2 and cast_lines / len(candidates) >= 0.6:
        note(audit, "cast lists", block)
        stats["cast lists dropped"] += 1
        return []
    # A block with no speaker label is kept: it is usually a later paragraph of a long speech.
    if SPEAKER.match(first):
        stats["speaker names dropped"] += 1
        lines = lines[1:]
    body = "\n".join(l.strip() for l in lines)
    body = re.sub(r"\[[^\]]*\]", "", body)          # inline directions: [Aside.] [To HELENA]
    return [body] if body.strip() else []


def split_couplets(paragraph, stats, long_run=16, min_lines=4, max_lines=10):
    """Splits a long run of rhymed verse into sentence-ended groups of whole couplets.

    A paragraph of up to `long_run` lines (a stanza, a whole sonnet) is left alone. A
    longer one is cut into groups of 4 to 10 lines with an even line count, closing at
    the first line that ends a sentence, so couplets stay together and each group
    says something complete.
    """
    lines = paragraph.split("\n")
    if len(lines) <= long_run:
        return [paragraph]
    groups, current = [], []
    for line in lines:
        current.append(line)
        count = len(current)
        ends_sentence = bool(re.search(r"[.!?]['\")]*$", line))
        if count % 2 == 0 and ((count >= min_lines and ends_sentence) or count >= max_lines):
            groups.append(current)
            current = []
    if current:                       # leftover short tail joins the previous group
        if groups and len(current) < min_lines:
            groups[-1].extend(current)
        else:
            groups.append(current)
    stats["verse runs split"] += 1
    return ["\n".join(g) for g in groups]


# --- 5. Paragraph cleanup --------------------------------------------------------

# Cross-references make no sense once an utterance stands alone.
CROSS_REFS = [
    # "See "Batteries" on page 4-12 for information."  (a whole sentence of pointing)
    re.compile(r"\s*\b[Ss]ee\s+(?:also\s+)?\"[^\"\n]+\"(?:\s+on\s+pages?\s+[\w-]+)?[^.\n]*\.?"),
    re.compile(r"\s*\[(?:[Ss]ee|section|p\.|pp\.|page|Introduction)[^\]]*\]"),
    re.compile(r"\s*\((?:[Ss]ee|[Pp]age|[Pp]ages|[Pp]\.|[Pp]p\.|section|Introduction,\s*page)[^)]*\)"),
    # A trailing pointer: ", see p. 14" / "(see page 1-17)"
    re.compile(r"[,;]\s*[Ss]ee (?:also )?(?:page|pages|p\.|pp\.|Fig\.?|Figure)\s*[\w.-]*\w"),
    # An edition citation after a maxim: "(1665, No. 134.)"
    re.compile(r"\s*\(\d{4},\s*No\.\s*\d+\.?\)"),
]
# A leading label: "Ch. 1.", "12.", "section 24.", "7.--", "XXIV.--", repeated ("Ch. 1. 1.").
LEADING_LABEL = re.compile(
    r"^(?:(?:Ch\.\s*)?\d{1,4}\.\s+|section\s+\d{1,3}\.?\s*|\([A-Za-z0-9]{1,3}\)\s+|[a-z]\)\s+"
    r"|[IVXLC]{1,8}\.\s+|\d{1,3}:\d{1,3}(?::\d{1,3})?\.?\s+|\d{1,3}\.--\s*|[IVXLC]{1,8}\.--\s*)+"
    r"(?=[A-Z\"'(\[])")
HEADING_WORDS = re.compile(
    r"^(?:Hexagram|Section|Chapter|Appendix|Part|Book|Preface|Introduction|Contents|"
    r"Note|Notes|Index|Figure|Table)\b", re.I)


def is_heading(paragraph):
    """A single short line that labels what follows instead of saying something."""
    if "\n" in paragraph:
        return False
    words = paragraph.split()
    if not words or len(words) > 9:
        return False
    if looks_like_heading(paragraph) or paragraph.endswith(":"):
        return True
    if HEADING_WORDS.match(paragraph):
        return True
    return paragraph[-1] not in ".!?\"')]" and paragraph[0].isupper()


def is_wordlike(token):
    """An ordinary word: letters only (plus ' and -), at least two of them, with a vowel."""
    core = re.sub(r"^[^A-Za-z]+|[^A-Za-z]+$", "", token)
    if core in ("a", "A", "I", "O"):                  # the real one-letter words
        return True
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z'-]+", core)) and bool(re.search(r"[AEIOUYaeiouy]", core))


def usable_utterance(paragraph):
    """False for OCR debris and fragments: too short, or too little of it is real words.

    Real prose is nearly all ordinary words. OCR debris ("fc ty Fon ty", "ae a) (3 BESS
    eis = CeCReeererir") fails on the share of wordlike tokens, or, when it is short,
    on average word length.
    """
    tokens = paragraph.split()
    if len(tokens) < 3:
        return False
    flat = re.sub(r"\s", "", paragraph)
    letters = sum(c.isalpha() for c in flat)
    words = [t for t in tokens if is_wordlike(t)]
    if letters / max(1, len(flat)) < 0.7 or len(words) / len(tokens) < 0.8:
        return False
    if len(tokens) <= 5:
        mean_length = sum(len(re.sub(r"[^A-Za-z]", "", t)) for t in tokens) / len(tokens)
        return mean_length >= 3
    return True


def merge_broken_paragraphs(paragraphs, stats):
    """Rejoins a sentence that OCR or wrapping split across paragraphs.

    A single-line paragraph that ends without punctuation, followed by one that
    starts in lowercase, is one sentence. Multi-line paragraphs (verse) are left alone.
    """
    merged = []
    for paragraph in paragraphs:
        if (merged and "\n" not in merged[-1] and "\n" not in paragraph
                and not re.search(r"[.!?:;\"')\]]$", merged[-1])
                and re.match(r"[a-z]", paragraph)):
            merged[-1] = merged[-1] + " " + paragraph
            stats["broken paragraphs rejoined"] += 1
        else:
            merged.append(paragraph)
    return merged


DICTIONARY_ENTRY = re.compile(r"^([A-Z][A-Za-z' ,()-]{1,40}?)--(?=\S)")
# Zero-width marks and variation selectors that ride along with symbols.
INVISIBLE = re.compile(r"[\u200b-\u200f\u2060\ufe00-\ufe0f\ufeff]")


def strip_bracketed_notes(paragraph):
    """Removes an editor's [bracketed notes] and the *asterisk* marks that point to them.

    A note may be opened in this paragraph and closed in a later one (or the reverse),
    so an unmatched "[" cuts to the end and an unmatched "]" cuts from the start.
    """
    if re.match(r"\*+\s", paragraph):
        return ""                                    # a footnote: "* Strictly speaking, ..."
    previous = None
    while previous != paragraph:                     # innermost first, so nested notes unwind
        previous = paragraph
        paragraph = re.sub(r"\s*\[[^\[\]]*\]", "", paragraph)
    paragraph = re.sub(r"\s*\[[^\]]*$", "", paragraph)
    paragraph = re.sub(r"^[^\[]*\]\s*", "", paragraph)
    return re.sub(r"\*+", "", paragraph).strip()


def clean_paragraphs(paragraphs, drop_patterns, stats, audit, dictionary=False, unbracket=False,
                     unbrace=False, titles=False, linenos=False):
    """Strips labels and cross-references, then drops headings, junk and unwanted lines.

    With dictionary=True, "Term--meaning" entries (a dream dictionary) become
    "Term: meaning". With unbracket=True, an editor's [bracketed notes] are removed.
    """
    kept = []
    for paragraph in paragraphs:
        paragraph = INVISIBLE.sub("", paragraph).replace("\t", " ")
        # OCR reads a capital I as "l" or "/" ("lf you", "/f you"); neither is a word.
        paragraph = re.sub(r"(?<![\w/])[l/]f\b", "If", paragraph)
        if unbracket:
            paragraph = strip_bracketed_notes(paragraph)
        if unbrace:
            paragraph = unbrace_paragraph(paragraph)
        if titles:
            paragraph = strip_title_line(paragraph)
        if linenos:
            paragraph = re.sub(r"[ \t]+\d{1,4}(?=\n|$)", "", paragraph)   # verse line numbers: "chasing, 561"
        paragraph = re.sub(r"(?:\s*\*\s*){3,}", " ", paragraph)             # "* * *" breaks inside a paragraph
        paragraph = tidy_paragraph(paragraph)
        if not paragraph:
            continue
        if dictionary:
            paragraph, n = DICTIONARY_ENTRY.subn(r"\1: ", paragraph)
            stats["dictionary entries reformatted"] += n
        for pattern in CROSS_REFS:
            paragraph, n = pattern.subn("", paragraph)
            stats["cross-references removed"] += n
        # Bullets and stray marks; OCR often reads a bullet as a lone "e" or "o".
        paragraph = re.sub(r"^(?:[-*.|>]+|[eo](?=\s+[A-Z]))\s+", "", paragraph.strip())
        stripped = LEADING_LABEL.sub("", paragraph, count=1)
        if stripped != paragraph:
            stats["labels stripped"] += 1
            paragraph = stripped
        paragraph = re.sub(r"^--+(?=\S)", "", paragraph)       # a "--item" list dash
        lowered = re.match(r"^[IVXLC]{2,8}\.\s+(?=[a-z])", paragraph)
        if lowered:                                               # "XXIV. if any should..."
            rest = paragraph[lowered.end():]
            paragraph = rest[:1].upper() + rest[1:]
            stats["labels stripped"] += 1
        if re.search(r"[^\x00-\x7f]", paragraph):
            # Greek, Hebrew, symbols: anything fold_characters could not turn into ASCII.
            # Cutting the foreign word out would leave a broken sentence, so the paragraph goes.
            note(audit, "non-ASCII residue", paragraph)
            stats["non-ASCII paragraphs dropped"] += 1
        elif paragraph[:1].islower():
            # A paragraph that begins mid-sentence is a fragment of something longer.
            note(audit, "lowercase fragments", paragraph)
            stats["lowercase fragments dropped"] += 1
        elif any(p.search(paragraph) for p in drop_patterns):
            note(audit, "per-source drop", paragraph)
            stats["paragraphs dropped (per-source)"] += 1
        elif is_heading(paragraph):
            note(audit, "headings", paragraph)
            stats["headings dropped"] += 1
        elif not usable_utterance(paragraph):
            note(audit, "junk paragraphs", paragraph)
            stats["junk paragraphs dropped"] += 1
        else:
            kept.append(paragraph)
    return kept


# --- OCR spell gate --------------------------------------------------------------

SYSTEM_DICTIONARY = "/usr/share/dict/words"
_dictionary_cache = None


def load_dictionary():
    """Lower-cased word set from the system dictionary (cached); required by `spellcheck`."""
    global _dictionary_cache
    if _dictionary_cache is None:
        try:
            with open(SYSTEM_DICTIONARY, encoding="utf-8", errors="ignore") as handle:
                _dictionary_cache = {line.strip().lower() for line in handle}
        except OSError as error:
            raise RuntimeError(f"the `spellcheck` flag needs {SYSTEM_DICTIONARY}: {error}")
    return _dictionary_cache


def is_known_word(word, dictionary):
    """True if the word, or the word minus a common English ending, is in the dictionary."""
    if word in dictionary:
        return True
    for ending, restore in (("ies", "y"), ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"), ("d", ""),
                            ("ing", ""), ("ing", "e"), ("ly", ""), ("er", ""), ("est", ""), ("'s", "")):
        if word.endswith(ending) and len(word) > len(ending) + 2 and word[:-len(ending)] + restore in dictionary:
            return True
    return False


def spellcheck_paragraphs(paragraphs, stats, audit):
    """Drops paragraphs holding a word that looks like an OCR error.

    A lowercase word is suspect when it is not in the dictionary AND occurs fewer than
    three times in the source (real jargon such as "printwheel" repeats; OCR errors such
    as "cnaracter" and "ortoa" do not). A word with a capital inside it ("RFlIsh") is
    always suspect. Capitalized words and acronyms pass, since names are not in the dictionary.
    """
    dictionary = load_dictionary()
    counts = {}
    for paragraph in paragraphs:
        for word in re.findall(r"[A-Za-z][A-Za-z']*", paragraph):
            counts[word.lower()] = counts.get(word.lower(), 0) + 1
    kept = []
    for paragraph in paragraphs:
        suspect = False
        for word in re.findall(r"[A-Za-z][A-Za-z']*", paragraph):
            if re.search(r"[a-z][A-Z]", word):
                suspect = True
            elif word.islower() and len(word) >= 3 and counts[word.lower()] < 3 and not is_known_word(word, dictionary):
                suspect = True
            if suspect:
                break
        if suspect:
            note(audit, "OCR spell gate", paragraph)
            stats["OCR-suspect paragraphs dropped"] += 1
        else:
            kept.append(paragraph)
    return kept


# --- Entry point -----------------------------------------------------------------

def tidy_paragraph(paragraph):
    """Collapses runs of spaces, closes space before punctuation, strips each line."""
    lines = (re.sub(r"\s+([,;:!?]|\.(?=\s|$))", r"\1", re.sub(r"[ \t]{2,}", " ", line)).strip()
             for line in paragraph.split("\n"))
    return "\n".join(line for line in lines if line)


def standardize(text, flags, stats, audit=None, drop_patterns=()):
    """Runs every rule in order and returns the utterances, one paragraph each.

    Args:
        text: Trimmed source text.
        flags: Set of per-source flags from the manifest `normalize` column.
        stats: A Counter that rules add their counts to.
        audit: Optional dict; when given, rules record samples of what they removed.
        drop_patterns: Compiled regexes; any paragraph matching one is dropped
            (from the manifest `drop` column, for source-specific labels).
    """
    text = drop_generated_headings(text, stats, audit)
    text = fold_characters(text, "keep-accents" in flags, stats)
    if "summaries" in flags:
        text = drop_chapter_summaries(text, stats, audit)
    if "glosses" in flags:
        text = strip_glosses(text, stats)
    text = strip_markup(text, "entries" in flags, stats)
    text = drop_running_heads(text, stats, audit)
    text = strip_numeration(text, stats, audit)
    text = drop_decoration(text, stats, audit)

    paragraphs = []
    for block in re.split(r"\n\s*\n", text):
        if not block.strip():
            continue
        if ("indented" in flags or "shortlines" in flags) and not (
                ("indented" in flags and mostly_indented(block)) or
                ("shortlines" in flags and short_lined(block))):
            note(audit, "non-verse blocks", block)
            stats["non-verse blocks dropped"] += 1
            continue
        if "speeches" in flags:
            paragraphs.extend(speeches_from_block(block, stats, audit))
        elif "unwrap" in flags:
            paragraphs.extend(split_prose_block(block, stats))
        else:
            paragraphs.append(block)
    paragraphs = [tidy_paragraph(p) for p in paragraphs]
    if "couplets" in flags:
        paragraphs = [piece for p in paragraphs for piece in split_couplets(p, stats)]
    if "unwrap" in flags:
        paragraphs = merge_broken_paragraphs(paragraphs, stats)
    paragraphs = clean_paragraphs(paragraphs, drop_patterns, stats, audit,
                                  dictionary="dictionary" in flags,
                                  unbracket="unbracket" in flags, unbrace="unbrace" in flags,
                                  titles="titles" in flags, linenos="linenos" in flags)
    if "spellcheck" in flags:
        paragraphs = spellcheck_paragraphs(paragraphs, stats, audit)
    return "\n\n".join(paragraphs) + "\n"
