# Printer of Mysteries: Technical Design Notes

Rewritten 2026-10-27 from the first design notes, to match what has been built, measured and decided since.
Where this file and the code disagree, trust the code and fix this file. Day-to-day handoff and working rules are in
`project-state.md`; the words used here are defined in `lexicon.md`.

**Status tags.** *Built*: in `src/pom/` with tests. *Experiment*: works in `experiments/`, not yet rebuilt in
`src/pom/`. *Proposed*: a value or design suggested, not decided. *Open*: undecided.

## Concept

- A haunted terminal: an IBM Wheelwriter typewriter that eavesdrops on a room and every once in a while types a
  portent.
- Bidirectional: people can type to it, and it types back. Typed input is treated like overheard speech, with a small
  thumb on the scale toward a response.
- Its portents are tangential and lyrical, never direct answers. Whether you overhear it or speak to it, it stays in
  character.
- Fully local at run time, no cloud. The spirit is local.
- Lore: a printer that has printed a million pages over its lifetime is imbued with the residue of what it was
  perpetually obligated to say. Something said in the room snags on that residue, and it answers from it.
- Its in-world home is the Office of Ordinary Hauntings site (officeofordinaryhauntings.net, case study CS-0027). The
  site's "clean room" and wireless ban are theatre: the Pi keeps its radios. The honest line for a door notice is
  "Speech in the room is transcribed and discarded. Nothing is kept."
- **Nothing is kept.** Heard speech lives in memory only and is dropped as it ages; logs hold counts and scores,
  never words.

## Hardware

- Typewriter: IBM Wheelwriter 1000 (Type 6781-024), converted to a printing terminal with the open-source CadetWriter
  interface board (Teensy 3.5, donated by Dave Babcock).
- Computer: Raspberry Pi 5, 16 GB (`pom1`), 128 GB microSD, 27 W power supply, Active Cooler. Pi OS Legacy 64-bit Lite
  (Debian 12 Bookworm). Trixie was tried and avoided: cloud-init 25.2 crashed on first boot and never applied Wi-Fi.
  Wi-Fi falls back to a Comitup hotspot (`pom1-direct`). Details: `pi-setup-notes.md`.
- Connection: the Pi talks to the typewriter over USB serial. The interface board presents a normal port, so the
  typewriter stays swappable.
- Output speed: about 16 characters per second, so a portent takes 5 to 20 seconds to type. Typing is not the slow
  step: the oracle model is. On the Pi the 9B takes 35 to 40 seconds to write a typical portent (see The Pi, below).

## Paper

- Green-bar continuous fanfold, chosen for authenticity and easy handling.
- Friction feed. Drift is accepted and corrected by hand now and then; the folds are natural checkpoints.
- Feeds from a box behind the machine and refolds into a catch box below it.

## Microphone

- Audio-Technica ATR4697-USB: an omnidirectional boundary mic, **wired** (USB-C), plugged straight into the Pi. No
  audio HAT.
- It must sit on its own surface, not the typewriter stand, because a boundary mic hears the surface it is mounted on.
- Capture gain: **24 of 30** with the hardware auto-gain off (tested 2026-10-23: normal speech averaged -33 to -27 dB,
  a yell peaked near -3 dB at gain 26, so 24 leaves about 5.7 dB of headroom). The gain resets on every reboot, so
  startup must set it from config (a planned startup step). Level normalization per utterance, before whisper, is the
  part that adapts to the room.
- (open) Placement and cable routing, pending the idle-noise test with the Wheelwriter. Wireless if the cable becomes a
  problem.

## The pipeline, end to end

```
microphone -> whisper-driven cutting -> whisper -> sort: words / captions / nothing
  -> SegmentFragmenter -> Scorer -> Trigger -> hinge -> PromptBuilder -> oracle model -> Output -> typewriter
```

| Stage | Status |
|---|---|
| Microphone, cutting, whisper, the three-way sort | Experiment (`experiments/hearing-debug/`) |
| SegmentFragmenter | Built |
| Scorer (fragment_filter, scorer model, circuit breaker) | Built |
| Trigger (`ConvoInterestTracker`) | Built; used by the hearing-debug probe, not yet by a Conductor |
| Hinge (the sentence that triggered) | Decided; nothing to build but the hand-off |
| PromptBuilder, `OracleModelClient` | Built |
| Output (formatter, serial, pause listening) | Planned |
| Input (keyboard) | Planned |
| Conductor | Planned |

### Half-duplex

The machine does one thing at a time. While the oracle model writes (and while the typewriter prints), nothing is sent
to whisper or the scorer model. They stay loaded in memory, idle, so there is no reload cost, and the Printer does not
hear itself. Wes does not want lockfiles (they can lock the system out). (open) The enforcement is a Conductor state
(listening, generating, printing, recovering) with a watchdog timeout so a stuck state cannot deafen the Printer for
good. The memory for all three models at once was measured on the Mac at about 10 GB (the 9B oracle model 6.7 GB,
the 4B scorer model 3.1 GB, whisper 0.33 GB). On the Pi, with all three loaded and idle, at least 6.7 GB of the 16 GB
stayed available.

## Listening: from sound to text

1. **Microphone** (*experiment*): listens all the time and hands audio on in small frames.
2. **Cutting** (*experiment*): whisper decides where the speech is. Every 5 seconds a whisper run covers a rolling
   buffer with whisper's own Silero voice detection on (threshold 0.5); the segments whisper has finished (held back
   2 seconds so a sentence is not cut mid-thought) are kept, and the buffer never grows past 30 seconds. This replaced
   cutting by loudness (a slowly moving noise floor and a 0.7 s pause), which never found a pause in a room with bursty
   noise; the loudness cutter survives behind `--energy-cutter` for comparison. Time is counted in audio received, so
   a recording replays exactly as a live stream would, at any speed.
3. **Transcriber** (*experiment*, whisper.cpp v1.9.5 as `whisper-server`): turns the audio into punctuated text with
   segment times. Model `base.en`; `small.en` is slower (about 0.5 times real time against 0.2) for almost the same
   text on speech.
4. **The three-way sort** (*experiment*). Whisper's output is sorted before anything is scored:
   - *Words* go on: speech, and sung lyrics with the music symbol stripped. The Printer reacts to what it hears, not
     only to conversation, so lyrics compete on score like any other sentence.
   - *Ambient captions* such as "(dog barks)" or "[Music]" are never scored (a hallucinated "(gunshot)" scored 8.0).
     They are kept as a short list of recent sounds, to give the oracle model as context (*proposed*).
   - *Nothing-here markers* such as "[BLANK_AUDIO]" are dropped.
5. **SegmentFragmenter** (*built*): cuts the words into fragments stamped with a time. Sentences are what get scored;
   clauses and phrases remain available behind a setting (*proposed*: a config setting for which levels it emits).

**The known-good hearing setup.** The only combination that worked well in the room, and it is fragile: on `pom1`,
`./probe_hearing_run.sh --no-explain` from `experiments/hearing-debug`, whisper `base.en`, 4 threads, VAD threshold 0.5,
mic gain 24, the 4B scorer model on port 8098, the committed config. Change one thing at a time from here.

## Scoring: how interesting is a fragment?

**The Scorer** (*built*, `src/pom/scoring/`) gives each sentence a score from 0 to 9. It replaced the first plan
(a weighted sum of word lists). Its pieces:

- **fragment_filter**: the word tagger (NLTK) labels each word as a name, number, filler, stopword or content word. A
  fragment with too few content words ("or not.") skips the model and gets a fixed low score (`fragment_filter_score`,
  1.0), which saves a model call on talk that cannot be interesting.
- **Scorer model**: a small local language model, Qwen3-4B (Q4_K_M), served by `llama-server`. Llama-3.2-3B is the
  faster alternative. The prompt is Wes's, kept in config; the definition of "interesting" is kept separate from the
  meaning of the numbers, and there is no persona.
- **One sentence per request, no context.** A sentence scores the same whatever came before it. Batching and showing
  earlier lines were both measured and rejected: each made a score depend on its neighbours and their order.
- **The score** is the average of the digits the model might answer with, each weighted by how likely the model thinks
  it is: 75% sure of 6 and 25% sure of 7 scores 6.25.
- **Unscored**: if the model does not answer, the fragment has no score (None), and the trigger leaves it out. A
  circuit breaker stops asking after `failure_limit` (3) failures and tries again after `retry_seconds` (30). The
  Printer stays quiet and keeps listening. (A word-counting fallback is shelved, unused, in `fallback_scorer.py`.)
- **Warm-up.** The first request after the server starts takes 12 to 28 seconds on the Pi, so startup warms it first.

**What the scores look like** (4,278 sentences from four recorded meetings): they are lumpy, not bell-shaped. About 27%
sit at 1 (the fragment_filter's 497 plus the model's own), with humps near 3 and near 5, and almost nothing above 7
(0.9% reach 7; the 99th percentile is 7.0). There is no long tail of outliers, which is why a fixed high threshold
was abandoned and why the textbook outlier rule fails here (see Trigger).

**On the Pi** (`experiments/pi-benchmark/`): with whisper running flat out on shared cores, the 4B scorer model scores a
sentence in a median 2.3 s (95th percentile 3.2 s), uses about 3 GB, peaks at 72 °C with no throttling, and slows
whisper about 2.3 times, which is still real time.

**Retired:** the first Scorer was a weighted sum of word lists (corpus snag by `wordfreq`, WordNet concreteness,
NRC-VAD feeling, names/numbers/questions, filler and recent-use penalties). The model proved the better judge. The
word list design remains a documented fallback in `project-state.md`; the NRC-VAD file is not redistributable and is
downloaded per machine.

## The Trigger: when does the Printer speak?

**Built**: `ConvoInterestTracker` (`src/pom/scoring/convo_interest_tracker.py`), settings in `[trigger]`.

The rule is Tukey's fence, the standard test for a value that stands out from a set of values, applied to the room's
recent scores:

- **The window**: the scores heard in the last `window_seconds` (300, five minutes), or the last `min_scores` (20)
  if the room is so quiet that fewer were heard in that time; whichever is larger. It is a length of time, not a count,
  so a busy room and a quiet one both get "the last five minutes". Every score joins it, the lowest included. There is
  no floor.
- **The fence**: Q3 + `fence_multiple` (0.9) x IQR of the window, where Q1 and Q3 are the scores a quarter and
  three quarters of the way up, and IQR = Q3 - Q1.
- **The trigger**: a score triggers when it is strictly above the fence as it stood before that score joined, the window
  holds at least `min_scores` scores, and at least `cooldown_seconds` (60) have passed since the last trigger. A score
  above the fence inside the cooldown is held back (it still joins the window and does not restart the cooldown).
- **Time is passed in**: `record(score, now)`. Live it is a clock reading; in a replay it is the place in the
  recording, so a recording gives the same answers faster than real time. Unscored fragments are ignored.

**Why this rule.** The first design was a threshold that moved with the room: 25% above a decaying average of the
recent scores, plus a floor below which scores were ignored. Replayed on four recorded meetings (307 minutes,
about 835 sentences an hour) it fired on 34% of sentences, one every 13 seconds, and turning `trigger_fraction` up
barely helped (35% gave 30%, 50% gave 23%). An average sits in the middle of the scores, so a good share are always
above a bar set just over it, and the many scores near 1 pull the average, and so the bar, down. The textbook fence
(1.5) fired about once an hour because the scores have no outliers. A multiple of 0.9 on a five-minute window gives
the pace we want. The cooldown exists because one meeting had six triggers in eight seconds (a run of one-line
jokes, each clearing a bar that five minutes of scores barely moves).

**Result with the shipped settings** (4 recorded meetings): 29 triggers in 5.1 hours, **5.7 an hour, one every 10.6
minutes**, between 3.4 and 7.4 an hour by meeting; the shortest gap is 62 s and the longest 29 minutes. With the talk
thinned to a fifth, about 4 an hour. The goal is about 6 an hour.

**Principles behind it.** Absolute scoring is the enemy of emergence: thresholds should come from what is happening in
the room, not from a constant. The filters must work reasonably well in every room, not perfectly in one; nothing is
tuned per room.

**Not built, and not decided:**
- (proposed) *Paper guards*, so nobody comes back to a pile of paper: nothing triggers unless speech was heard in the
  last 10 minutes (this also keeps whisper's phantom phrases from printing in an empty room, and matters because the
  window can reach back past hours of silence), and hard ceilings of about 12 prints an hour and 60 a day as a last
  resort. The activity measure for the quiet timeout would be seconds of accepted speech per minute from the
  Transcriber, not loudness.
- (open) The retired design had a target print rate that followed room activity (0 silent, about 4 with a little
  talking, about 8 busy) and a rate governor. The fence plus the cooldown replaced them; the rate is now a result to
  watch, not a quantity steered. If a real room shows the rate drifting, a governor can come back.
- (open) How typed input joins: a small score bonus (as first planned) or a lower bar.
- (open) Everything above is tuned on four chatty meetings. A real room, especially a quiet one, is the real test.

## The hinge

- The hinge is the fragment the portent answers: a word, a phrase or a topic (in practice a sentence).
- Its connection to the portent is tangential and lyrical, not a direct answer and not random. Examples: talk about
  dogs gets an insult in the manner of Shakespeare calling someone a dog; something called weird gets something in
  the spirit of Blake on the uncanny; complaining about friends gets something about gossip.
- The leap is made by the oracle model, not looked up. A corpus lookup was rejected as too literal.
- **The hinge is the sentence that triggered** (Wes, 2026-10-27). The scorer and the trigger (the fence with its
  cooldown) were built to hand over about six sentences an hour, so the one that clears the fence is used as it
  occurs. If that is a little inconsistent, scaring some hinges and catching on others, that is what talking to an
  oracle means. The first design's weighted random pick among the best recent candidates is dropped. The hand-off
  (the triggering fragment to the PromptBuilder) is the Conductor's job.
- (open) *Defining "interesting".* Wes's conclusion (2026-10-22): the prompt and the trigger keep circling because we
  have not defined for ourselves what is worth answering, and we let the algorithm choose hoping it would work out. A
  human definition comes first; the scorer prompt then states it. Deferred, not dropped.

## The oracle model

**Built**: `PromptBuilder` and `OracleModelClient`; the model is served by `llama-server` (default port 8099).

- **The prompt is Wes's.** It lives in config (`oracle.prompt`), with `{exemplars}` and `{hinge}` marking where those
  go. It asks for a new passage in the manner of the style example without printing the theme or the example, under
  150 words. Claude measures prompts; Wes writes them.
- **Exemplars** (register exemplars): each time the Printer speaks, the ExemplarSelector draws chunks from the
  weighted corpus, independently and never matched to the hinge. They show the voice and nothing more. The bake-off and
  the config use **one** exemplar (`exemplars_min`/`exemplars_max` both 1); the range can be 1 to 5 or more.
- **Settings** (`[oracle]`): temperature 0.3, `max_tokens` 300 (a runaway guard; the prompt asks for under 150 words),
  timeout 120 s.
- **Output length** follows the exemplar, usually 15 to 50 words and occasionally up to 150, with variation as a
  feature.

### The bake-off (2026-10-26)

Seven 8 to 9B models, one frozen prompt, 16 hinges x 10 seeded draws = 160 portents each, the same hinge and exemplar
for every model, rated blind on seven yes/no marks (follows the hinge, follows the style, doesn't repeat the hinge,
didn't muddle its details, no extra text, length close, particularly creative). Mean score of 7:

| Model | Score |
|---|---|
| Qwen3.5-9B | 6.08 |
| Gemma 2 9B | 5.84 |
| Hermes 3 | 5.26 |
| Ministral 8B | 5.21 |
| Llama 3.1 8B Instruct | 5.19 |
| Stheno v3.4 | 4.73 |
| Qwen3-8B | 4.45 |

Qwen3.5-9B and Gemma 2 both beat Llama 3.1 clearly; Qwen3.5 against Gemma is not settled. No model was particularly
creative (the best managed it in half of its portents). Write-up: `model-bakeoff.md`; rubric: `bakeoff-rubric.md`.
**Decided 2026-10-27: the Printer uses Qwen3.5-9B, with Gemma 2 9B as the named alternate (one config change away),
unless a problem shows.** Gemma was more faithful to the hinge (it repeated the hinge 2 times in 160 against 10, and
muddled fewer details) but less creative (68 portents against 81), weaker on style, and should be 20 to 30% slower on
the Pi (an estimate, not measured). Caveats: one rater (Claude); Wes's spot-check of about 20 ratings is pending.

### The Pi (measured 2026-10-27)

The 9B is the biggest model in the system and the Pi's memory is much slower than the Mac's (the 9B writes 36 times slower on it). On the Mac the 9B
writes a portent in a median 0.8 to 0.9 s at about 68 tokens a second. `experiments/pi-oracle-benchmark/` measured the
same on the Pi, 12 portents a phase from an emptied page cache, with whisper and the 4B loaded and idle, and with the
9B alone:

- A portent takes a median 38 s (35 s alone) and up to about 100 s for a long one; the longest of 12 was 97 s, and a
  portent near the 150-word ceiling would take about 2 minutes. Typing it adds 5 to 20 s.
- The first word comes after a median 20 s: the Pi reads the prompt at about 10.5 tokens a second (0.095 s a token).
  The server reused none of the prompt between portents, though its start is the same each time; why is not known.
- Writing runs at about 1.9 to 2.0 tokens a second (0.5 s a token), slower than the typewriter types, so a portent is
  finished before typing starts.
- Cold start: 50 to 78 s for the 9B (read from the card) and 57 s for the 4B, once, at boot. A restart with the file
  in memory takes 13.5 s.
- The idle neighbours cost about 3 s a portent at most, within what 12 portents can resolve. Nothing throttled; the
  hottest the Pi got was 73.6 °C.
- Wes judged the wait acceptable (2026-10-27): the first word after about 20 s, a typical portent finished in 35 to
  40 s and a long one in up to about 100 s, then 5 to 20 s of typing. (open) Why the server caches none of the prompt;
  fixing it could cut the 20 s wait.

### Two voices (Track B not started)

The first design had two voices behind one interface: Track A, a stock instruct model with a prompt, and Track B, a
base model fine-tuned (LoRA, on the Mac with MLX, never on the Pi) on the corpus plus curated hinge pairs, run with an
almost empty prompt so that it cannot speak outside its registers. The bake-off is Track A: stock models, Wes's prompt.
Track B and the curated hinge pairs (an overheard line paired with a tangential in-register reply, curated by Wes,
Neil and River with a large model drafting candidates, logged in the AI-use ledger) have not been started. (open)
Whether to start them depends on how far the prompted oracle model gets.

## Corpus

- 81 sources in 14 registers, about 72,000 chunks of 15 to 150 words (the 14th register, `nonsense`, was added to test
  how hard growing the corpus is: easy). Public on GitHub.
- `corpus/ready/` holds clean utterances, one per paragraph, with no trace of their source. `corpus/chunks/` turns them
  into JSONL chunks. Everything is built by scripts from the manifest `corpus/sources.csv`, so it is repeatable.
- Registers are *what the oracle might say*, not where text came from. Draws are weighted: every utterance is in one
  pool and has its own chance, a register with few utterances gets a heavier weight per utterance, and the register
  shares start equal. Nothing is chosen category-first, so any combination can come up, including five obituaries in a
  row; it is just rare. `corpus/chunks/weights.json` is the one hand-edited file for shares and source weights (for
  example Shakespeare's plays 0.2). Draw odds are computed at load time, never stored.
- Rights: public domain, plus `owner-cleared` material (the IBM/Lexmark Wheelwriter manuals and the dad jokes), decided
  by Wes. Wes has said licensing and content are not concerns for this project: it is an art installation, far from
  the consumer market.
- (open) Equal register shares make small registers repeat. A simulation showed repeats are negligible over an evening
  (0.1%) and matter over months; a recency decay was prototyped and deliberately left out.
- Adding material: `adding-material.md`. How the standardizer works and what went wrong: `corpus-notes.md`.

## Output

- (planned) A formatter for the typewriter's character set (printable ASCII) and line width, and a serial port wrapper
  that paces the text. It tells the Conductor when printing starts and ends, so listening pauses (half-duplex).
- The output validation hook (`scripts/dev/corpus/validate_output.py`) is a deliberate stub. Checking for verbatim
  overlap with the corpus was judged the wrong measure: false positives on quotes that land, false negatives on
  near-copies, blind to echoing the hinge.
- (open) A "silence" tic across portents: the rubric rated portents one at a time, so a habit that repeats across them
  would not show up.

## Software stack

- **Speech to text**: whisper.cpp v1.9.5 (`whisper-server`, Silero VAD), model `base.en`, built on the Pi from a pinned
  tag by a setup script.
- **Language models**: `llama.cpp` (`llama-server`), one server per model: the scorer model (Qwen3-4B Q4_K_M, port
  8098) and the oracle model (Qwen3.5-9B Q4_K_M, port 8099). Whisper's server is on port 8110.
- **Control code**: Python 3.11 (Homebrew on the Mac, Bookworm's on the Pi). The Conductor will use `asyncio`: each
  worker an async task with a queue between them, leaf modules plain synchronous code, anything that blocks awaited or
  run in a thread.
- **Config**: `config/pom.toml` (committed, the quiet defaults the Pi runs with), over built-in defaults, under the
  gitignored `config/pom.local.toml` and then environment variables. A bad value means the default plus a warning.
  Everything a person might tune (prompts, thresholds, counts) is in config.
- **Training** (if Track B happens): a rented GPU or the Mac. The Pi only runs models.
- **Deployment**: Mac to Pi by `git push` and `git pull`, never by copying files. Updating the Pi is a pull and a
  restart script (planned: it would pull fast-forward only, refuse on a dirty tree, check the new config before
  touching the running service, then show the startup "ready" line).

## Software architecture

All runtime code is object-oriented: small classes with one concern each, methods that each do one thing, plain data
objects passed between them. Helper scripts (`scripts/`) import `pom`; `pom` never imports them. About 1,000 tests pass
(1,047 when this was written). Logic that needs no hardware is kept apart from code that touches the mic, a model or
the serial port, so most of it is tested on the Mac.

### Rules that cut across modules

- **The Printer never stops because of a typo or a recoverable fault** (a gallery cannot crash). A module recovers
  and records the problem in a `FaultRecorder` (WARNING = recovered, CRITICAL = cannot do its job; even then nothing
  raises). `strict=True` raises, for tests and dev tools only.
- Modules do not read `Config`. The entry point builds one `Config` and passes values in.
- Logs hold counts and scores, never words. One `debug` flag turns debug output on everywhere; each module can override
  it. Logging goes to stdout (journald collects it).
- (open) *Failing loudly.* If the scorer model or the oracle model is down the Printer can never respond, and a log
  line is not loud when someone is switching a printer on. Decide what loud means (a beep, an email to Wes, something
  at the venue) and make those startup steps critical in that way. Today the scorer step is not critical and an
  unscorable fragment is left unscored.

### Modules

```
src/pom/
  fault_recorder.py, log_setup.py, config.py     built
  startup.py                                     built runner; the ten startup duties are placeholders
  corpus/      draw_weights, chunk_store, exemplar_selector      built
  listening/   segment_fragmenter                                built
               (audio source, transcriber, rolling cutter)       experiment, to be rebuilt here
  scoring/     word_tagger, scorer_model_client, scorer,         built
               convo_interest_tracker (window, fence, cooldown)  built
               fallback_scorer                                   shelved, unused
  prompt/      prompt_builder                                    built
  voice/       oracle_model_client                               built
  output/      formatter, serial wrapper, reply selection        planned
  input/       keyboard                                          planned
  conductor                                                      planned; the only module that knows the whole pipeline
```

### Startup duties (placeholders in `startup.py`)

Config report; data check (corpus, weights, NLTK data; critical); microphone gain; audio device; transcriber;
scorer server; scorer warm-up; oracle model server (critical); typewriter port (critical); one-line readiness summary
(counts and seconds only).

## What we have measured

| Experiment | Folder | What it settled |
|---|---|---|
| Interest scorer | `interest-scorer/` | One sentence, no context, digit-probability score; Qwen3-4B; a bigger model (8B) scores gentle-weird lines higher |
| Pi benchmark | `pi-benchmark/` | The 4B scorer model next to flat-out whisper on the Pi: median 2.3 s a sentence |
| Hearing debug | `hearing-debug/` | Whisper-driven cutting, the three-way sort, the known-good setup, the trigger replays |
| Oracle playground | `oracle-playground/` | Hand trials of oracle models and prompts |
| Oracle bake-off | `oracle-bakeoff/` | Seven models, 160 rated portents each; Qwen3.5-9B first |
| Pi oracle benchmark | `pi-oracle-benchmark/` | The 9B on the Pi with whisper and the 4B idle: a typical portent in 35 to 40 s, the first word after 20 s |

The trigger replays used four recorded team meetings (public by design, kept in gitignored `data/`).

## Build order

1. (Done 2026-10-27) The oracle model is Qwen3.5-9B; the wait on the Pi (about 40 s typical, 100 s long) is acceptable.
   Look into why nothing is cached when convenient.
2. (Decided 2026-10-27) The hinge is the sentence that triggered. The hand-off is the Conductor's.
3. Rebuild the listening side in `src/pom/` (audio source, rolling cutter, transcriber with the three-way sort).
4. Output side: formatter, serial wrapper, reply selection.
5. The Conductor, with the half-duplex state and its watchdog, then the entry point and the startup duties.
6. Paper guards; "failing loudly".
7. Run it on the Pi; test in a real room (a quiet one too) and retune from that.
8. Later: Input (typed text), hinge pairs, Track B if wanted.

## Open questions, collected

- What is "interesting", defined by us before the model is asked (Wes).
- Why the server caches none of the prompt (fixing it could cut the 20 s wait); a prompt tuned for the top two oracle
  models; Wes's spot-check of the bake-off ratings. (Decided: Qwen3.5-9B, with Gemma 2 9B as the alternate.)
- How the trigger behaves in a real, quiet room; the paper guards.
- How the Conductor enforces half-duplex without lockfiles.
- How typed input joins the trigger.
- Mic placement and cable routing.
- What "failing loudly" means.
- Whether Track B and the hinge pairs are worth building.
- Real whispers: a very quiet aside in a very quiet room is treated today like any quiet sound (the voice detector may
  not call it speech). Revisit once the rest works.

## What changed from the first notes

- **Scorer**: word lists -> a small local language model (Qwen3-4B), one sentence per request, no context, a 0 to 9
  probability-weighted score.
- **Trigger**: a pool of faded scores against a moving threshold with a rate governor, activity curve and no cooldown
  -> Tukey's fence over a five-minute window with a 60 s cooldown and no floor.
- **Hearing**: cutting by loudness with a moving noise floor -> whisper's own voice detection on a rolling buffer; the
  three-way sort of whisper's output (words, ambient captions, nothing).
- **Voice**: two tracks behind an interface -> the oracle model, a prompted stock model with Wes's own prompt (Track B
  not started); Qwen3.5-9B leads the bake-off.
- **Corpus**: about 69,000 utterances in 13 registers -> about 72,000 chunks in 14 registers, chunks of 15 to 150 words.
- **Whisper**: `small.en` -> `base.en` as the model that worked in the room.
- **Half-duplex**: deaf while printing -> nothing is sent to whisper or the scorer model while the oracle model writes
  or the typewriter prints; both stay loaded.
- **Hinge**: a weighted random pick among the best recent candidates, with a temperature -> the sentence that triggered.
- **Retired**: the activity tracker and target-rate curve, the faded-score pool, the hinge picker, the Mimic splicing
  fallback, the word-list Scorer (kept as a documented fallback), and the license questions about wordfreq (no longer a dependency of
  the Scorer).
