# Design Audit 04: the big design problems

*2026-11-26, a read-only audit of the whole source, tests and dev scripts against the design philosophy in `~/.claude/CLAUDE.md` and
this project's CLAUDE.md.* Audit 03 listed small things out of place. This one asks a different question: **which patterns or
structural decisions go against the design philosophy across the whole Printer?** An item here is the size of "every module set up its
logger a different way", not one stray constant.

Four reviewers each read the source against one part of the philosophy:
1. responsibilities and structure;
2. boundaries and shared facts;
3. robustness and threads;
4. reuse, conventions and tests.

They did not read audits 01 to 03, so their read started fresh. Claude then checked the findings in the code. **Checked** means Claude
saw it in the code; **reported** means only a reviewer did.

How to work it: one item at a time, with Wes's go on each. Each plan says how the item will be solved, and lists every choice that
touches the system (names, settings, defaults) with one recommendation. Tick the box with the date and the commit. New findings go at
the bottom with the next number; nothing is renumbered.

## The checklist

- [x] **1. The Printer has two clocks** — **done 2026-11-26**: one `Timekeeper` (`src/pom/timekeeper/`) keeps stream time and every part is handed its `now`; before the first room it runs on real seconds from the Printer's start (see CHANGELOG). Original finding (checked). *Principle:* "When several parts must agree on a derived fact … build it once …
  and hand that same object to every part."
  - **The fact:** stream time is the timeline the bar, the cooldown, the chart and the typed lines must all share.
  - **Who owns it:** no object. It is pieced together by helper functions inside `build_printer`:
    - `make_room` carries the clock from one room to the next (`printer.py:205-214`);
    - `stream_now` reads whichever room is current (`printer.py:254-257`);
    - the Conductor keeps its own copy of the current room's ears (`conductor.py:96`).
  - **The second clock:** typed lines are timed by `time.monotonic() - started`, from when the Printer started (`printer.py:248`).
    TypedLineScorer's own docstring promises "the same timeline as the Eavesdropper's". The room is timed from when listening started.
  - **Consequence:**
    - The two clocks drift apart at boot by however long the microphone took.
    - In a `--wav --speed N` replay they drift by a factor of N.
    - Typed dots land in the wrong place on the chart.
    - The BarKeeper holds time at the latest time it has seen, so a typed line stamped ahead can hold the cooldown and the bar's
      fading still. That is the very failure the Eavesdropper docstring was written to prevent.
  - **Other timelines:** TurnHistory and MonitorSite add wall-clock time too, and every one travels as a bare float.

- [x] **2. The Printer's wiring is one big function, so nothing can reuse it, and the testbenches copy it and have broken** — **done 2026-11-26/27**: the `PrinterBuilder` (`src/pom/printer_builder.py`) builds each part once, when first asked; the Printer, the testbenches and the tests all build through it; the two broken testbenches run again; `tests/test_printer.py` reaches 47 private attributes, down from 109 (see CHANGELOG). Left for item 7: the tally and the page's status still live in the builder, as named methods. Original finding (checked).
  *Principles:* "One class, one job"; "the entry point reads the config and builds the parts"; hand-poking tools "use the real
  modules, never copies".
  - **The function:** `build_printer` (`printer.py:161-291`, about 130 lines) holds state and makes decisions in helpers nested
    inside it:
    - a live tally of attempts (`snapshot`);
    - the stream clock (`last_room`, item 1);
    - the monitor's status (`status_now`);
    - which status lights are expected;
    - derived settings such as `check_seconds + 1.0`.
  - **The Printer it builds:** `Printer` takes 11 arguments plus a `parts` dict looked up by string.
  - **No dev script calls it.** Each wires the parts again by hand:
    - the scorer chain (ScorerModelClient + WordTagger + ClipScorer) is built in 5 places;
    - the library in 3;
    - `check_seconds + 1.0` in 2.
  - **Breakage found:**
    - `scripts/dev/pipeline/listening_to_portent.py:166` builds `MonitorStatus(sentences_heard=…)`, a field that no longer exists.
      The page catches the error, so with `--monitor` the status tiles go blank without a word.
    - `scripts/dev/library/circulation.py:38` builds a Bibliomancer without the required `oracle`, so it crashes when run.
    - The whole-pipeline testbench has no Conductor, FloorKeeper or TurnTaker, so pause and skip-ahead are never tested.
    - Its chart notebook has no ticks or silence shape, so its chart differs from the Printer's.
  - **Tests:** `tests/test_printer.py` holds 72 of the suite's 162 reaches into private attributes (44%), including
    `p.conductor._room_factory()` 19 times, and 37 of its 52 monkeypatches. The smoke tests of the scripts run only `--help`, so none
    of the breakage is caught.
  - **Consequence:** the testbench Wes judges the system by is not the system.

- [ ] **3. The checks on the values passed between stages disagree, and are copied** (checked). *Principle (agreed with Wes,
  2026-11-27):* "Every public method checks what it receives against its own spec. A bad value gets the one fallback value agreed for that
  kind of value (a score, a stream time, a count) and a warning, so the log shows which caller to correct; `strict` raises instead. The check
  is one shared function, not a copy per module."
  - **Why not the global rule as written.** The global CLAUDE.md says "Make bad states unrepresentable. Clamp and validate in the value
    object; guards elsewhere then become dead code." The first half stands. The second does not: Python cannot make every caller go through
    the value object, and any public method can be reused by a caller that hands it a raw float or a NaN. A method's own check is its
    boundary, never dead code.
  - **Progress:** step 1 done 2026-11-27: `value_rules.py` (was `setting_rules.py`) and the `ValueChecker`, with the nearest good value
    for a number past its range; no receiver uses them yet. Step 2 done 2026-11-27: the 11 copies of `_is_number` are one `is_number`
    in `value_rules.py`, guarded by a test. Next: each kind of value gets its
    fallback value, chosen with Wes by reading how it is used, and each receiver checks with the ValueChecker.
  - **Unchecked values:** `Attempt` and the other values handed from stage to stage are frozen but check nothing when they are made.
  - **The same score and time are checked by these, each with its own fallback value:**
    - the BarKeeper (`bar_keeper.py:164, 191`);
    - each bar under it (`percentile_bar.py:87`, `decaying_quantile_bar.py:146`);
    - TurnHistory (`turn_history.py:118-124`);
    - TurnTaker, with `getattr(attempt, "score", None)` on its own Attempt type (`turn_taker.py:158`).
  - **Each layer falls back differently:** 0.0 in one place, None in another, the last time seen in the BarKeeper. So one bad value
    degrades a different way at each step, and many replacements are silent (`turn_history.py:123` turns a bad `heard_at` into 0.0).
  - **The number check:** `_is_number` is copied into 12 modules, written inline in 2 more, and `setting_rules.py` has one with the
    same name that means something else (it does not check for infinity or NaN).
  - **Handed-in parts:** about 15 constructors each write their own check that a clock or a sleep can be called; it should be one shared
    check.

- [ ] **4. Failures hide bugs** (checked). *Principle:* "A failure inside [a helper] is recorded as a warning … a `strict` flag raises
  instead, for tests and dev tools."
  - **No tracebacks:** no log line in the code ever includes one (0 uses of `exc_info` or `logger.exception`). 43 lines log only the
    error's type name, for example "the room failed (TypeError)" (`conductor.py:223`). A real bug on a Printer running for days shows up
    as one class name, and then the LogMuter mutes it as a repeat. Nobody can find the line it came from.
  - **One handler for everything:** the catch-alls treat an expected network error and a programming error the same way.
  - **Strict is not connected:** `config.strict` (`POM_STRICT`) is read, validated and tested, but `build_printer` never hands it to
    any part. 46 constructors each take their own `strict=`, and some parts (HingeFinder, ScorerWarmer, BootSlipper, AddressFinder,
    Printer) take none. The Bookkeeper already hands out every logger, which makes it the natural single place for strict.

- [ ] **5. The three model servers are talked to in about eight ways** (checked). *Principles:* "Prefer well-maintained,
  purpose-built tools over hand-rolled workarounds"; the config hard rule.
  - **Sending:** three hand-written `_http_post` functions. The oracle's and the scorer's are identical; whisper's adds a hand-rolled
    multipart body.
  - **Is it up?** Asked six ways:
    - `printer.py:85` and `printer.py:101`, `timeout=3`;
    - `WhisperModelClient.ping`, `PING_TIMEOUT=5`;
    - copies in `listening_to_portent.py`, which catch different errors;
    - `run_printer_with_servers.py:64`, `timeout=2`;
    - `write_portent_from_hinge.py`.
  - **Addresses:** the clients own their URLs, yet `printer.py` works out `/health` by taking them apart.
  - **Each server fails differently:** the scorer has a circuit breaker, the oracle retries in its caller, whisper has nothing.
  - **Consequence:** three timeouts outside the config, and a change to how a server error is handled must be made in about six
    places.

- [ ] **6. Nothing notices a thread that hangs** (reported; medium confidence). *Principle:* "A helper must never stop the main work."
  - **Watched:** the Conductor checks only that its own three threads are alive (`conductor.py:139-141, 172`).
  - **Unwatched:** five other threads have no watcher: the ServicePoller, the MonitorSite, the mic reader, the ScorerWarmer and the
    BootSlipper.
  - **The traced path:**
    1. The floor watchdog takes the floor back from a hung turn, but cannot free the stuck thread.
    2. New hinges keep taking the floor and pile up in a queue with no limit (`conductor.py:98, 264`).
    3. When the stuck thread wakes, `take_turn` never checks that its turn still holds the floor.
    4. Freshness is counted from when the turn left the queue, not from when the hinge was heard (`turn_taker.py:153, 229`).
    5. A lost floor prints anyway (`turn_taker.py:188-189`).
    6. So stale portents can be typed while the Printer is listening, which breaks half-duplex.
  - **Lifecycles:** thread start and stop are hand-written six times, each with its own join wait (5, 5, 5, 2, 1 and 1 s), none in
    config.

- [ ] **7. The monitor is fed five ways and knows how the bar works inside** (reported; medium-high confidence). *Principles:* "a
  display reads only its own data store, never the source behind it"; "a class that must know how another class draws, stores or
  derives things".
  - **The five feeds:**
    1. TurnTaker writes TurnHistory directly (`turn_taker.py:221`).
    2. The Conductor's `on_attempt` helper feeds the ChartNotebook (`printer.py:259-263`).
    3. The ChartNotebook calls back into the BarKeeper for ticks while the page is drawn (`chart_notebook.py:168-172`).
    4. MonitorSite pulls the live bar through `status_now`.
    5. The decks and the ServicePoller post to the StatusBoard.
  - **Knowledge running both ways:**
    - `Bar` carries a method that exists only for the chart (`bar.py:45`).
    - The notebook's tick design is built around when the bar forgets its scores (`chart_notebook.py:11-14`).
    - The chart's facts are worked out more than once: the silence shape is built for the notebook and again on every draw
      (`printer.py:232`, `score_chart.py:87`); the chart's time window is applied in three places.
  - **Consequence:** changing a bar, the chart or a turn means touching code in other layers.

- [ ] **8. Shared small types live in the modules of the classes that make them** (reported; medium confidence). *Principle:* "Shared
  small types go in their own module so two classes can use them without importing each other."
  - **Examples:**
    - `ScoredBy` lives in `scoring/clip_scorer.py`;
    - `Ruling` in `qualifying/bar_keeper.py`;
    - `Clip` and `ClipLevel` in `listening/take_cutter.py`;
    - `Portent` in `writing/portent_writer.py`, imported by the monitor;
    - `Passage` in `library/catalog.py`.
  - **Consequence:** `Attempt`, which every later stage uses, pulls in the BarKeeper, the ClipScorer, the TakeCutter and the
    WordTagger. There is no import cycle yet, but the dependencies point sideways.
  - **The model to follow:** `notebook_entry.py` and `audio_format.py` already do it the right way.

- [ ] **9. The score scale has no owner** (reported; medium confidence). *Principle:* "One source of truth."
  - **Where 0 to 9 appears:**
    - the scorer's digit check (`scorer_model_client.py:93`);
    - `[chart] score_max` and `score_gridlines`, which can be set apart from the scorer;
    - MovingAverageBar's `DEFAULT_SCORE_RANGE`.

- [ ] **10. Start-up and packaging don't follow the established patterns** (checked). *Principle:* "Build on [smarter people's] work
  (established patterns, libraries …) rather than reinventing it."
  - **Two start-up designs:** `src/pom/startup.py` (191 lines, with tests) is a stub step-runner that nothing in `src` or `scripts`
    uses. The real start-up is the health checks in `printer.py` plus the ScorerWarmer and the ServicePoller.
  - **No package install:** there is none, so 21 scripts and tests edit `sys.path` by hand, each working out its own root.
    `pip install -e .` with entry points is the standard way.
  - **Scattered parts:**
    - `conductor/` holds things that are not conducting: the AddressFinder, the BootSlipper and the KeyboardWatcher.
    - The keyboard is spread over three packages: line editing in `printing/terminal_port.py`, polling in `conductor/`, scoring in
      `keyboard/`.

- [ ] **11. Nothing shows that the test suite tests the right things** (raised by Wes, 2026-11-27, after item 2). *Principles:* "tests go
  through the public interface"; "If a test fails, the code changed behavior: fix the code, never weaken the test."
  - **The question:** with this much refactoring, how do we know the tests guard what the Printer should do, and not just what the code
    happens to do? Nothing we have answers it:
    - **Mutation checks test the tests, not whether they are good tests.** A killed mutant shows a test notices that line changing; it
      does not show the test checks the right thing. And Claude picks the lines.
    - **The old tests can be wrong too.** A test written by reading the code checks that the code does what it does. In item 2, 62 tests
      in `tests/test_printer.py` were rewritten in the same steps as the code they check.
    - **Real runs test the ordinary cases**, not the edges (the boundaries, the failures, the timing).
    - **A recording run would compare before and after**, but the Printer is not deterministic (see the last point).
  - **The review, one package at a time:**
    1. **Each test names the rule it guards**, from the Design Document, a config comment, the Lexicon, or a bug that was seen. A test that
       cannot name one checks the code's insides, not what it should do: rewrite it against a rule, or delete it.
    2. **Wes reviews the rules, not the code.** The test names and docstrings of a package, read as a list, should read as its spec. A rule
       Wes cares about that is missing from the list is untested. Claude cannot do this step alone: it would be checking its own reading
       of the code against its own reading of the code.
    3. **The edges come from each rule**, written down beside it: the boundaries (a score exactly at the bar, the first attempt, an empty
       room), the failures (a server down, a port gone, the cases in `Design Audit 01.md`), and the timing (a turn ending during a check).
       Then each edge gets a test.
    4. **The private reaches go** (47 in `tests/test_printer.py`, and the 18 of 66 test files that reach any): each is a sign the part's
       public interface is missing something.
  - **Then, maybe, a recording run as a regression test.** Its randomness comes from four places, and three can be pinned: whisper's
    text and the scorer's scores saved once from a real recording and replayed (no server), and the library draw on a seeded generator
    (the portent's text is not checked, only that a turn happened). The fourth, the threads (the Conductor's checks and the turns), can
    interleave differently from run to run, so the run would check what qualified and in what order, not the exact second. Untried;
    medium confidence.
  - **Where to start:** one package, to see whether the review is worth its cost before doing the rest.

- [ ] **12. The earlier audits' corrections were never all finished, and their boxes no longer say what is done** (raised by Wes,
  2026-11-27). *Principle:* "One source of truth": an audit is the list of what is left, and a list that is out of date misleads.
  - **What is open** (counted 2026-11-27):
    - `Design Audit 01.md` (Robust Testing) has no boxes; its table marks 10 rows "not tried".
    - `Design Audit 02.md` (Hardening TODO): 31 open boxes, 6 ticked (H2 to H29, and the smaller items at the end).
    - `Design Audit 03.md` (D1 to D12): 48 open boxes, 15 ticked.
  - **Some are done and not ticked.** For example, D1.1 (`listening_to_portent.py --monitor` shows a blank page), D9.1 (the wiring rebuilt by
    hand in the dev scripts) and part of D2.5 (the dead constants `PASSAGES` and `NLTK_DATA`) were all fixed by item 2 of this audit.
  - **Some are the same problem as an item here.** For example, D9.2 and D9.3 (each model client has its own HTTP code; two ways to ask
    whether a server is up) are item 5, and D12.2 (shared small types in the wrong module) is item 8.
  - **The reconciliation:** each open box in 01 to 03 gets one of four marks, with Claude checking the code for each:
    1. **done:** ticked, with the date and the commit that did it;
    2. **part of item N here:** ticked over to that item, which gains anything it was missing;
    3. **still open:** stays, with its line numbers checked against today's code;
    4. **drop:** Wes's call only, with the reason written beside it.
  - **Afterwards** this audit is the one list of what is left, and 01 to 03 are records.

## What the reviewers found sound

- **Injection and imports:** parts are handed in, not reached for; no module outside `config.py` reads the config; the imports run one
  way with no cycles.
- **Settings:** the settings hand-off (one `defaults.py`, each class handed its section) and its validation.
- **Clocks:** handed in as `clock=` consistently.
- **Lifecycles:** `start`/`stop` for threads and `close` for resources, across the code.
- **The conductor and the bar:** the Conductor's floor logic, the Turn as a context manager, and the BarKeeper/Bar split.
- **Shared state:** the classes built to hold it (FloorKeeper, BarKeeper, TurnHistory, ChartNotebook, StatusBoard, LogMuter) each have
  one lock and return copies.
- **The serial port:** uses pyserial's own flow control.
- **Command lines:** use argparse.
- **Tests:** each class has its own test file, and 48 of 66 test files reach no private attribute.
