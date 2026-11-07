#!/usr/bin/env python3
"""Play with the ClipScorer and the BarKeeper: type sentences, see each one's score and whether it would qualify.

The ClipScorer is the real one, built from config/pom.toml (the scorer model's llama-server must be running, on the address
in `scorer.url`), and so is the BarKeeper that rules on each score. Each sentence is taken to have been said
`--gap` seconds after the one before, so you can watch the room's window fill and the bar settle. The scoring is
src/pom/scoring/; this is only a window onto it.

Usage:
    score_typed_sentences.py "My wife doesn't know I lost the house." "Pass the salt, please."
    echo "I can hear the people in the walls again." | score_typed_sentences.py
    score_typed_sentences.py                       # type sentences at the prompt; an empty line quits
    score_typed_sentences.py --min-scores 5 --gap 20 "..."     # let a score qualify after 5 scores, with 20 s between sentences

Nothing you type is kept: it is scored and forgotten.
"""
import argparse
import dataclasses
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from pom.config import Config  # noqa: E402
from pom.listening import TakeCutter  # noqa: E402
from pom.listening.take_cutter import ClipLevel  # noqa: E402
from pom.log_setup import configure_logging  # noqa: E402
from pom.qualifying import bar_keeper_from_settings, Ruling   # noqa: E402
from pom.scoring import ClipScorer, ScorerModelClient, WordTagger


def build(config: Config, min_scores: int):
    """The real ClipScorer and BarKeeper, from the config."""
    nltk_dir = ROOT / "data" / "nltk"
    tagger = WordTagger(nltk_data=str(nltk_dir) if nltk_dir.exists() else None)
    settings = config.scorer
    client = ScorerModelClient(settings.url, settings.prompt, settings.timeout, explain_prompt=settings.prompt_debug)
    scorer = ClipScorer(client, tagger, clip_filter_score=settings.clip_filter_score,
                    min_content_words=settings.min_content_words, failure_limit=settings.failure_limit,
                    retry_seconds=settings.retry_seconds)
    qualifying = dataclasses.replace(config.qualifying, min_scores=min_scores) if min_scores else config.qualifying
    return scorer, bar_keeper_from_settings(qualifying, started_at=0.0), client


def standing(ruling: Ruling, bar, held: int) -> str:
    """Where a score stands, for the end of its line."""
    if ruling is Ruling.NO_MARK or bar is None:
        return "[no score]"
    note = f"[window {held} scores, bar {bar:.2f}"
    return ("<-- would qualify  " if ruling is Ruling.QUALIFIED else "") + note + \
        ("; held" if ruling is Ruling.HELD else "") + "]"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("sentences", nargs="*", help="what to score (read from stdin, or typed at a prompt, if omitted)")
    parser.add_argument("--config", default=str(ROOT / "config" / "pom.toml"), help="the config file to read")
    parser.add_argument("--gap", type=float, default=10.0, help="seconds between one sentence and the next (default 10)")
    parser.add_argument("--min-scores", type=int, default=0,
                        help="scores needed before a score may qualify (default: the config's, 20)")
    args = parser.parse_args()

    configure_logging("WARNING")
    config = Config.load(args.config)
    scorer, bar_keeper, client = build(config, args.min_scores)
    take_cutter = TakeCutter()
    print(f"scorer at {config.scorer.url}; qualifying ({config.qualifying.algorithm}): a score above {bar_keeper.describe()}")
    if not scorer.warm_up():
        print("the scorer model is NOT answering: sentences will come back unscored (start its llama-server)")

    now = 0.0

    def handle(text: str) -> None:
        nonlocal now
        for clip in take_cutter.split(text, heard_at=now):
            if clip.level is not ClipLevel.SENTENCE:
                continue
            scored = scorer.score(clip)
            bar, held = bar_keeper.bar_at(now), bar_keeper.window_size_at(now)
            ruling = bar_keeper.rule(scored.score, now)
            shown = f"{scored.score:5.2f}" if scored.score is not None else "  -  "
            print(f"  {shown}  {scored.scored_by.value:<16} {clip.text}   "
                  f"{standing(ruling, bar, held)}")
            now += args.gap

    if args.sentences:
        for text in args.sentences:
            handle(text)
    elif not sys.stdin.isatty():
        for line in sys.stdin:
            if line.strip():
                handle(line)
    else:
        try:
            while True:
                line = input("sentence> ").strip()
                if not line:
                    break
                handle(line)
        except (EOFError, KeyboardInterrupt):
            print()
    for line in [*client.report.lines(), *bar_keeper.report.lines()]:
        print(line)


if __name__ == "__main__":
    main()
