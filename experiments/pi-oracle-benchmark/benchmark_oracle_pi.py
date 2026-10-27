#!/usr/bin/env python3
"""Pi oracle benchmark: how long does the oracle model take to write a portent on the target hardware?

Part of experiments/pi-oracle-benchmark. The runner script (benchmark_oracle_pi_run.sh) starts the servers; this
script times the oracle model against them and writes down what the machine was doing meanwhile.

THE QUESTION: with whisper and the 4B scorer loaded but idle (the design is half-duplex: nothing is sent to them while
the oracle works), how long does the 9B take to load, to start writing, and to finish a portent?

It runs the REAL modules: the PromptBuilder makes the prompt (the frozen prompt and settings from config/pom.toml) and
the real OracleModelClient asks the model. The only difference from the Printer is the transport: the client's `post`
is replaced by a streaming one (StreamTimer) so the time to the first token can be measured. The client's request
body is untouched; "stream": true is added.

The cells (hinge and draw) are the bake-off's: every third benchmark hinge, draws 1 and 2, with the same seeds, so
the exemplars are the same ones the Mac saw. One warm-up portent is made first and not counted.

What is kept: counts, timings and memory, never the portents themselves (a portent is not kept, as nothing said in
a room is).

Usage:
    benchmark_oracle_pi.py wait   --url URL --pid PID --phase P --what WHAT --out DIR [--timeout S]
    benchmark_oracle_pi.py run    --phase P --port PORT --out DIR [--watch NAME=PID ...]
    benchmark_oracle_pi.py report DIR

`wait` polls a server's /health and records how long it took to be ready (startup.jsonl). `run` makes the portents and
writes DIR/<phase>.jsonl and DIR/<phase>-samples.jsonl. `report` prints the summary and saves DIR/report.txt.
It uses only the standard library, apart from the project's own modules.
"""
import argparse
import hashlib
import json
import math
import random
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments" / "oracle-playground"))
sys.path.insert(0, str(ROOT / "experiments" / "oracle-bakeoff"))
sys.path.insert(0, str(ROOT / "experiments" / "pi-benchmark"))
from pom.config import Config                                      # noqa: E402
from pom.corpus import ChunkStore, DrawWeights, ExemplarSelector   # noqa: E402
from pom.log_setup import configure_logging                        # noqa: E402
from pom.prompt import PromptBuilder                               # noqa: E402
from pom.voice import OracleModelClient                            # noqa: E402
from run_oracle_bakeoff import (HINGES, corpus_fingerprint, draw_seed, git_state,   # noqa: E402
                                read_hinges)
from benchmark_pi_scorer import (describe_throttling, mem_available_mb, rss_mb,     # noqa: E402
                                 soc_temp_c, throttled_flags)

CONFIG_PATH = ROOT / "config" / "pom.toml"
CHUNKS = ROOT / "corpus" / "chunks"
HINGE_STRIDE = 3        # every third bake-off hinge ...
DRAWS = (1, 2)          # ... at these draws: 6 hinges x 2 draws = 12 cells
WARM_UP_CELL_INDEX = 1  # the warm-up portent uses the hinge at this position (not one of the counted ones)


# --- The cells -------------------------------------------------------------------------------------

def pick_cells(hinges: list, stride: int = HINGE_STRIDE, draws: Iterable = DRAWS) -> list:
    """The (hinge, draw) cells to time: every `stride`-th hinge, starting with the first, at each of `draws`."""
    return [(hinge, draw) for hinge in hinges[::stride] for draw in draws]


# --- Streaming the reply, to time the first token --------------------------------------------------

@dataclass
class StreamResult:
    """What one streamed reply held.

    Attributes:
        text: The whole reply, joined from the pieces.
        finish_reason: "stop", "length" or None if the stream gave none.
        first_token_seconds: Seconds from the request to the first piece of text, or None if there was none.
        timings: The server's own `timings` block from the last chunk (prompt_n, cache_n, predicted_n, ...), or None.
    """
    text: str = ""
    finish_reason: Optional[str] = None
    first_token_seconds: Optional[float] = None
    timings: Optional[dict] = field(default=None)


def read_stream(lines: Iterable, clock: Callable[[], float], started: float) -> StreamResult:
    """Reads a server-sent-events reply from llama-server (lines of `data: {json}`, ending `data: [DONE]`).

    Anything that is not a data line, or not JSON, is skipped: a stream that is odd must not break the timing run.

    Args:
        lines: The reply's lines, bytes or text.
        clock: Returns the current time in seconds.
        started: The clock reading when the request was sent.
    """
    result = StreamResult()
    pieces = []
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except ValueError:
            continue
        if isinstance(chunk.get("timings"), dict):
            result.timings = chunk["timings"]
        choices = chunk.get("choices") or [{}]
        text = (choices[0].get("delta") or {}).get("content")
        if text:
            if result.first_token_seconds is None:
                result.first_token_seconds = clock() - started
            pieces.append(text)
        if choices[0].get("finish_reason"):
            result.finish_reason = choices[0]["finish_reason"]
    result.text = "".join(pieces)
    return result


class StreamTimer:
    """A replacement `post` for OracleModelClient that streams the reply and remembers how it went.

    The client calls it as post(url, body, timeout) and expects the parsed reply of a normal (non-streamed)
    request, so the reply is rebuilt in that shape. The time to the first token and the server's timings are kept in
    `last` for the benchmark to read after the client returns.

    Attributes:
        last: The StreamResult of the most recent request, or None before the first.
    """

    def __init__(self, clock: Callable[[], float] = time.perf_counter):
        self._clock = clock
        self.last: Optional[StreamResult] = None

    def __call__(self, url: str, body: dict, timeout: float) -> dict:
        request = urllib.request.Request(url, json.dumps({**body, "stream": True}).encode("utf-8"),
                                         {"Content-Type": "application/json"})
        started = self._clock()
        with urllib.request.urlopen(request, timeout=timeout) as response:      # nosec: the client checked the scheme
            self.last = read_stream(response, self._clock, started)
        return {"choices": [{"message": {"content": self.last.text}, "finish_reason": self.last.finish_reason}]}


# --- Looking at the machine meanwhile --------------------------------------------------------------

class ServerSampler(threading.Thread):
    """Once a second, records each watched server's memory, the memory still free, the temperature and throttling."""

    def __init__(self, watch: dict, interval: float = 1.0):
        """Args:
            watch: Names and process ids of the servers to watch, e.g. {"oracle-9b": 1234, "scorer-4b": 1200}.
            interval: Seconds between samples.
        """
        super().__init__(daemon=True)
        self._watch, self._interval, self.samples = watch, interval, []
        self._done = threading.Event()

    def run(self):
        while not self._done.is_set():
            self.samples.append({"t": round(time.time(), 1),
                                 "rss_mb": {name: rss_mb(pid) for name, pid in self._watch.items()},
                                 "mem_available_mb": mem_available_mb(), "temp_c": soc_temp_c(),
                                 "throttled": throttled_flags()})
            self._done.wait(self._interval)

    def stop(self):
        self._done.set()
        self.join(timeout=5)


def summarize_samples(samples: list) -> dict:
    """Peak memory per watched server, the least free memory, the hottest temperature, any throttling seen.

    Missing readings (None, off a Pi) are ignored. Throttle flags from every sample are combined, so a flag that
    appeared at any moment shows.
    """
    peak_rss = {}
    for sample in samples:
        for name, value in (sample.get("rss_mb") or {}).items():
            if value is not None:
                peak_rss[name] = max(peak_rss.get(name, 0.0), value)
    free = [s["mem_available_mb"] for s in samples if s.get("mem_available_mb") is not None]
    temps = [s["temp_c"] for s in samples if s.get("temp_c") is not None]
    flags = [s["throttled"] for s in samples if s.get("throttled") is not None]
    combined = 0
    for value in flags:
        combined |= value
    return {"samples": len(samples), "peak_rss_mb": {n: round(v) for n, v in peak_rss.items()},
            "min_mem_available_mb": round(min(free)) if free else None,
            "peak_temp_c": max(temps) if temps else None,
            "throttled": describe_throttling(combined if flags else None)}


# --- Summarizing the portents ----------------------------------------------------------------------

def percentile(values: list, fraction: float) -> Optional[float]:
    """The nearest-rank percentile (fraction 0.95 is the 95th), or None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(fraction * len(ordered)) - 1, 0)]


def summarize_portents(records: list) -> dict:
    """Median, 95th percentile and worst of the times, and the medians of the rest, over the portents that were made."""
    made = [r for r in records if r.get("words")]

    def column(key):
        return [r[key] for r in made if r.get(key) is not None]

    seconds = column("seconds")
    return {"requested": len(records), "made": len(made),
            "seconds_median": statistics.median(seconds) if seconds else None,
            "seconds_p95": percentile(seconds, 0.95), "seconds_max": max(seconds) if seconds else None,
            "first_token_median": statistics.median(column("first_token_seconds")) if column("first_token_seconds") else None,
            "prompt_tokens_median": statistics.median(column("prompt_tokens")) if column("prompt_tokens") else None,
            "tokens_per_second_median": (statistics.median(column("tokens_per_second"))
                                         if column("tokens_per_second") else None),
            "words_median": statistics.median(column("words")) if column("words") else None,
            "cut_off": sum(1 for r in made if r.get("cut_off"))}


def portent_record(phase: str, hinge: str, draw: int, client, timer: StreamTimer, portent: Optional[str]) -> dict:
    """One line for the results file: the cell, the times and token counts, and the word count, never the portent."""
    stream = timer.last
    timings = (stream.timings if stream is not None else None) or {}
    prompt_ms, predicted_n = timings.get("prompt_ms"), timings.get("predicted_n")
    return {"type": "portent", "phase": phase, "hinge": hinge, "draw": draw,
            "seconds": round(client.last_seconds, 2) if client.last_seconds is not None else None,
            "first_token_seconds": (round(stream.first_token_seconds, 2)
                                    if stream is not None and stream.first_token_seconds is not None else None),
            "prompt_tokens": timings.get("prompt_n"), "cached_tokens": timings.get("cache_n"),
            "prompt_seconds": round(prompt_ms / 1000, 2) if prompt_ms is not None else None,
            "tokens": predicted_n, "tokens_per_second": (round(timings["predicted_per_second"], 2)
                                                        if timings.get("predicted_per_second") is not None else None),
            "words": len(portent.split()) if portent else 0, "cut_off": bool(client.last_cut_off)}


# --- The commands ----------------------------------------------------------------------------------

def command_wait(args) -> None:
    """Polls a server's /health until it says ok, and records how long that took."""
    started = time.perf_counter()
    while True:
        try:
            with urllib.request.urlopen(args.url, timeout=2) as reply:
                if b'"ok"' in reply.read():
                    break
        except (urllib.error.URLError, OSError, ValueError):
            pass
        if time.perf_counter() - started > args.timeout:
            sys.exit(f"{args.what}: not ready after {args.timeout:.0f} s")
        time.sleep(0.5)
    seconds = round(time.perf_counter() - started, 1)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "startup.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"phase": args.phase, "what": args.what, "seconds": seconds}) + "\n")
    print(f"{args.what}: ready after {seconds} s")


def command_run(args) -> None:
    """Makes the portents for the cells through the real modules, timing each, and writes the phase's files."""
    configure_logging("WARNING")
    config = Config.load(CONFIG_PATH)
    oracle = config.oracle
    rng = random.Random()
    store = ChunkStore.load(CHUNKS)
    selector = ExemplarSelector(store, DrawWeights.from_file(CHUNKS / "weights.json"), rng,
                                exemplar_range=config.exemplar_range)
    if not selector.ready:
        sys.exit("nothing to draw exemplars from; see the messages above")
    builder = PromptBuilder(oracle.prompt, selector)
    if not builder.available:
        sys.exit("there is no oracle prompt in the config")
    hinges = read_hinges(HINGES)
    cells = pick_cells(hinges)
    url = urllib.parse.urlparse(oracle.url)._replace(netloc=f"127.0.0.1:{args.port}").geturl()
    timer = StreamTimer()
    client = OracleModelClient(url, oracle.timeout, oracle.max_tokens, oracle.temperature, post=timer)
    watch = dict(item.split("=", 1) for item in args.watch)
    watch = {name: int(pid) for name, pid in watch.items()}

    def make(hinge: str, draw: int) -> dict:
        rng.seed(draw_seed(hinge, draw))                # the bake-off's own seed for this cell
        built = builder.build(hinge)
        portent = client.portent(built.text) if built is not None else None
        return portent_record(args.phase, hinge, draw, client, timer, portent)

    run = {"type": "run", "phase": args.phase, **git_state(), "temperature": oracle.temperature,
           "max_tokens": oracle.max_tokens, "exemplar_range": list(config.exemplar_range),
           "prompt_sha256": hashlib.sha256(oracle.prompt.encode("utf-8")).hexdigest()[:12],
           "corpus": corpus_fingerprint(store), "cells": len(cells), "watched": sorted(watch)}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"{args.phase}: warm-up portent (not counted) ...", flush=True)
    make(hinges[min(WARM_UP_CELL_INDEX, len(hinges) - 1)], DRAWS[0])
    sampler = ServerSampler(watch)
    sampler.start()
    records = []
    try:
        with (out / f"{args.phase}.jsonl").open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(run) + "\n")
            for number, (hinge, draw) in enumerate(cells, start=1):
                record = make(hinge, draw)
                records.append(record)
                handle.write(json.dumps(record) + "\n")
                handle.flush()
                print(f"{args.phase} {number}/{len(cells)}  {record['seconds']} s total, "
                      f"{record['first_token_seconds']} s to first token, {record['tokens_per_second']} tokens/s, "
                      f"{record['words']} words", flush=True)
    except KeyboardInterrupt:
        print("\nstopped early; the lines written so far are kept")
    finally:
        sampler.stop()
    summary = {"type": "summary", "phase": args.phase, **summarize_portents(records),
               "resources": summarize_samples(sampler.samples)}
    with (out / f"{args.phase}.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(summary) + "\n")
    with (out / f"{args.phase}-samples.jsonl").open("w", encoding="utf-8") as handle:
        for sample in sampler.samples:
            handle.write(json.dumps(sample) + "\n")


def _fmt(value, digits: int = 1, unit: str = "") -> str:
    """A number for the report, or n/a."""
    return "n/a" if value is None else f"{value:.{digits}f}{unit}"


def build_report(directory: Path) -> str:
    """The report for a run directory: startup times, then per phase the timings and the machine's state."""
    lines = [f"Pi oracle benchmark: {directory.name}", ""]
    startup = directory / "startup.jsonl"
    if startup.exists():
        lines.append("Server startup (seconds until it answered /health):")
        for row in (json.loads(l) for l in startup.read_text(encoding="utf-8").splitlines() if l.strip()):
            lines.append(f"  {row['phase']:<8} {row['what']:<28} {row['seconds']:>6.1f} s")
        lines.append("")
    for path in sorted(directory.glob("*.jsonl")):
        if path.name in ("startup.jsonl",) or path.name.endswith("-samples.jsonl"):
            continue
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        run = next((r for r in rows if r.get("type") == "run"), {})
        summary = next((r for r in rows if r.get("type") == "summary"), None)
        lines.append(f"Phase {run.get('phase', path.stem)}  (prompt {run.get('prompt_sha256')}, corpus {run.get('corpus')}, "
                     f"commit {run.get('commit')}{' + uncommitted' if run.get('uncommitted') else ''})")
        if summary is None:
            lines += ["  no summary: the run was cut short", ""]
            continue
        resources = summary["resources"]
        lines += [f"  portents made: {summary['made']} of {summary['requested']}; cut off at max_tokens: {summary['cut_off']}",
                  f"  total seconds: median {_fmt(summary['seconds_median'])}, 95th percentile {_fmt(summary['seconds_p95'])}, "
                  f"slowest {_fmt(summary['seconds_max'])}",
                  f"  seconds to first token (median): {_fmt(summary['first_token_median'])}",
                  f"  prompt tokens (median): {_fmt(summary['prompt_tokens_median'], 0)}; "
                  f"writing speed (median): {_fmt(summary['tokens_per_second_median'])} tokens/s; "
                  f"words (median): {_fmt(summary['words_median'], 0)}",
                  f"  memory: peak per server {resources['peak_rss_mb']} MB; least free {_fmt(resources['min_mem_available_mb'], 0)} MB",
                  f"  hottest {_fmt(resources['peak_temp_c'])} C; throttling: {resources['throttled']}", ""]
    return "\n".join(lines)


def command_report(args) -> None:
    directory = Path(args.directory)
    text = build_report(directory)
    (directory / "report.txt").write_text(text + "\n", encoding="utf-8")
    print(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    wait = commands.add_parser("wait", help="wait for a server to be ready and record how long it took")
    wait.add_argument("--url", required=True)
    wait.add_argument("--phase", required=True)
    wait.add_argument("--what", required=True)
    wait.add_argument("--out", required=True)
    wait.add_argument("--timeout", type=float, default=900)
    wait.set_defaults(func=command_wait)
    run = commands.add_parser("run", help="make the portents for the cells and time them")
    run.add_argument("--phase", required=True)
    run.add_argument("--port", type=int, required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--watch", action="append", default=[], metavar="NAME=PID")
    run.set_defaults(func=command_run)
    report = commands.add_parser("report", help="summarize a run directory")
    report.add_argument("directory")
    report.set_defaults(func=command_report)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
