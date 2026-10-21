# Adding new material to the corpus

How a new text gets from "I found a book" to "the oracle can draw from it". It was exercised end to
end by adding *Alice's Adventures in Wonderland* as a new register. The path:

```
find it -> add -> process -> set markers/flags -> audit -> promote -> chunk -> weights -> verify -> commit
```

The commands below use `python`, meaning the pom venv's Python (`/Users/wmodes/dev/envs/pom/bin/python`).
Corpus scripts are in `scripts/dev/corpus/`. For what each corpus column and flag means, see
`corpus/README.md`; for how the standardizer works and what went wrong before, see
`documentation/corpus-notes.md`.

## 1. Decide where it belongs

- **Rights first.** Only cleared text is fetched. Public domain is the normal case; mid-century
  material the owner has decided to use is `owner-cleared`. Project Gutenberg works are a strong hint
  of US public domain, not a legal opinion.
- **Pick a register.** A register is *what the oracle might say* (`poetry`, `manual`, `epitaph`,
  `nonsense`...), not where the text came from. See the list in `corpus/README.md`.
  - **Add to an existing register** when the voice fits: the new source joins that register's pool and
    shares its slice of the draws with its neighbors. This is the easy case.
  - **Start a new register** only when the voice is genuinely different. Registers share the draws
    equally by default, so a new one takes a slice from every other register (13 registers gave
    1/13 each; 14 give 1/14).
- **Prefer passages that stand alone** over mid-story narrative, though fragments that need their
  context can be a feature.

## 2. Bring it in

```
python scripts/dev/corpus/gutenberg_catalog.py search 'title regex'     # find a Gutenberg book
python scripts/dev/corpus/corpus.py add --id REGISTER-author-title-year --form REGISTER \
    --title "..." --author "..." --year 1865 --rights public-domain \
    --basis "why we believe it" --url https://www.gutenberg.org/ebooks/N --status approved
python scripts/dev/corpus/corpus.py process ID
```

`process` fetches the text, lists landmark lines (contents, "THE END", ...) with the exact marker to
paste, cleans it, and shows the start, middle and end of the result. The manifest is changed only
through `add` and `set`, never by hand.

## 3. Trim and clean

```
python scripts/dev/corpus/corpus.py set ID "start_marker=..." "end_marker=..." "normalize=unwrap" \
    "notes=Trim: what was cut and why."
python scripts/dev/corpus/corpus.py process ID          # repeat until it reads right
```

Prose gets `unwrap`; verse does not; rhymed runs get `couplets`; plays get `speeches`. Source-specific
junk goes in `drop` patterns. Front and back matter (title page, contents, prefaces, indexes,
bibliographies, dedications, footnotes) must not reach `ready/`.

## 4. Check it, then promote

```
python scripts/dev/corpus/audit.py --dir draft                   # artifact rates; look at your new file's row
python scripts/dev/corpus/audit.py --dir draft ID lower_start    # examples of any check
```

The checks that should read zero are `nonascii`, `lower_start`, `leading_label`, `url_email` and
`markup`; the others are prompts to read, not verdicts. Read some of the draft yourself, plus its
first and last utterances, where leaked front and back matter shows up. When it is right:

```
python scripts/dev/corpus/corpus.py set ID reviewed=yes         # the human sign-off
python scripts/dev/corpus/corpus.py promote --id ID
```

## 5. Chunk it and weigh it

```
python scripts/dev/corpus/chunk.py
```

This rewrites `corpus/chunks/` (only the new source's file changes) and prints a report. Watch for
warnings: *"register(s) not in weights file"* or *"source(s) not in weights file"* mean the new
material counts 1.0 until you list it. That works, but listing it makes the intent visible.

Edit `corpus/chunks/weights.json` by hand. Under the register, add the source with a weight (1.0
unless it should come up more or less often than its neighbors); for a new register, add the whole
entry with a `share` (1.0 matches the others):

```json
"nonsense": {"share": 1.0, "sources": {"nonsense-carroll-alice-1865": 1.0}}
```

Nothing else needs regenerating when a weight changes.

## 6. Verify

```
python scripts/dev/corpus/sample.py --pulls 5                   # see what actually gets drawn
python scripts/dev/corpus/sample.py --stats 3000                # each register's share vs its target
python scripts/dev/corpus/draw_weights.py                       # the odds table
python -m pytest -q                                             # the whole suite, including the real corpus
```

The tests check the committed corpus against `weights.json`, so they fail (by design) if the weights
file lists something that does not exist.

## 7. Record it and commit

Add a line to `CHANGELOG.md` under `[Unreleased]` in the same commit (what was added and why it
matters), list a new register in `corpus/README.md`, and commit. Early and often: the corpus
addition is one commit, any code or test change it forced is another.

## Changing or removing a source

- **Remove:** `python scripts/dev/corpus/corpus.py set ID status=excluded`, delete its entry from
  `weights.json`, and re-run `chunk.py` (it deletes chunk files for sources that are gone). A weights
  entry for a source with no chunks is only a warning, but it is probably a typo.
- **Change the cleaning:** `set` the new markers, flags or drop patterns, `process`, audit, then
  `promote --id ID` and `chunk.py` again. Nothing in `draft/`, `ready/` or `chunks/` is edited by hand.
- **Change how often it comes up:** only `weights.json`.
