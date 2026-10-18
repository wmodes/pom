# AI ledger

Scope: **remote AI only**, meaning Claude work on this project (setup, code, debugging).
Local AI that runs on the Pi (whisper.cpp, the local LLM, the embedding gate) is not logged here.

Token counts come from Claude Code's `/cost` or `/usage` at the end of the day. Don't estimate them from memory.

| Date | Model | ~Tokens (from /cost) | What it did |
|---|---|---|---|
| 2026-10-18 | Claude Sonnet 5.5 | _fill at end of day_ | Provisioned the Pi 5 (`pom1`): Imager settings, diagnosed a Trixie cloud-init first-boot failure from the SD card's logs and moved to Bookworm, apt upgrade, bootloader EEPROM update, Comitup Wi-Fi fallback hotspot (installed and tested), wrote `pi-setup-notes.md`. In progress: whisper.cpp setup. |
