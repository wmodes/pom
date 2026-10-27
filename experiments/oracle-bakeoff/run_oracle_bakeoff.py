#!/usr/bin/env python3
"""Runs the oracle bake-off benchmark on one model and saves every portent.

Part of experiments/oracle-bakeoff. For each benchmark hinge (benchmark_hinges.txt) it makes SEEDS draws of
exemplars, builds the prompt with the real PromptBuilder, asks the model for a portent through the real
OracleModelClient, and writes one line per portent to results/<label>.jsonl: the hinge, the exemplars it was
shown, the portent, the time, and the three mechanical checks (bakeoff_checks.py). Rating the portents is done
afterwards, by reading, against documentation/bakeoff-rubric.md.

THE PROMPT AND SETTINGS ARE FROZEN BY BEING RECORDED. Nothing is configured here: the prompt, temperature,
max_tokens and exemplar count come from config/pom.toml, exactly as the Printer will use them, and the first line
of the results file records them (with the prompt text, a fingerprint of the corpus, the git commit and the
llama-server version) so that two results files can be checked to have been made under the same conditions.

SAME DRAWS FOR EVERY MODEL. Before each portent the random generator is reseeded from the hinge and the draw
number ("<hinge>#<n>"), so a given hinge and draw number give the same exemplars whichever model is running.
Draws 1 to SEEDS: each cell of the benchmark is run once, and the spread over the draws is the spread you see.

It starts its own llama-server for the model, on --port (default 8097, so it does not collide with the playground
or the scorer), and stops it at the end. It refuses to start if that port is already in use, because the results
would then be from whatever model is already listening.

Usage:
    run_oracle_bakeoff.py --model unsloth/Llama-3.1-8B-Instruct-GGUF:Q4_K_M
    run_oracle_bakeoff.py --model PATH_TO.gguf --label qwen3-8b
"""
import argparse
import hashlib
import json
import random
import re
import shutil
import socket
import statistics
import subprocess
import sys
import urllib.parse
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "experiments" / "oracle-playground"))
sys.path.insert(0, str(HERE))
from pom.config import Config                                      # noqa: E402
from pom.corpus import ChunkStore, DrawWeights, ExemplarSelector   # noqa: E402
from pom.log_setup import configure_logging                        # noqa: E402
from pom.prompt import PromptBuilder                               # noqa: E402
from pom.voice import OracleModelClient                            # noqa: E402
from probe_oracle import start_server                              # noqa: E402  (the playground's own server start)
from bakeoff_checks import check_portent                           # noqa: E402

CONFIG_PATH = ROOT / "config" / "pom.toml"
CHUNKS = ROOT / "corpus" / "chunks"
HINGES = HERE / "benchmark_hinges.txt"
RESULTS = HERE / "results"
SEEDS = 10
DEFAULT_PORT = 8097


def read_hinges(path: Path) -> list:
    """The benchmark hinges: one per line, blank lines and lines starting with # skipped."""
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


def draw_seed(hinge: str, draw: int) -> str:
    """The seed for one cell. A string, which random.seed hashes the same way on every run (hash() would not)."""
    return f"{hinge.strip()}#{draw}"


def default_label(model: str) -> str:
    """A file-name-safe label from the model: the file's stem, or the repository and quantization of a Hugging Face name."""
    path = Path(model).expanduser()
    name = path.stem if path.exists() else model.split("/")[-1].replace(":", "_")
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name)


def port_in_use(port: int) -> bool:
    """True if something is already listening on the port."""
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def corpus_fingerprint(store: ChunkStore) -> str:
    """A short hash of every chunk and of the draw weights, so a changed corpus is noticed."""
    digest = hashlib.sha256()
    for line in sorted(f"{chunk.id}\t{chunk.text}" for chunk in store):
        digest.update(line.encode("utf-8"))
    digest.update((CHUNKS / "weights.json").read_bytes())
    return digest.hexdigest()[:12]


def git_state() -> dict:
    """The short commit, and whether the config or source has uncommitted changes (then the prompt hash is what pins it)."""
    def git(*args):
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True).stdout.strip()
    return {"commit": git("rev-parse", "--short", "HEAD"), "uncommitted": bool(git("status", "--porcelain", "--", "config", "src"))}


def server_version() -> str:
    """The llama-server's own version line."""
    binary = shutil.which("llama-server") or "llama-server"
    output = subprocess.run([binary, "--version"], capture_output=True, text=True)
    return next((line for line in (output.stdout + output.stderr).splitlines() if line.startswith("version")), "unknown")


def summarize(records: list) -> None:
    """Prints what the mechanical checks and the clock say about the portents of one run."""
    made = [record for record in records if record["portent"] is not None]
    seconds = [record["seconds"] for record in made]
    print(f"\n{len(made)} of {len(records)} portents made")
    if not made:
        return
    print(f"  doesn't repeat the hinge: {sum(not r['auto']['repeats_hinge'] for r in made)}/{len(made)}")
    print(f"  no extra text:            {sum(not r['auto']['extra_text'] for r in made)}/{len(made)}")
    print(f"  length close:             {sum(r['auto']['length_close'] for r in made)}/{len(made)}")
    print(f"  seconds: median {statistics.median(seconds):.1f}, slowest {max(seconds):.1f}; "
          f"cut off at max_tokens: {sum(r['cut_off'] for r in made)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", required=True, help="a .gguf file, or a Hugging Face model like unsloth/Qwen3-8B-GGUF:Q4_K_M")
    parser.add_argument("--label", help="the name of the results file (default: taken from the model)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"the port for this run's llama-server (default {DEFAULT_PORT})")
    args = parser.parse_args()

    label = args.label or default_label(args.model)
    out_path = RESULTS / f"{label}.jsonl"
    if out_path.exists():
        sys.exit(f"{out_path} already exists; delete it to run this model again")
    if port_in_use(args.port):
        sys.exit(f"port {args.port} is already in use; stop that server or pass --port")

    configure_logging("WARNING")
    config = Config.load(CONFIG_PATH)
    oracle = config.oracle
    hinges = read_hinges(HINGES)
    rng = random.Random()
    store = ChunkStore.load(CHUNKS)
    selector = ExemplarSelector(store, DrawWeights.from_file(CHUNKS / "weights.json"), rng,
                                exemplar_range=config.exemplar_range)
    if not selector.ready:
        sys.exit("nothing to draw exemplars from; see the messages above")
    builder = PromptBuilder(oracle.prompt, selector)
    if not builder.available:
        sys.exit("there is no oracle prompt in the config")

    url = urllib.parse.urlparse(oracle.url)._replace(netloc=f"127.0.0.1:{args.port}").geturl()
    client = OracleModelClient(url, oracle.timeout, oracle.max_tokens, oracle.temperature)
    run = {"type": "run", "label": label, "model": args.model, "date": date.today().isoformat(),
           **git_state(), "llama_server": server_version(), "temperature": oracle.temperature,
           "max_tokens": oracle.max_tokens, "exemplar_range": list(config.exemplar_range),
           "prompt_sha256": hashlib.sha256(oracle.prompt.encode("utf-8")).hexdigest()[:12],
           "corpus": corpus_fingerprint(store), "hinges": len(hinges), "draws": SEEDS,
           "prompt": oracle.prompt}

    RESULTS.mkdir(exist_ok=True)
    server = start_server(args.model, url)
    records = []
    try:
        with out_path.open("w", encoding="utf-8") as out:
            out.write(json.dumps(run, ensure_ascii=False) + "\n")
            for hinge in hinges:
                for draw in range(1, SEEDS + 1):
                    rng.seed(draw_seed(hinge, draw))
                    built = builder.build(hinge)
                    portent = client.portent(built.text) if built is not None else None
                    exemplars = [chunk.text for chunk in built.exemplars] if built is not None else []
                    record = {"type": "portent", "hinge": hinge, "draw": draw,
                              "exemplar_ids": [chunk.id for chunk in built.exemplars] if built is not None else [],
                              "exemplars": exemplars, "portent": portent, "seconds": round(client.last_seconds, 2),
                              "cut_off": client.last_cut_off,
                              "auto": check_portent(hinge, portent, exemplars) if portent is not None else None}
                    records.append(record)
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    out.flush()
                    print(f"{hinge[:44]:<44} draw {draw}/{SEEDS}  {record['seconds']:>5.1f} s"
                          f"{'' if portent is not None else '  NO PORTENT'}", flush=True)
    except KeyboardInterrupt:
        print("\nstopped early; the lines written so far are kept")
    finally:
        server.terminate()
        server.wait(timeout=15)
    summarize(records)
    print(f"\nwritten to {out_path}")


if __name__ == "__main__":
    main()
