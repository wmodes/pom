# Hearing debug

**Status: working prototype, for refining the prompts.** It is not part of the Printer. It is a live
window onto how the Printer interprets what it hears, built so the prompt can be improved in a closed loop.

## What it is

A small pipeline that runs on the Pi (or on the Mac against a recording):

```
microphone -> cut at pauses -> level normalized -> whisper -> sentences -> Scorer -> explanation
```

For every sentence it prints the real `pom` Scorer's score, and a little later the model's own written
account of why: the content it found that earned the score. Each score also goes to the real
`ConvoInterestTracker`, which keeps the room's recent scores, and a sentence that stands out from them (above
Tukey's fence for that window) is marked `<-- would trigger`. There is no fixed threshold: the bar moves with the room.

Every line ends with a note that says where the score stands, for example `[window 69 scores, bar 5.12]`: how many
recent scores the sentence was judged against and the score it had to be above (Q3 + `fence_multiple` x IQR of
those scores). `[warming up 3/20]` means the window does not yet hold enough scores for anything to trigger, and
`[window 69 scores, bar 5.12; cooling down]` means the sentence was above the bar but too soon after the last
`<-- would trigger` (`cooldown_seconds`), and `[no score]` means the sentence got no score and was left out. The settings are in the `[trigger]` section of
`config/pom.toml`, and the startup banner prints the ones in force. Reloading the config restarts the window,
because a changed prompt changes what a score means. The `r` command re-runs sentences without counting them
again. With `--wav` the window runs on the position in the recording, not the wall clock, so a recording replayed
fast (`--speed 0`) gives the triggers a live run would; the `[+hh:mm:ss]` on each "heard" line is that position.

The Scorer, its settings and its prompts are the real thing, read from `config/pom.toml`. The
microphone handling (cutting at pauses, level normalization, whisper) is a quick stand-in for modules
that are not built yet; if it works well it becomes the Segmenter and Transcriber.

## The closed loop

1. The prompts live in `config/pom.toml`: `prompt_scoring` (what counts as interesting, and the scale),
   `prompt_output` (how to reply when scoring) and `prompt_output_debug` (how to reply when explaining).
   Fast mode (`--no-explain`) sends scoring plus output; the default debug mode sends scoring plus debug output.
2. `probe_hearing_tune.sh watch` (on the Mac) delivers the config to the Pi every time you save it, **by git**:
   it commits each valid version to a throwaway branch, `prompt-tuning`, and pushes it. The Pi follows that branch
   with `git fetch` (see below). Nothing is copied, and your working tree stays on `main`.
3. The pipeline re-reads the config before each utterance, and says so when it changes, with a short
   fingerprint of each prompt so you can tell which version scored what.
4. Type `r` and Enter at the pipeline's terminal to **run the last dozen sentences again with the current
   prompts**. Each result shows the new score and the old one (`was 4.97`). Say something once, then try
   several wordings of the prompt against the same words.

You can do the same against a recording (`--wav`), which is the best way to compare two wordings fairly:
the same audio, the same sentences, only the prompt differs.

## Running it

The code gets to the Pi with git: push from the Mac, then `git pull` on the Pi (it has a clone at `~/pom`).

On the Mac, to deliver your prompt edits to the Pi as you make them:

```
experiments/hearing-debug/probe_hearing_tune.sh start     # once: makes the throwaway branch and sends the current prompts
experiments/hearing-debug/probe_hearing_tune.sh watch     # leave running: each save of config/pom.toml is sent
```

On the Pi, in a second terminal:

```
ssh pi@pom1.local
cd ~/pom && git pull
cd experiments/hearing-debug
./probe_hearing_run.sh                      # listen on the microphone
./probe_hearing_run.sh --wav ~/pom/data/recordings/work/some-clip.wav    # or replay a 16 kHz mono recording
```

`probe_hearing_run.sh` sets the microphone gain, starts a whisper-server (whisper.cpp's own server, model kept
loaded, with its Silero voice detection on) and the language model (from `~/pom/data/models`), runs the pipeline on
the live config (or `config/pom.toml` if there is none) and stops both servers when you quit. Useful options:
`--model small.en` for a bigger whisper model (and `--whisper-threads`, `--vad-threshold`, which go to the
whisper-server), `--no-explain` for **fast mode** (each sentence scored at once by the Printer's
one-token scorer, no reasons, much lighter on the CPU), `--margin-db 4` if music or noise is masking speech, `--speed 0` to play a recording as fast as possible.

**The known-good setup (Wes, 2026-10-27):** on the Pi, `./probe_hearing_run.sh --no-explain` with nothing else
changed. That is whisper `base.en` (the script's default, so no `--model`), microphone gain 24 of 30, the default
whisper threads (4) and voice-detection threshold (0.5), Silero VAD on, cutting by whisper's own segments (a run every
5 s on a rolling buffer), and the Qwen3-4B scorer. Whisper was ready in about 1 s and the scorer in about 10 s. It
is the only combination that has worked well in the room, and it is fragile, so change one thing at a time from here.

To try it on the Mac against a recording, start a whisper-server (the header of `probe_hearing_pipeline.py` has
the command), set `POM_SCORER_URL` to a running llama-server, and use `--wav`.

## How the prompts travel (git, no copying)

You edit `config/pom.toml` on `main`, uncommitted, as for any change. `probe_hearing_tune.sh` keeps a second
checkout of a throwaway branch, `prompt-tuning` (a git worktree in `data/tuning-push`, ignored by git). On each
save it checks that the config loads **without a single problem** (a typo is not sent), commits it on that branch
with the three prompt fingerprints in the message, and pushes. Your checkout is never switched, so commits made to
`main` meanwhile land where they should.

On the Pi, `probe_hearing_run.sh` follows that branch: a scratch worktree in `data/tuning` is brought up to date
with an ordinary `git fetch` every few seconds, and the pipeline reads its config from there. The Pi's main clone is
not touched, so `git pull` there stays simple. If no tuning branch exists, the committed config is used.

When you settle on a prompt, commit `config/pom.toml` to `main` as usual and run `probe_hearing_tune.sh stop`, which
deletes the throwaway branch (local and remote) and its checkouts; it holds nothing worth keeping. Expect a delay
of a few seconds between saving and the Pi seeing the change.

## What it keeps

Nothing. What is heard is printed to the terminal and nowhere else: the audio goes to the whisper-server from
memory and never touches a disk, the last few sentences stay in memory for the `r` command, and the servers'
logs go to memory (`/dev/shm`) and hold request details, not words. Do not redirect the output to a file, and do
not run it as a service. It is a development tool, used on purpose.

## Things to know

- **Whisper decides what is speech and where to cut.** Its own Silero VAD (`--vad`) decides what is speech, so it
  stops inventing words for silence and noise. Every `--step` seconds (5) the pipeline hands whisper the audio
  since the last kept segment and keeps the segments whisper finished (those ending at least `--hold` seconds, 2,
  before the end of the buffer); the rest waits for the next round. See `rolling_cutter.py` and the last section.
  `--energy-cutter` brings back the old way: cut by loudness, at a 0.7 s pause or a 20 s cap, with the noise floor
  re-estimated all the time. It is kept for comparison; lower `--margin-db` (energy cutter only) if quiet speech is missed.
- **The VAD ignores singing and most sounds.** So when it finds no speech, a second pass runs without it and keeps
  only what whisper itself marks as non-speech: lyrics between music symbols and captions like "(dog barks)". Those
  lines say "sound, not speech". Plain text from that pass is dropped, since that is where whisper invents words.
  `--no-sounds` skips the second pass; `--no-vad` turns the VAD off.
- **Each sentence makes ONE trip through the model, as soon as whisper has produced it.** In debug mode (the
  default) a sentence is shown as `waiting` the moment it is heard, then the model runs your debug prompt on it
  once, and the score and the reasons come out of that same output; nothing is scored first and explained
  afterwards. The model takes one sentence at a time, in order. Writing words is the slowest thing the model does
  (one measured 1.25 tokens a second while the Pi was also listening), so on the Pi results can trail the room and
  whisper can run slower beside it; fast mode (`--no-explain`) is the light option. Sentences that have no content
  words skip the model.
- **Whisper's output is sorted three ways.** *Words* (speech, and song lyrics with the music symbols removed) go to
  the Scorer. *Ambient captions*, whisper's guesses at the room such as `(dog barks)`, `[Music]` or `(heavy breathing)`,
  are shown on their own `ambient, not scored` line and **never scored or explained**: they describe the room rather
  than something said, and as hinge candidates they only gave the scorer a caption word to react to. They are kept as a
  short list of recent ambient sounds, ready to go into the voice's prompt later as context. Whisper's "nothing here" markers (`[BLANK_AUDIO]`, `[silence]`, `(silence)`, `[no audio]`) are dropped
  entirely, and when whisper has said an audio was silent the second pass is not run.
- **Whisper runs as a server** (`whisper-server`), so its model stays loaded between utterances; the pipeline sends
  each utterance to it over HTTP, from memory. Measured on the Pi, with matched settings, that saves about 15 to
  30% of each call; most of the Pi's cost per call is whisper encoding a padded 30-second window whatever the length
  of the audio, which is why fewer, longer calls are cheaper per second of audio. The server has a known bug (see
  `whisper_server_client.py`): `verbose_json` with voice detection on crashes it when the audio holds no speech, so
  timestamps are requested as SRT.
- **The number and the reasons come from the same output.** Your `prompt_output_debug` asks for the score, the
  sentence and the content found in a single answer. The score shown is read from the probabilities of that
  answer's first token (the way the one-token scorer reads its own), and the reasons are the rest of the same
  output. Under each result it prints what the model wrote as its score and how long the run took. The reasons
  are written after the number, so they are a story the model tells about it, not a readout of its insides; the
  one-run design guarantees only that the number and the story belong together. The real Printer will use the
  one-token scorer, so the debug prompt's score can differ a little from what the Printer would give.

## Files

- `probe_hearing_pipeline.py`: the pipeline (cutter, Transcriber, Scorer and display, in one file).
- `rolling_cutter.py`: the `RollingCutter`, which lets whisper's voice detection find the cuts (the pipeline's default).
- `probe_hearing_run.sh`: on the Pi; microphone gain, language model, pipeline.
- `probe_hearing_tune.sh`: on the Mac; `start`, `watch`, `sync`, `stop`. Delivers prompt edits through git.
- `probe_hearing_follow_tuning.sh`: on the Pi (used by `probe_hearing_run.sh`); follows the tuning branch with `git fetch`.
- `whisper_server_client.py`: a small client for whisper-server, shared by the tools in this folder.
- `probe_whisper_segments.py`: offline test (no microphone, no language model) of the `RollingCutter` on a recording,
  compared with whisper reading the whole file. See the next section.

## Finding the cut points: the energy cutter against whisper's own voice detection

The energy cutter (`--energy-cutter`) in `probe_hearing_pipeline.py` ends an utterance after 0.7 s below a speech threshold. In a room with
fluctuating noise (street, music, a hookah) that pause never comes: on a 90 s attic recording 34 to 55% of the frames
counted as speech even in the stretches where nobody spoke, so every utterance ran to the 20 s cap and was cut by the
clock, in the middle of a thought. `probe_whisper_segments.py recording.wav` replays a recording through the alternative:
a rolling buffer handed to a whisper-server (with its voice detection on) every `--step` seconds, keeping the segments whisper finished, joining a segment that
does not end like a sentence to the next, and splitting
segments into sentences with the Printer's own `SegmentFragmenter`. It compares the result with whisper reading the whole
file at once. Findings so far: on the attic recording, whisper found no speech in the noise-only stretches and cut at
speech pauses, with every sentence of the whole-file answer found and none broken; a sentence split at a pause in the middle
is handled by joining it to the next segment. Whisper's segment ends can run late and might clip the next sentence's
first syllables; `--rewind` (off by default) starts each round a little earlier to guard against that, but live it
re-heard the last word of the previous segment as the start of the next, and on the recordings it kept no more words
(it added extras). Words of the whole-file reading that the rolling buffer also produced (5 s step, no rewind),
against the old energy cutter with level normalization: attic recording 93% against 87%; loud music near the
microphone 90% against 90%; a quiet classroom recording 52% against 43%. Clear speech comes through well; quiet speech in a classroom loses
about half of its words with either method, and the whole-file reading does better there because whisper has the
earlier text for context. Sentence boundaries differ a little between steps, since whisper punctuates each window
as it sees it. Giving whisper the recent text as a prompt was tried and was mixed (clear speech improved,
the classroom recording got worse), so it is not used.
