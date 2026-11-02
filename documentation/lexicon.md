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
- **config** †: the settings, in `config/pom.toml` (committed, the quiet defaults the Printer runs with) over built-in defaults,
  under the gitignored `config/pom.local.toml` (where the monitoring site's password lives) and then environment variables. A bad
  value means the default plus a warning. Everything a person might tune is in it.
- **AI ledger** †: `documentation/ai-ledger.md`, the record of remote AI (Claude) use. Local models are never counted.

## Listening: from sound to text

- **segment**: whisper's raw output, using whisper's own name for it.
- **fragment**: one piece of a split segment, usually a sentence; the thing that gets scored.
- **audio source** †: anything that gives out 30 ms frames of sound: the microphone (`MicSource`; `MacMicSource` on a Mac) or, for
  tests and replays, a recording (`WavSource`).
- **rolling cutter** †: the component that cuts the stream: every few seconds it hands whisper the audio since the last kept
  segment and keeps the segments whisper finished (`RollingCutter`).
- **whisper text sorter** †: the component that sorts whisper's text into words, ambient captions and nothing-here marks
  (`WhisperTextSorter`).
- **listener** †: the component that joins the audio source, the rolling cutter and the whisper text sorter, and hands on what
  was heard (`Listener`).
- **heard segment** †: one thing the listener heard that was new: the words (speech or lyrics), any new ambient captions, and
  where in the stream it ended (`HeardSegment`).
- **segment fragmenter** †: the component that cuts a heard segment's words into fragments, each stamped with a time. Sentences are
  what get scored (`SegmentFragmenter`; the levels are `FragmentLevel`).
- **whisper client** †: the component that talks to whisper-server: it sends audio and gets segments back (`WhisperServerClient`).
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
- **bar** †: the score a new score must be above to trigger: the `percentile` of the window's scores.
- **percentile** †: how much of the window a score must beat to trigger (`percentile`, 99.5 to begin with; 100 is the best score).
- **trigger** †: the moment a score is above the bar, once the window holds `min_scores` scores and the
  cooldown is over. The score is compared with the bar as it stood before that score was added. Then the Printer
  responds.
- **cooldown** †: how long after a trigger the next may come, `cooldown_seconds` (60 to begin with; 0 for none). A
  score above the bar inside the cooldown is held back but still joins the window.
- **hinge** (or **the hinge**): the fragment chosen at trigger; the basis for the portent.
- **trigger algorithm** †: how the bar is worked out (`algorithm` in `[trigger]`): `percentile` (a percentile of the window, as the
  entries above describe it), `decaying_quantile` (a percentile in which a score counts half as much every `half_life_seconds`; the
  shipped choice) or `moving_average` (a decaying average plus a margin). Each is a `TriggerBar` with its own settings. With
  `exclude_triggers` on, a score that triggered is left out of the window, so one zinger does not hold the bar up.
- **starting bar** †: `starting_bar`, where the bar begins before the room has said enough for one to be worked out; it fades as the
  room speaks. There is no silent warm-up.
- **score effect** †: what the trigger did with a score: *ignored* (no usable score), *counted*, *triggered*, *cooling down* (above
  the bar but inside the cooldown), or *judged* (a typed score that stayed under the bar and out of the window) (`ScoreEffect`).
- **scored sentence** †: one thing heard or typed, with its score and what the trigger made of it (`ScoredSentence`). Its kind is
  speech, lyrics, ambient or typed; the one the trigger fired on is the hinge.

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
- **startup** †: the one-time preparation when the Printer starts: the config's corrections, the data, the microphone, whisper, the
  scorer model and oracle model servers and the terminal, each a step that is checked, and a one-line readiness summary
  (`Startup`, `startup.py`). Only the runner is built; the steps are placeholders.

## Writing: from hinge to portent

- **oracle model**: the medium language model that writes the portent from the hinge and the
  exemplars.
- **portent**: the oracle model's output; what gets typed.
- **oracle prompt** †: the text the oracle model is given, written by Wes in the config (`oracle.prompt`), with
  `{exemplars}` and `{hinge}` marking where those go.
- **prompt builder** †: the component that fills the exemplars and the hinge into the oracle prompt
  (`PromptBuilder`).
- **trigger record** †: one trigger and what came of it: the hinge, its score, the bar it beat, the window, the exemplars
  shown and the portent (`TriggerRecord`). **Trigger history**: the last hour of them, newest first, kept in memory only and
  never written down (`TriggerHistory`), for the monitoring site.
- **monitoring site** †: the password-protected web page of the last hour of triggers, with a detail page for each: the hinge,
  the exemplars, the portent, the score and the bar (`MonitorSite`, settings in `[monitor]`). Open it from another machine on the
  Printer's network.
- **score chart** †: on the monitoring site, the chart of every scored sentence's score against the bar over the last hour, with each
  trigger marked. Numbers only, never words (`ScoreTrace`, `render_score_chart`).
- **oracle model client** †: the component that talks to the oracle model's llama-server (`OracleModelClient`).
- **hinge finder** †: the component that joins the listener, the fragmenter, the scorer and the trigger, and yields each
  thing heard as a `ScoredSentence`; the one the trigger fired on is the hinge (`HingeFinder`).
- **portent writer** †: the component that takes a hinge and returns a portent, joining the prompt builder and the oracle
  model client (`PortentWriter`). What it returns is a `Portent`: the text, the exemplars shown, the seconds, and whether it was cut off.

## Output: from portent to paper

The code may run on any Raspberry Pi with any terminal, so these words say *terminal* and *typewriter*. A machine's name appears only
where a fact belongs to it.

- **terminal** †: the serial connection to the typewriter, which acts as a terminal (settings in `[terminal]`). The Printer sends it
  text and the keyboard sends keys back.
- **CadetWriter** †: the open-source board that turns the IBM Wheelwriter 1000 into a serial terminal. Its own settings (line ending,
  flow control, duplex) must match `[terminal]`: a mismatch loses text or types every character twice.
- **typewriter formatter** †: the component that makes a portent printable: code fences out, plain ASCII only, wrapped to the line
  width, and ending with `linefeeds_after_portent` line feeds (4) so the paper has moved on (`TypewriterFormatter`, `[output]`).
- **quote** †: a piece of a portent the oracle model fenced in triple backticks. The typewriter has no italics, so a quote is typed
  indented `quote_indent` (4) spaces with a blank line before and after; the fence lines are never printed.
- **line width** †: how many characters fit on a line, worked out from `paper_width_inches`, `margin_inches` and `characters_per_inch`
  (65 for 8.5 inch paper, 1 inch margins and 10 characters per inch).
- **print wheel** †: the wheel of characters the typewriter prints with. Its pitch is `characters_per_inch` (10, 12 or 15).
- **composed characters** †: the nine ASCII characters most print wheels lack, `< > \ ^ ` { | } ~`, which the CadetWriter builds from
  periods and micro-movements ("period graphics"), slowly and dotty. `<` and `>` can carry meaning, so they are always typed as they
  are. The other seven are swapped for look-alikes (`` ` `` becomes `'`, `~` becomes `-`, `{ }` become `( )`, `\` and `|` become `/`,
  `^` is dropped) unless `keep_composed_characters` keeps them; `ascii_printwheel` keeps all seven.
- **typewriter port** †: the component that sends formatted text over the terminal's serial line, paced at `characters_per_second`
  (16) with flow control and the line ending the CadetWriter expects, waits `finish_wait_seconds` after the last character, and
  reads the keyboard coming back (`TypewriterPort`). The **console typewriter** † is the same code typing on the screen, for
  development (`ConsoleTypewriter`).
- **echoplex** †: the old terminal name for the host echoing each key back as it arrives (`echoplex`). With it on, backspace moves
  the carriage left and edits the typed line; the CadetWriter does not use the typewriter's correction tape.
- **type during output** †: `type_during_output`. False (the default) throws away any keys pressed while a portent is being typed,
  and any half-typed line, so nothing typed over a portent appears after it. True keeps them for the next line read.

## Typing and turns: the keyboard and one thing at a time

- **typed line**: a line typed at the terminal's keyboard, someone speaking to the Printer directly rather than being overheard.
  It does not go through the listener. The scorer scores it, the typed score multiplier is applied, and the same bar judges it
  (`TypedLineScorer`, which yields a `ScoredSentence` of kind `typed`). It has no cooldown, and a typed line under the bar is
  ignored: nothing is printed. A typed score stays out of the window unless `typed_lines_affect_bar` is on.
- **typed score multiplier**: what a typed line's score is multiplied by before it meets the bar (`typed_score_multiplier`, 1.5 to
  begin with; 1 is no bonus). The product is not capped at 9.
- **turn**: one hinge taken through to paper: the portent written, formatted and typed. The Printer does not listen during a turn.
- **half-duplex gate**: *(planned)* the keeper of the turn: the state (listening, generating, printing, recovering), the rule that
  one hinge takes the floor at a time (first come, first served), and the watchdog that returns to listening if a state runs too
  long (`HalfDuplexGate`).
- **keyboard watcher**: *(planned)* the component that reads typed lines while the Printer is listening and yields each as a
  `ScoredSentence`, as the hinge finder does for the room (`KeyboardWatcher`).
- **Conductor**: *(planned)* the one part that knows the whole pipeline. It runs the hinge finder and the keyboard watcher in threads
  of their own, takes each hinge through a turn (write, format, type) while the Printer does not listen, and feeds the monitoring
  site. The entry point starts it.

## Where the code does not match yet †

These names predate the lexicon. Each rename is Wes's decision.

- The hearing-debug **`Segmenter`** cuts *audio* by loudness before whisper runs; it does not
  produce segments in whisper's sense.
- Older docs say **pronouncement** for portent, and a few say **chunk** for fragments.
- The part that decides whether a score triggers is `ConvoInterestTracker` (`convo_interest_tracker.py`); the lexicon's word for it
  is the **trigger**. `TriggerJudge` was proposed as a name (not decided).
