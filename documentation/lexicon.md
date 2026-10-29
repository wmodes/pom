# Lexicon

The words this project uses, so that code, docs, prompts and conversation all mean the same thing.
Use these terms; propose a new one before using it. Terms marked † were added by Claude for Wes to
confirm.

A term that names a model always ends in *model*: oracle model, scorer model.

## The project

- **The Printer** †: the whole installation, an IBM Wheelwriter 1000 driven by a Raspberry Pi, which
  overhears a room and now and then types a portent.
- **playground** †: a small tool that lets you type into one built part of the Printer and see what it does, using the real
  modules (`scripts/dev/`). The old experiment folders `oracle-playground` and `hearing-debug` did this before the parts were
  built; the playgrounds replace them for the built parts.
- **experiments**: the set-aside place (`experiments/`) where we prototype. Nothing there is part of
  the Printer until it is rebuilt in `src/pom/`.

## Listening: from sound to text

- **segment**: whisper's raw output, using whisper's own name for it.
- **fragment**: one piece of a split segment, usually a sentence; the thing that gets scored.
- **audio source** †: anything that gives out 30 ms frames of sound: the microphone (`MicSource`) or, for tests and replays,
  a recording (`WavSource`).
- **rolling cutter** †: the component that cuts the stream: every few seconds it hands whisper the audio since the last kept
  segment and keeps the segments whisper finished (`RollingCutter`).
- **whisper text sorter** †: the component that sorts whisper's text into words, ambient captions and nothing-here marks
  (`WhisperTextSorter`).
- **listener** †: the component that joins the audio source, the rolling cutter and the whisper text sorter, and hands on what
  was heard (`Listener`).
- **heard segment** †: one thing the listener heard that was new: the words (speech or lyrics), any new ambient captions, and
  where in the stream it ended (`HeardSegment`).
- **ambient caption**: whisper's guess at a sound in the room, like `(dog barks)` or `[Music]`.
  Never scored; kept as context for the oracle model.
- **lyrics** †: sung words whisper marks with music symbols. They are words, so they are scored.

## Scoring and triggering: choosing what to respond to

- **score**: the process of assigning a value to each incoming fragment, and the value itself
  (0 to 9, anywhere in between). The scorer does it, mostly with the scorer model. The model answers with
  one digit but is rarely certain, so the score is the average of the digits it might say, each weighted by
  how likely the model thinks it is: 75% sure of 6 and 25% sure of 7 scores 6.25. A model that is certain
  scores a whole number.
- **scorer**: the component that scores fragments: the fragment_filter, then the scorer model,
  with a fragment left unscored if the scorer model doesn't reply.
- **scorer model**: the small language model that scores fragments (Qwen3-4B for now).
- **fragment_filter**: the check in front of the scorer model that gives fragments with no content
  words (such as "or not.") a fixed low score, `fragment_filter_score`, without asking the scorer model.
- **unscored** †: a fragment the scorer model gave no score; its score is None. The
  ConvoInterestTracker leaves it out.
- **fallback**: shelved, unused: a rough word-based score once used when the scorer model did not
  reply (`fallback_scorer.py`).
- **word tagger** †: the component that labels each word of a fragment as a name, number, filler,
  stopword or content word. The fragment_filter uses its labels.
- **content word** †: a word that is not filler, a stopword or a number. A fragment with too few
  content words skips the scorer model.
- **window** †: the room's recent scores the trigger is judged against: the scores heard in the last
  `window_seconds` (300, the last five minutes), or the last `min_scores` (20) if the room is so quiet that
  fewer were heard in that time. A length of time, not a count of scores. Every score joins it, the lowest too.
- **fence** †: the bar a score must be above to trigger: Q3 + `fence_multiple` x IQR of the window (Tukey's
  fence). Q3 is the score three quarters of the way up the window's scores, Q1 a quarter of the way up, and the
  IQR (interquartile range) is Q3 minus Q1. `fence_multiple` is 0.9 to begin with.
- **trigger** †: the moment a score is above the fence, once the window holds `min_scores` scores and the
  cooldown is over. The score is compared with the fence as it stood before that score was added. Then the Printer
  responds.
- **cooldown** †: how long after a trigger the next may come, `cooldown_seconds` (60 to begin with; 0 for none). A
  score above the fence inside the cooldown is held back but still joins the window.
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
- **oracle prompt** †: the text the oracle model is given, written by Wes in the config (`oracle.prompt`), with
  `{exemplars}` and `{hinge}` marking where those go.
- **prompt builder** †: the component that fills the exemplars and the hinge into the oracle prompt
  (`PromptBuilder`).
- **hinge finder** †: the component that joins the listener, the fragmenter, the scorer and the trigger, and yields each
  thing heard as a `ScoredSentence`; the one the trigger fired on is the hinge (`HingeFinder`).
- **portent writer** †: the component that takes a hinge and returns a portent, joining the prompt builder and the oracle
  model client (`PortentWriter`). What it returns is a `Portent`: the text, the exemplars shown, the seconds, and whether it was cut off.

## Where the code does not match yet †

These names predate the lexicon. Each rename is Wes's decision.

- The hearing-debug **`Segmenter`** cuts *audio* by loudness before whisper runs; it does not
  produce segments in whisper's sense.
- Older docs say **pronouncement** for portent, and a few say **chunk** for fragments.
