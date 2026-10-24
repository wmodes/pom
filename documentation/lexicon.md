# Lexicon

The words this project uses, so that code, docs, prompts and conversation all mean the same thing.
Use these terms; propose a new one before using it. Terms marked † were added by Claude for Wes to
confirm.

## The project

- **The Printer** †: the whole installation, an IBM Wheelwriter 1000 driven by a Raspberry Pi, which
  overhears a room and now and then types a portent.
- **Observation Session** †: a public showing of the Printer (the first is planned for December 18).
- **experiments**: the set-aside place (`experiments/`) where we prototype. Nothing there is part of
  the Printer until it is rebuilt in `src/pom/`.

## Listening: from sound to text

- **segment**: whisper's raw output, using whisper's own name for it.
- **chunk**: the mechanical split of segments; the thing that gets scored.
- **ambient caption** †: whisper's guess at a sound in the room, like `(dog barks)` or `[Music]`.
  Never scored; kept as context for the voice.
- **lyrics** †: sung words whisper marks with music symbols. They are words, so they are scored.

## Scoring and triggering: choosing what to answer

- **score**: the process of assigning a value to each incoming chunk, and the value itself
  (1.00 to 9.99). The Scorer does it with a small language model.
- **filter** †: the check in front of the Scorer that gives chunks with no content words (such as
  "or not.") the lowest score without asking the model.
- **fallback** †: the rough word-based score used when the model does not answer.
- **avg_convo_floor** †: the line below which scores are ignored and do not feed the heat; for now
  a fixed fraction of the score range.
- **avg_convo_heat** †: a decaying moving average of the scores above the floor; how interesting
  the room's talk has been lately.
- **trigger** †: the moment a score rises far enough above the heat (by `trigger_pct`) for the
  Printer to answer. That chunk becomes the hinge.
- **hinge** (or **the hinge**): the text sent to the language model as the basis for any portent.

## Writing: from hinge to portent

- **voice** †: the language model that writes the portent from the hinge and the exemplars.
- **corpus**: the material from which the language model draws example material for its output.
- **register** †: one kind of writing in the corpus (the 14 registers include `nonsense`).
- **source** †: one text in the corpus, such as Alice's Adventures in Wonderland.
- **exemplars** †: the pieces of corpus drawn at random for one portent, as examples of voice.
- **weights**: values we assign to scale things proportionally, such as how often each register
  and source is drawn.
- **portent**: the output of the language model that is ultimately typed.

## Where the code does not match yet †

These names predate the lexicon. Each rename is Wes's decision.

- The code's **`Chunk`** and **`ChunkStore`** are pieces of the *corpus*, not of what was heard. The
  corpus docs call those pieces *utterances*.
- The code's **`Fragment`**, **`Fragmenter`** and **`ScoredFragment`** are what this lexicon calls
  chunks.
- The hearing-debug **`Segmenter`** cuts *audio* by loudness before whisper runs; it does not
  produce segments in whisper's sense.
- **`InterestClient`** and the word "interest" describe scoring.
- Older docs say **pronouncement** for portent.
