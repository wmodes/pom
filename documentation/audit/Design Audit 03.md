# Design Audit 03

*2026-11-26, a read-only audit of the whole source, tests and dev scripts against the design principles in `~/.claude/CLAUDE.md` and this project's CLAUDE.md.* Five reviewers read one area each (wiring and config; listening and printing; conductor, bar and keyboard; scoring, writing and library; monitor, tests and dev scripts). Claude then checked the findings marked **verified** in the code by hand; **reported** means only a reviewer read it. Nothing already on [Design Audit 02.md](Design%20Audit%2002.md) (the Hardening TODO) is repeated here except as a cross-reference (H-numbers). Nothing was changed.

The findings fall into a few patterns that recur in every part of the system, so they are grouped by pattern, not by module. Each pattern names its instances.

## How we work through it

Like the Hardening TODO: one item at a time, Wes's go on each, the plan says how it will be solved and lists every choice that touches the system (names, settings, defaults) with one recommendation, then test first, the change, mutation checks, CHANGELOG, lexicon and design docs. Tick the box with the date and commit. Add new findings at the bottom of the right group with the next number; do not renumber.

## Suggested order

1. **Broken or misleading now** (D1, D2): a testbench that silently shows nothing, and settings that do nothing.
2. **Leftovers of the logging work** (D3): small, mine, and they teach the old way.
3. **Hidden design decisions for Wes to make** (D4): each is a question, not a fix.
4. **The one big structural pattern** (D5): defaults and checks written twice. Fixing it the right way (each class takes its frozen settings object from config) also fixes most of D6 and much of D11.
5. The rest, in any order.

---

## D1. Broken now

- [ ] **D1.1 `listening_to_portent.py --monitor` shows a blank monitor** (verified). `scripts/dev/pipeline/listening_to_portent.py:170` builds `MonitorStatus(sentences_heard=…, sentences_scored=…)`; the fields are now `attempts_heard`/`attempts_scored`. The `TypeError` is caught by `MonitorSite._status`, so the page shows no bar, no window and no lights, with no error. `test_testbench.py` only runs `--help` and failure paths. The root cause is D9.1 (the testbench rebuilds the Printer's monitor wiring by hand).

## D2. Settings and state that do nothing (no reader)

A setting that changes nothing misleads whoever tunes it; kept state with no reader is code nobody can trust.

- [ ] **D2.1 `[listening] room_tone_memory` does nothing** (verified). Its comment says it is "to tell the oracle model what the room sounds like"; `Eavesdropper.recent_room_tone` (`eavesdropper.py:135,157,245`) has no reader anywhere. Wire it into the oracle prompt, or remove the setting. *A decision for Wes (see D4).*
- [x] **D2.2 The `strict` setting has no reader** — **done 2026-11-27** in Design Audit 04 item 4: the entry point builds the Bookkeeper with it. (verified). `config.py` reads it (and `POM_STRICT`); nothing passes it to any class; the docstring (`config.py:74`) says the entry point does.
- [ ] **D2.3 The scorer's `explain` machinery is never called** (verified). `explain()`, `Explanation`, `parse_written_score`, `can_explain`, `explain_timeout`, `EXPLAIN_MAX_TOKENS` (`scorer_model_client.py`) have no caller outside tests, yet `printer.py:175` and four dev scripts pass `prompt_debug` to it.
- [ ] **D2.4 Write-only values** (verified unless marked):
  - `RollingCutter.took`, `.forced` (`rolling_cutter.py:119-120`); the injected `clock` exists only for `took`; `vad` is never passed (always True).
  - `MicDeck.dropped_frames` (`mic_deck.py:72,144`). It is the reader H4 needs, so keep it and give it that reader.
  - `Attempt.counted` and `BarKeeper.counted` (`hinge_finder.py:80,84`; nothing reads `attempt.counted`).
  - `Circulation._strict` (`circulation.py:70`).
  - `WordTagger`: `lemma` (called "the slow step"; its cache grows without limit), `wordnet_pos`, `lower`, and `content_words()`, which is never called while `clip_scorer.py:143,156` writes the same filter twice (reported).
  - `ClipScorer` counts, `counted()`, `ranked()`, `score_many()`, `degraded`; `Bibliomancer` draw counts, `reset_counts`, `probability`: read only by tests and one dev script (reported).
  - `FloorKeeper.overruns`/`turns_taken` (the Conductor counts turns too), `TurnTaker.outcomes`, `TurnResult.write_attempts/print_tries/record_id`, `BootSlipper.typed/text`, `Clip.heard_at` (its docstring promises a use that never came), `TakeCutter.max_words`, `WhisperModelClient.available`, `TerminalPort.available` (reported).
  - `ChartNotebook.head_ticks()` has no caller (verified); `latest_at` is read only by tests.
- [ ] **D2.5 Dead constants** (verified). `printer.py:56-58` `PASSAGES`, `NLTK_DATA`, `DEFAULT_SERVICE_CHECK_SECONDS` (the same paths are rebuilt inline at `:174`, `:222`); `address_finder.py:27` `LOOPBACK_ADDRESS` (duplicates `boot_slipper.py:46`, reported).
- [ ] **D2.6 The monitor stores each turn's outcome and never shows it** (verified). `TurnHistory.set_outcome` keeps it; neither page nor `/api/turns` reads it; the Lexicon says it is "shown beside it on the monitoring site". Show it, or stop keeping it. *A decision for Wes (see D4).*
- [ ] **D2.7 The monitor's JSON feed sends every hinge, portent and exemplar on every poll; the page reads only `stats` and `turns[0].id`** (reported; `monitor_site.py:513-529`, `194-200`). Slim it, or name its outside reader.

## D3. Leftovers of the logging work (Claude's, 2026-11-25/26)

- [x] **D3.1 Leftover module loggers** — **done 2026-11-26**: the four unused ones removed (3929a26); the LogMuter's own failure line now goes to stderr as Python's logging does (a0d01b7). The werkzeug request line in `monitor_site.py` remains (section E of the Hardening TODO).
- [x] **D3.2 `log_setup.py`'s docstring still teaches the old way** — **fixed 2026-11-26 (0abbb1b)**; the package no longer exports the two functions. (verified): "they ask `get_logger` for a logger", the per-module `_log` line, and "call `configure_logging()` twice". `bookkeeper/__init__.py` still exports `get_logger` and `configure_logging`.
- [x] **D3.3 `Catalog` and `Circulation` had a second way to get a logger** — **fixed 2026-11-26 (262034c)**: no `logger=` argument and no `_new_logger`; Circulation reads its document itself; its unread `_strict` went too (D2.4).
- [x] **D3.4 The dev scripts set up the log their own way** — **won't do (Wes, 2026-11-26)**: dev scripts are not the Printer and need not use the Bookkeeper ("they are of the Varda and live in Valinor; different rules"). They keep calling `configure_logging` from `log_setup`.
- [x] **D3.5 Two styles of log line** — **done 2026-11-26**: 119 lines (bbb7ddc) and the last nine, through helpers (cf6e94a); DeviceFinder's two pass-through helpers removed; a test keeps every message in `%` style.
- [x] **D3.6 `device_finder.py` docstrings describe what is gone** — **fixed 2026-11-26 (0bf3466)**, with the same old wording in 31 other files. (reported): "the count of tries needs no lock" (`:24`) with no count; lines "kept for reading" (`:67`, `:108`) with nothing kept.
- [x] **D3.7 Failing callbacks are dropped without a word** — **fixed 2026-11-26 (524ca94)**. (verified): `mic_deck.py:131` (`heard`) and `terminal_port.py:347` (`still_alive`) do `except Exception: pass`. The rule is "a helper never stops the main work; its failure is a warning". The LogMuter now handles the repeats.
- [x] **D3.8 Hand-made muting rules** — **done 2026-11-26 (a6df4d9)**: the TurnTaker's every-sixth-try, the Bibliomancer's warn-once and the terminal's once-a-minute reminder (with its setting `wait_log_reminder_seconds`) are gone; the LogMuter mutes repeats. Kept as information, not muting: the "(N so far)" counts and the CRITICAL after three failed turns in a row (its `3` is D6.1).
- [x] **D3.9 Tests read the log two ways** — **fixed 2026-11-26**: `logged` catches every level from the start and has `debugs()`, `messages()`, `text()`; every test reads through it (about 100 reads in 32 files moved); only the bookkeeper's own tests read `caplog`, and a test keeps it so.

## D4. Hidden design decisions (questions for Wes, not fixes)

Each of these is behaviour nobody approved or that a code edit alone can change.

- [ ] **D4.1 Only sentences are scored; clauses and phrases are cut and thrown away** (verified). `hinge_finder.py:73` `if clip.level is ClipLevel.SENTENCE`. `TakeCutter` still cuts, de-duplicates and windows clauses and phrases. The Design Document says they "remain available behind a setting"; there is no setting. `pom.toml`'s `max_clip_words` comment still says "sentences, clauses and phrases to be scored". Decide: a setting, or cut sentences only.
- [ ] **D4.2 `room_tone_memory`** (D2.1): wire it to the oracle, or drop it.
- [ ] **D4.3 Turn outcomes on the monitor** (D2.6): show them, or drop them.
- [ ] **D4.4 The boot slip takes the floor a different way from a hinge** (reported). The slip takes the floor straight from the FloorKeeper and then sleeps `check_seconds + 1.0` (the hidden `1.0`), guessing when the Conductor will next shut the ears; a hinge's turn shuts them at once. One way to take the floor would remove the guess and the magic number.

## D5. Defaults and checks written twice (the biggest pattern)

**The agreed design (Wes, 2026-11-26)**, from the well-known pattern (Django's `global_settings.py`, pydantic-settings' settings model, Hydra's structured configs): one central, typed config schema; the file is read over it; each part is handed what it needs.

- **`src/pom/defaults.py`**: the shadow of `config/pom.toml`, not a user-serviceable part. Every section as a frozen dataclass (`ConductorSettings`, ...), each setting written once with its default, its check and a short comment. No `DEFAULT_...` shadow names: the field's name is the setting's name, and `ConductorSettings().freshness_seconds` is its default. Organised by `pom.toml`'s sections, not by packages (they do not line up, and nothing assumes they do).
- **`src/pom/config.py`**: only the reader. It lays `pom.toml` and `pom.local.toml` over `defaults.py` and warns for any key that is missing (D5.8), unknown or bad, naming the key and the default used. Then it builds `Config`.
- **`printer.py`**: hands each class the section object(s) it reads. One rule: a class that needs values from two sections is handed both objects (`TurnTaker(..., conductor, oracle)`); its constructor names them, so its needs are visible.
- **Classes** hold no defaults and check nothing; they read `self.<section>.<setting>`. A test builds only what it changes: `ConductorSettings(freshness_seconds=5)`.
- **A test** keeps `defaults.py` and the shipped `pom.toml` to exactly the same keys.
- Rejected: per-package `defaults.py` (sections do not match packages; a setting used by two packages would have two defaults or live in the wrong folder); a shadow `defaults.toml` (another file to read and keep in step; `pom.toml` already is the complete shipped file); pydantic-settings (it raises on a bad value; our rule is the default with a warning, so it would mean fighting the library).
- Steps: (1) `defaults.py` and the reader, behaviour the same apart from the new missing-key warnings; (2) one class at a time onto its section object(s), its copies and checks deleted; (3) a guard test: no `DEFAULT_` constant and no `_is_number` outside `defaults.py`/`config.py`.
- **Done 2026-11-26** (c2f8d80 to 78da5ed): all three steps. Step 3 as built: `tests/test_setting_guards.py` fails on a `DEFAULT_` name outside `defaults.py`/`config.py` unless it is on a short list of names that are not pom.toml settings, each with its reason (the list may only shrink), and on any `self.<section>.<setting>` or `config.<section>.<setting>` that `pom.defaults` does not declare. No guard on `_is_number`: every copy left checks live data (a score, a time, a stream start), not a setting; open for Wes.

The rule: config.py is the one place a setting is defaulted and validated; classes trust the frozen settings they are handed. In practice nearly every class keeps its own copy of the defaults and checks the values again.

- [x] **D5.1 Classes repeat config's defaults** — **fixed 2026-11-26** (D5 step 2): every class is handed its section object(s); its copies are gone, and a guard test keeps them gone. (verified): `DEFAULT_*` constants in 31 modules outside config.py. They already drift: `floor_keeper.py:75` says the oracle timeout is "120 s now"; it is 240. `DEFAULT_STARTING_BAR = 6.8` is written four times (config and all three bars). The typed-line bar influence falls back to 0 in four places.
- [x] **D5.2 Classes re-validate what config validated** — **fixed 2026-11-26** (D5 step 2): the classes' checks of settings are gone; the `_is_number` copies that remain check live data, not settings. (verified): `_is_number` is copied into 15 modules (the whisper copy has no `isfinite`). Examples: `terminal_port.py:74-93`, `rolling_cutter.py:44-47`, `eavesdropper.py:62-64`, `clip_scorer.py:111-137`, `oracle_model_client.py:79-100`, `bibliomancer.py:119-144`, `monitor_site.py:311-322`, `turn_history.py:392-399`, `chart_notebook.py:82-92`. `capture_card_finder.py:53` states the rule correctly; the others contradict it.
- [x] **D5.3 Inside config.py, each setting is written about five times** — **fixed 2026-11-26 (c2f8d80)**: `defaults.py` declares each once; `config.py` walks the fields. (reported): in `_KEYS`, a `DEFAULT_` constant, a dataclass field, the docstring, and `load()` (key and default twice each); section objects are built by position (`ScorerSettings(scorer_url, …)`), so two numbers can swap silently. Two checkers for one rule (`_count` and `_whole(…,1)`; `_number(…,0.0)` and `_not_negative`). `Config` itself is a mutable 20-argument class and `load` is about 300 lines. *Direction:* each field carries its checker (`dataclasses.field(metadata=…)`), `load` walks `dataclasses.fields()`, `_KEYS` is derived from them. The standard library's own extension point; no new dependency.
- [x] **D5.4 Settings types copied** — **fixed 2026-11-26 (df776a4)**: `ChartLook` is gone; the chart is drawn from `ChartSettings`. (reported): `ChartLook` (`score_chart.py:38-56`) repeats `ChartSettings` field for field.
- [x] **D5.5 Which bars exist is defined three times** — **fixed 2026-11-26 (21a4613)**: the BarKeeper requires its bar; the names and the default are `BAR_ALGORITHMS` in `pom.defaults`, the factory's `ALGORITHMS` maps them to classes, and a test keeps the two the same. (verified): `config.py:136-137` (`BAR_ALGORITHMS`, default), `bar_keeper_factory.py:25-30` (`ALGORITHMS`, default), and `bar_keeper.py:93` builds a `PercentileBar` itself when none is handed in. One registry; the BarKeeper requires its bar.
- [ ] **D5.6 Settings split across sections and renamed** (reported): `[oracle] exemplars_min/max` and `exemplar_redraws` configure the Library, arrive as `config.exemplar_range` (not on `config.oracle`) and `config.oracle.exemplar_redraws`, and the code calls it `redraw_limit`.
- [ ] **D5.7 Environment overrides follow no rule** (reported): 8 keys can be overridden (`POM_SCORER_URL`, `POM_WHISPER_URL`, not the oracle's); whisper's is read mid-`load`; `pom.toml:3-4` doesn't list `POM_WHISPER_URL`.
- [x] **D5.8 A setting missing from `pom.toml` falls back to its default without a word** — **fixed 2026-11-26**: each unset setting is a warning when a file was read; a missing pom.toml is one warning. (verified 2026-11-26; asked by Wes). `config.py` warns about an unknown key and a bad value, but a missing key quietly takes the default, so a line cut from one section and pasted into another loses its value silently (the new spot warns as unknown; the old spot says nothing). The global rule says a default in code "is only the fallback for a missing or bad value, and it warns". *Direction:* every missing key is a warning naming the key and the default used; one test, worked out from the settings objects, checks the shipped `pom.toml` has exactly every key, none missing and none extra.

## D6. Hidden values still in code (the config rule; not in H27)

- [ ] **D6.1 Timings** (verified unless marked): `keyboard_watcher.py:32-33` poll and error pause (`printer.py` never passes them, so the constants are the only source); `turn_taker.py:65-67` `BEAT_SECONDS`, `WRITE_FAILURES_THAT_ALARM`, `PRINT_TRIES_BETWEEN_LOG_LINES`; `conductor.py:51` `JOIN_SECONDS`, `service_poller.py:25` `JOIN_SECONDS`, `monitor_site.py:364` `join(timeout=5)`; `floor_keeper.py:78` and `bar_keeper.py:58` `LOCK_TIMEOUT_SECONDS` (reported); `printer.py:86,102` `timeout=3` (when a service light goes down); `printer.py:253` `+ 1.0`; `wav_deck.py:31` `end_pause_seconds` (reported).
- [ ] **D6.2 Model request settings and prompt text** (verified): `scorer_model_client.py:61` `USER_TEMPLATE = "Rate this line: {clip_text}"`; `top_logprobs: 20` (bears on H21); `enable_thinking: False` in both clients; `prompt_builder.py:56-60` `"Style Example"`, `"Theme"` and the separators (Wes's oracle prompt refers to these words, so his config depends on text only a code edit can change).
- [ ] **D6.3 The filler list** (verified): `word_tagger.py:42` `DEFAULT_FILLERS`; `printer.py` never passes `fillers`; the docstring promised it would move to config "when the Scorer's settings do". This list decides which clips skip the model.
- [ ] **D6.4 Others** (reported): `bibliomancer.py:247` draw cap `max(50, 20 * wanted)`; `portent_formatter.py:71-74` default arguments that are clamped silently (**gone 2026-11-26**, D5 step 2); `config.py:251` `MIN_PRINTING_LINE_WIDTH`; `moving_average_bar.py:36` the 0-9 score range (config has `[chart] score_max`); `log_setup.py:44-47` log file folder, size and backups (and `pom.toml:33`'s comment states the value); `monitor_site.py:107` "just now" under 5 s; messages like "connection lost, retrying" and the auth `REALM`.
- [ ] **D6.5 Config keys without their unit** (reported): `scorer.timeout`, `oracle.timeout`, `listening.whisper_timeout`, `chart.margin_*`, `drawing_*`, `*_radius`, `break_*`; `hinge_snippet_chars` beside `max_typed_line_characters` (two words for one unit).
- [ ] **D6.6 Comments that state values instead of naming the setting** (reported): `config.py:21,28-29,472`, `floor_keeper.py:75` (**gone 2026-11-26** with the FloorKeeper's copies); they go stale (D5.1).

## D7. Results passed through side-channel attributes

A caller makes a call, then reads an attribute the callee set, which depends on call order and isn't thread-safe. The result should carry the outcome.

- [ ] **D7.1** `OracleModelClient.last_seconds`/`last_cut_off`, read by `PortentWriter` after `consult()` (verified). `consult` returns a frozen result.
- [ ] **D7.2** `WhisperModelClient.error`, read by `RollingCutter` and `Eavesdropper` after the call (reported).
- [ ] **D7.3** `DeviceFinder.found`, read by `printer.py:204`; `resolve` returns the setting itself ("auto") when nothing is there (reported). Return `Optional[str]`.
- [ ] **D7.4** The bar is read (`bar_at`, `window_size_at`, `counted`) in separate calls before `rule()`, with up to 30 s of scoring between, while the keyboard thread can move it (verified; `bar_keeper.py:35-38` admits it). The Attempt, chart and history can record a bar the ruling never used. `rule()` returns one frozen judgement built under the lock.

## D8. Locks: state outside its lock, work inside it

- [ ] **D8.1 Shared state with no lock** (reported unless marked): `ClipScorer`'s breaker state and `WordTagger`'s cache, used from the room thread, the keyboard thread and the warm-up thread; the entry point's `snapshot` dict, incremented on two threads and read on the web thread (verified, `printer.py:263`); the Conductor's counters (`conductor.py:269,276`); `TerminalPort`'s line state (`_received`, `_line`, `_after_cr`), changed by `read_line` outside the lock and cleared by `transmit` under it.
- [ ] **D8.2 Logging and calls out while a lock is held** (reported): `floor_keeper.py:214,266-268,297`, `bar_keeper.py:199,223`, `moving_average_bar.py:134`; `conductor.py:165` reads `floor.state` (the FloorKeeper's lock, which can log) inside the Conductor's lock. Their own docstrings say nothing inside the lock waits. Decide under the lock, log after.

## D9. The same plumbing written more than once

- [ ] **D9.1 The Printer's wiring is rebuilt by hand in dev scripts** (reported; D1.1 is the result): `listening_to_portent.py` has its own `answers`/`reachable` and `ServiceWatch` (redoing `printer.py`'s health checks, StatusBoard and ServicePoller), its notebook has no ticks or silence shape, `TakeCutter()` ignores `max_clip_words` (also `listen_to_room.py:76`); `serve_fake_turns.py` wires status and notebook a third time; the library (Catalog + Circulation + Bibliomancer) is wired by hand in three places. Playgrounds should use the real wiring: functions in src that `build_printer` and the scripts share.
- [ ] **D9.2 The three model clients each have their own HTTP code** (reported): `_http_post` in scorer and oracle, a third in whisper, the http/https check three times. One small transport class, handed to every client, would be the one place for H4's deadline and H26's proxies.
- [ ] **D9.3 Two ways to ask "is the server up"** (verified): `printer.py:81-105` (`health`, `reachable`, `timeout=3`) and `WhisperModelClient.ping` (`PING_TIMEOUT=5`, only dev scripts use it). Also the ScorerWarmer and the ServicePoller both poll the scorer (reported).
- [ ] **D9.4 The silence shape is worked out again on every chart draw** (reported): `score_chart.py:109`, while the notebook is handed its own; they have already drifted in the testbench. Hand both the same `SilenceShape`.
- [ ] **D9.5 Duplicated test stand-ins** (reported): `class Clock` written 8 times; `FakeClock` ×2, `FakeScorer` ×3, `FakePost` ×3, others ×2. One shared `tests/fakes.py`.

## D10. Classes with more than one job, and long argument lists

- [ ] **D10.1 `TerminalPort`** (reported): 22 constructor arguments; pacing and sending, stalls and reconnects, AND the keyboard's line handling (CR/LF, backspace, echo, length). `printer.py` passes 10 of them by position.
- [ ] **D10.2 `MonitorSite`** (reported): 14 arguments; password checks, server start/stop, page numbers and rows, chart assembly, and about 140 lines of inline CSS/JS/HTML filled in with `%`-formatting (not template and static files, which is also why the security policy needs `'unsafe-inline'`).
- [ ] **D10.3 `Eavesdropper`** (reported): 15 arguments; builds its own RollingCutter and TrackSplitter and passes cutter settings through (`cutter=` is "for tests"). The entry point should build the cutter and hand it in.
- [ ] **D10.4 `ClipScorer`** (reported): filters, counts, ranks AND runs the scorer's circuit breaker, which is server-connection policy.
- [ ] **D10.5 `build_printer` keeps live state in closures** (verified): `snapshot` (D8.1), `last_room` and the stream-clock carry-over; the typed-line clock is a second stream clock (`printer.py:258`, see H4). A small stream-clock class handed to both. `Printer.run` and `main` read the test-only `parts` dict (`parts["monitor"].host`, `parts["mode"]`).
- [ ] **D10.6 Others** (reported): `TurnTaker` 14 arguments and `DecayingQuantileBar` 10 (config already builds frozen settings; the bars have `from_settings`, the conductor's parts don't); `TurnTaker` unpacks the Attempt into `TurnHistory.add`'s fields; `DecayingQuantileBar.window_size_at`, a question, prunes memory.

## D11. Tests reach inside

- [ ] **D11.1 About 265 reads of private attributes in tests** (verified count): `test_printer.py` about 110 (for example `taker._freshness`, `p.site._look`, `conductor._room_factory` 17 times), `test_decaying_quantile_bar.py` 45, `test_bar_keeper.py` 14, `test_bar_keeper_factory.py` 13, `test_terminal_port.py` 11. The sign: what `build_printer` built, and with which settings, can't be seen through a public interface. D5's frozen settings objects would give it one.
- [ ] **D11.2 Classes with no test file of their own** (reported): `silence_compressor.py` (the arithmetic every chart part shares) is tested only through others.
- [ ] **D11.3 Old words in test names** (reported): 58 test names say "report" or "recorder".

## D12. Types and words

- [ ] **D12.1 Free strings where an Enum belongs** (reported): an attempt's `kind` ("speech", "lyrics", "typed", "") set in three modules; `turn_taker.py:268` "waiting for the printer" beside `TurnOutcome` members, with `_set_outcome` taking either.
- [ ] **D12.2 Shared small types in the wrong module** (reported): `ScoredBy` lives in `clip_scorer.py` and qualifying, conductor and keyboard import it from there; scoring imports `Clip` from listening.
- [ ] **D12.3 A pyserial error matched by its name** (verified): `terminal_port.py:321` `type(error).__name__ == "SerialTimeoutException"`.
- [ ] **D12.4 Old and doubled words** (reported): "selector" and "store" (`PromptBuilder(selector=)`, `parts["selector"]`, `Bibliomancer(store=)`, "selector ready"), retired in the Lexicon; "Nothing here may stop the oracle" 54 times, meaning the whole Printer; `held` meaning window size beside `Ruling.HELD`; the "printer" light means the terminal; `terminal_port.py` docstrings still say `type(text)`; the Lexicon is behind on the BarKeeper ("sets the bar").
- [ ] **D12.4b A state that can't happen, still guarded** (reported): `bar.py:31` declares `level_at -> Optional[float]`, but no bar returns None for a time, so the BarKeeper's `level is None` paths (`bar_keeper.py:126,190,217`) are dead guards.
- [ ] **D12.5 Comments that name a particular machine for a general fact** (reported): "the Wheelwriter" in `terminal_port.py:3`, `portent_formatter.py:3`, `startup.py:189`; "CadetWriter" in `config.py:187,562,567`; "ATR4697-USB" in `startup.py:177`. Some are true facts about that machine, which is allowed.
- [ ] **D12.6 `startup.py` has drifted from its plan** (reported): two of its ten duties are done elsewhere now; its docstring says the Conductor reads `result.ready`; nothing does. Relates to H6, H17.
- [ ] **D12.7 The monitor's chart legend is wrong in dark mode** (reported): it says "Black dots"; in dark mode they are near-white; the grey tick dots aren't explained; `.hit` has the one colour outside the theme tokens.

---

## Checked and found sound

The Bookkeeper, LogRecorder and LogMuter (one lock, on the handlers, never raises); `ConfigProblems`; no module but `printer.py` imports config, and `build_printer` contacts no server; `pom.toml`'s keys match `_KEYS` both ways; `audio_format.py` is the one frame geometry; `AudioDeck` is a clean Protocol and `MacMicDeck` changes only the capture command; `TrackSplitter`, `PortentFormatter` and `score_from_scorecard` are pure; the DeviceFinder rule is written once; `ScoredClip`, `Portent`, `OraclePrompt`, `Passage`, `PrintResult`, `Options`, `SilenceShape` are frozen; Catalog (read), Circulation (weights) and Bibliomancer (draw) are cleanly split; the bar's influence rule is exactly bar + influence × (score − bar) everywhere, with no blends; all three bars can be chosen in config; FloorKeeper's turn context manager and lazy expiry; ticks are a pure function of the clock; StatusBoard, TurnHistory and ChartNotebook call out outside their locks; werkzeug's `make_server(request_handler=…)` is its designed extension point; the site's password check is constant-time; no clip or portent text is logged.
