# Pi benchmark

**Status: prepared, not yet run.** The Pi was out of reach when this was written, so everything that can
be tested without it was tested on the Mac (the scripts run end to end with a stand-in for whisper). The
Pi itself has not been touched. Nothing here is part of the Printer.

## The question

The interest-scorer experiment settled that the Scorer is a small local model scoring one sentence at a
time. On the Mac that takes 30 to 50 ms. Can the Raspberry Pi 5 do it fast enough, with enough memory
left over, **while whisper.cpp is transcribing the room**?

That overlap is new. The earlier Pi notes assumed whisper and the language model never run at the same
time (the typewriter is deaf while it prints). But the Scorer scores sentences as they are heard, so it
overlaps with whisper, and the two will compete for the same four cores.

## What it measures

For two models, Qwen3-4B and Llama-3.2-3B (Q4_K_M), scoring one sentence per request with no context:

- **Scorer alone**, on all four cores.
- **Scorer with whisper transcribing back to back** (the worst case; a real room leaves gaps), with whisper
  `base.en` and `small.en`, with the cores **shared** (4 + 4 threads: both programs may use all four, and
  the system divides them as the load changes, which is how the real system will run). An even **split**
  (2 + 2) is available with `SPLITS="4+4 2+2"` if the shared results look bad.
- **How much the Scorer slows whisper down**, against whisper alone.
- Per call: the time, and how much of the prompt came from the cache (the prompt is the same every time,
  so most of it should).
- Meanwhile, once a second: the server's memory, memory still free, the SoC temperature, and the Pi's
  **throttling flags** (a hot or underpowered Pi quietly slows down).

Two samples:

- **Small**: the probe's `BENCHMARK` sentences (Wes's), two passes, in every phase.
- **Large**: `sample-large.txt`, 294 corpus sentences (21 from each register), one pass, only in the
  Scorer-alone phase and the harshest shared phase. It is a long run, which is where heat, throttling,
  memory creep and slow outliers would show up. The corpus is written text, not speech; for a load test
  what matters is variety and ordinary sentence length. Make it again with `benchmark_pi_make_sample.py`.

## Running it

From the Mac, with the Pi on the network:

```
experiments/pi-benchmark/benchmark_pi_prepare.sh check     # what does the Pi have, what is missing?
experiments/pi-benchmark/benchmark_pi_prepare.sh push      # copy the code and any missing models (about 2 GB: Qwen is already there)
```

On the Pi (nothing else heavy running; give it its proper power supply and cooling):

```
cd ~/pom-bench/experiments/pi-benchmark
QUICK=1 ./benchmark_pi_run.sh sanity       # a sanity run, roughly 20 to 30 minutes (my estimate): one whisper model, shared cores, no large sample
./benchmark_pi_run.sh                      # the full run; plan on a couple of hours (an estimate, not measured)
```

Back on the Mac:

```
experiments/pi-benchmark/benchmark_pi_prepare.sh fetch <run name>      # then read results/<run name>/report.txt
```

Do the quick run first. `check`, `push` and `fetch` accept `DRY_RUN=1` to show what they would do. The
runner's settings (threads, whisper models, context size, rest between phases) are environment
variables listed at the top of `benchmark_pi_run.sh`. It stops any server it started however it exits,
and the whisper load stops itself after 30 minutes (two hours for the large sample) if the runner dies.

## Reading the report

`report.txt` has one line per run: warm-up (the first call, which loads the model into memory), median,
p95 and slowest time per sentence, the share of the prompt served from cache, the server's peak memory,
the lowest free memory, peak temperature and any throttling. Then whisper's time alone against with the
Scorer running, and PASS or CHECK against two **proposed** thresholds:

- Scorer p95 under 4 seconds per sentence (an utterance is one to three sentences, so scoring should
  finish well before the next arrives).
- Whisper no more than 1.5 times slower than alone.

They are guesses to react to, not requirements; change them with `--target-p95` and
`--max-whisper-slowdown` on the `report` command.

## Results so far

**Quick run, 2026-10-22** (`results/quick-2026-10-22/`: `base.en` only, shared cores, small sample,
100 sentences twice; `report.txt` has the table). One sentence per request on the Pi:

- Scorer alone: Qwen3-4B median 1.1 s, p95 1.6 s; Llama-3.2-3B median 0.66 s, p95 1.0 s.
- Scorer with whisper `base.en` running flat out: Qwen median 2.3 s, p95 3.2 s, slowest 4.5 s; Llama
  median 1.4 s, p95 2.2 s. Both under the proposed 4 s p95.
- Both models roughly halve in speed when whisper competes, and whisper does too (`base.en` 2.6 s alone,
  6.0 s with Llama running; Qwen's whisper log was lost, see below). Six seconds is still far inside a
  30 s window.
- System memory used: Llama about 2.3 GB alone and 2.7 GB with whisper; Qwen about 2.8 and 3.2 GB.
- Prompt cache served 94 to 96% of prompt tokens. Peak 71 °C, no throttling.
- The first call after a server starts took 12 to 28 s (model pages loading), so the Scorer needs a
  warm-up request at startup.

That run had a bug: both models wrote their whisper log under the same name, so Llama's overwrote
Qwen's. Fixed afterwards (the log name now includes the model); the full run has not yet been done.
Not yet measured: `small.en`, the large sample (sustained heat), and any real room audio.

## What it decides

Whether Qwen3-4B (better ranking) is fast enough on the Pi, or Llama-3.2-3B (faster, flatter ranking)
is needed; whether the context can stay small; and how
much memory is left for the voice model.

## Files

- `benchmark_pi_prepare.sh`: on the Mac. `check`, `push`, `fetch`. Only looks and copies; installs nothing.
- `benchmark_pi_run.sh`: on the Pi. Runs every phase and prints the report.
- `benchmark_pi_scorer.py`: scores the sentences and samples the machine (`run`); summarizes (`report`).
- `benchmark_pi_whisper.py`: whisper alone, or whisper back to back as the load.
- `benchmark_pi_make_sample.py`: makes `sample-large.txt`.
- `sample-large.txt`: the large sample, committed so the Pi needs no corpus.
- `results/`: saved runs. Server logs are not kept.

## Caveats

- **The numbers are for comparing, not for predicting.** The point is Qwen against Llama under the same
  conditions. The whisper load runs `whisper-cli`, which reloads its model on every pass, unlike the
  final system's resident whisper; that costs both Scorer models the same, so the comparison holds,
  but absolute whisper times will look worse than the real thing.

- A clip of 11 seconds stands in for a whisper window; whisper encodes a full 30 second window either
  way, so the cost is about the same.
- Back-to-back whisper is pessimistic. A real room is mostly gaps.
- Scores differ a little by hardware and by server settings (a 0.03 difference from the Mac bake-off
  appeared with a smaller context and one slot), so rankings, not exact numbers, are what carry over.
- The Llama model was never copied to the Pi before; `push` does it.
