#!/usr/bin/env python3
"""Turns the bake-off ratings into a report, one section per model.

Part of experiments/oracle-bakeoff. Joins three things: the results files (the three program checks and the
times), ratings/key.json (which blind id is which model) and ratings/ratings.jsonl (the reader's marks).

Every file ratings/ratings*.jsonl is read (so the ratings can be written in parts), one line per id, for example:

    {"id": "r017", "follows_hinge": true, "follows_style": true, "details_ok": true, "creative": false, "note": "..."}

The four judged marks are those four fields. A reader may also override a program-checked mark by adding
"no_repeat" or "no_extra" (true or false), with the reason in "note". Mark 6, length, is always the program's.
An id with no rating line is counted as unrated and left out of the judged marks.

Writes ratings/report.md and prints it.
"""
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
RATINGS = HERE / "ratings"

MARKS = [
    ("follows_hinge", "follows the hinge"),
    ("follows_style", "follows the style"),
    ("no_repeat", "doesn't repeat the hinge"),
    ("details_ok", "didn't muddle the hinge's details"),
    ("no_extra", "no extra text"),
    ("length_close", "length was close"),
    ("creative", "particularly creative"),
]
JUDGED = ["follows_hinge", "follows_style", "details_ok", "creative"]


def read_lines(path: Path) -> list:
    """The JSON lines of a file ([] if it does not exist)."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def marks_for(record: dict, rating: dict) -> dict:
    """The seven marks for one portent: the program's three (unless overridden) and the reader's four.

    A mark is True, False, or None where it is judged and has not been rated yet. A portent that was never made is
    False on every mark.
    """
    if record["portent"] is None:
        return {name: False for name, _ in MARKS}
    auto = record["auto"]
    marks = {
        "no_repeat": rating.get("no_repeat", not auto["repeats_hinge"]),
        "no_extra": rating.get("no_extra", not auto["extra_text"]),
        "length_close": auto["length_close"],
    }
    marks.update({name: rating.get(name) for name in JUDGED})
    return marks


def score(marks: dict) -> int:
    """How many of the seven are yes."""
    return sum(1 for value in marks.values() if value)


def model_section(label: str, run: dict, rows: list) -> list:
    """The report lines for one model. rows is a list of (record, marks, rated)."""
    made = [record for record, _, _ in rows if record["portent"] is not None]
    rated = [(record, marks) for record, marks, was_rated in rows if was_rated]
    lines = [f"## {label}", "", f"- {run['model']}; {len(made)} of {len(rows)} portents made, {len(rated)} rated."]
    seconds = [record["seconds"] for record in made]
    if seconds:
        lines.append(f"- Speed on the Mac: median {statistics.median(seconds):.1f} s, slowest {max(seconds):.1f} s; "
                     f"cut off at max_tokens: {sum(record['cut_off'] for record in made)}.")
    if rated:
        lines.append(f"- Mean score: {statistics.mean(score(marks) for _, marks in rated):.2f} of 7.")
    for name, words in MARKS:
        # the program's marks cover every portent; the judged ones only those that have been rated
        pool = rated if name in JUDGED else [(record, marks) for record, marks, _ in rows]
        yes = sum(1 for _, marks in pool if marks[name])
        lines.append(f"- {words}: {yes} of {len(pool)}")
    return lines


def main() -> None:
    results = {}
    for path in sorted(RESULTS.glob("*.jsonl")):
        lines = read_lines(path)
        run = next((line for line in lines if line.get("type") == "run"), None)
        if run is not None:
            results[run["label"]] = (run, {(line["hinge"], line["draw"]): line for line in lines if line.get("type") == "portent"})
    key = json.loads((RATINGS / "key.json").read_text(encoding="utf-8")) if (RATINGS / "key.json").exists() else {}
    ratings = {line["id"]: line for path in sorted(RATINGS.glob("ratings*.jsonl")) for line in read_lines(path)}
    if not results or not key:
        sys.exit("nothing to report: run the benchmark, make the rating sheet and rate it first")

    out = ["# Bake-off report", "", "Marks are yes/no, yes is good; see documentation/bakeoff-rubric.md.", ""]
    ranking = []
    for label, (run, cells) in results.items():
        rows = []
        for item_id, entry in key.items():
            if entry["label"] != label:
                continue
            record = cells.get((entry["hinge"], entry["draw"]))
            if record is None:
                continue
            rating = ratings.get(item_id, {})
            marks = marks_for(record, rating)
            rows.append((record, marks, item_id in ratings))
        out += model_section(label, run, rows) + [""]
        rated_scores = [score(marks) for _, marks, was_rated in rows if was_rated]
        if rated_scores:
            ranking.append((statistics.mean(rated_scores), label))

        by_hinge = {}
        for record, marks, was_rated in rows:
            if was_rated:
                by_hinge.setdefault(record["hinge"], []).append(score(marks))
        out += [f"By hinge, mean score of 7 for {label}:"] + [f"- {hinge}: {statistics.mean(values):.1f}" for hinge, values in by_hinge.items()] + [""]

    out += ["## Ranking by mean score", ""] + [f"- {label}: {mean:.2f}" for mean, label in sorted(ranking, reverse=True)]
    text = "\n".join(out) + "\n"
    (RATINGS / "report.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
