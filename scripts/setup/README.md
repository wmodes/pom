# scripts/setup/

Provisioning for the Raspberry Pi: scripts that build the third-party tools, and the service files that run the Printer. The scripts are idempotent; each builds a tool from a pinned upstream tag, and the tool itself is never vendored into this repo.

- `setup-whisper.sh`: whisper.cpp (speech to text) and its models.
- `setup-llama.sh`: llama.cpp (the local LLM runtime). Models are chosen separately.

Both build into `data/tools/` inside the repo (gitignored, so the clone stays clean), and use a relative
library path (`$ORIGIN`) so a built folder can be moved without rebuilding. Override with `WHISPER_DIR` and
`LLAMA_DIR`.

Run them on the Pi. They skip steps already done and never delete an existing checkout.

- `setup-tailscale.sh` puts the Pi on a Tailscale network (apt repository, package, service, sign-in, name resolution left alone) so its pages and a shell can be reached from anywhere. Safe to run again. See "Remote access (Tailscale)" in the Pi setup notes.
- `setup-nltk-data.sh` fetches the NLTK data (WordNet, stopwords, a part-of-speech tagger) into the gitignored `data/nltk/` folder, for the Scorer. Run it on each machine after `pip install -r requirements.txt`.

## Running it as services

The Printer and its three model servers run under systemd, so they start at boot and come back if they stop. Everything for that is here, in the repo,
and is **linked** into place (`systemctl link`), not copied: the file systemd reads is the file you edit, so a `git pull` rewrites the services.

- `systemd/pom-whisper.service`, `pom-scorer.service`, `pom-oracle.service`: the three servers. Each keeps its model loaded and resident (`-lm mmap -lzm off` for the llama-servers). They are not pinned with mlock: measured on the Pi, pinning used the memory twice and left under 1 GB available; plain mmap leaves about 7 GB with all three running.
- `systemd/pom.service`: the Printer (`python -m pom`). It comes after and wants the servers, and never requires them, so a crashed server cannot take it down.
- `journald/90-pom.conf`: keeps the log across reboots, capped in size (`journalctl -u pom`).
- `networkmanager/90-pom-wifi.conf`: Wi-Fi power saving off, so the Pi's radio does not sleep through broadcasts and multicast.
- `check-service-flags.sh`: checks that every flag in the server units is one the installed server lists in its own `--help` (a flag the Pi's build does not know makes the service crash-loop); the install script runs it first.
- `install-services.sh`: link the units and the log setting, reload systemd, enable them, and restart only the services whose unit file changed (so the models stay loaded). `--start` also starts any that are not running. Run it as the `pi` user.
- `update-pi.sh`: `git pull`, refresh the Python packages, run the install script, and restart the Printer only if code, settings or the library changed.

The units name `/home/pi/pom` and `/home/pi/envs/pom`, because a linked file cannot be filled in per machine. The ports and flags are pinned by `tests/scripts/test_services.py` against `config/pom.toml` and the bench script.

