# Project state: read this first after a break

A handoff for whoever picks the work up next (a person, or Claude after its context was compacted).
Written 2026-10-21. It says where things stand, the rules we work by, and what comes next. For
detail it points at the other docs instead of repeating them. If this file and the code disagree,
trust the code and fix this file.

## What this is

The **Printer of Mysteries**: an IBM Wheelwriter 1000 typewriter, driven by a Raspberry Pi 5 (16 GB,
hostname `pom1`), that eavesdrops on a room and every so often types a tangential, lyrical
pronouncement. People can also type to it. Fully local: no cloud at run time. Its in-world home is
the Office of Ordinary Hauntings site (officeofordinaryhauntings.net, case study CS-0027), and a
public **Observation Session** is planned for **December 18**, when the stronger of two voices runs.

The full design notes are Wes's own document (not in this repo); the decisions that matter are
summarized below.

## Where things stand

- **Built and tested** (439 tests, all passing): the shared plumbing (`report`, `logs`, `config`),
  the corpus module (`Weights`, `ChunkStore`, `Sampler`), and the `Fragmenter`.
- **Corpus:** 81 sources, 14 registers, about 72,000 chunks, public on GitHub. Alice's Adventures in
  Wonderland went in as the 14th register (`nonsense`) to test how hard growing the corpus is: easy.
- **Git:** branch `main`, remote `origin` (github.com/wmodes/pom, public). Run `git status -sb` to see
  what is unpushed; as of writing that is the Fragmenter, its helper script and this handoff.
- **Not built yet:** everything from the Trigger policy up (see "Next steps"), the Voice and model
  runner, the Output side, the Conductor, and the Pi deployment. The LLM bake-off, the Track B
  fine-tune and the curated hinge pairs have not been started.

## The rules we work by

**Code**
- Everything in `src/pom/` is object-oriented: small classes, one concern each, methods that each
  do one thing, plain data objects passed between them. Scripts in `scripts/` are standalone helpers;
  they may import `pom`, and `pom` never imports them.
- **The Printer must never stop because of a typo or a recoverable fault** (a gallery cannot crash).
  A problem a module can recover from is recovered from and recorded in a `pom.report.Report`
  (WARNING = recovered, CRITICAL = cannot do its job, and even then nothing raises). `strict=True`
  turns every issue into an exception, for tests and dev tools only.
- Modules do not read `Config`. The entry point builds one `Config` and passes values in
  (for example `Sampler(samples=config.samples)`). Anything a person might tune (system messages,
  thresholds, counts) goes in config, so all the user-serviceable parts are in one place.
- Config layers, highest priority first: environment variables (`POM_STRICT`, `POM_DEBUG`,
  `POM_LOCAL_LOGS`, `POM_LOG_LEVEL`, `POM_SAMPLES_MIN/MAX`), the gitignored `config/pom.local.toml`
  (this machine only; currently sets DEBUG logging and `local_logs`), the committed `config/pom.toml`
  (the quiet defaults the Pi runs with), built-in defaults. Every setting is validated leniently:
  a bad value means the default plus a warning.
- Logging goes to stdout (journald collects it when this runs as a service). One `debug` flag in
  config turns on debug output everywhere; each module has a `DEBUG = None` line at the top that
  overrides it for that module (a test refuses a committed override). `local_logs` also writes
  `logs/pom.log` (gitignored, rotating) for work on the Mac.
- **No transcript of the room is ever kept.** Heard speech lives in memory only; logs hold counts and
  scores, never words.

**Process**
- Build **leaf-first**: the modules that depend on nothing, one at a time, each with its own tests and
  a way to see it work, before anything that assembles them.
- Every module gets a test suite. Break the code on purpose to check the tests notice (mutation
  checking; see "Gotchas").
- **Commit early and often**, one logical change per commit, and update `CHANGELOG.md` (Keep a
  Changelog format) in the same commit. Commit messages end with the Co-Authored-By line given in the
  session. Push only when asked.
- Prefer prebuilt installs (Homebrew, apt, wheels) over compiling.
- Documentation is general, not detailed. Anything repeatable lives in scripts, not by hand.
- When asked for a "list" that will be texted, give plain bullets, not a table.

## Architecture: modules, built and planned

```
src/pom/
  report.py            built   problems as data (WARNING / CRITICAL, strict mode, cap, logging)
  logs.py              built   stdout logging, debug flag, per-module override, local log file
  config.py            built   shared settings, layered (env > local file > file > defaults)
  corpus/
    weights.py         built   weights.json: register shares, source weights, defaults, drift checks
    chunk_store.py     built   reads corpus/chunks/*.jsonl into immutable Chunk objects
    sampler.py         built   weighted random draws; 3 to 5 chunks per pull; no repeats in a pull
  listening/
    fragmenter.py      built   transcript text -> sentence / clause / phrase fragments, all levels
    (audio source, transcriber [whisper.cpp], room energy: planned)
  hinge/               planned Scorer (interest), Trigger policy (moving threshold), Hinge picker
  prompt/              planned Prompt builder (system messages come from config)
  voice/               planned Voice interface, Track A and Track B voices, model runner (llama.cpp)
  output/              planned reply selection, formatter, serial port wrapper (pauses listening)
  input/               planned keyboard, same path as speech with a small score bonus
  conductor            planned the only module that knows the whole pipeline
```

## Design decisions to keep

**The pipeline.** Whisper transcribes; the Fragmenter cuts the text into candidates at all levels
(sentence, clause, phrase), each stamped with a time. A scorer rates how interesting each is.
Scores fade with age (so hinges come from roughly the last minute). An utterance triggers when the
best recent fragment clears a moving threshold: lower in a quiet room, higher in a loud one, driven
by voice activity and a target rate (about 4 an hour), with a jump after each print that decays
(no fixed cooldown). The hinge is a weighted random pick from the top candidates, with a
temperature. Typed input takes the same path with a small bonus. The mic is ignored while printing
and for a few seconds after. Whisper's phantom phrases are filtered using its timestamps.

**The scorer** should be separate from the voice model (a base model cannot be asked to rate
anything). A tiny model or heuristics first; not yet decided.

**The hinge is not a corpus lookup.** The leap from room to reply is made by the voice model, not
looked up; lookup was rejected as too literal. The corpus enters through training (Track B) and as
register exemplars (Track A).

**Two voices behind one interface**, chosen by config:
- *Track A, insurance voice* (days): a stock instruct model with a persona prompt ("never break
  character", let the hinge in sideways, don't answer it), plus exemplars: 3 to 5 chunks drawn by the
  Sampler (random, never matched to the hinge) and a few curated hinge pairs.
- *Track B, personality voice* (weeks): a base model, LoRA fine-tuned on the corpus plus hinge pairs
  so it cannot speak outside its registers, run with an almost empty prompt (just the hinge). Training
  runs on the Mac (M4 Max, 64 GB, with MLX), never on the Pi.
- Bake-off: the same hinges to both voices, compared side by side. The stronger runs on Dec 18;
  the other stays as backup, one config change away.

**Output** is usually 15 to 50 words, occasionally up to 150 (typing speed is about 16 characters a
second). The output validation hook (`scripts/dev/corpus/validate_output.py`) is a deliberate stub:
verbatim-overlap checking was judged the wrong measure (false positives on quotes that land, false
negatives on near-copies, blind to echoing the hinge); its header records the reasoning.

**Hinge pairs** (a mundane overheard line paired with a tangential in-register reply) will be curated
by Wes, Neil and River, with a large LLM drafting candidates; that drafting is logged in the AI-use
ledger (`documentation/ai-ledger.md`, which counts remote AI use only).

**The Observation Session theatre.** The site's "clean room" and wireless ban are pure theatre; the
Pi keeps its radios. Speech is transcribed and discarded; nothing is kept, though the site says
"recorded in full" as part of the bit. A door notice such as "Speech in the room is transcribed and
discarded. Nothing is kept." is the honest line.

## The corpus, in short

`corpus/ready/` holds clean utterances, one paragraph each, no trace of their source. `corpus/chunks/`
turns them into 15 to 150 word JSONL chunks (short ones stay whole, exact duplicates are dropped).
`corpus/chunks/weights.json` is the single hand-edited file for draw weights: a tree of registers,
each with a `share` and its sources' `weight`s; anything unlisted counts 1.0. Draw odds are computed
at load time, never stored. Registers are *what the oracle might say*, not where text came from, and
by default share the draws equally.

- Adding material, start to finish: `documentation/adding-material.md`.
- Rules, columns, flags: `corpus/README.md`. How the standardizer works and what went wrong:
  `documentation/corpus-notes.md`.
- Rights: public domain, plus `owner-cleared` (not public domain, decided by Wes: the IBM/Lexmark
  Wheelwriter manuals and the dad jokes). Wes made the repo public knowing this; `corpus.py
  publish-check` still flags them by design.
- `ready/` files are marked reviewed provisionally; Wes (and River, via the public repo) still have to
  review them.
- Known gaps: the CadetWriter guide (needs a rights decision), Jung's 1916 book (candidate), leads with
  no source yet (telegrams, memos, modern classifieds and obituaries, US federal text such as IRS and
  NASA), Doyle's *History of Spiritualism*, I Ching hexagrams 32 to 64, the rest of Psalms and Isaiah.
- Sampling note: chunks are not remembered across pulls. A simulation showed repeats are negligible
  over an evening (0.1%) and only matter over months (the Sampler docstring has the numbers); a
  recency decay was prototyped and deliberately left out.

## How to work here

```
# the project's Python (Homebrew 3.11, matching the Pi's Bookworm); below it is called `python`
/Users/wmodes/dev/envs/pom/bin/python

python -m pytest -q                                     # all tests (use PYTHONDONTWRITEBYTECODE=1)
python scripts/dev/corpus/sample.py --pulls 3           # play with the Sampler (--stats N for shares)
python scripts/dev/listening/fragment.py "Hi, there."   # play with the Fragmenter
python scripts/dev/corpus/audit.py                      # artifact rates per ready/ file
python scripts/dev/corpus/chunk.py                      # regenerate corpus/chunks/
python scripts/dev/corpus/draw_weights.py               # the draw-odds table
```

The Pi (`ssh pi@pom1.local`) is only reachable on the home network. See
`documentation/pi-setup-notes.md` for its setup, Comitup Wi-Fi fallback and the whisper.cpp and
llama.cpp setup scripts.

## Next steps, in order

1. **Trigger policy**: the moving threshold, its jump and decay after a print, and fading of old
   scores. Pure math over time, no clock inside (pass time in).
2. **Scorer**: interest rating of fragments (heuristics first, a tiny model later), separate from the
   voice.
3. **Hinge picker**: weighted random pick from the top candidates, with a temperature.
4. **Prompt builder**: persona and system messages come from config; it draws exemplars from the
   Sampler. Config will need multi-line strings and probably sections.
5. **Voice interface and model runner** (llama.cpp), Track A first; then the bake-off harness, hinge
   pairs, Track B training.
6. Listening adapters (audio, whisper.cpp, room energy), the Output side, the Conductor, then deploy
   to the Pi (git remote or deploy key) and the memory and latency checks there.
7. Open: base model choice (4B class), the hinge-pair set, scorer design, threshold parameters.

## Gotchas

- **Mutation checking** (editing a module to break it and confirming a test fails): run with
  `PYTHONDONTWRITEBYTECODE=1`, `python -B` and `-p no:cacheprovider`, clear `__pycache__` first, and
  run the whole `tests/` directory. A same-length edit restored within a second keeps running stale
  bytecode otherwise. The harness is not in the repo; rebuild it from this description.
- `caplog.at_level(level, logger="name")` resets that logger's level, which cancels a module's
  `DEBUG` override; to test the override, set the level on the root logger only.
- The test of the committed `config/pom.toml` checks a copy, so a developer's `pom.local.toml` cannot
  change it. Tests that touch the real corpus derive their expectations from the data (no
  hardcoded register counts).
- `sources.csv` no longer has a `weight` column; weights live only in `corpus/chunks/weights.json`.
- In zsh, `echo` interprets backslashes in text read back from files; use Python to print raw values.
