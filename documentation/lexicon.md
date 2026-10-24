# Lexicon

The words this project uses, so that code, docs, prompts and conversation all mean the same thing.
Use these terms; propose a new one before using it. Terms marked † were added by Claude for Wes to
confirm.

A term that names a model always ends in *model*: oracle model, scorer model.

## The project

- **The Printer** †: the whole installation, an IBM Wheelwriter 1000 driven by a Raspberry Pi, which
  overhears a room and now and then types a portent.
- **experiments**: the set-aside place (`experiments/`) where we prototype. Nothing there is part of
  the Printer until it is rebuilt in `src/pom/`.

## Listening: from sound to text

- **segment**: whisper's raw output, using whisper's own name for it.
- **fragment**: one piece of a split segment, usually a sentence; the thing that gets scored.
- **ambient caption**: whisper's guess at a sound in the room, like `(dog barks)` or `[Music]`.
  Never scored; kept as context for the oracle model.
- **lyrics** †: sung words whisper marks with music symbols. They are words, so they are scored.

## Scoring and triggering: choosing what to respond to

- **score**: the process of assigning a value to each incoming fragment, and the value itself
  (1.00 to 9.99). The scorer does it, mostly with the scorer model.
- **scorer**: the component that scores fragments: the fragment_filter, then the scorer model,
  with a fragment left unscored if the scorer model doesn't reply.
- **scorer model**: the small language model that scores fragments (Qwen3-4B for now).
- **fragment_filter**: the check in front of the scorer model that gives fragments with no content
  words (such as "or not.") the lowest score without asking the scorer model.
- **unscored** †: a fragment the scorer model gave no score; its score is None. The trigger and
  avg_convo_interest leave it out.
- **fallback**: shelved, unused: a rough word-based score once used when the scorer model did not
  reply (`fallback_scorer.py`).
- **word tagger** †: the component that labels each word of a fragment as a name, number, filler,
  stopword or content word. The fragment_filter uses its labels.
- **content word** †: a word that is not filler, a stopword or a number. A fragment with too few
  content words skips the scorer model.
- **avg_convo_floor** †: the line below which scores are ignored and do not feed avg_convo_interest;
  for now a fixed fraction of the score range.
- **avg_convo_interest** †: a decaying moving average of the scores above the floor; how
  interesting the room's talk has been lately.
- **trigger** †: the moment a score rises far enough above avg_convo_interest (by `trigger_pct`)
  for the Printer to respond.
- **hinge** (or **the hinge**): the fragment chosen at trigger; the basis for the portent.

## Corpus: the material the oracle model draws on

- **corpus**: the material exemplars are drawn from.
- **register** †: one kind of writing in the corpus (the 14 registers include `nonsense`).
- **source** †: one text in the corpus, such as Alice's Adventures in Wonderland.
- **chunk**: one piece of the prepared corpus (made by the chunker); exemplars are drawn from
  chunks.
- **exemplar**: a chunk drawn by weight for one portent, as an example of style.
- **draw weights**: values we assign to scale things proportionally, such as how often each
  register and source is drawn. Not to be confused with a model's weights.

## Keeping going: faults

- **fault** †: a problem a module recovered from and recorded instead of crashing.
- **fault recorder** †: the component that keeps a module's faults (WARNING means recovered,
  CRITICAL means the module cannot do its job). The Printer never stops because of a fault.

## Writing: from hinge to portent

- **oracle model**: the medium language model that writes the portent from the hinge and the
  exemplars.
- **portent**: the oracle model's output; what gets typed.

## Where the code does not match yet †

These names predate the lexicon. Each rename is Wes's decision.

- The hearing-debug **`Segmenter`** cuts *audio* by loudness before whisper runs; it does not
  produce segments in whisper's sense.
- Older docs say **pronouncement** for portent, and a few say **chunk** for fragments.
