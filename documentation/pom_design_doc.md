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
microphone -> whisper-driven cutting -> whisper -> track split: words / room tone / blanks
  -> TakeCutter -> Scorer -> Qualifying -> hinge -> PromptBuilder -> oracle model -> Printing -> typewriter
```

| Stage | Status |
|---|---|
| Microphone, audio decks, whisper model client, cutting, the track split, the Eavesdropper | Built (`src/pom/listening/`), matched against the experiment on a recorded meeting; the new `MicDeck` has not yet heard a real room |
| TakeCutter | Built |
| Scorer (clip_filter, scorer model, circuit breaker) | Built |
| Qualifying (`BarKeeper`) | Built; used by the hearing-debug probe, not yet by a Conductor |
| Hinge (the sentence that qualified) | Decided; nothing to build but the hand-off |
| PromptBuilder, `OracleModelClient` | Built |
| Input: typed line scoring (`TypedLineScorer`) | Built |
| Printing: formatter (`PortentFormatter`) | Built |
| Printing: serial port (`TerminalPort`) and the keyboard coming back | Built |
| Printing: pausing listening while it types | `Eavesdropper.pause()` and `skip_ahead()` built; called by the Conductor |
| Input (keyboard) | Planned |
| Conductor and entry point (`python -m pom`) | Built; burn-tested on the Mac with real servers |

### Half-duplex

The machine does one thing at a time. While the oracle model writes (and while the typewriter prints), nothing is sent
to whisper or the scorer model. They stay loaded in memory, idle, so there is no reload cost, and the Printer does not
hear itself. Lockfiles are avoided (they can lock the system out). The enforcement is the FloorKeeper's four states
(listening, writing, printing, recovering) with a watchdog so a stuck state cannot deafen the Printer for
good (see "The floor and the watchdog" below). The memory for all three models at once was measured on the Mac at about 10 GB (the 9B oracle model 6.7 GB,
the 4B scorer model 3.1 GB, whisper 0.33 GB). On the Pi, with all three loaded and idle, at least 6.7 GB of the 16 GB
stayed available.

### All the models stay loaded, always

**Decision (Wes's; restated 2026-11-03 because it was forgotten): whisper, the scorer model and the oracle model are all
resident in memory at the same time, for as long as the Printer runs.** That is why the Pi is the 16 GB one. Half-duplex (above) is
about *compute*: nothing is sent to whisper or the scorer while the oracle writes, so they never compete for the CPU. It is not about
memory, and nothing is ever unloaded to make room. Consequences, so they are not reopened by accident:

- No model is loaded on demand, swapped, or started only for its turn. A reload costs 12 to 28 s for the scorer and more for the oracle,
  and the first word after a hinge must not wait for it.
- The context sizes, model sizes and quantisations are chosen to fit together in 16 GB with room to spare (measured: all three idle, at
  least 6.7 GB of the 16 GB stayed available on the Pi). They are not traded against memory pressure; if the sum ever stops fitting, that
  is a design question for Wes, not a setting to quietly lower.
- The servers outlive the Printer: restarting `python -m pom` leaves the models warm.

### The servers are services

(*Agreed 2026-11-03; the units are not written yet.*) On the Pi, systemd runs each server as its own unit: `pom-whisper.service`,
`pom-scorer.service` and `pom-oracle.service`, each `Restart=always`, each logging to journald (which rotates the logs). `pom.service`
runs after them with `After=` and `Wants=`, never `Requires=`: a server that crashes or restarts must not take the Printer down, and the
Printer already copes with a missing server (it says so on the Monitor page, keeps going, and recovers when the server answers). The
ports and model paths are in the unit files and the ports are also in `config/pom.toml`; the unit files live in the repo
(`scripts/setup/`) and reach the Pi through git like everything else. On the Mac, `scripts/dev/pipeline/run_printer_with_servers.py` is the
test bench that starts the same three servers, runs the Printer on them, and stops what it started.

### Pausing the ears, like a tape deck

(*Built*, in the Eavesdropper.) The Conductor calls `Eavesdropper.pause()` the moment a turn takes
the floor and `skip_ahead()` when it yields it. While paused, the listening thread keeps reading frames from the deck but throws them
away and asks whisper nothing, so the microphone's buffer never overflows (no false "dropping sound" warning each turn); a segment
whisper had transcribed but not yet handed on is dropped as well, and the HingeFinder stops scoring the rest of the take being cut, so
nothing reaches the scorer model either. `skip_ahead()` ends the pause with a clean slate: the cutter forgets its audio and any
half-finished segment, the repeat memory is cleared (so the same words said after the turn are heard), and the cutter's clock moves
forward by the sound that went by unheard. Decided 2026-11-02: the turn's time counts, so audio time keeps matching real time and the
60-second cooldown and the bar's memory run through the turn (otherwise a 60-second turn would stretch the cooldown to two minutes).
Both calls only set a flag and return at once, from any thread; the listening thread does the dropping, so no state is shared.
A whisper request already in flight when the pause begins finishes first and its result is dropped.

### The floor and the watchdog

(*Built*: the `FloorKeeper`, `src/pom/conductor/floor_keeper.py`.) One object knows who may speak. The floor is in one of four states and a turn
moves through them in order: listening, writing, printing, recovering, and back to listening. The first of the room and the keyboard to ask
gets a `Turn` (first come, first served); the other is refused. A `Turn` is used in a `with` block, so the floor is yielded even when the
work fails. The recovery is a few seconds with the ears still shut, so the terminal's last sounds die away before the Printer listens.

*Design rule: the Printer fails toward speaking.* A bug here may cost one missed turn; it must never hold the floor for good. Every way
a turn can end (it finished, it raised, it ran too long) goes to recovering and then listening; nothing waits on anything; the lock
has a timeout; every call first settles anything that has run out ("lazy expiry"), so no ticker thread has to survive for the watchdog to
work; the clock is monotonic, since the Pi has no battery clock and its wall clock jumps when the network sets it. A turn the watchdog
has cut off is harmless when it wakes up later: its `Turn` is stale and its calls do nothing. The Conductor will check once a second that the
ears follow the floor (if the floor is listening and the Eavesdropper is still paused, call `skip_ahead()`), so a missed event cannot
leave the Printer deaf.

*The limits must cover the longest likely run, not the average* (decided 2026-11-02). The consequences:
- **Too short:** a healthy slow turn is cut off while it is still working. The floor goes back to listening and, after the recovery, the
  ears open while the terminal is still typing, so the Printer can hear itself and react to its own printing. A limit near the average would
  cut off exactly the long portents, and the Printer would say only short and medium ones.
- **Too long:** a turn that is really stuck (a hung request, a dead serial port) keeps the Printer deaf until the limit runs out.

The cost of too short is a wrong answer; the cost of too long is a quiet spell. So the limits err long: writing 300 s, printing 180 s, recovery
3 s (all to be settings in `[conductor]`). The watchdog cannot stop a thread that is stuck, only take the floor back, so the TurnTaker must give
every blocking call a timeout of its own; the watchdog is the last line of defense, not the first. Everything is logged (every change of state
with who holds the floor and how long the last state lasted, each refused request, each overrun, each late call from a cut-off turn): labels and
seconds, never words.

- *The writing limit must be longer than the oracle client's own timeout* (`[oracle] timeout`). That timeout is for the whole reply (the request
  is not streamed), and writing runs at about 0.5 s a token, so 120 s covered only about 230 tokens: a 150-word portent (about 200 tokens) plus
  the prompt was close to it, and `max_tokens` (300) needs about 155 s. Decided 2026-11-02: the timeout is 240 s, so the oracle gives up first,
  the watchdog stays the backstop at 300 s, and printing (180 s) covers the longest portent at the terminal's pace.

### A turn: the TurnTaker

(*Built*: `src/pom/conductor/turn_taker.py`.) `take_turn(turn, attempt)` takes the Turn the FloorKeeper handed out and the attempt that
qualified through four steps: write the portent, record the turn in the TurnHistory (at once, before any printing, so the monitoring site
shows it even while the printer is being waited for), format it and type it, and say how the turn ended. It runs inside `with turn:`, so the
floor is always yielded, and it never raises: an unexpected error ends the turn with the outcome `error`. Nothing said in the room, and
nothing the oracle wrote, is logged or put in a report; only numbers and labels are.

*Printing has priority over listening* (Wes, 2026-11-02). The Printer listens only to find something to print; a portent costs minutes of
the oracle's time and they come every 6 to 8 minutes, so there is no hurry back to listening, and a portent that has been written is printed
(even one that finished after the watchdog took the floor: that is logged as a warning).

- **The oracle gives nothing** (server down, slow, answering oddly): the turn tries again, up to 3 attempts 10 s apart, but only while a
  whole attempt (the oracle's 240 s timeout) still fits in the writing budget (270 s, under the watchdog's 300 s). A quick failure is retried;
  a timeout is not. Still nothing: outcome `no portent written`, back to listening. Three turns in a row: a CRITICAL fault says the oracle looks broken.
- **A portent grows stale.** It answers one sentence, and a few minutes later the room has moved on. A portent is worth printing for
  `freshness_seconds` (6 minutes, counted from the start of the turn, a little under the usual gap between portents). Past that it is dropped,
  its record says `too late to print`, and the Printer goes back to listening. The check is made before each try, never in the middle of
  typing: a portent that has begun is finished.
- **The terminal cannot print yet** (unplugged, held, no paper, port gone): nothing was typed, so the turn waits 10 s and tries again, until the
  portent goes stale, showing `waiting for the printer` on its record. The ears stay shut: with no printer there is nothing to listen for.
  A heartbeat (`Turn.still_printing()`, every 5 s) tells the FloorKeeper it is waiting and not hung, so the printing limit (180 s) does not
  cut off a wait. The moment the terminal works, the portent is typed. Whether the CadetWriter signals "held" or "out of paper" is for a test on
  the real machine; if it gives no signal, software cannot see it.
- **The terminal fails halfway**: part of the portent is on the paper, so typing it again would repeat the start. Outcome `print failed`, a
  CRITICAL fault, no retry. (The TerminalPort waits out a held or lost port itself and goes on where it stopped; see Printing.)
- **No printer by design** (`terminal` is `None`): write, record, done; the outcome is `not printed (no printer)`. The Printer can be
  watched listening and writing, with everything on the monitoring site, with nothing attached.

### The keyboard's ear: the KeyboardWatcher

(*Built*: `src/pom/conductor/keyboard_watcher.py`.) The typed-line twin of the HingeFinder: it reads each finished line from the terminal
(`read_line`, with a one-second poll), has the TypedLineScorer score it and the BarKeeper rule on it, and yields the attempt; the one that qualified is
the hinge, exactly as for the room. It has the Eavesdropper's two calls, `pause()` when a turn takes the floor and `skip_ahead()` when it is yielded: a
line that finishes during a turn is still read (so the terminal's buffer stays empty) but is dropped unscored and counted, so nothing reaches the scorer
model mid-turn. Empty lines are skipped. A terminal or a scorer that raises is one warning naming the error type and a short pause, never the end of the
watching; only `close()` ends it. What was typed is never logged or put in a report.

### The Conductor and the entry point

(*Built*: `src/pom/conductor/conductor.py` and `src/pom/printer.py`; `python -m pom`.) The Conductor runs the Printer on four ordinary threads. The
**room** thread runs the HingeFinder and offers each attempt; the **keyboard** thread runs the KeyboardWatcher (only with a real typewriter); the
**turn** thread takes each hinge through a turn with the TurnTaker, one at a time; and the thread that called `run()` keeps watch every second.

- *First come, first served.* An attempt that qualified asks the FloorKeeper for the floor. The first to ask gets a Turn and the other hinge is
  dropped, with a line in the log. The turn goes to the turn thread, never run on the one that found it: the room thread must go on reading sound
  (which the paused Eavesdropper throws away) or the microphone's buffer would overflow while the Printer is busy.
- *The ears shut the moment the floor is taken* (the thread that took it pauses both ears at once), and *open again by looking, not by being
  told*: every second the watching thread compares the floor with the ears, and if the floor is listening and the ears are shut it calls
  `skip_ahead()`. So a turn that ends any way at all, including the watchdog cutting it off, cannot leave the Printer deaf.
- *The room is built again each time it ends.* The entry point gives the Conductor a `room_factory` that makes a fresh deck, Eavesdropper and
  HingeFinder (the BarKeeper and the scorer are shared, so the memory of the room's scores survives). A microphone that is unplugged ends the
  HingeFinder; the Conductor builds a new room after `restart_pause_seconds`, again and again, so plugging the microphone back in is all it
  needs. A recording that ends is the end of the run. A thread that dies unexpectedly is started again by the watching thread.
- *It starts whatever state the world is in.* At boot the servers, the microphone and the printer come up in no particular order and nobody is
  there to restart anything. A server that does not answer yet is a line in the log; each client recovers when its server arrives, a turn waits
  for the printer, and a monitoring site that cannot start (no password, port taken) is a warning, not a stop.
- *Settings.* `[conductor]` in `config/pom.toml` holds every limit (the watchdog's, how long a portent stays worth printing, the oracle retries);
  `[terminal] mode` is `serial` (the typewriter), `console` (the screen) or `none` (no printer); options on the command line (`--wav`, `--mac-mic`,
  `--terminal`, `--no-keyboard`, `--config`) choose for one run. `Config` checks the limits against the settings they depend on and warns when a
  watchdog is shorter than what it watches (the values are kept; the person is told).
- *Stopping.* SIGTERM (systemd) and ctrl-C stop it cleanly: the ears shut, the keyboard closes, each thread is waited for at most 5 seconds. A portent
  being typed for longer than that is cut off when the process exits (seen in the burn test), so restart the service between portents if it matters.

**Burn test (2026-11-02, the Mac, real servers).** whisper-server, the scorer (Qwen3-4B) and the oracle (Qwen3.5-9B) were run for real. A 5-minute
recording and a 71-minute one at 6x speed gave turns back to back (memory steady at about 120 MB, 5 threads, no warnings). The oracle was killed
mid-run: three attempts 10 s apart, the turn dropped, listening again, and when the server came back the next turn simply worked. whisper was
killed before the start: the Printer ran, reported it, and heard again when it returned. A stand-in typewriter (a pseudo-terminal opened by the real
pyserial) was unplugged mid-portent and replugged under another device name: it resumed at the exact character it stopped (`...so real an` then
`d whole, And holds...`) with nothing repeated or lost; a printer absent at the start was waited for and used when it appeared; a line typed on the
stand-in's keyboard scored 8.5 against a bar of 6.8, took the floor as the keyboard, and its portent was typed back. SIGTERM during a turn exited in
about 7 seconds. What the burn test could not show: the real CadetWriter's flow-control behavior (hold, out of paper), a real microphone being
unplugged, and the Pi's own speed.

## Listening: from sound to text

1. **Microphone** (*built*, `MicDeck`): listens all the time through `arecord` and hands audio on in 30 ms frames. It reads in a thread of its own into a 10-second buffer, so a slow whisper run cannot lose sound; if the buffer fills, the oldest frames are dropped and counted. A recording can be played instead (`WavDeck`), for tests and replays.
2. **Cutting** (*built*, `RollingCutter`): whisper decides where the speech is. Every 5 seconds a whisper run covers a rolling
   buffer with whisper's own Silero voice detection on (threshold 0.5); the segments whisper has finished (held back
   2 seconds so a sentence is not cut mid-thought) are kept, and the buffer never grows past 30 seconds. This replaced
   cutting by loudness (a slowly moving noise floor and a 0.7 s pause), which never found a pause in a room with bursty
   noise; the loudness cutter survives behind `--energy-cutter` in the experiment only. Time is counted in audio received, so
   a recording replays exactly as a live stream would, at any speed.
3. **Transcriber** (*built*, `WhisperModelClient`; whisper.cpp v1.9.5 as `whisper-server`): turns the audio into punctuated text with
   segment times. Model `base.en`; `small.en` is slower (about 0.5 times real time against 0.2) for almost the same
   text on speech.
4. **The track split** (*built*, `TrackSplitter`). Whisper's output is sorted before anything is scored:
   - *Words* go on: speech, and sung lyrics with the music symbol stripped. The Printer reacts to what it hears, not
     only to conversation, so lyrics compete on score like any other sentence.
   - *Room tone* such as "(dog barks)" or "[Music]" is never scored (a hallucinated "(gunshot)" scored 8.0).
     They are kept as a short list of recent sounds, to give the oracle model as context (*proposed*).
   - *Blanks* such as "[BLANK_AUDIO]" are dropped.
5. **TakeCutter** (*built*): cuts the words into clips stamped with a time. Sentences are what get scored;
   clauses and phrases remain available behind a setting (*proposed*: a config setting for which levels it emits).
6. **Eavesdropper** (*built*): joins the microphone, the cutter and the track splitter. `hear()` is a plain generator the Conductor
   will run in a thread; it yields a `Take` (the new words and their kind, the new room tone, and where in the stream
   it ended) for each segment that holds something new, skipping repeats and remembering the last six bits of room tone. After three
   rounds in a row with no speech it asks whisper once, without the voice detector, for lyrics and room tone. Whisper being
   down is one warning for each outage; the audio is kept and tried again. *Known limit, inherited from the experiment:*
   that pass hears about two seconds, not ten, because a stretch with no speech is trimmed to its last two seconds first,
   so lyrics sung between passes are mostly missed. Fixing it means the Eavesdropper keeping its own recent audio.

**The known-good hearing setup.** The only combination that worked well in the room, and it is fragile: on `pom1`,
`./probe_hearing_run.sh --no-explain` from `experiments/hearing-debug`, whisper `base.en`, 4 threads, VAD threshold 0.5,
mic gain 24, the 4B scorer model on port 8098, the committed config. Change one thing at a time from here. The values
that belong to the listening side are the `[listening]` section of the config, and a test pins the committed file to them.

## Scoring: how interesting is a clip?

**The Scorer** (*built*, `src/pom/scoring/`) gives each sentence a score from 0 to 9. It replaced the first plan
(a weighted sum of word lists). Its pieces:

- **clip_filter**: the word tagger (NLTK) labels each word as a name, number, filler, stopword or content word. A
  clip with too few content words ("or not.") skips the model and gets a fixed low score (`clip_filter_score`,
  1.0), which saves a model call on talk that cannot be interesting.
- **Scorer model**: a small local language model, Qwen3-4B (Q4_K_M), served by `llama-server`. Llama-3.2-3B is the
  faster alternative. The prompt is human-written and kept in config; the definition of "interesting" is kept separate from the
  meaning of the numbers, and there is no persona.
- **One sentence per request, no context.** A sentence scores the same whatever came before it. Batching and showing
  earlier lines were both measured and rejected: each made a score depend on its neighbours and their order.
- **The score** is the average of the digits the model might answer with, each weighted by how likely the model thinks
  it is: 75% sure of 6 and 25% sure of 7 scores 6.25.
- **Unscored**: if the model does not answer, the clip has no score (None), and the BarKeeper leaves it out. A
  circuit breaker stops asking after `failure_limit` (3) failures and tries again after `retry_seconds` (30). The
  Printer stays quiet and keeps listening. (A word-counting fallback is shelved, unused, in `fallback_scorer.py`.)
- **Warm-up.** The first request after the server starts takes 12 to 28 seconds on the Pi, so startup warms it first.

**What the scores look like** (4,278 sentences from four recorded meetings): they are lumpy, not bell-shaped. About 27%
sit at 1 (the clip_filter's 497 plus the model's own), with humps near 3 and near 5, and almost nothing above 7
(0.9% reach 7; the 99th percentile is 7.0). There is no long tail of outliers, which is why a fixed high threshold
was abandoned and why the textbook outlier rule fails here (see Qualifying).

**On the Pi** (`experiments/pi-benchmark/`): with whisper running flat out on shared cores, the 4B scorer model scores a
sentence in a median 2.3 s (95th percentile 3.2 s), uses about 3 GB, peaks at 72 °C with no throttling, and slows
whisper about 2.3 times, which is still real time.

**Retired:** the first Scorer was a weighted sum of word lists (corpus snag by `wordfreq`, WordNet concreteness,
NRC-VAD feeling, names/numbers/questions, filler and recent-use penalties). The model proved the better judge. The
word list design remains a documented fallback in `project-state.md`; the NRC-VAD file is not redistributable and is
downloaded per machine.

## Qualifying: when does the Printer speak?

**Built**: `BarKeeper` (`src/pom/qualifying/bar_keeper.py`), settings in `[qualifying]`. The way the bar is worked out is selectable
(`algorithm`): the percentile rule described below (`PercentileBar`, the default), a decaying quantile (`DecayingQuantileBar`, EWMQ), or the first
method, a decaying moving average (`MovingAverageBar`). The BarKeeper keeps what they share (the qualifying decision, the cooldown, the clock) and a small
factory builds the bar from the settings. Every bar starts at `starting_bar` and slides to the room's own level, so there is no warm-up silence.

The rule is a percentile of the room's recent scores. It makes no assumption about their shape:

- **The window**: the scores heard in the last `window_seconds` (300, five minutes), or the last `min_scores` (20)
  if the room is so quiet that fewer were heard in that time; whichever is larger. It is a length of time, not a count,
  so a busy room and a quiet one both get "the last five minutes". Every score joins it, the lowest included. There is
  no floor.
- **The bar**: the `percentile` (99.5) of the window's scores, by nearest rank: line the scores up and take the lowest
  one with at least that share of the window at or below it. It is always a score the room really earned. At 100 it is the
  best score of the window, so a sentence has to beat everything said lately; at 99.5 about one score in two hundred stands
  above it; at 90 it is the top tenth.
- **Qualifying**: a score qualifies when it is strictly above the bar as it stood before that score joined, the window
  holds at least `min_scores` scores, and at least `cooldown_seconds` (60) have passed since the last qualifier. A score
  above the bar inside the cooldown is held back (it still joins the window and does not restart the cooldown).
- **Time is passed in**: `record(score, now)`. Live it is a clock reading; in a replay it is the place in the
  recording, so a recording gives the same answers faster than real time. Unscored clips are ignored.

**Why this rule.** The first design was a threshold that moved with the room: 25% above a decaying average of the
recent scores, plus a floor below which scores were ignored. Replayed on four recorded meetings (307 minutes,
about 835 sentences an hour) it fired on 34% of sentences, one every 13 seconds, and turning `trigger_fraction` up
barely helped (35% gave 30%, 50% gave 23%). An average sits in the middle of the scores, so a good share are always
above a bar set just over it. The second design was Tukey's fence (a bar at Q3 plus a multiple of the interquartile
range). It does not suit these scores: they are lumpy, a pile at 1, humps near 3 and 5, almost nothing above 7, so the
spread between the lumps stretches the interquartile range and the bar lands above the real top. At a multiple of 0.8
the bar was above the best score of its own window in two windows out of three, it reached 9.85 on a scale that ends at
9 in a live call, and it gave 12.5 qualifiers an hour in a live room against 4 to 9 in the Zoom meetings. A percentile
measures where the top of the window actually is instead of predicting it from the middle. 100 (beat the best of ten
minutes) fit the recordings best, but one lucky high score then holds the bar up for the whole window, so the shipped
setting is 99.5 over five minutes, chosen by hand to make the Printer answer more often (9.8 an hour on the recordings,
against 6.7 with twenty minutes). 99.5 only differs from 100 once the window holds about 200 scores, and a busy five
minutes holds about 75, so with this window it is still the best score of the window: below that size the rate follows the
window, above it how much the room talks (about one sentence in two hundred). The cooldown exists
because one meeting had six qualifiers in eight seconds (a run of one-line jokes).

**Result with the shipped settings** (5 recorded meetings, four Zoom calls and one live room, 7.1 hours): about 6.7 qualifiers
an hour, one every 9 minutes, between 3.4 and 9.3 an hour by meeting (the live room is the 9.3: 15 qualifiers in 96 minutes,
in bursts, with a 27-minute gap). 100 with ten minutes gave 6.4 (4.0 to 8.1). The goal is about 6 an hour. The settings were tuned on these recordings only; a quiet real room is still the open test.

**Principles behind it.** Absolute scoring is the enemy of emergence: thresholds should come from what is happening in
the room, not from a constant. The filters must work reasonably well in every room, not perfectly in one; nothing is
tuned per room.

### What qualifying must do

Whatever rule is used, it is judged against these. They came out of the replays and the arguments about them, and they are the
reason some rules were dropped.

1. **Two good qualifiers can follow each other.** A strong qualifier must not hold the bar so high that an almost-as-strong one cannot
   fire soon after it. (An 8.9 should not shut out a 7.2 that is still well above the room's usual high tide.) The cooldown is the
   only thing allowed to space them.
2. **Responsive to room heat.** Any qualifying scheme must respond to changes in the room's heat in the five-to-ten-minute range. Rooms
   change tenor on that time scale, so the memory behind the bar has to be about that long, and a bar that adapts more slowly stops
   being responsive.
3. **A quiet room is not silenced by one good qualifier.** With few scores in the window, one strong score must not hold the bar for the
   better part of an hour.
4. **Nothing absolute.** No fixed floor, no score that counts everywhere, nothing tuned for one room: the bar comes from what the
   room is doing.

Preference, tuned and not required: about six qualifiers an hour on average, at random. Timing should not be evenly spaced: qualifiers may
bunch, several may come in the first twenty minutes, and a quiet stretch may follow. The average is a setting to tune, not a test a rule
must pass.

Practical choice, not a requirement: a short cooldown (60 s) stays, because a run of one-line jokes clears any bar one after another
and one meeting had six qualifiers in eight seconds. It is the only thing allowed to space two good qualifiers (see 1).

Where the current rule stands: a short window with a high percentile is really "beat the best of the window", which fails 1 and
makes the timing too regular, and a long window fixes those and fails 2 and 3. A decaying memory (exponentially weighted quantiles)
meets 1 and 2 better and still leaves a quiet room silent for long stretches after a strong qualifier. A rule that meets all four is
still being looked for.

### Bar rules we tried

The same question every time: given the score of a new clip and the scores of the room lately, is this one worth answering? Each
rule was replayed on the recorded meetings before it was kept or dropped. In order:

**1. A fixed threshold** (a score of 7 or more).
- *Worked:* it is the simplest rule there is, and it is easy to explain and to predict.
- *Did not:* the number means the same in every room, so a lively room fires constantly and a dull one never does. It is the
  absolute scoring the project is trying to avoid.

**2. A moving average** (a score a fraction above a decaying average of recent scores, with a floor under which scores were ignored).
- *Worked:* the bar follows the room, and it needs only a few lines of state.
- *Did not:* an average sits in the middle of the scores, so a large share of them are always above a bar set just over it. It fired on
  about a third of sentences, and the setting that was meant to control the rate barely moved it. The floor was a hand-picked constant,
  and it stood for a decision (what counts as too dull to count) that nothing in the data had made.

**3. Tukey's fence** (Q3 plus a multiple of the interquartile range, over a timed window).
- *Worked:* it is a standard test for "stands out", it needs no floor, and the timed window gave a sensible sense of "the room right now".
  A cooldown handled bursts. At the textbook setting it was very strict, and a lower multiple brought the rate to about the goal.
- *Did not:* it predicts where the top of the scores should be from the width of the middle half, and that assumes a bell shape. The
  scorer's numbers are lumpy: a pile at 1, humps near 3 and 5, almost nothing above 7. The pile stretches the spread, so the bar often
  landed above the best score the room had actually produced, and once above the top of the scale, where nothing can qualify. The rate
  also swung from room to room at one setting.

**4. A percentile of a timed window** (the current rule: beat a high percentile of the last few minutes).
- *Worked:* it makes no assumption about shape. It measures where the top of the window is instead of guessing. The bar is always a score
  the room really earned, so it can never leave the scale, and across rooms the rate stayed closer to the goal than the fence did.
- *Did not, or not yet:* with a short window it is really "beat the best score of the window", so a qualifier's own score becomes the bar
  and a strong qualifier is followed by a long quiet stretch; one lucky high score holds the bar up. The rate is set mostly by the window's
  length and not by how much the room talks, until the window holds a couple of hundred scores. Qualifiers arrive at random, so they can bunch
  and they can leave long gaps.

**5. Double MAD** (a median, and a spread measured only from the scores above it).
- *Looked promising:* the pile of low scores sits below the median and cannot widen the upper spread, and medians are robust.
- *Found in a replay, not built:* with the proposed multiplier the bar was above the scale in more than half of the windows, because the climb
  from the median to the top is large on these scores, so the same dead zone came back. The rate varied several-fold between rooms, so no one
  multiplier suits them all. It needs the cooldown and a rule for the first scores that the first draft left out, and its small floor on the
  spread does not protect a window that is mostly the lowest score. Still under consideration.

**What the attempts taught**
- The scores are bounded and lumpy, so any bar worked out from the middle of the scores tends to overshoot the top. A bar that measures the top
  directly behaves better.
- The rate of qualifiers is a result, not a setting. Every rule trades how often it fires against how much that depends on the room, and
  there is no neutral point: a rule that follows the room's talk fires more in a lively room by design.
- A cooldown is needed whatever the rule: a run of one-line jokes clears any bar one after another.
- Every rule inherits the scorer's own quirks (an odd stretched spelling can score high), because the BarKeeper only ever sees numbers.
- All of this was tuned on four video-call meetings and one live room. A quiet real room is the test that is still to come.

**Not built, and not decided:**
- (proposed) *Paper guards*, so nobody comes back to a pile of paper: nothing qualifies unless speech was heard in the
  last 10 minutes (this also keeps whisper's phantom phrases from printing in an empty room, and matters because the
  window can reach back past hours of silence), and hard ceilings of about 12 prints an hour and 60 a day as a last
  resort. The activity measure for the quiet timeout would be seconds of accepted speech per minute from the
  Transcriber, not loudness.
- (open) The retired design had a target print rate that followed room activity (0 silent, about 4 with a little
  talking, about 8 busy) and a rate governor. The fence plus the cooldown replaced them; the rate is now a result to
  watch, not a quantity steered. If a real room shows the rate drifting, a governor can come back.
- Typed input has its own way in (the TypedLineScorer, not the Eavesdropper): the Scorer scores the line, `typed_handicap` (1.5, uncapped) multiplies it, and the same bar judges it. No cooldown, and a line under the bar is silently ignored. By default it stays out of the bar (`typed_lines_affect_bar`), which listens to the room.
- (open) Everything above is tuned on four chatty meetings. A real room, especially a quiet one, is the real test.

## The hinge

- The hinge is the clip the portent answers: a word, a phrase or a topic (in practice a sentence).
- Its connection to the portent is tangential and lyrical, not a direct answer and not random. Examples: talk about
  dogs gets an insult in the manner of Shakespeare calling someone a dog; something called weird gets something in
  the spirit of Blake on the uncanny; complaining about friends gets something about gossip.
- The leap is made by the oracle model, not looked up. A library lookup was rejected as too literal.
- **The hinge is the sentence that qualified** (decided 2026-10-27). The scorer and the BarKeeper (the percentile bar with its
  cooldown) were built to hand over about six sentences an hour, so the one that clears the bar is used as it
  occurs. If that is a little inconsistent, scaring some hinges and catching on others, that is what talking to an
  oracle means. The first design's weighted random pick among the best recent candidates is dropped. The hand-off
  (the qualifying clip to the PromptBuilder) is the Conductor's job.
- (open) *Defining "interesting".* Conclusion of 2026-10-22: the prompt and the BarKeeper keep circling because the project
  has not defined for itself what is worth answering, and the algorithm was left to choose in the hope it would work out. A
  human definition comes first; the scorer prompt then states it. Deferred, not dropped.

## The oracle model

**Built**: `PromptBuilder` and `OracleModelClient`; the model is served by `llama-server` (default port 8099).

- **The prompt is human-written.** It lives in config (`oracle.prompt`), with `{exemplars}` and `{hinge}` marking where those
  go. It asks for a new passage in the manner of the style example without printing the theme or the example, under
  150 words. Models are used to measure prompts, not to write them.
- **Exemplars** (shelf exemplars): each time the Printer speaks, the Bibliomancer draws passages from the
  weighted library, independently and never matched to the hinge. They show the voice and nothing more. The bake-off and
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
the Pi (an estimate, not measured). Caveats: one rater (an LLM judge); a human spot-check of about 20 ratings is pending.

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
- The wait was judged acceptable (2026-10-27): the first word after about 20 s, a typical portent finished in 35 to
  40 s and a long one in up to about 100 s, then 5 to 20 s of typing. (open) Why the server caches none of the prompt;
  fixing it could cut the 20 s wait.

### Two oracle tracks (Track B not started)

The first design had two ways of writing behind one interface: Track A, a stock instruct model with a prompt, and Track B, a
base model fine-tuned (LoRA, on the Mac with MLX, never on the Pi) on the library plus curated hinge pairs, run with an
almost empty prompt so that it cannot speak outside its shelves. The bake-off is Track A: stock models, the project's own prompt.
Track B and the curated hinge pairs (an overheard line paired with a tangential in-shelf reply, curated by Wes,
Neil and River with a large model drafting candidates, logged in the AI-use ledger) have not been started. (open)
Whether to start them depends on how far the prompted oracle model gets.

## Library

- 81 holdings in 14 shelves, about 72,000 passages of 15 to 150 words (the 14th shelf, `nonsense`, was added to test
  how hard growing the library is: easy). Public on GitHub.
- `library/ready/` holds clean utterances, one per paragraph, with no trace of their holding. `library/passages/` turns them
  into JSONL passages. Everything is built by scripts from the manifest `library/holdings.csv`, so it is repeatable.
- Shelves are *what the oracle might say*, not where text came from. Draws are weighted: every utterance is in one
  pool and has its own chance, a shelf with few utterances gets a heavier weight per utterance, and the shelf
  shares start equal. Nothing is chosen category-first, so any combination can come up, including five obituaries in a
  row; it is just rare. `library/passages/circulation.json` is the one hand-edited file for shares and holding weights (for
  example Shakespeare's plays 0.2). Draw odds are computed at load time, never stored.
- Rights: public domain, plus `owner-cleared` material (the IBM/Lexmark Wheelwriter manuals and the dad jokes), decided
  by the project owner. Licensing and content are not concerns for this project: it is an art installation, far from
  the consumer market.
- **Unfinished exemplars are not drawn** (found 2026-11-03). The prompt tells the model to match its exemplar's form, tone and length, so it
  also copies the exemplar's ending: a passage that stops at "...is the", or is a lead-in ending on a colon ("The following bugle signals
  may be used:") with its list gone, makes a portent that stops the same way. This was the "cut-off portent": the writer's text, the formatter
  and the printing were all intact; the exemplar was the cause (the oracle server showed a normal stop at 22 tokens). 1,059 of the 72,264
  passages (1.47%) end unfinished (918 on a colon, 144 on a word like "the" or "of"; `pom.library.endings` is the one definition). Two
  fixes, both on purpose: the Bibliomancer draws again when a pick lands on one (`[oracle] exemplar_redraws`, 50 tries a pick, 0 is off; the odds among
  the finished passages are unchanged), and the library pipeline stops producing them. Verse and comma or semicolon endings are left alone.
- (open) Equal shelf shares make small shelves repeat. A simulation showed repeats are negligible over an evening
  (0.1%) and matter over months; a recency decay was prototyped and deliberately left out.
- Adding material: `adding-material.md`. How the standardizer works and what went wrong: `library-notes.md`.

## Printing

- Built: the `PortentFormatter` (`src/pom/printing/`) makes a portent printable. It takes out the triple-backtick fences
  (the prompt shows the model its exemplars and the hinge in fences, so it may echo them); a fenced piece is a quotation and
  is typed indented four spaces with a blank line on each side, since the typewriter has no italics. It swaps curly quotes,
  dashes and accents for plain ASCII and drops what has no plain form, wraps to the line width, and ends the text with
  `linefeeds_after_portent` line feeds (4) so the paper has moved on for the next portent. Most wheels lack nine ASCII characters
  (`< > \ ^ ` { | } ~`); the CadetWriter builds them from periods, slowly. `<` and `>` are left to it; the other seven are swapped for
  a look-alike (`'`, `-`, `( )`, `/`) unless `keep_composed_characters` says otherwise, and `ascii_printwheel` keeps all of them. The line width is worked out
  in `[printing]` from the paper width, the margins and the wheel's characters per inch (8.5 inch paper with 1 inch margins:
  65 characters at 10 cpi, 78 at 12, 97 at 15; 10 is the default, the widest letters, so it fits any wheel).
- Built: the `TerminalPort` (`src/pom/printing/terminal_port.py`, config `[terminal]`) sends the formatted portent over serial, paced (16
  characters a second to start; to be measured), with flow control, CR+LF line endings and only safe bytes. `type()` blocks until the
  machine has had time to finish, so the Conductor pauses listening, calls it, and resumes (half-duplex); the port knows nothing about
  listening. The same port reads the keyboard: `read_line()` returns what someone types when they press C Rtn. In the CadetWriter's default
  full-duplex mode the Pi echoes each key (`echoplex`), a Backspace overstrikes but edits the line, and keys pressed during a portent are
  thrown away (unless `type_during_printing` is on). The `ConsoleTerminal` is the same code on the terminal, for development.
- Built (2026-11-02): *the TerminalPort stays with the portent*. A serial printer is fickle (switched off, unplugged, held, out of paper), so
  once typing has begun a failed write does not end the portent. A **stall** (the machine stopped taking characters; pyserial raises a write
  timeout under flow control) is waited out and the same character is sent again; a **lost port** is closed and opened again every
  `reconnect_seconds`, and typing goes on from the first character not sent. Either wait lasts at most `stall_patience_seconds` (600), sends the
  caller's heartbeat every few seconds, logs one warning for the stretch and a reminder each minute, and ends with a log line when typing goes on;
  the pacing restarts from that moment. The `port` may be a pattern (`/dev/ttyACM*`, `/dev/serial/by-id/usb-*`) looked up at each open, so a
  printer replugged under another name is found. A port that will not open at the start returns at once; the TurnTaker waits for it (up to the
  freshness limit). *Not visible to software:* whether the CadetWriter signals a hold or out-of-paper through flow control (to be tested on the
  machine), and a printer powered off behind a USB adapter that swallows the bytes. A character or two in flight at an unplug may be lost or doubled.
- The TypedLineScorer (`src/pom/keyboard/`) turns a typed line into a `Attempt` (kind `typed`), judged by `BarKeeper.rule_typed()`. Serializing its calls with the HingeFinder's thread, and handing a typed hinge to the oracle, are the Conductor's.
- The output validation hook (`scripts/dev/library/validate_output.py`) is a deliberate stub. Checking for verbatim
  overlap with the library was judged the wrong measure: false positives on quotes that land, false negatives on
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
objects passed between them. Helper scripts (`scripts/`) import `pom`; `pom` never imports them. About 1,500 tests pass
(1,494 when this was written). Logic that needs no hardware is kept apart from code that touches the mic, a model or
the serial port, so most of it is tested on the Mac.

### Rules that cut across modules

- **The Printer never stops because of a typo or a recoverable fault** (a gallery cannot crash). A module recovers
  and records the problem in a `FaultRecorder` (WARNING = recovered, CRITICAL = cannot do its job; even then nothing
  raises). `strict=True` raises, for tests and dev tools only.
- Modules do not read `Config`. The entry point builds one `Config` and passes values in.
- Logs hold counts and scores, never words. One `debug` flag turns debug output on everywhere; each module can override
  it. Logging goes to stdout (journald collects it).
- (open) *Failing loudly.* If the scorer model or the oracle model is down the Printer can never respond, and a log
  line is not loud when someone is switching a printer on. Decide what loud means (a beep, an email to the project owner, something
  at the venue) and make those startup steps critical in that way. Today the scorer step is not critical and an
  unscorable clip is left unscored.

### Modules

```
src/pom/
  fault_recorder.py, log_setup.py, config.py     built
  startup.py                                     built runner; the ten startup duties are placeholders
  library/     catalog, circulation, bibliomancer                built
  listening/   audio_deck, mic_deck, wav_deck,                   built
               whisper_model_client, track_splitter,             built
               rolling_cutter, eavesdropper, take_cutter,        built
               audio_format                                      built
               mac_mic_deck (laptop testing)                     built
  scoring/     word_tagger, scorer_model_client, clip_scorer     built
               fallback_scorer                                   shelved, unused
  qualifying/  bar_keeper (window, bar, cooldown), attempt,      built
               the bars (percentile, decaying quantile,
               moving average)
  conductor/   hinge_finder (listening to hinge)                 built
               floor_keeper, turn_taker, keyboard_watcher, conductor   built; the Conductor is the only module that knows the whole pipeline
  printer.py   the entry point (`python -m pom`): builds everything from one Config   built
  keyboard/    typed_line_scorer                                 built
  writing/     prompt_builder, oracle_model_client,              built
               portent_writer
  printing/    portent_formatter, terminal_port                  built
               reply selection                                   planned
  monitor/     turn_history (the last hour, memory only),        built
               monitor_site (the web page, Flask, password)      built; not yet fed by the pipeline
```

### Startup duties (placeholders in `startup.py`)

Config report; data check (library, weights, NLTK data; critical); microphone gain; audio device; transcriber;
scorer server; scorer warm-up; oracle model server (critical); typewriter port (critical); one-line readiness summary
(counts and seconds only).

## What we have measured

| Experiment | Folder | What it settled |
|---|---|---|
| Interest scorer | `interest-scorer/` | One sentence, no context, digit-probability score; Qwen3-4B; a bigger model (8B) scores gentle-weird lines higher |
| Pi benchmark | `pi-benchmark/` | The 4B scorer model next to flat-out whisper on the Pi: median 2.3 s a sentence |
| Hearing debug | `hearing-debug/` | Whisper-driven cutting, the track split, the known-good setup, the qualifying replays |
| Oracle testbench | `oracle-playground/` | Hand trials of oracle models and prompts |
| Oracle bake-off | `oracle-bakeoff/` | Seven models, 160 rated portents each; Qwen3.5-9B first |
| Pi oracle benchmark | `pi-oracle-benchmark/` | The 9B on the Pi with whisper and the 4B idle: a typical portent in 35 to 40 s, the first word after 20 s |

The qualifying replays used four recorded team meetings (public by design, kept in gitignored `data/`).

## Build order

1. (Done 2026-10-27) The oracle model is Qwen3.5-9B; the wait on the Pi (about 40 s typical, 100 s long) is acceptable.
   Look into why nothing is cached when convenient.
2. (Decided 2026-10-27) The hinge is the sentence that qualified. The hand-off is the Conductor's.
3. (Done) The listening side is rebuilt in `src/pom/listening/`, leaf-first, each part tested and mutation-checked. Next there: try it on the Pi with the real microphone (`scripts/dev/listening/listen_to_room.py`).
4. Printing side: the formatter and the serial port are built; reply selection is next.
5. The startup duties (the readiness checks in `startup.py`), the systemd units on the Pi, then the paper guards.
6. Paper guards; "failing loudly".
7. Run it on the Pi; test in a real room (a quiet one too) and retune from that.
8. Later: Input (typed text), hinge pairs, Track B if wanted.

## Open questions, collected

- What is "interesting", defined by us before the model is asked.
- Why the server caches none of the prompt (fixing it could cut the 20 s wait); a prompt tuned for the top two oracle
  models; a human spot-check of the bake-off ratings. (Decided: Qwen3.5-9B, with Gemma 2 9B as the alternate.)
- How qualifying behaves in a real, quiet room; the paper guards.
- How the Conductor enforces half-duplex without lockfiles.
- How the Conductor serializes the typed path and the listening thread (the BarKeeper now has a lock of its own, so only the floor is left to decide).
- Mic placement and cable routing.
- What "failing loudly" means.
- Whether Track B and the hinge pairs are worth building.
- Real whispers: a very quiet aside in a very quiet room is treated today like any quiet sound (the voice detector may
  not call it speech). Revisit once the rest works.

## What changed from the first notes

- **Scorer**: word lists -> a small local language model (Qwen3-4B), one sentence per request, no context, a 0 to 9
  probability-weighted score.
- **Qualifying**: a pool of faded scores against a moving threshold with a rate governor, activity curve and no cooldown
  -> a percentile (99.5) of the last five minutes of scores with a 60 s cooldown and no floor.
- **Hearing**: cutting by loudness with a moving noise floor -> whisper's own voice detection on a rolling buffer; the
  track split of whisper's output (words, room tone, nothing).
- **Oracle**: two tracks behind an interface -> the oracle model, a prompted stock model with a human-written prompt (Track B
  not started); Qwen3.5-9B leads the bake-off.
- **Library**: about 69,000 utterances in 13 shelves -> about 72,000 passages in 14 shelves, passages of 15 to 150 words.
- **Whisper**: `small.en` -> `base.en` as the model that worked in the room.
- **Half-duplex**: deaf while printing -> nothing is sent to whisper or the scorer model while the oracle model writes
  or the typewriter prints; both stay loaded.
- **Hinge**: a weighted random pick among the best recent candidates, with a temperature -> the sentence that qualified.
- **Retired**: the activity BarKeeper and target-rate curve, the faded-score pool, the hinge picker, the Mimic splicing
  fallback, the word-list Scorer (kept as a documented fallback), and the license questions about wordfreq (no longer a dependency of
  the Scorer).
