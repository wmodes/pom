# scripts/dev/

Helpers that run on the Mac while developing, grouped by task. Several are **testbenches**: small tools that let you type
into one built part of the Printer and see what it does, using the real modules (not their own versions of them).

- `library/`: fetches and cleans the public-domain library from `library/holdings.csv`. Rules and workflow are in the repo-root `library/README.md`.
  - `library/validate_output.py` is a stub for checks on the oracle's replies. It reports nothing yet; the reasons are in its header.
  - `library/sample.py` plays with the Bibliomancer: prints pulls of random passages, or measures
    the long-run shelf shares.
- `listening/`: `cut_speech_into_hinges.py` shows where the TakeCutter cuts a piece of speech into hinge candidates; `split_whisper_tracks.py` shows how
  the TrackSplitter splits whisper's text into words, room tone and blanks; `listen_to_room.py` runs the whole listening side on the
  microphone or a recording, scoring what it hears and marking what would qualify (it needs whisper-server running).
- `scoring/`: `score_typed_sentences.py` scores the sentences you type with the real Scorer and shows whether it would qualify (it needs
  the scorer model's llama-server); `score_typed_lines.py` does the same for the typed-line path: each line's score, its score multiplier (`typed_lines_score_multiplier`) and whether
  the Printer would answer or stay silent.
- `pipeline/`: `listening_to_portent.py` (`--monitor` also serves the monitoring site, fed by every turn) runs the whole Printer except the typewriter: the microphone (`--mac-mic` for this Mac's, or
  the Pi's by default) or a recording goes through the Eavesdropper, the scorer and the BarKeeper, and when a sentence qualifies the
  hinge is sent to the oracle and the portent is printed where the typewriter would. It needs whisper and both llama-servers running.
  `run_printer_with_servers.py` is the test bench for the real thing: it starts whisper and both llama-servers (using any already running), runs `python -m pom` with whatever options you give it
  (`--mac-mic --terminal console`, or `--wav FILE --speed 6 --terminal none`), and stops the servers it started when the Printer ends.
- `monitor/`: `serve_fake_turns.py` serves the monitoring site with invented turns (`--live 15` adds one every 15 seconds,
  `--hide-hinges` shows the numbers-only page) so you can open it, click a turn and watch it update. Password: `demo`. `--chart` also draws the chart:
  the real BarKeeper and anchor (from `config/pom.toml`) over invented attempts, on a stream clock that runs `--speed` times faster than the wall clock
  (default 30, so a one-minute tick comes every two seconds). It plays 35 stream minutes at once so the chart is not empty, and the invented room goes quiet
  fairly often, so you can watch the bar slide down and the silences drawn short.
- `qualifying/`: `replay_scores_through_bar.py` replays a recorded stream of scores (by default the first hour of the live room, 2026-10-26, numbers only) through the real BarKeeper at
  several `qualifier_bar_influence` values and prints the qualifiers an hour, the share of scores above the bar, and the bar's level. `--offset-hours 30` makes the first score
  come 30 hours after the Printer started, so the bar's starting line has faded. It needs no models and no servers.
  `report_score_percentiles.py` reports a percentile (the 90th unless `--percentile` says otherwise) of the scores each recording already earned, the four meetings and the live room, and of all of them pooled, from the numbers-only score files in
  `data/recordings/work/zoom-scores/`, over every score and over the model's alone. It does not re-score or replay anything; re-score into new files when the scorer's prompt or model changes and run it again (the files' dates are shown). `--scores FILE` reads other files.
- `printing/`: `format_text_for_typewriter.py` shows how the PortentFormatter sets text for the typewriter: type or pipe text in (quote the
  heredoc, `<<'EOT'`, or the shell runs anything in backticks) and see it between rulers, with the quote indented. `--cpi 12`, `--width`,
  `--indent`, `--feeds`, `--keep` and `--ascii-wheel` change what `[printing]` in the config sets.
- `printing/` also has `type_text_on_typewriter.py`: it formats the text and types it, paced, on the screen (the ConsoleTerminal, the real port code
  writing to the terminal) or on a real serial port with `--port`; `--fast` skips the waiting and `--reply SECONDS` waits for a line typed back.
- `writing/`: `write_portent_from_hinge.py` takes the hinges you type and prints the portent the oracle model writes, with the exemplars it was
  given (it needs the oracle model's llama-server, or starts one).
