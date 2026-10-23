# scripts/setup/

One-time, idempotent provisioning scripts for the Raspberry Pi. Each builds a third-party tool from a pinned upstream tag; the tool itself is never vendored into this repo.

- `setup-whisper.sh`: whisper.cpp (speech to text) and its models.
- `setup-llama.sh`: llama.cpp (the local LLM runtime). Models are chosen separately.

Both build into `data/tools/` inside the repo (gitignored, so the clone stays clean), and use a relative
library path (`$ORIGIN`) so a built folder can be moved without rebuilding. Override with `WHISPER_DIR` and
`LLAMA_DIR`.

Run them on the Pi. They skip steps already done and never delete an existing checkout.

- `setup-nltk-data.sh` fetches the NLTK data (WordNet, stopwords, a part-of-speech tagger) into the gitignored `data/nltk/` folder, for the Scorer. Run it on each machine after `pip install -r requirements.txt`.
