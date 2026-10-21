# corpus/

Examples of the kind of thing the oracle should say: few-shot examples now, retrieval next, possibly fine-tuning later.

## The rule that matters, and what "ready" means

**Only `ready/` is safe to use as training, few-shot or retrieval data.** `raw/` and `draft/` are not.

A file in `ready/` is a list of **utterances**: each utterance is its own paragraph, separated from the next by one blank line, with no trace of the source it came from. No headings, chapter or section labels, jest or list numbers, speaker names, stage directions, page numbers, footnotes or cross-references. Verse keeps its line breaks inside a paragraph, and a stanza is a paragraph. Text is plain typewriter-printable characters. Utterances run from a line to a stanza or a few sentences (the chunker later merges under 20 words and splits over 300).

## Layout

```
corpus/
├── README.md       this file
├── sources.csv     one row per source: the single source of truth
├── inbox/          human-supplied files that cannot be downloaded (committed)
├── raw/            untouched downloads, one directory per source id.  NOT safe. (gitignored)
├── draft/          trimmed and standardized, awaiting review.         NOT safe.
│   └── <form>/<id>.txt
└── ready/          approved + cleared + reviewed.                     SAFE.
    └── <form>/<id>.txt
```

`<form>` is what the oracle might write, not where the text came from. In use: `divination`, `wisdom`, `poetry`, `drama`, `humor`, `horoscope`, `manual`, `prophecy`, `epitaph`, `aphorism`, `rhyme`, `spiritualism`, `news`, `nonsense`. Add a form only when a source needs it, and list it here.

Nothing in `draft/` or `ready/` is edited by hand. Both come from `scripts/dev/corpus/corpus.py` reading `raw/` and the manifest, deterministically: re-running `clean` gives byte-identical files. To change a text, change its row in `sources.csv` (or a rule in `normalize.py`) and re-run.

## Rules

1. **Rights.** Every source records its rights and the basis for that claim.
   - `public-domain` and `cc0`: the normal case.
   - `owner-cleared`: not public domain. The project owner decided to use it. Covers mid-20th-century and similar material (the IBM/Lexmark Wheelwriter manuals, the dad-jokes list) that has no realistic economic stake. It is committed to the private repo, and `corpus.py publish-check` fails if any such text is tracked, so run it before the repo is ever made public.
   - `in-copyright` and `unknown`: logged as `excluded` or `candidate`, never fetched. The pipeline refuses.
2. **Every source gets a row in `sources.csv` first.** If it isn't in the manifest, it isn't in the corpus.
3. **Source furniture does not reach `ready/`.** Title pages, copyright notices, contents, indexes, ads, transcriber notes, footnotes, and prefaces, forewords and introductions that talk *about* the book, by anyone. The test: text starts at the first section of the work itself; an opening essay that is part of the work stays.
4. **A human reviews before promotion.** `promote` refuses anything not marked `reviewed`.

## sources.csv columns

| Column | Meaning |
|---|---|
| `id` | Short stable slug. Names the directories and files. |
| `form` | One of the forms above. |
| `title`, `author`, `year` | As published. `year` is the first publication year when known. |
| `rights`, `rights_basis` | See Rules. The basis says why we believe it. |
| `url` | Where it came from; decides how it is fetched. `inbox/<file>` means a file in `corpus/inbox/`. Handlers: Project Gutenberg, Wikisource, direct `.pdf`, direct `.txt`, inbox. |
| `status` | `candidate` (found, rights not confirmed), `approved` (ok to fetch), or `excluded` (rejected, kept on record). |
| `reviewed` | `yes` once a human has checked the draft. Blank until then. |
| `start_marker`, `end_marker` | Where the real text begins and ends (see Trim markers). Blank means the whole text. |
| `extract` | PDFs only. Blank = text layer unless nearly empty, then OCR; `ocr` = force OCR (scrambled text layer); `layer` = force the embedded text. |
| `normalize` | Space-separated flags for standardizing this source: `unwrap` (join hard-wrapped prose), `couplets` (split long runs of rhymed verse into sentence-ended groups), `speeches` (plays: keep speech bodies), `entries` (one paragraph per `=Term.=--` entry), `dictionary` (`Term--meaning` becomes `Term: meaning`), `glosses` (strip an edition's margin glosses and note anchors), `summaries` (drop the editor's summary under each "Chapter N" heading), `indented` (keep only mostly-indented blocks: quoted epitaphs, reprinted ads, quatrains), `shortlines` (keep only blocks of short lines: verse in a book that also has prose), `unbracket` (drop an editor's [bracketed notes] and the asterisks pointing to them), `unbrace` (keep [inserted words], drop the brackets), `titles` (verse: drop a poem's title sitting directly above its first line), `linenos` (verse: strip line numbers at line ends), `spellcheck` (OCR sources: drop paragraphs with a word that is not in the system dictionary and not repeated in the source), `keep-accents`. |
| `drop` | Regexes, separated by `;;`. Any utterance matching one is dropped (for source-specific labels and notes). |
| `notes` | Anything a future reader needs, including a `Trim:` line saying why the markers are where they are. |

## Trim markers

A marker is the exact text of a whole line (surrounding spaces ignored). Add `#N` to pick the Nth exact occurrence, which is how you skip a table-of-contents entry that repeats a chapter heading.

- `start_marker`: kept text begins at that line, inclusive. Default occurrence is the first.
- `end_marker`: kept text stops before that line, exclusive. Default occurrence is the last. `#-1` means the last, `#1` the first.
- A marker that matches nothing is an error, never silently ignored.

## Standardization (in general)

After trimming, `normalize.py` turns the text into utterances. In order: typography to typewriter-safe ASCII (curly quotes, dashes, accents, ligatures, symbols); markup and footnote debris removed; page furniture removed (page numbers, running heads such as "1-2 User's Guide", trailing verse line numbers, rule lines, tables); text split into paragraphs (prose unwrapped, list items and speeches separated, headings dropped); then per-paragraph cleanup (leading numbers and labels stripped, cross-references removed, source-specific `drop` patterns applied, and junk and fragments such as OCR debris dropped). `clean -v` prints what each rule changed; `clean --audit N` prints N samples of what was removed, so you can check that real content was not lost. Check the audit whenever a rule or a source's flags change. Before a human review, run `audit.py` over `ready/`: the checks that should read zero everywhere are `nonascii`, `lower_start`, `leading_label`, `url_email` and `markup`.

## Chunks and draw weights

`chunks/<register>/<id>.jsonl` is made from `ready/` by `chunk.py`, and is what the voice models use. Each line is one chunk with its source, register and word count, and nothing about the rest of the corpus or its weight. Never edit a chunk file by hand: change `ready/` or the manifest and re-run `chunk.py`.

- **Size.** Utterances over 150 words are cut at sentence breaks (line breaks for verse) into near-equal pieces. Shorter ones are kept whole; `--min-words N` would merge them forward. Exact duplicates anywhere in the corpus are dropped, and the survivor records its `repeats`.
- **Weights.** We weight the corpus to account for differences in quantity. Every chunk is in one pool and has its own chance of being drawn, set so each register gets its target share over many draws. The chance is computed from the register totals when the corpus is loaded and is never stored, so adding a chunk changes only its own register's numbers. Any combination of draws stays possible, including several from one register. All the weights live in one file, `chunks/weights.json`, laid out as a tree that mirrors the folders: each register has a relative `share` (equal to start) and lists every one of its sources with a `weight` (how often it is drawn compared with its neighbors in the same register; a source left out counts 1.0). Edit it by hand and nothing else needs regenerating. `draw_weights.py` prints the result.

## Commands

```
scripts/dev/corpus/corpus.py add --id ID --form F --title T --rights R --basis B --url U [--status approved]
scripts/dev/corpus/corpus.py set ID column=value ...  # edit markers, flags, drop patterns, status
scripts/dev/corpus/corpus.py process ID               # fetch, clean, audit, sample (stops before promote)
scripts/dev/corpus/corpus.py status                   # what is on disk
scripts/dev/corpus/corpus.py mix [--prefix bible-]    # weighted share of each source and form (weights from chunks/weights.json)
scripts/dev/corpus/corpus.py fetch [--id ID]          # raw/
scripts/dev/corpus/corpus.py inspect --id ID          # landmarks + first/last lines, to choose markers
scripts/dev/corpus/corpus.py clean [-v] [--audit N]   # draft/
scripts/dev/corpus/corpus.py inspect --id ID --trimmed
scripts/dev/corpus/corpus.py review [--id ID]         # one-screen report per draft
scripts/dev/corpus/corpus.py promote [--id ID]        # ready/
scripts/dev/corpus/corpus.py publish-check            # fail if non-public-domain text is committed
scripts/dev/corpus/chunk.py [--max-words N] [--min-words N] [--check]   # ready/ -> chunks/ (JSONL)
scripts/dev/corpus/draw_weights.py                  # expected share of draws per register
scripts/dev/corpus/audit.py [ID CHECK]               # artifact rates per file of ready/ (--dir draft for drafts)
scripts/dev/corpus/gutenberg_catalog.py search REGEX  # find Gutenberg candidates offline
```

## Adding a source

The manifest is edited only through `add` and `set`, never by hand or by scratch scripts.

1. **`add`** the source (status `candidate` until its rights are confirmed, then `approved`).
2. **`process ID`**: fetches, shows landmark lines with the exact marker to paste (when there are no markers yet), cleans with an audit of what was removed, shows the trimmed edges, and samples utterances from the start, middle and end.
3. **`set ID start_marker=... end_marker=... normalize=... drop=... notes=...`** (put a `Trim:` sentence in `notes`), then **`process ID`** again. Repeat until it reads right. Prose gets `unwrap`; verse does not; rhymed runs get `couplets`.
4. **Read the audit.** It shows samples of what each rule removed. This is where real content being lost shows up.
5. **Sign-off:** a human checks the result and sets `reviewed=yes` (`set ID reviewed=yes`). Then **`promote`**.

A long file with several self-contained parts (Chaucer, Shakespeare) becomes several rows with the same `url` and different markers; a part that is mostly plot, or is only banter, is left out.

Promoting is not the end: the text still has to be chunked and given a weight. The whole path, from choosing a register to the commit, is in `documentation/adding-material.md`.

See `documentation/corpus-notes.md` for the process, tool versions, lessons learned, and known gaps.
