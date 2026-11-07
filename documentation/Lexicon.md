# Lexicon

The words this project uses, so that code, docs, prompts and conversation all mean the same thing. Use these terms; propose a new one before using it.

## The project

What the Printer is, in the world. The machinery is under *Running the Printer*; how we build it is under *Workshop*.

-   **The Printer** †: the whole installation, an IBM Wheelwriter 1000 driven by a Raspberry Pi, which overhears a room and now and then prints a portent. People can also type to it, and it may answer.
-   **Office of Ordinary Hauntings** †: the Printer's in-world home (officeofordinaryhauntings.net, case study CS-0027).
-   **Observation Session** †: the Office's in-world event, where the Printer is observed. The site's "clean room" and wireless ban are theatre; the Pi keeps its radios.
-   **nothing is kept** †: speech is transcribed and discarded. Heard words live in memory only; logs hold counts and scores, never words. This is the honest line for a door notice.

## The conventions

A name should tell you, wherever you meet it, what it is and where it lives. These rules are how we get there. A new word, or a rename, is a design choice: propose it before using it.

-   **One word per thing.** No synonyms (segment, fragment and sentence for nearly one thing was the warning), and no word with two meanings: if a word means two things, one of them changes.
-   **Sections are distinct.** No word lives in two sections. A word another part of the project already uses (source, page, volume) is not borrowed, and neither is one that collides with Python or the hardware (`Collection`).
-   **Stages are gerunds, suppliers are nouns.** The sections on the line are named for what they do (Listening, Scoring, Qualifying, Writing, Printing); the ones beside it are named for what they are (Library, Keyboard). Each stage section opens with its place: *takes ___, hands on ___ to ___*.
-   **Hand-offs are named in the pipeline.** The things that pass between stages (clip, score, hinge, portent) are defined where the pipeline is, so a word tells you which border it crosses. A data object carries its stage as a past participle: `HeardSegment`, `ScoredClip`.
-   **Components are Noun + Verber**: the thing acted on, then what is done to it (`TakeCutter`, `PromptBuilder`, `PortentWriter`).
-   **A section may borrow a metaphor** when its plain words are too generic to say where they belong. Every word in the section then comes from that one family, and the words nest the way the real things do: a passage is cut from a holding, which sits on a shelf, in the library. The metaphor has to be one a stranger could guess; cleverness that needs explaining fails.
-   **Bridge words cross on purpose.** A few words carry one thing from one section to the next (an exemplar is a library passage once it is lent to Writing; a hinge is the clip (or typed line) Qualifying hands to Writing). Each is defined where it crosses.
-   **Families without a metaphor share a word.** Where a section uses plain words, its types carry the family's word (`WhisperModelClient`, `TerminalPort`).
-   **Models end in *model*** (scorer model, oracle model), and the part that talks to one is its client (`ScorerModelClient`).
-   **Machines are general.** Say *terminal* and *typewriter*; name a machine (Wheelwriter, pom1) only where a fact belongs to it.
-   **Config keys say their units** (`window_seconds`, `characters_per_inch`).
-   **†** marks a term Claude added for Wes to confirm.

## The pipeline

How something said in the room becomes a portent on paper. Each stage has its own section below, and each hands one thing to the next; those hand-offs are named here so a word tells you which border it crosses.

```
room -> Listening -> Scoring -> Qualifying -> Writing -> Printing -> paper
                        ^                        ^
             Keyboard (typed line)      Library (exemplars)

```

-   **Listening** †: takes sound from the room, hands on *clips* to Scoring. Whisper's segments are gathered into a take, which is cut into clips.
-   **Scoring** †: takes a clip, hands on its *score* to Qualifying. Typed lines from the Keyboard join here.
-   **Qualifying** †: takes each score as an attempt, and now and then hands on a *hinge* to Writing: the clip (or typed line) that qualified against the room's recent scores.
-   **Writing** †: takes a hinge, hands on a *portent* to Printing. The oracle model writes it, shown exemplars from the Library.
-   **Printing** †: takes a portent, puts it on paper.
-   **Library** †: beside the line. It lends *exemplars* to Writing and takes nothing from the room.
-   **Keyboard** †: beside the line. A person types a *typed line*, which enters at Scoring.
-   **turn**: one hinge taken through to paper: the portent written, formatted and printed. The Printer does not listen during a turn (half-duplex).
-   **Conductor**: the one part that knows the whole pipeline. It runs the hinge finder and the keyboard watcher in threads of their own, hands each hinge that wins the floor to a turn thread (write, format, print) while the Printer does not listen, and keeps the ears in step with the floor. The entry point starts it. Example: `Conductor`

## Library: what the oracle model reads

Beside the line: lends exemplars to Writing, and takes nothing from the room.

The corpus words used to be generic data terms (chunk, source, weights). Nothing in them said they belonged together, or belonged to the corpus, and "source" already meant a microphone. A library gives the section one family of borrowed words, so any name in it, met anywhere in the code, tells you where it lives and what it goes with. The metaphor also fits the Printer: the oracle model doesn't look things up, it reads from the stacks the way bibliomancy reads a book opened at random.

-   **library**: The domain word for the whole corpus. Example: `library/` (was: corpus)
-   **holding**: Anything the library keeps, whether it's one book or a compiled set. It avoids `Collection`, which clashes with Python. Example: `Holding` (was: source)
-   **shelf**: A kind of writing, like the epitaph shelf or the horoscope shelf (14 shelves, including `nonsense`). Example: `shelf` (was: register)
-   **passage**: A piece of a holding. It avoids "page", which already means the paper. Example: `Passage` (was: chunk)
-   **catalog**: Every passage, indexed by shelf and holding. Example: `Catalog` (was: `ChunkStore`)
-   **cataloguer**: Cuts holdings into passages and files them. Example: `cataloguer.py` (was: chunker)
-   **circulation**: The set of draw weights: how the library's passages circulate, kept in one hand-edited file. Example: `Circulation`, `circulation.json` (was: draw weights)
-   **draw weight**: How often a shelf or a holding is drawn: a shelf's *share* among the shelves, or a holding's *weight* among the holdings on its shelf. Anything unlisted counts 1. Not to be confused with a model's weights. Example: `share()`, `holding_weight()` (was/still: draw weights)
-   **unfinished** (of a passage or an ending): stops before its thought is done: a lead-in whose content is elsewhere (it ends on a colon, even one followed by a dash or quotes as in `asks thee:--`, or on `viz.`, `chap.`, `fol.`, `e.g.`), or, with no closing mark, ends on a word that cannot end a thought ("the", "of", "and"). The oracle copies its exemplar's ending, so an unfinished exemplar makes an unfinished portent. Verse and comma or semicolon endings are not unfinished. Example: `ends_unfinished`, `pom.library.endings`
-   **redraw**: Drawing again when a pick lands on an unfinished passage, up to a limit for that pick, after which the unfinished one is used so the oracle is never stopped. Example: `Bibliomancer(redraw_limit=...)`, `[oracle] exemplar_redraws`
-   **bibliomancy**: Opening a book at random and reading the passage as a sign, which is what this does. Example: `Bibliomancer` (was: `ExemplarSelector`)
-   **exemplar**: A passage once it's lent for a portent. It's also the oracle prompt's word, so it stays as the bridge. Example: `exemplar` (was/still: exemplar)

## Listening: from sound to text

Takes sound from the room, hands on clips to Scoring.

We use the metaphor of a sound editor -- think Harry Caul in *The Conversation* -- to describe the actions and actors in this part of
the pipeline. Caul is both eavesdropper and editor, and so is this section: it overhears a room, then cuts what it caught into clean
pieces without knowing what they mean (meaning is Scoring's job). Whisper's own words are kept where whisper uses them. Unlike
Caul's, this tape is never kept.

    room -> tape -> lift -> segments -> selects -> tracks -> take -> clips -> Scoring

- **deck**: What plays the tape: the live microphone, or a recording played back. Example: `MicDeck`, `WavDeck`, `MacMicDeck`,
  `AudioDeck` (was: audio source, `MicSource`, `WavSource`, `MacMicSource`, `AudioSource`)
- **rolling cutter**: Every few seconds, cuts the newest length off the running tape for whisper, and keeps the segments whisper has
  finished. Example: `RollingCutter` (was/still: rolling cutter)
- **whisper**: The transcriber: finds the speech and writes it down as segments. Example: `WhisperModelClient` (was: whisper client,
  `WhisperServerClient`)
- **segment**: Whisper's own word for a piece it transcribed, with its times. Example: `Segment` (was/still: segment)
- **track splitter**: Splits whisper's text the way a film's sound is split: dialogue (speech), music (lyrics) and room tone, with
  blanks dropped. Example: `TrackSplitter`, `Tracks` (was: whisper text sorter, `WhisperTextSorter`, `WhisperText`)
- **room tone**: Whisper's guess at a sound in the room, like `(dog barks)`. Not scored unless `score_room_tone` is on; the last
  `room_tone_memory` (6) are kept as context for the oracle model. Example: `room_tone` (was: ambient caption,
  `consider_ambient_sounds`, `ambient_memory`)
- **lyrics**: The sung words on the music track. They are words, so they are scored. Example: `lyrics` (was/still: lyrics)
- **blank**: Whisper's own mark for nothing heard (`[BLANK_AUDIO]`). Dropped. Example: `blank` (was: nothing-here mark)
- **eavesdropper**: Caul's rig: the deck, the rolling cutter, whisper and the track splitter, handing on takes. Example:
  `Eavesdropper` (was: listener, `Listener`)
- **take**: What is new on the tracks, with where in the tape it ended. Example: `Take` (was: heard segment, `HeardSegment`)
- **take cutter**: Cuts a take into clips. Example: `TakeCutter` (was: segment fragmenter, `SegmentFragmenter`)
- **clip**: A piece of a take, usually a sentence, with its time; the thing Scoring scores. Example: `Clip`, `ClipLevel` (was:
  fragment, `Fragment`, `FragmentLevel`)
- **room-tone pass**: After a few runs with no dialogue, whisper listens once without its voice detector, for music and room tone
  only. It can only hear what is still held, about two seconds after a quiet stretch. Example: `room_tone_pass_every`, `room_tone_pass_seconds` (was: sound pass, `sound_every_steps`, `sound_seconds`)
- **cut timing**: The rolling cutter's rhythm: a cut every `cut_every_seconds` (5); a segment is kept once it ends `hold_back_seconds`
  (2) before the end of the cut; a cut never runs past `max_cut_seconds` (30). `rewind_seconds` (0, off) would start each cut that far
  before the last kept segment's end; it made whisper repeat the last word. Example: config (was: `step`, `hold`, `max_buffer`,
  `rewind`)
- **known-good rig**: The one setup that worked in the room (whisper `base.en`, voice detection 0.5, a cut every 5 seconds); changed
  one thing at a time and pinned by a test. Example: none (was: known-good hearing setup)

**Words in reserve.** The diagram's materials, ready if the code ever needs a name for them:
- **tape**: the running sound, frame by frame.
- **lift**: the length the rolling cutter hands whisper (today a buffer inside `RollingCutter`).
- **selects**: the segments whisper has finished (today a list inside `RollingCutter`).
- **splice**: joining a half sentence to the next (today `join_unfinished`).
- **outtakes**: everything dropped: blanks, and what was already passed on.

## Scoring: how interesting is a clip

Takes a clip (or a typed line from the Keyboard), hands on its score to Qualifying.

We use the metaphor of a sports judge -- any judged sport, from dressage to boxing -- to describe this part of the pipeline. The
scorer model is the judge: it watches one performance and gives it a mark. You can shape the criteria it judges by, but it has its own
eye, and it never decides who goes through; that is Qualifying's job. The scorer model and the score keep their plain names.

    clip -> tagged words -> scorecard -> score -> Qualifying

- **score**: The mark a clip gets, 0 to 9, anywhere in between. Example: `score` (was/still: score)
- **scorecard**: The judge's answer: every digit it might give, each with how likely it is. The score is the scorecard's average, each
  digit weighted by its likelihood: 75% sure of 6 and 25% sure of 7 scores 6.25, like a panel's marks averaged. Example:
  `score_from_scorecard` (was: digit probabilities, `score_from_probabilities`)
- **scorer model**: The judge: the small language model that scores clips (Qwen3-4B for now). Example: `ScorerModelClient`
  (was/still: scorer model)
- **scorer prompt**: The criteria the judge scores by, written by Wes. Example: `prompt_scoring`, `prompt_output` (was/still: scorer
  prompt)
- **scorer**: The judging, start to finish: the clip filter, then the scorer model, with the clip left unscored if the judge does not
  answer. Example: `ClipScorer` (was: `Scorer`)
- **scored clip**: A clip with its score and what made it (the judge or the clip filter). Example: `ScoredClip`, `ScoredBy` (was:
  `ScoredFragment`)
- **clip filter**: Rules a clip out before the judge sees it when it has no content words (such as "or not."), giving it a fixed low
  mark, `clip_filter_score`. Example: `is_clip_filtered` (was: fragment_filter, `fragment_filter_score`, `is_fragment_filtered`)
- **word tagger**: Labels each word of a clip as a name, number, filler, stopword or content word, for the clip filter. Example:
  `WordTagger`, `TaggedWord` (was: word tagger, `Word`)
- **content word**: A word that is not filler, a stopword or a number. Example: `content_words`, `min_content_words` (was/still:
  content word)
- **unscored**: A clip the judge gave no score; its score is None, and Qualifying leaves it out. Example: `None` (was/still: unscored)
- **circuit breaker**: If the judge fails `failure_limit` (3) times in a row, it is left alone for `retry_seconds` (30), and clips go
  unscored meanwhile. Example: config (was/still: circuit breaker)
- **fallback**: Shelved, unused: a rough word-based score once used when the judge did not answer. Example: `FallbackScorer`
  (was/still: fallback)

**Words in reserve.** Ready if the code ever needs a name for them:
- **judge**: the scorer model, in prose.
- **deduction**: a mark taken off for a flaw, if scoring ever penalizes something (filler, a repeat).

## Qualifying: choosing what to respond to

Takes scores, and now and then hands on a hinge to Writing.

We use the metaphor of qualifying, as in a competition's qualifying round, to describe this part of the pipeline. Each new score is
an attempt to qualify. There is no fixed standard to meet (nothing absolute): an attempt qualifies by placing high enough against the
field, the room's own recent scores. The BarKeeper is the official who sets the bar from the field and rules on every attempt, and the
clip (or typed line) that qualifies becomes the hinge. It sits beside Scoring's judges: the judges give marks, but never decide who goes on.

    score -> attempt -> qualifies -> hinge -> Writing
                ^
               bar (set from the window)

- **BarKeeper**: Keeps the window, sets the bar, rules on each attempt, and keeps the cooldown. Example: `BarKeeper`,
  `bar_keeper_from_settings` (was: `ConvoInterestTracker`, `tracker_from_settings`)
- **attempt**: One score trying to qualify, with what came of it: the clip (or typed line), its score, the bar as it stood, and the ruling. Example:
  `Attempt` (was: scored sentence, `ScoredSentence`)
- **ruling**: What the BarKeeper made of an attempt: *qualified*; *did not qualify* (it joins the window); *held* (it qualified inside
  the cooldown; it goes to the bar as a qualifier does, by `qualifier_bar_influence`); or *no mark* (no usable score). Example: `Ruling`, `rule()`, `rule_typed()` (was: score effect, `ScoreEffect`, `record()`, `record_direct()`)
- **qualifies**: An attempt qualifies when its score is above the bar, once the window holds `min_scores` and the cooldown is over. The
  score meets the bar as it stood before that score joined. Example: `QUALIFIED`, `[qualifying]` (was: trigger,
  `TRIGGERED`, `[trigger]`)
- **qualifier bar influence**: How much a score that stood above the bar (a qualifier, or one held by the cooldown) influences the bar. It goes in as
  the bar plus this much of the way up to the score: 1 is the score itself, 0 the bar itself, 0.5 half way. A plain number, not limited to 0 to 1. Example: `qualifier_bar_influence`, `[qualifying]` (was: `exclude_qualifiers`, on or off)
- **anchor**: A steady background of ticks under the room, so that silence lowers the bar: when the room goes quiet the bar settles toward the anchor score. Example: `anchor_score`,
  `anchor_interval_minutes`, `[qualifying]`
- **tick**: One of the anchor's stand-in scores: it has the anchor score for its value, sits in the bar's memory like an attempt and fades on the same half-life, but is never judged, never
  qualifies, never starts a cooldown and is never counted as an attempt. Ticks fall on a fixed grid, one every anchor interval, from the bar's first use. Example: `DecayingQuantileBar._ticks_at()`
- **anchor score**: The value of each tick, the level the bar settles toward in silence (the pooled 90th percentile of the recordings' scores to begin with). Example: `anchor_score`
- **anchor interval**: The minutes between ticks; shorter pulls the bar down faster in silence. Example: `anchor_interval_minutes`
- **discard age**: How many minutes the bar remembers a score, or a tick; older ones are dropped from its memory. Once every real score is dropped the bar is made of the ticks alone. Example:
  `discard_scores_older_than_minutes`, `[qualifying]` (was: a constant, `MEMORY_HALF_LIVES`, 8 half-lives)
- **bar**: The score an attempt must be above to qualify. Example: `Bar`, `bar_at()` (was: bar, `TriggerBar`, `trigger_level_at()`)
- **bar types**: How the bar is set (`algorithm`): `percentile` of the window; `decaying_quantile`, a percentile in which a score counts
  half as much every `half_life_seconds` (the shipped choice); or `moving_average`, a decaying average plus a margin. Example:
  `PercentileBar`, `DecayingQuantileBar`, `MovingAverageBar` (was/still)
- **window**: The field an attempt is placed against: the scores of the last `window_seconds` (300), or the last `min_scores` (20) if
  the room is quiet. A length of time, not a count. Every score joins it, the lowest too; a qualifier joins part of the way up
  from the bar to its score (see *qualifier bar influence*), so one zinger does not hold the bar up. Example: `window_seconds` (was/still: window)
- **bar percentile**: How much of the room's recent scores an attempt must beat to qualify (99.5 to begin with; 100 is the best). The percentile the bar sits at. Example:
  `bar_percentile`, `[qualifying]` (was: `percentile`; the *bar type* `percentile` keeps its name)
- **starting bar**: Where the bar begins before the room has said enough to set one; it fades as the room speaks. Example:
  `starting_bar` (was/still)
- **starting bar weight**: How many scores' worth of weight the starting bar carries when the bar first begins; it fades on the same half-life as any score. Example:
  `starting_bar_weight`, `[qualifying]` (was: `starting_weight`, an argument the config never set)
- **cooldown**: How long after a qualifier the next may come (`cooldown_seconds`, 60; 0 for none). Example: `cooldown_seconds`
  (was/still)
- **hinge**: The clip (or typed line) that qualified; the basis for the portent. The bridge to Writing. Example: `hinge` (was/still)

**Words in reserve.** Ready if the code ever needs a name for them:
- **the field**: everyone competing lately; the window, in prose.
- **standard**: a fixed qualifying mark. Deliberately unused: nothing absolute.
- **personal best**: the highest score the room has reached tonight.

## Writing: from hinge to portent

Takes a hinge, hands on a portent to Printing.

We use the metaphor of the oracle -- think Delphi -- to describe this part of the pipeline. Something said in the room is brought to
the oracle as the hinge, along with exemplars lent by the Library to show the voice. The oracle does not answer it; it speaks from it,
and what it speaks is a portent.

    hinge + exemplars -> oracle prompt -> oracle model -> portent -> Printing

- **oracle model**: The oracle: the medium language model that writes the portent. Example: `OracleModelClient` (was/still)
- **consult**: Put the finished prompt to the oracle model and wait for what it says. Example: `OracleModelClient.consult()` (was:
  `OracleModelClient.portent()`)
- **oracle prompt**: What the oracle is told, written by Wes in the config, with `{exemplars}` and `{hinge}` marking where those go.
  Example: `oracle.prompt` (was/still)
- **prompt builder**: Fills the exemplars and the hinge into the oracle prompt. Example: `PromptBuilder`, `OraclePrompt` (was/still)
- **portent**: What the oracle says; what goes on to be printed. It carries the text, the exemplars shown, how long it took, and whether
  it was cut off. Example: `Portent` (was/still)
- **portent writer**: Takes a hinge, has the prompt builder fill the prompt, consults the oracle model, and returns the portent.
  Example: `PortentWriter` (was/still)
- **hinge**, **exemplar**: Bridge words, defined where they cross: the hinge under Qualifying, the exemplar under Library.

**Words in reserve.** Ready if the code ever needs a name for them:
- **consultation**: one prompt put to the oracle and the portent that came back.
- **riddle**: a portent that leaves its hinge entirely behind.

## Printing: from portent to paper

Takes a portent, prints it on the terminal.

We use the metaphor of the terminal -- the teletype and its descendants -- to describe this part of the pipeline. The Pi is the host;
the converted typewriter is a hard-copy terminal at the end of a serial line, and any other terminal (an ADM-3A, a VT220) would do.
These words say *terminal*; a machine's name appears only where a fact belongs to that machine.

    portent -> printable text -> terminal -> paper

- **terminal**: The device at the end of the serial line: the converted typewriter, or any terminal. The host transmits text to it,
  and its keyboard sends keys back. Example: `[terminal]` (was/still)
- **CadetWriter**: The open-source board that turns the IBM Wheelwriter 1000 into a serial terminal. Its own settings (line ending,
  flow control, duplex) must match `[terminal]`: a mismatch loses text or prints every character twice. Example: none (was/still)
- **portent formatter**: Makes a portent printable: code fences out, plain ASCII only, wrapped to the line width, and ending with
  `linefeeds_after_portent` (4) so the paper has moved on. Example: `PortentFormatter`, `[printing]` (was: typewriter formatter,
  `TypewriterFormatter`, `[output]`)
- **terminal port**: Transmits printable text over the serial line, paced at `characters_per_second` (16) with flow control and the
  line ending the terminal expects, waits `finish_wait_seconds` after the last character, and reads lines typed at the keyboard.
  Example: `TerminalPort`, `transmit()`, `read_line()` (was: typewriter port, `TypewriterPort`, `type()`)
- **console terminal**: The same code printing on the screen, for development. Example: `ConsoleTerminal` (was: console typewriter,
  `ConsoleTypewriter`)
- **print result**: What happened to one printing. Example: `PrintResult` (was: `TypingReport`)
- **quote**: A piece of a portent the oracle model fenced in triple backticks. The terminal has no italics, so a quote is printed
  indented `quote_indent` (4) spaces with a blank line before and after; the fence lines are never printed. Example: `quote_indent`
  (was/still)
- **line width**: How many characters fit on a line, from `paper_width_inches`, `margin_inches` and `characters_per_inch` (65 for 8.5
  inch paper, 1 inch margins and 10 characters per inch). Example: `line_width` (was/still)
- **print wheel**: The wheel of characters a daisy-wheel terminal prints with. Its pitch is `characters_per_inch` (10, 12 or 15).
  Example: `ascii_printwheel` (was/still)
- **composed characters**: The nine ASCII characters most print wheels lack, ``< > \ ^ ` { | } ~``, which the CadetWriter builds from
  periods and micro-movements, slowly and dotty. `<` and `>` can carry meaning, so they are printed as they are; the other seven are
  swapped for look-alikes (`` ` `` becomes `'`, `~` becomes `-`, `{ }` become `( )`, `\` and `|` become `/`, `^` is dropped) unless
  `keep_composed_characters` keeps them, and `ascii_printwheel` keeps all seven. Example:
  `keep_composed_characters` (was/still)
- **echoplex**: The old terminal name for the host echoing each key back as it arrives. With it on, backspace moves the carriage left
  and edits the typed line; the CadetWriter does not use the typewriter's correction tape. Example: `echoplex` (was/still)
- **type during printing**: False (the default) throws away keys pressed while a portent is printing, and any half-typed line, so
  nothing typed over a portent appears after it. True keeps them for the next line read. Example: `type_during_printing` (was:
  `type_during_output`)

**Words in reserve.** Ready if the code ever needs a name for them:
- **host**: the Pi, as the terminal sees it.
- **hard copy**: what ends up on paper, as against a screen.
- **carriage**: what moves across the line; the carriage return in `end_of_line`.
- **patience**: How long the terminal waits, once a portent is being typed, for a machine that stopped taking characters (held, out of paper,
  busy) or went away (unplugged); when it returns the portent goes on where it stopped, nothing repeated. Example: `stall_patience_seconds`,
  `reconnect_seconds` (was: none)

## Keyboard: what people type

Beside the line: a person types a typed line, which enters at Scoring and is ruled on by Qualifying.

The Keyboard has no metaphor of its own; its words share the plain word *typed*. People type, the Printer prints. A typed line is
someone speaking to the Printer directly rather than being overheard, so it skips Listening, is scored like a clip, and gets a boost
before it meets the bar. Once it is scored and ruled on it speaks the language of those sections: the *score multiplier* is Scoring's word,
and the *bar* is Qualifying's.

    keyboard -> typed line -> Scoring -> score x multiplier -> Qualifying

- **typed line**: A line typed at the terminal's keyboard. It is scored like a clip, multiplied by the score multiplier, and ruled on against the same
  bar. It has no cooldown; a typed line under the bar does not qualify, and nothing is printed. Example: `TypedLineScorer`
  (was/still), `rule_typed()` (was: `record_direct()`)
- **score multiplier**: What a typed line's score is multiplied by before it meets the bar, to even the field against the room (1.5 to begin
  with; 1 is none). The product is not capped at 9. It is a multiplier, not a "factor": it names what is done to the score. Example:
  `typed_lines_score_multiplier` (was: handicap, `typed_handicap`; before that, `typed_score_multiplier`)
- **typed lines bar influence**: The qualifier bar influence for a typed line, above the bar or below it: it goes in as the bar plus this much of the way up
  to its (multiplied) score. A plain number, not limited to 0 to 1. Example:
  `typed_lines_bar_influence` (was: `typed_lines_affect_bar`, on or off)
- **keyboard watcher**: Defined under *Conductor*, beside the hinge finder.

## Conductor: running the pipeline

The parts that join the stages and take a hinge through a turn. The turn and the Conductor are defined under *The pipeline*.

The Conductor, defined under *The pipeline*, runs it all. The words for what it runs come from turn-taking in conversation. The room
and the keyboard both offer attempts; when one qualifies, the Printer
takes the floor for one turn (writing and printing) and does not listen; then it yields the floor and listens again. One turn at a
time: that is half-duplex.

    attempts (room, keyboard) -> the floor -> turn: write, print -> yield -> listening

- **hinge finder**: Runs Listening, Scoring and Qualifying for the room, and yields every attempt; the one that qualified carries the
  hinge. Example: `HingeFinder` (was/still)
- **keyboard watcher**: Does the same for typed lines as the hinge finder does for the room: reads each finished line from the terminal, has it
  scored and ruled on, and yields the attempt. It has the eavesdropper's `pause()` and `skip_ahead()`: a line finished during a turn is read
  and thrown away unscored. Example: `KeyboardWatcher` (was/still)
- **the floor**: Who may speak: the room, or the Printer during a turn. One holds it at a time, first come, first served.
- **FloorKeeper**: Keeps the floor: the state (listening, writing, printing, recovering), the one-turn-at-a-time rule (first come, first
  served), and the watchdog that takes the floor back from a turn that runs too long (it goes to recovering, then listening). The limits
  are generous on purpose: they must cover the longest likely run, not the average. Example: `FloorKeeper` (was: half-duplex gate,
  `HalfDuplexGate`)
- **turn** (the handle): What `take_floor()` hands back: a `Turn` with `start_printing()` and `finish()`, used in a `with` block so the
  floor is always yielded. Once it is over, or the watchdog has taken the floor from it, its calls do nothing. Example: `Turn`
  (was: none)
- **turn taker**: Takes one hinge through a turn: has the portent written (trying again a few times if the oracle gives nothing), records it
  in the turn history at once, formats it and has it printed, waiting patiently if the terminal cannot print yet, and says how the turn ended.
  It closes the turn itself, so the floor is always yielded. Example: `TurnTaker`, `take_turn()`, `TurnResult` (was: portent deliverer,
  `PortentDeliverer`)
- **freshness**: How long after its hinge a portent is still worth printing (6 minutes to begin with, a little under the usual gap between
  portents). It answers one sentence, and a few minutes later the room has moved on. Past it the portent is dropped. Example:
  `freshness_seconds` (was: none)
- **turn outcome**: How a turn ended, shown beside it on the monitoring site: *printed*, *not printed (no printer)*, *no portent written*,
  *nothing to print*, *too late to print*, *print failed*, *error*; and *waiting for the printer* while it waits. Example: `TurnOutcome`
  (was: none)
- **pause**: When the floor is taken, the eavesdropper stops listening: it keeps reading frames from the deck so its buffer never
  overflows, but throws them away and asks whisper nothing; what was transcribed and not yet handed on is dropped too. Like pausing a
  tape. Example: `Eavesdropper.pause()`, `paused` (was: none)
- **skip ahead**: When the floor is yielded, the eavesdropper ends the pause with a clean slate, so the Printer does not hear its own
  printing: the cutter forgets its audio, the repeat memory is cleared, and the cutter's clock moves on by the sound that went by unheard,
  so the cooldown and the bar count the turn's time. Example: `Eavesdropper.skip_ahead()` (was: `Listener.skip_ahead()`)
- **entry point**: Builds the one `Config`, builds the parts from it, and starts the Conductor; on the Pi it runs as a service. It takes the
  testbench's choices as options: a microphone or a recording, the real terminal, the console terminal or none, the keyboard on or off. It
  starts whatever state the servers, the microphone and the printer are in, and stops cleanly on SIGTERM or ctrl-C. Example: `python -m pom`,
  `pom.printer` (`Options`, `build_printer`, `Printer`) (was: none)
- **boot slip**: The few lines the typewriter types when the Printer and the typewriter come online, laid out like a terminal announcing itself, one of them the Pi's address so the monitoring page and ssh can be reached with no screen. Its words are `[boot_slip] text`. Example: `BootSlipper`, `[boot_slip]` (was: none)
- **address finder**: Lists the machine's usable IPv4 addresses (not loopback, not link-local) for the boot slip. Example: `AddressFinder` (was: none)

**Words in reserve.** Ready if the code ever needs a name for them:
- **interruption**: something that wants the floor during a turn (today it is simply not heard).
- **overrun**: a state that runs too long; what the watchdog ends.

## Running the Printer

The machine, its settings, how it keeps going, and how it is watched.

**The machine**

- **pom1**: The Raspberry Pi 5 (16 GB) the Printer runs on (`ssh pi@pom1.local`). Away from the home network it falls back to its own
  Wi-Fi hotspot, `pom1-direct` (Comitup). It is the client: work reaches it through git and nothing is edited on it by hand. Example:
  none (was/still)
- **servers**: The programs the Printer talks to over HTTP, on the Pi itself: whisper-server for Listening (port 8110) and one
  llama-server for each model (the scorer model on 8098, the oracle model on 8099). They stay loaded and idle during a turn. Example:
  none (was/still)
- **config**: The settings, in `config/pom.toml` (committed, the quiet defaults the Printer runs with) over built-in defaults, under the
  gitignored `config/pom.local.toml` (where the monitoring site's password lives) and then environment variables. A bad value means
  the default plus a warning. Everything a person might tune is in it. Example: `Config` (was/still)

**Keeping going**

- **startup**: The one-time preparation when the Printer starts: each step checked (the config's corrections, the data, the
  microphone, whisper, the model servers, the terminal), then a one-line readiness summary. Only the runner is built; the steps are
  placeholders. Example: `Startup`, `StepStatus`, `StepOutcome` (was: `Status`, `Outcome`)
- **fault**: A problem a module recovered from and recorded instead of crashing. Example: `Fault` (was/still)
- **fault recorder**: Keeps a module's faults (WARNING means recovered, CRITICAL means the module cannot do its job). The Printer never
  stops because of a fault. Example: `FaultRecorder` (was/still)

**Watching**

- **monitoring site**: The password-protected web page of the last hour of turns, with a detail page for each: the hinge, the
  exemplars, the portent, the score and the bar. Open it from another machine on the Printer's network. Example: `MonitorSite`,
  `[monitor]` (was/still)
- **turn record**: One turn and what came of it: the hinge, its score, the bar it beat, the window, the exemplars shown and the
  portent. **Turn history** is the last hour of them, newest first, in memory only and never written down. Example: `TurnRecord`,
  `TurnHistory` (was: trigger record, `TriggerRecord`, `TriggerHistory`)
- **score chart**: On the monitoring site, every attempt's score against the bar over the last hour, with each qualifier marked.
  Numbers only, never words. Example: `ScoreTrace`, `render_score_chart` (was/still)

## Workshop: how we build it

The places we try things, the records we keep, and the practices we work by.

**Places**

- **testbench**: A small tool for trying one built part of the Printer on its own and seeing what it does, using the real modules.
  The old experiment folders `oracle-playground` and `hearing-debug` did this before the parts were built. Example: `scripts/dev/`
  (was: playground)
- **experiments**: The set-aside place where we prototype. Nothing there is part of the Printer until it is rebuilt in `src/pom/`.
  Example: `experiments/` (was/still)

**Records**

- **handoff**: Where the project stands and the working rules, for whoever picks the work up next. Example:
  `documentation/Project State.md` (was/still)
- **changelog**: Updated in the same commit as each change; commits are small and frequent. Example: `CHANGELOG.md` (was/still)
- **AI ledger**: The record of remote AI (Claude) use. Local models are never counted. Example: `documentation/AI Ledger.md`
  (was/still)

**Practices**

- **design choices first**: Before building anything that affects the system, say how it will be solved and list the choices, each
  with a recommendation. Building waits for Wes's **go**, and a feature is added only if Wes agrees. A name, or a rename, is one of
  these choices.
- **leaf-first**: Build a **leaf** (a module that depends on nothing not already built) alone, with its own tests and a way to see it
  work, before anything that assembles it.
- **mutation check**: Break the code on purpose, one small change (a *mutant*) at a time, and run the whole suite. A mutant the tests
  do not catch (it *survives*) shows a missing test.
- **preview**: For anything Wes will see (a page, a chart, output), build it, show it running, and wait for suggestions before the
  mutation checks and the commit.
- **Wes writes the prompts**: The scorer prompt and the oracle prompt are Wes's. Claude measures them and does not author them.

## Planned and undecided

Ideas that are not built and not settled. Planned parts that already have a place in the pipeline are listed in their own sections.

- **paper guards** †: *(proposed)* so nobody comes back to a pile of paper: nothing qualifies unless speech was heard in the last 10
  minutes, and hard ceilings of about 12 portents an hour and 60 a day.
- **failing loudly** †: *(open)* if the scorer model or the oracle model is down the Printer can never respond, and a log line is not
  loud when someone is switching a printer on. What "loud" means (a beep, an email, something at the venue) is not decided.
- **real-room test** †: the Printer in a real room, a quiet one too, then retuning the bar, which has so far been tuned on four chatty
  meetings and one live room.
- **Track A and Track B** †: the two oracle models the design began with. Track A is a stock instruct model with the oracle prompt,
  which is what runs and what the bake-off measured. Track B is a base model fine-tuned (LoRA, on the Mac, never on the Pi) on the
  library plus hinge pairs. Track B has not been started.
- **hinge pair** †: *(for Track B)* an overheard line paired with a tangential reply in the manner of one shelf, curated by Wes, Neil
  and River, with a large language model drafting candidates (logged in the AI ledger).

## Names to settle †

What the 2026-11-02 review of names decided on paper, and what is still open. Nothing here is applied in the code yet; each rename is
Wes's decision.

**Settled in the lexicon, not yet in the code**

- **trigger**: the moment is now a *qualifies*, the part that decides is the *BarKeeper*, and the stage is *Qualifying* (`[trigger]`
  becomes `[qualifying]`).
- **segment, fragment, sentence**: whisper keeps *segment*; what Listening hands on is a *take* cut into *clips*; what Qualifying rules
  on is an *attempt*. A typed line is its own thing, scored like a clip.
- **typed**: the Printer prints and people type (`TerminalPort.transmit()`, `type_during_printing`).
- **voice and oracle**: the oracle family wins; *voice* is gone from the lexicon. `src/pom/voice/` and the "voice-model" startup step
  still need renaming.
- **terminal, typewriter, Wheelwriter**: the code says *terminal*; a machine's name appears only for facts about that machine.
- **corpus**: now the *Library*.

**Still open**

- **Packages and sections**: whether each section becomes its own package (Qualifying is inside `scoring/` today; Writing is split
  across `prompt/` and `voice/`).
- **The keyboard watcher**: under *Conductor* or *Keyboard*.
- **`RollingCutter`**: one class doing two jobs (cutting the lift, keeping the finished segments); whether to split it.

**Older names, left as they are**

- The hearing-debug **`Segmenter`** cuts *audio* by loudness before whisper runs, and isn't a segmenter in whisper's sense. It lives
  in `experiments/`, outside the lexicon's reach.
- Older docs say **pronouncement** for portent and **chunk** for fragment.