# Corpus notes: how the corpus is built, and how to repeat it

Written 2026-10-18 while building the first batch. This is the *process and lessons*. The rules and column reference are in [`corpus/README.md`](../corpus/README.md); the per-source decisions are in the `notes` column of [`corpus/sources.csv`](../corpus/sources.csv). If this file and the code disagree, the code wins.

## What the corpus is for

Examples of the kind of thing the oracle should say: few-shot examples now, retrieval next, possibly fine-tuning later. Only text in `corpus/ready/` (and the chunks derived from it) is safe to feed to anything. Everything upstream of `ready/` is scratch.

The key design decision (Wes, 2026-10-18): **`ready/` is a list of utterances, one per paragraph, with no trace of their source**: no headings, labels, numbering, speaker names or references. Blank-line-delimited paragraphs are the whole format. This also keeps the oracle's output from looking sourced.

## Stages

```
raw/      untouched download                                  not safe, gitignored
draft/    trimmed and standardized into utterances by script  not safe, committed
ready/    approved, cleared, reviewed by a human              SAFE
chunks/   ready/ paragraphs, merged under 20 words and split  SAFE (planned, not built yet)
          over 300 (JSONL with metadata)
inbox/    human-supplied files that cannot be fetched, committed
```

Every transformation is a script reading the manifest. Nothing in `draft/` or `ready/` is edited by hand. Cleaning is deterministic: re-running `clean` on the same `raw/` gives byte-identical files (checked with SHA-256 across all drafts).

## Tools

All in `scripts/dev/corpus/`, standard-library Python on the Mac:

| Script | Job |
|---|---|
| `corpus.py` | `add`, `set`, `process`, `status`, `fetch`, `inspect`, `clean` (`-v`, `--audit N`), `review`, `promote`, `publish-check` |
| `chunk.py` | Makes `chunks/` from `ready/`: splits over-long utterances, drops exact duplicates |
| `draw_weights.py` | Draw odds that give each register its target share, computed at load time from the chunk counts (never stored); all weights in `chunks/weights.json` |
| `audit.py` | Read-only artifact rates per `ready/` (or `draft/`) file, and examples of any check; the pre-review sweep |
| `normalize.py` | The standardizer that turns trimmed text into utterances; imported by `corpus.py` |
| `gutenberg_catalog.py` | `update`, `search`, `authors`: offline search of Gutenberg's whole catalog |

External programs (Homebrew): `poppler` (`pdftotext`, `pdfinfo`, `pdftoppm`) and `tesseract` for PDFs. Versions used: Python 3.14.7, tesseract 5.5.0, poppler pdftotext 26.04.0, macOS 26.6.2. **OCR output depends on the tesseract version**, so a different version can give slightly different text.

## Repeating it from scratch

```
git clone <repo> && cd pom
brew install poppler tesseract                       # only needed for PDF sources
scripts/dev/corpus/corpus.py fetch                   # raw/ (not committed; ~2s delay per request)
scripts/dev/corpus/corpus.py clean                   # draft/ (OCR sources take minutes)
scripts/dev/corpus/corpus.py review                  # read the reports
scripts/dev/corpus/corpus.py promote                 # ready/ (needs reviewed=yes in the manifest)
```

`ready/` is committed, so a fresh checkout already has the text. You only need to run the pipeline to rebuild it or add sources.

## Adding a source: the working procedure

The short version: `add`, then `process ID`, then adjust with `set` and re-run `process`, read the audit, human sign-off, `promote`. The manifest is only ever changed through `add` and `set`. After `promote` the text still needs chunking and a weight; the whole path is in `documentation/adding-material.md`. In detail:

1. **Find it.** For books: `gutenberg_catalog.py search 'regex'`. Gutenberg only distributes works it has cleared for the US, which is a strong hint, not a legal opinion. For other hosts see "Where sources came from" below.
2. **Record it first.** `corpus.py add --id ... --form ... --title ... --rights ... --basis ... --url ...`, as `candidate` until the rights are confirmed (or straight to `approved`). The pipeline refuses to approve anything that is not cleared.
3. **`process ID`.** It fetches, and with no markers yet it prints the landmark-looking lines (contents, preface, index, "THE END", notes) with the exact marker string to paste, plus the first and last lines. When a heading repeats (contents entry, then the real heading) it gives `text#N`. Then it cleans with an audit, shows the trimmed edges, and samples utterances from the start, middle and end.
4. **Choose markers and flags with `set`.** `start_marker` is the first line of the real text; `end_marker` is the first line after it. Prose gets `unwrap`; verse does not; rhymed runs get `couplets`; plays get `speeches`; an edition with margin glosses gets `glosses`. Source-specific junk goes in `drop`. Write a `Trim:` sentence in `notes`. Re-run `process ID`.
5. **Read the audit.** It samples what each rule removed; this is how real content being dropped with the furniture gets caught.
6. **Sign-off.** A human checks the draft and sets `reviewed=yes`. Then **`promote`**.

A long file with several self-contained parts (Chaucer, Shakespeare) becomes several rows with the same `url` and different markers. A part that is mostly plot or only banter is left out.

### What counts as front and back matter

Title pages, copyright and warranty notices, contents, indexes, publisher ads, transcriber notes, collected footnotes, `[Illustration]` markers, and prefaces, forewords and introductions that talk *about* the book, by anyone. The test: text starts at the first section of the work itself. An opening essay that is part of the work stays.

## Where sources came from, and what worked

| Host | How | Notes |
|---|---|---|
| Project Gutenberg | `fetch` downloads `cache/epub/N/pgN.txt`; the cleaner strips the licence header and footer and the "Produced by" credit | Most of the corpus. Catalog search is offline (`gutenberg_catalog.py`). |
| Wikisource | `fetch` lists a title's subpages with the API and renders each with `action=parse`, then strips HTML | Used for Legge's I Ching. Only hexagrams 1-32 are transcribed, and 32 is partial. |
| Direct PDF | `fetch` saves `source.pdf`; `clean` uses the text layer, or OCR | See "PDFs" below. |
| Direct `.txt` | `fetch` saves `source.txt` | Used for the Internet Archive's own OCR text (`*_djvu.txt`). |
| `inbox/` | The file is committed in `corpus/inbox/` and the manifest `url` is `inbox/<name>` | For Drive docs that need sign-in, and for original writing from contributors. |

### PDFs

- `extract` column: blank = use the text layer unless it is nearly empty (under 40 words per page), then OCR. `ocr` = force OCR. `layer` = force the embedded text.
- Some scans carry a **scrambled** text layer (the Personal Wheelwriter 1 guide has words out of order), which the automatic check cannot catch. Use `extract=ocr` for those.
- OCR runs at 300 dpi with tesseract and is cached as `raw/<id>/ocr.txt`. It is slow: the 84-page Personal Wheelwriter 1 scan took about 25 minutes to render at 300 dpi, so run it in the background. Diagrams produce garbage lines (keyboard layouts in the Wheelwriter guides); the standardizer's junk filter drops most of them.

## Rights decisions

- `public-domain` and `cc0`: the normal case. Basis is recorded per source.
- `in-copyright` and `unknown`: logged as `excluded` or `candidate`, never fetched. The pipeline refuses.
- `owner-cleared`: **not public domain.** Wes decided on 2026-10-18 to use these in this private project, on the grounds that mid-20th-century and similar material has no realistic economic stake: the IBM and Lexmark Wheelwriter manuals and the dad-jokes list so far, and mid-century material in general. The text is committed to the private repo.
  - Do not publish these. `corpus.py publish-check` exits non-zero if any non-public-domain text is tracked by git; run it before making the repo public or sharing it.
  - The CadetWriter user guide is `unknown`: that repo declares no license. Ask the authors, or decide it as `owner-cleared`.

## Lessons (each became a rule or a tool)

| What went wrong | What we did |
|---|---|
| Google Drive listing returned empty pages that were not empty; one document at the top level was missed | Treat empty listings as unreliable. Re-list every folder and follow every page token. Keep to the shared folder only. |
| Gutendex (Gutenberg's search API) timed out and returned 403 to the default Python user agent | Use the offline catalog CSV instead. |
| Wikipedia-family servers returned 429 | Identify the client in the User-Agent (repo URL, not an email), rate-limit to one request per 2 s, honor `Retry-After`. |
| A failed download left a half-finished raw directory that the next run treated as complete | Download into `<id>.partial` and rename only on success. One source's failure no longer stops the others. |
| I started three books at their prefaces and the I Ching at Legge's introduction, against my own rule | Wrote the rule down precisely (prefaces are front matter, even the author's). Added `review`, and checked all 12 starts and ends before showing them. |
| Wikisource's plain-text API returns nothing for transcluded wrapper pages | Use `action=parse` and convert the HTML. |
| Non-breaking spaces broke a header regex | Match `\s`, not a literal space. |
| A Wikisource page was a partial transcription and ended mid-sentence | End the text at the last complete section; strip Wikisource's "scan-backed" notice. |
| sacred-texts.com is behind a bot challenge | Not circumvented. Use another host. |
| The Xavier server resets Python connections | Fetch the byte-identical copy from the CadetWriter repo (SHA-256 verified). |
| The Personal Wheelwriter 1 PDF has a scrambled text layer | Added the `extract` column. |
| The standardizer's junk filter counted one-letter words ("a", "I") as non-words, dropping jokes like "I used to be a baker, but..." | Found only because the audit of the dad jokes showed a word-count drop on a source that should have been lossless. Fixed; the lesson is to audit small clean sources as well as messy ones. |
| A play's stage-direction rule treated Sonnet 8 ("Music to hear...") as a direction because it starts with a cue word | Stage directions are short; require that. Found by reading the audit of removed blocks. |
| An early noise filter worked on single lines and would have dropped printwheel part numbers, a voltage spec and a phone number | Dropped the line-level filter. Quality is judged per utterance instead (below). |
| Manifest rows were being added and edited with one-off scripts in a scratch folder | Added `add`, `set` and `process` so the whole intake is in the repo and repeatable. |
| A rule that dropped play blocks without a speaker label threw away real speeches: a long speech's later paragraphs have no label | Removed the rule. Cast lists are detected by their own shape (several name-and-description lines after the label), tightened after the first version also swallowed ordinary speeches. |
| A full sweep of `ready/` after promotion found Greek and Hebrew text, jest and maxim numbers (`1033.`, `XXVIII.`, `7.--`), editors' bracketed notes (all of Robert's Rules), Douay footnotes ("Thy arms... That is, ..."), lowercase fragments, a printer's colophon, indexes, bibliographies, dedications and a prose "Argument" in Shakespeare's poems | Added `audit.py` and run it before every review. General rules: drop any paragraph with non-ASCII left after folding, and any that starts mid-sentence; wider label patterns; flags `unbracket`, `unbrace`, `titles`, `linenos`. The rest became per-source `drop` patterns and tighter trim markers. |
| Three OCR manuals (Wheelwriter) passed every earlier check yet held "cnaracter", "ortoa", "RFlIsh" and table debris | Added the `spellcheck` flag: drop a paragraph that has a word absent from the system dictionary and repeated fewer than three times in the source. Real jargon repeats; OCR errors do not. |
| Making stage-direction detection require "short blocks" (to save Sonnet 8) let long Contents and cast blocks through | Split the cues: structural ones (ACT, SCENE, Contents, Dramatis) drop at any length; only weak cues like "Music" need the short-block test. |

## Standardization (in general)

After trimming, `normalize.py` turns a source into utterances. The steps, in order:

1. **Characters.** Typography a typewriter cannot print becomes ASCII: curly quotes, dashes, ellipses, ligatures, accents folded, fractions, astrological symbols turned into words, pictograms (hexagram glyphs, Chinese characters) removed.
2. **Markup.** Italic and bold markers, footnote anchors and bodies, page markers, and the `=Term.=--` markup of the dream book are removed or restructured.
3. **Page furniture.** Page numbers, running heads and folios (found by repetition), line numbers trailing verse lines, rule lines (`* * *`), tables, and dotted leaders.
4. **Structure.** Text is split into paragraphs. Hard-wrapped prose is joined (`unwrap`); list items and plays' speeches become separate paragraphs; hanging indents are continuations; verse keeps its line breaks.
5. **Paragraph cleanup.** Leading labels ("Ch. 1.", "12.", "(a)", "section 24.") and bullets are stripped; cross-references ("see page 14", "[See p. 161]") are removed; headings are dropped (short ALL-CAPS lines, or short lines with no closing punctuation); per-source `drop` patterns are applied; and fragments and OCR debris are dropped (too short, or too few ordinary words).

Per-source behavior comes from the manifest's `normalize` flags and `drop` patterns, never from hand edits. **The audit is the safeguard**: `clean --audit N` samples what each rule removed, so a human can see whether real content was lost. It has caught real bugs (see Lessons). Rules are heuristics, so run the audit whenever a rule or a source's settings change, and sample the output of every source before promoting it.

What it cannot fix: headings glued to the first line of a paragraph by OCR (they stay), inline diagram residue inside a sentence, and scholarly notes that look like text (the I Ching uses `drop` patterns for Legge's commentary).

## Known gaps

- I Ching: hexagrams 1-31 only. Need a source for 32-64 (Internet Archive scan of Sacred Books of the East vol. 16 is the lead).
- The Wheelwriters Service Manual (candidate) is held for noisy OCR; the CadetWriter user guide (candidate) has no license.
- Pope's *Essay on Criticism* (Gutenberg #7409) has some unpunctuated lines and a few missing apostrophes in the digitization itself ("critic share"); not fixable by us.
- Chaucer is carved into self-contained parts (the General Prologue, the Wife of Bath's Prologue, the Pardoner's, Nun's Priest's and Manciple's tales); the plot-driven tales are left out. The edition is Gutenberg #2383 (modernized spelling). Shakespeare is split into plays, sonnets and narrative poems.
- No haiku proper from a public-domain source yet; Gutenberg has none under the obvious searches.
- 1980s satanic-panic material has no public-domain source; use original writing via `inbox/`.
- The rest of the verified Gutenberg batch (Waley and other Chinese poems, Theophrastus, epitaphs, Lear and Mother Goose, maxims, spiritualism, Nostradamus) is in the manifest as `candidate` but not yet fetched or trimmed.
- Chunking is now a size policy over `ready/` paragraphs (merge under 20 words, split over 300, JSONL with source metadata): designed, not built.
