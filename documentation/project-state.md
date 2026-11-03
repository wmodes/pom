# Project state: read this first after a break

A handoff for whoever picks the work up next (a person, or Claude after its context was compacted).
Written 2026-10-21, rewritten 2026-10-27. It says where things stand, the rules we work by, and what comes next.
For detail it points at the other docs instead of repeating them: **the design is in `pom_design_doc.md`**, the words
are in `lexicon.md`. If this file and the code disagree, trust the code and fix this file.

## What this is

The **Printer of Mysteries**: an IBM Wheelwriter 1000 typewriter, driven by a Raspberry Pi 5 (16 GB,
hostname `pom1`), that eavesdrops on a room and every so often types a tangential, lyrical
pronouncement (a *portent*). People can also type to it. Fully local: no cloud at run time. Its in-world home is
the Office of Ordinary Hauntings site (officeofordinaryhauntings.net, case study CS-0027).

## Where things stand (2026-10-27)

- **Built and tested** (1,494 tests, all passing): the shared plumbing (`fault_recorder`, `log_setup`, `config` with
  `[scorer]`, `[qualifying]`, `[listening]` and `[oracle]` sections, the `startup` runner whose ten duties are still placeholders); the library
  module (`Circulation`, `Catalog`, `Bibliomancer`); the `TakeCutter`; the whole listening side (`MicDeck`, `WavDeck`, `WhisperModelClient`, `RollingCutter`,
  `TrackSplitter`, `Eavesdropper`); the Scorer (`WordTagger`,
  `ScorerModelClient`, `ClipScorer`); the `BarKeeper` (qualifying); the `PromptBuilder`; the `OracleModelClient`.
- **Decided:**
  - **Scorer model:** Qwen3-4B, one sentence per request, no context, a 0 to 9 score.
  - **Qualifying:** a percentile of the last five minutes of scores (the larger of 5 minutes or the last 20 scores), `percentile`
    99.5 (about one score in 200 stands above the bar), 60 s cooldown, no floor. Replayed on five recorded meetings (four Zoom,
    one live room) it gives about 9.8 qualifiers an hour (8.1 to 11.2 by meeting; the live room 11.2, one every 5.4 minutes). The goal is about six an hour. Tukey's fence was tried first and replaced
    (2026-10-29): it sat above the window's best score in two windows out of three.
  - **Hinge:** the sentence that qualified (Wes, 2026-10-27). No separate hinge picker.
  - **Oracle model:** Qwen3.5-9B, with Gemma 2 9B as the named alternate. On the Pi a typical portent takes 35 to 40 s
    (first word after 20 s, up to about 100 s for a long one); Wes judged that acceptable.
  - **Half-duplex:** nothing is sent to whisper or the scorer model while the oracle model writes or the typewriter prints.
  - **All the models stay loaded (Wes's decision, restated 2026-11-03):** whisper, the scorer and the oracle are resident in memory at the same time, always (the 16 GB Pi is why). Half-duplex is about CPU, not memory. Never propose loading on demand or shrinking contexts for memory. Servers run as separate systemd services (agreed 2026-11-03, units not written yet); see the design doc, "All the models stay loaded, always".
- **Experiments** (`experiments/`, not part of the Printer until rebuilt in `src/pom/`): `hearing-debug` (the working
  listening prototype), `interest-scorer`, `pi-benchmark`, `oracle-playground`, `oracle-bakeoff` (seven models, 160 rated
  portents each), `pi-oracle-benchmark` (the 9B on the Pi, run 2026-10-27).
- **Library:** 81 holdings, 14 shelves, about 72,000 passages, public on GitHub.
- **Git:** branch `main`, remote `origin` (github.com/wmodes/pom, public). Run `git status -sb` to see what is
  unpushed. The Pi's checkout (`~/pom`) was last pulled at `b0b68e9`, with the 4B and 9B model files in `data/models/`.
- **Testbenches** (`scripts/dev/`, small tools that use the real modules): `listening/split_whisper_tracks.py`, `listening/listen_to_room.py` (the
  microphone or a recording through the Eavesdropper, Scorer and BarKeeper), `scoring/score_typed_sentences.py` (type sentences, see scores and
  the BarKeeper's ruling), `scoring/score_typed_lines.py` (the typed-line path: score, handicap, answer or silence), `writing/write_portent_from_hinge.py` (type a hinge, get a portent), and `listening/cut_speech_into_hinges.py`, and `pipeline/listening_to_portent.py` (the whole pipeline but the typewriter,
  live from the microphone, `--mac-mic` on a Mac). `pipeline/run_printer_with_servers.py` starts the three servers, runs `python -m pom` with your options, and stops what it started.
- **The Conductor and the entry point are built** (2026-11-02): `python -m pom` runs the whole Printer (see the design doc, "The Conductor"),
  burn-tested on the Mac with real servers; only the real typewriter and the Pi's services are not yet tried together.
- **Not built yet:** the monitoring site's printer status line and per-turn outcome (they need a preview and Wes's say), the systemd units on the Pi,
  the paper guards, "failing loudly", and a full startup check (`startup.py`'s steps are still placeholders). Track B (the fine-tuned oracle)
  and the curated hinge pairs have not been started.

## The rules we work by

**Code**
- Everything in `src/pom/` is object-oriented: small classes, one concern each, methods that each
  do one thing, plain data objects passed between them. Scripts in `scripts/` are standalone helpers;
  they may import `pom`, and `pom` never imports them. `src/pom/` never imports from `experiments/`.
- **The Printer must never stop because of a typo or a recoverable fault** (a gallery cannot crash).
  A problem a module can recover from is recovered from and recorded in a `pom.fault_recorder.FaultRecorder`
  (WARNING = recovered, CRITICAL = cannot do its job, and even then nothing raises). `strict=True`
  turns every fault into an exception, for tests and dev tools only.
- Modules do not read `Config`. The entry point builds one `Config` and passes values in
  (for example `BarKeeper(c.percentile, c.window_seconds, c.min_scores, c.cooldown_seconds)`). Anything a
  person might tune (system messages, thresholds, counts, times) goes in config, so all the user-serviceable parts are
  in one place.
- Config layers, highest priority first: environment variables (`POM_STRICT`, `POM_DEBUG`,
  `POM_LOCAL_LOGS`, `POM_LOG_LEVEL`, `POM_EXEMPLARS_MIN/MAX`, `POM_SCORER_URL`), the gitignored `config/pom.local.toml`
  (this machine only), the committed `config/pom.toml` (the quiet defaults the Pi runs with), built-in defaults. Every
  setting is validated leniently: a bad value means the default plus a warning.
- Logging goes to stdout (journald collects it when this runs as a service). One `debug` flag in
  config turns on debug output everywhere; each module has a `DEBUG = None` line at the top that
  overrides it for that module (a test refuses a committed override). `local_logs` also writes
  `logs/pom.log` (gitignored, rotating) for work on the Mac.
- **No transcript of the room is ever kept.** Heard speech lives in memory only; logs hold counts and
  scores, never words. The one exception is the four recorded team meetings (the "Zoom" files, public by design, Wes's
  say-so), kept only in gitignored `data/recordings/`, used to test the BarKeeper.
- Time is passed in, never read from inside a leaf module (the BarKeeper takes `now`), so a recording replayed fast
  behaves like a live run.

**Process**
- Build **leaf-first**: the modules that depend on nothing, one at a time, each with its own tests and
  a way to see it work, before anything that assembles them.
- **Explain design choices first.** Say how a thing will be solved and list the choices that affect the system, each
  with a recommendation, then wait for Wes's go. Ask before adding any feature. Propose names (a rename is a design
  choice) and use the lexicon's words.
- **Wes writes the prompts** (the scorer prompt and the oracle prompt); Claude measures them and does not author them.
- Every module gets a test suite. Break the code on purpose to check the tests notice (mutation
  checking; see "Gotchas"). Experiments are measured, not guessed: replay recorded data through the real modules.
- **Commit early and often**, one logical change per commit, and update `CHANGELOG.md` (Keep a
  Changelog format) in the same commit. Commit messages end with the Co-Authored-By line given in the
  session. **Stage files by name, never `git add -A`**: Wes edits `config/pom.toml` live, and a blanket add would commit
  work in progress. Push only when asked.
- Deliver to the Pi through git (commit, push, `git pull` there), never by copying files. Wes does not edit or delete on the
  Pi by hand; the Pi is the client. Results come back to the Mac by reading them, then the Pi's copy is removed so a pull
  is not blocked.
- Delete your own leftovers when done (rejected models, old builds, stale logs, temp files) and say what was removed.
- Prefer prebuilt installs (Homebrew, apt, wheels) over compiling. In-plan install steps need no separate go.
- Documentation is general, not detailed. Anything repeatable lives in scripts, not by hand.
- Wes uses they/them in conversation, docs, commit messages and the changelog.
- When asked for a "list" that will be texted, give plain bullets, not a table.

## Architecture: modules, built and planned

```
src/pom/
  fault_recorder.py    built   faults as data (WARNING / CRITICAL, strict mode, cap, logging)
  log_setup.py         built   stdout logging, debug flag, per-module override, local log file
  config.py            built   shared settings, layered; [scorer], [qualifying], [oracle]
  startup.py           built   the startup runner; its ten duties are placeholders in planned_steps()
  library/
    circulation.py     built   circulation.json: shelf shares, holding weights, defaults, drift checks
    catalog.py         built   reads library/passages/*.jsonl into immutable Passage objects
    bibliomancer.py    built   weighted random draws; passages per pull set by [oracle]; no repeats in a pull
  listening/
    take_cutter.py     built   a take's words -> sentence / clause / phrase clips, all levels
    audio_deck, mic_deck, wav_deck built the microphone (arecord, read in a thread) and a recording replay
    whisper_model_client built audio to whisper-server from memory; text or timed segments back
    track_splitter     built   whisper's text -> words / room tone / blanks
    rolling_cutter     built   whisper's own voice detection cuts the stream, every 5 s
    eavesdropper       built   joins them: sound in, takes out (a generator the Conductor runs in a thread)
    audio_format       built   16 kHz mono 16-bit, 30 ms frames: the numbers they share
  scoring/
    word_tagger.py     built   text -> words with lemma, part of speech, name/number/filler flags (NLTK)
    scorer_model_client.py built one clip to the scorer model, one score (or an explanation) back
    clip_scorer.py     built   clip_filter, scorer model, circuit breaker; unscored if no answer
    fallback_scorer.py shelved word-counting stand-in for the scorer model; unused
  qualifying/
    bar_keeper.py      built   the window, the bar, the cooldown: whether an attempt qualifies (bars: percentile, decaying quantile, moving average)
    attempt.py         built   one score trying to qualify, and the ruling it got
  conductor/
    hinge_finder.py    built   listening to hinge
                       planned the Conductor, the only module that knows the whole pipeline; hands the qualifying sentence on as the hinge
  keyboard/
    typed_line_scorer.py built a typed line scored (multiplied by `typed_handicap`) and judged by the same bar
  writing/
    prompt_builder.py  built   hinge + drawn exemplars into the oracle prompt from config
    oracle_model_client.py built the finished oracle prompt to the oracle model's llama-server, one portent back
    portent_writer.py  built   a hinge in, a Portent out (joins the prompt builder and the model client; seedable exemplars)
  printing/
    portent_formatter.py built portent in, printable wrapped text out (fences removed, quote indented, ASCII, line feeds after)
    terminal_port.py   built   text to the typewriter over serial, paced; keyboard lines back (echoplex); ConsoleTerminal stand-in
                       planned reply selection
```

## Design decisions to keep

The reasoning and the numbers are in `pom_design_doc.md`. In short:

- **The pipeline:** whisper (cutting the audio itself) -> the track split (words / room tone / nothing) ->
  TakeCutter -> Scorer -> BarKeeper -> the qualifying sentence is the hinge -> PromptBuilder -> oracle
  model -> Printing. Typed input has its own way in: the TypedLineScorer scores the line, multiplies it by `typed_handicap` (1.5), and the same bar judges it (no cooldown; below the bar it is ignored; it stays out of the bar unless `typed_lines_affect_bar`).
- **Unfinished exemplars never reach the oracle** (2026-11-03). The oracle copies its exemplar's ending, so a library passage that stops on a colon or at "...the" produced cut-off portents. `pom.library.endings.ends_unfinished` is the one definition; the library no longer keeps such passages (`normalize.py`, `cataloguer.py`) and the Bibliomancer redraws if one ever turns up (`[oracle] exemplar_redraws`). `audit.py`'s `unfinished` column must read 0. Details in the design doc, "Library".
- **The scorer** is a small local model scoring one sentence, no context, so a sentence scores the same whatever came
  before. Scores are relative and run low (almost nothing above 7), which is why the BarKeeper works from the room's own
  recent scores and not a fixed threshold.
- **The hinge is not a library lookup.** The leap from room to reply is made by the oracle model; lookup was rejected as
  too literal. The library enters as shelf exemplars in the prompt.
- **Two oracle tracks** was the first plan: a prompted stock model (what the bake-off tested) and a fine-tuned base model
  (Track B, never started; LoRA on the Mac with MLX, never on the Pi).
- **Output** is usually 15 to 50 words, up to 150 (the oracle prompt's ceiling). The output validation hook
  (`scripts/dev/library/validate_output.py`) is a deliberate stub: verbatim-overlap checking was judged the wrong measure.
- **Hinge pairs** (a mundane overheard line paired with a tangential in-shelf reply) will be curated by Wes, Neil
  and River, with a large LLM drafting candidates; that drafting is logged in the AI-use ledger
  (`documentation/ai-ledger.md`, which counts remote AI use only, never local models).
- **The Observation Session theatre.** The site's "clean room" and wireless ban are pure theatre; the
  Pi keeps its radios. Speech is transcribed and discarded; nothing is kept. A door notice such as "Speech in the room
  is transcribed and discarded. Nothing is kept." is the honest line.

## The library, in short

`library/ready/` holds clean utterances, one paragraph each, no trace of their holding. `library/passages/`
turns them into 15 to 150 word JSONL passages (short ones stay whole, exact duplicates are dropped).
`library/passages/circulation.json` is the single hand-edited file for draw weights (the circulation): a tree of shelves,
each with a `share` and its holdings' `weight`s; anything unlisted counts 1.0. Draw odds are computed
at load time, never stored. Shelves are *what the oracle might say*, not where text came from, and
by default share the draws equally. `library/samples/` holds Wes's 100 benchmark sentences for the Scorer (test inputs, not
library material).

- Adding material, start to finish: `documentation/adding-material.md`.
- Rules, columns, flags: `library/README.md`. How the standardizer works and what went wrong:
  `documentation/library-notes.md`.
- Rights: public domain, plus `owner-cleared` (not public domain, decided by Wes: the IBM/Lexmark
  Wheelwriter manuals and the dad jokes). Wes made the repo public knowing this; `library.py
  publish-check` still flags them by design. Licensing and content are not concerns for this project.
- `ready/` files are marked reviewed provisionally; Wes (and River, via the public repo) still have to
  review them.
- Known gaps: the CadetWriter guide (needs a rights decision), Jung's 1916 book (candidate), leads with
  no holding yet (telegrams, memos, modern classifieds and obituaries, US federal text such as IRS and
  NASA), Doyle's *History of Spiritualism*, I Ching hexagrams 32 to 64, the rest of Psalms and Isaiah.
- Sampling note: passages are not remembered across pulls. A simulation showed repeats are negligible
  over an evening (0.1%) and only matter over months (the Bibliomancer docstring has the numbers); a
  recency decay was prototyped and deliberately left out.

## How to work here

```
# the project's Python (Homebrew 3.11, matching the Pi's Bookworm); below it is called `python`
/Users/wmodes/dev/envs/pom/bin/python

PYTHONDONTWRITEBYTECODE=1 python -m pytest -q           # all tests
python scripts/dev/library/sample.py --pulls 3           # play with the Bibliomancer (--stats N for shares)
python scripts/dev/listening/cut_speech_into_hinges.py "Hi, there."   # play with the TakeCutter
python scripts/dev/listening/split_whisper_tracks.py "(dog barks) Hello."   # play with the TrackSplitter
python scripts/dev/scoring/score_typed_sentences.py "Some sentence."        # scores and the BarKeeper's ruling (needs the scorer model's server)
python scripts/dev/writing/write_portent_from_hinge.py --server                # type a hinge, get a portent (needs the oracle model's server)
python scripts/dev/listening/listen_to_room.py --wav FILE --speed 0   # the whole listening side on a recording (needs whisper too)
python scripts/dev/library/audit.py                      # artifact rates per ready/ file
python scripts/dev/library/cataloguer.py                      # regenerate library/passages/
python scripts/dev/library/circulation.py               # the draw-odds table
```

To hear the Printer's judgment live, on the Pi: `cd ~/pom/experiments/hearing-debug && ./probe_hearing_run.sh
--no-explain` (the known-good setup; see the design doc). To replay a recording on the Mac, start whisper-server and the
scorer's llama-server and run `probe_hearing_pipeline.py --wav FILE --speed 0 --no-explain` (see that folder's README).
The Pi oracle benchmark is `experiments/pi-oracle-benchmark/benchmark_oracle_pi_run.sh`, run on the Pi.

The Pi (`ssh pi@pom1.local`) is only reachable on the home network. See `documentation/pi-setup-notes.md` for its setup,
Comitup Wi-Fi fallback and the whisper.cpp and llama.cpp setup scripts.

## Decision log

Dated, kept short. Anything marked *superseded* is here for the reasoning, not as current design.

- **Pipeline written out as workers with handoff qualifiers** (2026-10-21; now in the design doc).
- **Words:** a *clip* is a piece the TakeCutter cuts; the *hinge* is the one that qualified.
- **Conductor uses asyncio** (2026-10-21; Wes dislikes blocking Python): each worker is an async task with an
  `asyncio.Queue` between them; leaf modules stay plain synchronous code; anything that blocks is awaited or run in a
  thread; `TaskGroup` (Python 3.11) for clean shutdown.
- **Deaf only while the typewriter is printing** (2026-10-21) *superseded 2026-10-27 by half-duplex:* nothing is sent
  to whisper or the scorer model while the oracle model writes or the typewriter prints; both stay loaded and idle. No
  lockfiles (they can lock the system out); the plan is a Conductor state with a watchdog timeout, not built.
- **Segmenting:** the energy-based cutter (a slowly moving noise floor, a 0.7 s pause) never finds a pause in a room
  with bursty noise, so **cutting is whisper-driven** (`experiments/hearing-debug/rolling_cutter.py`: a whisper run every
  5 s on a rolling buffer, whisper's own Silero voice detection at threshold 0.5). The energy cutter and the level
  normalizer survive behind `--energy-cutter` in the experiment only. **Known-good setup (2026-10-27):** whisper
  `base.en`, 4 threads, VAD 0.5, mic gain 24 of 30, the 4B scorer on port 8098, `./probe_hearing_run.sh --no-explain`.
  Fragile; change one thing at a time.
- **The Scorer is a small local LLM** (2026-10-22, replacing a word-list plan). One sentence per request, no context,
  score read from the digit probabilities. Batching and shown-context both made scores depend on order and neighbours and
  were rejected. Qwen3-4B (Q4_K_M) chosen 2026-10-23; Llama-3.2-3B is the faster alternative. Clips are whole
  sentences. The word-counting fallback is shelved, unused. Evidence: `experiments/interest-scorer/`.
- **Pi timing of the scorer** (2026-10-22, `experiments/pi-benchmark/`): with whisper running flat out on shared cores,
  Qwen3-4B scores a sentence in a median 2.3 s (p95 3.2 s); both use about 3 GB, peak 72 °C, no throttling. The first call
  after a server starts takes 12 to 28 s, so warm it up at startup. Whisper output on a quiet room needs a filter for
  non-speech tags and phantom repeats.
- **The microphone** (ATR4697-USB on the Pi, 2026-10-23): records quietly; capture gain **24 of 30** with hardware
  auto-gain off (a yell at gain 26 peaked near -3 dB). It resets on reboot, so startup must set it (a config setting, to be
  built).
- **The Printer reacts to what it hears, not only conversation** (Wes, 2026-10-23): the Transcriber strips the music
  symbol and keeps lyric words, which are scored. **Whisper's bracketed room tone** such as "(dog barks)" are never
  scored (a hallucinated "(gunshot)" scored 8.0); they are kept as a short list of recent sounds to give the oracle model
  as context. Nothing-here markers ("[BLANK_AUDIO]") are dropped.
- **Absolute scoring is the enemy of emergence** (Wes, 2026-10-23): thresholds and scales come from what is happening in
  the room, not from constants. Related: the filters must work reasonably well in every room, not perfectly in one; nothing
  is tuned per room.
- **Qualifying** (2026-10-27). *Superseded:* a fixed 7.0 threshold, then 25% above a decaying average with a floor (it fired on
  34% of sentences in recorded meetings and barely responded to its setting). Then Tukey's fence (2026-10-27 to 29; above the real top of the scores).
  *Current:* a percentile of a time window with a cooldown, no floor (see the design doc and `[qualifying]` in `config/pom.toml`).
- **Oracle model** (2026-10-27): Qwen3.5-9B, Gemma 2 9B the alternate. The bake-off is in `model-bakeoff.md`; the Pi run
  is in `experiments/pi-oracle-benchmark/`. Writing is memory-speed-bound on the Pi (about 2 tokens a second) and the first
  word waits on the prompt (about 0.095 s a token). llama.cpp reuses none of a changed prompt for Qwen3.5 (a hybrid model);
  left alone.
- **Revisit later (Wes, 2026-10-23): real whispers.** In the quietest of rooms, an actual whispered aside might be worth
  acting on. Today a whisper is treated like any quiet sound.
- **Earlier word-list plan, now a fallback only:** snag on the corpus (`wordfreq`), concreteness (WordNet), feeling
  (NRC-VAD, free for non-commercial research, **not redistributable**, so each machine downloads its own into the
  gitignored `data/lexicons/`), names/numbers/questions, a filler penalty, a recent-use penalty. `wordfreq` and `nltk` are
  in `requirements.txt`; `scripts/setup/setup-nltk-data.sh` fetches the NLTK data into `data/nltk/` (set
  `NLTK_DATA=<repo>/data/nltk`).

## Next steps, in order

1. **Try the new listening code in the real room, on the Pi.** `git pull`, start whisper and the scorer model
   (the known-good commands are in `scripts/dev/listening/listen_to_room.py`), then run `listen_to_room.py` and compare with the
   known-good `probe_hearing_run.sh` run. The code matches the experiment on a recorded meeting (all 705 things heard
   identical), but the new `MicDeck` has only met stand-in `arecord` programs. One known limit: the room-tone pass for
   lyrics hears about two seconds, not ten (see the design doc).
2. **The Printing side:** the "stop listening while printing" signal belongs to the Conductor. (The `TerminalPort` is built too: paced serial, flow control, CR+LF, keyboard lines back with echoplex; config `[terminal]`; tested on fakes only, not yet on the machine. The `PortentFormatter` is built:
   fences out, a quote indented with blank lines around it, ASCII only, wrapped to the `[printing]` line width, `linefeeds_after_portent` line feeds at the end.)
3. **The service on the Pi.** The Conductor and the entry point are built (`python -m pom`) and the Pi has its virtualenv
   (`/home/pi/envs/pom`). What is left: the systemd units (a draft is in `pi-setup-notes.md`), the startup duties in `startup.py` (mic gain,
   scorer warm-up, checks for whisper, the oracle model and the typewriter...). Updating the Pi is then `git pull` plus a restart script
   (Wes's idea: fast-forward pull only, refuse on a dirty tree, install dependencies only if they changed, check the new config before
   touching the running service, skip the restart for docs-only changes, then show the startup "ready" line).
4. **Paper guards** (nothing qualifies unless speech was heard in the last 10 minutes; hard ceilings of about 12 an hour
   and 60 a day; proposed values) and **"failing loudly"** (Wes, 2026-10-24): if the scorer model or the oracle model is
   down, the Printer can never respond, and a log line is not loud when someone is switching a printer on. Decide what loud
   means (a beep, an email to Wes, something at the venue).
5. **A real-room test,** a quiet room too, then retune from that. The `[qualifying]` settings were tuned on four chatty meetings.
6. Later: the Conductor's typed-input wiring, the curated hinge pairs, Track B if wanted.
7. Open and small: Wes's spot-check of about 20 bake-off ratings; a prompt tuned for the top two oracle models; defining
   "interesting" ourselves before asking the model (Wes, 2026-10-22, deferred); `ratings/report.md` still says Stheno 4.72
   (it is 4.73); a "silence" tic that repeats across portents would not show up in the one-at-a-time ratings.

## Gotchas

- **Mutation checking** (editing a module to break it and confirming a test fails): run with
  `PYTHONDONTWRITEBYTECODE=1`, `python -B` and `-p no:cacheprovider`, clear `__pycache__` first, and
  run the whole `tests/` directory. A same-length edit restored within a second keeps running stale
  bytecode otherwise. Back the file up and restore it in a `finally`. The harness is not in the repo; rebuild it from this
  description.
- `caplog.at_level(level, logger="name")` resets that logger's level, which cancels a module's
  `DEBUG` override; to test the override, set the level on the root logger only.
- The test of the committed `config/pom.toml` checks a copy, so a developer's `pom.local.toml` cannot
  change it. Tests that touch the real library derive their expectations from the data (no
  hardcoded shelf counts).
- `holdings.csv` no longer has a `weight` column; circulation lives only in `library/passages/circulation.json`.
- `.gitignore` has a `samples/` rule to keep recordings out; `!library/samples/` exempts the benchmark sentences. A new
  folder named `samples` anywhere else is ignored.
- whisper.cpp 1.9.5's server **crashes** on `verbose_json` with voice detection on when the audio holds no speech; ask
  for SRT subtitles to get timestamps.
- llama.cpp reuses none of a changed prompt for Qwen3.5 (hybrid recurrent layers); `cached_tokens` is 0. Not worth fighting.
- On the Pi, `pgrep -f NAME` run through `ssh` matches its own command line; use `pgrep -x` or look for the process
  another way. Detach long runs on the Pi (`setsid nohup ... < /dev/null`) so a dropped connection does not kill them.
- The shell tool here sometimes drops the output or a variable of a compound command (`cd X && VAR=... && cmd`, or a
  heredoc after a `cd`): write a script file and run it with absolute paths. In zsh, `echo` interprets backslashes in text
  read back from files; use Python to print raw values.
