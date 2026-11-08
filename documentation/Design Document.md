# Printer of Mysteries: Design Document

This document explains what was chosen in designing the Printer of Mysteries, and why. It is organized by part of the system, so a reader can go straight to the part they care about. Revised 2026-11-04 to match what is built, measured and decided.

Where this file and the code disagree, trust the code and fix this file. Day-to-day handoff, the working rules and what to do next are in [Project State](Project%20State.md); the words used here are defined in [Lexicon](Lexicon.md). The other documents worth knowing are listed in [Appendix B](#appendix-b-companion-documents).

Status tags used throughout: *Built* (in `src/pom/`, with tests), *Experiment* (works in `experiments/`, not rebuilt in `src/pom/`), *Proposed* (suggested, not decided), *Open* (undecided).

## Concept and principles

### What it is

The Printer of Mysteries is a haunted terminal: an IBM Wheelwriter typewriter that eavesdrops on a room and, every once in a while, types a portent. Most of what it hears it lets pass. Now and then a sentence stands out from the room's own chatter, and the typewriter answers. The answer is never direct. A portent is tangential and lyrical, a reply in the spirit of what was said rather than to it, and the Printer stays in character whether it overheard the remark or was spoken to.

It runs in both directions. People can type to it at the typewriter's own keyboard, and it types back; a typed line is treated like overheard speech, with a thumb on the scale toward a response, because someone who takes the trouble to type is talking to it. And it is fully local at run time: the speech recognition and both language models live on the machine, with no cloud. The spirit is local.

### Lore and theatre

A printer that has printed a million pages over its lifetime is imbued with the residue of what it was perpetually obligated to say. Something said in the room snags on that residue, and the printer answers from it. Its in-world home is the Office of Ordinary Hauntings site ([officeofordinaryhauntings.net](https://officeofordinaryhauntings.net), case study CS-0027). The site's "clean room" and wireless ban are theatre: the Pi keeps its radios. The honest line for a door notice is "Speech in the room is transcribed and discarded. Nothing is kept."

### Principles

These cut across every part of the design. Each is here so it is not reopened by accident.

#### Nothing is kept

Heard speech lives in memory only and is dropped as it ages. Logs and reports hold counts, labels and scores, never words, and that includes what was typed and what the oracle wrote. The one exception is the recorded team meetings used to test the qualifying rule: public by design, and kept only in gitignored `data/`.

#### Nothing is absolute

Absolute scoring is the enemy of emergence. Thresholds and scales come from what is happening in the room, not from constants. The filters must work reasonably well in every room, not perfectly in one, and nothing is tuned per room.

#### The Printer fails toward speaking

The Printer never stops because of a typo or a recoverable fault: a gallery cannot crash. A bug may cost a missed turn; it must never hold the floor for good or leave the Printer deaf. Every limit errs long, since a wrong cut-off costs a wrong answer and a too-long wait only costs a quiet spell. Printing has priority over listening: the Printer listens only to find something to print.

#### The prompts are human-written

The scorer prompt and the oracle prompt are written by hand and kept in config. Models are used to measure prompts, not to write them.

#### One thing at a time (half-duplex)

The machine does one thing at a time. While the oracle model writes and while the typewriter prints, nothing is sent to whisper or the scorer model, so they never compete for the CPU and the Printer does not hear itself. No lockfiles (they can lock the system out): the enforcement is the FloorKeeper's four states with a watchdog (see [Conducting: running a turn](#conducting-running-a-turn)).

#### All the models stay loaded, always

Decision (restated 2026-11-03 because it was forgotten): whisper, the scorer model and the oracle model are all resident in memory at the same time for as long as the Printer runs. That is why the Pi is the 16 GB one. Half-duplex is about compute: it says nothing about unloading, and nothing is ever unloaded to make room. Consequences:

- No model is loaded on demand, swapped, or started only for its turn. A reload costs 12 to 28 s for the scorer and more for the oracle, and the first word after a hinge must not wait for it.
- Context sizes, model sizes and quantisations are chosen to fit together in 16 GB with room to spare. They are not traded against memory pressure; if the sum ever stops fitting, that is a design question, not a setting to quietly lower.
- The servers outlive the Printer: restarting `python -m pom` leaves the models warm.

## System overview

### The pipeline

```
microphone
    ↓
whisper-driven cutting
    ↓
whisper
    ↓
track split: words / room tone / blanks
    ↓
TakeCutter
    ↓
Scorer  <------------ keyboard (typed line)
    ↓
Qualifying
    ↓  the hinge: the sentence that qualified
PromptBuilder  <----- library (exemplars)
    ↓
oracle model
    ↓
Printing
    ↓
typewriter
```

### Where each stage stands

| Stage | Status |
|---|---|
| Listening: microphone, audio decks, cutting, whisper client, track split, TakeCutter, Eavesdropper | Built (`src/pom/listening/`); matched against the experiment on a recorded meeting; has heard a live room on the Pi |
| Scoring: clip filter, scorer model client, circuit breaker, warm-up | Built (`src/pom/scoring/`) |
| Qualifying: `BarKeeper` and its bars | Built (`src/pom/qualifying/`) |
| Hinge: the sentence that qualified | Decided; the hand-off is the Conductor's |
| Writing: `PromptBuilder`, `OracleModelClient`, `PortentWriter` | Built (`src/pom/writing/`) |
| Keyboard: `TypedLineScorer`, `KeyboardWatcher` | Built |
| Printing: `PortentFormatter`, `TerminalPort` | Built; tried on stand-ins, not yet on the real CadetWriter |
| Conducting: `FloorKeeper`, `TurnTaker`, `Conductor`, entry point (`python -m pom`) | Built; burn-tested on the Mac, running as services on the Pi |
| Monitoring site | Built, with a terminal light (up, DOWN, none) and a kiosk on the Pi's own monitor; a richer printer status line (on hold, out of paper) is not |
| Start-up duties | Runner built; the duties are placeholders (the scorer warm-up is done elsewhere) |

### How it runs

The Conductor runs the Printer on four ordinary threads: the room (listening to hinge), the keyboard (only with a real typewriter), the turn (one hinge at a time, from writing to paper) and the thread that called `run()`, which keeps watch every second. Leaf modules are plain synchronous code. The details are under [Conducting: running a turn](#conducting-running-a-turn).

## Hardware

### The typewriter

IBM Wheelwriter 1000 (Type 6781-024), converted to a printing terminal with the open-source CadetWriter interface board (Teensy 3.5, donated by Dave Babcock). The Pi talks to it over USB serial; the board presents a normal port, so the typewriter stays swappable. It types about 16 characters a second, so a portent takes 5 to 20 seconds to type. Typing is not the slow step: the oracle model is.

### The computer

Raspberry Pi 5, 16 GB (`pom1`), 128 GB microSD, 27 W power supply, Active Cooler. Pi OS Legacy 64-bit Lite (Debian 12 Bookworm). Trixie was tried and avoided: cloud-init 25.2 crashed on first boot and never applied Wi-Fi. Wi-Fi falls back to a Comitup hotspot (`pom1-direct`). Details: [Pi Setup Notes](Pi%20Setup%20Notes.md).

### Paper

Green-bar continuous fanfold, chosen for authenticity and easy handling. Friction feed: drift is accepted and corrected by hand now and then, and the folds are natural checkpoints. It feeds from a box behind the machine and refolds into a catch box below it.

### The microphone

- Audio-Technica ATR4697-USB: an omnidirectional boundary mic, wired (USB-C), plugged straight into the Pi. No audio HAT.
- It must sit on its own surface, not the typewriter stand, because a boundary mic hears the surface it is mounted on.
- Capture gain 24 of 30 with the hardware auto-gain off (tested 2026-10-23: normal speech averaged -33 to -27 dB, and a yell peaked near -3 dB at gain 26, so 24 leaves about 5.7 dB of headroom). The gain survived a clean reboot (observed 2026-11-03: ALSA saves and restores the mixer state). A start-up step to check it and set it from config is planned. Level normalization per utterance, before whisper, is the part that adapts to the room.
- (open) Placement and cable routing, pending the idle-noise test with the Wheelwriter. Wireless if the cable becomes a problem.

## Platform and deployment

### Software stack

- Speech to text: whisper.cpp v1.9.5 (`whisper-server`, Silero voice detection), model `base.en`, built on the Pi from a pinned tag by a setup script. Port 8110.
- Language models: `llama.cpp` (`llama-server`), one server per model: the scorer model (Qwen3-4B Q4_K_M, port 8098) and the oracle model (Qwen3.5-9B Q4_K_M, port 8099).
- Control code: Python 3.11 (Homebrew on the Mac, Bookworm's on the Pi), threads and plain blocking calls. The Conductor was first planned around `asyncio` (2026-10-21) and was built on threads, which keep each leaf module simple and testable.
- Training, if Track B happens: a rented GPU or the Mac. The Pi only runs models.

### Configuration

[config/pom.toml](../config/pom.toml) (committed: the quiet defaults the Pi runs with) over built-in defaults, under the gitignored `config/pom.local.toml` (per-machine settings and secrets such as the monitoring password), under environment variables. A bad value means the default plus a warning, never a failure. Everything a person might tune (prompts, thresholds, counts, times) is in config, so the serviceable parts are in one place. Every key a local file sets must also appear in `pom.toml`, so reading it shows everything that can be set.

### The servers are services

Built and installed on the Pi 2026-11-03 ([Pi Setup Notes](Pi%20Setup%20Notes.md)). systemd runs each server as its own unit: `pom-whisper.service`, `pom-scorer.service` and `pom-oracle.service`, each `Restart=always`, each logging to journald (which keeps the log across reboots, capped in size). `pom.service` runs the Printer after them with `After=` and `Wants=`, never `Requires=`: a server that crashes or restarts must not take the Printer down, and the Printer already copes with a missing server (it says so on the monitoring page, keeps going, and recovers when the server answers).

The unit files live in the repo ([scripts/setup/systemd/](../scripts/setup/systemd/)) and are linked into place, not copied, so a `git pull` rewrites the services; [scripts/setup/update-pi.sh](../scripts/setup/update-pi.sh) pulls, refreshes packages, relinks, and restarts only what changed. On the Mac, [scripts/dev/pipeline/run_printer_with_servers.py](../scripts/dev/pipeline/run_printer_with_servers.py) starts the same three servers, runs the Printer on them, and stops what it started.

### Memory

The three models at once were measured on the Mac at about 10 GB (the 9B oracle 6.7 GB, the 4B scorer 3.1 GB, whisper 0.33 GB). On the Pi the llama-servers load with `-lm mmap -lzm off` (loaded and fully resident) and are not pinned with mlock: with all three loaded, plain mmap leaves about 7 GB of the 16 GB available, while `mmap+mlock` pinned the memory twice (this ARM build keeps a repacked copy of the weights) and left 0.9 GB.

### Delivery

Mac to Pi by `git push` and `git pull`, never by copying files. The Pi is the client: nobody edits or deletes on it by hand. [update-pi.sh](../scripts/setup/update-pi.sh) is fast-forward only and leaves the models loaded.

## The pipeline, stage by stage

### Listening: from sound to text

#### The stages

1. Microphone (*built*, `MicDeck`): listens all the time through `arecord` and hands audio on in 30 ms frames. It reads in a thread of its own into a 10-second buffer, so a slow whisper run cannot lose sound; if the buffer fills, the oldest frames are dropped and counted. A recording can be played instead (`WavDeck`), for tests and replays; `MacMicDeck` is for laptop testing.
2. Cutting (*built*, `RollingCutter`): whisper decides where the speech is. Every 5 seconds a whisper run covers a rolling buffer with whisper's own Silero voice detection on (threshold 0.5); the segments whisper has finished (held back 2 seconds so a sentence is not cut mid-thought) are kept, and the buffer never grows past 30 seconds. This replaced cutting by loudness (a slowly moving noise floor and a 0.7 s pause), which never found a pause in a room with bursty noise. Time is counted in audio received, so a recording replays exactly as a live stream would, at any speed.
3. Transcriber (*built*, `WhisperModelClient`): turns the audio into punctuated text with segment times. Model `base.en`; `small.en` is slower (about 0.5 times real time against 0.2) for almost the same text on speech.
4. The track split (*built*, `TrackSplitter`): whisper's output is sorted before anything is scored.
   - Words go on: speech, and sung lyrics with the music symbol stripped. The Printer reacts to what it hears, not only to conversation, so lyrics compete on score like any other sentence.
   - Room tone such as "(dog barks)" or "[Music]" is not scored by default (a hallucinated "(gunshot)" scored 8.0; `score_room_tone` turns it on). It is kept as a short list of recent sounds, to give the oracle as context (*proposed*).
   - Blanks such as "[BLANK_AUDIO]" are dropped.
5. TakeCutter (*built*): cuts the words into clips stamped with a time. Sentences are what get scored; clauses and phrases remain available behind a setting.
6. Eavesdropper (*built*): joins the microphone, the cutter and the track splitter. `hear()` is a plain generator the Conductor runs in a thread; it yields a `Take` (the new words and their kind, the new room tone, and where in the stream it ended) for each segment that holds something new, skipping repeats and remembering the last six bits of room tone. After three rounds in a row with no speech it asks whisper once, without the voice detector, for lyrics and room tone. Whisper being down is one warning for each outage; the audio is kept and tried again.

#### Known limit

The room-tone pass hears about two seconds, not ten: a stretch with no speech is trimmed to its last two seconds first, so lyrics sung between passes are mostly missed. Fixing it means the Eavesdropper keeping its own recent audio. Inherited from the experiment.

#### The known-good hearing setup

The only combination that worked well in the room, and it is fragile: on `pom1`, `./probe_hearing_run.sh --no-explain` from [experiments/hearing-debug](../experiments/hearing-debug), whisper `base.en`, 4 threads, VAD threshold 0.5, mic gain 24, the 4B scorer on port 8098, the committed config. Change one thing at a time from here. The `[listening]` section of the config holds these values, and a test pins the committed file to them.

### Scoring: how interesting is a clip?

The Scorer (*built*, `src/pom/scoring/`) gives each sentence a score from 0 to 9. It replaced the first plan, a weighted sum of word lists, because the model proved the better judge.

#### The parts

- Clip filter: the word tagger (NLTK) labels each word as a name, number, filler, stopword or content word. A clip with too few content words ("or not.") skips the model and gets a fixed low score (`clip_filter_score`, 1.0), which saves a model call on talk that cannot be interesting.
- Scorer model: a small local language model, Qwen3-4B (Q4_K_M), served by `llama-server`. Llama-3.2-3B is the faster alternative. The prompt is human-written and kept in config; the definition of "interesting" is kept separate from the meaning of the numbers, and there is no persona.
- One sentence per request, no context. A sentence scores the same whatever came before it. Batching and showing earlier lines were both measured and rejected: each made a score depend on its neighbours and their order.
- The score is the average of the digits the model might answer with, each weighted by how likely the model thinks it is: 75% sure of 6 and 25% sure of 7 scores 6.25.
- Unscored: if the model does not answer, the clip has no score (None), and Qualifying leaves it out. A circuit breaker stops asking after `failure_limit` (3) failures and tries again after `retry_seconds` (30). The Printer stays quiet and keeps listening. A word-counting fallback is shelved, unused, in `fallback_scorer.py`.

#### Warm-up

The first request after the server starts evaluates the whole scoring prompt (300 tokens, 15.7 s measured on the Pi); llama-server then reuses the cached start of the prompt, so every later request costs only its sentence (about 1 s). The `ScorerWarmer`, a background thread the Printer starts, waits until the scorer's server answers, sends it one throwaway request, and ends, so no real sentence pays for the first. It is a thread and not a start-up step because the Printer starts whatever state the servers are in. The scorer's timeout (30 s) covers the first clip after a server is restarted later, since the cache is lost then. Nothing in the Printer scores two clips at once, so one cached slot is enough.

#### What the scores look like

4,278 sentences from four recorded meetings: the scores are lumpy, not bell-shaped. About 27% sit at 1 (the clip filter's 497 plus the model's own), with humps near 3 and near 5, and almost nothing above 7 (0.9% reach 7; the 99th percentile is 7.0). There is no long tail of outliers, which is why a fixed high threshold was abandoned and why the textbook outlier rule fails here (see Qualifying).

#### Speed on the Pi

([experiments/pi-benchmark/](../experiments/pi-benchmark/)) With whisper running flat out on shared cores, the 4B scores a sentence in a median 2.3 s (95th percentile 3.2 s), uses about 3 GB, peaks at 72 °C with no throttling, and slows whisper about 2.3 times, which is still real time.

#### Retired

The first Scorer was a weighted sum of word lists (corpus snag by `wordfreq`, WordNet concreteness, NRC-VAD feeling, names/numbers/questions, filler and recent-use penalties). It remains a documented fallback in [Project State](Project%20State.md); the NRC-VAD file is not redistributable and is downloaded per machine.

### Qualifying: when does the Printer speak?

*Built*: `BarKeeper` (`src/pom/qualifying/`), settings in `[qualifying]`. The question every time: given the score of a new clip and the scores of the room lately, is this one worth answering?

#### How it works

- The bar is the score an attempt must be above to qualify, worked out from the room's own recent scores. A score qualifies when it is strictly above the bar as it stood before that score joined, and at least `cooldown_seconds` (60) have passed since the last qualifier.
- A score above the bar inside the cooldown is held back: it does not restart the cooldown, and it goes to the bar as the qualifier it would have been.
- Every score joins what the bar is built from, the lowest included. There is no floor. A score that stood above the bar (a qualifier, or one held by the cooldown) joins as the bar plus `qualifier_bar_influence` of the way up to the score, so the zinger that fired neither becomes the room's best and holds the bar up (an influence of 1) nor adds nothing, so that the bar can never follow the room up (an influence of 0). [The qualifier's influence](#the-qualifiers-influence-on-the-bar) says how.
- Every bar starts at `starting_bar` (6.8, about the usual top of the scores) and slides to the room's own level as it speaks, so there is no silent warm-up.
- Time is passed in: `record(score, now)`. Live it is a clock reading; in a replay it is the place in the recording, so a recording gives the same answers faster than real time. Unscored clips are ignored.
- The BarKeeper keeps what every bar shares (the decision, the cooldown, the clock, a lock for the room's and the keyboard's threads); a `Bar` works out where the bar sits, and a small factory builds the one `algorithm` names.

#### The bars

- `decaying_quantile` (EWMQ, the shipped default): the bar is the `bar_percentile` (99.5) of all recent scores, each counting for half as much every `half_life_seconds` (300). The quantile is read by linear interpolation, so the percentile is a smooth dial even with few scores. The starting line counts as `starting_bar_weight` scores' worth of weight and fades like a real score, so after a long silence the bar returns to it. A zinger fades by degrees instead of holding the bar until it falls off the edge of a window.
- `percentile`: the bar is the `bar_percentile` of the scores heard in the last `window_seconds` (300), or the last `min_scores` (20) if the room is so quiet that fewer were heard; whichever is larger. Nearest rank, so the bar is always a score the room really earned. At 100 it is the best score of the window.
- `moving_average`: the first method, restored so it can still be chosen: the bar is a fraction above a decaying average of the scores, with a fixed floor under which scores are ignored.

#### The qualifier's influence on the bar

*Decided and built 2026-11-06.* One dial, `qualifier_bar_influence`, says how much a score that stood above the bar influences the bar. It replaced an on/off setting (`exclude_qualifiers`, on) whose polarity was backwards and whose two states were both wrong in one room or another.

- **The one rule.** A score that stood above the bar goes into what the bar is built from not as itself but as `bar + influence × (score - bar)`, where `bar` is the level it had to beat. An influence of 1 goes in as the score itself, 0 as the bar itself, 0.5 half way between. Nothing else is special-cased: at 0 the score goes in at the bar's own level, which adds a point where the bar already is.
- **Why not off.** With qualifiers left out entirely the bar cannot follow the room up. After about 30 hours of silence on the Pi the bar had settled near 3, the level of the room's own noise; when two people then talked, nearly every sentence cleared it, and since each qualifier was left out the bar stayed at 3. Taking qualifiers in whole is the opposite failure: the bar rides up to the room's best score and almost nothing beats it.
- **Why not a weight.** The first version gave a qualifier a fraction of an ordinary score's weight in the quantile. With a window of a dozen scores the 99.5th percentile is the top score, so any weight that was not tiny put the bar at the qualifier, and the dial did nothing between 0 and 1 except at a busy room's scale. Changing the value, not the weight, makes the dial do what its name says at every traffic level.
- **The dial is the BarKeeper's, not each bar's.** The BarKeeper changes the value on its way in and hands it to the bar, so all three bars (`decaying_quantile`, `percentile`, `moving_average`) get it and none needs to know about it.
- **It is not clamped.** The setting is any number, because an experiment may want 1.1 (the value overshoots the score) or -0.1 (it falls below the bar).
- **Measured** (2026-11-06, [replay_scores_through_bar.py](../scripts/dev/qualifying/replay_scores_through_bar.py) on the 940 scores of the first hour of the live room, 2026-10-26; one room, so a first look). The qualifiers an hour and the bar's mean level, by influence: 17 an hour and 6.67 at 0, 13 and 6.82 at 0.25, 10 and 6.87 at 0.5, 10 and 6.90 at 1. Starting the same hour 30 hours after the Printer started, so the starting line had faded as it had on the Pi, an influence of 0 still slid the bar to about 4.9 (45 an hour qualified, 138 more held); 0.25 gave 20 an hour with the bar near 6.6 to 6.9, and 0.5 gave 12 an hour near 6.8. This lively room allows about 10 an hour at the most, against a goal of about six, which the percentile and the cooldown are the levers for.
- **Typed lines get the same rule.** `typed_lines_bar_influence` is the same dial for a typed line: every typed line, above the bar or below it, goes in as the bar plus that much of the way up to its (multiplied) score, and the qualifier's influence is not applied on top. It replaced the on/off `typed_lines_affect_bar`. `typed_handicap` was renamed `typed_lines_score_multiplier`, because it is a multiplier on the line's score and the three settings now read alike.
- **The starting line fades from the stream's start.** It fades from `started_at`, the stream time at which listening begins (the Printer gives the bar that start; it is not the first attempt), and does not return after a silence. A reasonable starting score stands in for a long period of listening with no chance of a portent; and since the anchor's ticks run from the same start, a quiet room's bar is already sliding toward the anchor before anyone speaks, so the first sentence after a long quiet faces the settled bar and not the starting one.

#### The anchor: how silence lowers the bar

*Decided and built 2026-11-07.* Silence has no scores, so by itself it cannot lower the bar: every weight fades by the same factor, their shares do not change, and the bar freezes at the last crowd's high. So a steady background runs under the room the whole time, like a click track.

- **Ticks.** One every `anchor_interval_minutes`, on a fixed grid from `started_at`, the stream's start. A tick is a stand-in score of `anchor_score` that sits in the bar's memory like an attempt and fades on the same half-life. While people talk the real scores bury the ticks; when the room goes quiet nothing buries them, and the bar settles toward the anchor. There is no silence detection.
- **Ticks never qualify, never start a cooldown and are never counted as attempts.** They live inside the `DecayingQuantileBar`, so the BarKeeper never sees them, and `window_size_at` leaves them out. They are not stored: a tick is a pure function of the clock (the grid, each weighted by its age, dropped at the same memory horizon as a score), which is the same as having added each one when its time came, but needs no ticker and gives the same answer in a replay.
- **`anchor_score` is a fixed number, not a percentile of the room.** It is the pooled 90th percentile of the scores of the four Zoom meetings and the live room of 2026-10-26 (scored 2026-10-27 to 10-29). The rooms clustered, so one number serves. `scripts/dev/qualifying/report_score_percentiles.py` redoes the figure after any change to the scorer's prompt or model.
- **Measured** (2026-11-07, `replay_scores_through_bar.py` on the first hour of the live room): ticks barely touch a room that is talking. Qualifiers an hour stayed at 10, and the mean bar moved from 6.89 without ticks to 6.88 at one minute and 6.87 at a quarter of a minute. With the first score 30 hours after the start it stayed at 12 an hour, and the first score faced 5.85 where it faced 6.8 before.
- **What silence does to the bar, and when.** A bar at a high percentile follows the best recent score, so a crowd's high is held until its top scores have faded to about half a percent of the total weight, and only then do the ticks bring the bar down, smoothly. Measured (2026-11-07, a crowd of 9s, an anchor of 5, the bar's first fall below 7): with a tick a minute, 31 minutes for a crowd of three scores, 43 for fifteen, 54 for sixty and 68 for three hundred; with a tick every quarter minute, 21, 34, 44 and 58. A busier crowd takes longer, because more weight has to fade; a shorter interval brings it forward.
- **The discard age is a setting, `discard_scores_older_than_minutes`.** A score, or a tick, older than this is dropped from the bar's memory. It was a hidden constant of 8 half-lives (40 minutes at the shipped half-life), never approved: with it, a crowd of fifteen or more held its high until the 40 minutes ran out and then dropped to the anchor in one step. Now the ticks release a crowd well inside any sensible discard age, so the discard age mostly bounds memory and decides what the bar is after a very long quiet: the ticks alone, or the starting bar if no tick has come. A weight that has faded below what a float can hold is dropped too, so a very long discard age can never divide by zero.

#### Why this rule

The first design was a threshold that moved with the room: 25% above a decaying average of recent scores, plus a floor. Replayed on four recorded meetings (307 minutes, about 835 sentences an hour) it fired on 34% of sentences, and turning the setting up barely helped. An average sits in the middle of the scores, so a good share are always above a bar set just over it. The second design was Tukey's fence (Q3 plus a multiple of the interquartile range). It does not suit these scores: they are lumpy, so the spread between the lumps stretches the interquartile range and the bar lands above the real top. At a multiple of 0.8 the bar was above the best score of its own window in two windows out of three, and it reached 9.85 on a scale that ends at 9 in a live call.

A percentile measures where the top of the window actually is instead of predicting it from the middle. But with a short window it is really "beat the best score of the window": one lucky high score holds the bar up, and a strong qualifier is followed by a long quiet stretch. The decaying quantile with qualifiers left out (how it was first shipped, before `qualifier_bar_influence`) keeps the percentile's honesty and fixes that: a lower zinger within 3 minutes of a higher one fired 2.2 times an hour, against 0.1 before.

The cooldown exists because one meeting had six qualifiers in eight seconds (a run of one-line jokes clears any bar one after another).

#### What qualifying must do

Whatever rule is used is judged against these four requirements. They came out of the replays and the arguments about them, and they are the reason some rules were dropped.

1. Two good qualifiers can follow each other. A strong qualifier must not hold the bar so high that an almost-as-strong one cannot fire soon after it. (An 8.9 should not shut out a 7.2 that is still well above the room's usual high tide.) The cooldown is the only thing allowed to space them.
2. Responsive to room heat. A scheme must respond to changes in the room's heat in the five-to-ten-minute range. Rooms change tenor on that time scale, so the memory behind the bar has to be about that long.
3. A quiet room is not silenced by one good qualifier. With few scores in the window, one strong score must not hold the bar for the better part of an hour.
4. Nothing absolute. No fixed floor, no score that counts everywhere, nothing tuned for one room.

A preference, tuned and not required: about six qualifiers an hour on average, at random. Timing should not be evenly spaced; qualifiers may bunch and a quiet stretch may follow. The average is a setting to tune, not a test a rule must pass.

Where the current rule stands: a short window with a high percentile fails 1 and makes the timing too regular; a long window fixes those and fails 2 and 3. The decaying quantile with qualifiers left out (how it was first shipped, before the dial) meets 1 and 2 better and keeps a quiet room alive (replayed on five recordings: about 13.5 qualifiers an hour in busy rooms and 11.1 in a quiet one, which is above the goal; `percentile` and `half_life_seconds` are the levers). Those rates were measured before the held-score fix and are to be re-measured. A rule that meets all four is still being looked for.

#### Bar rules we tried

Each rule was replayed on the recorded meetings before it was kept or dropped, in this order.

1. A fixed threshold (a score of 7 or more).
   - Worked: the simplest rule there is, easy to explain and to predict.
   - Did not: the number means the same in every room, so a lively room fires constantly and a dull one never does. It is the absolute scoring the project is trying to avoid.
2. A moving average (a fraction above a decaying average, with a floor).
   - Worked: the bar follows the room, and it needs only a few lines of state.
   - Did not: an average sits in the middle of the scores, so about a third of sentences are always above it, and the setting meant to control the rate barely moved it. The floor was a hand-picked constant standing for a decision (what counts as too dull to count) that nothing in the data had made.
3. Tukey's fence (Q3 plus a multiple of the interquartile range, over a timed window).
   - Worked: a standard test for "stands out", no floor, and the timed window gave a sensible sense of "the room right now". A lower multiple brought the rate to about the goal.
   - Did not: it predicts the top of the scores from the width of the middle half, which assumes a bell shape. The pile at 1 stretches the spread, so the bar often landed above the best score the room had produced, and once above the top of the scale nothing can qualify. The rate also swung from room to room at one setting.
4. A percentile of a timed window (`percentile`).
   - Worked: no assumption about shape. The bar is always a score the room really earned, so it can never leave the scale, and across rooms the rate stayed closer to the goal than the fence did. With 99.5 over five minutes it gave 9.8 an hour on the recordings, against 6.7 with twenty minutes.
   - Did not, or not yet: with a short window it is "beat the best of the window", so one lucky score holds the bar up. The rate is set mostly by the window's length and not by how much the room talks, until the window holds a couple of hundred scores (99.5 only differs from 100 then).
5. A decaying quantile (`decaying_quantile`, the shipped default; see above).
6. Double MAD (a median, and a spread measured only from the scores above it).
   - Looked promising: the pile of low scores sits below the median and cannot widen the upper spread.
   - Found in a replay, not built: with the proposed multiplier the bar was above the scale in more than half of the windows, and the rate varied several-fold between rooms. Still under consideration.

#### What the attempts taught

- The scores are bounded and lumpy, so any bar worked out from the middle of the scores tends to overshoot the top. A bar that measures the top directly behaves better.
- The rate of qualifiers is a result, not a setting. A rule that follows the room's talk fires more in a lively room by design.
- A cooldown is needed whatever the rule.
- Every rule inherits the scorer's own quirks (an odd stretched spelling can score high), because the BarKeeper only ever sees numbers.
- All of this was tuned on four video-call meetings and one live room. A quiet real room is the test still to come.

#### Not built, and not decided

- (proposed) Paper guards, so nobody comes back to a pile of paper: nothing qualifies unless speech was heard in the last 10 minutes (this also keeps whisper's phantom phrases from printing in an empty room), and hard ceilings of about 12 prints an hour and 60 a day as a last resort. The activity measure would be seconds of accepted speech per minute from the Transcriber, not loudness.
- (open) The retired design had a target print rate that followed room activity and a rate governor. The bar plus the cooldown replaced them; the rate is a result to watch, not a quantity steered. If a real room shows the rate drifting, a governor can come back.

### The hinge: what the portent answers

- The hinge is the clip the portent answers: a word, a phrase or a topic (in practice a sentence).
- Its connection to the portent is tangential and lyrical, not a direct answer and not random. Talk about dogs gets an insult in the manner of Shakespeare calling someone a dog; something called weird gets something in the spirit of Blake on the uncanny; complaining about friends gets something about gossip.
- The leap is made by the oracle model, not looked up. A library lookup was rejected as too literal.
- Decided 2026-10-27: the hinge is the sentence that qualified. The scorer and the BarKeeper were built to hand over about six sentences an hour, so the one that clears the bar is used as it occurs. If that is a little inconsistent, scaring some hinges and catching on others, that is what talking to an oracle means. The first design's weighted random pick among the best recent candidates is dropped. The hand-off to the PromptBuilder is the Conductor's job.
- (open) Defining "interesting". Conclusion of 2026-10-22: the prompt and the BarKeeper keep circling because the project has not defined for itself what is worth answering. A human definition comes first; the scorer prompt then states it. Deferred, not dropped.

### Writing the portent: the oracle model

*Built*: `PromptBuilder`, `OracleModelClient` and `PortentWriter` (`src/pom/writing/`); the model is served by `llama-server` on port 8099.

#### The prompt and its settings

- The prompt is human-written and lives in config (`oracle.prompt`), with `{exemplars}` and `{hinge}` marking where those go. It asks for a new passage in the manner of the style example without printing the theme or the example, under 150 words.
- Exemplars: each time the Printer speaks, the Bibliomancer draws passages from the weighted library, independently and never matched to the hinge. They show the voice and nothing more. The bake-off and the config use one exemplar (`exemplars_min` and `exemplars_max` both 1); the range can be 1 to 5 or more.
- Settings (`[oracle]`): temperature 0.8; `max_tokens` 300 (a runaway guard); timeout 240 s. The whole reply is waited for at once (not streamed), and writing runs at about 0.5 s a token on the Pi, so the timeout must cover the longest portent the model can write; the Conductor's writing watchdog (300 s) is longer so the oracle gives up first.
- Output length follows the exemplar, usually 15 to 50 words and occasionally up to 150, with variation as a feature.

#### The model choice: the bake-off (2026-10-26)

Seven 8 to 9B models, one frozen prompt, 16 hinges x 10 seeded draws = 160 portents each, the same hinge and exemplar for every model, rated blind on seven yes/no marks (follows the hinge, follows the style, doesn't repeat the hinge, didn't muddle its details, no extra text, length close, particularly creative). Mean score of 7:

| Model | Score |
|---|---|
| Qwen3.5-9B | 6.08 |
| Gemma 2 9B | 5.84 |
| Hermes 3 | 5.26 |
| Ministral 8B | 5.21 |
| Llama 3.1 8B Instruct | 5.19 |
| Stheno v3.4 | 4.73 |
| Qwen3-8B | 4.45 |

Qwen3.5-9B and Gemma 2 both beat Llama 3.1 clearly; Qwen3.5 against Gemma is not settled. No model was particularly creative (the best managed it in half of its portents). Decided 2026-10-27: the Printer uses Qwen3.5-9B, with Gemma 2 9B as the named alternate (one config change away), unless a problem shows. Gemma was more faithful to the hinge (it repeated the hinge 2 times in 160 against 10, and muddled fewer details) but less creative (68 portents against 81), weaker on style, and should be 20 to 30% slower on the Pi (an estimate, not measured). Caveats: one rater (an LLM judge); a human spot-check of about 20 ratings is pending. Write-up: [Model Bakeoff](Model%20Bakeoff.md); rubric: [Bakeoff Rubric](Bakeoff%20Rubric.md).

#### Speed on the Pi

The 9B is the biggest model in the system and the Pi's memory is much slower than the Mac's (the 9B writes 36 times slower on it). On the Mac a portent takes a median 0.8 to 0.9 s. [experiments/pi-oracle-benchmark/](../experiments/pi-oracle-benchmark/) measured the Pi, 12 portents a phase from an emptied page cache, with whisper and the 4B loaded and idle, and with the 9B alone (2026-10-27):

- A portent takes a median 38 s (35 s alone) and up to about 100 s for a long one; a portent near the 150-word ceiling would take about 2 minutes. Typing it adds 5 to 20 s.
- The first word comes after a median 20 s: the Pi reads the prompt at about 10.5 tokens a second.
- Writing runs at about 2 tokens a second, slower than the typewriter types, so a portent is finished before typing starts.
- Cold start: 50 to 78 s for the 9B, once, at boot. A restart with the file in memory takes 13.5 s.
- The idle neighbours cost about 3 s a portent at most. Nothing throttled; the hottest the Pi got was 73.6 °C.
- The wait was judged acceptable: the first word after about 20 s, a typical portent finished in 35 to 40 s, then 5 to 20 s of typing.
- The server reuses none of the prompt between portents even though its start is the same each time. llama.cpp does not reuse a changed prompt for Qwen3.5 (a hybrid model with recurrent layers; `cached_tokens` is 0). Left alone.

#### Two oracle tracks (Track B not started)

The first design had two ways of writing behind one interface: Track A, a stock instruct model with a prompt, and Track B, a base model fine-tuned (LoRA, on the Mac with MLX, never on the Pi) on the library plus curated hinge pairs, run with an almost empty prompt so that it cannot speak outside its shelves. The bake-off is Track A. Track B and the curated hinge pairs (an overheard line paired with a tangential in-shelf reply, curated by hand, with a large model drafting candidates, logged in the AI-use ledger) have not been started. (open) Whether to start them depends on how far the prompted oracle gets.

### The Library: the form that influences the Oracle

#### What it holds

81 holdings in 14 shelves, about 71,000 passages of 15 to 150 words, public on GitHub. [library/ready/](../library/ready/) holds clean utterances, one per paragraph, with no trace of their holding. [library/passages/](../library/passages/) turns them into JSONL passages. Everything is built by scripts from the manifest [library/holdings.csv](../library/holdings.csv), so it is repeatable. Adding material: [Adding Material](Adding%20Material.md). How the standardizer works and what went wrong: [Library Notes](Library%20Notes.md).

#### How it is drawn

Shelves are what the oracle might say, not where text came from. Draws are weighted: every utterance is in one pool and has its own chance, a shelf with few utterances gets a heavier weight per utterance, and the shelf shares start equal. Nothing is chosen category-first, so any combination can come up, including five obituaries in a row; it is just rare. [library/passages/circulation.json](../library/passages/circulation.json) is the one hand-edited file for shares and holding weights (Shakespeare's plays are 0.2, for example). Draw odds are computed at load time, never stored.

#### Rights

Public domain, plus owner-cleared material (the IBM/Lexmark Wheelwriter manuals and the dad jokes), decided by the project owner. Licensing and content are not concerns for this project: it is an art installation, far from the consumer market.

#### Unfinished exemplars are not drawn

Found 2026-11-03. The oracle's prompt tells it to match its exemplar's form, tone and length, so it also copies the exemplar's ending: a passage that stops at "...is the", or is a lead-in ending on a colon with its list gone, makes a portent that stops the same way. This was the "cut-off portent": the writer, formatter and printing were intact; the exemplar was the cause. About 1.5% of the passages ended unfinished. Two fixes, both on purpose:

- The Bibliomancer draws again when a pick lands on one (`[oracle] exemplar_redraws`, 50 tries a pick, 0 is off), so the odds among finished passages are unchanged. It stays as the safety net for any new holding.
- The library pipeline no longer keeps them. `normalize.py` drops utterances that end unfinished, and the cataloguer cuts a long utterance only after a sentence end (the rule: a full stop, question mark or exclamation mark, then any closing quotes or brackets and the dash some old books print after it; `pom.library.endings` is the one definition). A stretch with no sentence end is never cut, and a piece over the 150-word ceiling is dropped (0.08% of the library, mostly very long 19th-century sentences; `--oversize 1.5` would keep those). Sentence ends are found anywhere in the text, mid-line too, because Shakespeare's end part-way along lines. The audit's `unfinished` column must read 0.

#### Open

- (open) Equal shelf shares make small shelves repeat. A simulation showed repeats are negligible over an evening (0.1%) and matter over months; a recency decay was prototyped and deliberately left out.

### Printing: from portent to paper

#### The formatter

*Built*: `PortentFormatter` (`src/pom/printing/`) makes a portent printable. It takes out the triple-backtick fences (the prompt shows the model its exemplars and the hinge in fences, so it may echo them); a fenced piece is a quotation and is typed indented four spaces with a blank line on each side, since the typewriter has no italics. It swaps curly quotes, dashes and accents for plain ASCII and drops what has no plain form, wraps to the line width, and ends the text with `linefeeds_after_portent` line feeds (4) so the paper has moved on for the next portent.

Most print wheels lack nine ASCII characters (`< > \ ^ ` { | } ~`); the CadetWriter builds them from periods, slowly. `<` and `>` are left to it; the other seven are swapped for a look-alike unless `keep_composed_characters` says otherwise, and `ascii_printwheel` keeps all of them. The line width is worked out in `[printing]` from the paper width, the margins and the wheel's characters per inch (8.5 inch paper with 1 inch margins: 65 characters at 10 cpi, 78 at 12, 97 at 15; 10 is the default, the widest letters, so it fits any wheel).

#### The serial port

*Built*: `TerminalPort` (`src/pom/printing/terminal_port.py`, config `[terminal]`) sends the formatted portent over serial, paced (16 characters a second to start; to be measured), with flow control, CR+LF line endings and only safe bytes. `type()` blocks until the machine has had time to finish; the port knows nothing about listening. `ConsoleTerminal` is the same code on the screen, for development. `[terminal] mode` is `serial`, `console` or `none`.

The same port reads the keyboard: `read_line()` returns what someone types when they press C Rtn. In the CadetWriter's default full-duplex mode the Pi echoes each key (`echoplex`), a Backspace overstrikes but edits the line, and keys pressed during a portent are thrown away (unless `type_during_printing` is on).

#### The port stays with the portent

A serial printer is fickle (switched off, unplugged, held, out of paper), so once typing has begun a failed write does not end the portent (decided and built 2026-11-02). A stall (the machine stopped taking characters) is waited out and the same character is sent again; a lost port is closed and opened again every `reconnect_seconds`, and typing goes on from the first character not sent. Either wait lasts at most `stall_patience_seconds` (600), sends the caller's heartbeat every few seconds, and logs one warning for the stretch. The `port` may be a pattern (`/dev/ttyACM*`, `/dev/serial/by-id/usb-*`) looked up at each open, so a printer replugged under another name is found.

Not visible to software: whether the CadetWriter signals a hold or out-of-paper through flow control (to be tested on the machine), and a printer powered off behind a USB adapter that swallows the bytes. A character or two in flight at an unplug may be lost or doubled.

#### The boot slip

*Built* (2026-11-04): every time the Printer and the typewriter come online, the typewriter types a few lines laid out like a terminal announcing itself, one of them the address the Pi has, so the monitoring page and ssh can be reached with no screen and no router page. It is a plain terminal's greeting and nothing from the Office. The words are `[boot_slip] text` in [config/pom.toml](../config/pom.toml), with `{address}` and `{hostname}` filled in:

```
CADETWRITER - IBM-1620
MODE ......... TERMINAL
SELF TEST .... OK
LINE 1 ....... 192.168.1.23
ON LINE
```

The design choices, and why:

- **It waits in the background and holds nothing.** `BootSlipper` (`src/pom/conductor/`) is a thread in the manner of the scorer's warm-up. The Printer listens at once, whatever state the world is in: the servers may still be loading, the typewriter may be switched on later than the Pi, Wi-Fi may not be up.
- **It types whenever the typewriter is online, every start, network or not.** It asks the terminal port, quietly (`TerminalPort.online()`), until the port opens. A Pi with no network still types its slip.
- **The address is read at the last moment.** If the machine has none yet, it waits up to `address_grace_seconds` (15) for Wi-Fi to come up, then types anyway with the loopback address, 127.0.0.1, which says plainly that there is no network. Every usable address is listed, so the Comitup hotspot's shows when that is what is live.
- **The `AddressFinder` asks the operating system about every interface** (psutil, a maintained library with prebuilt wheels for the Pi and the Mac). The usual stand-in, a UDP socket toward an outside address, finds nothing when there is no default route, which is exactly when the Pi is its own hotspot and the address matters most. Loopback and link-local (169.254.x.x, assigned when DHCP failed) addresses are left out.
- **Typing takes the floor, like a portent.** The Printer is deaf while the slip types and does not hear its own typing. The floor is first come, first served, so a slip that arrives while a portent is printing waits for it. After taking the floor it waits the Conductor's check interval plus a second, so the ears are shut before the first character, since the Conductor shuts them when its watching thread next looks.
- **It types once.** If the typewriter drops out before the first character, it goes back to waiting; part-way through, the start is on the paper and it is not typed again, as with a portent.
- **Nothing that was said is in it.** The addresses go in the log (they are not speech); the monitor password is never printed.

#### Output checks

The output validation hook ([scripts/dev/library/validate_output.py](../scripts/dev/library/validate_output.py)) is a deliberate stub. Checking for verbatim overlap with the library was judged the wrong measure: false positives on quotes that land, false negatives on near-copies, blind to echoing the hinge. (open) A "silence" tic across portents: the rubric rated portents one at a time, so a habit that repeats across them would not show up.

### The keyboard: what people type

People can type to the Printer, and typed input has its own way in, not the Eavesdropper.

- The `TypedLineScorer` (`src/pom/keyboard/`) has the Scorer score the line (one sentence, no context), multiplies the score by `typed_lines_score_multiplier` (1.5, uncapped: a typed 7 beats any spoken 9), and the same bar judges it through `BarKeeper.rule_typed()`. There is no cooldown, a qualifying typed line does not start one, and a line under the bar is silently ignored. Every typed line then goes to the bar by the same rule as a qualifier, with its own dial, `typed_lines_bar_influence`.
- The `KeyboardWatcher` (`src/pom/conductor/`) is the typed-line twin of the hinge finder: it reads each finished line from the terminal (one-second poll), has the TypedLineScorer score it and the BarKeeper rule on it, and yields the attempt; the one that qualified is the hinge, exactly as for the room. It has the Eavesdropper's `pause()` and `skip_ahead()`: a line that finishes during a turn is still read (so the terminal's buffer stays empty) but dropped unscored and counted. A terminal or scorer that raises is one warning naming the error type and a short pause, never the end of the watching.

### Conducting: running a turn

#### The floor and the watchdog

*Built*: `FloorKeeper` (`src/pom/conductor/floor_keeper.py`). One object knows who may speak. The floor is in one of four states and a turn moves through them in order: listening, writing, printing, recovering, and back to listening. The first of the room and the keyboard to ask gets a `Turn` (first come, first served); the other is refused. A `Turn` is used in a `with` block, so the floor is yielded even when the work fails. Recovery is a few seconds with the ears still shut, so the terminal's last sounds die away before the Printer listens.

The design rule is that the Printer fails toward speaking. Every way a turn can end (it finished, it raised, it ran too long) goes to recovering and then listening; nothing waits on anything; the lock has a timeout; every call first settles anything that has run out ("lazy expiry"), so no ticker thread has to survive for the watchdog to work; the clock is monotonic, since the Pi has no battery clock and its wall clock jumps when the network sets it. A turn the watchdog has cut off is harmless when it wakes up later: its `Turn` is stale and its calls do nothing. The watchdog cannot stop a thread that is stuck, only take the floor back, so the TurnTaker gives every blocking call a timeout of its own; the watchdog is the last line of defense, not the first.

#### The limits err long

Decided 2026-11-02: the limits must cover the longest likely run, not the average.

- Too short: a healthy slow turn is cut off while it is still working. The floor goes back to listening and, after the recovery, the ears open while the terminal is still typing, so the Printer can hear itself and react to its own printing. A limit near the average would cut off exactly the long portents, and the Printer would say only short and medium ones.
- Too long: a turn that is really stuck (a hung request, a dead serial port) keeps the Printer deaf until the limit runs out.

The cost of too short is a wrong answer; the cost of too long is a quiet spell. So the limits in `[conductor]` are: writing 300 s, printing 180 s, recovery 3 s. The writing limit must be longer than the oracle client's own timeout (240 s), so the oracle gives up first. A heartbeat during a wait for the printer restarts the printing clock. Everything is logged (every change of state with who holds the floor and how long the last state lasted, each refused request, each overrun, each late call from a cut-off turn): labels and seconds, never words.

#### Taking a turn

*Built*: `TurnTaker` (`src/pom/conductor/turn_taker.py`). `take_turn(turn, attempt)` takes the Turn and the attempt that qualified through four steps: write the portent, record the turn in the TurnHistory (at once, before any printing, so the monitoring site shows it even while the printer is being waited for), format it and type it, and say how the turn ended. It runs inside `with turn:`, so the floor is always yielded, and it never raises: an unexpected error ends the turn with the outcome `error`.

Printing has priority over listening (decided 2026-11-02). A portent costs minutes of the oracle's time and they come every 6 to 8 minutes, so there is no hurry back to listening, and a portent that has been written is printed (even one that finished after the watchdog took the floor: that is logged as a warning). How each failure ends:

- The oracle gives nothing (server down, slow, answering oddly): the turn tries again, up to 3 attempts 10 s apart, but only while a whole attempt (the oracle's 240 s timeout) still fits in the writing budget (270 s, under the watchdog's 300 s). A quick failure is retried; a timeout is not. Still nothing: outcome `no portent written`, back to listening. Three in a row: a CRITICAL fault says the oracle looks broken.
- A portent grows stale. It answers one sentence, and a few minutes later the room has moved on. A portent is worth printing for `freshness_seconds` (6 minutes from the start of the turn, a little under the usual gap between portents). Past that it is dropped, its record says `too late to print`, and the Printer goes back to listening. The check is made before each try, never in the middle of typing: a portent that has begun is finished.
- The terminal cannot print yet (unplugged, held, no paper, port gone): nothing was typed, so the turn waits 10 s and tries again until the portent goes stale, showing `waiting for the printer` on its record. The ears stay shut: with no printer there is nothing to listen for.
- The terminal fails halfway: part of the portent is on the paper, so typing it again would repeat the start. Outcome `print failed`, a CRITICAL fault, no retry. (The TerminalPort waits out a held or lost port itself first.)
- No printer by design (`terminal` is `None`): write, record, done, outcome `not printed (no printer)`. The Printer can be watched listening and writing, with everything on the monitoring site, with nothing attached.

#### Pausing the ears, like a tape deck

*Built*, in the Eavesdropper. The Conductor calls `Eavesdropper.pause()` the moment a turn takes the floor and `skip_ahead()` when it yields it. While paused, the listening thread keeps reading frames from the deck but throws them away and asks whisper nothing, so the microphone's buffer never overflows; a segment whisper had transcribed but not yet handed on is dropped as well, and the hinge finder stops scoring the rest of the take being cut, so nothing reaches the scorer mid-turn. `skip_ahead()` ends the pause with a clean slate: the cutter forgets its audio and any half-finished segment, the repeat memory is cleared (so the same words said after the turn are heard), and the cutter's clock moves forward by the sound that went by unheard.

Decided 2026-11-02: the turn's time counts, so audio time keeps matching real time and the 60-second cooldown and the bar's memory run through the turn (otherwise a 60-second turn would stretch the cooldown to two minutes). Both calls only set a flag and return at once, from any thread; the listening thread does the dropping, so no state is shared. A whisper request already in flight when the pause begins finishes first and its result is dropped.

#### The Conductor

*Built*: `src/pom/conductor/conductor.py` and `src/pom/printer.py` (`python -m pom`).

- First come, first served. An attempt that qualified asks the FloorKeeper for the floor. The first to ask gets a Turn and the other hinge is dropped, with a line in the log. The turn goes to the turn thread, never run on the one that found it: the room thread must go on reading sound (which the paused Eavesdropper throws away) or the microphone's buffer would overflow while the Printer is busy.
- The ears shut the moment the floor is taken, and open again by looking, not by being told: every second the watching thread compares the floor with the ears, and if the floor is listening and the ears are shut it calls `skip_ahead()`. So a turn that ends any way at all, including the watchdog cutting it off, cannot leave the Printer deaf.
- The room is built again each time it ends. The entry point gives the Conductor a `room_factory` that makes a fresh deck, Eavesdropper and hinge finder (the BarKeeper and the scorer are shared, so the memory of the room's scores survives). A microphone that is unplugged ends the hinge finder; the Conductor builds a new room after `restart_pause_seconds`, so plugging the microphone back in is all it needs. A recording that ends is the end of the run. A thread that dies unexpectedly is started again by the watching thread.
- It starts whatever state the world is in. At boot the servers, the microphone and the printer come up in no particular order and nobody is there to restart anything. A server that does not answer yet is a line in the log; each client recovers when its server arrives, a turn waits for the printer, and a monitoring site that cannot start (no password, port taken) is a warning, not a stop.
- Settings. `[conductor]` in [config/pom.toml](../config/pom.toml) holds every limit; options on the command line (`--wav`, `--mac-mic`, `--terminal`, `--no-keyboard`, `--config`) choose for one run. `Config` checks the limits against the settings they depend on and warns when a watchdog is shorter than what it watches (the values are kept; the person is told).
- Stopping. SIGTERM (systemd) and ctrl-C stop it cleanly: the ears shut, the keyboard closes, each thread is waited for at most 5 seconds. A portent being typed for longer than that is cut off when the process exits, so restart the service between portents if it matters.

#### The burn test

2026-11-02, the Mac, real servers (whisper-server, the Qwen3-4B scorer, the Qwen3.5-9B oracle). A 5-minute recording and a 71-minute one at 6x speed gave turns back to back, with memory steady at about 120 MB, 5 threads and no warnings. The oracle was killed mid-run: three attempts 10 s apart, the turn dropped, listening again, and when the server came back the next turn simply worked. whisper was killed before the start: the Printer ran, reported it, and heard again when it returned. A stand-in typewriter (a pseudo-terminal opened by the real pyserial) was unplugged mid-portent and replugged under another name: it resumed at the exact character it stopped, with nothing repeated or lost. A printer absent at the start was waited for and used when it appeared. A line typed on the stand-in's keyboard took the floor as the keyboard and its portent was typed back. SIGTERM during a turn exited in about 7 seconds.

What the burn test could not show: the real CadetWriter's flow-control behavior (hold, out of paper), and the Pi's own speed (since measured; the Pi has since run it as services through a reboot and a hard kill of a server).

### Watching: the monitoring site

*Built*: `src/pom/monitor/`. A password-protected web page, on the Printer's network (for example `http://pom1.local:8080`), of the last hour of turns, newest first, with a page for each: the hinge, the exemplars the oracle was shown, the portent, the score, the bar it beat and the window at that moment. It also draws a chart of the room's scores against the bar, and shows which servers are up.

- The hinge is what someone said in the room, so the `TurnHistory` keeps it in memory only, for an hour, and the site asks for a password (HTTP basic auth) and will not start without one. `show_hinges = false` hides the hinge text and shows numbers only. Nothing the page shows is stored anywhere but that memory. Pages are never cached, and all text is escaped, because the hinge and the portent are untrusted words.
- The page updates itself by polling a few lines of script, every `[monitor] poll_seconds` (5); no websockets. The chart, the status tiles and the turns all move on that one beat. The tiles (the bar to beat now, the scores in the window) are read live on each poll at the stream clock, not from a snapshot taken when an attempt arrives, so in a quiet room they fall with the ticks; a turn shows on the first poll after its portent is written (its record is made then, so the page cannot know of a turn sooner). The server is werkzeug's own, in a thread of its own, meant for a single reader on a trusted network behind a password.
- **The chart moves through silence.** Its right edge is now on the stream clock (the Printer's `stream_now()`, the listening side's clock, which runs through silence), not the newest attempt. The anchor's ticks are drawn as grey dots at `anchor_score`, in the gridline color. The bar line is drawn through the bar as it stood at each tick (`level_as_of`: the bar's memory read at a past time, ignoring scores heard after it, so it is exact and needs no sampler and no open page), then to the live bar at the right edge, so it slides down through a quiet stretch as the bar really did.
- **The ticks on the chart come from two places.** The ticks since the newest attempt are worked out afresh on each poll, while the bar still holds the scores they need. The older ones are in the chart notebook: when an attempt arrives, the notebook asks the bar for the ticks since the previous attempt (or since the stream began) and writes them down with the bar at each, because the bar forgets scores after `discard_scores_older_than_minutes` and a tick from before a long silence could not be worked out again (the page once asked for no ticks further back than the Turns list lasts, an hour, so after a long silence the left of the chart had no dots). Across a silence longer than `longest_silence_minutes` nothing is written, since the chart draws that stretch as a break and hides what is inside it (a room quiet for 15 hours would be 900 ticks). The notebook is memory only and keeps an hour back from its newest entry (`history_seconds`), at most `chart_notebook_max_entries` of them.
- **A long silence is drawn short, like a broken part in an engineering drawing.** A silence (no attempt, between two attempts or from the newest to now) longer than `[chart] longest_silence_minutes` (10) is drawn that wide (`SilenceCompressor` turns stream time into drawn time, squeezing each silence evenly), so a quiet hour does not push everything else off the chart. `window_minutes` are *drawn* minutes, so after a silence the chart reaches back further in real time; the caption still says the last 30 minutes. Over the silence is a clear gap in the card's color (`break_gap_width`, or the whole silence if narrower), centered, with a zigzag break mark on each edge (`break_mark_size`; none on a left edge the window cuts off) and "14 min" over "silence" written in it ("1 hr 5 min" over "silence" past an hour). The rest of the silence shows either side of the gap: the bar sliding down and the ticks squeezed together. No vertical gridlines are drawn inside a silence, since their spacing would mean nothing there.
- **The time axis is one note.** The "-30 min" style labels are gone: one left-justified line, "Intervals at approximately 5 min" (`gridline_every_minutes`; approximately, because silences are drawn short), and "now" at the right edge.
- The site is off by default (`[monitor] enabled`), and the real password lives only in the gitignored `config/pom.local.toml`.
- **The terminal has a light, with the servers'**: `terminal: up`, `DOWN` or `none`. *none* (grey) means no terminal is configured (`[terminal] mode = "none"`), so there is nothing to ask; *up* means the port is open or opens now (`TerminalPort.online()`), and while a portent is being typed on it; *DOWN* means it will not open. The page asks with `online(wait=False)`: typing holds the port's lock for minutes, and a waiting question would freeze the page's polling. It cannot see a stall in the middle of a print (the turn's own outcome, "waiting for the printer", does), nor "on hold" or "out of paper" (not visible to software, see above). The light is drawn when the page loads.
- **A quiet start is a silence too.** The stretch from the stream's start (`stream_began_at`, the same start the bar's `started_at` is) to the first attempt, or to now if none has come, is drawn like any other silence, so a Printer that has heard nothing since it started shows its break. The chart uses the start only while the `ScoreTrace` still holds every attempt since it (`holds_everything`); after the page has forgotten older attempts the first remaining attempt is not far from a start that nothing shows any more.
- **The Printer's own screen is a kiosk.** A monitor plugged into the Pi shows the monitoring page full-screen. `pom-display.service` runs *cage* (a one-window Wayland compositor) on virtual terminal 7, and cage runs `scripts/setup/kiosk_browser.py`, which waits for the page to answer (asking every `poll_seconds`) and then becomes Chromium in kiosk mode on `http://127.0.0.1:<port>/`; the browser, its flags and its profile folder are the `[display]` settings. Raspberry Pi OS Lite (headless, no desktop) needs only cage and Chromium for this, installed by `scripts/setup/setup-kiosk.sh` from the prebuilt packages. **The display can never trouble the Printer:** `pom.service` knows nothing of it, the unit only `Wants=` the Printer and never `Requires=` it, and it is not a model server, so nothing is loaded or unloaded for it. **No monitor is not an error:** its `ExecCondition=` runs `display-attached.sh`, which asks the kernel whether any display connector says `connected`; with none, systemd skips the unit quietly (no failure, no restart loop). Cage exits by itself when its last monitor is unplugged, and a udev rule (`scripts/setup/udev/90-pom-display.rules`, linked by `install-services.sh`) starts the unit again when one is plugged in. It restarts only on a failure (`Restart=on-failure`), so a setting only a person can change (the site not enabled, or still asking the Printer for a password) is reported in the journal once and left, not looped on. A mouse works as it does in any browser (the qualifier dots and turn rows are links; "all turns" on a turn's page goes back); a keyboard can switch away with ctrl-alt-F1 (the console login), since cage runs with `-s`.
- **The Printer's own screen needs no password.** `[monitor] password_on_localhost = false` lets a request that comes from the Printer's own machine (127.0.0.1 or ::1, the connection's own address, which a client cannot choose) in without one, so no password is kept in the kiosk's settings. Everyone else on the network still needs it, and a request that names a proxy (`X-Forwarded-For`, `Forwarded`, `X-Real-IP`) is treated as coming from elsewhere, so a reverse proxy or `tailscale serve` on the Printer, which would make every request look local, cannot open the page. Anything running on the Pi itself can read the page without the password; that is the trade, and it is a Pi on a lab network. The code default is `true` (everyone asked); `config/pom.toml` ships `false`. The kiosk's launcher refuses to start (with the reason in the journal) while it is `true`.
- Not built: the per-turn outcome on the page. It waits on a preview and a decision.

## Software architecture

### Rules that cut across modules

- All runtime code is object-oriented: small classes with one concern each, methods that each do one thing, plain data objects passed between them. Helper scripts (`scripts/`) import `pom`; `pom` never imports them. Logic that needs no hardware is kept apart from code that touches the mic, a model or the serial port, so most of it is tested on the Mac (3,396 tests).
- The Printer never stops because of a typo or a recoverable fault. A module recovers and records the problem in a `FaultRecorder` (WARNING = recovered, CRITICAL = cannot do its job; even then nothing raises). `strict=True` raises, for tests and dev tools only.
- Modules do not read `Config`. The entry point builds one `Config` and passes values in. Time is passed in too, never read inside a leaf module, so a recording replayed fast behaves like a live run.
- Logs hold counts and scores, never words. One `debug` flag turns debug output on everywhere; each module can override it with a `DEBUG = None` line at its top (a test refuses a committed override). Logging goes to stdout, which journald collects.
- (open) Failing loudly. If the scorer model or the oracle model is down the Printer can never respond, and a log line is not loud when someone is switching a printer on. Decide what loud means (a beep, an email to the project owner, something at the venue) and make those start-up steps critical in that way. Today the scorer step is not critical and an unscorable clip is left unscored.

### Modules

```
src/pom/
  fault_recorder.py, log_setup.py, config.py     the shared plumbing
  startup.py                                     the runner; the ten duties are placeholders
  printer.py                                     the entry point (python -m pom): builds everything from one Config
  library/     catalog, circulation, bibliomancer, endings
  listening/   audio_deck, mic_deck, wav_deck, mac_mic_deck, audio_format,
               whisper_model_client, rolling_cutter, track_splitter, take_cutter, eavesdropper
  scoring/     word_tagger, scorer_model_client, clip_scorer, scorer_warmer
               fallback_scorer                   shelved, unused
  qualifying/  bar_keeper, attempt, bar, bar_keeper_factory,
               percentile_bar, decaying_quantile_bar, moving_average_bar
  keyboard/    typed_line_scorer
  writing/     prompt_builder, oracle_model_client, portent_writer
  printing/    portent_formatter, terminal_port
  conductor/   hinge_finder, keyboard_watcher, floor_keeper, turn_taker, conductor
               (the Conductor is the only module that knows the whole pipeline)
  monitor/     turn_history, chart_notebook, score_chart, monitor_site
```

### Start-up duties

`startup.py` has a built runner and ten duties, still placeholders that report themselves as skipped, and the entry point does not yet call it: config report; data check (library, weights, NLTK data; critical); microphone gain; audio device; transcriber; scorer server; scorer warm-up (done by the `ScorerWarmer` instead); oracle server (critical); typewriter port (critical); a one-line readiness summary (counts and seconds only). They are replaced one at a time; the runner and its tests do not change.

## What we have measured

| Experiment | Folder | What it settled |
|---|---|---|
| Interest scorer | [interest-scorer/](../experiments/interest-scorer/) | One sentence, no context, digit-probability score; Qwen3-4B; a bigger model (8B) scores gentle-weird lines higher |
| Pi benchmark | [pi-benchmark/](../experiments/pi-benchmark/) | The 4B scorer next to flat-out whisper on the Pi: median 2.3 s a sentence |
| Hearing debug | [hearing-debug/](../experiments/hearing-debug/) | Whisper-driven cutting, the track split, the known-good setup, the qualifying replays |
| Oracle testbench | [oracle-playground/](../experiments/oracle-playground/) | Hand trials of oracle models and prompts |
| Oracle bake-off | [oracle-bakeoff/](../experiments/oracle-bakeoff/) | Seven models, 160 rated portents each; Qwen3.5-9B first |
| Pi oracle benchmark | [pi-oracle-benchmark/](../experiments/pi-oracle-benchmark/) | The 9B on the Pi with whisper and the 4B idle: a typical portent in 35 to 40 s, the first word after 20 s |

The qualifying replays used four recorded team meetings and one live room (public by design, kept in gitignored `data/`).

## Open questions

- What is "interesting", defined by us before the model is asked. (The hinge)
- How qualifying behaves in a real, quiet room, and whether the rate settles near six an hour; the paper guards. (Qualifying)
- A prompt tuned for the top two oracle models; a human spot-check of the bake-off ratings. (Writing the portent)
- Whether Track B and the hinge pairs are worth building. (Writing the portent)
- Mic placement and cable routing; the idle-noise test with the Wheelwriter. (Hardware)
- How the real CadetWriter signals a hold or out of paper. (Printing)
- What "failing loudly" means. (Software architecture)
- A real whisper: a very quiet aside in a very quiet room is treated today like any quiet sound (the voice detector may not call it speech). Revisit once the rest works. (Listening)
- Whether equal shelf shares need a recency decay over months. (The library)

## Appendix A: What changed from the first notes

- Scorer: word lists -> a small local language model (Qwen3-4B), one sentence per request, no context, a 0 to 9 probability-weighted score.
- Qualifying: a pool of faded scores against a moving threshold with a rate governor, activity curve and no cooldown -> a bar worked out from the room's own recent scores (a decaying quantile, 99.5th percentile, 5 minute half-life) with a 60 s cooldown and no floor.
- Hearing: cutting by loudness with a moving noise floor -> whisper's own voice detection on a rolling buffer; the track split of whisper's output (words, room tone, nothing).
- Oracle: two tracks behind an interface -> a prompted stock model with a human-written prompt (Track B not started); Qwen3.5-9B leads the bake-off.
- Library: about 69,000 utterances in 13 shelves -> about 71,000 passages in 14 shelves, passages of 15 to 150 words.
- Whisper: `small.en` -> `base.en`, the model that worked in the room.
- Half-duplex: deaf while printing -> nothing is sent to whisper or the scorer model while the oracle writes or the typewriter prints; all models stay loaded.
- Hinge: a weighted random pick among the best recent candidates, with a temperature -> the sentence that qualified.
- Conductor: planned on `asyncio` -> built on threads, with a FloorKeeper and a watchdog.
- Retired: the activity bar and target-rate curve, the faded-score pool, the hinge picker, the Mimic splicing fallback, the word-list Scorer (kept as a documented fallback), Tukey's fence, and the license questions about wordfreq (no longer a dependency of the Scorer).

## Appendix B: Companion documents

This document says what was designed and why. These say the rest.

| Document | What it is for |
|---|---|
| [README.md](../README.md) (repository root) | What the Printer is and how it works, how to run it, a map of the repository, where it stands |
| [documentation/Lexicon.md](Lexicon.md) | The project's vocabulary: every name used in the code, config, prompts and docs, and what it replaced. Read it first if a word here is unfamiliar |
| [documentation/Project State.md](Project%20State.md) | The handoff: the working rules, the decision log, what comes next, gotchas |
| [documentation/Pi Setup Notes.md](Pi%20Setup%20Notes.md) | Setting up and running the Pi: OS, Wi-Fi and the Comitup fallback, the whisper.cpp and llama.cpp builds, the services, the microphone, and what to do when `pom1.local` will not resolve |
| [documentation/Model Bakeoff.md](Model%20Bakeoff.md), [documentation/Bakeoff Rubric.md](Bakeoff%20Rubric.md) | The oracle bake-off: method, results and the rating rubric |
| [documentation/Adding Material.md](Adding%20Material.md), [documentation/Library Notes.md](Library%20Notes.md), [library/README.md](../library/README.md) | Adding to the library; how the standardizer works and what went wrong; the library's rules and columns |
| [documentation/AI Ledger.md](AI%20Ledger.md) | The record of remote AI (Claude) use; local models are never counted |
| [scripts/dev/README.md](../scripts/dev/README.md) | The testbenches and playgrounds: small tools that use the real modules. Use them before diagnosing anything |
| [scripts/setup/README.md](../scripts/setup/README.md) | The provisioning scripts and the service files |
| [config/pom.toml](../config/pom.toml) | Every setting, each with a comment saying what it does and why |
| [CHANGELOG.md](../CHANGELOG.md) | What changed and why, in order |
| `experiments/*/README.md` | The experiments behind the measurements above |
