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
  warning. Off by default, since on the Pi journald keeps the log. Tests: 34 in `tests/test_logs.py`,
  61 in `tests/test_config.py`, 17 in `tests/test_debug_overrides.py`.
- **Sample count in the config.** `samples_min` and `samples_max` in `config/pom.toml` (or
  `POM_SAMPLES_MIN` / `POM_SAMPLES_MAX`) set how many corpus chunks go into each pull of
  exemplars, 3 to 5 by default. `Config` exposes them as `config.samples`, which the entry
  point passes to `Sampler(samples=...)`; the Sampler never reads Config itself. A bad value
  falls back to the default with a warning and a reversed pair is swapped.
- **Per-machine config override.** An optional, gitignored `config/pom.local.toml` overrides the
  committed `config/pom.toml` (the environment still wins over both). The committed file keeps
  the quiet defaults the Pi runs with; a developer's machine can keep debug logging and
  `local_logs` on without touching them.
- **Report** (`src/pom/report.py`), the shared way every module reports problems as data
  instead of raising: WARNING (recovered) and CRITICAL (cannot do its job, still no crash),
  a `strict` mode that raises for tests and dev tools, a cap on how many issues are kept,
  and criticals that are never dropped. 21 tests in `tests/test_report.py`.
- **Design principle:** the Printer never stops because of a typo or a recoverable fault.
  Written into the `pom` package docstring.
- **Runtime libraries and data for the Scorer:** `requirements.txt` (`wordfreq`, `nltk`),
  `scripts/setup/setup-nltk-data.sh` (fetches WordNet, stopwords and a part-of-speech tagger into the
  gitignored `data/nltk/`), and the decisions behind the Scorer in the project handoff.
- **A project handoff** (`documentation/project-state.md`): where things stand, the working rules,
  the architecture, the design decisions, how to run things, the next steps and the gotchas, for
  picking the work up after a break.
- **Scorer** (`src/pom/hinge/scorer.py`). Turns fragments into scored candidate hinges. Sentences
  with no content words (all filler, stopwords or numbers) skip the model and get the floor score;
  the rest go to the interest client. If the model gives no score, a crude word-tool stand-in is used,
  capped below the model's usual top, and a circuit breaker leaves a dead server alone for a while
  (3 failures in a row, 30 seconds by default) before trying it again. Each result says whether it came
  from the model, the filter or the fallback. Never raises and never logs what was said. Tests: 49 in
  `tests/hinge/test_scorer.py`, with fakes for the client and the clock.
- **Interest client** (`src/pom/hinge/interest_client.py`), the first half of the Scorer. Asks a
  local llama-server how interesting one sentence is and returns a score read from the digit
  probabilities, one sentence per request with no context, built exactly as the interest-scorer
  experiment built it. A server that is down, slow or answering oddly gives no score and a warning,
  never an exception; sentence text is never logged or put in a report. Has a warm-up call for startup.
  Tests: 56 in `tests/hinge/test_interest_client.py`, using a fake transport.
- **Word tools** (`src/pom/hinge/words.py`), the first piece of the Scorer. Reads a fragment as
  words and says what each one is: base form, part of speech, name, number, stopword, filler (a
  configurable list plus any interjection), and whether it can carry interest. Uses NLTK from
  `data/nltk`; if that data is missing it falls back to plain rules and records a warning rather
  than stopping. Tests: 27 in `tests/hinge/test_words.py`, which use fakes and need no data.
- **Interest-scoring probe** (`experiments/interest-scorer/probe_interest_scorer.py`). Part of a
  formative experiment, not of the Printer: asks a local llama-server how interesting each sentence of overheard speech is. By
  default each sentence is sent alone and the model answers with one digit; the score is read from
  the digit probabilities, so it is continuous, repeatable, and independent of the other lines.
  `--batch` sends a numbered list in one request instead (scores shift with order and cluster on a
  few values), and `--context` shows earlier lines. Both prompts are at the top of the script.
- **Interest-scorer experiment** (`experiments/interest-scorer/`, with a new `experiments/` folder
  and README for formative experiments, named `<level>_<subject>`). A fixed bake-off
  (`bakeoff_interest_scorer.py` and `.sh`) that runs the same test, one sentence per request with no
  context, against Qwen3-4B, Llama-3.2-3B and Gemma-3-4B and writes a report; the first saved run is in
  `results/bakeoff-2026-10-22/`. The README records the rules, what was learned, and that the Scorer will
  be a small local model scoring one sentence at a time. `documentation/project-state.md` updated.
  The sample sentences (now `BENCHMARK` in the probe, 100 of them) grew beyond the first 34; the
  scripts follow whichever name the probe uses.
- **Pi benchmark** (`experiments/pi-benchmark/`, prepared but not yet run on the Pi). Measures how fast
  and how heavy the interest Scorer is on the Raspberry Pi 5, alone and while whisper.cpp transcribes
  back to back, with the cores shared (an even 2+2 split is optional), on a small sample (the probe's sentences) and a large one
  (`sample-large.txt`, 294 corpus sentences). Records per-call time, prompt-cache use, memory,
  temperature and throttling, and reports PASS or CHECK against proposed thresholds. A prepare script
  (`check`, `push`, `fetch`) works from the Mac; the runner and harnesses use only the standard library.
  Tested on the Mac with a stand-in for whisper. First quick run on the Pi saved in
  `results/quick-2026-10-22/`: both models stay under 4 s p95 even with whisper running flat out, with
  Llama about 1.7 times faster than Qwen; findings in the README. The full run is in
  `results/full-2026-10-22/`: it adds whisper `small.en` and a 294-sentence sustained run, and finds
  no throttling, a peak of 72 °C, whisper about 2.3 times slower beside the Scorer (still real-time),
  and Qwen's p95 just over 4 s only in the harshest case.
- **Fragmenter module** (`src/pom/listening/fragmenter.py`). Cuts punctuated transcript text into
  hinge candidates at every level together: sentences (at . ? !), clauses (at ; : dashes and
  ellipses) and phrases (at commas), depth first and in speech order, each stamped with the
  timestamp it was given and its sentence index. Single words are kept; a piece that repeats a
  higher level is not repeated. Abbreviations (Dr., e.g.), initials, decimals and times do not
  cause false cuts. Text with no punctuation is cut into windows of at most `max_words` words
  (40 by default). It never stops the oracle: text that is not a string gives nothing and a bad
  limit falls back to the default with a warning; `strict=True` raises. Tests in
  `tests/listening/test_fragmenter.py`, including a fuzz test on random junk.
- **Fragmenter playground** (`scripts/dev/listening/fragment.py`): type or pipe in some speech
  and see every fragment with its level.
- **A guide to adding material** (`documentation/adding-material.md`): the whole path from
  choosing a register through chunking, weights, verifying and committing, with the commands. The
  corpus README and notes point to it.
- **A new `nonsense` register**, started with *Alice's Adventures in Wonderland* (Project
  Gutenberg #11; 784 utterances, 790 chunks). Added through the normal pipeline as a test of how
  hard it is to grow the corpus: `add`, `process`, one `set` for the markers and `unwrap`, `promote`,
  `chunk.py`, then one entry in `weights.json`. With equal register shares each register now gets
  1/14 of the draws instead of 1/13.
- **Sampler playground** (`scripts/dev/corpus/sample.py`). Prints pulls of random chunks, or with
  `--stats N` draws N pulls quietly and compares each register's share of draws with its
  target. Builds the real Sampler from the corpus, config and weights, and takes `--weights` to
  try a different weights file before editing the real one.
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
  13 ms. 62 tests in `tests/corpus/test_sampler.py`. Chunks are not remembered across pulls; a
  measured note in the module says why no recency decay was added and when to revisit it.
- **ChunkStore module** (`src/pom/corpus/chunk_store.py`). Reads the chunk files into immutable
  `Chunk` objects and answers layout and count questions; its `layout()` is what
  `Weights.check` compares against. It never stops the oracle: a bad line is skipped, an
  unreadable file is skipped, a record that disagrees with its folder or file name is repaired
  (the location wins), a missing id is generated, and only an empty store is critical. Every
  problem is recorded in a `Report` and logged; `strict=True` raises instead. Loading is
  deterministic, and the real corpus (71,474 chunks) loads in about 0.2 seconds. 73 tests in
  `tests/corpus/test_chunk_store.py`.
- **Weights module** (`src/pom/corpus/weights.py`, the first runtime code). Reads
  `corpus/chunks/weights.json`, treats anything unlisted as 1.0, normalizes register shares over
  the registers that have chunks, and checks the file against the corpus layout. It never stops
  the oracle: a bad number counts 1.0, a bad or unknown entry is skipped, a missing or invalid
  file means all defaults, and every problem is recorded in a `Report`. `strict=True` raises
  instead, for tests and dev tools. 75 tests in `tests/corpus/test_weights.py`.
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
- **Pi benchmark whisper logs** were named by phase alone, so the second Scorer model's overwrote the
  first's; the name now includes the model. The report also shows memory used against the pre-start
  baseline, since the server's resident size counts file-backed model pages.
- **Pi benchmark readiness check** always said no whisper was running, because `pgrep` rejected the
  long process-name pattern; it now tests `whisper-cli` and `whisper-server` separately.

### Security

- **Repository made public** on 2026-10-21 at the owner's decision, including the owner-cleared
  Wheelwriter manuals and dad jokes, which `corpus.py publish-check` flags by design.

[Unreleased]: https://github.com/wmodes/pom/commits/main
