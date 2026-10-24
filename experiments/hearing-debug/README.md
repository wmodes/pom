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
`ConvoInterestTracker`, which keeps the room's average interest, and a sentence that stands out from that
average is marked `<-- would trigger`. There is no fixed threshold: the bar moves with the room.

Every line ends with a note that says where the score stands, for example `[interest 4.31 -> 4.52, bar 4.74]`:
the room's average interest the sentence was judged against, the average after it was added, and the score it
had to reach (10% above the first number). `[warming up 3/10; ...]` means not enough scores have counted yet for
anything to trigger, and `[below the floor, ignored; interest 4.31, unchanged]` means the score did not count. The settings
are in the `[trigger]` section of `config/pom.toml`, and the startup banner prints the ones in force.
Reloading the config restarts the average, because a changed prompt changes what a score means. The `r`
command re-runs sentences without counting them again.

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

`probe_hearing_run.sh` sets the microphone gain, starts the language model (from `~/pom/data/models`), runs
the pipeline on the live config (or `config/pom.toml` if there is none) and stops the model when you quit. Useful options (all passed through): `--model small.en` for a bigger whisper
model, `--no-explain` for **fast mode** (each sentence scored at once by the Printer's
one-token scorer, no reasons, much lighter on the CPU), `--margin-db 4` if music or noise is masking speech, `--speed 0` to play a recording as fast as possible.

To try it on the Mac against a recording, set `POM_SCORER_URL` to a running llama-server and use `--wav`;
see the header of `probe_hearing_pipeline.py`.

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

Nothing. What is heard is printed to the terminal and nowhere else: the audio is cut into a temporary file
in memory (`/dev/shm`) and deleted as soon as whisper has read it, the last few sentences stay in memory for
the `r` command, and the model server's log goes to memory. Do not redirect the output to a file, and do
not run it as a service. It is a development tool, used on purpose.

## Things to know

- **What is speech is decided by whisper's own Silero VAD** (`--vad`), not by a threshold of mine. With it, whisper
  stops inventing words for silence and noise, and hears quiet speech better. The cut into utterances is still
  energy-based, but only as a chunker: the noise floor is re-estimated all the time (a low percentile of the last
  30 s), so it works when started in a loud room. Lower `--margin-db` if quiet speech is missed.
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
- **Whisper reloads its model for every utterance** here, as in the Pi benchmark. The real system will keep
  it resident.
- **The number and the reasons come from the same output.** Your `prompt_output_debug` asks for the score, the
  sentence and the content found in a single answer. The score shown is read from the probabilities of that
  answer's first token (the way the one-token scorer reads its own), and the reasons are the rest of the same
  output. Under each result it prints what the model wrote as its score and how long the run took. The reasons
  are written after the number, so they are a story the model tells about it, not a readout of its insides; the
  one-run design guarantees only that the number and the story belong together. The real Printer will use the
  one-token scorer, so the debug prompt's score can differ a little from what the Printer would give.

## Files

- `probe_hearing_pipeline.py`: the pipeline (Segmenter, Transcriber, Scorer and display, in one file).
- `probe_hearing_run.sh`: on the Pi; microphone gain, language model, pipeline.
- `probe_hearing_tune.sh`: on the Mac; `start`, `watch`, `sync`, `stop`. Delivers prompt edits through git.
- `probe_hearing_follow_tuning.sh`: on the Pi (used by `probe_hearing_run.sh`); follows the tuning branch with `git fetch`.
