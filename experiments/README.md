# experiments/

Questions we answered by trying things, kept so the reasoning and the evidence survive. Nothing here
is part of the Printer and nothing under `src/` imports from it. Each experiment gets its own folder
with a README that says what was asked, how it was tested, and what was decided.

File names say what is being tested and how far along it is: `<level>_<subject>.<ext>`.

- `probe_`: exploratory. One model, quick to change, run by hand, to find out what is possible.
- `bakeoff_`: a fixed test run identically against several candidates, with saved results.
- `benchmark_`: a fixed test of speed and resource use on the target hardware, with saved results.

Experiments:

- `interest-scorer/`: can a small local language model score how interesting a sentence is? Settled
  how the Scorer works (complete).
- `pi-benchmark/`: can the Raspberry Pi 5 run that Scorer fast enough while whisper is transcribing?
  Prepared and tested on the Mac; not yet run on the Pi.

A related, still-planned experiment is the voice bake-off (Track A against Track B); it will live here too.
