# corpus/

Source text for the oracle's voice: few-shot examples now, retrieval later, possibly fine-tuning after that.

## The one rule that matters

**Only `ready/` is safe to use as training, few-shot or retrieval data.** Everything in it is openly licensed, trimmed of front and back matter, and signed off by a human. `raw/` and `draft/` are not safe and must never be read by anything that feeds the machine.

## Layout

```
corpus/
├── README.md       this file
├── sources.csv     one row per source: the single source of truth
├── raw/            untouched downloads, one directory per source id.  NOT safe. (gitignored)
├── draft/          machine-cleaned and trimmed, awaiting review.      NOT safe.
│   └── <form>/<id>.txt
└── ready/          approved + openly licensed + reviewed.             SAFE.
    └── <form>/<id>.txt
```

`<form>` is what the oracle might write, not where the text came from. In use: `divination`, `wisdom`, `poetry`, `drama`, `humor`, `horoscope`, `manual`. Expected next: `prophecy`, `epitaph`, `aphorism`, `riddle`, `news`. Add a form only when a source needs it, and list it here.

Nothing in `draft/` or `ready/` is edited by hand. Both are produced by `scripts/dev/corpus/corpus.py` from `raw/` and the manifest, deterministically (re-running `clean` gives byte-identical files). To change a text, change its row in `sources.csv` and re-run.

## Rules

1. **Public domain (or equally open) only.** Anything in copyright, or with unknown rights, is logged as `excluded` and never fetched.
2. **Every source gets a row in `sources.csv` first.** If it isn't in the manifest, it isn't in the corpus.
3. **Interstitial text does not reach `ready/`.** That means title pages, copyright notices, contents, editor introductions, indexes, publisher ads, transcriber notes, collected footnotes and `[Illustration]` markers. Headings that carry structure (chapter and hexagram titles) stay.
4. **A human reviews before promotion.** `promote` refuses anything not marked `reviewed`.

## sources.csv columns

| Column | Meaning |
|---|---|
| `id` | Short stable slug, `<work>-<author-or-origin>-<year>`. Names the directories and files. |
| `form` | One of the forms above. Decides the subdirectory. |
| `title`, `author`, `year` | As published. `year` is the first publication year when known. |
| `rights` | `public-domain`, `cc0`, `in-copyright`, or `unknown`. |
| `rights_basis` | Why we believe that, for example "Project Gutenberg, US public domain". |
| `url` | Where it came from. Decides how it is fetched (Gutenberg and Wikisource so far). |
| `status` | `candidate` (found, rights not confirmed), `approved` (rights confirmed, ok to fetch), or `excluded` (rejected, kept so the decision is on record). |
| `reviewed` | `yes` once a human has checked the draft's start, end and content. Blank until then. |
| `start_marker` | Where the real text begins (see Trim markers). Blank means from the start. |
| `end_marker` | Where it ends. Blank means to the end. |
| `notes` | Anything a future reader needs, including a `Trim:` line saying why the markers are where they are. |

Only `rights` of `public-domain` or `cc0` may be `approved`; the pipeline refuses anything else, and `promote` re-checks.

## Trim markers

A marker is the exact text of a whole line (surrounding spaces ignored). Add `#N` to pick the Nth exact occurrence, which is how you skip a table-of-contents entry that repeats a chapter heading.

- `start_marker`: kept text begins at that line, inclusive. Default occurrence is the first.
- `end_marker`: kept text stops before that line, exclusive. Default occurrence is the last. `#-1` means the last, `#1` the first.
- A marker that matches nothing is an error, never silently ignored.

## Adding a source

```
scripts/dev/corpus/corpus.py status                  # what is on disk
scripts/dev/corpus/corpus.py fetch --id ID           # raw/
scripts/dev/corpus/corpus.py inspect --id ID         # landmarks + first/last lines
scripts/dev/corpus/corpus.py clean --id ID           # draft/
scripts/dev/corpus/corpus.py inspect --id ID --trimmed   # edges after your markers
scripts/dev/corpus/corpus.py promote --id ID         # ready/
```

1. **Add a row** with `status` `candidate`. Confirm the rights, record the basis, then set `approved`.
2. **`fetch`**, then **`inspect`**. It lists landmark-looking lines (contents, preface, index, "THE END", transcriber notes) with the marker string to paste, and shows the first and last lines.
3. **Choose markers** and put them in the row, with a `Trim:` sentence in `notes` explaining the choice.
4. **`clean`**, then **`inspect --trimmed`** and read the edges. Skim the middle too: cleaning does not fix OCR errors or editorial asides inside the text. Repeat 3 and 4 until it is right.
5. **Review.** When you are satisfied, set `reviewed` to `yes`.
6. **`promote`**. It copies the draft to `ready/` only if the row is approved, openly licensed and reviewed, and tells you why if not.

A source on a host the script doesn't know needs a handler added to `scripts/dev/corpus/corpus.py` first (Gutenberg and Wikisource exist). If a fetch or parse needs a new rule, change the script, not the output, so every source stays reproducible.
