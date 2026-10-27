# Oracle bake-off

A repeatable way to compare models as the oracle. Each model gets the same prompt, the same settings and the
same exemplars for the same hinges, and its portents are rated against a fixed rubric
(`documentation/bakeoff-rubric.md`). The results so far are in `documentation/model-bakeoff.md`.

## What is frozen

Nothing is configured here. The prompt, temperature, `max_tokens` and exemplar count come from
`config/pom.toml`, so the bake-off judges what the Printer will do. The first line of every results file records
them (with the prompt text, a fingerprint of the corpus, the git commit and the llama-server version), and
`make_rating_sheet.py` refuses to compare files whose conditions differ. **Do not edit the oracle section of the
config while a bake-off is running.**

## The benchmark

- `benchmark_hinges.txt`: sixteen hinges. Eight were typed by Wes; eight were drawn at random (fixed seed) from the
  sample sentences in `experiments/interest-scorer/probe_interest_scorer.py`.
- Ten draws of exemplars per hinge (draws 1 to 10), each portent made once: 160 portents per model. A draw is a
  seed: the generator is seeded from the hinge and the draw number, so every model sees the same 160 exemplar
  draws, and draws 1 to 6 are the same ones the first pass used.
- `results/first-pass/` and `ratings/first-pass/` hold the first pass (8 hinges, 6 draws) and its ratings; it is
  kept for reference and is not part of the comparison.

## Running it

```
python experiments/oracle-bakeoff/run_oracle_bakeoff.py --model unsloth/Llama-3.1-8B-Instruct-GGUF:Q4_K_M
python experiments/oracle-bakeoff/make_rating_sheet.py     # blind sheet of every portent, shuffled across models
# read ratings/sheet.txt and write ratings/ratings.jsonl (see report_bakeoff.py for the format)
python experiments/oracle-bakeoff/report_bakeoff.py        # ratings/report.md
```

- `run_oracle_bakeoff.py` starts its own llama-server (port 8097) and stops it. It refuses to start if that port
  is in use, and if the model's results file already exists (delete the file to run the model again).
- `bakeoff_checks.py` holds the three checks a program makes (repeats the hinge, extra text, length); they are
  tested in `tests/oracle_bakeoff/`.
- `results/` holds one file per model. `ratings/` holds the blind sheet, its key, the ratings and the report.
