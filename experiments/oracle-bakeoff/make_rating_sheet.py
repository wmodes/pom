#!/usr/bin/env python3
"""Builds the blind rating sheet from the bake-off results.

Part of experiments/oracle-bakeoff. Reads every results/<label>.jsonl, gives each portent an id (r001, r002...)
in a shuffled order that does not reveal the model, and writes:

    ratings/sheet.txt    what the reader sees: for each id the hinge, the exemplar, the portent and the
                         program's three checks. No model name.
    ratings/sheet.jsonl  the same, as data.
    ratings/key.json     which id is which model, hinge and draw. Not looked at until the report.

The ratings go in ratings/ratings.jsonl, one line per id (see report_bakeoff.py). Running this again after
more results have come in keeps every id already given and numbers only the new portents, so ratings already
made stay valid.

It refuses to build a sheet if the results files were not made under the same conditions (same prompt,
temperature, exemplar count, corpus, hinges and draws), because then the models are not comparable.
"""
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
RATINGS = HERE / "ratings"
SHUFFLE_SEED = 20261025
CONDITIONS = ["prompt_sha256", "temperature", "max_tokens", "exemplar_range", "corpus", "hinges", "draws"]


def load_results() -> dict:
    """Every results file as {label: (run line, [portent lines])}."""
    loaded = {}
    for path in sorted(RESULTS.glob("*.jsonl")):
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        run = next((line for line in lines if line.get("type") == "run"), None)
        if run is not None:
            loaded[run["label"]] = (run, [line for line in lines if line.get("type") == "portent"])
    return loaded


def check_same_conditions(loaded: dict) -> None:
    """Exits if the results were not all made under the same frozen conditions."""
    runs = [run for run, _ in loaded.values()]
    for name in CONDITIONS:
        values = {json.dumps(run.get(name)) for run in runs}
        if len(values) > 1:
            sys.exit(f"the results were not made under the same conditions: '{name}' differs "
                     f"({', '.join(sorted(values))}); delete the odd file out or run it again")


EXEMPLAR_VIEW_LENGTH = 340


def shorten(text: str) -> str:
    """The first EXEMPLAR_VIEW_LENGTH characters of an exemplar, marked if cut."""
    return text if len(text) <= EXEMPLAR_VIEW_LENGTH else text[:EXEMPLAR_VIEW_LENGTH].rstrip() + " [...cut for reading, whole exemplar is" \
        f" {len(text.split())} words]"


def describe(item: dict) -> str:
    """One item as the reader sees it."""
    auto = item["auto"]
    checks = ("no portent was made" if auto is None else
              f"repeats hinge: {'YES' if auto['repeats_hinge'] else 'no'}; "
              f"extra text: {', '.join(auto['extra_text']) or 'none'}; "
              f"length {auto['length_ratio']} x exemplar ({'close' if auto['length_close'] else 'NOT close'})"
              f"{'; CUT OFF at max_tokens' if item['cut_off'] else ''}")
    # a long exemplar is cut for reading: its form and register show in the first lines (sheet.jsonl keeps it whole)
    exemplars = "\n---\n".join(shorten(text) for text in item["exemplars"]) or "(none)"
    return (f"[{item['id']}]\nHINGE: {item['hinge']}\nEXEMPLAR:\n{exemplars}\nPORTENT:\n{item['portent']}\n"
            f"CHECKS: {checks}\n")


def main() -> None:
    loaded = load_results()
    if not loaded:
        sys.exit("no results files in results/")
    check_same_conditions(loaded)
    RATINGS.mkdir(exist_ok=True)

    key_path = RATINGS / "key.json"
    key = json.loads(key_path.read_text(encoding="utf-8")) if key_path.exists() else {}
    known = {(entry["label"], entry["hinge"], entry["draw"]) for entry in key.values()}

    new = [(label, record) for label, (_, records) in loaded.items() for record in records
           if (label, record["hinge"], record["draw"]) not in known]
    random.Random(SHUFFLE_SEED + len(key)).shuffle(new)
    for label, record in new:
        item_id = f"r{len(key) + 1:03d}"
        key[item_id] = {"label": label, "hinge": record["hinge"], "draw": record["draw"]}
        record["id"] = item_id

    items = []
    by_cell = {(label, record["hinge"], record["draw"]): record for label, (_, records) in loaded.items() for record in records}
    for item_id, entry in key.items():
        record = by_cell.get((entry["label"], entry["hinge"], entry["draw"]))
        if record is not None:
            items.append({"id": item_id, "hinge": record["hinge"], "draw": record["draw"],
                          "exemplars": record["exemplars"], "portent": record["portent"],
                          "cut_off": record["cut_off"], "auto": record["auto"]})

    key_path.write_text(json.dumps(key, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (RATINGS / "sheet.jsonl").write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items), encoding="utf-8")
    (RATINGS / "sheet.txt").write_text("\n".join(describe(item) for item in items), encoding="utf-8")
    print(f"{len(items)} portents on the sheet ({len(new)} new) from {len(loaded)} model(s): ratings/sheet.txt")


if __name__ == "__main__":
    main()
