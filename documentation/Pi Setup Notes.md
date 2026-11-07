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
| Network | Wi-Fi on the home network, MAC `88:a2:9e:87:88:41` (DHCP: 192.168.4.22 on 2026-10-18, 192.168.4.95 on 2026-11-04) |
| Updates | `apt full-upgrade` done 2026-10-18 (6 packages, no reboot needed) |
| Bootloader | EEPROM 2026-09-25 (updated 2026-10-18) |
| Wi-Fi fallback | Comitup 1.47.1 (hotspot `pom1-direct`, portal at `http://10.41.0.1`); tested 2026-10-18 |
| Remote access | Tailscale (installed 2026-11-06, `scripts/setup/setup-tailscale.sh`): the monitor and a shell from anywhere, by the name `pom1`; see "Remote access (Tailscale)" |
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

### When `pom1.local` will not resolve (diagnosed 2026-11-04)

The installation has to work on whatever Wi-Fi it is given, so a name that does not resolve is a case to understand, not to work around. On 2026-11-04 `ssh pi@pom1.local` stopped working from the Mac while the Pi was running normally. What was found, in the order that narrowed it down:

1. **Was the Pi down?** No. The journal is persistent, so `journalctl -b -1` (the boot before the one you power-cycled into) shows it ran to the last second: the four services never restarted, Wi-Fi stayed associated, the DHCP lease renewed every 2 hours, and there were no kernel, memory, SD-card or Wi-Fi errors. (Whisper's "vad time" in its log is a cumulative counter, not a per-clip time; it climbs by about 42 s an hour and is not a stall.)
2. **Reach it without the name.** `ssh -o HostKeyAlias=pom1.local pi@192.168.4.95` checks the Pi against the host key already saved for `pom1.local`, so the identity is still verified when only the address is known. Find the address with the ARP sweep above. If the typewriter is on, the address is also on the boot slip it types each time the Pi comes online (the `LINE 1` line).
3. **Is the Pi's mDNS responder working?** Ask it directly, over unicast, so no multicast is involved: `dig +short @192.168.4.95 -p 5353 pom1.local` answered `192.168.4.95`. Avahi had joined `224.0.0.251` on `wlan0` and registered the name.
4. **Does mDNS work between machines on this Wi-Fi?** Use a third machine. `lpfm` (another Pi) resolved `pom1.local` and the Mac's name, and `pom1` resolved `lpfm.local` (`avahi-resolve -n lpfm.local`, or `getent hosts lpfm.local`, which is the resolver programs really use). So the network carries multicast, and the Pi is fine.
5. **So the Mac is the one that cannot.** `ping pom1.local` and `ping lpfm.local` both fail from the Mac, inside and outside Claude Code's sandbox, after `sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder`, and after turning Wi-Fi off and on. `scutil --dns` is normal (`.local` is handed to mDNS). `dns-sd -B _services._dns-sd._udp local.` finds **no Bonjour service at all**, not even the SSH entries the Pis advertise, so the Mac is receiving no mDNS from anyone. (Run `dns-sd` under `script -q /dev/null dns-sd ...`: redirected to a file it buffers its output and looks empty.)

**Not the cause, as tested:** the Pi's Wi-Fi power saving (it was on, but Pi-to-Pi lookups worked 5 of 5 with it on and 5 of 5 with it off); the Mac's application firewall (off); the Pi's avahi; Claude Code's sandbox.

**Still open: why the Mac receives no mDNS.** Suspects, none confirmed: (a) the Mac's weaker signal (-70 dBm against -54 for the Pis, both on channel 36) or a different mesh node, since the router is an eero and multicast can fail between nodes while unicast works; (b) the Cisco Secure Client agents on the Mac (Umbrella, Zero Trust, Network Visibility), which are known to interfere with `.local` and Bonjour on managed Macs; the eero also answers a plain `http://192.168.4.1` with a redirect to its `blocked.eero.com` page. The cheap tests that tell these apart: take the Mac next to the Pis and try `ping lpfm.local` again; try a phone on the same Wi-Fi; look in Cisco Secure Client for which modules are on. A packet capture on the Mac needs root (`sudo tcpdump -i en0 -n udp port 5353`).

**Working when it fails:**
- Use the address with `HostKeyAlias` (step 2). The address is DHCP and can change; a DHCP reservation in the router keeps it, but that is a convenience for one house, not a fix.
- The Pi's own hotspot (`pom1-direct`, portal at `http://10.41.0.1`) needs no mDNS and no router; see the Comitup section.
- Nothing in the Printer itself depends on mDNS: the Pi talks to its own servers on `127.0.0.1`, and the monitoring page is reached by address.

**Wi-Fi power saving is turned off on the Pi** (`scripts/setup/networkmanager/90-pom-wifi.conf`, linked into `/etc/NetworkManager/conf.d/` by `scripts/setup/install-services.sh`). A radio that sleeps between the access point's beacons can miss broadcast and multicast traffic, and the Printer runs unattended. `sudo iw dev wlan0 get power_save` reports the live state. It was on, by default, until 2026-11-04.

**Diagnostic tools installed on pom1 on 2026-11-04:** `tcpdump`, `iw` (in `/usr/sbin`, so use `sudo /usr/sbin/iw`) and `avahi-utils` (`avahi-browse`, `avahi-resolve`). Watching mDNS from the Pi: `sudo tcpdump -i wlan0 -n udp port 5353`.

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

**Design decision (2026-10-18): half-duplex.** The machine listens, and only types when a TBD threshold is met. It does not need to process overheard audio *while* the typewriter is typing. So whisper and the LLM never *compute* at the same time (all the models stay loaded in memory throughout; the Pi is the 16 GB one for that reason, see the design doc, "All the models stay loaded, always"):
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
library is nearly all (the heavy folders `data/` and `library/raw` are gitignored and not in a clone).

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
- `data/tuning/` a scratch git worktree that follows the throwaway `prompt-tuning` branch while prompts are being
  tuned (`experiments/hearing-debug/probe_hearing_tune.sh` on the Mac); it is removed when the run ends

The tools were first built in the home folder and moved here on 2026-10-23. A built binary found its libraries
through an absolute path, so they were rebuilt in place, and the setup scripts now build with a relative library
path (`$ORIGIN`) so the folder can be moved again without rebuilding. Keep the Pi's clone clean (no edits in tracked files), so `git pull` always works; `benchmark_pi_prepare.sh check` says if it is not.

## Running the Printer as a service (installed 2026-11-03)

The Printer and its three model servers are systemd services, and their unit files live in the repo and are linked into place, so a `git pull` rewrites
them. The files and the scripts are in `scripts/setup/` (its README describes each).

| Service | What | Notes |
|---|---|---|
| `pom-whisper` | whisper-server, `base.en`, whisper's own voice detection, port 8110 | |
| `pom-scorer` | llama-server, Qwen3-4B, port 8098, `-np 2` | |
| `pom-oracle` | llama-server, Qwen3.5-9B, port 8099, `-np 1` | loads in about 20 s once the files are in the page cache; about a minute cold |
| `pom` | `python -m pom` | after and wants the three servers (never requires them) |

**Install (once) and update (after every change):**

```
cd ~/pom && git pull && scripts/setup/install-services.sh --start     # first time: link, enable, start the four
scripts/setup/update-pi.sh                                           # afterwards: pull, relink, restart only what changed
```

`install-services.sh` first checks every flag in the server units against each server's own `--help` (a flag this llama.cpp does not know made the
first scorer crash-loop), links the units with `systemctl link`, makes the journal persistent (the Pi ships it volatile; `scripts/setup/journald/90-pom.conf`,
500 MB cap), reloads, enables, and restarts only the services whose unit changed, so the models stay loaded. `journalctl -u pom -f` follows the Printer;
the log holds counts, labels and seconds, never anything said.

**The Pi's own `config/pom.local.toml`** (gitignored; not in the repo) holds the monitor's password and `[terminal] mode = "none"` while no typewriter is
connected. The monitoring page is at `http://pom1.local:8080` (any user name). Set `mode = "serial"` (or remove the section) once the CadetWriter is plugged in.

**Memory: the models are loaded with `-lm mmap -lzm off`, not pinned.** Measured on the Pi 2026-11-03 with whisper, the scorer and the oracle all loaded and idle:
about 7.7 GB of the 16 GB available (the oracle holds 9.2 GB resident, 5.0 anonymous and 4.2 mapped file; the scorer 5.1 GB). Pinning the models in RAM
(`-lm mmap+mlock`) was tried and rejected: this ARM build keeps a repacked copy of the weights, so it pinned the memory twice and left 0.9 GB available, close
to the OOM-killer. The oracle alone: 10.6 GB available with `mmap`, 10.1 GB with `mlock`, 6.1 GB with `mmap+mlock`.

**Tried and working:** killing the oracle with SIGKILL, systemd restarted it and it was answering again in about 32 s; the Printer never stopped and the monitor's
service lights stayed green. The microphone is open (`arecord -D plughw:0,0`). **Reboot (2026-11-03): passed.** All four services came up by themselves, with no restarts and no warnings. From the moment SSH answered (about 27 s after the
command) all three servers were answering within about 90 s (a cold load from the SD card), the Printer had started first and logged "scorer NOT answering, oracle
NOT answering" and then saw them come up, the monitor's lights were green, 7.1 GB was available, and the journal had kept the previous boot (`journalctl --list-boots`).
The microphone gain was still 24 of 30 with auto gain off: ALSA saves the mixer state at shutdown and restores it at boot, so it does not reset on a clean reboot
(the start-up step stays as a check that sets it if it differs). Not yet tried: the typewriter, and a real voice.

## Remote access (Tailscale)

*Installed 2026-11-06.* On the same network the Pi is reachable as `pom1.local` (`ssh pi@pom1.local`, `http://pom1.local:8080`). Away from it (a phone on a cafe's cellular or Wi-Fi) it
is reached through Tailscale: a private network between the devices signed in to one account. Each runs a small client and gets a private address and a name (`pom1`), they dial out to
Tailscale's coordination service, and they connect to each other directly when they can and through encrypted relays when they cannot. Nothing on the Pi listens on the open
internet and no router or firewall changes are needed, which matters behind a venue's or the lab's network.

- **Install:** `scripts/setup/setup-tailscale.sh`, run on the Pi as `pi`. It adds Tailscale's own apt repository for the OS (`debian` or `raspbian`, by release name), installs the package,
  enables `tailscaled` at boot, and signs the Pi in. Signing in prints a link; open it on any device, sign in to the tailnet's account (use two-factor on that account, since
  whoever controls it controls which devices join), and approve the Pi once. Safe to run again.
- **Name resolution is left alone.** The script sets `--accept-dns=false`, so Tailscale never rewrites the Pi's `resolv.conf` (NetworkManager's, pointing at the router). The names such as `pom1`
  work on the phone and the Mac, which is where they are wanted. This was chosen because of the venue and lab DNS trouble described above.
- **What it does not touch:** the Printer and the three model servers (they listen on localhost only), Comitup (in hotspot mode Tailscale simply goes offline and returns with Wi-Fi), `pom1.local`
  on the LAN, and the update flow. Only SSH (22) and the monitor (8080) are open to the network, as before; they are now also reachable on the tailnet address. It uses little memory
  (tens of MB) and `pom.service` does not depend on it.
- **From a phone:** install the Tailscale app, sign in to the same account, switch it on, then open `http://pom1:8080` (type the `http://`; the full name or the `100.x` address, both shown in the app, work
  if the short one does not). For a shell, use an SSH app to `pom1` as `pi` with a key. Tested from a phone on cellular, 2026-11-06.
- **Not turned on:** Tailscale SSH (it would let the account log in without keys), exit nodes, and subnet routing (which would also enable IP forwarding). Each is a separate decision.
- **Device keys expire** after a while by default (about 6 months); an expired Pi shows as such in Tailscale's admin console and is re-approved there, or the expiry is disabled for that device.
- **Undo:** `sudo tailscale down`, `sudo apt remove tailscale`, and remove the Pi from the admin console.
- **Another Pi in the lab (`lpfm`, not part of this repo)** was put on the same tailnet the same way the same day, so from `pom1` it is reachable as `ssh pi@lpfm` too.

## The display (kiosk)

*Installed 2026-11-07.* A monitor on the Pi's HDMI port (HDMI-A-2, 1920x1080) shows the monitoring page full-screen. The Pi is headless (Bookworm Lite, boots to `multi-user.target`),
so this is `cage` (a one-window Wayland compositor) running Chromium in kiosk mode, as its own service, `pom-display.service`. Design: see the Design Document, "The Printer's own screen is a kiosk".

- **Installed by:** `scripts/setup/setup-kiosk.sh` (apt: `cage` 0.2.0, `chromium` 156 from Raspberry Pi's repository, a font; nothing compiled), then `update-pi.sh` links the unit and the udev rule. About 300 MB of RAM
  while it runs (available memory 2.3 GB before, 2.0 GB after, with all three models loaded); the models are not touched.
- **What it shows:** `http://127.0.0.1:8080/` with no password, because `[monitor] password_on_localhost = false` lets the Pi's own machine in. Checked 2026-11-07: `curl http://127.0.0.1:8080/` is 200, and the same page by the Pi's LAN address
  from the Pi is 401 (the password is still asked of everyone else).
- **No monitor is not an error:** its start condition (`display-attached.sh`) skips the unit when no display connector says `connected`. Cage exits when its last monitor is unplugged, and the udev rule starts the unit when one is plugged in.
  *Not yet tested on the real machine:* unplugging the monitor while it runs and plugging it back in.
- **A mouse** works as in any browser (qualifier dots and turn rows are links; "all turns" goes back). **A keyboard:** ctrl-alt-F1 is the console login (cage runs with `-s`, which allows switching).
- **Look:** `systemctl status pom-display`; `journalctl -u pom-display -f`; `ps -eo pid,rss,args | grep -E 'cage|chromium'`. A blank screen with the unit active: check `journalctl -u pom-display` and that the page answers (`curl http://127.0.0.1:8080/`).
- **Settings** are in `[display]` in `config/pom.toml` (browser, flags, profile folder). Not yet needed: a zoom, if the page is too small at 1920x1080 on the real screen.
- **A trap found on the first install:** the cage wiki's unit has `TTYVHangup=yes`; with the `chvt 7` that runs after cage starts, the hang-up fired again and killed cage with SIGHUP (the service "succeeded" and ended at once, with nothing in the log).
  Found with throwaway units (`systemd-run`) that each differed in one thing; the unit now has no `TTYVHangup`, and a test pins it.

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

- [ ] **Why the Mac cannot resolve `.local` names** (2026-11-04): the Pis and the network are fine; the Mac sees no Bonjour services at all. See "When `pom1.local` will not resolve". Next test: the Mac beside the Pis, and a phone.
- [x] **Bootloader EEPROM update.** Done 2026-10-18: 2025-08-28 -> 2026-09-25 via `sudo rpi-eeprom-update -a` (`VERIFY: SUCCESS`), then reboot. `rpi-eeprom-update` now reports up to date.
- [x] **Wi-Fi fallback AP for venues.** Done 2026-10-18 with Comitup (see the section above). Still untested: whether it rescans for known networks while the hotspot is up, and the real venue Wi-Fi flow.
- [x] Fan verified 2026-10-18: during the llama.cpp build the Pi reached 52.7 C and `fan1_input` read 2794 RPM (it is off at idle).
- [ ] Case: stacked acrylic / open-frame case that explicitly supports Pi 5 + official Active Cooler (top window open, power button and fan header accessible).
- [~] Mic: Audio-Technica ATR4697-USB is wired, not wireless as the project brief assumed. Verified 2026-10-23: it shows up as `card 0: ATR4697USB`, records with `arecord -D plughw:0,0 -f S16_LE -r 16000 -c 1`, and its ALSA `Mic` capture control goes 0 to 30 (default 21; **24 chosen**, since a yell reached -2.7 dB at 26) with hardware auto-gain off; the gain resets on reboot. Plugging it in power-cycled the Pi. Still to do: the typewriter idle-noise test with it and the fan.
