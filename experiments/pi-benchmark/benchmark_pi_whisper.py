#!/usr/bin/env python3
"""Whisper load for the Pi benchmark: transcribe a clip over and over, and time each pass.

Part of experiments/pi-benchmark. The Scorer will run while whisper.cpp is transcribing the room,
so the benchmark needs whisper busy in the background, and it needs to know how much the Scorer
slows whisper down. Two modes:

    benchmark_pi_whisper.py alone --model base.en --threads 4 --out DIR [--iterations 5]
        Whisper by itself, a few passes, as the baseline. Writes DIR/whisper-alone__<model>-t<threads>.json.

    benchmark_pi_whisper.py load --model base.en --threads 4 --phase NAME --out DIR [--max-seconds 1800]
        Whisper back to back until the file DIR/STOP appears (or the time limit passes), as the load
        under the Scorer. Writes DIR/whisper__<phase>.json after every pass, so a crash loses nothing.

Back-to-back transcribing is the worst case: a real room leaves whisper idle between utterances.
Whisper encodes a full 30 s window whatever the clip length, so the 11 s sample clip costs about what
a real window costs. The first pass includes loading the model and is left out of the averages.

Where things are (the defaults match scripts/setup/setup-whisper.sh):
    WHISPER_DIR  checkout location        (default <repo>/data/tools/whisper.cpp)
    WHISPER_CLI  the whisper-cli binary   (default $WHISPER_DIR/build/bin/whisper-cli)
    WHISPER_WAV  the clip to transcribe   (default $WHISPER_DIR/samples/jfk.wav)

It uses only the standard library.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def settings(model: str):
    """(whisper-cli, model file, wav) from the environment, exiting with a clear message if missing."""
    root = Path(os.environ.get("WHISPER_DIR", Path(__file__).resolve().parents[2] / "data" / "tools" / "whisper.cpp"))
    cli = Path(os.environ.get("WHISPER_CLI", root / "build" / "bin" / "whisper-cli"))
    model_file = root / "models" / f"ggml-{model}.bin"
    wav = Path(os.environ.get("WHISPER_WAV", root / "samples" / "jfk.wav"))
    for label, path in (("whisper-cli", cli), ("model", model_file), ("clip", wav)):
        if not path.exists():
            sys.exit(f"whisper {label} not found: {path} (see scripts/setup/setup-whisper.sh)")
    return cli, model_file, wav


def transcribe_once(cli: Path, model_file: Path, wav: Path, threads: int):
    """Transcribes the clip once. Returns (seconds, succeeded)."""
    start = time.perf_counter()
    done = subprocess.run([str(cli), "-m", str(model_file), "-f", str(wav), "-t", str(threads), "-nt"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return time.perf_counter() - start, done.returncode == 0


def save(path: Path, record: dict) -> None:
    path.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")


def alone(args) -> None:
    cli, model_file, wav = settings(args.model)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    seconds, failures = [], 0
    for _ in range(args.iterations):
        took, ok = transcribe_once(cli, model_file, wav, args.threads)
        seconds.append(took)
        failures += 0 if ok else 1
    save(out / f"whisper-alone__{args.model}-t{args.threads}.json",
         {"kind": "alone", "model": args.model, "threads": args.threads, "seconds": seconds, "failures": failures})
    print(f"whisper alone ({args.model}, {args.threads} threads): "
          + ", ".join(f"{s:.1f}s" for s in seconds))


def load(args) -> None:
    cli, model_file, wav = settings(args.model)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stop_file = out / "STOP"
    deadline = time.time() + args.max_seconds          # never leave the Pi pegged if the runner dies
    seconds, failures = [], 0
    while not stop_file.exists() and time.time() < deadline:
        took, ok = transcribe_once(cli, model_file, wav, args.threads)
        seconds.append(took)
        failures += 0 if ok else 1
        save(out / f"whisper__{args.phase}.json",
             {"kind": "load", "phase": args.phase, "model": args.model, "threads": args.threads,
              "seconds": seconds, "failures": failures})
    print(f"whisper load {args.phase}: {len(seconds)} passes")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="mode", required=True)
    alone_parser = commands.add_parser("alone", help="whisper by itself, as the baseline")
    load_parser = commands.add_parser("load", help="whisper back to back until told to stop")
    for sub in (alone_parser, load_parser):
        sub.add_argument("--model", default="base.en", help="whisper model name (default base.en)")
        sub.add_argument("--threads", type=int, default=4)
        sub.add_argument("--out", required=True, help="results folder")
    alone_parser.add_argument("--iterations", type=int, default=5)
    load_parser.add_argument("--phase", required=True, help="name of the benchmark phase")
    load_parser.add_argument("--max-seconds", type=float, default=1800.0)
    args = parser.parse_args()
    alone(args) if args.mode == "alone" else load(args)


if __name__ == "__main__":
    main()
