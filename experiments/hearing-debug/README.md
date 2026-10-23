# Hearing debug

**Status: working prototype, for refining the prompts.** It is not part of the Printer. It is a live
window onto how the Printer interprets what it hears, built so the prompt can be improved in a closed loop.

## What it is

A small pipeline that runs on the Pi (or on the Mac against a recording):

```
microphone -> cut at pauses -> level normalized -> whisper -> sentences -> Scorer -> interpretation
```

For every sentence it prints the real `pom` Scorer's score, and a little later the model's own written
account of why: the content it found that earned the score. Sentences at or above the threshold (7.0 to
start) are marked as ones that would trigger.

The Scorer, its settings and its prompts are the real thing, read from `config/pom.toml`. The
microphone handling (cutting at pauses, level normalization, whisper) is a quick stand-in for modules
that are not built yet; if it works well it becomes the Segmenter and Transcriber.

## The closed loop

1. The prompts live in `config/pom.toml`: `prompt_scoring` (what counts as interesting, and the scale),
   `prompt_output` (how to reply when scoring) and `prompt_output_debug` (how to reply when explaining).
   The Scorer is sent scoring plus output; the interpretation is sent scoring plus debug output.
2. `probe_hearing_sync_config.sh watch` (on the Mac) copies the config to the Pi every time you save it,
   to `~/pom/data/live/pom.toml`, outside git so a `git pull` there never meets a modified file.
3. The pipeline re-reads the config before each utterance, and says so when it changes, with a short
   fingerprint of each prompt so you can tell which version scored what.
4. Type `r` and Enter at the pipeline's terminal to **re-score the last dozen sentences with the current
   prompts**. Each line shows the new score and the old one (`was 4.97`). Say something once, then try
   several wordings of the prompt against the same words.

You can do the same against a recording (`--wav`), which is the best way to compare two wordings fairly:
the same audio, the same sentences, only the prompt differs.

## Running it

The code gets to the Pi with git: push from the Mac, then `git pull` on the Pi (it has a clone at `~/pom`).

On the Mac, with the Pi on the network, to keep the prompts in step while you edit them:

```
experiments/hearing-debug/probe_hearing_sync_config.sh watch
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
model, `--threshold 7.0`, `--no-explain` to skip the written interpretations (much lighter on the CPU),
`--explain-above 5` to interpret only sentences scoring 5 or more, `--margin-db 4` if music or noise is
masking speech, `--speed 0` to play a recording as fast as possible.

To try it on the Mac against a recording, set `POM_SCORER_URL` to a running llama-server and use `--wav`;
see the header of `probe_hearing_pipeline.py`.

## What it keeps

Nothing. What is heard is printed to the terminal and nowhere else: the audio is cut into a temporary file
in memory (`/dev/shm`) and deleted as soon as whisper has read it, the last few sentences stay in memory for
the `r` command, and the model server's log goes to memory. Do not redirect the output to a file, and do
not run it as a service. It is a development tool, used on purpose.

## Things to know

- **Cutting at pauses is energy-based.** It learns the room's noise floor at the start and calls anything a
  few dB above it speech. It cannot tell speech from music or a steady noise: with music playing, lower
  `--margin-db` (4 worked in the attic) and expect long utterances.
- **Interpretations are slow.** Writing about 100 words takes the Pi several seconds, so they arrive after
  the score, and if the room talks faster than the model writes, older ones are skipped (it says so). Scores
  are never held up by them: the model server has two slots.
- **Whisper's captions are kept**, like "(dog barks)", and the music symbol is removed with the words kept.
  Its `[BLANK_AUDIO]` silence marker is dropped.
- **Whisper reloads its model for every utterance** here, as in the Pi benchmark. The real system will keep
  it resident.
- **The explanation is the model's own account, not a readout of its reasoning.** It is useful for seeing
  what it thinks it found, and it can be wrong or wander; the debug prompt is yours to tighten.

## Files

- `probe_hearing_pipeline.py`: the pipeline (Segmenter, Transcriber, Scorer and display, in one file).
- `probe_hearing_run.sh`: on the Pi; microphone gain, language model, pipeline.
- `probe_hearing_sync_config.sh`: on the Mac; copies `config/pom.toml` to the Pi, and `watch` keeps it in step as you edit.
