# corpus/

Source text for the oracle's voice: few-shot examples now, retrieval later, possibly fine-tuning after that.

## Rules

1. **Public domain (or equally open) only.** Anything in copyright, or with unknown rights, is logged and excluded.
2. **Every source gets a row in `sources.csv`** before any text for it exists here. The manifest is the single source of truth; if it isn't in the manifest, it isn't in the corpus.
3. **Organize by form, not by where it came from.** The form is what the oracle might write: divination readings, poetry, humor, and so on.
4. **Raw is disposable, processed is the record.** Raw downloads are reproducible from the manifest and are not committed.

## Layout

```
corpus/
├── README.md       this file
├── sources.csv     one row per source (see columns below)
├── raw/            fetched originals, one directory per source id (gitignored)
└── processed/
    └── <form>/     cleaned plain text, one file per source: <id>.txt
```

`<form>` values in use: `divination`, `wisdom`, `poetry`, `drama`, `humor`, `horoscope`, `manual`. Add a new form only when a source needs it, and add it here. Forms we expect to need next: `prophecy`, `epitaph`, `aphorism`, `riddle`, `news`.

## sources.csv columns

| Column | Meaning |
|---|---|
| `id` | Short stable slug, `<work>-<author-or-origin>-<year>`. Used for directory and file names. |
| `form` | One of the forms above. Decides the `processed/` subdirectory. |
| `title`, `author`, `year` | As published. `year` is the first publication year when known. |
| `rights` | `public-domain`, `cc0`, `in-copyright`, or `unknown`. |
| `rights_basis` | Why we believe that: for example "Project Gutenberg, US public domain". |
| `url` | Where it came from. |
| `status` | A human decision: `candidate` (found, rights not yet confirmed), `approved` (rights confirmed, ok to fetch), or `excluded` (rejected, kept so the decision is on record). |
| `notes` | Anything a future reader needs. |

Only `rights` of `public-domain` or `cc0` may be `approved`. The pipeline refuses to fetch anything else.

Whether a source has been fetched or cleaned is *not* in the manifest; that is what is on disk. Ask the pipeline:

## Workflow

```
scripts/dev/corpus/corpus.py status          # what is on disk, per source
scripts/dev/corpus/corpus.py fetch [--id ID] # approved sources -> raw/<id>/
scripts/dev/corpus/corpus.py clean [--id ID] # raw/<id>/ -> processed/<form>/<id>.txt
```

1. Add a row as `candidate`. Confirm the rights, then set it to `approved`.
2. `fetch`, then `clean`. Both skip work already done; `fetch --force` redownloads.
3. Look at the processed text before relying on it. Cleaning strips licence boilerplate and normalizes whitespace; it does not fix OCR errors or editorial notes.

Supported hosts so far: Project Gutenberg and Wikisource. A source on another host needs a handler added to the script first.
