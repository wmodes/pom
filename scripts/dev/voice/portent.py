#!/usr/bin/env python3
"""Play with the oracle: type a hinge, get a portent, from the real PromptBuilder and OracleModelClient.

Nothing here is its own version of the oracle: the prompt comes from `oracle.prompt` in config/pom.toml (and
config/pom.local.toml), the exemplars are drawn by the real ExemplarSelector, the prompt is built by the real
PromptBuilder, and the portent comes from the real OracleModelClient. What you see here is what the Printer will do when it
is wired up. The only thing this script adds is the typing of the hinge, which the trigger supplies.

It starts a llama-server for the model you name, on the port of `oracle.url`, and stops it when you quit. Use `--server` to
talk to one that is already running instead.

Same hinge, same exemplars. Before each hinge the random generator is reseeded from a hash of the hinge, so the same hinge
gets the same exemplars (and the same number of them) whichever model is running; that is what makes two models
comparable. `--seed N` replaces the hash, to get different exemplars for the same hinge.

Usage:
    portent.py --model ~/.cache/huggingface/hub/.../Qwen3.5-9B-Q4_K_M.gguf
    portent.py --model unsloth/Qwen3.5-9B-GGUF:Q4_K_M       # a Hugging Face model, downloaded on first use
    portent.py --server                                      # use the llama-server that is already running
    portent.py --server --seed 7                             # other exemplars for the same hinge

Type a hinge at the prompt and press return; an empty line (or ctrl-D) quits. Edit the prompt in the config and type the
hinge again to see the effect: the config is read again before every hinge.
"""
import argparse
import hashlib
import os
import random
import shutil
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
from itertools import zip_longest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from pom.config import Config                                      # noqa: E402
from pom.corpus import ChunkStore, DrawWeights, ExemplarSelector   # noqa: E402
from pom.log_setup import configure_logging                        # noqa: E402
from pom.prompt import PromptBuilder                               # noqa: E402
from pom.voice import OracleModelClient                            # noqa: E402

CONFIG_PATH = ROOT / "config" / "pom.toml"
CHUNKS = ROOT / "corpus" / "chunks"
CONTEXT_SIZE = 4096


def seed_for(hinge: str) -> int:
    """A seed taken from the hinge. sha256, not hash(): hash() is different in every run."""
    return int(hashlib.sha256(hinge.strip().encode("utf-8")).hexdigest()[:8], 16)


def exemplar_columns(chunks, width: int) -> list:
    """Lays the exemplars out two to a row, side by side, each headed by its id and wrapped to its column."""
    column = max((width - 3) // 2, 20)

    def cell(chunk) -> list:
        lines = [chunk.id, "-" * min(len(chunk.id), column)]
        for line in chunk.text.splitlines():                  # verse keeps its line breaks
            lines += textwrap.wrap(line, column) or [""]
        return lines

    rows = []
    for start in range(0, len(chunks), 2):
        left = cell(chunks[start])
        right = cell(chunks[start + 1]) if start + 1 < len(chunks) else []
        rows += [f"{a:<{column}} | {b}".rstrip() for a, b in zip_longest(left, right, fillvalue="")]
        rows.append("")
    return rows


def start_server(model: str, url: str) -> subprocess.Popen:
    """Starts a llama-server for the model on the port in the oracle url and waits until it answers."""
    binary = os.environ.get("LLAMA_SERVER") or shutil.which("llama-server")
    if not binary:
        sys.exit("llama-server not found (brew install llama.cpp, or set LLAMA_SERVER)")
    port = urllib.parse.urlparse(url).port
    source = ["-m", model] if Path(model).expanduser().exists() else ["-hf", model]    # a file, else a Hugging Face name
    if source[0] == "-m":
        source[1] = str(Path(model).expanduser())
    process = subprocess.Popen([binary, *source, "--port", str(port), "--ctx-size", str(CONTEXT_SIZE), "-ngl", "99", "--jinja"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    health = f"http://127.0.0.1:{port}/health"
    print(f"starting llama-server for {model} on port {port} (a first download can take a while) ...", flush=True)
    while True:
        if process.poll() is not None:
            sys.exit("the llama-server exited at startup (is the port already in use?)")
        try:
            with urllib.request.urlopen(health, timeout=2):
                return process
        except (urllib.error.URLError, OSError):
            time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", help="a .gguf file, or a Hugging Face model like unsloth/Qwen3.5-9B-GGUF:Q4_K_M")
    parser.add_argument("--server", action="store_true", help="use the llama-server that is already running")
    parser.add_argument("--seed", type=int, default=None, help="use this seed instead of one taken from the hinge")
    args = parser.parse_args()
    if bool(args.model) == bool(args.server):
        sys.exit("give --model to start a llama-server for that model, or --server to use one that is running")

    configure_logging("WARNING")
    config = Config.load(CONFIG_PATH)
    rng = random.Random()
    selector = ExemplarSelector(ChunkStore.load(CHUNKS), DrawWeights.from_file(CHUNKS / "weights.json"), rng,
                                exemplar_range=config.exemplar_range)
    if not selector.ready:
        sys.exit("nothing to draw exemplars from; see the messages above")

    server = None if args.server else start_server(args.model, config.oracle.url)
    try:
        while True:
            try:
                hinge = input("\nhinge> ").strip()
            except EOFError:
                break
            if not hinge:
                break
            oracle = Config.load(CONFIG_PATH).oracle                       # read again, so prompt edits show at once
            builder = PromptBuilder(oracle.prompt, selector)
            client = OracleModelClient(oracle.url, oracle.timeout, oracle.max_tokens, oracle.temperature)
            rng.seed(args.seed if args.seed is not None else seed_for(hinge))
            prompt = builder.build(hinge)
            if prompt is not None:
                print("\n".join(exemplar_columns(prompt.exemplars, shutil.get_terminal_size().columns)) or "exemplars: none")
                portent = client.portent(prompt.text)
                if portent is not None:
                    print(f"\n{portent}\n")
                    print(f"({len(portent.split())} words, {client.last_seconds:.1f} s"
                          f"{', cut off at max_tokens' if client.last_cut_off else ''})")
            for line in [*builder.report.lines(), *client.report.lines()]:
                print(line)
    except KeyboardInterrupt:
        print()
    finally:
        if server is not None:
            server.terminate()
            server.wait(timeout=15)


if __name__ == "__main__":
    main()
