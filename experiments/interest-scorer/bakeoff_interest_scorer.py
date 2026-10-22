#!/usr/bin/env python3
"""Bake-off: the same interest-scoring test, run against several models, then compared.

Part of experiments/interest-scorer. One model is held in a running llama-server (the runner
script, bakeoff_interest_scorer.sh, starts them one at a time); this script runs the test against
it, or compares the saved results of several models.

The rules are fixed so every model gets exactly the same test:
  * batch size 1: each sentence is a request of its own
  * no context: the model never sees any other sentence
  * the prompt is SINGLE_PROMPT and the sentences are the BENCHMARK list, both read from
    probe_interest_scorer.py (written by Wes), and both saved next to the results
  * the score is the probe's: the expected value of the digit the model starts its reply with
Every line is scored twice, so the results also show whether the scores repeat.

Usage:
    bakeoff_interest_scorer.py run LABEL PORT RESULTS_DIR   # score the lines with the server on PORT
    bakeoff_interest_scorer.py report RESULTS_DIR           # compare the saved models

It uses only the standard library.
"""
import argparse
import hashlib
import importlib.util
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE = HERE / "probe_interest_scorer.py"


def load_probe():
    """Imports the probe script as a module, so the bake-off scores with exactly its code."""
    spec = importlib.util.spec_from_file_location("probe_interest_scorer", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sample_lines(probe) -> list:
    """The probe's sample sentences, whichever name it currently gives the list."""
    return getattr(probe, "BENCHMARK", None) or probe.DEFAULT_LINES


def run(label: str, port: int, results_dir: Path) -> None:
    """Scores every line twice with the server on `port` and saves LABEL.json in `results_dir`."""
    probe = load_probe()
    lines = sample_lines(probe)
    url = f"http://127.0.0.1:{port}/v1/chat/completions"
    results_dir.mkdir(parents=True, exist_ok=True)

    def score_all():
        scores, seconds = {}, []
        for line in lines:
            score, took = probe.rate(url, probe.SINGLE_PROMPT, line, [])    # no context, ever
            scores[line] = score
            seconds.append(took)
        return scores, seconds

    first, seconds = score_all()
    second, _ = score_all()
    differences = [abs(first[line] - second[line]) for line in first if first[line] == first[line]]
    record = {
        "label": label,
        "prompt_sha1": hashlib.sha1(probe.SINGLE_PROMPT.encode("utf-8")).hexdigest()[:10],
        "scores": first,
        "unscored": sum(score != score for score in first.values()),
        "max_difference_between_passes": max(differences, default=0.0),
        "ms_mean": statistics.mean(seconds) * 1000,
        "ms_slowest": max(seconds) * 1000,
    }
    (results_dir / f"{label}.json").write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    # The test itself, written once per results folder so the folder is self-describing.
    (results_dir / "PROMPT.txt").write_text(probe.SINGLE_PROMPT.strip() + "\n", encoding="utf-8")
    (results_dir / "LINES.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{label}: scored {len(first)} lines, {record['ms_mean']:.0f} ms each")


def ranks(scores: dict) -> dict:
    """Position of each line when sorted by score (ties broken by order, which is fine here)."""
    return {line: place for place, line in enumerate(sorted(scores, key=lambda k: scores[k]))}


def rank_agreement(a: dict, b: dict) -> float:
    """Spearman rank correlation between two models' scores for the same lines (1 = same order)."""
    ra, rb, n = ranks(a), ranks(b), len(a)
    return 1 - 6 * sum((ra[line] - rb[line]) ** 2 for line in a) / (n * (n * n - 1))


def report(results_dir: Path) -> None:
    """Prints, and saves as report.txt, a summary table, the agreement, and each model's ranking."""
    found = {path.stem: json.loads(path.read_text(encoding="utf-8"))
             for path in sorted(results_dir.glob("*.json"))}
    if not found:
        sys.exit(f"no results in {results_dir}")
    labels = list(found)
    out = [f"Interest-scoring bake-off: {results_dir.name}", "",
           "model          repeats (max diff)  mean ms  slowest ms  unscored  score range"]
    for label in labels:
        d = found[label]
        values = [v for v in d["scores"].values() if v == v]
        out.append(f"{label:<14} {d['max_difference_between_passes']:>18.3f}  {d['ms_mean']:>7.0f}  "
                   f"{d['ms_slowest']:>10.0f}  {d['unscored']:>8}  {min(values):.2f} - {max(values):.2f}")
    if len(labels) > 1:
        out += ["", "rank agreement (1.00 = same order):"]
        for i, first in enumerate(labels):
            for second in labels[i + 1:]:
                out.append(f"  {first} vs {second}: "
                           f"{rank_agreement(found[first]['scores'], found[second]['scores']):+.2f}")
    for label in labels:
        out += ["", f"=== {label}, best first ==="]
        for line, score in sorted(found[label]["scores"].items(), key=lambda kv: -kv[1]):
            out.append(f"{score:5.2f}  {line}")
    text = "\n".join(out) + "\n"
    (results_dir / "report.txt").write_text(text, encoding="utf-8")
    print(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="score the lines with one running server")
    run_parser.add_argument("label")
    run_parser.add_argument("port", type=int)
    run_parser.add_argument("results_dir", type=Path)
    report_parser = commands.add_parser("report", help="compare the models saved in a results folder")
    report_parser.add_argument("results_dir", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        run(args.label, args.port, args.results_dir)
    else:
        report(args.results_dir)


if __name__ == "__main__":
    main()
