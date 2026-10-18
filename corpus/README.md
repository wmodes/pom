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

`<form>` values in use: `divination`, `poetry`, `humor`, `horoscope`, `office`, `news`. Add a new form only when a source needs it, and add it here.

## sources.csv columns

| Column | Meaning |
|---|---|
| `id` | Short stable slug, `<work>-<author-or-origin>-<year>`. Used for directory and file names. |
| `form` | One of the forms above. Decides the `processed/` subdirectory. |
| `title`, `author`, `year` | As published. `year` is the first publication year when known. |
| `rights` | `public-domain`, `cc0`, `in-copyright`, or `unknown`. |
| `rights_basis` | Why we believe that: for example "Project Gutenberg, US public domain". |
| `url` | Where it came from. |
| `status` | `candidate` (found, rights not yet confirmed), `approved`, `fetched`, `processed`, or `excluded`. |
| `notes` | Anything a future reader needs. |

Only `rights` of `public-domain` or `cc0` may reach `approved` or later. Rows with other rights stay as `excluded` so the decision is on record.

## Workflow

`candidate` -> confirm rights -> `approved` -> fetch to `raw/<id>/` (`fetched`) -> clean to `processed/<form>/<id>.txt` (`processed`).
