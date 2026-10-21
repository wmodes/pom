# scripts/setup/

One-time, idempotent provisioning scripts for the Raspberry Pi. Each builds a third-party tool from a pinned upstream tag; the tool itself is never vendored into this repo.

- `setup-whisper.sh`: whisper.cpp (speech to text) and its models.
- `setup-llama.sh`: llama.cpp (the local LLM runtime). Models are chosen separately.

Run them on the Pi. They skip steps already done and never delete an existing checkout.

- `setup-nltk-data.sh` fetches the NLTK data (WordNet, stopwords, a part-of-speech tagger) into the gitignored `data/nltk/` folder, for the Scorer. Run it on each machine after `pip install -r requirements.txt`.
