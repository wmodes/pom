# Changelog

All notable changes to the Printer of Mysteries project are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Nothing has been
released yet, so everything is under Unreleased. Update this file in the same commit as the change it describes.

## [Unreleased]

### Added

- **The library no longer keeps unfinished passages** (the second half of the cut-off portent fix): `normalize.py` drops an utterance that ends unfinished (colon, or a word like "the" or "of"; `ends_unfinished` in `pom.library.endings` is the one definition), `cataloguer.py` drops the pieces it cuts that way, and `audit.py` gained an `unfinished` column that must read 0. Re-ran `clean`, `promote` and `cataloguer.py` for every holding: 1,004 utterances removed from `ready/` across 55 holdings (checked: every removed paragraph is unfinished, none added, the order of the rest unchanged) and 60 cut pieces, leaving 71,169 passages (was 72,264), none unfinished. Passage ids are positions in a holding, so those after a removed passage were renumbered (the large diff in `library/passages/`). Housekeeping found on the way: three raw folders still used an earlier layout (`source.pdf`, `source.txt`) and were renamed to `holding.*` in the gitignored `raw/`, so `clean` can run for every holding again. `library/README.md` has the rule (5, nothing ends unfinished).
- **Unfinished exemplars are drawn again** (the cut-off portent, found 2026-11-03): the oracle copies its exemplar's ending, and 1,059 of the 72,264 passages (1.47%) end on a colon or on a word like "the" or "of". `pom.library.endings.ends_unfinished` is the one definition; the Bibliomancer draws again when a pick lands on one (`redraw_limit`, `[oracle] exemplar_redraws`, default 50 per pick, 0 turns it off), prefers finished passages in its exact fallback too, uses an unfinished one with a single warning if 50 picks in a row end unfinished (so the oracle is never stopped), and counts the redraws in its debug log (no text). The odds among finished passages are unchanged. The two testbenches that build a Bibliomancer (`listening_to_portent.py`, `write_portent_from_hinge.py`) pass the setting, so they draw as the Printer does. Six fixture passages in two tests in `test_bibliomancer.py` got full stops because "Small a" ends on the word "a". New terms in the lexicon: unfinished, redraw.
- **The test bench `scripts/dev/pipeline/run_printer_with_servers.py`**: starts whisper-server and the scorer's and oracle's llama-servers (ports read from the config; a server already answering is used and left running), waits until each answers, runs `python -m pom` with every option it was given (`--mac-mic --terminal console`, `--wav FILE --speed 6`, ...), and stops the servers it started when the Printer ends or gets SIGTERM. Server logs go to a temporary folder, kept only if a server failed to start. Tried on the Mac with a recording: started, ran a turn, stopped everything on SIGTERM; reused a whisper-server started by hand.
- **Documented, so it is not forgotten: all the models stay loaded** (design doc, "All the models stay loaded, always", and project-state): whisper, the scorer and the oracle are resident in memory at once on the 16 GB Pi; half-duplex is about CPU, not memory. Also recorded the agreed plan for the servers: one systemd unit each, `pom.service` after them with `Wants=` (units not written yet). The config's two stale comments ("the entry point starts llama-server") now say the servers run as services.

- **The TypedLineScorer** (`src/pom/scoring/typed_line_scorer.py`): a line typed at the terminal's keyboard goes in, a `ScoredSentence` (kind `typed`) comes out, so the Conductor and the monitoring
  site take it like any hinge. It has its own way in and never touches the Listener. The Scorer scores the line (one sentence, no context), the score is multiplied by `typed_score_multiplier` (1.5;
  not capped at 9, so a typed 7 beats any spoken 9) and the same bar judges it. A line that does not clear the bar is ignored: nothing is printed. A typed line has no cooldown and a typed
  trigger does not start the room's. By default a typed score stays out of the bar, which stays a picture of the room (`typed_lines_affect_bar`, false). The new `ConvoInterestTracker.record_direct()` and
  `ScoreEffect.JUDGED` do the judging; the typed text is never logged or reported. Two new `[trigger]` settings, and the playground `scripts/dev/scoring/score_typed_lines.py`.
- **The TypewriterPort and the ConsoleTypewriter** (`src/pom/output/typewriter_port.py`): the last step after the formatter. `type(text)` sends a portent to the typewriter over a serial
  port (pyserial, new in `requirements.txt`), paced at `characters_per_second` (16) from a schedule that does not drift, with XON/XOFF or RTS/CTS flow control, a newline sent as the CadetWriter's
  line ending (CR+LF by default: a bare LF only feeds the paper), only printable ASCII and newlines let through (an ESC would start a CadetWriter escape sequence), and a wait at the end
  for the machine to finish. A port that is missing or fails halfway gives a report and a warning, never an exception, and is reopened next time. The ConsoleTypewriter is the same code typing on the
  terminal, so everything can be tried without the machine.
- **The keyboard comes back through the same port**: `read_line(timeout)` returns a line typed on the typewriter when C Rtn is pressed. With `echoplex` (the old terminal name for the host echoing
  what it receives; the CadetWriter stays in its default full duplex) each key is echoed as it arrives, Backspace moves the carriage left and edits the line (a retype overstrikes; the CadetWriter
  does not use a correction ribbon), and C Rtn is echoed as the line ending. Keys typed during a portent, and any half-typed line, are thrown away when it finishes unless `type_during_output` is on (it is off by default).
  The portent and the keyboard share the line under a lock, so they never interleave. Neither the text nor the keys typed are logged or reported. 76 tests; 78 mutants checked.
- **`[terminal]` in the config**: `port` (`/dev/ttyACM0`), `baud` (9600), `flow_control` (`xon_xoff`), `end_of_line` (`crlf`), `characters_per_second` (16), `finish_wait_seconds` (2), `echoplex` (true)
  and `type_during_output` (false). The defaults are the CadetWriter's own (its user guide, section 3); `end_of_line` and `flow_control` must match its settings. A bad boolean now falls
  back to that setting's own default, not always false. 26 config tests; 17 mutants checked.
- **The playground `scripts/dev/output/type_text_on_typewriter.py`** formats text and types it on the screen at the typewriter's pace (`--fast`, `--cps`), on a real port with `--port`, and waits for a typed-back line with `--reply SECONDS`.
- **The TypewriterFormatter** (`src/pom/output/`): a portent in, text the Wheelwriter can print out. It removes the triple-backtick fences
  (the prompt shows the model its exemplars and the hinge in fences, so a model may echo them) and types a fenced quotation indented
  4 spaces with a blank line before and after, as a block quote would be set. Curly quotes, dashes, accents and ligatures become plain ASCII
  and anything with no plain form (an emoji) is dropped. Seven of the nine ASCII characters most print wheels lack (the guide of the CadetWriter lists
  `< > \ ^ ` { | } ~`, which it builds from periods and micro-movements, slowly and dotty) are swapped for a look-alike so the typing stays quick:
  `` ` `` becomes `'`, `~` becomes `-`, `{ }` become `( )`, `\` and `|` become `/`, `^` is dropped. `<` and `>` are always typed as they are and
  CadetWriter draws them. Lines wrap to the line width, a word too long is cut at the margin, and the text ends with
  `linefeeds_after_portent` line feeds. An unclosed fence (the model was cut off) quotes the rest. A census of 60 portents from the running
  9B found no fences, so this is a safeguard, not a fix for a seen problem. 69 tests; 58 mutants checked.
- **`[output]` in the config**: `paper_width_inches` (8.5), `margin_inches` (1.0), `characters_per_inch` (10; 12 or 15 for other wheels), `quote_indent` (4)
  `linefeeds_after_portent` (4), `keep_composed_characters` (empty: any of the seven swappable characters to type as they are, for example `"^|"`) and
  `ascii_printwheel` (false; true for the rare ASCII wheel, IBM/Lexmark Courier 10 P/N 1353909, where all nine are kept). The line width is worked out from the first three (65 at 10 cpi, 78 at 12, 97 at 15) and rounds down
  without losing a character to floating point; margins that leave under 20 characters fall back to the default paper, margins and pitch with a warning.
  `OutputSettings.line_width` gives it. 35 mutants checked.
- **The playground `scripts/dev/output/format_text_for_typewriter.py`** shows the formatter between rulers (`--cpi`, `--width`, `--indent`, `--feeds`).

### Changed

- **The Conductor, the entry point and `[conductor]`** (the Conductor is finished): `python -m pom` (`src/pom/printer.py`, `__main__.py`) builds every part from one `Config` and runs the `Conductor`
  (`src/pom/conductor/conductor.py`) on four ordinary threads: the room (HingeFinder), the keyboard (KeyboardWatcher), the turn (TurnTaker, one at a time) and the watcher. First come, first served for the floor; the ears
  shut the moment it is taken and open again because the watching thread looks at the floor every second (level-triggered, so a turn that ends any way, the watchdog's included, cannot leave the Printer deaf); a microphone
  that is unplugged ends the room and a fresh one is built (the BarKeeper and scorer shared) after `restart_pause_seconds`; a recording ends the run; a thread that dies is started again. It starts whatever state the
  world is in: a server that is not up yet, a monitoring site without a password or on a taken port, or an absent printer are lines in the log, not a stop; SIGTERM and ctrl-C stop it cleanly. New `[conductor]`
  settings in `Config` and `config/pom.toml` (the watchdog's limits, the freshness of a portent, the oracle retries, the restart and check times), checked against the settings they depend on (a warning when a watchdog is
  shorter than what it watches, or the printing limit shorter than the longest portent at the typewriter's pace), and `[terminal] mode` (`serial`, `console` or `none`). Options: `--wav`, `--speed`, `--mac-mic`,
  `--terminal`, `--no-keyboard`, `--config`. Burn-tested on the Mac with the real servers (long runs, the oracle killed and restored, whisper down at the start, SIGTERM mid-turn) and with a pseudo-terminal opened by the
  real pyserial as a stand-in typewriter (unplugged mid-portent and replugged under another name: resumed at the exact character; a printer absent at the start; a line typed on its keyboard); details in the design doc.
  The burn tests found one bug the unit tests could not: `main` read the wrong options field (fixed with a test). A draft systemd unit is in `pi-setup-notes.md` (not installed). The mutation-test helper now treats a
  hang as caught. 45 Conductor tests, 44 entry-point tests and about 30 config tests; about 50 mutants checked (the survivors showed a redundant guard, a budget rule that ignored the pause, and weak tests: all fixed). 3,029 tests pass.
- **The KeyboardWatcher** (`src/pom/conductor/keyboard_watcher.py`; fifth leaf of the Conductor): the typed-line twin of the HingeFinder. `watch()` reads each finished line from the terminal, has the TypedLineScorer score it
  and the BarKeeper rule on it, and yields the `Attempt`. It has the Eavesdropper's `pause()` and `skip_ahead()` (a line finished during a turn is read and dropped unscored, counted in `dropped_lines`), skips empty
  lines, and survives a terminal or scorer that raises (a warning naming the error type, a short pause, on); only `close()` ends it. Nothing typed is logged. 36 tests; 12 mutants checked (three of them make `watch()` never
  end and are caught by a timeout). The mutation helper was also hardened: it restores the file from the text it read, never from a disk copy, after a stopped run left a mutant in the source. 2,901 tests pass.
- **The TerminalPort stays with the portent** (stage 2 of the printer's robustness): once typing has begun, a failed write no longer ends the portent. A *stall* (flow control holds the line: the hold button, no paper, busy; a
  write timeout) is waited out and the same character is sent again; a *lost port* is closed and reopened every `reconnect_seconds` (2), and typing goes on from the first character not sent. Either wait lasts at most
  `stall_patience_seconds` (600), calls the heartbeat the TurnTaker hands it (`transmit(text, still_alive=...)`) every few seconds, is one warning for the stretch with a reminder in the log each minute, and pacing restarts
  from the moment typing goes on. `[terminal] port` may be a pattern (`/dev/ttyACM*`, `/dev/serial/by-id/usb-*`), looked up at each open, so a printer replugged under another name is found. A port that will not open is one warning
  for the whole stretch and a log line when it opens. A failed keyboard read now pauses a second instead of spinning. New `[terminal]` settings `stall_patience_seconds` and `reconnect_seconds` (in `Config` and `config/pom.toml`). The
  TurnTaker passes its heartbeat. New lexicon term: patience. The old test that expected a halfway failure to end the portent now expects it to resume. 44 new tests; 25 mutants checked (three survivors: one muddled helper field,
  removed; two tests added). 2,861 tests pass.
- **The TurnTaker** (`src/pom/conductor/turn_taker.py`; fourth leaf of the Conductor): takes one hinge through a turn. `take_turn(turn, attempt)` has the portent written (up to 3 attempts 10 s apart, only while a whole
  attempt still fits in the 270 s writing budget), records the turn in the TurnHistory at once, formats it and types it, and returns a `TurnResult` (an outcome plus numbers, never words). It owns `with turn:`, so the
  floor is always yielded, and it never raises. Printing has priority: a written portent is printed even if the watchdog took the floor meanwhile (a warning); a portent is worth printing for `freshness_seconds`
  (6 minutes from the start of the turn) and is dropped after (`too late to print`, CRITICAL); a terminal that has typed nothing is waited for and tried every 10 s, with a heartbeat so the watchdog sees a wait
  and not a hang, and `waiting for the printer` on the record; a print that fails halfway is not repeated (`print failed`, CRITICAL); with no terminal (`None`) a turn is write, record, done (`not printed (no
  printer)`). Outcomes: printed, not printed (no printer), no portent written, nothing to print, too late to print, print failed, error. Also: `Turn.still_printing()` (the FloorKeeper heartbeat; 8 tests), and
  `TurnRecord.outcome` with `TurnHistory.set_outcome()` (9 tests; it also found that an expired record could be "set"). New lexicon terms: freshness, turn outcome. 94 TurnTaker tests; 32 mutants checked across the
  three parts (two survivors, a timing detail and a weak heartbeat test, now fixed). 2,817 tests pass.
- **The oracle timeout is 240 s** (`[oracle] timeout` in `config/pom.toml`, and the built-in defaults of `OracleModelClient` and `Config`; was 120): the reply is waited for whole and the Pi writes about 0.5 s a token, so 120 s
  covered only about 230 tokens and would have cut off the longest portents (`max_tokens` is 300, about 155 s, plus reading the prompt). The Conductor's writing watchdog (300 s) stays longer. The comment in the
  config says why. 2,698 tests pass.
- **The FloorKeeper** (`src/pom/conductor/floor_keeper.py`; third leaf of the Conductor): who may speak. Four states in order (listening, writing, printing, recovering), first come, first served between the room and the
  keyboard (`take_floor(who)` returns a `Turn`, or None), a `Turn` used in a `with` block so the floor is always yielded, and a watchdog that takes the floor from a turn that runs past its limit. Built to fail toward
  speaking: every end of a turn goes to recovering and then listening; every call first settles anything that has run out (no ticker has to survive); a monotonic clock (the Pi has no battery clock); a lock with a
  timeout and a safe answer; a turn the watchdog cut off is stale and harmless if it wakes later. The limits cover the longest likely run, not the average (writing 300 s, printing 180 s, recovery 3 s), and the
  consequences of too short (the Printer hears itself; only short portents) and too long (a quiet spell) are written in the code and the design doc. Logged at INFO: every change of state with who holds the floor and how
  long the last state lasted, each refused request, each overrun (also a WARNING), each late call from a cut-off turn; labels and seconds only. 72 tests; 22 mutants checked (one redundant line removed, one test added).
  Flagged, not changed: the oracle client's own timeout (120 s, the whole reply) is close to the time a long portent takes (about 0.5 s a token); the design doc has the open item. 2,698 tests pass.
- **`Eavesdropper.pause()` and `skip_ahead()`** (second leaf of the Conductor): the tape-deck way to stay deaf during a turn. `pause()` (called when a turn takes the floor) makes the listening thread keep reading
  frames from the deck, so its buffer never overflows and logs no false warning, but throw them away and ask whisper nothing; a segment transcribed but not yet handed on is dropped, and the `HingeFinder`
  scores nothing more while `paused` (so the scorer model is not called either). `skip_ahead()` (when the floor is yielded) ends the pause with a clean slate: `RollingCutter.skip_ahead(seconds)` forgets its audio
  and any half-finished segment and moves its clock forward by the sound that went by unheard, so audio time keeps matching real time and the cooldown and the bar's memory count the turn's time (decided
  2026-11-02); the repeat memory is cleared, the room-tone memory is kept. Both calls only set a flag and return at once from any thread; the listening thread does the dropping. The new names `pause` and
  `paused` are in the lexicon, and the design doc's Half-duplex section has a new subsection. 32 new tests; 21 mutants checked (three survivors showed two redundant lines, now removed, and one weak test, now
  stronger). 2,622 tests pass.
- **The BarKeeper has a lock of its own** (first leaf of the Conductor): the room's thread (the HingeFinder) and the keyboard's thread (the TypedLineScorer) both call it, so `bar_at`, `window_size_at`,
  `rule` and `rule_typed` each hold one `threading.Lock` for the whole call and a ruling never sees the bar or the window half-updated by the other thread. The lock is per call, not per sequence: an
  attempt's recorded bar (asked just before the ruling) can differ slightly from the bar the ruling used if the other thread's score lands between the two; the ruling itself is always consistent. Two
  threaded tests (a bar that notices two threads inside it, three threads calling at once, and a count of every score); five mutants checked (no lock, and each of the four calls unlocked). Also two stale
  names in the BarKeeper's docstring (`record` for `rule`) and the test class `TestRecordDirect` (now `TestRuleTyped`). 2,582 tests pass.
- **The BarKeeper's lock cannot deafen the Printer**: the lock is in memory only (no lockfile, so a crash or power cut cannot leave it stuck), and a call that cannot get it within
  `LOCK_TIMEOUT_SECONDS` (5; the work inside takes microseconds) records a CRITICAL fault that names the call and answers as if it had nothing (`NO_MARK`, no bar, a window of 0) instead of waiting for
  ever. Eight tests (a lock held by "another thread", the time limit, strict mode, recovery once it is freed, a lock released after an exception); eight mutants checked. 2,590 tests pass.
- **The Great Rename, words (12): the table's stragglers**: every old name in the rename table was checked against the whole repository (not a list from memory). It found *caption* (the old word for
  room tone) still in the Eavesdropper, TrackSplitter, HingeFinder, `Attempt` and `config` docs, `[listening]` comments in `config/pom.toml`, the docs and the testbench README (including a
  half-converted "words, room tone / captions and nothing" in the Eavesdropper's docstring), and *sorts* where the TrackSplitter *splits*; a `.gitignore` comment still pointing at `library/SOURCES.md`
  (now `holdings.csv`); `requirements.txt` saying "triggers"; a test docstring naming `pom.scoring.scorer`; `startup.py` saying `Outcome` for `StepOutcome`; the Eavesdropper's `step, hold, rewind`
  argument note; and "source" for a holding in three lead rows of `library/holdings.csv` and in `project-state.md`. Words only; 2,580 tests pass.
- **The Great Rename, words (11): the stragglers**: a sweep of the code, tests, dev tools and docs against the lexicon's "was" names found a few left over. The typed line's *multiplier* is the *handicap*
  (`TypedLineScorer(handicap=...)`, `DEFAULT_HANDICAP`, the warning text, the tests, and `score_typed_lines.py --handicap`, which was `--multiplier`); a *scored sentence* is an *attempt* (the monitor
  chart's legend says "Black dots: attempts.", and the `ScoreTrace`, chart and test names follow); the Eavesdropper's private `_sound_every` is `_room_tone_every`; the dev tools' `heard_settings` is
  `listening`; and a handful of docstrings and test names (`output module`, `sorted into`, `room fragment`, `both voices`, `nothing_here_marks`, a `[voice]` section in a config test). Words only; 2,580 tests pass.
- **The Great Rename, words (10): the draw weights are the values, the circulation is the set**: following the lexicon's new entries, *circulation* names the whole set (the `Circulation` class and
  `circulation.json`) and a *draw weight* is one value in it (a shelf's `share()`, a holding's `holding_weight()`). Where the earlier word pass had turned "draw weights" into "circulation" it says draw
  weights again: the `Circulation` docstring, the file's `_about` note, the library README heading, `project-state.md`, `adding-material.md`, `validate_output.py` and `cataloguer.py`. Words only.
- **The Great Rename, docs: the design doc and project-state use the lexicon's names**: `documentation/pom_design_doc.md` and `documentation/project-state.md` now say *library* (shelves, holdings,
  passages, `library/passages/circulation.json`, `holdings.csv`), *clip*, *room tone*, *track split*, *Eavesdropper*, *TakeCutter*, *BarKeeper* and *qualifying* (a score *qualifies*; the section "The Trigger" is
  "Qualifying" and "Triggers we tried" is "Bar rules we tried"), *Printing* and the new package folders (`library/`, `listening/`, `scoring/`, `qualifying/`, `conductor/`, `keyboard/`, `writing/`, `printing/`),
  and their module-layout blocks match the real tree. The history of what was tried and why is kept; only the words changed (`trigger_fraction`, a retired setting, keeps its name, and *corpus* stays where it
  means `wordfreq`'s). The lexicon's "Orig text" blocks, kept for cross-reference while the code was renamed, are deleted. Every other file in `documentation/`, the READMEs and the file paths they name were
  checked too; three stray words are fixed (`model-bakeoff.md`, `pi-setup-notes.md`, `bakeoff-rubric.md`).
- **The Great Rename, words (9): form is shelf, weights are circulation**: the `form` column of `library/holdings.csv` is `shelf` (and `library.py add --form` is `--shelf`, `<form>` in the folder layout is `<shelf>`, the `mix`
  table says "by shelf"), and the `--weights` flags of `cataloguer.py`, `circulation.py` and `sample.py` are `--circulation`. The "weights file" in messages, docstrings and docs is the *circulation file*, and the
  tests' `weights` variables (always a `Circulation`) are `circulation`. A *holding weight* stays, as the lexicon says. One slip from the word pass is undone: `validate_output.py` says "not obvious source" again
  ("holding" there read as someone carrying contraband). The catalog output is unchanged. 2,580 tests pass.
- **The Great Rename, words (8): the monitor page says turns and qualifiers**: what the page lists is a *turn* ("Turns, newest first", "turns in the last 60 min", "last turn", "turns since start", "Turn N"
  with "← all turns"), the orange marks on the chart are *qualifiers*, and "Trigger rule" is "Bar type". The addresses change with the words: `/trigger/N` is `/turn/N` and `/api/triggers` is `/api/turns`
  (an old `/trigger/N` link is now a 404), and the JSON fields follow (`turns`, `turns_in_history`, `turns_per_hour`, `turns_since_start`, `last_turn_ago`, `bar_description`). In the code `TracePoint.triggered`
  is `qualified`, and the `[monitor]` comments in `config/pom.toml` say turns. 2,580 tests pass.
- **The Great Rename, words (7): playground is testbench, voice is oracle**: the dev tools in `scripts/dev/` are *testbenches* (in the comments, messages, docs and `tests/scripts/test_testbench.py`; the experiments folder `oracle-playground` keeps its name), and where *voice* meant the oracle it says
  *oracle*: startup's `voice-model` step is `oracle-model`, and the docs and docstrings that spoke of "a voice's replies" or "both voice tracks" say the oracle and its tracks. *Voice detector* and *voice detection* (whisper's) are a different thing and stay.
  Behavior is unchanged apart from that startup step's name; 2,580 tests pass.
- **The Great Rename, words (6): the library's words**: in the library code, its tests and dev tools, the oracle-side users and the docs, a *corpus* is the *library*, a *register* is a *shelf*, a *source* is a *holding*, a *chunk* is a *passage* and the *draw weights* are *circulation*
  (`Passage.holding` and `.shelf`, `Catalog.shelves()`, `.holdings(shelf)`, `.passages(shelf, holding)`, `Catalog.from_passages`, `Bibliomancer.shelf_shares`, `Circulation.holding_weight`, `LIBRARY` and `PASSAGES` in the tools, the monitor page's exemplar list). **The data
  changes with it:** every record in `library/passages/*.jsonl` has the keys `holding` and `shelf` (were `source` and `register`), and `library/passages/circulation.json` has `shelves` and `holdings` (were `registers` and `sources`) with a rewritten `_about`.
  The passages were regenerated with `cataloguer.py`, and all 72,264 records were compared with the old ones: the same, apart from the two key names. A checkout that pulls this needs no other step. Not renamed: the `form` column of `holdings.csv` and the `--form` flag (the same thing as a shelf; it is
  not in the lexicon yet) and the `--weights` flags. Behavior is unchanged; 2,580 tests pass.
- **The Great Rename, words (5): trigger is qualify, tracker is BarKeeper, effect is ruling**: outside the monitor, a score no longer *triggers*, it *qualifies*, the thing that decides is the *BarKeeper* (not "the trigger" or "the tracker"), and what it says about an attempt is a
  *ruling*. That is the variables and parameters (`bar_keeper`, `HingeFinder(eavesdropper, take_cutter, scorer, bar_keeper)`), `Attempt.trigger_level` as `Attempt.bar`, `Attempt.effect` as `Attempt.ruling`, `last_trigger` as `last_qualifier`,
  the messages (`at most one qualifier every 60 s`, `the qualifying algorithm must be one of ...`), the dev tools' output (`<-- would qualify`, `; held`), the comments and the test names. The monitoring site, its page and its API still say *trigger*, and so do the
  config and docs that describe it; they change together in the monitor pass, with a preview. Behavior is unchanged apart from those messages; 2,580 tests pass.
- **The Great Rename, words (4): source is deck, sorter is splitter, a nothing-here mark is a blank**: on the listening side, what feeds the Eavesdropper is a *deck* (the parameter, the `ScriptedDeck` test stand-in, the messages, comments and test names), `TrackSplitter.sort()` is `split()` (and the `sorter`
  parameter is `splitter`), and the marks whisper writes for nothing are *blanks*. The other uses of the word *source* (the library's holdings, a model file in a dev tool) are other things and stay for their own pass. Behavior is unchanged; 2,580 tests pass.
- **The Great Rename, words (3): listener is eavesdropper**: variables, parameters, messages, comments and test names (`HingeFinder(eavesdropper, ...)`). This commit also fixes two dev tools, `scripts/dev/listening/listen_to_room.py` and `scripts/dev/pipeline/listening_to_portent.py`,
  which still read the `[listening]` settings by their old names (`step`, `hold`, `rewind`) after the config rename; neither was caught by the suite because both need the servers running. A scan of every settings attribute used in the code, tests and tools found no other stale one. 2,580 tests pass.
- **The Great Rename, words (2): ambient is room tone**: whisper's captions of sounds like "(dog barks)" are *room tone* (the `Take.room_tone` and `Tracks.room_tone` fields, `Eavesdropper.recent_room_tone`, the `ROOM_TONE` kind, now the string `"room_tone"`, and the
  comments, messages and tests), and the *sound pass* is the *room-tone pass* (the testbench tool prints `room tone:` and its labels are re-aligned). Behavior is unchanged except for that kind string; 2,580 tests pass.
- **The Great Rename, words (1): fragment is clip**: in the listening, scoring, qualifying and writing code, the tests, the dev tools and the config comments, a *fragment* is a *clip* and the *fragmenter* is the *take cutter* (variables, parameters, the `ScoredClip.clip` field, the `{clip_text}` placeholder
  in the scorer's request template, messages, comments and test names). The scorer prompt text itself is unchanged. The library's own use of the word (a fragment of OCR text in `normalize.py` and the library docs) is a different thing and stays. Behavior is unchanged; 2,580 tests pass.
- **The Great Rename, folders (3): two file names**: the dev tool `scripts/dev/listening/sort_whisper_text.py` is `split_whisper_tracks.py` (it shows the TrackSplitter), and `tests/scripts/test_playgrounds.py` is `test_testbench.py`. Behavior is unchanged; 2,580 tests pass.
- **The Great Rename, folders (2): the other packages**: each lexicon section is its own package now. `pom.output` is `pom.printing`, `pom.voice` and `pom.prompt` are merged into `pom.writing` (the prompt builder, the oracle model client and the
  portent writer), and `pom.scoring` is split: the bar, the BarKeeper, its factory, the three bar algorithms and the `Attempt` (cut out of `hinge_finder.py` into its own module) are in `pom.qualifying`, the `HingeFinder` is in `pom.conductor`,
  the `TypedLineScorer` is in `pom.keyboard`, and `pom.scoring` keeps the clip scorer, the scorer model client, the word tagger and the fallback. The tests and the dev tools move the same way (`tests/printing/`, `tests/writing/`, `tests/qualifying/`, `tests/conductor/`,
  `tests/keyboard/`, `scripts/dev/printing/`, `scripts/dev/writing/`), and the loggers follow the module names. Imports that went through `pom.scoring` now name the package that owns each name. Behavior is unchanged; 2,580 tests pass.
- **The Great Rename, folders (1): Library**: the corpus code and data take the library's names. The package `pom.corpus` is `pom.library` (the tests are in `tests/library/`), the data folder `corpus/` is `library/` with `chunks/` as `passages/`
  (the shelves inside keep their names), `weights.json` as `passages/circulation.json` and `sources.csv` as `holdings.csv`, the dev tools are in `scripts/dev/library/` (`corpus.py` is `library.py`), and `documentation/corpus-notes.md` is
  `library-notes.md`. Every path in the code, tests, scripts, experiments, `.gitignore` and docs follows; the gitignored `raw/` folder moved with the rest. The JSONL keys (`source`, `register`) and the words in comments are unchanged until the word pass.
  A clone that pulls this has a new `library/` folder and keeps nothing named `corpus/` (git removes the old folder; a gitignored `corpus/raw/` left behind can be deleted or moved to `library/raw/`). Behavior is unchanged; 2,580 tests pass.
- **The Great Rename, Qualifying (and the monitor's and startup's names)**: the part that decides what to respond to takes the lexicon's names. `ConvoInterestTracker` is `BarKeeper` (module `bar_keeper.py`; `tracker_from_settings` is
  `bar_keeper_from_settings` in `bar_keeper_factory.py`), `ScoredSentence` is `Attempt`, `TriggerBar` is `Bar` (module `bar.py`), and `ScoreEffect` is `Ruling` with `record()` as `rule()`, `record_direct()` as `rule_typed()` and `trigger_level_at()` as
  `bar_at()`. The rulings are `NO_MARK` ("no mark"), `DID_NOT_QUALIFY`, `QUALIFIED`, `HELD` and `TYPED_DNQ` ("typed DNQ"), so a score that used to be *triggered* now *qualifies*. **Config:** `[trigger]` is `[qualifying]` (`config.qualifying`,
  `QualifyingSettings`), `exclude_triggers` is `exclude_qualifiers` and `typed_score_multiplier` is `typed_handicap`; `config/pom.toml` is changed to match, and a copy of an old name in `config/pom.local.toml` (here or on the Pi) would now be ignored with
  a warning. The monitor keeps a `TurnRecord` in a `TurnHistory` (were `TriggerRecord` and `TriggerHistory`, module `turn_history.py`), startup's `Status` and `Outcome` are `StepStatus` and `StepOutcome`, and the playground
  `serve_fake_triggers.py` is `serve_fake_turns.py`. A sweep also put the new class names into the comments and docs that rope had left, fixed two stale `__all__` entries, and corrected a/an before the new names. The word *trigger* in prose and the `pom.scoring`
  package name are separate passes. Behavior is unchanged except that the ruling values (`"qualified"`, `"typed DNQ"` and so on) show in the debug log; 2,580 tests pass.
- **The Great Rename, Listening (2 of 2): the config keys**: the `[listening]` keys say what they do and carry their units. `step` is `cut_every_seconds`, `hold` is `hold_back_seconds`, `rewind` is `rewind_seconds`, `max_buffer` is `max_cut_seconds`,
  `sound_every_steps` is `room_tone_pass_every`, `sound_seconds` is `room_tone_pass_seconds`, `ambient_memory` is `room_tone_memory` and `consider_ambient_sounds` is `score_room_tone`. The `RollingCutter` and `Eavesdropper` parameters, the `ListeningSettings` fields and
  the `DEFAULT_` constants follow, and `config/pom.toml` is changed to match. A copy of an old name in `config/pom.local.toml` (here or on the Pi) would now be ignored with a warning; the shipped files have none. The words ambient, fragment, listener
  and source in comments, messages and test names are a separate pass. Behavior is unchanged; 2,580 tests pass.
- **The Great Rename, Listening (1 of 2): the classes and modules**: the listening side takes the lexicon's names. The audio sources are decks (`MicSource` is `MicDeck`, `WavSource` is `WavDeck`, `MacMicSource` is `MacMicDeck`, `AudioSource` is `AudioDeck`),
  `WhisperServerClient` is `WhisperModelClient`, `WhisperTextSorter` is `TrackSplitter` (it hands on `Tracks`, was `WhisperText`), `Listener` is `Eavesdropper`, `HeardSegment` is `Take`, `SegmentFragmenter` is `TakeCutter`, and `Fragment` / `FragmentLevel`
  are `Clip` / `ClipLevel`. Their errors, modules (`mic_deck.py`, `eavesdropper.py`, `take_cutter.py` and so on, so the loggers follow) and tests follow. The experiments that import them use the new names; the hearing-debug folder's own
  `whisper_server_client.py` is its own file and keeps its name. Config keys and the words (ambient, fragment, source, sound pass) follow in 2 of 2. Behavior is unchanged; 2,580 tests pass.
- **The Great Rename, Scoring**: `Scorer` is `ClipScorer` (module `clip_scorer.py`, error `ClipScorerError`), `ScoredFragment` is `ScoredClip`, `Word` (the word tagger's) is `TaggedWord`, `score_from_probabilities` is `score_from_scorecard`,
  `is_fragment_filtered` is `is_clip_filtered`, and `WARM_UP_FRAGMENT` is `WARM_UP_CLIP`. The fragment filter is the **clip filter**: the config key `scorer.fragment_filter_score` is `scorer.clip_filter_score` (a copy of the old name in
  `config/pom.local.toml` would now be ignored with a warning; the shipped one has none), and `ScoredBy.FRAGMENT_FILTER` (`"fragment_filter"`) is `ScoredBy.CLIP_FILTER` (`"clip_filter"`). The word *fragment* itself becomes *clip* with the Listening
  batch, where `Fragment` does. Behavior is unchanged; 2,580 tests pass, and every `from pom... import` in experiments, scripts and tests still resolves.
- **The Great Rename, Writing**: `OracleModelClient.portent()` is `consult()` (put the finished prompt to the oracle model). The `pom.voice` package keeps its name until the packages move together. Behavior is unchanged.
- **The Great Rename, Printing**: the output side takes the lexicon's names. `TypewriterFormatter` is `PortentFormatter`, `TypewriterPort` is `TerminalPort` (its `type()` is `transmit()`), `ConsoleTypewriter` is `ConsoleTerminal`,
  `TypingReport` is `PrintResult`, and `OutputSettings` is `PrintingSettings`. The modules are `portent_formatter.py` and `terminal_port.py`, the tests follow. **Config:** the `[output]` section is `[printing]`
  (`config.printing`), and `terminal.type_during_output` is `terminal.type_during_printing`; `config/pom.toml` is changed to match, and a copy of the old names in `config/pom.local.toml` would now be ignored with a warning (the shipped one has
  none). The `pom.output` package keeps its name until the packages move together. Behavior is unchanged; 2,580 tests pass.
- **The Great Rename, Library (the classes, modules and scripts)**: the corpus code takes the lexicon's names. `ChunkStore` is `Catalog`, `Chunk` is `Passage`, `DrawWeights` is `Circulation`, `ExemplarSelector` is `Bibliomancer`, and their
  errors follow (`CatalogError`, `CirculationError`, `BibliomancerError`). The modules are `catalog.py`, `circulation.py` and `bibliomancer.py` (so their loggers are `pom.corpus.catalog` and so on), the tests follow, and the dev
  scripts are `scripts/dev/corpus/cataloguer.py` (was `chunk.py`) and `circulation.py` (was `draw_weights.py`). Behavior is unchanged, and so are the data files on disk, the `corpus/` folder and the `pom.corpus` package: they move in later
  batches, with the words register, source and chunk. The three experiments that import these classes use the new names. Old CHANGELOG entries keep the old words. Done with rope (scope-aware), then checked against the whole suite (2,580 passed).
- **The shipped trigger leaves triggering scores out of the bar** (`exclude_triggers = true`). The zinger that fired no longer becomes the room's best score and holds the bar up, so a good score soon after a great one can still fire, and a quiet room stays alive. Replayed on the five recordings
  with EWMQ at 5 minutes and percentile 99.5: about 13.5 triggers an hour in busy rooms (7.9 before), 11.1 in a quiet one (3.6 before), and a lower zinger within 3 minutes of a higher one 2.2 times an hour (0.1 before). The rate is higher; percentile and half-life are the levers for it.
- **The chart looks like graph paper**: black dots for the sentences (off-white in dark mode), the bar as a red line (a lighter red in dark mode), triggers still orange, and solid vertical lines every 5 minutes to match the horizontal ones (the time labels stay every 10). The caption says so. Chosen from a live preview. 9 new chart tests, 3 new page tests; 19 mutants checked.
- **The EWMQ bar's "window" is the effective size**: `DecayingQuantileBar.window_size_at` now gives the scores' worth of weight the real scores carry (a score counts 1 when heard and half that a half-life later), to the nearest whole score, so a busy room shows about 100 at a 5 minute half-life
  and not the 600 or so scores kept in memory for 40 minutes. 8 mutants checked.
- **The shipped trigger algorithm is now `decaying_quantile`** (EWMQ: 5 minute half-life, percentile 99.5, 60 s cooldown, starting bar 6.8, `exclude_triggers` off). On the five recordings it gave about 7.9 triggers an hour (busy) and 3.6 (a quiet
  room, with gaps up to 41 minutes), against 10.1 and 6.4 for the percentile rule; a lower zinger rarely follows a higher one within 3 minutes (0.1 an hour) until `exclude_triggers` is turned on. The percentile rule is one line away.
  The test that pinned the shipped algorithm now only checks it is one the factory knows.
- **The trigger's algorithm is selectable** (`[trigger] algorithm = "percentile" | "decaying_quantile" | "moving_average"`, default `percentile`, so nothing changes until it is switched). `ConvoInterestTracker(bar, cooldown_seconds)`
  now takes a `TriggerBar` and keeps only what every way shares (the trigger decision, the cooldown, the clock); `tracker_from_settings(config.trigger)` builds the bar the config names, each bar reading only its own keys.
  New settings: `starting_bar` (6.8, shared: the bar begins there and slides to the room's own level, replacing the silent warm-up), `exclude_triggers` (off; leaves a triggering score out of the bar's memory),
  `half_life_seconds` (300, decaying quantile) and `average_fraction`, `average_half_life_scores`, `average_floor_fraction` (moving average). `pom.toml` has a section for each. The playgrounds and the hearing-debug probe
  use the factory and print `tracker.describe()`. Checked against the tagged percentile tracker on all five recordings: the same effect for every one of 6,768 scores with an unreachable starting line, and with 6.8 the same after the
  first 20 scores (the only difference: 2 triggers in the first 20 scores of two recordings, the warm-up going away). The tracker's 117 tests were replaced by 82 over a fake bar plus the bars' own; the factory has 29, the config tests grew.
- **The shipped trigger values are no longer pinned by a test** (`percentile`, `window_seconds`, `min_scores`, `cooldown_seconds` in `config/pom.toml`). They are tuned
  often, and the test that made them equal the code's defaults turned the suite red at every change. It now checks only that the shipped
  file loads without a warning and that the tracker accepts the values. The `[listening]` values stay pinned.
- **The trigger window is five minutes** (`window_seconds = 300`, in `config/pom.toml`, the defaults of the tracker and `Config`, and the tests that pin
  them), with the percentile still 99.5. Wes lowered it to make the Printer answer more often. Replayed on the five recordings it gives about
  9.8 triggers an hour (8.1 to 11.2 by meeting; the live room 11.2, 18 in 96 minutes, one every 5.4 minutes, the longest gap 14 minutes),
  against 6.7 with twenty minutes and a goal of about 6. A busy five minutes holds about 75 scores, so 99.5 is still the best score of
  the window there; it only separates from 100 above about 200 scores.
- **Oracle `temperature` back to 0.8** in `config/pom.toml` (it was 0.3 since the `[oracle]` section was made). At 0.3 the model takes the
  likeliest words, and the portents stayed close to the one exemplar's register and to the hinge's own words; 0.8 is also the
  `OracleModelClient` default. Whether it makes the portents more lyrical and sideways is still to be seen.
- **Two playgrounds now just call the new modules** (`listen_to_room.py` calls the `HingeFinder`, `write_portent_from_hinge.py` calls the
  `PortentWriter`), so the wiring lives in `src` and is no longer copied in scripts. Checked against the old versions first: on the
  first 15 minutes of the live-room recording `listen_to_room.py` gave all 233 sentences with the same score, scorer, trigger mark and
  window note (3 would-trigger marks); `write_portent_from_hinge.py` gave the same exemplars for the same hinges, with and without a
  seed. Small differences: `listen_to_room.py` prints one line per sentence (no separate line for the whole segment heard), each
  caption on its own line, and follows `consider_ambient_sounds`; `write_portent_from_hinge.py` shows the exemplars only when a
  portent was written, since the `Portent` carries them.
- **The trigger is set to the 99.5th percentile of the last twenty minutes** (`percentile = 99.5`, `window_seconds = 1200`, both the shipped
  config and the defaults of the tracker and `Config`). 100 (beat the best of ten minutes) fit the five recordings best (6.4 an
  hour, 4.0 to 8.1 by meeting), but a single lucky high score then holds the bar up for the whole window; 99.5 gives 6.7 an hour
  (3.4 to 9.3), the live room 9.3 (15 triggers in 96 minutes). Below 100 the rate follows how much the room talks, about one
  sentence in two hundred. Defaults pinned by tests and mutation-checked (5 of 5).
- **The trigger is a percentile, not Tukey's fence** (`[trigger] percentile`, default 100, replaces `fence_multiple`; `window_seconds`
  now 600; `ConvoInterestTracker(percentile, window_seconds, min_scores, cooldown_seconds)`; the tracker tests rewritten, 96 to 117,
  mutation-checked: 21 of 21 and 10 of 10 for the setting). The bar is now the `percentile` of the window by nearest rank, always
  a score the room earned; 100 means beating the best score of the last ten minutes. Why: on the scorer's lumpy scores the fence
  sat above the window's own best in two windows out of three (it reached 9.85 on a 0 to 9 scale in a live call), and gave 12.5
  triggers an hour in a live room against a goal of 6. Replayed on five recordings (four Zoom, one live room) with the 60 s
  cooldown: 6.4 an hour overall, 4.0 to 8.1 by meeting, against 8.2 (4.1 to 12.5) for the fence at 0.8. A `fence_multiple`
  left in a config is warned about as an unknown key. The playgrounds, the hearing-debug probe (which uses the real tracker) and
  the docs follow. Not added: a minimum score, which would be an absolute number (it changed nothing on these recordings).
- **Design doc in the project's own voice**: the lines that named a person as the source of a decision or a judgment ("Wes judged the wait acceptable", "the prompt is Wes's") now say what was decided or that the prompt is human-written, and the one-rater note says "an LLM judge". Facts, dates and numbers are unchanged.
- **Playgrounds renamed so the file says the job** (in `scripts/dev/`): `listening/sort.py` is now `sort_whisper_text.py`, `listening/hear.py` is `listen_to_room.py`, `listening/fragment.py` is `cut_speech_into_hinges.py`, `scoring/score.py` is `score_typed_sentences.py` and `voice/portent.py` is `write_portent_from_hinge.py`. Nothing else changed; the tests, README, design doc, handoff and earlier changelog entries use the new names. From now on a leaf or branch is tried by hand with these.

### Added

- **`listening_to_portent.py --monitor` feeds the chart**: every scored sentence goes into a `ScoreTrace` (score, the bar it was judged against, audio time, whether it triggered; numbers only, kept for `history_seconds`) that the site draws, so the page now shows the room's scores and the bar riding over them live.
- **The chart on the monitoring page**: with a `ScoreTrace` (new `trace=` argument of `MonitorSite`) the page draws the last 30 minutes of scores and the bar above "Triggers, newest first", as server-made inline SVG with a one-line caption; the page script refreshes it from the new `/chart.svg` route
  on every 5 s poll, and its trigger dots link to the trigger pages. Numbers only, so it is drawn even when hinges are hidden. Without a trace there is no chart. 17 tests; 17 mutants checked (3 are equivalent).
- **`render_score_chart`** (`pom/monitor/score_chart.py`, 36 tests, mutation-checked: 33 of 36 caught, the other three are the drawing's own size constants), the chart for the monitoring site as a pure function from `ScoreTrace` points to an inline SVG: grey dots for every scored sentence on a fixed 0 to 9 scale,
  the bar as a stepping line (broken where there was no bar), larger orange dots for triggers (linked to their pages where the record is known), a time axis back from now. Numbers only. Not on the page yet.
- **`ScoreTrace` and `TracePoint`** (`pom/monitor/score_trace.py`, 57 tests, mutation-checked: 33 of 33 caught), the data for a chart on the monitoring site: one point per scored sentence (when it was heard, its score, the bar it was judged against,
  whether it triggered), numbers only and in memory only, bounded by age and count, thread-safe. Times are the audio clock, so a recording replayed fast draws as a live run would. No words from the room are kept for non-triggers.
  Not wired in yet.
- **`listening_to_portent.py --monitor`**: the real `MonitorSite` runs beside the pipeline and every trigger is added to its `TriggerHistory` as it happens (hinge, score, the bar and window as they stood, the exemplars and the portent), with the status tiles
  (bar now, effective window, sentences scored and heard, whisper, scorer and oracle up or down, checked at most every 15 s) and the trigger algorithm. It needs a password under `[monitor]` in the gitignored `pom.local.toml`; the page keeps `history_seconds` of triggers in memory only.
  A trigger appears once its portent is written. A finished recording leaves the site up until ctrl-C. New `--config` option. Tried for real on a recorded meeting: the page, the JSON feed, the services and the password check all worked. 2 smoke tests.
- **The monitoring site reports the trigger algorithm**: `MonitorStatus` gains `algorithm` and `trigger_description`; the page shows "Trigger rule: decaying_quantile - a score above the 99.5th percentile of scores that count half as much every 300 s, ..." under the tiles and on each trigger's page,
  and `/api/triggers` carries both in `stats`. Empty means nothing is said. 12 tests; 22 mutants checked.
- **`describe()` on every bar**: one line saying how it works and how it is set (for example "the 99.5th percentile of the last 300 s of scores (at least the last 20), starting at 6.8"), for playgrounds and logs. 5 tests.
- **`MovingAverageBar`** (`pom/scoring/moving_average_bar.py`, 59 tests, mutation-checked: 34 of 34 caught), the first trigger method restored from git history (59eb8fc^) so it can still be chosen: a floor below which scores are ignored, a decaying average (half-life in scores), the bar a
  fraction above the average. New: the average starts at `starting_bar / (1 + fraction)` so there is no warm-up. Not wired in yet.
- **`PercentileBar`** (`pom/scoring/percentile_bar.py`, 61 tests, mutation-checked: 41 of 43 caught, the other two equivalent), today's percentile rule moved into a bar of its own: a nearest-rank percentile of a window of time (the larger of `window_seconds` and the last
  `min_scores` scores), with a `starting_bar` in place of the silent warm-up (until `min_scores` scores have been heard the bar is the starting line, not nothing) and an opt-in `exclude_triggers`. Not wired into the tracker yet; the tracker still carries its own copy.
- **`TriggerBar` and `DecayingQuantileBar`** (`pom/scoring/`, 97 tests, mutation-checked: 43 of 44 caught, the last is equivalent), the first step of making the trigger's algorithm selectable. `TriggerBar` is the small contract a bar
  keeps (`level_at`, `window_size_at`, `add`, `ignores`); `DecayingQuantileBar` is EWMQ: scores weighted by 2 ** (-age / half_life_seconds), the bar their `percentile` read by linear interpolation through the
  cumulative weight (so 99.5 works with a few dozen scores, and a faded score hardly matters), a `starting_bar` that blends in with a fading weight so there is no warm-up period, and an opt-in `exclude_triggers`
  that leaves a triggering score out of what the bar remembers. Not wired into the tracker or the config yet; the percentile rule still runs.
- **"What the trigger must do"** in the design doc's trigger section: four requirements the rule is judged against (two good triggers can follow each other; responsive to room heat in the five-to-ten-minute range;
  a quiet room is not silenced by one good trigger; nothing absolute), the rate of about six an hour at random as a tuned preference, and the 60 s cooldown as a practical choice.
- **"Triggers we tried"** in the design doc's trigger section: the five rules considered (a fixed threshold, a moving average, Tukey's fence, a percentile of a timed window, double MAD), what each
  did well and badly in the replays, and what the attempts taught. Mostly conceptual, with a few figures.
- **Newer and Older links on a trigger's page** (`MonitorSite`, 7 new tests, mutation-checked on the new lines: 11 of 11 caught). They go to the next trigger up and down the list
  (the list is newest first), by the triggers' own numbers; a trigger with nothing on one side shows that label greyed, and a neighbour that has expired is not linked.
- **`serve_fake_triggers.py`**, the playground for the monitoring site (`scripts/dev/monitor/`, 2 smoke tests): the real `MonitorSite` over invented
  triggers and an invented status, so the page can be opened and clicked before the pipeline feeds it. `--live N` adds a trigger every N seconds,
  `--hide-hinges` shows the numbers-only page, `--check` reads the JSON feed once and exits. Password `demo`.
- **`MonitorSite`** (`pom/monitor/monitor_site.py`, 80 new tests, mutation-checked: 64 of 64 caught) and **`[monitor]` in the config** (19 new tests, 19 of 19 caught), the
  monitoring site. A Flask page of the last hour of triggers: summary tiles (triggers in the hour and at that pace, the last one, the bar now,
  scores in the window, sentences scored and heard, uptime, which servers are up), then the triggers newest first, and for each a detail page
  with the hinge, the exemplars the oracle was shown, the portent, the score, the bar and the window at that moment. `/api/triggers` is the same as
  JSON, and the page polls it every 5 s, updating in place and reloading when a newer trigger arrives. Behind a password (HTTP basic auth, any
  user name, compared in constant time); the site will not start without one. `show_hinges = false` hides the hinge text everywhere. All text is
  escaped, pages are never cached, and a Content-Security-Policy allows only the page's own few lines of script. A taken port, a missing
  password or a failing status function is reported, never raised. Settings: `enabled` (off), `host` (0.0.0.0), `port` (8080), `password` (empty:
  set it in the gitignored `pom.local.toml`, never in `pom.toml`), `show_hinges` (true; a value that cannot be read hides them), `history_seconds`
  (3600). The shipped file has the site off and no password, pinned by a test. `flask` joins `requirements.txt`. Not yet fed by the pipeline.
- **`TriggerHistory` and `TriggerRecord`** (`pom/monitor/trigger_history.py`, 72 tests, mutation-checked: 36 of 36 caught), the first piece of
  the monitoring site. `add(hinge, score, bar, window_size, heard_at, portent)` keeps one record per trigger (numbered from 1,
  never reused, stamped with the clock); `recent()` gives the last hour newest first, `get(id)` one record. **Memory only**: a hinge is
  what someone said in the room, so records are never written to disk, logged or reported, and they go after `max_age_seconds`
  (an hour) or when the Printer restarts. Bounded by age and by `max_records` (500), thread-safe, and a bad record or setting is
  warned about without its text and never raises. The site, its settings and the Conductor's use of it come next.
- **`listening_to_portent.py`**, the playground for the whole pipeline but the typewriter (`scripts/dev/pipeline/`, 3 smoke tests):
  a microphone (`--mac-mic`, the Mac's system default input; otherwise the Pi's) or a recording (`--wav`) goes through the
  Listener, the `HingeFinder` and the `PortentWriter`, printing each sentence with its score and the trigger's standing, and
  the portent where the typewriter would type it. Like the Printer it is not listening while the oracle writes. The config
  is read again before each portent, so prompt edits show at once.
- **`HingeFinder`** (`pom/scoring/hinge_finder.py`, 38 new tests, mutation-checked: 34 of 34 caught), the second of the two modules that
  move the wiring out of the playgrounds and into `src`. `find()` is a generator over the Listener, the SegmentFragmenter, the
  Scorer and the `ConvoInterestTracker` and yields a `ScoredSentence` for each thing heard (text, kind, score, who scored it,
  the trigger's effect, the time, the bar and window as they stood, the count); the one with `is_hinge` is the sentence to
  answer. Ambient captions are yielded either way; they are scored and counted by the trigger only when
  `consider_ambient_sounds` is on. Pausing while the oracle writes is the Conductor's job. Nothing that was heard reaches a log
  or a report.
- **`[listening] consider_ambient_sounds`** (default `false`; 5 new config tests, mutation-checked: 7 of 7 caught). Whether captions
  of sounds like "(dog barks)" may be scored and become hinges the way speech does: the Printer reacts to what is happening
  around it, and a dog barking may be all that is happening. It only sets the flag; the `HingeFinder` will read it. Off
  keeps today's rule that captions are never scored. How a caption scores is untried, so the flag stays off.
- **`MacMicSource`** (`pom/listening/mac_mic_source.py`, 15 new tests, mutation-checked: 16 of 16 caught), the Mac's microphone through
  `ffmpeg` and AVFoundation, so the listening side can be tried live on a laptop. It is a `MicSource` that changes only the
  capture command and the wording of its faults (`ffmpeg was not found ... brew install ffmpeg`); the buffering, the
  drop-the-oldest rule and the never-stop faults are the one copy in `MicSource`, which gained a small `_command` method for
  this (its own behaviour is unchanged, 6 more mutants checked). The default device is `default`, the system's default
  input, which on a Mac may be a webcam or headset rather than the built-in microphone. It does not test the Pi's microphone path.
- **`PortentWriter`** (`pom/voice/portent_writer.py`, 30 new tests, mutation-checked: 23 of 23 caught), the first of two
  modules that move the wiring out of the playgrounds and into `src`. `write(hinge, seed=None)` joins the `PromptBuilder` and
  the `OracleModelClient` and returns a `Portent` (text, the exemplars shown, seconds, cut off, word count), or None if no
  portent could be had, the reason being in the report of the part that met it (`report_lines()` reads all three). A seed
  makes the same hinge show the same exemplars (the generator must be the one the `ExemplarSelector` was built with); a seed
  it cannot use is recorded rather than ignored. Neither the hinge nor the portent reaches a log or a report.
- **Playgrounds** (`scripts/dev/`, 10 smoke tests in `tests/scripts/`), the way to see each built part work, all
  using the real modules: `listening/sort_whisper_text.py` (type whisper-style text, see how it is sorted), `listening/listen_to_room.py` (the
  microphone or a recording through the Listener, Scorer and trigger, printing what it hears with scores and the trigger),
  `scoring/score_typed_sentences.py` (type sentences, see their scores and whether the trigger would fire) and `voice/write_portent_from_hinge.py` (type a
  hinge, get a portent; it began as `experiments/oracle-playground/probe_oracle.py`, which stays as it was). Tried against
  real servers: the score and hear playgrounds with a 4B scorer and whisper on a recorded meeting, `write_portent_from_hinge.py` against the
  9B. `playground` and the new listening terms are in the lexicon. Experiments stay standalone snapshots.
- **`[listening]` in the config** (`ListeningSettings`, 11 settings, 56 new tests, mutation-checked: 33 of 33 caught),
  the sixth step of the listening side: how the Printer hears the room. The whisper-server's address (also
  `POM_WHISPER_URL`) and timeout, the microphone's capture device and buffer, the `RollingCutter`'s `step`, `hold`, `rewind`
  and `max_buffer`, and the Listener's `sound_every_steps`, `sound_seconds` and `ambient_memory`. The defaults are the
  combination that worked in the room (whisper `base.en` with its voice detection at 0.5, a run every 5 seconds), and a
  test pins the committed `pom.toml` to those exact values, so the fragile setup cannot drift by accident; changing it
  on purpose means changing that test too. The microphone's capture gain stays a startup duty, since it resets on every
  reboot. The entry point builds the `WhisperServerClient`, `MicSource` and `Listener` from `config.listening`.
- **`Listener`** and **`HeardSegment`** (`src/pom/listening/listener.py`, 68 tests, mutation-checked: 64 of 64 caught), the
  fifth step of the listening side, which joins the parts. `hear()` is a plain generator: it reads an `AudioSource`,
  feeds the `RollingCutter`, asks whisper every few seconds, sorts each finished segment with the `WhisperTextSorter` and
  yields a `HeardSegment` (the new words and their kind, the new ambient captions, and where in the stream it ended) for
  each one that holds something new. Words identical to the last are skipped, captions identical to the last are not
  repeated, and the last six captions are remembered for the oracle model's context. After three rounds in a row with no
  speech it asks whisper once without the voice detector and keeps only lyrics and captions. Whisper being down is one
  warning for each outage; the audio is kept and tried again. The Conductor will run `hear()` in a thread of its own.
  Checked on a whole recorded 60-minute meeting through the real client and a real whisper-server: all 705 things
  heard are identical, to the second and the word, to what the hearing-debug experiment heard on the same recording.
  **Known limit, inherited from the experiment:** the sound pass listens to about two seconds, not the ten it is
  asked for, because a stretch with no speech is trimmed to its last two seconds before the pass runs. So lyrics sung
  between passes are mostly not heard. Fixing it needs the Listener to keep its own recent audio; not done, since the
  port is meant to match the known-good setup. In this meeting the pass never fired.
- **`RollingCutter`** (`src/pom/listening/rolling_cutter.py`, 99 tests, mutation-checked: 67 of 67 caught), the
  fourth step of the listening side and the heart of the known-good setup. Every `step` seconds (5) it hands whisper the
  audio since the last kept segment, with whisper's own voice detection deciding what is speech, and keeps the
  segments that ended at least `hold` seconds (2) before the end of the buffer, so a thought still going is not cut in
  two. A segment that does not end like a sentence is joined to the next; a buffer with no speech is trimmed to its last
  two seconds; a thought that outgrows `max_buffer` (30 s) is forced out; an optional `rewind` (off) re-listens to the end
  of the last kept segment. Time is counted in audio received, so a recording replays exactly as a live stream. If whisper
  cannot be reached the round returns nothing and the audio stays for the next; a client that raises is a warning, not a
  crash. Checked against the experiment's cutter on 400 random runs (random speech timelines, settings, feeding
  patterns and failed rounds): no difference. The mutation check found two redundancies in the port (a window always
  starts where the buffer starts), which are removed.
- **`WavSource`, `MicSource` and `AudioSource`** (`src/pom/listening/`, 86 tests, mutation-checked: 31 of 31 and
  43 of 43 caught), the third step of the listening side. An `AudioSource` is anything that gives out 30 ms frames of
  16 kHz mono 16-bit sound and can be closed. `MicSource` runs the Pi's `arecord` and reads it in a thread of its own
  into a buffer (10 s), so a slow whisper run cannot make it lose sound; if the listener falls so far behind that the
  buffer fills, the oldest frames are dropped, counted, and warned about once for each stretch. `WavSource` plays a
  recording at real time, faster or flat out (speed 0), with 1.2 s of silence after the end, for tests and for trying a
  change on a meeting we already have; the Printer never constructs it. Neither raises: no `arecord`, a microphone that
  stops, a missing file or a recording in the wrong format is a CRITICAL fault in `report` and the source just ends
  (`strict=True` raises, for tests). The tests use stand-in `arecord` programs and generated WAV files.
- **`WhisperServerClient`** and **`audio_format`** (`src/pom/listening/`, 65 tests, mutation-checked: 47 of 47 caught),
  the second leaf of the listening side. The client sends audio to a running `whisper-server` from memory (a WAV made in
  memory, never a file) and returns plain text (`text`) or timed `Segment`s shifted by an offset (`segments`, asked for as
  SRT, because `verbose_json` crashes whisper.cpp 1.9.5 on audio with no speech), and `ping`s it. A server that is down,
  slow or odd gives an empty result and a warning in `report`, never an exception; `error` says what went wrong with the
  latest request so a caller can tell "heard nothing" from "could not ask". The transport is injected, and the tests run
  against a small stand-in server on a local port. Neither audio nor text reaches a log or a report. `audio_format` holds
  the numbers every listening module shares (16 kHz, 16-bit, 30 ms frames). Checked against the experiment's client on
  a real whisper-server: identical text and segments, with and without the voice detector.
- **`WhisperTextSorter`** (`src/pom/listening/whisper_text_sorter.py`, 48 tests, mutation-checked: 22 of 22 caught),
  the first leaf of the listening side moved from `experiments/hearing-debug/` into `src/pom/`. It sorts what whisper wrote
  about a stretch of sound into words (speech, and lyrics with the music symbols removed), ambient captions such as
  "(dog barks)" (kept once each, never scored) and nothing-here marks such as "[BLANK_AUDIO]" (dropped). Pure text,
  immutable result, never raises. Checked against the experiment's `split_whisper` on 40,000 random strings: no
  difference on the speech path; on the lyrics-only pass it no longer returns stray spaces or a blank "lyric" when a pair of
  music symbols surrounds only space (a quirk the invariant tests found in the experiment's code).
- **Pi oracle benchmark: the Pi run** (`experiments/pi-oracle-benchmark/results/pi-2026-10-27/`, 2026-10-27, 22 minutes,
  commit `b0b68e9`; the prompt and corpus fingerprints match the bake-off's). Qwen3.5-9B on `pom1`, 12 portents per
  phase from an emptied page cache: with whisper `base.en` and the 4B scorer loaded and idle, a portent took a median
  38.1 s (95th percentile 96.6 s, the slowest of 12), the first word came after 20.3 s, and writing ran at 1.9 tokens a
  second; the 9B alone took 35.4 s (86.6 s), 20.1 s and 2.0. The idle neighbours cost about 3 s, within what 12
  portents can resolve. Cold starts were 50 to 78 s for the 9B and 57 s for the 4B; a restart with the file in memory
  13.5 s. At least 6.7 GB of 16 GB stayed available with all three loaded; no throttling, hottest 73.6 °C. The
  wait for the first word is the Pi reading the prompt (about 0.095 s a prompt token), and the server reused none of
  it (`cached_tokens` 0 on all 24), for a reason not yet known. For scale, the Mac took 0.8 to 0.9 s a portent. The
  results were read back from the Pi and checked by checksum; the portents themselves are not kept. The design doc and
  the experiment's README now carry the numbers.
- **`corpus/samples/benchmark-lines.py`**: a copy of Wes's 100 benchmark sentences (`BENCHMARK` in
  `experiments/interest-scorer/probe_interest_scorer.py`), checked to be identical and in the same order. They are
  test inputs for the Scorer, not corpus material, so `corpus/samples/` has its own one-line README. The experiments
  still read the probe's list, so the two copies can drift apart. `.gitignore` gets `!corpus/samples/`, because its
  `samples/` rule (meant to keep recordings out) matched this folder too; `.wav` and `.m4a` files inside it are still
  ignored.
- **`documentation/pom_design_doc.md`**: the technical design notes, rewritten from Wes's first notes to match what is
  built, measured and decided. New or replaced since the first notes: the Scorer (a small local model, one sentence,
  no context, instead of word lists), the Trigger (Tukey's fence over a five-minute window with a 60 s cooldown, instead
  of a moving threshold and a rate governor), whisper-driven cutting and the three-way sort of whisper's output, the
  oracle model and the bake-off (Qwen3.5-9B leads), half-duplex, the corpus (81 sources, 14 registers, about 72,000
  chunks), and a status for every stage. It ends with a list of the open questions and a list of what changed.
  `project-state.md` now points to it.
- **`[trigger] cooldown_seconds`** (default 60; 0 turns it off; 20 new tests, mutation-checked: 30 of 31 caught, the
  survivor an equivalent mutant). After a trigger, a score above the bar is not marked until `cooldown_seconds` have
  passed since that trigger: it is a new `ScoreEffect.COOLING_DOWN`, still joins the window, and does not restart the
  cooldown. `ConvoInterestTracker` takes it as a fourth setting; the probe shows `[window 83 scores, bar 6.17; cooling
  down]`. Why: oct12 had 18 triggers in five stretches, one of them six in eight seconds (a run of one-line swearing
  jokes, each scoring 6.8 to 7.3 against a bar that five minutes of scores barely moves). Replayed on the four recorded
  meetings, 60 s takes all four from 34 triggers (6.6 an hour) to 21 (4.1 an hour, one every 14.6 minutes, shortest gap
  62 s) and oct12 from 12.2 to 5.4 an hour. With the cooldown, `fence_multiple` 0.9 gives 5.7 an hour (one every 10.6
  minutes) and 0.8 gives 6.8; not changed here. A trigger inside the Printer's own busy time is a separate matter for
  the Conductor, which does not exist yet.
- **`[trigger] fence_multiple`** (default 1.5; 5 new tests). For a bar set by Tukey's fence instead of the room's
  average: how many interquartile ranges above the recent scores' upper quartile (Q3) a score must be to trigger, so
  the bar is Q3 + `fence_multiple` x IQR. 0 puts it at Q3 (about the top quarter of scores); 1.5 is the textbook outlier
  line. It takes over the job `trigger_fraction` does for the average-based bar, under a name that says what it is.
  Read and validated by `Config` (0 or more, else the default with a warning); the ConvoInterestTracker does not use it
  yet, and `trigger_fraction` and `interest_half_life` are untouched.
- **`[trigger] window_seconds`** (default 300, the last five minutes; 7 new tests). How many seconds of recent scores
  the room's level is judged from, as a length of time, not a count of scores, so a busy room and a quiet one both get
  the same stretch. Read by `Config` and validated like the other trigger values (a number above 0, else the default
  with a warning). The ConvoInterestTracker does not use it yet; it is there for the offline time-window replay.
- **Recording-time stamps in the hearing-debug replay** (`probe_hearing_pipeline.py`). With `--wav`, each "heard" line
  is stamped with where in the recording the utterance ended (`+hh:mm:ss`, from the rolling cutter's own segment times)
  instead of the wall clock, which means nothing when a recording is replayed faster than real time. Live listening
  is unchanged. Needed to test a trigger that judges the room over minutes, not over a count of sentences.
- **Pi oracle benchmark** (`experiments/pi-oracle-benchmark/`, 49 tests in `tests/pi_oracle_benchmark/`,
  mutation-checked). Times the 9B oracle model on the Pi with whisper (`base.en`) and the 4B scorer loaded but idle,
  against a baseline of the 9B alone, each from an emptied page cache: startup time (cold and restart), total seconds,
  seconds to the first token, writing speed, and memory, temperature and throttling once a second. It runs the real
  `PromptBuilder` and `OracleModelClient` on 12 of the bake-off's own hinge-and-seed cells; only the client's `post` is
  swapped for a streaming one. Counts and timings are kept, never the portents. Run on the Mac as a reference
  (median 0.8 to 0.9 s a portent, about 68 tokens a second, about 10 GB in use by the three models, no difference
  between the two phases; the prompt and corpus fingerprints match the bake-off). A first Mac run, disturbed by use of
  the playground on the same GPU, was discarded. Run on the Pi on 2026-10-27 (see the newer entry above).
- **Oracle model bake-off** (`experiments/oracle-bakeoff/`, 41 tests in `tests/oracle_bakeoff/`, mutation-checked;
  `documentation/bakeoff-rubric.md` and `documentation/model-bakeoff.md`). Seven 8 to 9B models run through the real
  PromptBuilder and OracleModelClient under Wes's frozen prompt (temperature 0.3, one exemplar): 16 benchmark hinges
  times 10 seeded draws, 160 portents per model, the same hinge-and-exemplar pairs for every model. Each run records the
  prompt hash, the corpus fingerprint, the commit and the llama-server version, so two runs can be checked to have the
  same conditions. The portents were rated blind against seven yes/no marks (follows the hinge, follows the style,
  doesn't repeat the hinge, didn't muddle its details, no extra text, length close, particularly creative); three are
  checked by a program (`bakeoff_checks.py`). Mean score of 7: Qwen3.5-9B 6.08, Gemma 2 9B 5.84, Hermes 3 5.26,
  Ministral 8B 5.21, Llama 3.1 8B Instruct 5.19, Stheno v3.4 4.73, Qwen3-8B 4.45. The write-up compares the models on
  each question and has an awards section. One rater (Claude), not yet spot-checked by Wes.
- **Oracle playground** (`experiments/oracle-playground/probe_oracle.py`). Step four of the voice side. Type a hinge,
  get a portent, using the real `PromptBuilder`, `OracleModelClient`, ExemplarSelector and the config's `oracle.prompt`
  (read again before every hinge). It starts a llama-server for `--model` (a .gguf file or a Hugging Face name) on the
  port in `oracle.url`, or uses a running one with `--server`. The random generator is reseeded from a sha256 of the
  hinge, so the same hinge gets the same exemplars whichever model runs (`--seed N` to vary). Tried on the Mac with
  Qwen3-8B and the placeholder prompt: the wiring works and the same hinge gave the same exemplars; the portents are
  meaningless until the prompt is written.
- **OracleModelClient** (`src/pom/voice/oracle_model_client.py`, 53 tests in `tests/voice/`, mutation-checked). Step
  three of the voice side. `portent(prompt)` sends the finished oracle prompt to the oracle model's llama-server as one
  user message, word for word, with `max_tokens` and `temperature` from the config (not temperature 0: the model
  chooses its words), and returns what it wrote, trimmed, or None with a warning in `report` if the server is down,
  slow, or answers oddly or with nothing. Never raises outside strict mode. A portent cut off at `max_tokens` is
  returned and flagged in `last_cut_off`. Neither the prompt (it holds the hinge) nor the portent is logged. The
  transport is injected, so the tests need no server; the four lines of HTTP are the scorer client's, repeated here
  so the two packages stay apart.
- **`[oracle]` section in the config** (`OracleSettings`, `Config.oracle`; 8 tests, mutation-checked). `url` (a second
  llama-server, port 8099), `timeout` (120 s), `max_tokens` (300), `temperature` (0.8) and `prompt`, the oracle prompt
  with `{exemplars}` and `{hinge}`. `config/pom.toml` has a PLACEHOLDER prompt for Wes to replace. The four numbers are
  my starting values (llama-server's own default temperature is 0.8; the rest are guesses sized for a 15 to 150 word
  portent on the Pi).
- **PromptBuilder** (`src/pom/prompt/prompt_builder.py`, 32 tests in `tests/prompt/`, mutation-checked). Step one of
  the voice side. It fills the oracle prompt (Wes's text, with `{exemplars}` and `{hinge}` marking the places) with
  exemplars drawn by the ExemplarSelector and the hinge (the fragment that triggered), and returns the text with the
  chunks it used. Nothing else is added. Both places are filled in one pass, so braces in a chunk or a hinge stay
  plain text; the exemplars are only drawn if the prompt has a place for them; the hinge is never logged. A bad or
  missing prompt, an empty hinge, or nothing to draw is recorded and never raises (`strict=True` raises). To repeat
  a draw, give the selector a seeded `random.Random` and reseed it before each build (tested, with a seed taken
  from the hinge). The oracle model client is next.
- **`experiments/hearing-debug/probe_whisper_segments.py`**: an offline test of letting whisper's own voice detection
  find the cut points instead of the energy chunker, which in a noisy room cuts every utterance at the 20 s cap
  (measured: 34 to 55% of frames counted as speech in the stretches of an attic recording where nobody spoke). A rolling
  buffer goes to `whisper-cli --vad`; finished segments are kept, unfinished ones joined to the next, the buffer starts
  a little before the last kept segment's end, and segments are split into sentences with the `SegmentFragmenter`. On the
  attic recording it found every sentence of the whole-file answer with none broken; quiet speech still needs level
  normalization. Not wired into the live pipeline.
- **ConvoInterestTracker, step two: avg_convo_interest and the trigger.** `record(score)` takes in one score and
  says what happened to it (`ScoreEffect`: ignored, counted or triggered). Scores at or above the floor feed
  `avg_convo_interest`, a decaying moving average (the first becomes the average, each later one moves it
  `interest_weight` of the way toward itself). A score triggers when it is at least `trigger_fraction` above the
  average as it stood before that score was added, once `min_scores` have counted. Four settings in `[trigger]`:
  `floor_fraction` 0.05, `trigger_fraction` 0.10, `interest_weight` 0.1, `min_scores` 5. 112 tracker tests, mutation-
  checked. Not wired into anything yet; the hearing-debug view is next. Filtered fragments still score
  `fragment_filter_score` = 1.0, above the floor, until it is set to 0.
- **ConvoInterestTracker, step one: the floor** (`src/pom/scoring/convo_interest_tracker.py`, 46 tests in
  `tests/scoring/test_convo_interest_tracker.py`). `avg_convo_floor` is the score below which scores are ignored:
  a fixed fraction of the score range, `floor_fraction` (0.05, so 0.45 on the 0 to 9 scale), set in a new `[trigger]`
  section of the config (`Config.trigger`, `TriggerSettings`). `is_above_floor(score)` counts a score at the floor
  and ignores an unscored fragment or anything that is not a number. A bad setting falls back to the default with a
  warning. avg_convo_interest, which averages the scores above the floor, comes next. Mutation-checked. Note: filtered
  fragments still score `fragment_filter_score` = 1.0, above the floor, until Wes decides to set it to 0.
- **`documentation/lexicon.md`**: the project's vocabulary (segment, chunk, score, hinge, corpus, weights,
  portent, experiments, and terms awaiting confirmation), plus the code names that do not match it yet.
- **Changelog.** This file, in Keep a Changelog format.
- **Logging, a debug flag, and shared config.** The service's stdout goes to journald, so
  problems are logged there. `src/pom/logs.py` sets up stdout logging once (no timestamp, since
  journald adds one, idempotent, never fails). There is one `debug` flag: set it project-wide in
  `config/pom.toml` (or `POM_DEBUG=1`), or override it in a single module with the
  `DEBUG = None` line at the top of that file (True forces that module's debug output on, False
  keeps it quiet). A test refuses to let a module be committed with the override set.
  `src/pom/config.py` holds the shared settings `strict`, `debug` and `log_level` from the
  config file and the `POM_STRICT` / `POM_DEBUG` / `POM_LOG_LEVEL` environment variables, with
  the environment winning, and never raises. A `Report` can be given a logger, and `Weights`
  and `ChunkStore` log their problems, an INFO summary on load, and DEBUG detail.
- **Local log file.** For working on the Mac, `local_logs = true` (or `POM_LOCAL_LOGS=1`) also
  writes the log to `logs/pom.log` with timestamps. The folder is gitignored, the file rotates at
  1 MB with three backups, and if it cannot be opened logging carries on to stdout with one
  warning. Off by default, since on the Pi journald keeps the log. Tests: 34 in `tests/test_logs.py`,
  61 in `tests/test_config.py`, 17 in `tests/test_debug_overrides.py`.
- **Sample count in the config.** `samples_min` and `samples_max` in `config/pom.toml` (or
  `POM_SAMPLES_MIN` / `POM_SAMPLES_MAX`) set how many corpus chunks go into each pull of
  exemplars, 3 to 5 by default. `Config` exposes them as `config.samples`, which the entry
  point passes to `Sampler(samples=...)`; the Sampler never reads Config itself. A bad value
  falls back to the default with a warning and a reversed pair is swapped.
- **Per-machine config override.** An optional, gitignored `config/pom.local.toml` overrides the
  committed `config/pom.toml` (the environment still wins over both). The committed file keeps
  the quiet defaults the Pi runs with; a developer's machine can keep debug logging and
  `local_logs` on without touching them.
- **Report** (`src/pom/report.py`), the shared way every module reports problems as data
  instead of raising: WARNING (recovered) and CRITICAL (cannot do its job, still no crash),
  a `strict` mode that raises for tests and dev tools, a cap on how many issues are kept,
  and criticals that are never dropped. 21 tests in `tests/test_report.py`.
- **Design principle:** the Printer never stops because of a typo or a recoverable fault.
  Written into the `pom` package docstring.
- **Runtime libraries and data for the Scorer:** `requirements.txt` (`wordfreq`, `nltk`),
  `scripts/setup/setup-nltk-data.sh` (fetches WordNet, stopwords and a part-of-speech tagger into the
  gitignored `data/nltk/`), and the decisions behind the Scorer in the project handoff.
- **A project handoff** (`documentation/project-state.md`): where things stand, the working rules,
  the architecture, the design decisions, how to run things, the next steps and the gotchas, for
  picking the work up after a break.
- **The Pi is deployed with git.** It has a clone of the repo at `~/pom` (about 57 MB, 16 MB of history), so
  the layout matches the Mac and code arrives with `git pull`; models and recordings live under the gitignored
  `data/`. The per-experiment copy scripts were replaced: `probe_hearing_push.sh` became `probe_hearing_tune.sh`
  (prompt edits travel by git, see the next entry), and `benchmark_pi_prepare.sh push` became `models`.
  Documented in `documentation/pi-setup-notes.md`.
- **Everything on the Pi now lives inside `~/pom`.** whisper.cpp and llama.cpp are built under
  `data/tools/` (the setup scripts default there and build with a relative `$ORIGIN` library path so the
  folders can be moved without rebuilding), logs under `data/logs/`, and the early whisper sample under
  `data/recordings/samples/`. Scripts and docs that assumed `~/llama.cpp` and `~/whisper.cpp` were updated.
- **Hearing-debug experiment** (`experiments/hearing-debug/`). A live prototype of the front half of the
  Printer for watching how it interprets what it hears and refining the prompt in a closed loop. On the Pi
  (or the Mac, against a recording) it cuts the microphone at pauses, normalizes the level, transcribes with
  whisper, splits sentences, scores them with the real Scorer from `config/pom.toml` and prints the model's
  own written account of each score using `prompt_output_debug`. The config is re-read before every
  utterance, a push script copies it to the Pi each time it is saved, and typing `r` re-scores the last
  sentences with the new prompt and shows old and new. Nothing heard is kept: audio goes through memory-backed
  temporary files that are deleted at once, and the output must not be redirected to a file.
- **Startup** (`src/pom/startup.py`), a stub for everything done once when the Printer starts. The
  runner is built: it runs steps in order, never raises, records a failed optional step as a warning and a
  failed critical one as critical (with `ready` false), and keeps going either way. The ten duties (config
  report, data check, mic gain, audio device, whisper, the Scorer's server and warm-up, the voice model,
  the typewriter's serial port, a readiness line) are listed in `planned_steps()` as placeholders to be
  written one at a time. Tests: 24 in `tests/test_startup.py`.
- **Decisions recorded** (`documentation/project-state.md`): Qwen3-4B is the starting Scorer model and
  7.0 the starting trigger threshold. First look at the real USB microphone on the Pi: it records quietly
  (mean -38 dB) with capture gain to spare, and whisper wraps song lyrics in ♪ marks; the
  Transcriber will strip the symbol and keep the words, since the Printer reacts to what it hears.
  Mic gain on the Pi settled at 24 of 30 after a loud-room test (a yell reached -2.7 dB at 26).
  Whisper's bracketed sound captions are kept too; a variable trigger threshold that follows how
  interesting the room is, starting from 7.0, is the plan.
- **Scorer settings in the config.** `config/pom.toml` gains a `[scorer]` section: the language
  model's address and timeout, the prompt that defines "interesting" and the score scale (kept here as
  the single copy, so it is the main thing to adjust), the floor score, how many content words a
  sentence needs to be sent to the model, and the circuit-breaker numbers. `Config` reads it into
  `config.scorer` (a `ScorerSettings`), validates each value with a warning and a default, merges a
  `[scorer]` key from `pom.local.toml` one key at a time, and accepts `POM_SCORER_URL`. Unknown
  sections and keys are warned about. 30 new tests in `tests/test_config.py`.
- **Scorer** (`src/pom/hinge/scorer.py`). Turns fragments into scored candidate hinges. Sentences
  with no content words (all filler, stopwords or numbers) skip the model and get the floor score;
  the rest go to the interest client. If the model gives no score, a crude word-tool stand-in is used,
  capped below the model's usual top, and a circuit breaker leaves a dead server alone for a while
  (3 failures in a row, 30 seconds by default) before trying it again. Each result says whether it came
  from the model, the filter or the fallback. Never raises and never logs what was said. Tests: 49 in
  `tests/hinge/test_scorer.py`, with fakes for the client and the clock. `Scorer.is_filtered(text)` says
  whether a sentence would skip the model, using the same rule, without scoring it (5 more tests).
- **Interest client** (`src/pom/hinge/interest_client.py`), the first half of the Scorer. Asks a
  local llama-server how interesting one sentence is and returns a score read from the digit
  probabilities, one sentence per request with no context, built exactly as the interest-scorer
  experiment built it. A server that is down, slow or answering oddly gives no score and a warning,
  never an exception; sentence text is never logged or put in a report. Has a warm-up call for startup.
  Tests: 56 in `tests/hinge/test_interest_client.py`, using a fake transport.
  It also has `interpret(sentence)` for the debug view: it sends the debug prompt and returns the
  model's written reply (the score and the content that earned it), with its own longer timeout, and
  `parse_written_score` reads the leading number from such a reply. 32 more tests.
- **Word tools** (`src/pom/hinge/words.py`), the first piece of the Scorer. Reads a fragment as
  words and says what each one is: base form, part of speech, name, number, stopword, filler (a
  configurable list plus any interjection), and whether it can carry interest. Uses NLTK from
  `data/nltk`; if that data is missing it falls back to plain rules and records a warning rather
  than stopping. Tests: 27 in `tests/hinge/test_words.py`, which use fakes and need no data.
- **Interest-scoring probe** (`experiments/interest-scorer/probe_interest_scorer.py`). Part of a
  formative experiment, not of the Printer: asks a local llama-server how interesting each sentence of overheard speech is. By
  default each sentence is sent alone and the model answers with one digit; the score is read from
  the digit probabilities, so it is continuous, repeatable, and independent of the other lines.
  `--batch` sends a numbered list in one request instead (scores shift with order and cluster on a
  few values), and `--context` shows earlier lines. Both prompts are at the top of the script.
- **Interest-scorer experiment** (`experiments/interest-scorer/`, with a new `experiments/` folder
  and README for formative experiments, named `<level>_<subject>`). A fixed bake-off
  (`bakeoff_interest_scorer.py` and `.sh`) that runs the same test, one sentence per request with no
  context, against Qwen3-4B, Llama-3.2-3B and Gemma-3-4B and writes a report; the first saved run is in
  `results/bakeoff-2026-10-22/`. The README records the rules, what was learned, and that the Scorer will
  be a small local model scoring one sentence at a time. `documentation/project-state.md` updated.
  The sample sentences (now `BENCHMARK` in the probe, 100 of them) grew beyond the first 34; the
  scripts follow whichever name the probe uses.
- **Pi benchmark** (`experiments/pi-benchmark/`, prepared but not yet run on the Pi). Measures how fast
  and how heavy the interest Scorer is on the Raspberry Pi 5, alone and while whisper.cpp transcribes
  back to back, with the cores shared (an even 2+2 split is optional), on a small sample (the probe's sentences) and a large one
  (`sample-large.txt`, 294 corpus sentences). Records per-call time, prompt-cache use, memory,
  temperature and throttling, and reports PASS or CHECK against proposed thresholds. A prepare script
  (`check`, `push`, `fetch`) works from the Mac; the runner and harnesses use only the standard library.
  Tested on the Mac with a stand-in for whisper. First quick run on the Pi saved in
  `results/quick-2026-10-22/`: both models stay under 4 s p95 even with whisper running flat out, with
  Llama about 1.7 times faster than Qwen; findings in the README. The full run is in
  `results/full-2026-10-22/`: it adds whisper `small.en` and a 294-sentence sustained run, and finds
  no throttling, a peak of 72 °C, whisper about 2.3 times slower beside the Scorer (still real-time),
  and Qwen's p95 just over 4 s only in the harshest case.
- **Fragmenter module** (`src/pom/listening/fragmenter.py`). Cuts punctuated transcript text into
  hinge candidates at every level together: sentences (at . ? !), clauses (at ; : dashes and
  ellipses) and phrases (at commas), depth first and in speech order, each stamped with the
  timestamp it was given and its sentence index. Single words are kept; a piece that repeats a
  higher level is not repeated. Abbreviations (Dr., e.g.), initials, decimals and times do not
  cause false cuts. Text with no punctuation is cut into windows of at most `max_words` words
  (40 by default). It never stops the oracle: text that is not a string gives nothing and a bad
  limit falls back to the default with a warning; `strict=True` raises. Tests in
  `tests/listening/test_fragmenter.py`, including a fuzz test on random junk.
- **Fragmenter playground** (`scripts/dev/listening/cut_speech_into_hinges.py`): type or pipe in some speech
  and see every fragment with its level.
- **A guide to adding material** (`documentation/adding-material.md`): the whole path from
  choosing a register through chunking, weights, verifying and committing, with the commands. The
  corpus README and notes point to it.
- **A new `nonsense` register**, started with *Alice's Adventures in Wonderland* (Project
  Gutenberg #11; 784 utterances, 790 chunks). Added through the normal pipeline as a test of how
  hard it is to grow the corpus: `add`, `process`, one `set` for the markers and `unwrap`, `promote`,
  `chunk.py`, then one entry in `weights.json`. With equal register shares each register now gets
  1/14 of the draws instead of 1/13.
- **Sampler playground** (`scripts/dev/corpus/sample.py`). Prints pulls of random chunks, or with
  `--stats N` draws N pulls quietly and compares each register's share of draws with its
  target. Builds the real Sampler from the corpus, config and weights, and takes `--weights` to
  try a different weights file before editing the real one.
- **Sampler module** (`src/pom/corpus/sampler.py`). Draws weighted random chunks so each register
  gets its target share over many draws while any combination of draws stays possible. A
  chunk's chance is its register's share times its source's weight over the register's total,
  computed once at startup and never stored. One pull never repeats a chunk; asking for more
  than exist returns fewer. `draw()` with no argument returns a number of chunks in a configurable
  range (3 to 5 by default); `draw(n)` overrides it. The random generator is injected so draws
  can be seeded. It never stops the oracle: a register with nothing drawable is skipped with a
  warning, and nothing at all to draw is a critical issue with an empty result. It counts draws
  per register (counts only) so an over-represented register can be spotted. `strict=True`
  raises. A draw takes about 3 microseconds on the real corpus and building the odds about
  13 ms. 62 tests in `tests/corpus/test_sampler.py`. Chunks are not remembered across pulls; a
  measured note in the module says why no recency decay was added and when to revisit it.
- **ChunkStore module** (`src/pom/corpus/chunk_store.py`). Reads the chunk files into immutable
  `Chunk` objects and answers layout and count questions; its `layout()` is what
  `Weights.check` compares against. It never stops the oracle: a bad line is skipped, an
  unreadable file is skipped, a record that disagrees with its folder or file name is repaired
  (the location wins), a missing id is generated, and only an empty store is critical. Every
  problem is recorded in a `Report` and logged; `strict=True` raises instead. Loading is
  deterministic, and the real corpus (71,474 chunks) loads in about 0.2 seconds. 73 tests in
  `tests/corpus/test_chunk_store.py`.
- **Weights module** (`src/pom/corpus/weights.py`, the first runtime code). Reads
  `corpus/chunks/weights.json`, treats anything unlisted as 1.0, normalizes register shares over
  the registers that have chunks, and checks the file against the corpus layout. It never stops
  the oracle: a bad number counts 1.0, a bad or unknown entry is skipped, a missing or invalid
  file means all defaults, and every problem is recorded in a `Report`. `strict=True` raises
  instead, for tests and dev tools. 75 tests in `tests/corpus/test_weights.py`.
- **Project setup for tests:** `pyproject.toml` (pytest settings), `requirements-dev.txt`, and the
  `pom` virtual environment on Python 3.11 to match the Pi's Bookworm.
- **Output validation stub** (`scripts/dev/corpus/validate_output.py`). Reports nothing yet; its
  header records why a verbatim-overlap check was set aside and what could replace it.
- **Chunks** (`corpus/chunks/<register>/<id>.jsonl`, 71,474 of them). `ready/` utterances sized
  at 15 to 150 words, made by `scripts/dev/corpus/chunk.py`. Long utterances are split at
  sentence or line breaks; exact duplicates are dropped. A chunk holds only facts about itself.
- **Draw weights** in one hand-edited file, `corpus/chunks/weights.json`: a tree of registers,
  each with a share and its sources' weights. `scripts/dev/corpus/draw_weights.py` computes the
  odds at load time (never stored) and warns about unlisted registers and sources.
- **Corpus audit** (`scripts/dev/corpus/audit.py`): artifact rates per file of `ready/`, and
  examples of any check.
- **Corpus text:** 80 sources promoted to `ready/` (about 69,000 utterances in 13 registers),
  provisionally reviewed. New standardizer flags `unbracket`, `unbrace`, `titles`, `linenos` and
  `spellcheck`.
- **Corpus pipeline** (`scripts/dev/corpus/`): `add`, `set`, `process`, `inspect`, `clean`,
  `review`, `promote`, `mix` and `publish-check`, with trim markers, per-source cleaning flags and
  a Project Gutenberg catalog search. Corpus rules and workflow are in `corpus/README.md`.
- **Pi setup scripts** for whisper.cpp and llama.cpp, and Pi setup notes
  (`documentation/pi-setup-notes.md`).

### Changed

- **`documentation/project-state.md` is rewritten to the state of 2026-10-27**: what is built (1,047 tests), what is
  decided (scorer model, the fence trigger, the hinge, the oracle model, half-duplex), the rules we work by (including the
  ones learned since: explain design choices first, Wes writes the prompts, stage files by name, deliver to the Pi through
  git), the module tree, a short dated decision log with the superseded decisions marked, the next steps and the
  gotchas. The design itself now lives in `pom_design_doc.md`.
- **The hinge is the sentence that triggered** (decided by Wes, 2026-10-27). The scorer and the trigger were built
  to hand over about six sentences an hour, so each trigger is used as it occurs, with no separate hinge picker; some
  hinges will scare and some will catch, which is what talking to an oracle means. The design doc says so.
- **The oracle model is Qwen3.5-9B** (decided by Wes, 2026-10-27), with Gemma 2 9B as the named alternate, one
  config change away, unless a problem shows. Gemma was more faithful to the hinge in the bake-off (2 repeats in 160
  against 10) but less creative (68 portents against 81), weaker on style, and about 30% slower on the Mac (1.7 s
  against 1.3 s; its file is 6.8 GB against 5.7 GB). The Pi wait for Qwen3.5-9B (first word after 20 s, a typical
  portent in 35 to 40 s) was judged acceptable. The design doc and the bake-off write-up say so.
- **`fence_multiple` is 0.9** (was 1.0, in the tracker, `Config` and `pom.toml`), chosen with the 60 s cooldown to
  match the goal of about six portents an hour. Replayed on the four recorded meetings with the shipped settings:
  29 triggers in 5.1 hours, 5.7 an hour, one every 10.6 minutes (3.4, 5.1, 7.4 and 7.1 an hour by meeting; shortest gap
  62 s, median 3.3 minutes, longest 29 minutes). At a fifth of the talk the same settings give about 4 an hour. The
  tracker's tests pin their own fence multiple, so they no longer depend on the shipped default.
- **The trigger is Tukey's fence over the room's recent scores, and the floor and the running average are gone.**
  `ConvoInterestTracker` keeps the scores of the last `window_seconds` (300), or the last `min_scores` (20) if the room
  is so quiet that fewer were heard in that time, and a score triggers when it is above Q3 + `fence_multiple` (1.0) x
  IQR of that window as it stood before the score was said. Every score joins the window, the lowest included, so no
  fixed line throws any away. `record(score, now)` now takes the time the score was heard (a clock reading live, the
  place in the recording in a replay); a missing score or time is still IGNORED. Why: replayed on four recorded Zoom
  meetings (4,278 sentences, about 835 an hour), the old rule (25% above the running average) triggered on 34% of
  sentences, one every 13 seconds, and `trigger_fraction` barely moved it (0.35 gave 30%, 0.50 gave 23%): an average sits
  in the middle of the scores, so a good share of them is always above a bar set just over it, and the many scores near 1
  from short fragments pulled the average and the bar down further. The fence at 1.0 gave about 7 an hour in that busy
  room (one every nine minutes) and about 5 an hour at a fifth of the talk. The scores are bunched into lumps with no
  long tail (a spike at 1, and humps near 3 and 5), so a textbook outlier line (1.5) fired almost never.
  **Removed settings:** `trigger.floor_ignore_fraction`, `trigger.trigger_fraction` and `trigger.interest_half_life`
  (a `pom.toml` or `pom.local.toml` still holding them gets an unknown-key warning), and with them the tracker's
  `avg_convo_floor`, `avg_convo_interest`, `interest_weight`, `is_above_floor` and `score_range`. `trigger.min_scores`
  now means the window's minimum and defaults to 20 (at least 2); `trigger.fence_multiple` defaults to 1.0, down from
  the 1.5 added a day earlier. The probe is updated in the next change.
- **The hearing-debug probe runs on the new tracker.** Each sentence's note now reads `[window 69 scores, bar 5.12]`
  (how many recent scores it was judged against, and the score it had to be above), `[warming up 3/20]` before the
  window holds enough, or `[no score]`; the startup banner describes the fence. The tracker's time is the place in the
  audio stream where the utterance ended (the rolling cutter's segment end), not the wall clock, so a recording replayed
  fast with `--wav --speed 0` gives the triggers a live run would. The lexicon (window, fence, trigger), the
  hearing-debug README, `project-state.md` and two docstrings now describe the window and the fence. Mutation-checked:
  23 of 24 tracker mutants are caught; the one that survives is equivalent (with exactly `min_scores` scores inside the
  window, the fallback picks the same scores). A test for a clock stepping backwards was strengthened after two
  mutants showed it proved nothing.
- **The oracle playground prints the exemplars two to a row** above the portent, each headed by its id and wrapped to
  its column (`exemplar_columns`), so the exemplars the model was given can be checked at a glance.
- **The oracle's settings are all in `[oracle]`, and the committed oracle prompt is Wes's.** `exemplars_min` and
  `exemplars_max` moved from the top of `config/pom.toml` into the section (`oracle.exemplars_min` and
  `oracle.exemplars_max`; the old top-level keys are now reported as unknown, and the `POM_EXEMPLARS_MIN`/`MAX`
  environment overrides are unchanged). The file is ordered by the work (global, scorer, trigger, oracle). The prompt is
  Wes's "passage" prompt (the style example, then the theme) in place of the placeholder, with one exemplar, temperature
  0.3 and 300 tokens: the settings the oracle bake-off froze. Two new config tests.
- **PromptBuilder labels and fences what it fills in** (Wes's format). Each exemplar goes in as `**Style Example N**`
  followed by its text in a code fence, and the hinge as `**Theme**` followed by its text in a fence, so the model can
  tell the examples and the theme from the instructions around them. The fence is added in code and never written in
  the prompt, because the prompt itself is generated over. A chunk or hinge that holds a fence of its own is not
  escaped (the docstring says so). Both places are still filled in one pass. Tests updated, and new ones for the labels
  and the numbering.
- **Hearing-debug lets whisper cut the audio; the energy cutter is behind `--energy-cutter`** (Wes's call). The new
  `RollingCutter` (`rolling_cutter.py`) hands whisper-server the audio since the last kept segment every 5 s with its
  own voice detection on, keeps the segments whisper finished (ending 2 s or more before the buffer's end), joins a
  segment that does not end like a sentence to the next. `--rewind` (off, 0 s) would start each round that long before
  the last kept end, to save a sentence's first syllables from a late segment end, but live it re-heard the last word
  ("yourself.", "made it." came out twice). `--step`, `--hold` and `--rewind` set these; `--margin-db` now applies to the energy cutter only. The playground
  `probe_whisper_segments.py` uses the same class and the server, not `whisper-cli`. Word recall against whisper
  reading the whole file, rolling (rewind off) against the energy cutter: attic 93% / 87%, loud music 90% / 90%, quiet
  classroom 52% / 43% (that recording loses about half its words either way). Prompting whisper with the recent text was
  tried and left out: mixed results.
- **Hearing-debug talks to a whisper-server instead of running `whisper-cli` for every utterance** (Wes's call). The
  Transcriber and the new playground (`probe_whisper_segments.py`) share `whisper_server_client.py`: audio goes to the
  server from memory, so no audio file is written at all (the stale-slice cleanup from the previous change is no longer
  needed and is gone), whisper's voice detection is on for each request and off for the lyrics-and-captions second
  pass, and `probe_hearing_run.sh` starts and stops the server beside llama-server (`--model`, `--whisper-threads` and
  `--vad-threshold` now go to it). Measured on the Pi with matched settings the server saves about 15 to 30% per call.
  Found on the way: `verbose_json` with voice detection on crashes the 1.9.5 server when the audio has no speech, so
  timestamps are asked for as SRT. The pipeline also reports a dead server once and tells you how to start one.
- **`trigger_fraction` is now 0.25** (Wes, after a second live session): across the two pasted sessions 0.25 marks 11 of
  43 and 12 of 23 counted sentences (0.10 marked 19 of 43 and 13 of 23). The built-in default moves with it.
- **`trigger_fraction` is now 0.20** (Wes, after watching a live session): in the pasted session, 0.10 marked 19 of 43
  counted sentences as would-trigger and 0.20 marks 13. The built-in default moves with it, so a bad value falls back
  to 0.20.
- **Hearing-debug uses the ConvoInterestTracker instead of a fixed threshold.** Every score goes to the real
  tracker; a sentence that stands out from the room's average interest (by `trigger_fraction`, after `min_scores`)
  is marked `<-- would trigger`, and each line ends with a note: the average and the bar, a warm-up count, or
  `below the floor, ignored`. The `--threshold` option is gone; the startup banner prints the trigger settings.
  Reloading the config restarts the average, and `r` re-runs do not count a sentence twice.
  Every line carries the note, and it shows the average the sentence was judged against and the average after it
  (`interest 2.45 -> 2.73, bar 2.69`); ignored and unscored lines show the unchanged average.
- **Trigger settings renamed and re-based (Wes).** `floor_fraction` is `floor_ignore_fraction` (clearer: the bottom
  fraction of the score range that is ignored). `interest_weight` is replaced by `interest_half_life`, in scores:
  how many more scores it takes for an old score's pull on avg_convo_interest to halve (10, which works out to
  each score moving the average about 6.7% of the way; `weight = 1 - 0.5 ** (1 / half_life)`, shown as
  `ConvoInterestTracker.interest_weight`). `min_scores` now defaults to 10, the same as the half-life, so the
  average has its first full memory before anything can trigger. An old key in a local file warns as unknown.
- **Scorer prompt: the score is one digit, 0 to 9** (Wes). The scale now runs 0 to 9 in five two-digit buckets and
  the model is asked to "Reply with a one digit number and nothing else" (was two decimals from 1.00 to 9.99); the
  debug prompt asks for a single digit too. The old scale and output lines are kept as comments. The score itself
  is unchanged in method: the probability-weighted average of the digits the model might say.
- **The playground script prints scores to three significant figures** (`4.95`, `0.990`), so scores under 1 keep
  their detail.
- **The probability-weighted score stays** (a whole-digit score was tried for an afternoon and reverted at Wes's
  request). Its explanation is now written down in plain words in the lexicon and in `scorer_model_client.py`: the
  score averages the digits the model might say, weighted by how likely it thinks each is.
- **The interest-scorer playground script now uses the Printer's own prompt and reader.** `probe_interest_scorer.py`
  reads its prompt from `config/pom.toml` each run (`--config` picks another file, `--prompt-file` overrides it),
  prints which prompt it used, and reads the digits and wraps each line with the code in `pom.scoring`, so it can
  no longer drift from the Printer. The embedded copy of the old 1.00 to 9.99 prompt is gone (the bake-off's copy is
  saved in `results/bakeoff-2026-10-22/PROMPT.txt`); batch mode keeps its own prompt. Added `suite.txt`, a 20-line
  test suite (Wes's), to run with `--file`.
- **The score is read from one digit, 0 to 9.** The scorer model is asked for a single digit (your prompt), and
  the score is the probability-weighted value of the digits 0 to 9 in that first token, so it runs 0.0 to 9.0.
  The digit 0 now counts (it was ignored, which skewed scores up when the model said 0). The debug view's written
  score is read as a single digit, and a decimal or two-digit answer is no longer a score. Mutation-checked.
- **Config keys follow the lexicon.** `samples_min` and `samples_max` are `exemplars_min` and `exemplars_max`
  (environment: `POM_EXEMPLARS_MIN` and `POM_EXEMPLARS_MAX`, was `POM_SAMPLES_MIN`/`MAX`), `Config.samples` is
  `Config.exemplar_range`, and `[scorer] floor` is `fragment_filter_score` (`ScorerSettings.fragment_filter_score`).
  An old key in a local file now warns as unknown and is ignored. No behavior changed.
- **The `hinge/` package is now `scoring/`** (`src/pom/scoring/`, tests in `tests/scoring/`): it scores
  fragments, and the hinge is only the fragment the trigger will choose. Import paths and module log names
  changed from `pom.hinge...` to `pom.scoring...`. No behavior changed.
- **`Fragmenter` is now `SegmentFragmenter`** (`src/pom/listening/segment_fragmenter.py`; `FragmenterError` is
  `SegmentFragmenterError`), named for what it does: it fragments segments. `Kind` is `FragmentLevel` and
  `Fragment.kind` is `Fragment.level` (sentence, clause or phrase); `Fragment.at` and the `at` argument of
  `split()` and `score_text()` are `heard_at`. Tests moved to `tests/listening/test_segment_fragmenter.py`. No
  behavior changed.
- **Scorer vocabulary now follows the lexicon.** `Source` is `ScoredBy` (values `MODEL`, `FRAGMENT_FILTER`,
  `UNSCORED`; the strings are `model`, `fragment_filter`, `unscored`), `ScoredFragment.source` is `.scored_by`,
  `is_filtered()` is `is_fragment_filtered()`, and the Scorer's `floor` argument is `fragment_filter_score`
  (`DEFAULT_FRAGMENT_FILTER_SCORE`). The `scorer.floor` config key keeps its name until the config keys are
  renamed. The counts and the hearing-debug view say `fragment_filter` where they said `filter`.
- **`InterestClient` is now `ScorerModelClient`** (`src/pom/hinge/scorer_model_client.py`; `InterestClientError` is
  `ScorerModelClientError`). Its debug request is now `explain()` (was `interpret()`; `can_explain`,
  `Explanation`, `explain_prompt`, `explain_timeout`, `EXPLAIN_MAX_TOKENS`), `expected_digit()` is
  `score_from_probabilities()`, its argument is `fragment_text`, and the Scorer's `client` argument is
  `scorer_model_client`. The text sent to the model is unchanged. Tests moved to
  `tests/hinge/test_scorer_model_client.py`. The hearing-debug view now says "explanations".
- **`Sampler` is now `ExemplarSelector`** (`src/pom/corpus/exemplar_selector.py`; `SamplerError` is
  `ExemplarSelectorError`, `sample_range` is `exemplar_range`, its `samples` argument is `exemplar_range`, and
  its default is `DEFAULT_EXEMPLAR_RANGE`). The warning text says "exemplar_range" where it said "samples".
  `Config.samples` and the `samples_min`/`samples_max` keys are unchanged until the config keys are renamed.
  Tests moved to `tests/corpus/test_exemplar_selector.py`.
- **The Scorer no longer guesses a score when the scorer model does not answer.** The word-counting fallback
  (arbitrary, hard-coded numbers, never approved) is moved to `src/pom/hinge/fallback_scorer.py`, unused and
  documented with its weaknesses, so it can be deleted or revived; it has its own tests in
  `tests/hinge/test_fallback_scorer.py`. A fragment the model cannot score now comes back with `score=None`
  and source `UNSCORED` (replacing `FALLBACK`), and `ranked()` puts it last. The hearing-debug view shows it
  as `-`. The circuit breaker is unchanged. Mutation-checked.
- **`Weights` is now `DrawWeights`** (`src/pom/corpus/draw_weights.py`; `WeightsError` is `DrawWeightsError`), and
  the Sampler's `weights` argument is `draw_weights`. The module's log name is now `pom.corpus.draw_weights`. The
  data file stays `corpus/chunks/weights.json`. Tests moved to `tests/corpus/test_draw_weights.py`. No behavior
  changed.
- **`logs.py` is now `log_setup.py`** (`src/pom/log_setup.py`), named for what it does: it sets up logging once
  and hands each module its logger. Tests moved to `tests/test_log_setup.py`. No behavior changed.
- **`WordTools` is now `WordTagger`** (`src/pom/hinge/word_tagger.py`; `analyze()` is `tag()`, `Word.content` is
  `Word.is_content`, `WordToolsError` is `WordTaggerError`), and the Scorer's `words` argument is `word_tagger`.
  Tests moved to `tests/hinge/test_word_tagger.py`. No behavior changed.
- **`Report` is now `FaultRecorder`** (`src/pom/fault_recorder.py`; `Issue` is `Fault`, `strict_report` is
  `strict_fault_recorder`, and the `issues` property is `faults`), named for what it does under the project
  lexicon. Modules keep calling their instance `report`. Tests moved to `tests/test_fault_recorder.py`.
  No behavior changed, except that the cap message now says "faults" where it said "issues".
- **`config/pom.toml` is divided into labeled sections** (GLOBAL, ORACLE, SCORER), one banner per part of
  the Printer the settings affect. Comments only; no setting changed.
- **Scorer prompt: dropped "Most sentences are ordinary and score below 5."** (Wes). That line was an absolute
  expectation about the scale; the scoring prompt now only describes the scale.
- **Scorer prompt split into three parts** in `config/pom.toml`: `prompt_scoring` (what counts as
  interesting and what the numbers mean), `prompt_output` (reply with a bare number) and
  `prompt_output_debug` (reply with the number and the content that earned it). `ScorerSettings` exposes
  `prompt` (scoring plus output) and `prompt_debug` (scoring plus debug output), each joined with a blank
  line. The old single `prompt` key is now an unknown key. A scoring part with no output part is warned
  about. The scoring prompt also gained lines for gossip, mysteries, stakes and secrets. A stray extra
  quote after the debug prompt was removed.
- **Scorer prompt: "a big human feeling" is now "a human feeling"** in `config/pom.toml`. "Big" nudged
  heated lines up a little (on the 100 benchmark sentences "What the fuck?" went 5.44 to 4.76 without
  it, "I love you" 4.81 to 4.31) without changing the ranking much (agreement 0.996). The experiment's
  own copy of the prompt in `experiments/interest-scorer/` is left as it was run.
- **`draw_weights.py` and `chunk.py` now use the `pom` package** for the weights math and the
  weights.json checks, so the package is the single source of truth. Their output and the chunk
  files are unchanged.
- **Source weights moved** from the `weight` column in `corpus/sources.csv` into
  `corpus/chunks/weights.json`. `corpus.py set ID weight=...` now points at the new file.
- **Standardizer rules tightened** after a full sweep of `ready/`: leftover non-ASCII, lowercase
  fragments and wider label patterns are dropped; many sources got tighter trim markers and drop
  patterns.
- **Scripts reorganized by purpose** (`scripts/setup/`, `scripts/dev/`).

### Fixed

- **The design doc and the Pi oracle benchmark's README no longer say the Pi has "a quarter of the Mac's memory
  bandwidth".** That was a wrong guess, and the measured writing speeds say otherwise: 1.9 tokens a second on the Pi
  against 68 on the Mac, a factor of 36. Writing is limited by memory speed, and the Pi is at its ceiling: 1.9 tokens
  a second times the 5.7 GB model is about 11 GB of memory read every second.
- **Hearing-debug no longer leaves audio behind when it is stopped mid-transcription.** Two leftover `tmp*.wav` slices
  of room audio were found in the Pi's `/dev/shm` (removed). The Transcriber now keeps its slices in a folder of its own
  (`hearing-debug-audio`), clears anything a killed run left at startup, and empties it on exit.
- **Hearing-debug passes each sentence to the model as soon as whisper produces it.** The wait for a quiet room
  never ended in a room that is never quiet, so results sat at "waiting" forever. Removed the machinery built around
  it (`--explain-quiet`, the `e` command, the audio quiet-time tracking) and two comparison flags nobody had asked
  for (`--also-score`, `--score-ambient`). The model takes one sentence at a time, in order; `--no-explain` stays
  as the light option.
- **Leaks found in `ready/`:** Greek and Hebrew text, jest and maxim numbers, editors' bracketed
  notes, Douay footnotes, a printer's colophon, indexes, bibliographies, dedications and OCR noise
  in the Wheelwriter manuals.
- **Wrong attribution** in the personality-types note.
- **Hearing-debug: a long explanation could make a score time out.** On the Pi, a model busy writing an
  explanation left a one-token score waiting past its 15 s limit, so it fell back to the word tools. Explanations
  now wait until nothing is being heard or scored, and the tool waits up to 30 s for a score (`--score-timeout`).
- **Hearing-debug: one request per sentence, with the score and the reasons from the same output.** In the
  default debug mode each sentence is run once through `prompt_output_debug` in a quiet moment, and the score (read
  from the first token's probabilities) and the reasons print together; the separate one-token score that was shown
  first is gone (`--also-score` brings it back for comparison, `--no-explain` is fast mode with one-token scores and
  no reasons). `--explain-above` and `--explain-lyrics` were removed, since every sentence is now run. The waiting
  queue holds 40, so a stretch of continuous talk no longer drops sentences. `e` runs the waiting sentences now and
  `r` runs the recent ones again showing the old score.
- **Hearing-debug: whisper's ambient captions are never scored.** Output is sorted into words (speech and song
  lyrics, scored), ambient captions like `(dog barks)` or `(dramatic music)` (shown on their own line, not scored or
  explained, and kept as a short list of recent ambient sounds for the voice's prompt later), and "nothing here"
  markers (dropped). `--score-ambient` restores scoring them; `--explain-sounds` became `--explain-lyrics`.
- **Hearing-debug stops spending the model's time on what is not worth it.** Explanations are made only for
  speech scoring 4.0 or more (`--explain-above`; `--explain-sounds` adds whisper's captions), and quiet is now
  judged from the live audio, not from pipeline events, because a long stretch of talking produced no event and
  explanations started in the middle of it, stalling whisper for 12 to 24 s. Whisper's `[silence]`, `(silence)`,
  `[BLANK_AUDIO]` and `[no audio]` markers are dropped before scoring, and the sound-only second pass is skipped
  when whisper has already called the audio silent. The written reply is capped at 80 tokens, down from 120.
- **Hearing-debug: explanations are one run with the score, and no longer compete with listening.** The debug
  request now returns the model's written reply and the score read from the probabilities of that same output's
  first token (`InterestClient.interpret` returns an `Interpretation`), so the number and the reasons belong
  together; the Printer's own one-token score is shown beside them. On the Pi, explanations measured 1.25 tokens a
  second while listening and scoring, so they now start only after 3 s of quiet (`--explain-quiet`) and an `e`
  command explains the last few sentences on demand, including after a recording has played.
- **Prompt edits reach the Pi through git, not by copying.** `probe_hearing_tune.sh` (Mac) keeps a second
  worktree of a throwaway `prompt-tuning` branch; on each save it checks the config loads with no problems
  (a typo is not sent), commits it there and pushes, never switching your checkout off `main`. On the Pi,
  `probe_hearing_run.sh` follows that branch with `git fetch` into a scratch worktree (`probe_hearing_follow_tuning.sh`),
  leaving the main clone untouched. `stop` deletes the branch; the Pi's runner removes its scratch checkout and the branch's tracking ref on exit. An earlier file-copy version was replaced.
- **Hearing-debug uses whisper's own voice detection.** `whisper-cli --vad` (a Silero model) now decides what
  is speech, instead of a threshold of mine; the energy cut is only a chunker for the live stream. On the quiet
  class recording it replaced a drift into invented text ("You", "I'm sorry") with an accurate transcript that
  stops where the speech stops. The VAD ignores singing and most sounds, so when it finds no speech a second pass
  runs without it and keeps only what whisper itself marks as non-speech (lyrics between music symbols and
  captions like "(dog barks)"), labeled "sound, not speech"; plain text from that pass is dropped as likely
  invented. `setup-whisper.sh` now fetches the VAD model. A bug fixed on the way: the "nothing heard in 30 s"
  message printed after every utterance.
- **Hearing-debug no longer measures the room's noise floor once at startup.** That failed when it was
  started in a room that was already loud. The floor is now re-estimated continuously as a low percentile of the
  last 30 s of levels, so it works from the first frame whatever the room is doing. On the attic clip (music from
  the start) it finds 8 sensible utterances where the old version found 2.
- **Hearing-debug now explains silence.** When nothing has been heard for 30 s it says why (the room's noise
  floor, the speech threshold and the loudest sound of the last 5 s), and an `l` command shows the live levels.
  It also said nothing when the noise floor had been measured with a podcast already playing, which put the
  speech threshold above a quiet podcast's voice.
- **Hearing-debug's config sync** now says plainly that it runs on the Mac when it is started on the Pi.
- **Pi benchmark whisper logs** were named by phase alone, so the second Scorer model's overwrote the
  first's; the name now includes the model. The report also shows memory used against the pre-start
  baseline, since the server's resident size counts file-backed model pages.
- **Pi benchmark readiness check** always said no whisper was running, because `pgrep` rejected the
  long process-name pattern; it now tests `whisper-cli` and `whisper-server` separately.

### Security

- **Repository made public** on 2026-10-21 at the owner's decision, including the owner-cleared
  Wheelwriter manuals and dad jokes, which `corpus.py publish-check` flags by design.

[Unreleased]: https://github.com/wmodes/pom/commits/main
