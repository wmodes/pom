#!/usr/bin/env python3
"""Pi benchmark: how fast and how heavy is the interest Scorer on the target hardware?

Part of experiments/pi-benchmark. A llama-server holding one model is already running (the runner
script, benchmark_pi_run.sh, starts it); this script scores the sample sentences against it the
way the Scorer will (one sentence per request, no context, the probe's prompt), and records what
the machine was doing meanwhile.

For every call it records the wall time and the server's own prompt timings (how many prompt
tokens were reused from the cache). Alongside, a sampler records the server process's memory, the
memory still available to the system, the SoC temperature, and the Pi's throttling flags. All of
those degrade quietly to "not available" off a Pi, so this runs on a Mac for testing.

Usage:
    benchmark_pi_scorer.py run --label NAME --phase PHASE --port PORT --out DIR
                               [--sample small|large] [--pid SERVER_PID] [--repeats N]
                               [--compare-scores BAKEOFF.json]
    benchmark_pi_scorer.py report DIR [--target-p95 4.0] [--max-whisper-slowdown 1.5]

`run` writes DIR/<label>__<phase>.json. The small sample is the probe's own sentences (Wes's), two
passes by default: quick, and the same lines every time. The large sample is sample-large.txt (a few
hundred sentences from the corpus, one pass by default): a long sustained run, which is where heat,
memory creep and slow outliers show up. `report` reads every result in DIR (scorer runs and the
whisper logs from benchmark_pi_whisper.py), prints a summary with PASS/CHECK flags against the
thresholds, and saves it as DIR/report.txt. The two thresholds are proposals, not requirements.

The score is computed exactly as the probe computes it (the expected value of the digit the model
starts its reply with); --compare-scores checks that against a saved bake-off result so the two
cannot drift apart. It uses only the standard library.
"""
import argparse
import importlib.util
import json
import math
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE = Path(os.environ.get("PROBE_PATH", HERE.parent / "interest-scorer" / "probe_interest_scorer.py"))

# Bits of `vcgencmd get_throttled`, current (low) and since boot (high, 16 bits up).
THROTTLE_BITS = {0: "under-voltage", 1: "arm frequency capped", 2: "currently throttled",
                 3: "soft temperature limit"}


def load_probe():
    """Imports the probe so the benchmark uses exactly its prompt and sample sentences."""
    spec = importlib.util.spec_from_file_location("probe_interest_scorer", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- Looking at the machine ---------------------------------------------------------------------

def _command(*args):
    """Output of a short command, or None if it is missing or fails. Never raises."""
    try:
        done = subprocess.run(args, capture_output=True, text=True, timeout=5)
        return done.stdout.strip() if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def rss_mb(pid):
    """Resident memory of a process in MB, or None."""
    if pid is None:
        return None
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    out = _command("ps", "-o", "rss=", "-p", str(pid))          # macOS, for testing
    try:
        return int(out) / 1024 if out else None
    except ValueError:
        return None


def mem_available_mb():
    """Memory the system could still hand out, in MB (Linux only), or None."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return None


def soc_temp_c():
    """SoC temperature in degrees C from vcgencmd (Pi only), or None."""
    out = _command("vcgencmd", "measure_temp")                   # temp=48.3'C
    try:
        return float(out.split("=")[1].split("'")[0]) if out else None
    except (IndexError, ValueError):
        return None


def throttled_flags():
    """The Pi's throttling bitmask from vcgencmd (0 means never throttled), or None."""
    out = _command("vcgencmd", "get_throttled")                  # throttled=0x0
    try:
        return int(out.split("=")[1], 16) if out else None
    except (IndexError, ValueError):
        return None


def describe_throttling(flags):
    """Human words for a throttling bitmask."""
    if flags is None:
        return "n/a"
    if flags == 0:
        return "none"
    now = [name for bit, name in THROTTLE_BITS.items() if flags & (1 << bit)]
    since = [name for bit, name in THROTTLE_BITS.items() if flags & (1 << (bit + 16))]
    parts = []
    if now:
        parts.append("now: " + ", ".join(now))
    if since:
        parts.append("since boot: " + ", ".join(since))
    return "; ".join(parts) or f"0x{flags:x}"


class ResourceSampler(threading.Thread):
    """Records the server's memory, the system's free memory and the temperature once a second."""

    def __init__(self, pid, interval=1.0):
        super().__init__(daemon=True)
        self.pid, self.interval, self.samples = pid, interval, []
        self._done = threading.Event()

    def run(self):
        while not self._done.is_set():
            self.samples.append({"t": time.time(), "rss_mb": rss_mb(self.pid),
                                 "mem_available_mb": mem_available_mb(), "temp_c": soc_temp_c()})
            self._done.wait(self.interval)

    def stop(self):
        self._done.set()
        self.join(timeout=5)


# --- Scoring ------------------------------------------------------------------------------------

def timed_call(url, prompt, line):
    """Scores one sentence and returns what the call cost. Never raises: a failure is recorded."""
    body = {
        "messages": [{"role": "system", "content": prompt},
                     {"role": "user", "content": f"Rate this line: {line}"}],
        "max_tokens": 1, "temperature": 0, "logprobs": True, "top_logprobs": 20,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    request = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            data = json.load(response)
    except Exception as error:                                   # a dead server is a result, not a crash
        return {"seconds": time.perf_counter() - start, "score": None, "error": repr(error)[:160]}
    seconds = time.perf_counter() - start
    weights = {}
    for entry in data["choices"][0]["logprobs"]["content"][0]["top_logprobs"]:
        token = entry["token"].strip()
        if len(token) == 1 and token in "123456789":
            weights[int(token)] = weights.get(int(token), 0.0) + math.exp(entry["logprob"])
    total = sum(weights.values())
    timings = data.get("timings") or {}
    return {"seconds": seconds,
            "score": sum(d * w for d, w in weights.items()) / total if total else None,
            "prompt_n": timings.get("prompt_n"), "cache_n": timings.get("cache_n"),
            "prompt_ms": timings.get("prompt_ms"), "predicted_ms": timings.get("predicted_ms")}


def pick_lines(probe, sample):
    """The sentences to score: the probe's own (small) or the fixed large file next to this script."""
    if sample == "small":
        return getattr(probe, "BENCHMARK", None) or probe.DEFAULT_LINES      # the probe's list has been renamed once
    path = HERE / "sample-large.txt"
    lines = [row.strip() for row in path.read_text(encoding="utf-8").splitlines() if row.strip()]
    if not lines:
        sys.exit(f"{path} is empty; make it with benchmark_pi_make_sample.py")
    return lines


def run(args):
    """Scores the sample sentences `repeats` times and saves the calls and the machine samples."""
    probe = load_probe()
    url = f"http://127.0.0.1:{args.port}/v1/chat/completions"
    lines = pick_lines(probe, args.sample)
    repeats = args.repeats or (2 if args.sample == "small" else 1)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # The first call after a server starts pays for loading the model into memory and filling the
    # prompt cache. It is recorded on its own so it does not blur the steady-state numbers.
    warmup = timed_call(url, probe.SINGLE_PROMPT, lines[0])

    sampler = ResourceSampler(args.pid)
    sampler.start()
    flags_before = throttled_flags()
    started = time.time()
    calls = []
    for repeat in range(repeats):
        for index, line in enumerate(lines):
            calls.append({"repeat": repeat, "line_index": index, **timed_call(url, probe.SINGLE_PROMPT, line)})
    elapsed = time.time() - started
    sampler.stop()

    record = {"label": args.label, "phase": args.phase, "sample": args.sample, "port": args.port, "repeats": repeats,
              "lines": len(lines), "elapsed_seconds": elapsed, "warmup": warmup, "calls": calls,
              "samples": sampler.samples, "throttled_before": flags_before,
              "throttled_after": throttled_flags()}
    path = out_dir / f"{args.label}__{args.phase}.json"
    path.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    good = [c["seconds"] for c in calls if c.get("score") is not None]
    print(f"{args.label} / {args.phase}: {len(good)}/{len(calls)} calls ok, "
          f"median {statistics.median(good) * 1000:.0f} ms" if good else f"{args.label} / {args.phase}: no calls succeeded")
    if args.compare_scores:
        reference = json.loads(Path(args.compare_scores).read_text(encoding="utf-8"))["scores"]
        gaps = [abs(c["score"] - reference[lines[c["line_index"]]]) for c in calls
                if c.get("score") is not None and c["repeat"] == 0 and lines[c["line_index"]] in reference]
        print(f"  largest score difference from {Path(args.compare_scores).name}: "
              f"{max(gaps):.4f} over {len(gaps)} lines" if gaps else "  no overlapping lines to compare")


# --- Reporting ----------------------------------------------------------------------------------

def percentile(values, fraction):
    """Nearest-rank percentile of a non-empty list."""
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))]


def summarize(record):
    """Reduces one scorer run to the numbers the report prints."""
    ok = [c for c in record["calls"] if c.get("score") is not None]
    seconds = [c["seconds"] for c in ok]
    cached = sum(c.get("cache_n") or 0 for c in ok)
    fresh = sum(c.get("prompt_n") or 0 for c in ok)
    samples = record["samples"]
    rss = [s["rss_mb"] for s in samples if s["rss_mb"] is not None]
    avail = [s["mem_available_mb"] for s in samples if s["mem_available_mb"] is not None]
    temps = [s["temp_c"] for s in samples if s["temp_c"] is not None]
    return {
        "ok": len(ok), "total": len(record["calls"]), "warmup": record["warmup"]["seconds"],
        "median": statistics.median(seconds) if seconds else None,
        "p95": percentile(seconds, 0.95) if seconds else None,
        "slowest": max(seconds) if seconds else None,
        "cache_share": cached / (cached + fresh) if (cached + fresh) else None,
        "rss_peak": max(rss) if rss else None, "avail_min": min(avail) if avail else None,
        "temp_max": max(temps) if temps else None, "throttled": record.get("throttled_after"),
    }


def fmt(value, template, missing="n/a"):
    """Formats a number, or says it is not available."""
    return template.format(value) if value is not None else missing


def report(args):
    """Prints and saves the benchmark summary for a results folder."""
    folder = Path(args.dir)
    scorer, whisper_alone, whisper_load = [], {}, {}
    for path in sorted(folder.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("kind") == "alone":
            whisper_alone[(data["model"], data["threads"])] = data
        elif data.get("kind") == "load":
            whisper_load[data["phase"]] = data
        elif "calls" in data:
            scorer.append(data)
    if not scorer and not whisper_alone:
        sys.exit(f"no results in {folder}")

    def window_seconds(data):
        """Whisper times for one phase, leaving out the first (cold) iteration."""
        steady = data["seconds"][1:] or data["seconds"]
        return steady

    out = [f"Pi benchmark: {folder.name}", "",
           f"(proposed thresholds: scorer p95 <= {args.target_p95:g} s, whisper slowdown <= "
           f"{args.max_whisper_slowdown:g}x; seconds are per sentence)", ""]
    out.append("scorer runs:")
    out.append("  model        phase                               warmup  median     p95  slowest  cached  "
               "rss MB  free MB  temp  throttling")
    problems = []
    for record in sorted(scorer, key=lambda r: (r["label"], r["phase"])):
        s = summarize(record)
        verdict = ""
        if s["p95"] is not None and s["p95"] > args.target_p95:
            verdict = "  CHECK p95"
            problems.append(f"{record['label']} / {record['phase']}: p95 {s['p95']:.2f} s")
        if s["ok"] < s["total"]:
            verdict += f"  CHECK {s['total'] - s['ok']} failed"
            problems.append(f"{record['label']} / {record['phase']}: {s['total'] - s['ok']} calls failed")
        out.append(f"  {record['label']:<12} {record['phase']:<35} {fmt(s['warmup'], '{:6.1f}')} "
                   f"{fmt(s['median'], '{:7.2f}')} {fmt(s['p95'], '{:7.2f}')} {fmt(s['slowest'], '{:8.2f}')}  "
                   f"{fmt(s['cache_share'] and s['cache_share'] * 100, '{:4.0f}%')}  "
                   f"{fmt(s['rss_peak'], '{:6.0f}')}  {fmt(s['avail_min'], '{:7.0f}')}  "
                   f"{fmt(s['temp_max'], '{:4.0f}')}  {describe_throttling(s['throttled'])}{verdict}")

    out += ["", "whisper (seconds per transcription of the sample clip; whisper always encodes a full window):"]
    out.append("  whisper model   threads   alone mean   with scorer mean   slowdown   phase")
    for phase, load in sorted(whisper_load.items()):
        base = whisper_alone.get((load["model"], load["threads"]))
        loaded = statistics.mean(window_seconds(load))
        alone = statistics.mean(base["seconds"][1:] or base["seconds"]) if base else None
        slowdown = loaded / alone if alone else None
        verdict = ""
        if slowdown is not None and slowdown > args.max_whisper_slowdown:
            verdict = "  CHECK"
            problems.append(f"whisper {load['model']} in {phase}: {slowdown:.2f}x slower with the scorer running")
        out.append(f"  {load['model']:<14} {load['threads']:>7}   {fmt(alone, '{:10.2f}')}   {loaded:16.2f}   "
                   f"{fmt(slowdown, '{:7.2f}x')}   {phase}{verdict}")
    out += ["", "PASS: nothing exceeded the thresholds." if not problems else
            "CHECK: " + "; ".join(problems) + "."]
    text = "\n".join(out) + "\n"
    (folder / "report.txt").write_text(text, encoding="utf-8")
    print(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="score the sample sentences against a running server")
    run_parser.add_argument("--label", required=True, help="model name for the results")
    run_parser.add_argument("--phase", required=True, help="what else was running, for the results")
    run_parser.add_argument("--port", type=int, required=True)
    run_parser.add_argument("--out", required=True, help="results folder")
    run_parser.add_argument("--pid", type=int, help="server process id, to record its memory")
    run_parser.add_argument("--sample", choices=("small", "large"), default="small",
                            help="small: the probe's sentences; large: sample-large.txt")
    run_parser.add_argument("--repeats", type=int, help="passes over the sample (default 2 small, 1 large)")
    run_parser.add_argument("--compare-scores", help="a bake-off result JSON to compare the scores with")
    report_parser = commands.add_parser("report", help="summarize a results folder")
    report_parser.add_argument("dir")
    report_parser.add_argument("--target-p95", type=float, default=4.0)
    report_parser.add_argument("--max-whisper-slowdown", type=float, default=1.5)
    args = parser.parse_args()
    run(args) if args.command == "run" else report(args)


if __name__ == "__main__":
    main()
