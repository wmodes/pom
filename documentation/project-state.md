# Project state: read this first after a break

A handoff for whoever picks the work up next (a person, or Claude after its context was compacted).
Written 2026-10-21. It says where things stand, the rules we work by, and what comes next. For
detail it points at the other docs instead of repeating them. If this file and the code disagree,
trust the code and fix this file.

## What this is

The **Printer of Mysteries**: an IBM Wheelwriter 1000 typewriter, driven by a Raspberry Pi 5 (16 GB,
hostname `pom1`), that eavesdrops on a room and every so often types a tangential, lyrical
pronouncement. People can also type to it. Fully local: no cloud at run time. Its in-world home is
the Office of Ordinary Hauntings site (officeofordinaryhauntings.net, case study CS-0027).

The design notes are in `documentation/pom_design_doc.md` (rewritten 2026-10-27). The decisions that matter are
summarized below; where this file's older sections (the trigger, the Scorer plan, the two voices) differ from the design
doc, the design doc is newer.

## Where things stand

- **Built and tested** (609 tests, all passing): the shared plumbing (`report`, `logs`, `config`,
  now with a `[scorer]` section), the corpus module (`DrawWeights`, `ChunkStore`, `ExemplarSelector`), the
  `SegmentFragmenter`, the word tagger (`scoring/word_tagger.py`, the Scorer's filter), the
  `ScorerModelClient` (asks llama-server for a fragment's score) and the `Scorer` (filter, model,
  circuit breaker; a fragment the model cannot score is left unscored).
- **Scorer design settled by an experiment** (2026-10-22): a small local LLM scores one sentence at
  a time, no context. See `experiments/interest-scorer/README.md` and the decisions below. The
  Scorer module is built; what remains is a playground script and the entry point that builds it
  from `config.scorer` and calls `warm_up` at startup.
- **Corpus:** 81 sources, 14 registers, about 72,000 chunks, public on GitHub. Alice's Adventures in
  Wonderland went in as the 14th register (`nonsense`) to test how hard growing the corpus is: easy.
- **Git:** branch `main`, remote `origin` (github.com/wmodes/pom, public). Run `git status -sb` to see
  what is unpushed; as of writing that is the SegmentFragmenter, its helper script and this handoff.
- **Not built yet:** everything from the Trigger policy up (see "Next steps"), the Voice and model
  runner, the Output side, the Conductor, and the Pi deployment. The LLM bake-off, the Track B
  fine-tune and the curated hinge pairs have not been started.

## The rules we work by

**Code**
- Everything in `src/pom/` is object-oriented: small classes, one concern each, methods that each
  do one thing, plain data objects passed between them. Scripts in `scripts/` are standalone helpers;
  they may import `pom`, and `pom` never imports them.
- **The Printer must never stop because of a typo or a recoverable fault** (a gallery cannot crash).
  A problem a module can recover from is recovered from and recorded in a `pom.fault_recorder.FaultRecorder`
  (WARNING = recovered, CRITICAL = cannot do its job, and even then nothing raises). `strict=True`
  turns every fault into an exception, for tests and dev tools only.
- Modules do not read `Config`. The entry point builds one `Config` and passes values in
  (for example `ExemplarSelector(exemplar_range=config.exemplar_range)`). Anything a person might tune (system messages,
  thresholds, counts) goes in config, so all the user-serviceable parts are in one place.
- Config layers, highest priority first: environment variables (`POM_STRICT`, `POM_DEBUG`,
  `POM_LOCAL_LOGS`, `POM_LOG_LEVEL`, `POM_EXEMPLARS_MIN/MAX`), the gitignored `config/pom.local.toml`
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
  fault_recorder.py    built   faults as data (WARNING / CRITICAL, strict mode, cap, logging)
  log_setup.py         built   stdout logging, debug flag, per-module override, local log file
  config.py            built   shared settings, layered (env > local file > file > defaults)
  corpus/
    draw_weights.py    built   weights.json: register shares, source weights, defaults, drift checks
    chunk_store.py     built   reads corpus/chunks/*.jsonl into immutable Chunk objects
    exemplar_selector.py built weighted random draws; 3 to 5 chunks per pull; no repeats in a pull
  listening/
    segment_fragmenter.py built transcript text -> sentence / clause / phrase fragments, all levels
    (audio source, transcriber [whisper.cpp], room energy: planned)
  scoring/
    word_tagger.py     built   text -> words with lemma, part of speech, name/number/filler flags (NLTK)
    scorer_model_client.py built one fragment to the scorer model, one score (or an explanation) back
    scorer.py          built   fragment_filter, scorer model, circuit breaker; unscored if no answer
    fallback_scorer.py shelved word-counting stand-in for the scorer model; unused
    convo_interest_tracker.py built the window, the fence and the trigger test (used by hearing-debug)
    (Hinge picker, and the entry point that wires it all together: planned)
  prompt/
    prompt_builder.py  built   hinge + drawn exemplars into the oracle prompt from config
  voice/
    oracle_model_client.py built the finished oracle prompt to the oracle model's llama-server, one portent back
    (Track A and Track B voices, bake-off harness: planned)
  output/              planned reply selection, formatter, serial port wrapper (pauses listening)
  input/               planned keyboard, same path as speech with a small score bonus
  conductor            planned the only module that knows the whole pipeline
```

## Design decisions to keep

**The pipeline.** Whisper transcribes; the SegmentFragmenter cuts the text into candidates at all levels
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
  ExemplarSelector (random, never matched to the hinge) and a few curated hinge pairs.
- *Track B, personality voice* (weeks): a base model, LoRA fine-tuned on the corpus plus hinge pairs
  so it cannot speak outside its registers, run with an almost empty prompt (just the hinge). Training
  runs on the Mac (M4 Max, 64 GB, with MLX), never on the Pi.
- Bake-off: the same hinges to both voices, compared side by side. The stronger runs;
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
  over an evening (0.1%) and only matter over months (the ExemplarSelector docstring has the numbers); a
  recency decay was prototyped and deliberately left out.

## How to work here

```
# the project's Python (Homebrew 3.11, matching the Pi's Bookworm); below it is called `python`
/Users/wmodes/dev/envs/pom/bin/python

python -m pytest -q                                     # all tests (use PYTHONDONTWRITEBYTECODE=1)
python scripts/dev/corpus/sample.py --pulls 3           # play with the ExemplarSelector (--stats N for shares)
python scripts/dev/listening/fragment.py "Hi, there."   # play with the SegmentFragmenter
python scripts/dev/corpus/audit.py                      # artifact rates per ready/ file
python scripts/dev/corpus/chunk.py                      # regenerate corpus/chunks/
python scripts/dev/corpus/draw_weights.py               # the draw-odds table
```

The Pi (`ssh pi@pom1.local`) is only reachable on the home network. See
`documentation/pi-setup-notes.md` for its setup, Comitup Wi-Fi fallback and the whisper.cpp and
llama.cpp setup scripts.

## Decisions since the first handoff (2026-10-21, later)

- **Pipeline written out as workers with handoff triggers** (Microphone, Segmenter, Transcriber,
  SegmentFragmenter, Scorer, Trigger policy, Hinge picker, then Prompt builder, Voice, Output; plus an
  Activity tracker and the keyboard Input). Wes has the current wording in his design notes.
- **Words:** a *fragment* is a piece the SegmentFragmenter cuts; the Scorer makes *scored fragments*, also
  called *candidate hinges*; the *hinge* is the one the Hinge picker finally chooses.
- **Conductor uses asyncio** (Wes dislikes blocking Python): each worker is an async task with an
  `asyncio.Queue` between them; leaf modules stay plain synchronous code; anything that blocks is
  awaited or run in a thread; `TaskGroup` (Python 3.11) for clean shutdown.
- **Deaf only while the typewriter is actually printing.** While deciding whether to trigger, and
  while the voice generates, the Segmenter keeps gathering utterances into a bounded queue; only the
  Transcriber pauses during generation and then catches up.
- **Segmenting:** a voice-activity detector cuts utterances at a pause of about 0.7 s, or at a 20 s
  maximum with about 2 s overlap (duplicates dropped); an energy-based detector with a slowly moving
  noise floor first, a library later if too crude; Whisper's own no-speech check is a second screen.
  **Hearing-debug has since moved to whisper-driven cutting** (`experiments/hearing-debug/rolling_cutter.py`): the
  energy detector never finds a pause in a room with bursty noise, so whisper's own voice detection and segments
  decide, and the energy cutter is kept behind `--energy-cutter` for comparison. Not yet in `src/pom/`.
- **Room activity** is seconds of accepted speech per minute (from the Transcriber), not loudness.
  Target rate: 0 when silent, about 4 prints an hour with a little talking, about 8 busy (config),
  steered by a rate governor; paper guards: quiet timeout about 10 min, ceilings about 12 an hour
  and 60 a day (proposed values).
- **The Scorer is a small local LLM (decided 2026-10-22, replacing the plan below).** One sentence
  per request, no context, score read from the digit probabilities, so a sentence scores the same
  whatever came before it. Batching and shown-context both made scores depend on order and neighbors
  and were rejected. Qwen3-4B (Q4_K_M) is the current choice; Llama-3.2-3B is the faster alternative.
  The prompt (in config eventually) keeps the definition of "interesting" separate from the meaning of
  the numbers, with no persona. Scores run low and relative (top about 6 to 8), so the trigger must
  work from rank and gaps, not a fixed high threshold; the hinge picker's temperature does this.
  Fragments become **whole sentences** (the SegmentFragmenter gets a config setting for which levels it
  emits; clauses and phrases stay available). The word tagger stays as the filter. The
  word-counting fallback is shelved, unused, in `scoring/fallback_scorer.py`. Evidence, rules and results: `experiments/interest-scorer/`.
- **Pi timing done** (`experiments/pi-benchmark/`, 2026-10-22): with whisper running flat out on shared
  cores, Qwen3-4B scores a sentence in a median 2.3 s (p95 3.2 s; 4.3 s in the harshest sustained case)
  and Llama-3.2-3B in 1.4 s; both use about 3 GB, peak 72 °C, no throttling. Whisper slows about 2.3
  times but stays real-time. The first call after a server starts takes 12 to 28 s, so warm it up at
  startup. Real whisper output on a quiet room needs level normalization first and a filter for non-speech
  tags and phantom repeats; Qwen scores the phantom words 1.0, Llama about 4.
- **Decided by Wes (2026-10-23):** start with **Qwen3-4B**, and start the trigger threshold at
  **7.0** on the Scorer's scale (the Trigger is not built yet; put the value in config when it is).
  On the saved samples that lets through only the vivid lines (scores of about 7.3 to 8.0) and none of
  the ordinary class chatter (top 5.0) or song lyrics (top 6.0). Scores are relative, so expect to tune it.
- **First look at the real microphone** (ATR4697-USB on the Pi, 2026-10-23, music playing in the room):
  it records quietly, mean -38 dB with peaks near -18 dB, like the phone recordings; its capture gain is
  21 of 30 (+19.5 dB) with hardware auto-gain off, so there is room to raise it. Whisper transcribed the
  song lyrics almost completely and wrapped most lines in ♪ marks (not all: one lyric line came through
  unmarked). **Wes's decision (2026-10-23): the Printer reacts to what it hears, not only conversation, so
  the Transcriber strips the ♪ symbol and keeps the words**; lyrics then compete on score like any other
  sentence (the lyrics seen scored at most 6.0, under the 7.0 threshold). **Whisper's bracketed sound
  captions** such as "(dog barks)" are kept too (Wes: its guesses give the model more to go on); most
  score 1 to 2, but "(gunshot)" scored 8.0 and "(screaming)" 4.65 as written (3.4 and 2.0 without the
  brackets), so a hallucinated dramatic caption can clear 7.0. Whisper took 0.2 (base.en) and 0.5 (small.en) times real time on
  the music. Mic gain: tested at 26 of 30 (+7.5 dB over the default 21); normal speech averaged -33 to -27 dB,
  but a yell peaked at -2.7 dB, too close to clipping, so the working setting is **24 of 30**
  (+24 dB; about 5.7 dB of headroom for a yell). It resets on reboot, so the service must set it at startup
  (a config setting, to be built). Hardware auto-gain stays off. Software level normalization per
  utterance, before whisper, is the part that adapts to the room.
- **Trigger idea (Wes, 2026-10-23):** make the threshold variable, depending on whether the room is
  producing many interesting things or few. Plan: 7.0 is the neutral starting point; the moving threshold
  steers toward the target print rate (so a run of strong lines raises the bar and a dull stretch eases
  it, never below a floor); optionally also compare with a high percentile of the room's recent scores.
- **Revisit later (Wes, 2026-10-23): real whispers.** In the quietest of rooms, an actual whispered aside
  (something Shakespearean) might be worth acting on. Today a whisper is treated like any quiet sound: the energy
  gate may not pass it and the VAD may not call it speech. Revisit once the rest is sorted: how a very quiet voice
  against a very quiet room is segmented and transcribed, and whether it should count for more.
- **Model size is a lever (2026-10-23):** with Wes's unchanged prompt, Qwen3-8B scores gentle-weird single sentences
  at 7 to 8 where the 4B gives about 5, without rewarding stacked sentences more (see
  `experiments/interest-scorer/README.md`). Next: time the 8B on the Pi with whisper running before choosing it.
- **Still open for the Scorer:** a real-room test of speech with the USB microphone; and
  Wes's conclusion that we must first define for ourselves what "interesting" is and what should
  trigger a print, then state it to the model.
- **Earlier plan, now a fallback:** no separate model. Signals (weights in config): snag on the corpus (corpus
  counts times rarity in everyday speech from `wordfreq`), concreteness (WordNet senses, a vote across
  senses, not the first sense), feeling (NRC-VAD lexicon: free for non-commercial research and
  education, **not redistributable**, so each machine downloads its own into the gitignored
  `data/lexicons/NRC-VAD-Lexicon-v2.1/` (already downloaded on Wes's Mac); scores run from -1 to 1 with 0
  neutral, as tab-separated `term valence arousal dominance`, 44,729 words plus multi-word terms; missing
  means the signal is off with a warning; credit it in a credits file),
  names/numbers/questions (NLTK part-of-speech tags: NNP, CD; the question mark), a filler penalty
  (stopwords, interjections), a recent-use penalty.
- **Probe results that shaped it:** exact corpus matches are plentiful (dog 357, candle 108, couch 56,
  complain 67); WordNet's first sense is often wrong (draft -> a bill, Karen -> communication); WordNet
  neighbors across senses are noisy (dog -> head), so use them only as a small bonus: synonyms and direct
  broader/narrower words of the main sense, no siblings.
- **Libraries:** `wordfreq` and `nltk` are in `requirements.txt`; `scripts/setup/setup-nltk-data.sh`
  fetches the NLTK data into `data/nltk/` (gitignored). Set `NLTK_DATA=<repo>/data/nltk`.

## Next steps, in order

0. **Scorer playground** (`scripts/dev/scoring/score.py`: text in, ranked scored sentences out), then
   the entry point that builds the client and Scorer from `config.scorer`. Starting things up is the job
   of `src/pom/startup.py`: the runner is built and the ten duties (mic gain, Scorer warm-up, checks
   for whisper, the voice model and the typewriter...) are placeholders in `planned_steps()` to fill in. The Pi benchmark is done (see above). Then collect more real-room transcripts to try
   the Scorer on.
1. **Trigger policy**: the moving threshold, its jump and decay after a print, and fading of old
   scores. Pure math over time, no clock inside (pass time in).
2. **SegmentFragmenter setting** for which levels to emit (sentences only by default).
3. **Hinge picker**: weighted random pick from the top candidates, with a temperature.
4. **Prompt builder**: persona and system messages come from config; it draws exemplars from the
   ExemplarSelector. Config will need multi-line strings and probably sections.
5. **Voice interface and model runner** (llama.cpp), Track A first; then the bake-off harness, hinge
   pairs, Track B training.
6. Listening adapters (audio, whisper.cpp, room energy), the Output side, the Conductor, then run it
   on the Pi (deployed with `git pull`; see `documentation/pi-setup-notes.md`) and check memory and latency there.
   Future idea (Wes): updating the Pi is `git pull` then `scripts/restart.py`. That needs an entry point, a
   systemd unit and a Pi virtualenv first; the script would pull fast-forward only, refuse on a dirty tree, install
   dependencies only if they changed, check the new config before touching the running service, skip the restart
   for docs-only changes, then restart and show the startup "ready" line.
7. Open: base model choice (4B class), the hinge-pair set, scorer design, threshold parameters.
8. **Define "failing loudly"** (Wes, 2026-10-24). If the scorer model or the oracle model is down, the Printer
   can never respond, and a line in the log is not loud when someone is switching a printer on. Decide what
   loud means (a beep or bell, an email to Wes, something at the venue) and make the `scorer-server` and
   oracle-model startup steps critical in that way. Today `scorer-server` is not critical and a fragment the
   scorer model cannot score is left unscored.

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
