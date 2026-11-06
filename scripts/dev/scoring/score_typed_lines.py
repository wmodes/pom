#!/usr/bin/env python3
"""Play with the typed-line path: type lines, see each one's score, the score multiplier, and whether the Printer would answer.

The ClipScorer is the real one, built from config/pom.toml (the scorer model's llama-server must be running, on the address in
`scorer.url`), and so are the BarKeeper and the TypedLineScorer, with the `[qualifying]` settings
`typed_lines_score_multiplier` and `typed_lines_bar_influence`. A line that does not clear the bar is ignored: the Printer says nothing, and
this testbench says "silence". The scoring is src/pom/scoring/; this is only a window onto it.

Usage:
    score_typed_lines.py "Is anyone there?" "hello"
    echo "The walls are listening." | score_typed_lines.py
    score_typed_lines.py                       # type lines at the prompt; an empty line quits
    score_typed_lines.py --bar 5 "..."         # start the bar at 5 instead of the config's starting_bar
    score_typed_lines.py --multiplier 2 --bar-influence 0.5 "..."    # try another score multiplier, and let typed lines feed the bar at half weight

Nothing you type is kept: it is scored and forgotten.
"""
import argparse
import dataclasses
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from pom.config import Config  # noqa: E402
from pom.log_setup import configure_logging  # noqa: E402
from pom.keyboard import TypedLineScorer   # noqa: E402
from pom.qualifying import bar_keeper_from_settings
from pom.scoring import ClipScorer, ScorerModelClient, WordTagger


def build(config: Config, args):
    """The real ClipScorer, BarKeeper and TypedLineScorer, from the config and the command line."""
    nltk_dir = ROOT / "data" / "nltk"
    tagger = WordTagger(nltk_data=str(nltk_dir) if nltk_dir.exists() else None)
    settings = config.scorer
    client = ScorerModelClient(settings.url, settings.prompt, settings.timeout, explain_prompt=settings.prompt_debug)
    scorer = ClipScorer(client, tagger, clip_filter_score=settings.clip_filter_score,
                    min_content_words=settings.min_content_words, failure_limit=settings.failure_limit,
                    retry_seconds=settings.retry_seconds)
    qualifying = config.qualifying
    if args.bar is not None:
        qualifying = dataclasses.replace(qualifying, starting_bar=args.bar)
    bar_keeper = bar_keeper_from_settings(qualifying)
    multiplier = qualifying.typed_lines_score_multiplier if args.multiplier is None else args.multiplier
    bar_influence = qualifying.typed_lines_bar_influence if args.bar_influence is None else args.bar_influence
    started = time.monotonic()
    typed = TypedLineScorer(scorer, bar_keeper, lambda: time.monotonic() - started, multiplier, bar_influence)
    return scorer, bar_keeper, typed, client, multiplier, bar_influence


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("lines", nargs="*", help="what to score (read from stdin, or typed at a prompt, if omitted)")
    parser.add_argument("--config", default=str(ROOT / "config" / "pom.toml"), help="the config file to read")
    parser.add_argument("--bar", type=float, default=None, help="start the bar here (default: the config's starting_bar)")
    parser.add_argument("--multiplier", type=float, default=None, help="the score multiplier (default: the config's typed_lines_score_multiplier)")
    parser.add_argument("--bar-influence", type=float, default=None,
                        help="how much typed lines count toward the bar, 1 being a score heard in the room (default: the config's typed_lines_bar_influence)")
    args = parser.parse_args()

    configure_logging("WARNING")
    config = Config.load(args.config)
    scorer, bar_keeper, typed, client, multiplier, bar_influence = build(config, args)
    print(f"scorer at {config.scorer.url}; qualifying ({config.qualifying.algorithm}): a score above {bar_keeper.describe()}")
    print(f"typed lines: score x {multiplier:g}, no cooldown, count {bar_influence:g} toward the bar")
    if not scorer.warm_up():
        print("the scorer model is NOT answering: lines will come back unscored (start its llama-server)")

    def handle(text: str) -> None:
        sentence = typed.score(text)
        if sentence.score is None:
            print("      -  no score       [silence]")
            return
        raw = sentence.score / multiplier if multiplier else 0.0
        verdict = "<-- the Printer would answer" if sentence.is_hinge else "[silence]"
        bar = "none" if sentence.bar is None else f"{sentence.bar:.2f}"
        print(f"  {raw:5.2f} x {multiplier:g} = {sentence.score:5.2f}  bar {bar:>4}  "
              f"{sentence.scored_by.value:<16} {verdict}")

    if args.lines:
        for text in args.lines:
            handle(text)
    elif not sys.stdin.isatty():
        for line in sys.stdin:
            if line.strip():
                handle(line)
    else:
        try:
            while True:
                line = input("typed> ").strip()
                if not line:
                    break
                handle(line)
        except (EOFError, KeyboardInterrupt):
            print()
    for line in [*client.report.lines(), *bar_keeper.report.lines(), *typed.report.lines()]:
        print(line)


if __name__ == "__main__":
    main()
