# Interest scorer experiment

**Status: complete.** This was the formative experiment for the Scorer: it settled what the Scorer is
and how it is called. It is not part of the Printer. The production module will be built from what is
written down here.

## The question

The Printer overhears speech and should answer only a sentence worth answering. Can a small language
model that fits on the Raspberry Pi score how *interesting* a sentence is, well enough to pick from?
We first planned word-by-word signals (rarity, concreteness, feeling), but atomizing speech misses what
a phrase means ("the couch ate my dog" is not "the dog ate my couch"), so we tried a model.

## How it is tested (the rules)

- **Batch size 1.** Each sentence is its own request.
- **No context.** The model never sees any other sentence, so a sentence scores the same whatever
  came before it or wherever it sits in a list.
- **One prompt**, `SINGLE_PROMPT`, and **one set of sample sentences**, `BENCHMARK`, both in
  `probe_interest_scorer.py` (written by Wes). Both are copied next to each saved result.
- **The score** is the expected value of the digit the model starts its reply with, read from the
  model's probabilities (one generated token), so it is continuous and repeatable. The prompt asks for a
  two-decimal number, but only that first digit's probabilities are used.
- Models run in llama.cpp (`llama-server`), Q4_K_M, one at a time on the Mac.

## What we learned

- A 3 to 4 billion parameter model can do it; 1.7B (Qwen3) was too mushy. On the Mac it takes 30 to 50
  ms per sentence (Gemma about 200 ms).
- Scoring one sentence alone with the digit probabilities is repeatable and order-independent.
  **Batching** many sentences in one request let a sentence's score depend on its neighbors and
  position (about 0.7 points of jitter), and the model sometimes dropped rows. **Showing earlier
  lines as context** also moved scores (plain etiquette fragments rose from 2 to 4.6), so both were
  rejected.
- The prompt matters more than the model. What helped: a plain task ("score how interesting"), no
  persona or in-world wording, the *definition* of interesting kept separate from the *meaning of the
  numbers*, a scale defined by how much interesting content there is, and no line telling the model
  to give high scores only rarely (it clustered scores at one value).
- Models agree about the shape (rank agreement 0.78 to 0.89) and on the plain end. They differ at the
  top: Qwen3-4B gives a graded top and a clear gap between interesting and plain; Llama-3.2-3B is the
  fastest but bunches about 20 sentences between 7 and 8 and never scores below 3; Gemma-3-4B has the
  widest range but plateaus at 8.0 and is slowest.
- Every model rates sensational content (threats, sex, profanity) high alongside the genuinely strange;
  "bodies" in the prompt's list may be pulling that. A bare "I love you" scores low alone.

## Result

The saved bake-off is `results/bakeoff-2026-10-22/`: `report.txt` has the summary and each model's
ranking, with one JSON file per model, plus the exact `PROMPT.txt` and `LINES.txt` used. Re-running the
runner reproduced the earlier scores exactly.

**Chosen** (Wes, 2026-10-23): start with Qwen3-4B for the Scorer (graded top, clear break, repeatable, fast on the Mac),
with Llama-3.2-3B as the faster alternative. Timed on the Pi (`../pi-benchmark/`): Qwen is fast
enough even with whisper running flat out, though about 1.7 times slower than Llama. On real whisper
output from a quiet room, Qwen scored whisper's phantom words ("You", "I") at 1.0 and Llama at about 4,
which counts for Qwen.

## Files

- `probe_interest_scorer.py`: the exploratory probe. Runs by hand against one server; also has the
  `--batch` and `--context` modes that were tried and rejected.
- `bakeoff_interest_scorer.py`: the fixed test for one running model (`run`), and the comparison of
  saved results (`report`).
- `bakeoff_interest_scorer.sh`: starts each model's server in turn, runs the test, stops it, and
  prints the report.
- `results/`: saved runs. Server logs are not kept.

## A later finding: a bigger model fixes the compression

With the same prompt, Qwen3-8B (about 5 GB) scored fourteen single-sensational sentences ("He had no face", "a room
that only exists on Tuesdays", "the baby was born with a full set of teeth") at a mean of 7.7, 13 of them at 7 or
more, where the 4B scored a mean of 6.2 and 4 of 14. Plain talk stayed at 2.0 to 2.4 and a sentence stacking several
shocking things scored about the same as the single ones (8.0 to 8.2), so it judges strength, not a count. The one new
false high was "What the fuck?" (7.99). Three prompt rewrites tried on the 4B changed little, so the 4B's compression, not the
wording, was the limit. The 8B has not yet been timed on the Pi (it is about twice the size of the 4B).

## Re-running it

Needs `llama-server` (`brew install llama.cpp`); the models download on first use (about 2 GB each).

```
experiments/interest-scorer/bakeoff_interest_scorer.sh                  # a run named for today's date
experiments/interest-scorer/bakeoff_interest_scorer.py report experiments/interest-scorer/results/bakeoff-2026-10-22
```

To try one model by hand, start `llama-server -hf <repo>:Q4_K_M --port 8091 --ctx-size 8192 -ngl 99 --jinja`
and run `probe_interest_scorer.py`.

## Caveats and what is still open

- Wes wrote and tuned the prompt while watching Qwen's output, so Qwen has a home advantage.
- The sample is a few dozen sentences, and the "right" order is a judgment.
- Not yet measured: speed on the Pi (especially with whisper running), and behavior on real transcripts
  of a room full of people.
- We have not defined for ourselves what "interesting" means and what should trigger a print; the
  model can only be as clear as we are. That comes first, later.
