# scripts/dev/

Helpers that run on the Mac while developing, grouped by task. Several are **playgrounds**: small tools that let you type
into one built part of the Printer and see what it does, using the real modules (not their own versions of them).

- `corpus/`: fetches and cleans the public-domain corpus from `corpus/sources.csv`. Rules and workflow are in the repo-root `corpus/README.md`.
  - `corpus/validate_output.py` is a stub for checks on a voice's replies. It reports nothing yet; the reasons are in its header.
  - `corpus/sample.py` plays with the ExemplarSelector: prints pulls of random chunks, or measures
    the long-run register shares.
- `listening/`: `fragment.py` shows where the SegmentFragmenter cuts a piece of speech into hinge candidates; `sort.py` shows how
  the WhisperTextSorter sorts whisper's text into words, captions and nothing; `hear.py` runs the whole listening side on the
  microphone or a recording, scoring what it hears and marking what would trigger (it needs whisper-server running).
- `scoring/`: `score.py` scores the sentences you type with the real Scorer and shows whether the trigger would fire (it needs
  the scorer model's llama-server).
- `voice/`: `portent.py` takes the hinges you type and prints the portent the oracle model writes, with the exemplars it was
  given (it needs the oracle model's llama-server, or starts one).
