# scripts/dev/

Helpers that run on the Mac while developing, grouped by task.

- `corpus/`: fetches and cleans the public-domain corpus from `corpus/sources.csv`. Rules and workflow are in the repo-root `corpus/README.md`.
  - `corpus/validate_output.py` is a stub for checks on a voice's replies. It reports nothing yet; the reasons are in its header.
  - `corpus/sample.py` plays with the Sampler: prints pulls of random chunks, or measures the long-run register shares.
- `listening/`: `fragment.py` shows where the Fragmenter cuts a piece of speech into hinge candidates.
