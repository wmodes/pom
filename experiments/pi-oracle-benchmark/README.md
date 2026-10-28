# Pi oracle benchmark

**Status: built, tested, run on the Mac (a reference) and run on the Pi (2026-10-27, results below).** Nothing here is
part of the Printer.

## The Pi result (`results/pi-2026-10-27/`)

Qwen3.5-9B on `pom1` (Pi 5, 16 GB, Active Cooler, 27 W supply), 12 portents per phase, each phase from an emptied page
cache. The prompt (`2e18faf0034e`) and corpus (`25b0e1dc2cdb`) fingerprints match the bake-off's, at commit `b0b68e9`.
All 24 portents were made, none cut off at `max_tokens`, and the Pi never throttled (hottest 73.6 °C).

| | 9B with whisper and the 4B idle | 9B alone | Mac, for scale |
|---|---|---|---|
| Whole portent, median | 38.1 s | 35.4 s | 0.8 to 0.9 s |
| Whole portent, 95th percentile (the slowest of 12) | 96.6 s | 86.6 s | 2.4 to 2.5 s |
| Seconds to the first word, median | 20.3 | 20.1 | 0.3 |
| Writing speed, median | 1.9 tokens a second | 2.0 | 68 to 69 |
| Words, median | 28 | 26 | |
| 9B cold start (file read from the card) | 50.2 s | 77.6 s | |
| 9B restart (file already in memory) | 13.6 s | 13.5 s | |
| 4B scorer cold start | 56.6 s | | |

- **Idle neighbours cost almost nothing.** The medians differ by about 3 seconds and the writing speed by 0.1 tokens a
  second; with 12 portents each that is not a difference this run can resolve. With all three models loaded at least
  6.7 GB of the 16 GB stayed available.
- **The wait for the first word is the Pi reading the prompt**: about 0.095 s a prompt token (about 10.5 tokens a
  second), so 183 tokens took 18 s and 305 took 29 s. The server reused none of the prompt from one portent to the
  next (`cached_tokens` was 0 on all 24), though the start of the prompt is the same each time. Why is not known.
- **The writing itself is slow and steady**: about 0.5 s a token, so a portent of 28 words takes about 18 s after the
  first word, and one of 95 words about 67 s. The typewriter types faster than the model writes, so a portent must be
  finished before typing starts.
- **A portent takes 35 to 40 s typically and up to about 100 s** (the prompt asks for under 150 words, which would be
  about 2 minutes), plus 5 to 20 s to type it. The cold start of 50 to 78 s happens once, at boot.
- The run took 22 minutes. It was started detached on the Pi; the results were read back to the Mac and checked by
  checksum, and the Pi's copy was then removed. The server logs stayed on the Pi.

## The question

The oracle bake-off (`experiments/oracle-bakeoff/`) picked Qwen3.5-9B as the candidate to beat, on a Mac. The Pi 5 has
much slower memory than the Mac (it turned out to write the 9B's tokens 36 times slower) and the 9B is the biggest
model in the system. How long does it take to load,
to start writing, and to finish a portent on the Pi, **with whisper and the 4B scorer loaded but idle**?

"Idle" is the design, not a shortcut. The machine is half-duplex: while the oracle works, the Conductor sends nothing to
whisper or the scorer, so they sit in memory doing nothing. The benchmark is therefore the oracle running next to two
loaded-but-dormant neighbours. Whisper busy at the same time is a later experiment, if it turns out to matter.

## What it measures

Two phases, each starting from an emptied page cache (as after a boot):

- **dormant:** whisper (`base.en`) and the 4B scorer are started exactly as `probe_hearing_run.sh` starts them and left
  idle, then the 9B is started and timed. Then the 9B is stopped and started again, to time a restart with its file
  already in memory.
- **alone:** the same thing with the 9B by itself. The difference between the two phases is what the idle neighbours cost.

Per phase, over 12 portents (every third benchmark hinge, draws 1 and 2, with the bake-off's own seeds, so the exemplars
are the ones the Mac saw), after one uncounted warm-up portent:

- Server startup time (cold, and restart).
- Seconds in total, **seconds to the first token**, prompt tokens (and how many came from the server's cache), writing
  speed in tokens a second, words written, and whether it was cut off at `max_tokens`.
- Once a second: each server's memory, the memory still free, the SoC temperature and the Pi's throttling flags.

Only counts and timings are kept; never the portents.

## How it is run

The real `PromptBuilder` makes the prompt and the real `OracleModelClient` asks the model, with the frozen prompt and
settings from `config/pom.toml`. The one difference is that the client's `post` is replaced by a streaming one
(`StreamTimer`), so the time to the first token can be seen. The request body is the client's own, plus `"stream": true`.

## Running it

The Pi gets the code from git (`git pull`), and the 9B file, which git does not carry, from Hugging Face. On the Pi:

```
cd ~/pom && git pull
curl -L -C - -o ~/pom/data/models/Qwen3.5-9B-Q4_K_M.gguf \
  https://huggingface.co/unsloth/Qwen3.5-9B-GGUF/resolve/main/Qwen3.5-9B-Q4_K_M.gguf     # 5.7 GB, once
cd experiments/pi-oracle-benchmark
./benchmark_oracle_pi_run.sh                  # both phases; the script's header lists the overrides
```

Give the Pi its proper power supply and cooling, and stop anything else heavy first (the script refuses to start if a
`llama-server` or `whisper-server` is already running). It ends by printing the report and saving it as
`results/<run name>/report.txt`.

**Check the report's first line for each phase:** `prompt 2e18faf0034e` and `corpus 25b0e1dc2cdb` are the bake-off's
frozen prompt and corpus. If they differ, the Pi's checkout is not running the same conditions as the Mac's.

## Files

- `benchmark_oracle_pi_run.sh`: on the Pi; starts and stops the servers, empties the page cache, runs both phases.
- `benchmark_oracle_pi.py`: `wait` (time a server's startup), `run` (make and time the portents), `report`.
- `results/`: one folder per run (`startup.jsonl`, `<phase>.jsonl`, `<phase>-samples.jsonl`, server logs, `report.txt`).
- Tests are in `tests/pi_oracle_benchmark/`; they cover the parts that turn a stream and a list of samples into numbers.

## Limits

- **The Mac reference run** (`results/mac-2026-10-27-clean/`, an M4 Max with 64 GB, all three models on the GPU, page
  cache not emptied, so its "cold" load times are warm ones) is the no-memory-pressure baseline for the Pi. Both phases
  gave a median of 0.8 to 0.9 s a portent (2.4 to 2.5 s at the 95th percentile), 0.3 s to the first token and about
  68 to 69 tokens a second, so idle neighbours cost nothing measurable there. Memory in use at the peak: 6.7 GB for the
  9B, 3.1 GB for the 4B and 0.33 GB for whisper, about 10 GB together, which is what the Pi's 16 GB has to hold. These
  are Mac numbers and say nothing about the Pi's speed. A first run was thrown away because the playground's 9B was
  being used on the same GPU (two portents ran at 38 tokens a second instead of 65 to 70); nothing else was running
  for this one.
- 12 portents is enough to see the size of the delay, not to separate small differences.
- The scorer is warmed with one request, not run under load; it is dormant, as in the design.
