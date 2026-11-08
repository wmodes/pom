# Project state: read this first after a break

A handoff for whoever picks the work up next (a person, or Claude after its context was compacted).
Written 2026-10-21, rewritten 2026-10-27, brought up to date 2026-11-04. It says where things stand, the rules we work by, and what comes next.
For detail it points at the other docs instead of repeating them: **the design is in `Design Document.md`**, the words
are in `Lexicon.md`, the Pi is in `Pi Setup Notes.md`. If this file and the code disagree, trust the code and fix this file.

## What this is

The **Printer of Mysteries**: an IBM Wheelwriter 1000 typewriter, driven by a Raspberry Pi 5 (16 GB,
hostname `pom1`), that eavesdrops on a room and every so often types a tangential, lyrical
pronouncement (a *portent*). People can also type to it. Fully local: no cloud at run time. Its in-world home is
the Office of Ordinary Hauntings site (officeofordinaryhauntings.net, case study CS-0027).

## Where things stand (2026-11-04)

- **Built and tested** (3,396 tests, all passing): everything in the module map below except the start-up duties. In short: the shared plumbing (`fault_recorder`, `log_setup`, `config` with
  `[scorer]`, `[qualifying]`, `[listening]`, `[oracle]`, `[monitor]`, `[printing]`, `[terminal]` and `[conductor]` sections); the library
  (`Circulation`, `Catalog`, `Bibliomancer`); the whole listening side; the Scorer and its warm-up; the `BarKeeper` and its three bars; the `PromptBuilder` and the oracle client; the
  `PortentFormatter` and the `TerminalPort`; the typed-line path; the Conductor (floor, turns, watchdogs); the entry point (`python -m pom`); and the monitoring site.
  The boot slip (the Pi's address, typed when the typewriter comes online) is built. The `startup` runner is built, but its ten duties are still placeholders and `printer.py` does not call it.
- **Decided:**
  - **Scorer model:** Qwen3-4B, one sentence per request, no context, a 0 to 9 score. Its timeout is 30 s (the first score after a server starts took 15.7 s on the Pi).
  - **Qualifying:** `algorithm = "decaying_quantile"` (EWMQ): the bar is the 99.5th percentile of all recent scores, each counting for less the older it is (half-life 300 s),
    starting at 6.8 until the room has said enough. 60 s cooldown, no floor. `qualifier_bar_influence` (2026-11-06, replacing the on/off `exclude_qualifiers`): a qualifier or a score held by the cooldown goes into the bar as the bar plus that much of the way up to the score
    (1 is the score itself, 0 the bar itself; the held-score half was fixed 2026-11-04). The anchor (2026-11-07): a steady tick under the room (`anchor_score`, `anchor_interval_minutes`) lets silence lower the bar toward a fixed level instead of freezing at the last crowd's high; measured, the ticks release a crowd's high smoothly (a tick a minute: below 7 after 31 to 68 minutes, from a crowd of three scores to three hundred). The bar's memory limit is now a setting, `discard_scores_older_than_minutes`. `percentile` and `moving_average` stay selectable. The goal is about six qualifiers an hour. Replays gave about 7 an hour, but a
    later measurement gave 13.5 (a busy room) and 11.1 (a quiet one), taken before the held-score fix; **re-measure it.** Tukey's fence was tried first and replaced
    (2026-10-29): it sat above the window's best score in two windows out of three.
  - **Hinge:** the sentence that qualified (Wes, 2026-10-27). No separate hinge picker.
  - **Oracle model:** Qwen3.5-9B, with Gemma 2 9B as the named alternate. On the Pi a typical portent takes 35 to 40 s
    (first word after 20 s, up to about 100 s for a long one); Wes judged that acceptable. Temperature 0.8, at most 300 tokens, a 240 s timeout.
  - **Half-duplex:** nothing is sent to whisper or the scorer model while the oracle model writes or the typewriter prints.
  - **All the models stay loaded (Wes's decision, restated 2026-11-03):** whisper, the scorer and the oracle are resident in memory at the same time, always (the 16 GB Pi is why). Half-duplex is about CPU, not memory. Never propose loading on demand or shrinking contexts for memory. Servers run as separate systemd services, and `pom.service` uses `Wants=`, not `Requires=`; see the design doc, "All the models stay loaded, always".
- **Experiments** (`experiments/`, not part of the Printer until rebuilt in `src/pom/`): `hearing-debug` (the working
  listening prototype), `interest-scorer`, `pi-benchmark`, `oracle-playground`, `oracle-bakeoff` (seven models, 160 rated
  portents each), `pi-oracle-benchmark` (the 9B on the Pi, run 2026-10-27).
- **Library:** 81 holdings, 14 shelves, about 71,000 passages, public on GitHub. No passage ends unfinished (`audit.py` reads 0).
- **Git:** branch `main`, remote `origin` (github.com/wmodes/pom, public). Run `git status -sb` to see what is
  unpushed. The Pi's checkout (`~/pom`) is brought up to date with `scripts/setup/update-pi.sh` (it holds the 4B and 9B model files in `data/models/`).
- **Testbenches** (`scripts/dev/`, small tools that use the real modules; the list, with what each needs, is `scripts/dev/README.md`; read it before diagnosing anything):
  `listening/` (cut speech, split whisper's tracks, `listen_to_room.py` on the microphone or a recording), `scoring/` (type sentences or typed lines and see the scores and the bar's ruling),
  `writing/write_portent_from_hinge.py` (type a hinge, get a portent), `printing/` (format text for the typewriter, type it paced on the screen or a real port), `monitor/serve_fake_turns.py`
  (the monitoring site with invented turns), `library/`, and `pipeline/`: `listening_to_portent.py` (the whole pipeline but the typewriter; `--mac-mic` on a Mac) and
  `run_printer_with_servers.py` (starts the three servers, runs `python -m pom` with your options, and stops what it started).
- **The Conductor and the entry point are built** (2026-11-02): `python -m pom` runs the whole Printer (see the design doc, "Conducting: running a turn"),
  burn-tested on the Mac with real servers.
- **The Printer runs as services on the Pi** (2026-11-03): `scripts/setup/systemd/` linked into place by `install-services.sh`, persistent journal, models loaded with mmap and not pinned
  (about 7 GB left free), crash recovery tried. A reboot test passed (all four services up by themselves, mic gain still 24). It has listened to a live room through the
  microphone and written portents, with no typewriter attached. Wi-Fi power saving is off (a NetworkManager drop-in linked by `install-services.sh`; it was already off, so this is insurance).
  The Mac sometimes cannot resolve `pom1.local` while the Pis and a phone can; the diagnosis so far is in `Pi Setup Notes.md`, "When `pom1.local` will not resolve" (its cause is not proven).
- **Not built yet:** the typewriter on the Pi (the real CadetWriter, including how it signals "on hold" and "out of paper"), the monitoring site's per-turn outcome and a richer printer status line (on hold, out of paper; they need a preview and Wes's say; the terminal's up / DOWN / none light is built),
  the paper guards, "failing loudly", and the start-up duties (`startup.py`'s steps are still placeholders; the scorer warm-up is the one already done, by `ScorerWarmer`). Track B (the fine-tuned oracle)
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
  (for example `BarKeeper(c.percentile, c.window_seconds, c.min_scores, c.cooldown_seconds)`).
  **No hardcoded, user-inaccessible values in code; they belong in the config** (a standing rule, set by Wes 2026-11-07). Every number, threshold, count,
  age or size limit, duration, interval, window, message or name that can change what the Printer does, or that a person might want to see or change, lives in `config/pom.toml` with a comment saying what
  it does and what changes if it goes up or down. Never a module constant, a default argument or a magic number that only a code edit can change; a default in code is only the fallback for a missing or bad config value. A hidden value is a hidden
  design decision, so anything that changes behavior is proposed and approved first. The case that made the rule: `MEMORY_HALF_LIVES = 8`, which reset the bar after 40 minutes of quiet, invisibly.
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
  startup.py           built   the startup runner; its ten duties are placeholders in planned_steps(), and printer.py does not call it yet
  printer.py           built   the entry point (python -m pom): builds every part from one Config and runs the Conductor; --mac-mic, --wav, --terminal
  library/
    circulation.py     built   circulation.json: shelf shares, holding weights, defaults, drift checks
    catalog.py         built   reads library/passages/*.jsonl into immutable Passage objects
    bibliomancer.py    built   weighted random draws; passages per pull set by [oracle]; no repeats in a pull; redraws an unfinished pick
    endings.py         built   the one definition of an unfinished ending (ends_unfinished, ends_sentence)
  listening/
    take_cutter.py     built   a take's words -> sentence / clause / phrase clips, all levels
    audio_deck, mic_deck, wav_deck, mac_mic_deck built the microphone (arecord, read in a thread), a recording replay, and the Mac's microphone through ffmpeg
    whisper_model_client built audio to whisper-server from memory; text or timed segments back
    track_splitter     built   whisper's text -> words / room tone / blanks
    rolling_cutter     built   whisper's own voice detection cuts the stream, every 5 s
    eavesdropper       built   joins them: sound in, takes out (a generator the Conductor runs in a thread)
    audio_format       built   16 kHz mono 16-bit, 30 ms frames: the numbers they share
  scoring/
    word_tagger.py     built   text -> words with lemma, part of speech, name/number/filler flags (NLTK)
    scorer_model_client.py built one clip to the scorer model, one score (or an explanation) back
    clip_scorer.py     built   clip_filter, scorer model, circuit breaker; unscored if no answer
    scorer_warmer.py   built   a background thread: waits for the scorer's server, sends one throwaway request so the first real score is fast
    fallback_scorer.py shelved word-counting stand-in for the scorer model; unused
  qualifying/
    bar_keeper.py      built   the cooldown and the ruling: whether an attempt qualifies
    bar.py, percentile_bar.py, decaying_quantile_bar.py, moving_average_bar.py  built  where the bar sits, three ways; config `algorithm` picks one
    bar_keeper_factory.py built builds the BarKeeper the config asks for
    attempt.py         built   one score trying to qualify, and the ruling it got
  conductor/
    conductor.py       built   runs the whole Printer: offers attempts, takes the floor for one turn, listens again; restarts what dies
    floor_keeper.py    built   who may speak, the room or the Printer (half-duplex), and the watchdog on each state
    turn_taker.py      built   one hinge through a turn: the oracle writes (asked again if it fails), the terminal prints, the portent must still be fresh
    hinge_finder.py    built   listening to hinge: sound in, qualifying sentences out
    boot_slipper.py    built   a background thread: waits for the typewriter, takes the floor and types the boot slip (the Pi's address)
    address_finder.py  built   the machine's usable IPv4 addresses (psutil), for the boot slip
    keyboard_watcher.py built  lines typed at the terminal in, attempts out (the typed twin of the HingeFinder)
  keyboard/
    typed_line_scorer.py built a typed line scored (multiplied by `typed_lines_score_multiplier`) and judged by the same bar
  monitor/
    monitor_site.py    built   the password-protected web page: the last hour of turns, with a score chart
    turn_history.py, chart_notebook.py, score_chart.py built the turns kept in memory, the chart notebook (numbers only, one entry per attempt), the chart drawn from it
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

The reasoning and the numbers are in `Design Document.md`. In short:

- **The pipeline:** whisper (cutting the audio itself) -> the track split (words / room tone / nothing) ->
  TakeCutter -> Scorer -> BarKeeper -> the qualifying sentence is the hinge -> PromptBuilder -> oracle
  model -> Printing. Typed input has its own way in: the TypedLineScorer scores the line, multiplies it by `typed_lines_score_multiplier` (1.5), and the same bar judges it (no cooldown; below the bar it is ignored; it goes into the bar by the same rule, by `typed_lines_bar_influence`).
- **Unfinished exemplars never reach the oracle** (2026-11-03). The oracle copies its exemplar's ending, so a library passage that stops on a colon or at "...the" produced cut-off portents. `pom.library.endings.ends_unfinished` is the one definition; the library no longer keeps such passages (`normalize.py`, `cataloguer.py`) and the Bibliomancer redraws if one ever turns up (`[oracle] exemplar_redraws`). `audit.py`'s `unfinished` column must read 0. Details in the design doc, "The Library: the form that influences the Oracle".
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
  (`documentation/AI Ledger.md`, which counts remote AI use only, never local models).
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

- Adding material, start to finish: `documentation/Adding Material.md`.
- Rules, columns, flags: `library/README.md`. How the standardizer works and what went wrong:
  `documentation/Library Notes.md`.
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

The Pi (`ssh pi@pom1.local`) is only reachable on the home network. See `documentation/Pi Setup Notes.md` for its setup,
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
  lockfiles (they can lock the system out); built as the FloorKeeper's states with a watchdog on each (2026-11-02).
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
  after a server starts takes 12 to 28 s (it evaluates the whole scoring prompt; later calls reuse the cached start of it), so the Printer's
  `ScorerWarmer` sends it one throwaway request once the server is up. Whisper output on a quiet room needs a filter for
  non-speech tags and phantom repeats.
- **The microphone** (ATR4697-USB on the Pi, 2026-10-23): records quietly; capture gain **24 of 30** with hardware
  auto-gain off (a yell at gain 26 peaked near -3 dB). It survived a clean reboot on the Pi (ALSA restores the mixer state, 2026-11-03),
  but a start-up check that sets and verifies it (a config setting) is still to be built.
- **The Printer reacts to what it hears, not only conversation** (Wes, 2026-10-23): the Transcriber strips the music
  symbol and keeps lyric words, which are scored. **Whisper's bracketed room tone** such as "(dog barks)" are never
  scored (a hallucinated "(gunshot)" scored 8.0); they are kept as a short list of recent sounds to give the oracle model
  as context. Nothing-here markers ("[BLANK_AUDIO]") are dropped.
- **Absolute scoring is the enemy of emergence** (Wes, 2026-10-23): thresholds and scales come from what is happening in
  the room, not from constants. Related: the filters must work reasonably well in every room, not perfectly in one; nothing
  is tuned per room.
- **Qualifying** (2026-10-27). *Superseded:* a fixed 7.0 threshold, then 25% above a decaying average with a floor (it fired on
  34% of sentences in recorded meetings and barely responded to its setting). Then Tukey's fence (2026-10-27 to 29; above the real top of the scores). Then a plain percentile of
  the last five minutes (about 9.8 qualifiers an hour on five recorded meetings; kept as `algorithm = "percentile"`).
  *Current* (2026-10-30): a decaying quantile (EWMQ), so a zinger fades by degrees instead of holding the bar until it falls off the edge of a window; a cooldown, no floor;
  qualifiers and held scores kept out of the bar (see the design doc, "Qualifying: when does the Printer speak?", and `[qualifying]` in `config/pom.toml`).
- **Oracle model** (2026-10-27): Qwen3.5-9B, Gemma 2 9B the alternate. The bake-off is in `Model Bakeoff.md`; the Pi run
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

## The bar and the monitor: named problems (2026-11-07)

Wes's names, in the order they came up. What is built and what is open:

- **Problem 1: time influence on the bar. Solved.** Each score's weight decays continuously with its age (`half_life_seconds`), so time is already in the bar calculation with no cliffs. The one hard edge was a hidden 40-minute memory rule
  (`MEMORY_HALF_LIVES = 8`, never approved); it is now `discard_scores_older_than_minutes` in the config.
- **Problem 2: the bar updates with no attempts. Solved, on the bar and on the page (2026-11-07).** Silence has no scores, so by itself it could not lower the bar. The *bar* settles in silence (the anchor: `anchor_score` every `anchor_interval_minutes`, see the Design Document), and now the *monitor* shows it:
  the status tiles are read live on every poll at the stream clock (`status_now()` in `printer.py`), the chart's right edge is now and not the last attempt, the ticks are drawn as grey dots, the bar line slides down through a silence (drawn from the bar as it stood at each tick), and a silence longer than `[chart] longest_silence_minutes` is drawn short, as an engineering break with its length written in it. Also on the page: `sentence` is `attempt` throughout, the time labels are one note ("Intervals at approximately 5 min") and the poll beat is `[monitor] poll_seconds`.
  The chart notebook (2026-11-08) now holds the ticks too, written when the next attempt arrives, and the break is 10 minutes (`[chart] longest_silence_minutes`), so a 9-minute quiet stretch keeps its ticks and the bar's slide. It keeps an hour back from its newest entry, so after a very long silence the chart still shows the last hour before it, with its dots.
  (The bar's clock used to start at the first attempt, so in a silent room its ticks had not started; it now starts with the stream, `started_at`: fixed 2026-11-07.) Wes parked the fixes for the rest.
- **Problem 3 (the number is provisional: Wes did not number it): the listening side's restart clock. Fixed, 2026-11-07.** A listening side rebuilt after a failure began its stream clock at 0 while the BarKeeper held time at the latest it had seen, so the cooldown could not end for about as long as the old clock had run
  (two hours in: at most one qualifier in two hours). A new Eavesdropper now carries the old clock on, plus the real seconds since sound last arrived (`Eavesdropper.stream_time_at`).

Open around them: the unmoved hard-coded values (next step 6). The chart now draws the ticks (Wes: "we may end up having them charted"), so that is no longer open.

## Next steps, in order

1. **The typewriter.** Connect the CadetWriter to the Pi and switch `[terminal] mode` on the Pi from `none` to `serial`. Test on the real machine what has so far been tested on fakes:
   paced typing, flow control, CR+LF, keyboard lines back with echoplex, and how it signals "on hold" and "out of paper" (the Conductor waits for the printer and drops a portent that
   is no longer fresh, but how the machine reports those states is unknown). The boot slip types on the first connection: check it there, and that its address is right.
2. **Start-up duties.** `printer.py` does not call `startup.py` yet, and its ten duties are placeholders. First the mic-gain check (set and verify it from config), then the readiness checks
   for whisper, the servers and the typewriter. (The scorer warm-up is already done by `ScorerWarmer`; confirm on the Pi that a real clip now scores in about 1 s after it.)
3. **Re-measure the qualifying rate with the new dial** (`qualifier_bar_influence`, 2026-11-06). The EWMQ rates on record (13.5 an hour busy, 11.1 quiet) were taken with qualifiers left out and before the held-score fix, against a goal of about six. First look done 2026-11-06 on the first hour of the live room (`scripts/dev/qualifying/replay_scores_through_bar.py`): the rate falls smoothly with the dial, but that lively room still gave 10 an hour at 0.5 and above, so reaching about six is a percentile and cooldown question. First live look, the same evening on the Pi (about 2 h 15 min after the restart, read off the monitor): after a quiet stretch the first qualifiers cleared a bar of about 5 and the bar climbed with each, to about 6.5 over ten minutes, with no jump to the qualifier's level; 7 turns in the last hour, against the goal of about six. Wes judged it as it should be: more qualifies while the bar recovers from a quiet stretch. One stretch only. The test still to do is the one on the Pi: leave it in an empty room for a long while and talk to it afterward. Replay the recordings with the
   testbenches (`pipeline/listening_to_portent.py`, `listening/listen_to_room.py`), then try a real room, a quiet one too, and retune from that. The `[qualifying]` settings were tuned on chatty meetings.
4. **The monitoring site's per-turn outcome** and a richer printer status line (on hold, out of paper). They need a preview and Wes's say before they are built. (The terminal's light, up / DOWN / none, is built, 2026-11-07, and a quiet start is drawn as a silence. The Printer's own screen, a kiosk on a monitor plugged into the Pi, is built the same day: `pom-display.service`, `[monitor] password_on_localhost`, `[display]`; see the Design Document and "The display (kiosk)" in Pi Setup Notes.)
5. **Paper guards** (nothing qualifies unless speech was heard in the last 10 minutes; hard ceilings of about 12 an hour
   and 60 a day; proposed values) and **"failing loudly"** (Wes, 2026-10-24): if the scorer model or the oracle model is
   down, the Printer can never respond, and a log line is not loud when someone is switching a printer on. Decide what loud
   means (a beep, an email to Wes, something at the venue).
6. **Move the hard-coded values into config** (the rule above, 2026-11-07). Done: `MEMORY_HALF_LIVES` is `discard_scores_older_than_minutes`; and, on Wes's list of 2026-11-07, `starting_bar_weight`, `max_clip_words`, the scorer warm-up (`warmup_poll_seconds`, `warmup_attempts`), `service_check_seconds`, `max_typed_line_characters`, `refused_character_retry_seconds`, `wait_log_reminder_seconds` and the whole `[chart]` section. Wes said to leave the rest for now. Still hard-coded, from the same audit of `src/pom` (the modules' constants were all read; inline literals were checked in the behavior modules, so there may be more):
   - *Affects what the Printer does:* the clip filter's filler-word list (`WordTagger.DEFAULT_FILLERS`); the fallback scorer's whole heuristic (`fallback_scorer.py`: base, 0.35 per content word, 0.8 per name, 0.4 per number, 0.4 question bonus, a ceiling of 5, with counts); the scorer request's `temperature 0`, `top_logprobs 20`, `max_tokens 1`; the turn taker's `WRITE_FAILURES_THAT_ALARM = 3` and `DEFAULT_WRITE_ATTEMPT_SECONDS = 240` (a copy of `[oracle] timeout`, so they can drift apart); the `+ 1.0` second added to `check_seconds` for the boot slip's ears-shut wait; the whisper client's `PING_TIMEOUT = 5`; log rotation (`log_setup.py`: 1,000,000 bytes, 3 backups).
   - *Monitor, not on the list:* the turn history's `DEFAULT_MAX_RECORDS = 500` and the page's `SNIPPET_CHARS = 90`. (`POLL_SECONDS` is moved: it is `[monitor] poll_seconds`; the chart notebook's size limit is moved: it is `[monitor] chart_notebook_max_entries`.)
   - *Internal safety timeouts and structure, to decide:* the BarKeeper's and FloorKeeper's `LOCK_TIMEOUT_SECONDS = 5`, `JOIN_SECONDS = 5`, the turn taker's `BEAT_SECONDS = 5`, `PRINT_TRIES_BETWEEN_LOG_LINES = 6`, the keyboard watcher's polls, the terminal port's 0.1 s read slice; `MINIMUM_MIN_SCORES = 2`; the audio format (16 kHz, 16-bit, 30 ms frames, which whisper fixes); the 0 to 9 score scale, which is repeated in the moving-average bar and the scorer's prompt (and now has its own `[chart] score_max`).
   - *Not built yet, must be config when it is:* the microphone gain the start-up runner is to set (24 of 30 on the ATR4697-USB is only in a description string so far).
   (The monitor's tile and chart were live-only-on-an-attempt; fixed 2026-11-07, see Problem 2 above.)

7. Later: the curated hinge pairs, Track B if wanted.
8. Open and small:
   - Wes's spot-check of about 20 bake-off ratings; a prompt tuned for the top two oracle models; defining "interesting" ourselves before asking the model (Wes, 2026-10-22, deferred);
     `ratings/report.md` still says Stheno 4.72 (it is 4.73); a "silence" tic that repeats across portents would not show up in the one-at-a-time ratings.
   - `Pi Setup Notes.md` lacks the latest on the Mac's failed `.local` lookups (a looping network extension in the Cisco VPN client is the strong, unproven lead; Cisco was removed 2026-11-04 and the Mac may need a restart) and says
     Wi-Fi power saving was already off.
   - The cataloguer's `--oversize 1.5` question: keep a passage of up to 225 words whole instead of dropping the few over 150.

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
- The servers' models are loaded with `-lm mmap` and not pinned: on the Pi's ARM build `mmap+mlock` pinned the memory twice and left 0.9 GB available with both models loaded. Resident is not the same as pinned.
  This llama.cpp build also renamed `--mlock`; `scripts/setup/check-service-flags.sh` checks every flag in the units against the installed server's `--help` before an install changes anything.
- On the Pi, `pgrep -f NAME` run through `ssh` matches its own command line; use `pgrep -x` or look for the process
  another way. Detach long runs on the Pi (`setsid nohup ... < /dev/null`) so a dropped connection does not kill them.
- The shell tool here sometimes drops the output or a variable of a compound command (`cd X && VAR=... && cmd`, or a
  heredoc after a `cd`): write a script file and run it with absolute paths. In zsh, `echo` interprets backslashes in text
  read back from files; use Python to print raw values.
