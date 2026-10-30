# scripts/dev/

Helpers that run on the Mac while developing, grouped by task. Several are **playgrounds**: small tools that let you type
into one built part of the Printer and see what it does, using the real modules (not their own versions of them).

- `corpus/`: fetches and cleans the public-domain corpus from `corpus/sources.csv`. Rules and workflow are in the repo-root `corpus/README.md`.
  - `corpus/validate_output.py` is a stub for checks on a voice's replies. It reports nothing yet; the reasons are in its header.
  - `corpus/sample.py` plays with the ExemplarSelector: prints pulls of random chunks, or measures
    the long-run register shares.
- `listening/`: `cut_speech_into_hinges.py` shows where the SegmentFragmenter cuts a piece of speech into hinge candidates; `sort_whisper_text.py` shows how
  the WhisperTextSorter sorts whisper's text into words, captions and nothing; `listen_to_room.py` runs the whole listening side on the
  microphone or a recording, scoring what it hears and marking what would trigger (it needs whisper-server running).
- `scoring/`: `score_typed_sentences.py` scores the sentences you type with the real Scorer and shows whether the trigger would fire (it needs
  the scorer model's llama-server).
- `pipeline/`: `listening_to_portent.py` runs the whole Printer except the typewriter: the microphone (`--mac-mic` for this Mac's, or
  the Pi's by default) or a recording goes through the Listener, the scorer and the trigger, and when the trigger fires the
  hinge is sent to the oracle and the portent is printed where the typewriter would. It needs whisper and both llama-servers running.
- `monitor/`: `serve_fake_triggers.py` serves the monitoring site with invented triggers (`--live 15` adds one every 15 seconds,
  `--hide-hinges` shows the numbers-only page) so you can open it, click a trigger and watch it update. Password: `demo`.
- `voice/`: `write_portent_from_hinge.py` takes the hinges you type and prints the portent the oracle model writes, with the exemplars it was
  given (it needs the oracle model's llama-server, or starts one).
