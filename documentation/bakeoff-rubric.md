# Bake-off rubric

How a portent is rated in the oracle model bake-off. The benchmark, how it is run and what is frozen are in
`experiments/oracle-bakeoff/README.md`; the results so far are in `documentation/model-bakeoff.md`.

Each portent gets seven yes/no marks, all worded so that **yes is good**. A portent's score is its number of
yeses, out of 7. A model's score is the share of yeses on each item over all of its portents, and its speed.

If a model gave no portent at all (it failed or timed out), that portent is a no on all seven.

## The seven marks

1. **Follows the hinge.** The portent plainly takes its meaning from the hinge: its subject, image or event. It
   may go somewhere else with it (a metaphor, a consequence, a warning, one detail dwelt on), but it could not
   have been written without seeing the hinge. *No* if it is about something else, or only the exemplar's subject.
2. **Follows the style.** It has the exemplar's form and tone: verse stays verse, a one-line maxim stays a maxim,
   a fortune stays a fortune, an old register stays old. *No* if it is plain modern prose when the exemplar is
   not, or it ignores the layout.
3. **Doesn't repeat the hinge.** *Checked by program first:* *no* if the portent has an unbroken run of five of
   the hinge's words (or 60% of them, if the hinge is shorter). *Read afterwards:* also *no* if the portent
   merely restates the hinge in other words, which the program cannot see.
4. **Didn't muddle the hinge's details.** Who, what and how many stay true: a father stays a father, forty
   dollars stays forty dollars. Invented extras are fine if nothing in the hinge is contradicted.
5. **No extra text.** *Checked by program first:* no code fence, heading, bold title, preface ("Here is…"),
   label ("Passage:") or note. *Read afterwards:* also *no* for any other text that is not the passage itself.
6. **Length was close.** *Checked by program:* between half and double the exemplar's word count.
7. **Particularly creative.** The bonus, so expect fewer yeses. *Yes* only if marks 1 and 2 are both yes, the
   portent is not the exemplar with words swapped (a line-by-line rewrite is *no*), and it does something with
   the hinge beyond restating it. It should make a reader want to read it twice.

## Who rates, and how

- The three program-checked marks (3, 5 and 6) are filled in by `bakeoff_checks.py` the same way for every
  model. The reader may override mark 3 or 5 where reading shows the program missed something, and says why.
- The other four marks are judged by reading.
- Rating is **blind**: all models' portents are shuffled together and shown without the model's name, and the
  names are restored only for the report. The reader sees the hinge, the exemplar and the portent.
- A first rating by Claude, spot-checked by Wes: Wes re-rates a sample, and where they disagree, Wes's mark wins
  and the disagreement is a reason to tighten the wording above.

## Conventions used when rating

Judgment calls that came up in the first pass, written down so the later ratings follow the same ones:

- **Mark 4 is *no* when the hinge is ignored.** If a portent has nothing to do with the hinge there is no detail to
  keep true, so it cannot be said to have kept it. It is also *no* when a detail is changed (father becomes mother)
  or contradicted (forty dollars becomes a fortune).
- **Mark 1 is yes for a loose but real link** (a hospital monitor for a deathbed, "relic" for a tooth) and *no* when
  the portent is only about the exemplar's own subject, or about something a step away from the hinge (a different
  object, a general moral).
- **Mark 2 is about form and tone, not length.** A long poem for a short epitaph can still follow the style;
  length is judged separately by mark 6.
- **Mark 3 override.** A portent that is only the hinge again in other words (a short sentence with nothing added)
  is *no* even if the program passed it. A portent that restates the hinge and then adds to it passes.
- **Mark 7 is strict.** A line-by-line rewrite of the exemplar with the nouns swapped is *no*, however neatly done.
  *Yes* needs a new idea the exemplar and the hinge did not hold between them.
- A portent that is the exemplar word for word fails marks 1 and 4 (and 7), and passes 2 and 3.
- A portent cut off at `max_tokens` is rated on what it says; the cut-off count is reported with speed.

## Speed

Seconds per portent on the Mac, as the median and the slowest, from the client's own clock. The Pi will be much
slower and needs its own pass; the Mac numbers only rank the models.
