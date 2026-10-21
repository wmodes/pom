# Changelog

All notable changes to the Printer of Mysteries project are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Nothing has been
released yet, so everything is under Unreleased; the first named version is planned for the
December 18 session. Update this file in the same commit as the change it describes.

## [Unreleased]

### Added

- **Changelog.** This file, in Keep a Changelog format.
- **ChunkStore module** (`src/pom/corpus/chunk_store.py`). Reads the chunk files into immutable
  `Chunk` objects and answers layout and count questions; its `layout()` is what
  `Weights.check` compares against. Loading is strict (a record must match its folder and file
  name; errors name the file and line) and deterministic. The real corpus, 71,474 chunks, loads
  in about 0.2 seconds. 40 tests in `tests/corpus/test_chunk_store.py`.
- **Weights module** (`src/pom/corpus/weights.py`, the first runtime code). Reads
  `corpus/chunks/weights.json`, treats anything unlisted as 1.0, normalizes register shares over
  the registers that have chunks, and checks the file against the corpus layout. Drift is a
  warning; a listed register or source with no chunks is an error. 49 tests in
  `tests/corpus/test_weights.py`.
- **Project setup for tests:** `pyproject.toml` (pytest settings), `requirements-dev.txt`, and the
  `pom` virtual environment on Python 3.11 to match the Pi's Bookworm.
- **Output validation stub** (`scripts/dev/corpus/validate_output.py`). Reports nothing yet; its
  header records why a verbatim-overlap check was set aside and what could replace it.
- **Chunks** (`corpus/chunks/<register>/<id>.jsonl`, 71,474 of them). `ready/` utterances sized
  at 15 to 150 words, made by `scripts/dev/corpus/chunk.py`. Long utterances are split at
  sentence or line breaks; exact duplicates are dropped. A chunk holds only facts about itself.
- **Draw weights** in one hand-edited file, `corpus/chunks/weights.json`: a tree of registers,
  each with a share and its sources' weights. `scripts/dev/corpus/draw_weights.py` computes the
  odds at load time (never stored) and warns about unlisted registers and sources.
- **Corpus audit** (`scripts/dev/corpus/audit.py`): artifact rates per file of `ready/`, and
  examples of any check.
- **Corpus text:** 80 sources promoted to `ready/` (about 69,000 utterances in 13 registers),
  provisionally reviewed. New standardizer flags `unbracket`, `unbrace`, `titles`, `linenos` and
  `spellcheck`.
- **Corpus pipeline** (`scripts/dev/corpus/`): `add`, `set`, `process`, `inspect`, `clean`,
  `review`, `promote`, `mix` and `publish-check`, with trim markers, per-source cleaning flags and
  a Project Gutenberg catalog search. Corpus rules and workflow are in `corpus/README.md`.
- **Pi setup scripts** for whisper.cpp and llama.cpp, and Pi setup notes
  (`documentation/pi-setup-notes.md`).

### Changed

- **Source weights moved** from the `weight` column in `corpus/sources.csv` into
  `corpus/chunks/weights.json`. `corpus.py set ID weight=...` now points at the new file.
- **Standardizer rules tightened** after a full sweep of `ready/`: leftover non-ASCII, lowercase
  fragments and wider label patterns are dropped; many sources got tighter trim markers and drop
  patterns.
- **Scripts reorganized by purpose** (`scripts/setup/`, `scripts/dev/`).

### Fixed

- **Leaks found in `ready/`:** Greek and Hebrew text, jest and maxim numbers, editors' bracketed
  notes, Douay footnotes, a printer's colophon, indexes, bibliographies, dedications and OCR noise
  in the Wheelwriter manuals.
- **Wrong attribution** in the personality-types note.

### Security

- **Repository made public** on 2026-10-21 at the owner's decision, including the owner-cleared
  Wheelwriter manuals and dad jokes, which `corpus.py publish-check` flags by design.

[Unreleased]: https://github.com/wmodes/pom/commits/main
