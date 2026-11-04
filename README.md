# Printer of Mysteries

A haunted typewriter that listens to a room and, now and then, types back.

An IBM Wheelwriter 1000, driven by a Raspberry Pi 5, overhears the conversation around it. Most of what it hears it lets pass. Every so often a sentence stands out from the room's own chatter, and the typewriter answers with a *portent*: a short, tangential, lyrical pronouncement, never a direct reply. You can also type to it, and it may type back.

Everything runs locally: speech recognition and both language models live on the machine, with no cloud at run time. It is part of the Office of Ordinary Hauntings ([officeofordinaryhauntings.net](https://officeofordinaryhauntings.net), case study CS-0027), and the lore goes like this: a printer that has printed a million pages is imbued with the residue of everything it was ever obliged to say. Something said in the room snags on that residue, and the machine answers from it.

> **Nothing is kept.** Speech is transcribed and discarded. Heard words live in memory only and are dropped as they age; the logs hold counts and scores, never words.

## How it works

```
microphone ─▶ whisper ─▶ Scorer ─▶ BarKeeper ─▶ hinge ─▶ Bibliomancer ─▶ oracle ─▶ formatter ─▶ typewriter
  (room)     speech to   how        does it     the         draws a few     writes     sets it for   types at
             text        interesting stand out   sentence    passages from   the        the paper     16 characters
                         is this     from the    that        the library     portent                  a second
                         sentence?   room?       qualified
```

1. **Listening.** A microphone feeds whisper.cpp, which also cuts the audio into speech, room tone and silence.
2. **Scoring.** A small language model scores each sentence from 0 to 9 for how interesting it is, one sentence at a time and with no context, so a sentence scores the same whatever came before.
3. **Qualifying.** The *bar* is built from the room's own recent scores, so a loud, lively room needs a better sentence than a quiet one. A sentence that clears the bar, and a cooldown since the last portent, becomes the *hinge*.
4. **Writing.** The library holds about 71,000 passages from old books (scripture, almanacs, etiquette guides, manuals, epitaphs, nonsense verse). A few are drawn at random, weighted so every shelf gets its share, and shown to a larger language model, the *oracle*, along with the hinge. It writes something new in their manner.
5. **Printing.** The portent is set for the paper and typed over a serial port at the typewriter's own pace.

The machine does one thing at a time: while it writes and types, it does not listen. Typed lines have their own way in, with a small thumb on the scale toward an answer.

It is built to run unattended in a gallery. If the typewriter is unplugged, out of paper, or on hold, it waits and carries on where it stopped. If a model server dies and comes back, it recovers by itself. Each stage has a time limit, so a stuck step cannot silence it for good.

The reasons behind each choice are in the [Design Document](documentation/Design%20Document.md). The parts most worth reading: [how the bar is set](documentation/Design%20Document.md#qualifying-when-does-the-printer-speak) (and the rules tried and dropped), [how a turn is run and kept from hanging](documentation/Design%20Document.md#conducting-running-a-turn), and [how the library is drawn](documentation/Design%20Document.md#the-library-the-form-that-influences-the-oracle).

### The models

All three stay loaded in memory at once, which is why the Pi is the 16 GB one.

| Job | Model | Run by |
|---|---|---|
| Speech to text | whisper `base.en`, with Silero voice detection | [whisper.cpp](https://github.com/ggml-org/whisper.cpp) |
| Scoring sentences | Qwen3-4B (Q4_K_M) | [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server` |
| Writing portents | Qwen3.5-9B (Q4_K_M) | a second `llama-server` |

## Hardware

- **Typewriter:** IBM Wheelwriter 1000, converted to a printing terminal with the open-source CadetWriter interface board (Teensy 3.5), which shows up as an ordinary USB serial port.
- **Computer:** Raspberry Pi 5 (16 GB), Raspberry Pi OS Bookworm 64-bit.
- **Microphone:** Audio-Technica ATR4697-USB boundary microphone, wired.
- **Paper:** green-bar continuous fanfold.

The development machine is a Mac. Everything except the typewriter itself can run there, with a stand-in terminal on the screen.

## Running it

### On a Mac, for development

You need Python 3.11, [Homebrew](https://brew.sh), and a few tools:

```sh
brew install whisper.cpp llama.cpp ffmpeg

python3.11 -m venv ~/envs/pom && source ~/envs/pom/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
scripts/setup/setup-nltk-data.sh          # word lists and tagger data for the Scorer
```

Put the whisper model (`ggml-base.en.bin`) and the Silero voice-detection model (`ggml-silero-v6.2.0.bin`) in `data/whisper/`. The two language models download themselves the first time the servers start.

Then one command starts whisper and both model servers (reusing any already running), runs the Printer on them, and stops what it started when you quit:

```sh
# listen to this Mac's microphone and "type" on the screen
python scripts/dev/pipeline/run_printer_with_servers.py --mac-mic --terminal console

# or play a recording through it, faster than real time, with no printer at all
python scripts/dev/pipeline/run_printer_with_servers.py --wav meeting.wav --speed 6 --terminal none
```

Any other option goes straight to the Printer (`python -m pom --help` lists them): `--terminal serial|console|none`, `--no-keyboard`, `--config FILE`. A recording must be 16 kHz mono.

To watch what it is doing, switch on the monitoring page: copy `config/pom.local.toml.example` to `config/pom.local.toml`, set a `password` under `[monitor]` (it is empty, and the page will not start until it is set), restart, and open `http://localhost:8080`. It shows the last hour of turns, with each hinge, the exemplars the oracle was shown, the portent, and a chart of scores against the bar. The hinge is what someone said in the room, so the page asks for the password and keeps nothing on disk.

### On the Pi

The Printer is `python -m pom`, run by systemd, with each model server as its own service so a crashed server restarts without taking the Printer down. Provisioning (OS, Wi-Fi with a fallback hotspot, whisper.cpp and llama.cpp builds) is in [`documentation/Pi Setup Notes.md`](documentation/Pi%20Setup%20Notes.md). The unit files are in [`scripts/setup/systemd/`](scripts/setup/systemd/) and are linked into place by `scripts/setup/install-services.sh`, so a `git pull` then `scripts/setup/update-pi.sh` brings the Pi up to date; the log is `journalctl -u pom`.

## Configuration

Every tunable lives in [`config/pom.toml`](config/pom.toml), with a comment on each: the prompts, the thresholds, the time limits, the paper and margins, the serial port. Nothing the installation might need adjusting is buried in code.

To change something for one machine only, put it in `config/pom.local.toml`. It overrides the main file and is not committed, so it is also where passwords go. Start it from the template: `cp config/pom.local.toml.example config/pom.local.toml`. Every key a local file sets also appears in `pom.toml` with a safe default, and the monitoring page will not start until you give it a password there.

## The library

`library/` holds what the oracle may say: 81 holdings on 14 shelves (divination, wisdom, poetry, drama, humor, horoscope, manual, prophecy, epitaph, aphorism, rhyme, spiritualism, news, nonsense). A shelf is what the oracle might say, not where a text came from. Everything is built by scripts from one manifest, [`library/holdings.csv`](library/holdings.csv), so it can be rebuilt exactly. Most holdings are public domain; a few are not, and were cleared by the project owner (see [`library/README.md`](library/README.md)). How often each shelf and holding is drawn is set in one hand-edited file, `library/passages/circulation.json`.

A passage never stops mid-thought: long texts are cut only after a full stop, question mark or exclamation mark, and a passage that would end on a colon or a dangling "the" is kept out. The oracle copies the ending of what it is shown, and a cut-off exemplar once made a cut-off portent.

## Repository map

```
src/pom/            the Printer
  listening/          microphone, whisper client, cutting speech into sentences
  scoring/            the Scorer: how interesting is a sentence
  qualifying/         the bar, the cooldown, and who gets to speak
  library/            the passages and how they are drawn
  writing/            building the prompt and asking the oracle
  printing/           setting a portent for the paper, the serial port
  conductor/          who has the floor, one turn start to finish, the keyboard
  monitor/            the monitoring web page
config/             pom.toml (shipped settings); pom.local.toml (yours, not committed)
library/            the holdings manifest and the passages built from it
scripts/dev/        testbenches: small tools to poke one part with the real modules
scripts/setup/      one-time provisioning for the Pi
tests/              the test suite
documentation/      design notes, vocabulary, setup notes, working notes
experiments/        earlier prototypes and model bake-offs, kept as they were
LICENSE.md          the MIT License (for the code and docs; the library's texts have their own rights)
```

## Development

```sh
python -m pytest                    # the whole suite (over 3,000 tests)
```

- **Testbenches.** [`scripts/dev/README.md`](scripts/dev/README.md) lists small tools that exercise one stage with the real modules: type a hinge and read the portent, type sentences and see their scores, watch what would qualify on a recording.
- **Vocabulary.** [`documentation/Lexicon.md`](documentation/Lexicon.md) defines the words the code, docs and conversation share (hinge, portent, bar, circulation, turn, and the rest). Use them.
- **Design.** [`documentation/Design Document.md`](documentation/Design%20Document.md) explains how it works and why, and what was measured. [`documentation/Project State.md`](documentation/Project%20State.md) is the working handoff.
- **The Pi.** [`documentation/Pi Setup Notes.md`](documentation/Pi%20Setup%20Notes.md) covers setting it up, its services and microphone, and what to do when `pom1.local` will not resolve.
- **History.** [`CHANGELOG.md`](CHANGELOG.md) records every change.
- **Adding to the library.** [`documentation/Adding Material.md`](documentation/Adding%20Material.md).
- **AI use.** Use of remote AI in this project is logged in [`documentation/AI Ledger.md`](documentation/AI%20Ledger.md). The models that run the installation are local and are not counted there.

## Where it stands

Built and tested: listening, scoring, qualifying, the library and its draws, writing, formatting, a serial port that survives unplugging, the Conductor that runs it all, the entry point, and the monitoring page. The whole Printer has been run end to end on a Mac against recordings with a stand-in typewriter.

Running on the Pi: the Printer and its three model servers are systemd services that start by themselves at boot, restart after a crash, keep a persistent log, and hold all three models in memory at once (about 7 GB of the 16 GB left free). A reboot and a hard kill of a server both passed. It has listened to a live room through the microphone and written portents, with no typewriter attached.

Still to do: a test on the real CadetWriter (including how it signals "on hold" and "out of paper"), a printer status line on the monitoring page, and the remaining start-up duties (a check of the microphone gain, other readiness checks).

## Credits

Made by Wes Modes for the Office of Ordinary Hauntings. The CadetWriter board that lets a Pi talk to the typewriter was donated by Dave Babcock. Built on [whisper.cpp](https://github.com/ggml-org/whisper.cpp), [llama.cpp](https://github.com/ggml-org/llama.cpp), Flask, NLTK, wordfreq and pyserial, and on a great many old books.

## License

The code, the documentation and the rest of the project's own writing are released under the [MIT License](LICENSE.md): use them, change them, build on them, with the notice kept.

That covers the project, not the books. The texts in `library/` keep their own rights, recorded for each holding in [`library/holdings.csv`](library/holdings.csv): most are public domain, and a few are not and were cleared by the project owner for this installation (see [`library/README.md`](library/README.md)). The MIT License does not extend to those, so check a holding's row before reusing its text.
