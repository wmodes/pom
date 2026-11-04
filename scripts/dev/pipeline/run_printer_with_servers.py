"""Test bench: start every server the Printer needs, run the Printer on them, and stop what it started.

The Printer (`python -m pom`) listens to whisper-server and talks to two llama-servers, the scorer model and the oracle
model. All three stay loaded in memory at once; that is the design (see "All the models stay loaded" in
documentation/Design Document.md). This script starts the three, waits until each answers, runs `python -m pom` on
them in the foreground, and when the Printer ends (or you press Ctrl-C) stops the servers it started.

Every option it does not use itself goes straight to the Printer, so anything `python -m pom` takes works here:

    python scripts/dev/pipeline/run_printer_with_servers.py --mac-mic --terminal console
    python scripts/dev/pipeline/run_printer_with_servers.py --wav data/recordings/work/zoom-oct5_2026.wav --speed 6 --terminal none

The only option read here is `--config`, to learn the servers' ports from the same file the Printer reads (it is passed on too).
`-h` and `--help` skip the servers and show the Printer's own help.

A server that already answers on its port is used as it is, and left running afterwards; only what this script started is stopped.
The first run downloads the two models (a few GB) through llama-server's cache, so it can take a while; later runs load them from disk.
The servers' own logs go to a temporary folder, removed on exit unless a server failed to start (then its path is printed).
Run it from anywhere, with the project's Python environment active (it needs `whisper-server` and `llama-server` on the PATH).

These launch commands are the Mac's. The Pi's systemd units run the same servers with the same ports from the same config.
"""
import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from pom.config import Config                                              # noqa: E402  (after the path is set)

WHISPER_MODEL = ROOT / "data" / "whisper" / "ggml-base.en.bin"
WHISPER_VAD_MODEL = ROOT / "data" / "whisper" / "ggml-silero-v6.2.0.bin"
SCORER_MODEL = "unsloth/Qwen3-4B-GGUF:Q4_K_M"
ORACLE_MODEL = "unsloth/Qwen3.5-9B-GGUF:Q4_K_M"

READY_SECONDS = 900               # first run downloads the models; a loaded one answers in well under a minute
STOP_SECONDS = 10                 # how long a server gets to shut down before it is killed
CHECK_SECONDS = 1.0


class Server:
    """One server program: how to start it, where it answers, and the process once started."""

    def __init__(self, name: str, port: int, command: list, health_path: str, needs_ok: bool):
        self.name, self.port, self.command = name, port, command
        self.url = f"http://127.0.0.1:{port}{health_path}"
        self.needs_ok = needs_ok               # llama-server answers 503 while loading and 200 when ready; whisper answers 200 or 404
        self.process = None
        self.log_path = None

    def state(self) -> str:
        """'ready', 'loading' (something answers but is not ready) or 'down'."""
        try:
            with urllib.request.urlopen(self.url, timeout=2) as response:
                return "ready" if response.status == 200 or not self.needs_ok else "loading"
        except urllib.error.HTTPError as error:       # it answered, with an error status
            return "ready" if not self.needs_ok else ("loading" if error.code == 503 else "down")
        except (urllib.error.URLError, OSError):
            return "down"


def build_servers(config: Config) -> list:
    """The three servers, with the ports the Printer's config says it will look for them on."""
    def port_of(url: str) -> int:
        return urlparse(url).port

    return [
        Server("whisper", port_of(config.listening.whisper_url),
               ["whisper-server", "-m", str(WHISPER_MODEL), "--vad", "-vm", str(WHISPER_VAD_MODEL), "-vt", "0.5",
                "--host", "127.0.0.1", "--port", str(port_of(config.listening.whisper_url)), "-t", "4"], "/", needs_ok=False),
        Server("scorer", port_of(config.scorer.url),
               ["llama-server", "-hf", SCORER_MODEL, "--host", "127.0.0.1", "--port", str(port_of(config.scorer.url)),
                "--ctx-size", "4096", "-np", "2", "--jinja"], "/health", needs_ok=True),
        Server("oracle", port_of(config.oracle.url),
               ["llama-server", "-hf", ORACLE_MODEL, "--host", "127.0.0.1", "--port", str(port_of(config.oracle.url)),
                "--ctx-size", "4096", "-np", "1", "-ngl", "99", "--jinja"], "/health", needs_ok=True),
    ]


def start_missing(servers: list, log_dir: Path) -> list:
    """Start each server that is not already up; return those started. A server found up is used as it is."""
    started = []
    for server in servers:
        state = server.state()
        if state != "down":
            print(f"[bench] {server.name}: already answering on port {server.port} ({state}); using it, will leave it running", flush=True)
            continue
        server.log_path = log_dir / f"{server.name}.log"
        # Its own session, so a Ctrl-C meant for the Printer does not reach it: it is stopped by this script, after the Printer is done.
        server.process = subprocess.Popen(server.command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=open(server.log_path, "wb"),
                                          stderr=subprocess.STDOUT, start_new_session=True)
        started.append(server)
        print(f"[bench] {server.name}: started on port {server.port} (pid {server.process.pid})", flush=True)
    return started


def wait_until_ready(servers: list) -> bool:
    """Wait for every server to answer. False if one died or the time ran out (and say which)."""
    deadline = time.monotonic() + READY_SECONDS
    waiting = list(servers)
    while waiting:
        for server in list(waiting):
            if server.process is not None and server.process.poll() is not None:
                print(f"[bench] {server.name} stopped on its own (exit code {server.process.returncode}); its log is {server.log_path}", flush=True)
                return False
            if server.state() == "ready":
                print(f"[bench] {server.name}: ready", flush=True)
                waiting.remove(server)
        if waiting and time.monotonic() > deadline:
            print(f"[bench] gave up after {READY_SECONDS} s waiting for: {', '.join(s.name for s in waiting)}", flush=True)
            return False
        if waiting:
            time.sleep(CHECK_SECONDS)
    return True


def stop(started: list) -> None:
    """Stop what this script started: ask politely, then insist."""
    for server in started:
        if server.process.poll() is None:
            server.process.terminate()
    for server in started:
        try:
            server.process.wait(timeout=STOP_SECONDS)
        except subprocess.TimeoutExpired:
            server.process.kill()
            server.process.wait()
    if started:
        print(f"[bench] stopped {', '.join(s.name for s in started)}", flush=True)


def run_printer(arguments: list) -> int:
    """Run `python -m pom` on this terminal and return its exit code; a SIGTERM to this script is passed on to it."""
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [str(ROOT / "src"), os.environ.get("PYTHONPATH", "")])))
    printer = subprocess.Popen([sys.executable, "-m", "pom", *arguments], cwd=ROOT, env=environment)
    signal.signal(signal.SIGTERM, lambda signum, frame: printer.send_signal(signal.SIGTERM))
    signal.signal(signal.SIGINT, signal.SIG_IGN)          # Ctrl-C reaches the Printer itself; this script waits for it to finish
    return printer.wait()


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config")
    options, _ = parser.parse_known_args()
    arguments = sys.argv[1:]

    if "-h" in arguments or "--help" in arguments:
        return run_printer(arguments)

    for program in ("whisper-server", "llama-server"):
        if shutil.which(program) is None:
            print(f"[bench] {program} is not on the PATH (on the Mac: brew install whisper.cpp llama.cpp)", file=sys.stderr)
            return 2
    for model in (WHISPER_MODEL, WHISPER_VAD_MODEL):
        if not model.exists():
            print(f"[bench] missing {model.relative_to(ROOT)} (scripts/setup/setup-whisper.sh fetches it)", file=sys.stderr)
            return 2

    servers = build_servers(Config.load(options.config) if options.config else Config.load())
    log_dir = Path(tempfile.mkdtemp(prefix="pom-bench-"))
    started, keep_logs = [], False
    try:
        started = start_missing(servers, log_dir)
        if not wait_until_ready(servers):
            keep_logs = True
            return 1
        print("[bench] all three servers answer; starting the Printer", flush=True)
        return run_printer(arguments)
    finally:
        stop(started)
        if keep_logs:
            print(f"[bench] logs kept in {log_dir}", flush=True)
        else:
            shutil.rmtree(log_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
