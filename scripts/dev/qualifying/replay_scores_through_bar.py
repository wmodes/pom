#!/usr/bin/env python3
"""Play with the bar: replay a recorded stream of scores through the real BarKeeper at different qualifier bar influences.

The scores are the ones a recording earned when it was run through the Printer's listening and scoring (a CSV with `seconds_into_recording` and
`score` columns; by default the first hour of the live room on 2026-10-26, which holds numbers only, no words). Each is fed to the BarKeeper built
from config/pom.toml (`[qualifying]`) at the time it was heard, with `qualifier_bar_influence` set to each value asked for, and what came of it is
counted: how many qualified, how many were held by the cooldown, how often an hour, what share of the scores stood above the bar, and where the
bar sat. No models and no servers: the scoring was done when the CSV was made. The bar is
src/pom/qualifying/; this is only a window onto it.

Usage:
    replay_scores_through_bar.py                               # influence 0, 0.25, 0.5, 0.75 and 1 on the live-room hour
    replay_scores_through_bar.py --influence 0.5 --influence 1.1
    replay_scores_through_bar.py --offset-hours 30             # the first sentence comes 30 hours after the Printer started, so the starting line has faded
    replay_scores_through_bar.py --scores other.csv --bar-percentile 99 --half-life 120
    replay_scores_through_bar.py --anchor-interval-minutes 0.25 --anchor-score 5.5      # try other ticks (the config's `anchor_*`)
    replay_scores_through_bar.py --after-silence-minutes 10 --after-silence-minutes 30  # also: where the bar sits that long after the last score
"""
import argparse
import csv
import dataclasses
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))

from pom.config import Config  # noqa: E402
from pom.qualifying import Ruling, bar_keeper_from_settings  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
DEFAULT_SCORES = os.path.join(ROOT, "data", "recordings", "work", "zoom-scores", "live-oct26-first-hour.csv")
DEFAULT_INFLUENCES = (0.0, 0.25, 0.5, 0.75, 1.0)
STREAM_STARTED_AT = 0.0                 # the replayed stream begins at time 0, as the Printer's does when listening starts; `--offset-hours` moves the scores later, not the start


def read_scores(path: str) -> list:
    """The (seconds, score) pairs in the CSV, in time order. Rows with no usable number are skipped."""
    pairs = []
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                pairs.append((float(row["seconds_into_recording"]), float(row["score"])))
            except (KeyError, ValueError):
                continue
    return sorted(pairs)


def replay(settings, pairs: list, offset_seconds: float, silence_minutes: tuple = ()) -> dict:
    """Runs the scores through a fresh BarKeeper built from `settings`; the counts and the bar's level, and where the bar sits
    each of `silence_minutes` minutes after the last score (the room gone quiet)."""
    keeper = bar_keeper_from_settings(settings, STREAM_STARTED_AT)
    counts = {Ruling.QUALIFIED: 0, Ruling.HELD: 0, Ruling.DID_NOT_QUALIFY: 0}
    bars = []
    for seconds, score in pairs:
        now = seconds + offset_seconds
        level = keeper.bar_at(now)
        if level is not None:
            bars.append(level)
        ruling = keeper.rule(score, now)
        if ruling in counts:
            counts[ruling] += 1
    last = pairs[-1][0] + offset_seconds
    after = {minutes: keeper.bar_at(last + minutes * 60.0) for minutes in silence_minutes}
    hours = (pairs[-1][0] - pairs[0][0]) / 3600.0 if len(pairs) > 1 else 0.0
    scored = sum(counts.values())
    above = counts[Ruling.QUALIFIED] + counts[Ruling.HELD]
    return {"scored": scored, "qualified": counts[Ruling.QUALIFIED], "held": counts[Ruling.HELD], "hours": hours,
            "per_hour": counts[Ruling.QUALIFIED] / hours if hours else 0.0, "above_share": above / scored if scored else 0.0,
            "bar_first": bars[0] if bars else None, "bar_mean": sum(bars) / len(bars) if bars else None, "bar_last": bars[-1] if bars else None,
            "after": after}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--scores", default=DEFAULT_SCORES, help="a CSV with seconds_into_recording and score columns")
    parser.add_argument("--influence", type=float, action="append", help="a qualifier_bar_influence to try (repeat for more)")
    parser.add_argument("--offset-hours", type=float, default=0.0, help="the first score comes this many hours after the stream began (the Printer's start), so the starting line has faded")
    parser.add_argument("--bar-percentile", type=float, default=None, help="the bar's percentile (default: the config's)")
    parser.add_argument("--half-life", type=float, default=None, help="the half-life in seconds (default: the config's)")
    parser.add_argument("--after-silence-minutes", type=float, action="append",
                        help="also print where the bar sits this many minutes after the last score, as if the room then went quiet (repeat for more)")
    parser.add_argument("--anchor-score", type=float, default=None, help="the value of each tick (default: the config's anchor_score)")
    parser.add_argument("--anchor-interval-minutes", type=float, default=None,
                        help="minutes between ticks (default: the config's anchor_interval_minutes); a huge number means no ticks")
    parser.add_argument("--config", default=os.path.join(ROOT, "config", "pom.toml"), help="the config file to read")
    args = parser.parse_args()

    config = Config.load(args.config)
    base = config.qualifying
    if args.bar_percentile is not None:
        base = dataclasses.replace(base, bar_percentile=args.bar_percentile)
    if args.half_life is not None:
        base = dataclasses.replace(base, half_life_seconds=args.half_life)
    if args.anchor_score is not None:
        base = dataclasses.replace(base, anchor_score=args.anchor_score)
    if args.anchor_interval_minutes is not None:
        base = dataclasses.replace(base, anchor_interval_minutes=args.anchor_interval_minutes)
    pairs = read_scores(args.scores)
    if not pairs:
        sys.exit(f"no scores in {args.scores}")
    offset = args.offset_hours * 3600.0

    print(f"{len(pairs)} scores over {(pairs[-1][0] - pairs[0][0]) / 60:.0f} minutes; {base.algorithm}, percentile {base.bar_percentile:g}, "
          f"half-life {base.half_life_seconds:g} s, starting bar {base.starting_bar:g}, cooldown {base.cooldown_seconds:g} s, "
          f"a tick of {base.anchor_score:g} every {base.anchor_interval_minutes:g} min; "
          f"first score {args.offset_hours:g} h after the start")
    print(f"{'influence':>9}  {'qualified':>9}  {'held':>5}  {'per hour':>8}  {'above bar':>9}  {'bar first':>9}  {'bar mean':>8}  {'bar last':>8}")
    silence = tuple(sorted(set(args.after_silence_minutes or ())))
    afters = []
    for influence in args.influence or DEFAULT_INFLUENCES:
        r = replay(dataclasses.replace(base, qualifier_bar_influence=influence), pairs, offset, silence)
        afters.append((influence, r["after"]))
        print(f"{influence:9g}  {r['qualified']:9d}  {r['held']:5d}  {r['per_hour']:8.1f}  {r['above_share'] * 100:8.1f}%  "
              f"{r['bar_first']:9.2f}  {r['bar_mean']:8.2f}  {r['bar_last']:8.2f}")
    if silence:
        print("\nthe bar after the last score, as the room goes quiet (minutes after it):")
        print(f"{'influence':>9}  " + "  ".join(f"{'+%gm' % minutes:>6}" for minutes in silence))
        for influence, after in afters:
            print(f"{influence:9g}  " + "  ".join(f"{after[minutes]:6.2f}" for minutes in silence))


if __name__ == "__main__":
    main()
