#!/usr/bin/env python3
"""Play with the Scorer and the trigger: type sentences, see each one's score and whether the trigger would fire.

The Scorer is the real one, built from config/pom.toml (the scorer model's llama-server must be running, on the address
in `scorer.url`), and so is the ConvoInterestTracker that decides the trigger. Each sentence is taken to have been said
`--gap` seconds after the one before, so you can watch the room's window fill and the bar settle. The scoring is
src/pom/scoring/; this is only a window onto it.

Usage:
    score_typed_sentences.py "My wife doesn't know I lost the house." "Pass the salt, please."
    echo "I can hear the people in the walls again." | score_typed_sentences.py
    score_typed_sentences.py                       # type sentences at the prompt; an empty line quits
    score_typed_sentences.py --min-scores 5 --gap 20 "..."     # let the trigger fire after 5 scores, with 20 s between sentences

Nothing you type is kept: it is scored and forgotten.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from pom.config import Config  # noqa: E402
from pom.listening import SegmentFragmenter  # noqa: E402
from pom.listening.segment_fragmenter import FragmentLevel  # noqa: E402
from pom.log_setup import configure_logging  # noqa: E402
from pom.scoring import ConvoInterestTracker, ScoreEffect, Scorer, ScorerModelClient, WordTagger  # noqa: E402


def build(config: Config, min_scores: int):
    """The real Scorer and ConvoInterestTracker, from the config."""
    nltk_dir = ROOT / "data" / "nltk"
    tagger = WordTagger(nltk_data=str(nltk_dir) if nltk_dir.exists() else None)
    settings = config.scorer
    client = ScorerModelClient(settings.url, settings.prompt, settings.timeout, explain_prompt=settings.prompt_debug)
    scorer = Scorer(client, tagger, fragment_filter_score=settings.fragment_filter_score,
                    min_content_words=settings.min_content_words, failure_limit=settings.failure_limit,
                    retry_seconds=settings.retry_seconds)
    trigger = config.trigger
    tracker = ConvoInterestTracker(trigger.fence_multiple, trigger.window_seconds,
                                   min_scores if min_scores else trigger.min_scores, trigger.cooldown_seconds)
    return scorer, tracker, client


def standing(effect: ScoreEffect, bar, held: int, counted: int, needed: int) -> str:
    """Where a score stands, for the end of its line."""
    if effect is ScoreEffect.IGNORED:
        return "[no score]"
    if bar is None:
        return f"[warming up {counted}/{needed}]"
    note = f"[window {held} scores, bar {bar:.2f}"
    return ("<-- would trigger  " if effect is ScoreEffect.TRIGGERED else "") + note + \
        ("; cooling down" if effect is ScoreEffect.COOLING_DOWN else "") + "]"


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("sentences", nargs="*", help="what to score (read from stdin, or typed at a prompt, if omitted)")
    parser.add_argument("--config", default=str(ROOT / "config" / "pom.toml"), help="the config file to read")
    parser.add_argument("--gap", type=float, default=10.0, help="seconds between one sentence and the next (default 10)")
    parser.add_argument("--min-scores", type=int, default=0,
                        help="scores needed before the trigger may fire (default: the config's, 20)")
    args = parser.parse_args()

    configure_logging("WARNING")
    config = Config.load(args.config)
    scorer, tracker, client = build(config, args.min_scores)
    needed = args.min_scores or config.trigger.min_scores
    fragmenter = SegmentFragmenter()
    print(f"scorer at {config.scorer.url}; the trigger needs {needed} scores, then a score above Q3 + "
          f"{config.trigger.fence_multiple:g} x IQR of the last {config.trigger.window_seconds:g} s of scores "
          f"(or the last {needed}), at most one every {config.trigger.cooldown_seconds:g} s")
    if not scorer.warm_up():
        print("the scorer model is NOT answering: sentences will come back unscored (start its llama-server)")

    now = 0.0

    def handle(text: str) -> None:
        nonlocal now
        for fragment in fragmenter.split(text, heard_at=now):
            if fragment.level is not FragmentLevel.SENTENCE:
                continue
            scored = scorer.score(fragment)
            bar, held = tracker.trigger_level_at(now), tracker.window_size_at(now)
            effect = tracker.record(scored.score, now)
            shown = f"{scored.score:5.2f}" if scored.score is not None else "  -  "
            print(f"  {shown}  {scored.scored_by.value:<16} {fragment.text}   "
                  f"{standing(effect, bar, held, tracker.counted, needed)}")
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
    for line in [*client.report.lines(), *tracker.report.lines()]:
        print(line)


if __name__ == "__main__":
    main()
