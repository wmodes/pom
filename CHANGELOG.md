# Changelog

All notable changes to the Printer of Mysteries project are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Nothing has been
released yet, so everything is under Unreleased; the first named version is planned for the
December 18 session. Update this file in the same commit as the change it describes.

## [Unreleased]

### Added

- **Changelog.** This file, in Keep a Changelog format.
- **Logging, a debug flag, and shared config.** The service's stdout goes to journald, so
  problems are logged there. `src/pom/logs.py` sets up stdout logging once (no timestamp, since
  journald adds one, idempotent, never fails). There is one `debug` flag: set it project-wide in
  `config/pom.toml` (or `POM_DEBUG=1`), or override it in a single module with the
  `DEBUG = None` line at the top of that file (True forces that module's debug output on, False
  keeps it quiet). A test refuses to let a module be committed with the override set.
  `src/pom/config.py` holds the shared settings `strict`, `debug` and `log_level` from the
  config file and the `POM_STRICT` / `POM_DEBUG` / `POM_LOG_LEVEL` environment variables, with
  the environment winning, and never raises. A `Report` can be given a logger, and `Weights`
  and `ChunkStore` log their problems, an INFO summary on load, and DEBUG detail.
- **Local log file.** For working on the Mac, `local_logs = true` (or `POM_LOCAL_LOGS=1`) also
  writes the log to `logs/pom.log` with timestamps. The folder is gitignored, the file rotates at
  1 MB with three backups, and if it cannot be opened logging carries on to stdout with one
  warning. Off by default, since on the Pi journald keeps the log. 77 new tests across the
  logging, debug-flag and config work.
- **Sample count in the config.** `samples_min` and `samples_max` in `config/pom.toml` (or
  `POM_SAMPLES_MIN` / `POM_SAMPLES_MAX`) set how many corpus chunks go into each pull of
  exemplars, 3 to 5 by default. `Config` exposes them as `config.samples`, which the entry
  point passes to `Sampler(samples=...)`; the Sampler never reads Config itself. A bad value
  falls back to the default with a warning and a reversed pair is swapped. 19 new tests.
- **Per-machine config override.** An optional, gitignored `config/pom.local.toml` overrides the
  committed `config/pom.toml` (the environment still wins over both). The committed file keeps
  the quiet defaults the Pi runs with; a developer's machine can keep debug logging and
  `local_logs` on without touching them. 8 new tests.
- **Report** (`src/pom/report.py`), the shared way every module reports problems as data
  instead of raising: WARNING (recovered) and CRITICAL (cannot do its job, still no crash),
  a `strict` mode that raises for tests and dev tools, a cap on how many issues are kept,
  and criticals that are never dropped. 20 tests in `tests/test_report.py`.
- **Design principle:** the Printer never stops because of a typo or a recoverable fault.
  Written into the `pom` package docstring.
- **Sampler module** (`src/pom/corpus/sampler.py`). Draws weighted random chunks so each register
  gets its target share over many draws while any combination of draws stays possible. A
  chunk's chance is its register's share times its source's weight over the register's total,
  computed once at startup and never stored. One pull never repeats a chunk; asking for more
  than exist returns fewer. `draw()` with no argument returns a number of chunks in a configurable
  range (3 to 5 by default); `draw(n)` overrides it. The random generator is injected so draws
  can be seeded. It never stops the oracle: a register with nothing drawable is skipped with a
  warning, and nothing at all to draw is a critical issue with an empty result. It counts draws
  per register (counts only) so an over-represented register can be spotted. `strict=True`
  raises. A draw takes about 3 microseconds on the real corpus and building the odds about
  13 ms. 62 tests in `tests/corpus/test_sampler.py`.
- **ChunkStore module** (`src/pom/corpus/chunk_store.py`). Reads the chunk files into immutable
  `Chunk` objects and answers layout and count questions; its `layout()` is what
  `Weights.check` compares against. It never stops the oracle: a bad line is skipped, an
  unreadable file is skipped, a record that disagrees with its folder or file name is repaired
  (the location wins), a missing id is generated, and only an empty store is critical. Every
  problem is recorded in a `Report` and logged; `strict=True` raises instead. Loading is
  deterministic, and the real corpus (71,474 chunks) loads in about 0.2 seconds. 71 tests in
  `tests/corpus/test_chunk_store.py`.
- **Weights module** (`src/pom/corpus/weights.py`, the first runtime code). Reads
  `corpus/chunks/weights.json`, treats anything unlisted as 1.0, normalizes register shares over
  the registers that have chunks, and checks the file against the corpus layout. It never stops
  the oracle: a bad number counts 1.0, a bad or unknown entry is skipped, a missing or invalid
  file means all defaults, and every problem is recorded in a `Report`. `strict=True` raises
  instead, for tests and dev tools. 70 tests in `tests/corpus/test_weights.py`.
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

- **`draw_weights.py` and `chunk.py` now use the `pom` package** for the weights math and the
  weights.json checks, so the package is the single source of truth. Their output and the chunk
  files are unchanged.
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
