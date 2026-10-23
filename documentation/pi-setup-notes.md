# Pi 5 setup notes (pom1)

Started 2026-10-18. Covers provisioning the Raspberry Pi 5 for the Printer of Mysteries.
No passwords or Wi-Fi secrets belong in this file.

## Current state

| Item | Value |
|---|---|
| Hardware | Raspberry Pi 5 16GB, official 27W/5A PSU, Active Cooler, 128GB microSD |
| OS | Raspberry Pi OS (Legacy, 64-bit) Lite = Debian 12 Bookworm, aarch64 |
| Hostname | `pom1` (reachable as `pom1.local`) |
| User | `pi`, SSH key only (password login off), passwordless `sudo` |
| Network | Wi-Fi on the home network, MAC `88:a2:9e:87:88:41` (DHCP, was 192.168.4.22) |
| Updates | `apt full-upgrade` done 2026-10-18 (6 packages, no reboot needed) |
| Bootloader | EEPROM 2026-09-25 (updated 2026-10-18) |
| Wi-Fi fallback | Comitup 1.47.1 (hotspot `pom1-direct`, portal at `http://10.41.0.1`); tested 2026-10-18 |
| Mic | Audio-Technica ATR4697-USB (omni boundary, **wired** USB-C with USB-A adapter, 1.5 m cable); ordered, not yet tested |

Connect with `ssh pi@pom1.local`.

## Imager settings used (Raspberry Pi Imager v2.0.12)

- Device: Raspberry Pi 5
- OS: **Raspberry Pi OS (Legacy, 64-bit) Lite**. Imager's "Legacy" label means Bookworm with security updates.
  Not the Trixie entries (see below).
- Storage: the 128GB microSD
- Customisation: hostname `pom1`, user `pi`, Wi-Fi SSID/password + country US, locale/timezone America/New_York, keyboard `us`, SSH enabled with the public key from this Mac (`~/.ssh/id_rsa.pub`)
- Raspberry Pi Connect: **off**. It is a cloud relay and the project is fully local. Can be enabled later if remote maintenance is wanted.

## Why Bookworm and not Trixie

The first attempt used Trixie (Pi OS Lite 64-bit, 2026-10-06 image). The Pi booted but never appeared on Wi-Fi.
Read-only inspection of the SD card showed:

- Trixie configures Wi-Fi through **cloud-init 25.2** (`network-config` on the boot partition).
- On the first boot, cloud-init's `init-local` stage crashed with `JSONDecodeError: Expecting value` while reading an empty cached `/var/lib/cloud/instance/network-config.json`.
- Because that stage failed before applying the network config, and cloud-init only applies it on the first boot of an instance, later boots never retried.
- This matches two open upstream cloud-init PRs: [#7079](https://github.com/canonical/cloud-init/pull/7079) and [#7152](https://github.com/canonical/cloud-init/pull/7152). Not confirmed whether either is released in 25.2.
- The Imager settings and the generated `user-data`/`network-config` were all correct. This was not user error.
- Unexplained: the Pi's MAC briefly appeared on the network once during the Trixie attempt.

Bookworm does not use cloud-init (Imager uses the older `firstrun.sh` path) and joined Wi-Fi on first boot.
Bookworm is also the release the Comitup docs mention. Revisit Trixie when the upstream fix has shipped.

## Finding a headless Pi on the network

`.local` names can fail while a Pi is still booting. What worked:

1. Check the Mac's subnet size: `ifconfig en0 | grep inet`. This network is a `/22` (netmask `0xfffffc00`), so it spans **192.168.4.0 to 192.168.7.255**. Sweeping only 192.168.4.x is not enough.
2. Ping-sweep, then read the ARP table for the Pi's MAC prefix. ARP works even if the Pi ignores ping:
   ```bash
   for n in 4 5 6 7; do seq 1 254 | xargs -P 100 -I{} ping -c 1 -t 1 192.168.$n.{} >/dev/null 2>&1; done
   arp -a | grep -i -E "88:a2:9e|2c:cf:67|d8:3a:dd|dc:a6:32|e4:5f:01"
   ```
   (Pi 5 MAC prefixes seen: `88:a2:9e`, plus the usual Raspberry Pi Trading ranges.)
3. A `for ... &` loop with ~1000 background jobs hits zsh's job-table limit. Use `xargs -P`.
4. The router app's online/offline status is unreliable (it kept showing an unplugged device as online). Trust probes, not the app.
5. A blinking green ACT LED during boot is normal (SD card activity).

## Reading a Pi SD card's logs from macOS (read-only)

The boot partition (`bootfs`, FAT32) mounts on macOS. The root partition is ext4 and does not.
To read it without modifying anything:

1. `brew install e2fsprogs` (provides `debugfs`).
2. Find the device with `diskutil list` (it was `/dev/disk4s2`).
3. `sudo debugfs -c -R "cat /var/log/cloud-init.log" /dev/disk4s2` (`-c` = catastrophic/read-only mode).
4. Eject with `diskutil eject /Volumes/bootfs`.

Notes: `debugfs` needs root for the raw device. Trixie's journal was volatile, so only the cloud-init logs survived on the card.
`cloud-init.log` can contain the Wi-Fi password in debug output; treat dumped logs as sensitive.

## Wi-Fi fallback hotspot (Comitup)

If the Pi can't join a known network, it raises its own hotspot with a captive portal, like a new router or printer.

**How it behaves**
- Hotspot SSID `pom1-direct`, WPA-PSK. The password is set as `ap_password` in `/etc/comitup.conf` and is deliberately not recorded here. The AP's own address is `10.41.0.1`.
- Join it from a phone. The captive portal should open; if not, go to **http://10.41.0.1** (plain `http`). Pick a Wi-Fi network, enter its password, and the Pi joins and saves it.
- On every boot Comitup briefly raises the hotspot, then connects to a known network (about 30 s). Normal boots still rejoin the home network automatically.
- Comitup saves networks as ordinary NetworkManager profiles, so `nmcli con show` lists them.

**Install on Bookworm (what actually worked)**
1. Read-only survey first. Stock Bookworm was already clean: NetworkManager active, no `/etc/network/interfaces`, no `dnsmasq`/`systemd-resolved`/`dhcpcd`, nothing on ports 53/67.
2. **Do not mask `wpa_supplicant`**, despite old Comitup guides. NetworkManager uses it as its Wi-Fi backend.
3. `sudo apt-get install comitup` pulls Debian's 1.38 plus 32 dependencies, but **the service crashes at start**: `KeyError: UInt32(32)` in Debian's `python3-networkmanager` 2.2-2, which doesn't know the Wi-Fi P2P device type (`p2p-dev-wlan0`). Wi-Fi itself was unaffected.
4. Fix: add Comitup's own apt repo, as its docs say Bookworm needs.
   - Download `https://davesteele.github.io/comitup/deb/davesteele-comitup-apt-source_1.3_all.deb`, inspect it (`dpkg -I`, `dpkg -c`, read the `.sources` file and postinst), then `sudo dpkg -i` it. It adds a `Signed-By`-scoped keyring.
   - `sudo apt-get update`, then `sudo apt-get install --only-upgrade python3-networkmanager comitup` gives `python3-networkmanager` 2.2-3 and `comitup` 1.47.1.
5. Set `ap_name: pom1-direct` and `ap_password: ...` in `/etc/comitup.conf` (changes apply after a reboot), then reboot.

**Fallback test (passed 2026-10-18)**
1. On the Pi: `sudo nmcli con delete <home-wifi-profile>`. This drops SSH, since it deletes the active connection.
2. Comitup logged `HOTSPOT` immediately. `pom1-direct` appeared on a phone.
3. Joined from the phone, entered the home Wi-Fi in the portal, Comitup logged `CONNECTING`, the Pi joined, and SSH worked again at `pom1.local`.

**Caveats**
- `/etc/comitup.conf` is world-readable (644), so any local user can read the hotspot password. Low risk with key-only SSH and one user.
- A leftover `comitup-375-0000` profile from the default config is harmless.
- Not tested: whether Comitup rescans for known networks while the hotspot is up and the home Wi-Fi reappears later.

## whisper.cpp (speech to text)

*(Paths below are as first built, in the home folder. Since 2026-10-23 the tools live in `~/pom/data/tools/`; see "Deploying the project code".)*

Third-party, not bespoke: built on the Pi from a pinned upstream tag, not copied into this repo.

**Reproduce it with `scripts/setup/setup-whisper.sh`** (idempotent; pinned to v1.9.5; installs build tools, clones, builds, downloads `base.en` and `small.en`, then smoke-tests on `jfk.wav`). Override with `WHISPER_TAG`, `WHISPER_DIR`, `WHISPER_MODELS`. Tested 2026-10-18 on the Pi: re-run against the existing install (all steps skipped, smoke test passes), a fresh build into a throwaway directory (passes), and refusal when pointed at a checkout on a different tag. The manual steps below are what the script automates.

- Tag: **v1.9.5**, cloned to `~/whisper.cpp` (`git clone --depth 1 --branch v1.9.5 https://github.com/ggml-org/whisper.cpp.git`)
- Build tools: `git cmake build-essential` (cmake 3.25, gcc 12.2)
- Build: `cmake -B build && cmake --build build -j4 --config Release` (about 2-3 minutes on the Pi 5). Binaries in `build/bin/`: `whisper-cli`, `whisper-server`, `whisper-bench`. CPU features detected: NEON, ARM_FMA, FP16_VA, DOTPROD, OPENMP, REPACK.
- Models in `~/whisper.cpp/models/`: `ggml-base.en.bin` (142 MB), `ggml-small.en.bin` (466 MB), via `sh ./models/download-ggml-model.sh <name>`
- v1.9.x also builds `parakeet-cli` (NVIDIA Parakeet support). Not evaluated.

**Benchmark (2026-10-18, 4 threads, bundled `samples/jfk.wav`, 11 s of clean speech, two runs each, results stable):**

| Model | Total time | Encode | Peak RAM | Real-time factor |
|---|---|---|---|---|
| base.en | 2.5 s | 1.5 s | 290 MB | 0.23 (about 4.3x faster than real time) |
| small.en | 7.55 s | 5.5 s | 768 MB | 0.69 (about 1.5x faster than real time) |

- Both transcribed the clip correctly. `small.en` punctuated better.
- `small.en` at 768 MB fits the 1.2 GB budget for whisper.
- Whisper encodes a fixed 30 s window whatever the clip length, so an 11 s clip costs about what a full 30 s window costs. Per 30 s of audio that makes `small.en` about 7.5 s (RTF about 0.25) and `base.en` about 2.5 s. This is my understanding of whisper.cpp's design; I did not test it with a 30 s clip.
- Secondary sources predicted `small` would be slower than real time on a Pi 5. Measured, it is not.
- **Not yet measured:** accuracy on noisy room audio (needs the mic). This matters more than these clean-speech numbers.

**Casual-speech test (2026-10-18):** a 63.5 s phone recording (conversation, speaker "pretty far away, literally in the next room"), converted with `afconvert -f WAVE -d LEI16@16000 -c 1`, run with 4 threads:

| Model | Time | Real-time factor | Peak RAM |
|---|---|---|---|
| base.en | 8.4 s | 0.13 | 311 MB |
| small.en | 24.7 s | 0.39 | 789 MB |

- Transcripts were nearly identical. `small.en` punctuated slightly better; they disagreed on one word ("Shit" vs "Should").
- Dense speech costs more than the fixed-window estimate above: decoding adds real time on top of encode.
- **Leaning `base.en` as the default** (about 3x faster, 0.3 GB), with `small.en` kept on disk. One short phone clip is thin evidence; redo with the real mic.
- Whisper was accurate enough that any "mangling" for the oracle effect would probably have to be done deliberately downstream.

**Design decision (2026-10-18): half-duplex.** The machine listens, and only types when a TBD threshold is met. It does not need to process overheard audio *while* the typewriter is typing. So whisper and the LLM never run at the same time:
- No CPU contention between them, so either model is affordable. The casual-speech test favored `base.en` as default (see above); confirm with real mic audio.
- Typing noise is not a design problem. Only the typewriter's idle noise and the fan matter for the mic test.
- Open: should the pause also cover LLM generation (lean: yes), and does keyboard-typed input follow the same listen / respond / listen cycle?

## llama.cpp (local LLM runtime)

llama.cpp is the *runtime*, not a model; it runs Llama, Qwen, Gemma, Phi and others. Third-party, built from a pinned tag by `scripts/setup/setup-llama.sh` (idempotent, never deletes an existing checkout).

- Tag **b11721**, in `~/llama.cpp`; plain CPU Release build, about 15 minutes on the Pi 5 with 4 jobs. Binaries in `build/bin/`: `llama-cli`, `llama-server`, `llama-bench`. (`--version` reports "build 1" because the clone is shallow; the commit is `ff5a233`.)
- Tested: fresh build, then a re-run that skipped every step.
- Not enabled, a later experiment: `-DGGML_CPU_KLEIDIAI=ON` (Arm kernels).
- Model not chosen. Candidates verified on Hugging Face from trusted publishers (Q4_K_M): Qwen3-4B (`Qwen/Qwen3-4B-GGUF`, 2.50 GB, Apache-2.0); Llama 3.2 3B Instruct (`bartowski/Llama-3.2-3B-Instruct-GGUF`, 2.02 GB, Llama 3.2 license); Gemma 3 4B (`unsloth/gemma-3-4b-it-GGUF`, 2.49 GB, Gemma terms); LFM2.5 2.6B (`LiquidAI/LFM2.5-2.6B-GGUF`, 1.67 GB, license "other", read before using). No base (non-instruct) GGUF verified yet.
- Skipped on purpose: popular community "distill" and fine-tune uploads of unknown provenance.

## Deploying the project code (decided 2026-10-23)

The Pi has a real clone of the (public) repo at `~/pom`, so the layout matches the Mac and code arrives with
`git pull`; there is no deploy script. A clone is small: about 57 MB, with 16 MB of history, of which the
corpus is nearly all (the heavy folders `data/` and `corpus/raw` are gitignored and not in a clone).

```bash
git clone https://github.com/wmodes/pom.git ~/pom        # once
cd ~/pom && git pull                                     # to update, after pushing from the Mac
```

Everything on the Pi lives inside `~/pom`; nothing is loose in the home folder. What git does not carry is
under `~/pom/data/` (gitignored, as on the Mac):
- `data/tools/` the third-party builds, `llama.cpp/` and `whisper.cpp/` (with the whisper models), made by `scripts/setup`
- `data/logs/` build and server logs
- `data/models/` the language models (`.gguf`), copied from the Mac with `experiments/pi-benchmark/benchmark_pi_prepare.sh models`
- `data/recordings/` recordings and transcripts of real speech, which must never be committed (`work/` for
  working copies, `samples/` for the early whisper test clip)
- `data/live/pom.toml` a copy of `config/pom.toml` kept in step while the prompts are being edited
  (`experiments/hearing-debug/probe_hearing_sync_config.sh watch`), outside git so a pull never meets a modified file

The tools were first built in the home folder and moved here on 2026-10-23. A built binary found its libraries
through an absolute path, so they were rebuilt in place, and the setup scripts now build with a relative library
path (`$ORIGIN`) so the folder can be moved again without rebuilding. Keep the Pi's clone clean (no edits in tracked files), so `git pull` always works; `benchmark_pi_prepare.sh check` says if it is not.

## Fan check

The Active Cooler is firmware-controlled over the Pi 5's fan header and stays **off at idle**, ramping from roughly 50 C. A still fan is not a fault.
To verify later:

```bash
vcgencmd measure_temp
cat /sys/devices/platform/cooling_fan/hwmon/*/fan1_input   # RPM, 0 = off
sudo apt install stress-ng && stress-ng --cpu 4 --timeout 90s   # force it to spin
```

Fan noise may matter for the hidden mic, so include it in the typewriter idle-noise test.

## Pending

- [x] **Bootloader EEPROM update.** Done 2026-10-18: 2025-08-28 -> 2026-09-25 via `sudo rpi-eeprom-update -a` (`VERIFY: SUCCESS`), then reboot. `rpi-eeprom-update` now reports up to date.
- [x] **Wi-Fi fallback AP for venues.** Done 2026-10-18 with Comitup (see the section above). Still untested: whether it rescans for known networks while the hotspot is up, and the real venue Wi-Fi flow.
- [x] Fan verified 2026-10-18: during the llama.cpp build the Pi reached 52.7 C and `fan1_input` read 2794 RPM (it is off at idle).
- [ ] Case: stacked acrylic / open-frame case that explicitly supports Pi 5 + official Active Cooler (top window open, power button and fan header accessible).
- [~] Mic: Audio-Technica ATR4697-USB is wired, not wireless as the project brief assumed. Verified 2026-10-23: it shows up as `card 0: ATR4697USB`, records with `arecord -D plughw:0,0 -f S16_LE -r 16000 -c 1`, and its ALSA `Mic` capture control goes 0 to 30 (default 21; **24 chosen**, since a yell reached -2.7 dB at 26) with hardware auto-gain off; the gain resets on reboot. Plugging it in power-cycled the Pi. Still to do: the typewriter idle-noise test with it and the fan.
