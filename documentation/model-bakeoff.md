# Model bake-off: the oracle model

Which local model should write the portent. This is the record of what each candidate did, kept so the choice is
not made from memory. It is general, not detailed: it says what each model is good and bad at, not every output.
The method is in `experiments/oracle-bakeoff/README.md` and the rubric in `documentation/bakeoff-rubric.md`.

**Status (2026-10-26): seven models, one frozen prompt, 160 portents each, all rated.** Qwen3.5-9B comes out
ahead, Gemma 2 9B second, and the Llama 3.1 8B models are in the middle of the pack. Ratings are by Claude,
blind to the model, and have not yet been spot-checked by Wes (see "How far to trust it").

## How the models were tried

- On the Mac, one model at a time, through the real PromptBuilder and OracleModelClient, so what was seen is what
  the Printer will do. Q4_K_M for all, thinking mode off.
- **Frozen:** Wes's oracle prompt (the "passage" prompt with the style example and the theme, in the config),
  temperature 0.3, one exemplar per portent, at most 300 tokens, the corpus and its draw weights.
- **The benchmark:** 16 hinges (8 typed by Wes, 8 drawn at random from the scorer's sample sentences), 10 draws of
  exemplars per hinge, each portent made once: 160 per model, 1,120 in all. A draw is a seed, so every model saw
  the same 160 hinge-and-exemplar pairs.
- **Rated** against seven yes/no marks (follows the hinge, follows the style, doesn't repeat the hinge, didn't
  muddle the hinge's details, no extra text, length close, particularly creative). Three are checked by a program,
  the rest by reading. The portents were shuffled across models and the names hidden while rating.
- Times are Mac times. The Pi will be slower and has not been measured.

## The results

Each rubric question gets its own section below, with all seven models side by side. Counts are portents out of
160 that passed the mark, so higher is better, and models are listed best first. The overall score comes first
and speed comes last, as the least important: the Printer types slowly, so a second of model time hardly matters.
The same numbers are in a Google Sheet, one row per question and one column per model.

### Overall score

Mean score out of 7 (one point per mark passed), with a 95% interval from resampling the hinges:

- Qwen3.5-9B: 6.08 (5.91 to 6.25)
- Gemma 2 9B: 5.84 (5.64 to 6.04)
- Hermes 3: 5.26 (5.01 to 5.50)
- Ministral 8B Instruct: 5.21 (5.04 to 5.38)
- Llama 3.1 8B Instruct: 5.19 (4.96 to 5.40)
- Stheno v3.4: 4.73 (4.33 to 5.06)
- Qwen3-8B: 4.45 (4.19 to 4.70)

What is and is not separated, paired over the same 160 cells:
- Qwen3.5-9B beats Llama 3.1 Instruct by 0.89 (0.63 to 1.16): ahead in 91 cells, tied in 54, behind in 15. Clear.
- Gemma 2 9B beats Llama 3.1 Instruct by 0.65 (0.29 to 0.99), and Hermes 3 by 0.57 (0.34 to 0.84). Clear.
- Qwen3.5-9B against Gemma 2 9B is +0.24 (-0.04 to +0.55): a lead, not a proven one.
- Hermes 3, Ministral 8B and Llama 3.1 Instruct cannot be told apart (differences of 0.03 to 0.07).
- Qwen3-8B is clearly the weakest: 0.74 below Llama 3.1 Instruct.

Perfect portents (7 of 7): Qwen3.5-9B 75, Gemma 2 9B 64, Ministral 27, Llama 3.1 Instruct 26, Stheno 17, Hermes 3
16, Qwen3-8B 16.

No model wins every question, and Qwen3.5-9B wins only one (creative). It comes first overall because it has no
weak mark: it is within 18 portents of the best on every question. The models behind it each have a question
where they fall well back (Gemma 2 on style, Llama 3.1 Instruct on the hinge, Qwen3-8B on style and length).

At a glance (share of each model's 160 portents that passed, in percent):

| Question | Qwen3.5-9B | Gemma 2 9B | Hermes 3 | Ministral 8B | Llama 3.1 Inst. | Stheno | Qwen3-8B |
|---|---|---|---|---|---|---|---|
| Follows the hinge | 89.38 | 91.25 | 80.63 | 73.75 | 57.50 | 86.25 | 98.75 |
| Follows the style | 95.00 | 73.75 | 83.13 | 90.00 | 96.25 | 55.00 | 25.63 |
| Doesn't repeat the hinge | 93.75 | 98.75 | 91.88 | 90.63 | 98.75 | 70.00 | 58.75 |
| Doesn't muddle the hinge's details | 86.88 | 90.00 | 79.38 | 69.38 | 54.38 | 83.75 | 98.13 |
| No extra text | 99.38 | 100.00 | 96.88 | 86.25 | 100.00 | 93.13 | 100.00 |
| Length close to the exemplar | 93.13 | 87.50 | 81.88 | 90.63 | 93.75 | 72.50 | 51.25 |
| Particularly creative | 50.63 | 42.50 | 12.50 | 20.63 | 18.13 | 11.88 | 12.50 |

(Rounded half up. The counts out of 160 are in the sections below and in the Google Sheet.)

### Follows the hinge

Is the portent about what the hinge was about, not some other subject?

- Qwen3-8B 158, Gemma 2 146, Qwen3.5-9B 143, Stheno 138, Hermes 3 129, Ministral 118, Llama 3.1 Instruct 92.
- Four models are within 20 of each other at the top. Qwen3-8B's near-perfect score comes with a catch: it treats
  the hinge as the start of a story and writes on from it (see style and repeats).
- Llama 3.1 Instruct is far behind: in 68 portents it kept the exemplar's form and lost the hinge's subject.
  Ministral (42 misses) is the next worst.

### Follows the style

Does the portent have the exemplar's form, voice and kind of text (verse stays verse, a notice stays a notice)?

- Llama 3.1 Instruct 154, Qwen3.5-9B 152, Ministral 144, Hermes 3 133, Gemma 2 118, Stheno 88, Qwen3-8B 41.
- The reverse of the hinge question for Llama: it is the best at form and the worst at subject.
- Gemma 2 is the weakest of the top group here (42 misses). Stheno and Qwen3-8B mostly ignore the exemplar's form:
  Qwen3-8B missed in 119 of 160.

### Doesn't repeat the hinge

Checked by a program (a long run of the hinge's words, with a reader override). Does the portent say something
new, not hand the hinge back?

- Gemma 2 158, Llama 3.1 Instruct 158, Qwen3.5-9B 150, Hermes 3 147, Ministral 145, Stheno 112, Qwen3-8B 94.
- Gemma 2 and Llama 3.1 Instruct repeated the hinge twice each in 160.
- Stheno (48 repeats) and Qwen3-8B (66) repeat the hinge often; for Qwen3-8B this is the same habit as writing on
  from the hinge.

### Doesn't muddle the hinge's details

Are the hinge's facts (who, what, how many) kept true, not swapped or invented?

- Qwen3-8B 157, Gemma 2 144, Qwen3.5-9B 139, Stheno 134, Hermes 3 127, Ministral 111, Llama 3.1 Instruct 87.
- This runs almost in step with "follows the hinge": a portent that lost the subject usually muddled the details
  too, so the order is nearly the same. Llama 3.1 Instruct is last again; a hinge about a father often came out
  as a mother.

### No extra text

Checked by a program. No code fence, heading, preface, label or note around the portent.

- Gemma 2, Llama 3.1 Instruct and Qwen3-8B 160, Qwen3.5-9B 159, Hermes 3 155, Stheno 149, Ministral 138.
- Almost everyone passes. Ministral is the one that adds things (22 portents with a fence, heading or note).
  Stheno is the only model that echoed the whole prompt back as its answer (six times).

### Length close to the exemplar

Checked by a program: between half and double the exemplar's length.

- Llama 3.1 Instruct 150, Qwen3.5-9B 149, Ministral 145, Gemma 2 140, Hermes 3 131, Stheno 116, Qwen3-8B 82.
- Qwen3-8B often runs on (78 misses), and two Stheno portents ran to the 300-token limit.

### Particularly creative

The strictest mark, and partly a matter of taste: a portent a reader would want to keep.

- Qwen3.5-9B 81, Gemma 2 68, Ministral 33, Llama 3.1 Instruct 29, Hermes 3 20, Qwen3-8B 20, Stheno 19.
- The only question with a clear split: the two leaders have about twice the creative portents of the rest.
- Llama 3.1 Instruct tends to rewrite the exemplar with the nouns swapped, which is accurate but seldom creative.

### Speed

Median seconds per portent on the Mac. Times on the Pi are not measured and will be longer.

- Ministral 0.6 s, Llama 3.1 Instruct 0.7 s, Hermes 3 1.0 s, Qwen3.5-9B 1.3 s, Stheno 1.3 s, Gemma 2 1.7 s,
  Qwen3-8B 2.0 s.
- The spread is about a second and a half, which does not matter beside the printer's own typing time. Speed
  would only decide between models that are otherwise tied (Hermes 3, Ministral and Llama 3.1 Instruct).

## Qwen3.5-9B

The newest of the group. unsloth GGUF, Q4_K_M, 6.2 GB on disk. License to be checked.

Positive:
- The best score, and the most balanced: it stays on the hinge, keeps the form, and rarely repeats the hinge or
  adds extra text. 75 of 160 portents were perfect.
- The most creative by a wide margin, and it does this without losing the form.
- No thinking text leaked in any of the 160, with thinking turned off the way the client does it.
- Reasonable speed on the Mac, about 1.3 seconds.

Negative:
- It still changes details or drifts from the hinge in about one portent in eight.
- At 9B it is the biggest of the group; time on the Pi has not been measured.

## Gemma 2 9B

Google's model, bartowski GGUF, Q4_K_M, 6.4 GB on disk. Google's Gemma terms of use apply; check them.

Positive:
- Second best. Tied with Llama 3.1 Instruct for the fewest repeated hinges (2 of 160), and added no extra text.
- Often very good: 68 portents marked creative.
- Stays on the hinge's subject well.

Negative:
- The weakest at style among the top models: 42 portents did not follow the exemplar's form or tone.
- Slower than most on the Mac, about 1.7 seconds (only Qwen3-8B was slower).

## Hermes 3 (Llama 3.1 8B)

Nous Research's fine-tune, their own GGUF, Q4_K_M, about 5 GB.

Positive:
- Fast, about 1.0 second. Level with Llama 3.1 Instruct and Ministral overall.
- Several very good portents when the exemplar was verse, a fortune, a Bible verse or a short exchange.

Negative:
- Uneven: errors are spread over every mark (hinge, details, length, style).
- With a dry exemplar (a style-manual rule, a notice) it tends to repeat the exemplar or swap synonyms into it.
- Only 16 portents were perfect.

## Ministral 8B Instruct 2410

Mistral's 8B, bartowski GGUF, Q4_K_M, about 5 GB. I believe its license is non-commercial; check it.

Positive:
- The fastest, about 0.6 seconds, and its length was close to the exemplar's in 145 of 160.
- Good at the form: only 16 portents missed the style.

Negative:
- Drifts from the hinge or changes its details more than the leaders (42 and 49 portents).
- The most extra text: 22 portents carried a code fence, heading or note.

## Llama 3.1 8B Instruct

Meta's model, unsloth GGUF, Q4_K_M, about 5 GB. Meta's Llama community license applies.

Positive:
- The tidiest: no extra text in any of the 160, almost no repeated hinges, almost always the right form and length.
- Fast, about 0.7 seconds.
- Tolerated the prompt as written, with no rule juggling.

Negative:
- It often drops the hinge's subject or changes its details (68 and 73 portents): it keeps the exemplar's form but
  loses what the portent is meant to be about. A hinge about a father often became a mother.
- It tends to rewrite the exemplar with the nouns swapped, so it is rarely creative (29).

## Stheno v3.4 (Llama 3.1 8B)

A creative-writing and roleplay fine-tune of Llama 3.1, bartowski GGUF, Q4_K_M. Its license is CC-BY-NC-4.0, which
rules out commercial use; check that against the plans for the installation.

Positive:
- Stays on the hinge's subject (22 misses), and keeps the details true (26).

Negative:
- Poor at style: 72 portents ignored the exemplar's form, and 48 repeated the hinge.
- The only model that echoed the whole prompt back as its answer, six times, and 11 portents carried extra text.
- Two portents ran on until the 300-token limit.
- Fragile: a prompt that worked on the other models did not work on it. It may do better with a prompt written for
  it; that has not been tried.

## Qwen3-8B

The 8B in the same family as the scorer's 4B. Q4_K_M, about 5 GB.

Positive:
- Always stays on the hinge and keeps its details (158 and 157 of 160), the best of any model on those two.
- No extra text.

Negative:
- It ignores the exemplar's form (119 misses) and its length (78), and repeats the hinge in 66 portents. It
  treats the hinge as the beginning of a story and writes on from it.
- The slowest of all on the Mac, about 2 seconds.

## How far to trust it

- One rater, Claude, with no second opinion yet. Where the marks are judgments (hinge, style, details, creative)
  another reader could differ. Wes spot-checking about 20 of the 1,120 would show how much.
- Blind to the model while rating, but the exemplar and the portent are enough to recognise a model's habits, so the
  blindness is partial.
- 160 portents per model from 16 hinges. The exemplar drawn matters about as much as the model, which is why the
  intervals above come from resampling hinges and the comparisons are paired on the same cells.
- "Particularly creative" is a strict mark and a matter of taste; treat its totals as a guide, not a measurement.
- One prompt. A different prompt could change the order, especially for Stheno and for models that happen to need
  their own wording.
- The first pass (8 hinges, 6 draws, four models, a different sample of portents) is in `results/first-pass/` and
  `ratings/first-pass/`. It agreed with this run on the order of the four models it covered, except that Hermes
  and Llama 3.1 Instruct are now level.

## Where it stands

Qwen3.5-9B is the candidate to beat, with Gemma 2 9B close behind. Before choosing:
- Check each license against the plans for the installation (Qwen3.5, Gemma 2, Ministral, Stheno).
- Time the top two on the Pi, with whisper and the 4B scorer running, since both are 9B.
- Spot-check the ratings, and try a prompt tuned for the top two.
- Bigger models (Mistral Nemo 12B, Qwen3-14B) are not needed unless the 9B models prove too limiting.

## Awards

One award per thing a model was best (or worst) at, from the numbers above. Ties are named as ties.

- **Best Overall:** Qwen3.5-9B, mean 6.08 of 7.
- **Most Creative:** Qwen3.5-9B, 81 portents (50.63%). Gemma 2 9B is the runner-up with 68 (42.50%).
- **Most Perfect Portents:** Qwen3.5-9B, 75 of 160 (46.88%) scored 7 of 7. Gemma 2 9B had 64.
- **Truest to the Form:** Llama 3.1 8B Instruct, 154 (96.25%) followed the exemplar's style. Qwen3.5-9B is close
  behind at 152.
- **Most Faithful to the Hinge:** Qwen3-8B, 158 (98.75%) stayed on the hinge's subject. It also takes **Keeps Its
  Facts Straight** with 157 (98.13%) on the hinge's details. Among the other models, Gemma 2 9B leads both
  (146 and 144).
- **Most Original** (least hinge repeated): a tie between Gemma 2 9B and Llama 3.1 8B Instruct, 158 each (98.75%).
- **Cleanest Output:** a three-way tie at 160 (100.00%) for Gemma 2 9B, Llama 3.1 8B Instruct and Qwen3-8B,
  none of which added extra text.
- **Best Sense of Proportion** (length close to the exemplar): Llama 3.1 8B Instruct, 150 (93.75%), with
  Qwen3.5-9B at 149.
- **Fastest:** Ministral 8B, 0.59 seconds a portent on the Mac. Llama 3.1 8B Instruct is next at 0.72.
- **Most Balanced:** Qwen3.5-9B, within 18 portents of the best on every question.

And the other kind:

- **Most Likely to Write a Sequel:** Qwen3-8B, which treats the hinge as the first line of a story. It repeated the
  hinge in 66 portents and missed the exemplar's style in 119.
- **Most Likely to Turn a Father into a Mother:** Llama 3.1 8B Instruct. It kept the form and lost the subject in
  68 portents (57.50% on the hinge, the lowest of any model).
- **Most Likely to Hand Your Prompt Back:** Stheno v3.4, the only model to echo the whole prompt as its answer
  (six times).
- **Chattiest:** Ministral 8B, with a fence, heading or note around 22 of its portents (86.25% clean).
- **Slowest to Make Up Its Mind:** Qwen3-8B, 2.0 seconds a portent.
- **Least Creative:** Stheno v3.4, 19 portents (11.88%), narrowly ahead of Hermes 3 and Qwen3-8B at 20.
