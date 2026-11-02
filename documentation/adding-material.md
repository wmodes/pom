# Adding new material to the library

How a new text gets from "I found a book" to "the oracle can draw from it". It was exercised end to
end by adding *Alice's Adventures in Wonderland* as a new shelf. The path:

```
find it -> add -> process -> set markers/flags -> audit -> promote -> catalog -> circulation -> verify -> commit
```

The commands below use `python`, meaning the pom venv's Python (`/Users/wmodes/dev/envs/pom/bin/python`).
Library scripts are in `scripts/dev/library/`. For what each library column and flag means, see
`library/README.md`; for how the standardizer works and what went wrong before, see
`documentation/library-notes.md`.

## 1. Decide where it belongs

- **Rights first.** Only cleared text is fetched. Public domain is the normal case; mid-century
  material the owner has decided to use is `owner-cleared`. Project Gutenberg works are a strong hint
  of US public domain, not a legal opinion.
- **Pick a shelf.** A shelf is *what the oracle might say* (`poetry`, `manual`, `epitaph`,
  `nonsense`...), not where the text came from. See the list in `library/README.md`.
  - **Add to an existing shelf** when the voice fits: the new holding joins that shelf's pool and
    shares its slice of the draws with its neighbors. This is the easy case.
  - **Start a new shelf** only when the voice is genuinely different. Shelves share the draws
    equally by default, so a new one takes a slice from every other shelf (13 shelves gave
    1/13 each; 14 give 1/14).
- **Prefer passages that stand alone** over mid-story narrative, though fragments that need their
  context can be a feature.

## 2. Bring it in

```
python scripts/dev/library/gutenberg_catalog.py search 'title regex'     # find a Gutenberg book
python scripts/dev/library/library.py add --id SHELF-author-title-year --shelf SHELF \
    --title "..." --author "..." --year 1865 --rights public-domain \
    --basis "why we believe it" --url https://www.gutenberg.org/ebooks/N --status approved
python scripts/dev/library/library.py process ID
```

`process` fetches the text, lists landmark lines (contents, "THE END", ...) with the exact marker to
paste, cleans it, and shows the start, middle and end of the result. The manifest is changed only
through `add` and `set`, never by hand.

## 3. Trim and clean

```
python scripts/dev/library/library.py set ID "start_marker=..." "end_marker=..." "normalize=unwrap" \
    "notes=Trim: what was cut and why."
python scripts/dev/library/library.py process ID          # repeat until it reads right
```

Prose gets `unwrap`; verse does not; rhymed runs get `couplets`; plays get `speeches`. Holding-specific
junk goes in `drop` patterns. Front and back matter (title page, contents, prefaces, indexes,
bibliographies, dedications, footnotes) must not reach `ready/`.

## 4. Check it, then promote

```
python scripts/dev/library/audit.py --dir draft                   # artifact rates; look at your new file's row
python scripts/dev/library/audit.py --dir draft ID lower_start    # examples of any check
```

The checks that should read zero are `nonascii`, `lower_start`, `leading_label`, `url_email` and
`markup`; the others are prompts to read, not verdicts. Read some of the draft yourself, plus its
first and last utterances, where leaked front and back matter shows up. When it is right:

```
python scripts/dev/library/library.py set ID reviewed=yes         # the human sign-off
python scripts/dev/library/library.py promote --id ID
```

## 5. Catalog it and weigh it

```
python scripts/dev/library/cataloguer.py
```

This rewrites `library/passages/` (only the new holding's file changes) and prints a report. Watch for
warnings: *"shelf(s) not in circulation file"* or *"holding(s) not in circulation file"* mean the new
material counts 1.0 until you list it. That works, but listing it makes the intent visible.

Edit `library/passages/circulation.json` by hand. Under the shelf, add the holding with a weight (1.0
unless it should come up more or less often than its neighbors); for a new shelf, add the whole
entry with a `share` (1.0 matches the others):

```json
"nonsense": {"share": 1.0, "holdings": {"nonsense-carroll-alice-1865": 1.0}}
```

Nothing else needs regenerating when a weight changes.

## 6. Verify

```
python scripts/dev/library/sample.py --pulls 5                   # see what actually gets drawn
python scripts/dev/library/sample.py --stats 3000                # each shelf's share vs its target
python scripts/dev/library/circulation.py                       # the odds table
python -m pytest -q                                             # the whole suite, including the real library
```

The tests check the committed library against `circulation.json`, so they fail (by design) if the weights
file lists something that does not exist.

## 7. Record it and commit

Add a line to `CHANGELOG.md` under `[Unreleased]` in the same commit (what was added and why it
matters), list a new shelf in `library/README.md`, and commit. Early and often: the library
addition is one commit, any code or test change it forced is another.

## Changing or removing a holding

- **Remove:** `python scripts/dev/library/library.py set ID status=excluded`, delete its entry from
  `circulation.json`, and re-run `cataloguer.py` (it deletes passage files for holdings that are gone). A weights
  entry for a holding with no passages is only a warning, but it is probably a typo.
- **Change the cleaning:** `set` the new markers, flags or drop patterns, `process`, audit, then
  `promote --id ID` and `cataloguer.py` again. Nothing in `draft/`, `ready/` or `passages/` is edited by hand.
- **Change how often it comes up:** only `circulation.json`.
