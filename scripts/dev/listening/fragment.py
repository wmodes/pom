#!/usr/bin/env python3
"""Play with the SegmentFragmenter: see where a piece of speech gets cut into hinge candidates.

Give it text as arguments, or pipe text in, and it prints every fragment with its level (sentence,
clause or phrase) and the sentence it belongs to. The cutting logic is src/pom/listening/segment_fragmenter.py;
this is only a window onto it.

Usage:
    fragment.py "Honestly, my dog ate the couch yesterday."
    echo "Well... maybe so; I don't know, okay?" | fragment.py
    fragment.py --max-words 6 "a long run of words with no punctuation at all in it"
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))

from pom.listening import SegmentFragmenter  # noqa: E402
from pom.log_setup import configure_logging  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("text", nargs="*", help="the text to cut (read from stdin if omitted)")
    parser.add_argument("--max-words", type=int, default=40, help="longest fragment, in words (default 40)")
    args = parser.parse_args()

    configure_logging("WARNING")
    text = " ".join(args.text) if args.text else sys.stdin.read()
    fragments = SegmentFragmenter(max_words=args.max_words).split(text)
    if not fragments:
        print("(nothing to cut: no words found)")
    for fragment in fragments:
        print(f"{fragment.sentence}  {fragment.level.value:<9} {fragment.text}")


if __name__ == "__main__":
    main()
