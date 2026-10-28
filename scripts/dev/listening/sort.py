#!/usr/bin/env python3
"""Play with the WhisperTextSorter: see how whisper's text is sorted into words, ambient captions and nothing.

Give it text as arguments, or pipe text in, and it prints the words (speech, and lyrics with the music symbols taken
out), the kind of words they are, and the captions of sounds that were set aside. The sorting is
src/pom/listening/whisper_text_sorter.py; this is only a window onto it.

Usage:
    sort.py "(dog barks) Hello there. [BLANK_AUDIO]"
    sort.py "he said ♪ hello world ♪ and left"
    echo "[Music] and then we left" | sort.py
    sort.py --lyrics-only "hello ♪ la la ♪ there"      # text from a pass without the voice detector: only lyrics count
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "src"))

from pom.listening import WhisperTextSorter  # noqa: E402
from pom.log_setup import configure_logging  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("text", nargs="*", help="whisper's text (read from stdin if omitted)")
    parser.add_argument("--lyrics-only", action="store_true",
                        help="treat the text as coming from a pass without the voice detector, where only lyrics count")
    args = parser.parse_args()

    configure_logging("WARNING")
    raw = " ".join(args.text) if args.text else sys.stdin.read()
    heard = WhisperTextSorter().sort(raw, plain_text=not args.lyrics_only)
    print(f"words:   {heard.words or '(none)'}")
    print(f"kind:    {heard.kind or '(none)'}")
    print(f"ambient: {', '.join(heard.ambient) if heard.ambient else '(none)'}")


if __name__ == "__main__":
    main()
