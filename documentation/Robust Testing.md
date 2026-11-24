# Robust Testing

*Started 2026-11-24.* **Robust** is one of the design principles, and it has a concrete meaning here: **anyone can unplug, kill, switch off or lose anything, at any moment during a run, and the Printer carries on or comes back by itself.** It may go quiet while a part is missing, and it says so in its log, but it never stops, never needs a restart, and never loses its place. This page is the list of those tests: what was tried, where, what happened, and what has not been tried yet.

## How a test is done

1. Do it **on pom1 with the real part**, not a stand-in, wherever a real part can be had. The Mac and a pseudo-terminal are for finding bugs early; they do not count as the real test.
2. Watch the Printer while it happens: `ssh pi@pom1.local journalctl -u pom -f` (and the monitoring page's four lights).
3. Do the unkind thing, wait, undo it, wait again. Write down: **what was seen** (the log lines, the lights, the paper), **how long it took to recover**, and whether anyone had to do anything.
4. A test that fails is a bug. Fix it with a test, then repeat the real-part test.
5. Record it below with the date. Never mark a row passed from reading the code.

Status: **passed** (tried for real, recovered by itself), **found a bug** (fixed, see the date), **not tried**.

## The parts

| Part | Unkind thing | Status | What happened / what to watch for |
|---|---|---|---|
| The microphone | Boot with the sound cards numbered differently | **found a bug**, fixed 2026-11-24 | After a reboot the HDMI outputs took cards 0 and 1 and the microphone was card 2; `device = "plughw:0,0"` failed every 5 s and the Printer heard nothing. Fixed by `device = "auto"` (see CHANGELOG). The next reboot put the microphone on card 0 and it was found with no errors. |
| The microphone | Unplug while running, plug it back in | **not tried** | Expected: "cannot hear" logged, the room rebuilt every `restart_pause_seconds`, the card looked up again each time, hearing back within one pause of the replug. The log (INFO) says "sound card found after N tries" and then the setting, what was available and what was chosen; only the first `device_search_log_limit` misses are logged. |
| The microphone and the terminal | Two of them plugged in at once (a webcam or headset, a second USB-serial adapter) | **fixed in code 2026-11-24 (H10, H11); not tried for real** | With `auto` the first in sorted order is used and a WARNING says what was chosen and what else was there; a name (`device = "ATR4697"`, `port = "Prolific"`) chooses one. Try on pom1: plug a second USB audio or serial device, reboot, read the journal. |
| The microphone | Unplug and plug a different model | **not tried** | Expected: `auto` finds the new card by its name. |
| The microphone | Boot with no microphone, plug it in later | **not tried** | Expected: warning "no sound card that can record", then hearing as soon as it is plugged in. |
| The terminal (serial adapter) | Unplug mid-portent, plug back in | **passed on a pseudo-terminal only** (the Mac, replugged under another name: resumed at the exact character) | **Not tried on the real adapter and printer.** Watch for: the portent resumes where it stopped, or is dropped as too late (`stall_patience_seconds`), and says which. |
| The terminal (serial adapter) | Boot with it unplugged, plug it in later | **passed on a pseudo-terminal only** (a printer absent at the start) | Not tried for real. The boot slip is typed once the terminal is online. |
| The terminal | Switch the printer off, or take it off-line (SEL), or run out of paper, mid-portent | **found a bug, fixed 2026-11-24 (Hardening H1); real-typewriter test not tried** | The bug: the end of a portent called `flush()`, which never returns while the machine is held, so a short portent hung the Printer for good. Now it waits until the machine has taken every byte, like a stalled write (patience `stall_patience_seconds`, heartbeat, log lines "stopped taking characters" and "typing again after"), and gives up with a CRITICAL after the patience. Tested on a fake port only. **To try on the real Okidata:** take it off-line (SEL) a moment after a portent starts, wait, put it back on-line. Watch for: the warning in the journal, the rest typed after release, no repeat of the start. Also try holding it past `stall_patience_seconds` and see what the port's close does with the bytes still queued (do they print later?). |
| The terminal | The pace (`characters_per_second`) too fast for the printer's buffer | **passed at 300** (2026-11-23: a 646-character portent, all 14 lines) | The handshake itself under a full buffer is not tried. |
| The oracle, scorer, whisper (the three servers) | Kill one (SIGKILL) | **passed for the oracle** (2026-11-03: systemd restarted it, answering again in about 32 s, the Printer unaffected) | Not tried for the scorer or whisper. |
| The servers | Slow to come up after a boot | **passed** (every reboot; the scorer is warmed up by `ScorerWarmer`, about 14 s) | The log says "NOT answering" for the scorer and oracle for the first seconds, then they are up. |
| The Pi | Full reboot | **passed** (2026-11-03, and again several times on 2026-11-24: all four services up by themselves, boot slip typed) | |
| The Pi | Power cut (pull the plug) | **not tried** | Watch for: the SD card, the services, the log. The BarKeeper's lock is in memory only, so a cut cannot leave it stuck. |
| The Pi | Wi-Fi gone, or the router down | **not tried** | Expected: nothing the Printer does needs the network; only the monitoring page and ssh do. |
| The monitor (kiosk) | Unplug the screen while it runs, plug it back in | **not tried** | See "The display (kiosk)" in Pi Setup Notes. |
| The Printer service (`pom`) | Kill it (SIGKILL) | **not tried** on the Pi | Expected: `Restart=always`, back with the models still loaded (the three servers are separate services). |
| The Printer service | SIGTERM in the middle of a turn | **passed on the Mac** | Not tried on the Pi. |
| Whisper | Down at the start | **passed on the Mac** | Not tried on the Pi. |
| The disk | Full | **not tried** | The journal is capped (see `scripts/setup/journald`). |

## Next

- The weaknesses found by the 2026-11-24 audit, and the fix for each, are in [Hardening TODO.md](Hardening TODO.md); when one is fixed, its row here is updated and, where a real part can be had, tested on pom1.
- Microphone unplug and replug, live, with the journal open (the first row above). Then the serial adapter, mid-portent, with the real printer.
- The paper-out and printer-off test, which is what shows whether the handshake works.
- Then the rest, in the order that a person is most likely to do it by accident: power, then the network, then the monitor.
