#!/usr/bin/env python3
"""Play with the TakeCutter: see where a piece of speech gets cut into hinge candidates.

Give it text as arguments, or pipe text in, and it prints every clip with its level (sentence,
clause or phrase) and the sentence it belongs to. The cutting logic is src/pom/listening/take_cutter.py;
this is only a window onto it.

Usage:
    cut_speech_into_hinges.py "Honestly, my dog ate the couch yesterday."
    echo "Well... maybe so; I don't know, okay?" | cut_speech_into_hinges.py
    cut_speech_into_hinges.py --max-words 6 "a long run of words with no punctuation at all in it"
"""
import argparse
import dataclasses
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from pom.bookkeeper import Bookkeeper  # noqa: E402
from pom.config import Config  # noqa: E402
from pom.printer_builder import Options, PrinterBuilder  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("text", nargs="*", help="the text to cut (read from stdin if omitted)")
    parser.add_argument("--config", default=str(ROOT / "config" / "pom.toml"), help="the config file to read")
    parser.add_argument("--max-words", type=int, default=None, help="longest clip, in words (default: the config's [listening] max_clip_words)")
    args = parser.parse_args()

    config = Config.load(args.config)
    bookkeeper = Bookkeeper(strict=config.strict)
    bookkeeper.set_up("WARNING", debug=config.debug)
    if args.max_words is not None:
        config.listening = dataclasses.replace(config.listening, max_clip_words=args.max_words)
    builder = PrinterBuilder(config, Options(config_path=args.config, terminal="none"), root=ROOT, bookkeeper=bookkeeper)
    text = " ".join(args.text) if args.text else sys.stdin.read()
    clips = builder.take_cutter.split(text)
    if not clips:
        print("(nothing to cut: no words found)")
    for clip in clips:
        print(f"{clip.sentence}  {clip.level.value:<9} {clip.text}")


if __name__ == "__main__":
    main()
