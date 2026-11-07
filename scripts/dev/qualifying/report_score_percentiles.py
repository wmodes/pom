#!/usr/bin/env python3
"""Report a percentile of the scores each recording earned, and of all of them pooled.

The scores are ones already earned: this does NOT re-score or re-play any recording. It reads the score files the recordings were scored into
(by default every `zoom-*.jsonl` and `live-*-numbers.jsonl` in data/recordings/work/zoom-scores/, the four meetings and the live room of 2026-10-26),
numbers only, no words. Each file is JSON lines with a `score` and, if there is one, a `source` or `by` saying who scored it (`model`, or
`fragment_filter` for a clip the filter scored without asking the model), or a CSV with a `score` column and, if there is one, `scored_by`.

The percentile is read the way the bar reads one: the weighted quantile of the BarKeeper's own bar (`DecayingQuantileBar._quantile`), here with every
score counting equally, so the number is the one a bar would sit at over those scores. It is shown twice: over every score that reached the bar (the
filter's scores are scores the bar receives), and over the model's scores alone.

The number shifts whenever the scorer's prompt or model changes, so re-score the recordings into new score files (the files' dates are shown, to
tell how old a number is) and run this again.

Usage:
    report_score_percentiles.py                          # the 90th percentile of each recording and of all of them together
    report_score_percentiles.py --percentile 95
    report_score_percentiles.py --scores new-oct5.jsonl --scores new-live.csv     # other score files, each named by its file name
"""
import argparse
import csv
import datetime
import glob
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))

from pom.qualifying.decaying_quantile_bar import DecayingQuantileBar  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
SCORES_DIR = os.path.join(ROOT, "data", "recordings", "work", "zoom-scores")
DEFAULT_PATTERNS = ("zoom-*.jsonl", "live-*-numbers.jsonl")     # the four meetings, and the live room
DEFAULT_PERCENTILE = 90.0                                       # (the anchor percentile, once the config has one, is what this should default to)
MODEL = "model"


def label_of(path: str) -> str:
    """A recording's name from its score file's name: `zoom-oct5_2026.jsonl` is `oct5_2026`, `live-oct26-numbers.jsonl` is `live-oct26`."""
    name = os.path.splitext(os.path.basename(path))[0]
    if name.startswith("zoom-"):
        name = name[len("zoom-"):]
    if name.endswith("-numbers"):
        name = name[:-len("-numbers")]
    return name


def read_scores(path: str) -> tuple:
    """(scores, skipped): the (score, who scored it) pairs in a score file, and how many rows had no usable score. Only numbers are read."""
    pairs, skipped = [], 0
    if path.endswith(".csv"):
        with open(path, newline="", encoding="utf-8") as handle:
            rows = [(row.get("score"), row.get("scored_by")) for row in csv.DictReader(handle)]
    else:
        rows = []
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    rows.append((row.get("score"), row.get("source", row.get("by"))))
    for score, by in rows:
        try:
            value = float(score)
        except (TypeError, ValueError):
            skipped += 1
            continue
        if value != value or value in (float("inf"), float("-inf")):
            skipped += 1
            continue
        pairs.append((value, by))
    return pairs, skipped


def percentile_of(scores: list, percentile: float):
    """The bar's own quantile of these scores, each counting equally; None if there are none."""
    if not scores:
        return None
    return DecayingQuantileBar._quantile([(score, 1.0) for score in scores], percentile)


def shown(value) -> str:
    return "-" if value is None else f"{value:.2f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--percentile", type=float, default=DEFAULT_PERCENTILE, help="which percentile to report, above 0 and at most 100 (default: %(default)g)")
    parser.add_argument("--scores", action="append", help="a score file to read (repeat for more); default: the meetings' and the live room's in " + SCORES_DIR)
    args = parser.parse_args()
    if not 0 < args.percentile <= 100:
        parser.error("--percentile must be above 0 and at most 100")

    paths = args.scores or sorted(path for pattern in DEFAULT_PATTERNS for path in glob.glob(os.path.join(SCORES_DIR, pattern)))
    if not paths:
        sys.exit(f"no score files found in {SCORES_DIR}")

    everything, model_only = [], []
    print(f"the {args.percentile:g}th percentile of the scores each recording earned (read as the bar reads one; every score counts equally)")
    print(f"{'recording':<14} {'file dated':<11} {'scores':>6}  {'all':>6}   {'model only':>10} {'(scores)':>9}")
    for path in paths:
        try:
            pairs, skipped = read_scores(path)
        except (OSError, ValueError) as problem:
            print(f"{label_of(path):<14} could not be read: {problem}", file=sys.stderr)
            continue
        scores = [score for score, _ in pairs]
        by_model = [score for score, by in pairs if by == MODEL]
        everything += scores
        model_only += by_model
        dated = datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat()
        print(f"{label_of(path):<14} {dated:<11} {len(scores):6d}  {shown(percentile_of(scores, args.percentile)):>6}   "
              f"{shown(percentile_of(by_model, args.percentile)):>10} {len(by_model):9d}" + (f"   ({skipped} rows had no score)" if skipped else ""))
    print(f"{'pooled':<14} {'':<11} {len(everything):6d}  {shown(percentile_of(everything, args.percentile)):>6}   "
          f"{shown(percentile_of(model_only, args.percentile)):>10} {len(model_only):9d}")


if __name__ == "__main__":
    main()
