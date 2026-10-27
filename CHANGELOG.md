# Changelog

All notable changes to the Printer of Mysteries project are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Nothing has been
released yet, so everything is under Unreleased. Update this file in the same commit as the change it describes.

## [Unreleased]

### Added

- **`[trigger] cooldown_seconds`** (default 60; 0 turns it off; 20 new tests, mutation-checked: 30 of 31 caught, the
  survivor an equivalent mutant). After a trigger, a score above the bar is not marked until `cooldown_seconds` have
  passed since that trigger: it is a new `ScoreEffect.COOLING_DOWN`, still joins the window, and does not restart the
  cooldown. `ConvoInterestTracker` takes it as a fourth setting; the probe shows `[window 83 scores, bar 6.17; cooling
  down]`. Why: oct12 had 18 triggers in five stretches, one of them six in eight seconds (a run of one-line swearing
  jokes, each scoring 6.8 to 7.3 against a bar that five minutes of scores barely moves). Replayed on the four recorded
  meetings, 60 s takes all four from 34 triggers (6.6 an hour) to 21 (4.1 an hour, one every 14.6 minutes, shortest gap
  62 s) and oct12 from 12.2 to 5.4 an hour. With the cooldown, `fence_multiple` 0.9 gives 5.7 an hour (one every 10.6
  minutes) and 0.8 gives 6.8; not changed here. A trigger inside the Printer's own busy time is a separate matter for
  the Conductor, which does not exist yet.
- **`[trigger] fence_multiple`** (default 1.5; 5 new tests). For a bar set by Tukey's fence instead of the room's
  average: how many interquartile ranges above the recent scores' upper quartile (Q3) a score must be to trigger, so
  the bar is Q3 + `fence_multiple` x IQR. 0 puts it at Q3 (about the top quarter of scores); 1.5 is the textbook outlier
  line. It takes over the job `trigger_fraction` does for the average-based bar, under a name that says what it is.
  Read and validated by `Config` (0 or more, else the default with a warning); the ConvoInterestTracker does not use it
  yet, and `trigger_fraction` and `interest_half_life` are untouched.
- **`[trigger] window_seconds`** (default 300, the last five minutes; 7 new tests). How many seconds of recent scores
  the room's level is judged from, as a length of time, not a count of scores, so a busy room and a quiet one both get
  the same stretch. Read by `Config` and validated like the other trigger values (a number above 0, else the default
  with a warning). The ConvoInterestTracker does not use it yet; it is there for the offline time-window replay.
- **Recording-time stamps in the hearing-debug replay** (`probe_hearing_pipeline.py`). With `--wav`, each "heard" line
  is stamped with where in the recording the utterance ended (`+hh:mm:ss`, from the rolling cutter's own segment times)
  instead of the wall clock, which means nothing when a recording is replayed faster than real time. Live listening
  is unchanged. Needed to test a trigger that judges the room over minutes, not over a count of sentences.
- **Pi oracle benchmark** (`experiments/pi-oracle-benchmark/`, 49 tests in `tests/pi_oracle_benchmark/`,
  mutation-checked). Times the 9B oracle model on the Pi with whisper (`base.en`) and the 4B scorer loaded but idle,
  against a baseline of the 9B alone, each from an emptied page cache: startup time (cold and restart), total seconds,
  seconds to the first token, writing speed, and memory, temperature and throttling once a second. It runs the real
  `PromptBuilder` and `OracleModelClient` on 12 of the bake-off's own hinge-and-seed cells; only the client's `post` is
  swapped for a streaming one. Counts and timings are kept, never the portents. Run on the Mac as a reference
  (median 0.8 to 0.9 s a portent, about 68 tokens a second, about 10 GB in use by the three models, no difference
  between the two phases; the prompt and corpus fingerprints match the bake-off). A first Mac run, disturbed by use of
  the playground on the same GPU, was discarded. Not yet run on the Pi.
- **Oracle model bake-off** (`experiments/oracle-bakeoff/`, 41 tests in `tests/oracle_bakeoff/`, mutation-checked;
  `documentation/bakeoff-rubric.md` and `documentation/model-bakeoff.md`). Seven 8 to 9B models run through the real
  PromptBuilder and OracleModelClient under Wes's frozen prompt (temperature 0.3, one exemplar): 16 benchmark hinges
  times 10 seeded draws, 160 portents per model, the same hinge-and-exemplar pairs for every model. Each run records the
  prompt hash, the corpus fingerprint, the commit and the llama-server version, so two runs can be checked to have the
  same conditions. The portents were rated blind against seven yes/no marks (follows the hinge, follows the style,
  doesn't repeat the hinge, didn't muddle its details, no extra text, length close, particularly creative); three are
  checked by a program (`bakeoff_checks.py`). Mean score of 7: Qwen3.5-9B 6.08, Gemma 2 9B 5.84, Hermes 3 5.26,
  Ministral 8B 5.21, Llama 3.1 8B Instruct 5.19, Stheno v3.4 4.73, Qwen3-8B 4.45. The write-up compares the models on
  each question and has an awards section. One rater (Claude), not yet spot-checked by Wes.
- **Oracle playground** (`experiments/oracle-playground/probe_oracle.py`). Step four of the voice side. Type a hinge,
  get a portent, using the real `PromptBuilder`, `OracleModelClient`, ExemplarSelector and the config's `oracle.prompt`
  (read again before every hinge). It starts a llama-server for `--model` (a .gguf file or a Hugging Face name) on the
  port in `oracle.url`, or uses a running one with `--server`. The random generator is reseeded from a sha256 of the
  hinge, so the same hinge gets the same exemplars whichever model runs (`--seed N` to vary). Tried on the Mac with
  Qwen3-8B and the placeholder prompt: the wiring works and the same hinge gave the same exemplars; the portents are
  meaningless until the prompt is written.
- **OracleModelClient** (`src/pom/voice/oracle_model_client.py`, 53 tests in `tests/voice/`, mutation-checked). Step
  three of the voice side. `portent(prompt)` sends the finished oracle prompt to the oracle model's llama-server as one
  user message, word for word, with `max_tokens` and `temperature` from the config (not temperature 0: the model
  chooses its words), and returns what it wrote, trimmed, or None with a warning in `report` if the server is down,
  slow, or answers oddly or with nothing. Never raises outside strict mode. A portent cut off at `max_tokens` is
  returned and flagged in `last_cut_off`. Neither the prompt (it holds the hinge) nor the portent is logged. The
  transport is injected, so the tests need no server; the four lines of HTTP are the scorer client's, repeated here
  so the two packages stay apart.
- **`[oracle]` section in the config** (`OracleSettings`, `Config.oracle`; 8 tests, mutation-checked). `url` (a second
  llama-server, port 8099), `timeout` (120 s), `max_tokens` (300), `temperature` (0.8) and `prompt`, the oracle prompt
  with `{exemplars}` and `{hinge}`. `config/pom.toml` has a PLACEHOLDER prompt for Wes to replace. The four numbers are
  my starting values (llama-server's own default temperature is 0.8; the rest are guesses sized for a 15 to 150 word
  portent on the Pi).
- **PromptBuilder** (`src/pom/prompt/prompt_builder.py`, 32 tests in `tests/prompt/`, mutation-checked). Step one of
  the voice side. It fills the oracle prompt (Wes's text, with `{exemplars}` and `{hinge}` marking the places) with
  exemplars drawn by the ExemplarSelector and the hinge (the fragment that triggered), and returns the text with the
  chunks it used. Nothing else is added. Both places are filled in one pass, so braces in a chunk or a hinge stay
  plain text; the exemplars are only drawn if the prompt has a place for them; the hinge is never logged. A bad or
  missing prompt, an empty hinge, or nothing to draw is recorded and never raises (`strict=True` raises). To repeat
  a draw, give the selector a seeded `random.Random` and reseed it before each build (tested, with a seed taken
  from the hinge). The oracle model client is next.
- **`experiments/hearing-debug/probe_whisper_segments.py`**: an offline test of letting whisper's own voice detection
  find the cut points instead of the energy chunker, which in a noisy room cuts every utterance at the 20 s cap
  (measured: 34 to 55% of frames counted as speech in the stretches of an attic recording where nobody spoke). A rolling
  buffer goes to `whisper-cli --vad`; finished segments are kept, unfinished ones joined to the next, the buffer starts
  a little before the last kept segment's end, and segments are split into sentences with the `SegmentFragmenter`. On the
  attic recording it found every sentence of the whole-file answer with none broken; quiet speech still needs level
  normalization. Not wired into the live pipeline.
- **ConvoInterestTracker, step two: avg_convo_interest and the trigger.** `record(score)` takes in one score and
  says what happened to it (`ScoreEffect`: ignored, counted or triggered). Scores at or above the floor feed
  `avg_convo_interest`, a decaying moving average (the first becomes the average, each later one moves it
  `interest_weight` of the way toward itself). A score triggers when it is at least `trigger_fraction` above the
  average as it stood before that score was added, once `min_scores` have counted. Four settings in `[trigger]`:
  `floor_fraction` 0.05, `trigger_fraction` 0.10, `interest_weight` 0.1, `min_scores` 5. 112 tracker tests, mutation-
  checked. Not wired into anything yet; the hearing-debug view is next. Filtered fragments still score
  `fragment_filter_score` = 1.0, above the floor, until it is set to 0.
- **ConvoInterestTracker, step one: the floor** (`src/pom/scoring/convo_interest_tracker.py`, 46 tests in
  `tests/scoring/test_convo_interest_tracker.py`). `avg_convo_floor` is the score below which scores are ignored:
  a fixed fraction of the score range, `floor_fraction` (0.05, so 0.45 on the 0 to 9 scale), set in a new `[trigger]`
  section of the config (`Config.trigger`, `TriggerSettings`). `is_above_floor(score)` counts a score at the floor
  and ignores an unscored fragment or anything that is not a number. A bad setting falls back to the default with a
  warning. avg_convo_interest, which averages the scores above the floor, comes next. Mutation-checked. Note: filtered
  fragments still score `fragment_filter_score` = 1.0, above the floor, until Wes decides to set it to 0.
- **`documentation/lexicon.md`**: the project's vocabulary (segment, chunk, score, hinge, corpus, weights,
  portent, experiments, and terms awaiting confirmation), plus the code names that do not match it yet.
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
- **The Pi is deployed with git.** It has a clone of the repo at `~/pom` (about 57 MB, 16 MB of history), so
  the layout matches the Mac and code arrives with `git pull`; models and recordings live under the gitignored
  `data/`. The per-experiment copy scripts were replaced: `probe_hearing_push.sh` became `probe_hearing_tune.sh`
  (prompt edits travel by git, see the next entry), and `benchmark_pi_prepare.sh push` became `models`.
  Documented in `documentation/pi-setup-notes.md`.
- **Everything on the Pi now lives inside `~/pom`.** whisper.cpp and llama.cpp are built under
  `data/tools/` (the setup scripts default there and build with a relative `$ORIGIN` library path so the
  folders can be moved without rebuilding), logs under `data/logs/`, and the early whisper sample under
  `data/recordings/samples/`. Scripts and docs that assumed `~/llama.cpp` and `~/whisper.cpp` were updated.
- **Hearing-debug experiment** (`experiments/hearing-debug/`). A live prototype of the front half of the
  Printer for watching how it interprets what it hears and refining the prompt in a closed loop. On the Pi
  (or the Mac, against a recording) it cuts the microphone at pauses, normalizes the level, transcribes with
  whisper, splits sentences, scores them with the real Scorer from `config/pom.toml` and prints the model's
  own written account of each score using `prompt_output_debug`. The config is re-read before every
  utterance, a push script copies it to the Pi each time it is saved, and typing `r` re-scores the last
  sentences with the new prompt and shows old and new. Nothing heard is kept: audio goes through memory-backed
  temporary files that are deleted at once, and the output must not be redirected to a file.
- **Startup** (`src/pom/startup.py`), a stub for everything done once when the Printer starts. The
  runner is built: it runs steps in order, never raises, records a failed optional step as a warning and a
  failed critical one as critical (with `ready` false), and keeps going either way. The ten duties (config
  report, data check, mic gain, audio device, whisper, the Scorer's server and warm-up, the voice model,
  the typewriter's serial port, a readiness line) are listed in `planned_steps()` as placeholders to be
  written one at a time. Tests: 24 in `tests/test_startup.py`.
- **Decisions recorded** (`documentation/project-state.md`): Qwen3-4B is the starting Scorer model and
  7.0 the starting trigger threshold. First look at the real USB microphone on the Pi: it records quietly
  (mean -38 dB) with capture gain to spare, and whisper wraps song lyrics in ♪ marks; the
  Transcriber will strip the symbol and keep the words, since the Printer reacts to what it hears.
  Mic gain on the Pi settled at 24 of 30 after a loud-room test (a yell reached -2.7 dB at 26).
  Whisper's bracketed sound captions are kept too; a variable trigger threshold that follows how
  interesting the room is, starting from 7.0, is the plan.
- **Scorer settings in the config.** `config/pom.toml` gains a `[scorer]` section: the language
  model's address and timeout, the prompt that defines "interesting" and the score scale (kept here as
  the single copy, so it is the main thing to adjust), the floor score, how many content words a
  sentence needs to be sent to the model, and the circuit-breaker numbers. `Config` reads it into
  `config.scorer` (a `ScorerSettings`), validates each value with a warning and a default, merges a
  `[scorer]` key from `pom.local.toml` one key at a time, and accepts `POM_SCORER_URL`. Unknown
  sections and keys are warned about. 30 new tests in `tests/test_config.py`.
- **Scorer** (`src/pom/hinge/scorer.py`). Turns fragments into scored candidate hinges. Sentences
  with no content words (all filler, stopwords or numbers) skip the model and get the floor score;
  the rest go to the interest client. If the model gives no score, a crude word-tool stand-in is used,
  capped below the model's usual top, and a circuit breaker leaves a dead server alone for a while
  (3 failures in a row, 30 seconds by default) before trying it again. Each result says whether it came
  from the model, the filter or the fallback. Never raises and never logs what was said. Tests: 49 in
  `tests/hinge/test_scorer.py`, with fakes for the client and the clock. `Scorer.is_filtered(text)` says
  whether a sentence would skip the model, using the same rule, without scoring it (5 more tests).
- **Interest client** (`src/pom/hinge/interest_client.py`), the first half of the Scorer. Asks a
  local llama-server how interesting one sentence is and returns a score read from the digit
  probabilities, one sentence per request with no context, built exactly as the interest-scorer
  experiment built it. A server that is down, slow or answering oddly gives no score and a warning,
  never an exception; sentence text is never logged or put in a report. Has a warm-up call for startup.
  Tests: 56 in `tests/hinge/test_interest_client.py`, using a fake transport.
  It also has `interpret(sentence)` for the debug view: it sends the debug prompt and returns the
  model's written reply (the score and the content that earned it), with its own longer timeout, and
  `parse_written_score` reads the leading number from such a reply. 32 more tests.
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

- **`fence_multiple` is 0.9** (was 1.0, in the tracker, `Config` and `pom.toml`), chosen with the 60 s cooldown to
  match the goal of about six portents an hour. Replayed on the four recorded meetings with the shipped settings:
  29 triggers in 5.1 hours, 5.7 an hour, one every 10.6 minutes (3.4, 5.1, 7.4 and 7.1 an hour by meeting; shortest gap
  62 s, median 3.3 minutes, longest 29 minutes). At a fifth of the talk the same settings give about 4 an hour. The
  tracker's tests pin their own fence multiple, so they no longer depend on the shipped default.
- **The trigger is Tukey's fence over the room's recent scores, and the floor and the running average are gone.**
  `ConvoInterestTracker` keeps the scores of the last `window_seconds` (300), or the last `min_scores` (20) if the room
  is so quiet that fewer were heard in that time, and a score triggers when it is above Q3 + `fence_multiple` (1.0) x
  IQR of that window as it stood before the score was said. Every score joins the window, the lowest included, so no
  fixed line throws any away. `record(score, now)` now takes the time the score was heard (a clock reading live, the
  place in the recording in a replay); a missing score or time is still IGNORED. Why: replayed on four recorded Zoom
  meetings (4,278 sentences, about 835 an hour), the old rule (25% above the running average) triggered on 34% of
  sentences, one every 13 seconds, and `trigger_fraction` barely moved it (0.35 gave 30%, 0.50 gave 23%): an average sits
  in the middle of the scores, so a good share of them is always above a bar set just over it, and the many scores near 1
  from short fragments pulled the average and the bar down further. The fence at 1.0 gave about 7 an hour in that busy
  room (one every nine minutes) and about 5 an hour at a fifth of the talk. The scores are bunched into lumps with no
  long tail (a spike at 1, and humps near 3 and 5), so a textbook outlier line (1.5) fired almost never.
  **Removed settings:** `trigger.floor_ignore_fraction`, `trigger.trigger_fraction` and `trigger.interest_half_life`
  (a `pom.toml` or `pom.local.toml` still holding them gets an unknown-key warning), and with them the tracker's
  `avg_convo_floor`, `avg_convo_interest`, `interest_weight`, `is_above_floor` and `score_range`. `trigger.min_scores`
  now means the window's minimum and defaults to 20 (at least 2); `trigger.fence_multiple` defaults to 1.0, down from
  the 1.5 added a day earlier. The probe is updated in the next change.
- **The hearing-debug probe runs on the new tracker.** Each sentence's note now reads `[window 69 scores, bar 5.12]`
  (how many recent scores it was judged against, and the score it had to be above), `[warming up 3/20]` before the
  window holds enough, or `[no score]`; the startup banner describes the fence. The tracker's time is the place in the
  audio stream where the utterance ended (the rolling cutter's segment end), not the wall clock, so a recording replayed
  fast with `--wav --speed 0` gives the triggers a live run would. The lexicon (window, fence, trigger), the
  hearing-debug README, `project-state.md` and two docstrings now describe the window and the fence. Mutation-checked:
  23 of 24 tracker mutants are caught; the one that survives is equivalent (with exactly `min_scores` scores inside the
  window, the fallback picks the same scores). A test for a clock stepping backwards was strengthened after two
  mutants showed it proved nothing.
- **The oracle playground prints the exemplars two to a row** above the portent, each headed by its id and wrapped to
  its column (`exemplar_columns`), so the exemplars the model was given can be checked at a glance.
- **The oracle's settings are all in `[oracle]`, and the committed oracle prompt is Wes's.** `exemplars_min` and
  `exemplars_max` moved from the top of `config/pom.toml` into the section (`oracle.exemplars_min` and
  `oracle.exemplars_max`; the old top-level keys are now reported as unknown, and the `POM_EXEMPLARS_MIN`/`MAX`
  environment overrides are unchanged). The file is ordered by the work (global, scorer, trigger, oracle). The prompt is
  Wes's "passage" prompt (the style example, then the theme) in place of the placeholder, with one exemplar, temperature
  0.3 and 300 tokens: the settings the oracle bake-off froze. Two new config tests.
- **PromptBuilder labels and fences what it fills in** (Wes's format). Each exemplar goes in as `**Style Example N**`
  followed by its text in a code fence, and the hinge as `**Theme**` followed by its text in a fence, so the model can
  tell the examples and the theme from the instructions around them. The fence is added in code and never written in
  the prompt, because the prompt itself is generated over. A chunk or hinge that holds a fence of its own is not
  escaped (the docstring says so). Both places are still filled in one pass. Tests updated, and new ones for the labels
  and the numbering.
- **Hearing-debug lets whisper cut the audio; the energy cutter is behind `--energy-cutter`** (Wes's call). The new
  `RollingCutter` (`rolling_cutter.py`) hands whisper-server the audio since the last kept segment every 5 s with its
  own voice detection on, keeps the segments whisper finished (ending 2 s or more before the buffer's end), joins a
  segment that does not end like a sentence to the next. `--rewind` (off, 0 s) would start each round that long before
  the last kept end, to save a sentence's first syllables from a late segment end, but live it re-heard the last word
  ("yourself.", "made it." came out twice). `--step`, `--hold` and `--rewind` set these; `--margin-db` now applies to the energy cutter only. The playground
  `probe_whisper_segments.py` uses the same class and the server, not `whisper-cli`. Word recall against whisper
  reading the whole file, rolling (rewind off) against the energy cutter: attic 93% / 87%, loud music 90% / 90%, quiet
  classroom 52% / 43% (that recording loses about half its words either way). Prompting whisper with the recent text was
  tried and left out: mixed results.
- **Hearing-debug talks to a whisper-server instead of running `whisper-cli` for every utterance** (Wes's call). The
  Transcriber and the new playground (`probe_whisper_segments.py`) share `whisper_server_client.py`: audio goes to the
  server from memory, so no audio file is written at all (the stale-slice cleanup from the previous change is no longer
  needed and is gone), whisper's voice detection is on for each request and off for the lyrics-and-captions second
  pass, and `probe_hearing_run.sh` starts and stops the server beside llama-server (`--model`, `--whisper-threads` and
  `--vad-threshold` now go to it). Measured on the Pi with matched settings the server saves about 15 to 30% per call.
  Found on the way: `verbose_json` with voice detection on crashes the 1.9.5 server when the audio has no speech, so
  timestamps are asked for as SRT. The pipeline also reports a dead server once and tells you how to start one.
- **`trigger_fraction` is now 0.25** (Wes, after a second live session): across the two pasted sessions 0.25 marks 11 of
  43 and 12 of 23 counted sentences (0.10 marked 19 of 43 and 13 of 23). The built-in default moves with it.
- **`trigger_fraction` is now 0.20** (Wes, after watching a live session): in the pasted session, 0.10 marked 19 of 43
  counted sentences as would-trigger and 0.20 marks 13. The built-in default moves with it, so a bad value falls back
  to 0.20.
- **Hearing-debug uses the ConvoInterestTracker instead of a fixed threshold.** Every score goes to the real
  tracker; a sentence that stands out from the room's average interest (by `trigger_fraction`, after `min_scores`)
  is marked `<-- would trigger`, and each line ends with a note: the average and the bar, a warm-up count, or
  `below the floor, ignored`. The `--threshold` option is gone; the startup banner prints the trigger settings.
  Reloading the config restarts the average, and `r` re-runs do not count a sentence twice.
  Every line carries the note, and it shows the average the sentence was judged against and the average after it
  (`interest 2.45 -> 2.73, bar 2.69`); ignored and unscored lines show the unchanged average.
- **Trigger settings renamed and re-based (Wes).** `floor_fraction` is `floor_ignore_fraction` (clearer: the bottom
  fraction of the score range that is ignored). `interest_weight` is replaced by `interest_half_life`, in scores:
  how many more scores it takes for an old score's pull on avg_convo_interest to halve (10, which works out to
  each score moving the average about 6.7% of the way; `weight = 1 - 0.5 ** (1 / half_life)`, shown as
  `ConvoInterestTracker.interest_weight`). `min_scores` now defaults to 10, the same as the half-life, so the
  average has its first full memory before anything can trigger. An old key in a local file warns as unknown.
- **Scorer prompt: the score is one digit, 0 to 9** (Wes). The scale now runs 0 to 9 in five two-digit buckets and
  the model is asked to "Reply with a one digit number and nothing else" (was two decimals from 1.00 to 9.99); the
  debug prompt asks for a single digit too. The old scale and output lines are kept as comments. The score itself
  is unchanged in method: the probability-weighted average of the digits the model might say.
- **The playground script prints scores to three significant figures** (`4.95`, `0.990`), so scores under 1 keep
  their detail.
- **The probability-weighted score stays** (a whole-digit score was tried for an afternoon and reverted at Wes's
  request). Its explanation is now written down in plain words in the lexicon and in `scorer_model_client.py`: the
  score averages the digits the model might say, weighted by how likely it thinks each is.
- **The interest-scorer playground script now uses the Printer's own prompt and reader.** `probe_interest_scorer.py`
  reads its prompt from `config/pom.toml` each run (`--config` picks another file, `--prompt-file` overrides it),
  prints which prompt it used, and reads the digits and wraps each line with the code in `pom.scoring`, so it can
  no longer drift from the Printer. The embedded copy of the old 1.00 to 9.99 prompt is gone (the bake-off's copy is
  saved in `results/bakeoff-2026-10-22/PROMPT.txt`); batch mode keeps its own prompt. Added `suite.txt`, a 20-line
  test suite (Wes's), to run with `--file`.
- **The score is read from one digit, 0 to 9.** The scorer model is asked for a single digit (your prompt), and
  the score is the probability-weighted value of the digits 0 to 9 in that first token, so it runs 0.0 to 9.0.
  The digit 0 now counts (it was ignored, which skewed scores up when the model said 0). The debug view's written
  score is read as a single digit, and a decimal or two-digit answer is no longer a score. Mutation-checked.
- **Config keys follow the lexicon.** `samples_min` and `samples_max` are `exemplars_min` and `exemplars_max`
  (environment: `POM_EXEMPLARS_MIN` and `POM_EXEMPLARS_MAX`, was `POM_SAMPLES_MIN`/`MAX`), `Config.samples` is
  `Config.exemplar_range`, and `[scorer] floor` is `fragment_filter_score` (`ScorerSettings.fragment_filter_score`).
  An old key in a local file now warns as unknown and is ignored. No behavior changed.
- **The `hinge/` package is now `scoring/`** (`src/pom/scoring/`, tests in `tests/scoring/`): it scores
  fragments, and the hinge is only the fragment the trigger will choose. Import paths and module log names
  changed from `pom.hinge...` to `pom.scoring...`. No behavior changed.
- **`Fragmenter` is now `SegmentFragmenter`** (`src/pom/listening/segment_fragmenter.py`; `FragmenterError` is
  `SegmentFragmenterError`), named for what it does: it fragments segments. `Kind` is `FragmentLevel` and
  `Fragment.kind` is `Fragment.level` (sentence, clause or phrase); `Fragment.at` and the `at` argument of
  `split()` and `score_text()` are `heard_at`. Tests moved to `tests/listening/test_segment_fragmenter.py`. No
  behavior changed.
- **Scorer vocabulary now follows the lexicon.** `Source` is `ScoredBy` (values `MODEL`, `FRAGMENT_FILTER`,
  `UNSCORED`; the strings are `model`, `fragment_filter`, `unscored`), `ScoredFragment.source` is `.scored_by`,
  `is_filtered()` is `is_fragment_filtered()`, and the Scorer's `floor` argument is `fragment_filter_score`
  (`DEFAULT_FRAGMENT_FILTER_SCORE`). The `scorer.floor` config key keeps its name until the config keys are
  renamed. The counts and the hearing-debug view say `fragment_filter` where they said `filter`.
- **`InterestClient` is now `ScorerModelClient`** (`src/pom/hinge/scorer_model_client.py`; `InterestClientError` is
  `ScorerModelClientError`). Its debug request is now `explain()` (was `interpret()`; `can_explain`,
  `Explanation`, `explain_prompt`, `explain_timeout`, `EXPLAIN_MAX_TOKENS`), `expected_digit()` is
  `score_from_probabilities()`, its argument is `fragment_text`, and the Scorer's `client` argument is
  `scorer_model_client`. The text sent to the model is unchanged. Tests moved to
  `tests/hinge/test_scorer_model_client.py`. The hearing-debug view now says "explanations".
- **`Sampler` is now `ExemplarSelector`** (`src/pom/corpus/exemplar_selector.py`; `SamplerError` is
  `ExemplarSelectorError`, `sample_range` is `exemplar_range`, its `samples` argument is `exemplar_range`, and
  its default is `DEFAULT_EXEMPLAR_RANGE`). The warning text says "exemplar_range" where it said "samples".
  `Config.samples` and the `samples_min`/`samples_max` keys are unchanged until the config keys are renamed.
  Tests moved to `tests/corpus/test_exemplar_selector.py`.
- **The Scorer no longer guesses a score when the scorer model does not answer.** The word-counting fallback
  (arbitrary, hard-coded numbers, never approved) is moved to `src/pom/hinge/fallback_scorer.py`, unused and
  documented with its weaknesses, so it can be deleted or revived; it has its own tests in
  `tests/hinge/test_fallback_scorer.py`. A fragment the model cannot score now comes back with `score=None`
  and source `UNSCORED` (replacing `FALLBACK`), and `ranked()` puts it last. The hearing-debug view shows it
  as `-`. The circuit breaker is unchanged. Mutation-checked.
- **`Weights` is now `DrawWeights`** (`src/pom/corpus/draw_weights.py`; `WeightsError` is `DrawWeightsError`), and
  the Sampler's `weights` argument is `draw_weights`. The module's log name is now `pom.corpus.draw_weights`. The
  data file stays `corpus/chunks/weights.json`. Tests moved to `tests/corpus/test_draw_weights.py`. No behavior
  changed.
- **`logs.py` is now `log_setup.py`** (`src/pom/log_setup.py`), named for what it does: it sets up logging once
  and hands each module its logger. Tests moved to `tests/test_log_setup.py`. No behavior changed.
- **`WordTools` is now `WordTagger`** (`src/pom/hinge/word_tagger.py`; `analyze()` is `tag()`, `Word.content` is
  `Word.is_content`, `WordToolsError` is `WordTaggerError`), and the Scorer's `words` argument is `word_tagger`.
  Tests moved to `tests/hinge/test_word_tagger.py`. No behavior changed.
- **`Report` is now `FaultRecorder`** (`src/pom/fault_recorder.py`; `Issue` is `Fault`, `strict_report` is
  `strict_fault_recorder`, and the `issues` property is `faults`), named for what it does under the project
  lexicon. Modules keep calling their instance `report`. Tests moved to `tests/test_fault_recorder.py`.
  No behavior changed, except that the cap message now says "faults" where it said "issues".
- **`config/pom.toml` is divided into labeled sections** (GLOBAL, ORACLE, SCORER), one banner per part of
  the Printer the settings affect. Comments only; no setting changed.
- **Scorer prompt: dropped "Most sentences are ordinary and score below 5."** (Wes). That line was an absolute
  expectation about the scale; the scoring prompt now only describes the scale.
- **Scorer prompt split into three parts** in `config/pom.toml`: `prompt_scoring` (what counts as
  interesting and what the numbers mean), `prompt_output` (reply with a bare number) and
  `prompt_output_debug` (reply with the number and the content that earned it). `ScorerSettings` exposes
  `prompt` (scoring plus output) and `prompt_debug` (scoring plus debug output), each joined with a blank
  line. The old single `prompt` key is now an unknown key. A scoring part with no output part is warned
  about. The scoring prompt also gained lines for gossip, mysteries, stakes and secrets. A stray extra
  quote after the debug prompt was removed.
- **Scorer prompt: "a big human feeling" is now "a human feeling"** in `config/pom.toml`. "Big" nudged
  heated lines up a little (on the 100 benchmark sentences "What the fuck?" went 5.44 to 4.76 without
  it, "I love you" 4.81 to 4.31) without changing the ranking much (agreement 0.996). The experiment's
  own copy of the prompt in `experiments/interest-scorer/` is left as it was run.
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

- **Hearing-debug no longer leaves audio behind when it is stopped mid-transcription.** Two leftover `tmp*.wav` slices
  of room audio were found in the Pi's `/dev/shm` (removed). The Transcriber now keeps its slices in a folder of its own
  (`hearing-debug-audio`), clears anything a killed run left at startup, and empties it on exit.
- **Hearing-debug passes each sentence to the model as soon as whisper produces it.** The wait for a quiet room
  never ended in a room that is never quiet, so results sat at "waiting" forever. Removed the machinery built around
  it (`--explain-quiet`, the `e` command, the audio quiet-time tracking) and two comparison flags nobody had asked
  for (`--also-score`, `--score-ambient`). The model takes one sentence at a time, in order; `--no-explain` stays
  as the light option.
- **Leaks found in `ready/`:** Greek and Hebrew text, jest and maxim numbers, editors' bracketed
  notes, Douay footnotes, a printer's colophon, indexes, bibliographies, dedications and OCR noise
  in the Wheelwriter manuals.
- **Wrong attribution** in the personality-types note.
- **Hearing-debug: a long explanation could make a score time out.** On the Pi, a model busy writing an
  explanation left a one-token score waiting past its 15 s limit, so it fell back to the word tools. Explanations
  now wait until nothing is being heard or scored, and the tool waits up to 30 s for a score (`--score-timeout`).
- **Hearing-debug: one request per sentence, with the score and the reasons from the same output.** In the
  default debug mode each sentence is run once through `prompt_output_debug` in a quiet moment, and the score (read
  from the first token's probabilities) and the reasons print together; the separate one-token score that was shown
  first is gone (`--also-score` brings it back for comparison, `--no-explain` is fast mode with one-token scores and
  no reasons). `--explain-above` and `--explain-lyrics` were removed, since every sentence is now run. The waiting
  queue holds 40, so a stretch of continuous talk no longer drops sentences. `e` runs the waiting sentences now and
  `r` runs the recent ones again showing the old score.
- **Hearing-debug: whisper's ambient captions are never scored.** Output is sorted into words (speech and song
  lyrics, scored), ambient captions like `(dog barks)` or `(dramatic music)` (shown on their own line, not scored or
  explained, and kept as a short list of recent ambient sounds for the voice's prompt later), and "nothing here"
  markers (dropped). `--score-ambient` restores scoring them; `--explain-sounds` became `--explain-lyrics`.
- **Hearing-debug stops spending the model's time on what is not worth it.** Explanations are made only for
  speech scoring 4.0 or more (`--explain-above`; `--explain-sounds` adds whisper's captions), and quiet is now
  judged from the live audio, not from pipeline events, because a long stretch of talking produced no event and
  explanations started in the middle of it, stalling whisper for 12 to 24 s. Whisper's `[silence]`, `(silence)`,
  `[BLANK_AUDIO]` and `[no audio]` markers are dropped before scoring, and the sound-only second pass is skipped
  when whisper has already called the audio silent. The written reply is capped at 80 tokens, down from 120.
- **Hearing-debug: explanations are one run with the score, and no longer compete with listening.** The debug
  request now returns the model's written reply and the score read from the probabilities of that same output's
  first token (`InterestClient.interpret` returns an `Interpretation`), so the number and the reasons belong
  together; the Printer's own one-token score is shown beside them. On the Pi, explanations measured 1.25 tokens a
  second while listening and scoring, so they now start only after 3 s of quiet (`--explain-quiet`) and an `e`
  command explains the last few sentences on demand, including after a recording has played.
- **Prompt edits reach the Pi through git, not by copying.** `probe_hearing_tune.sh` (Mac) keeps a second
  worktree of a throwaway `prompt-tuning` branch; on each save it checks the config loads with no problems
  (a typo is not sent), commits it there and pushes, never switching your checkout off `main`. On the Pi,
  `probe_hearing_run.sh` follows that branch with `git fetch` into a scratch worktree (`probe_hearing_follow_tuning.sh`),
  leaving the main clone untouched. `stop` deletes the branch; the Pi's runner removes its scratch checkout and the branch's tracking ref on exit. An earlier file-copy version was replaced.
- **Hearing-debug uses whisper's own voice detection.** `whisper-cli --vad` (a Silero model) now decides what
  is speech, instead of a threshold of mine; the energy cut is only a chunker for the live stream. On the quiet
  class recording it replaced a drift into invented text ("You", "I'm sorry") with an accurate transcript that
  stops where the speech stops. The VAD ignores singing and most sounds, so when it finds no speech a second pass
  runs without it and keeps only what whisper itself marks as non-speech (lyrics between music symbols and
  captions like "(dog barks)"), labeled "sound, not speech"; plain text from that pass is dropped as likely
  invented. `setup-whisper.sh` now fetches the VAD model. A bug fixed on the way: the "nothing heard in 30 s"
  message printed after every utterance.
- **Hearing-debug no longer measures the room's noise floor once at startup.** That failed when it was
  started in a room that was already loud. The floor is now re-estimated continuously as a low percentile of the
  last 30 s of levels, so it works from the first frame whatever the room is doing. On the attic clip (music from
  the start) it finds 8 sensible utterances where the old version found 2.
- **Hearing-debug now explains silence.** When nothing has been heard for 30 s it says why (the room's noise
  floor, the speech threshold and the loudest sound of the last 5 s), and an `l` command shows the live levels.
  It also said nothing when the noise floor had been measured with a podcast already playing, which put the
  speech threshold above a quiet podcast's voice.
- **Hearing-debug's config sync** now says plainly that it runs on the Mac when it is started on the Pi.
- **Pi benchmark whisper logs** were named by phase alone, so the second Scorer model's overwrote the
  first's; the name now includes the model. The report also shows memory used against the pre-start
  baseline, since the server's resident size counts file-backed model pages.
- **Pi benchmark readiness check** always said no whisper was running, because `pgrep` rejected the
  long process-name pattern; it now tests `whisper-cli` and `whisper-server` separately.

### Security

- **Repository made public** on 2026-10-21 at the owner's decision, including the owner-cleared
  Wheelwriter manuals and dad jokes, which `corpus.py publish-check` flags by design.

[Unreleased]: https://github.com/wmodes/pom/commits/main
