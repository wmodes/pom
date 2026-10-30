#!/usr/bin/env python3
"""Play with the whole listening side: the microphone (or a recording) through the Listener, the Scorer and the trigger.

It is the real HingeFinder (the Listener, the SegmentFragmenter, the Scorer and the ConvoInterestTracker joined) and nothing
else: every sentence it hears is shown with its score and where the trigger stands, and the ones that would trigger the
Printer are marked. It needs a whisper-server running (the address is `listening.whisper_url`) and the scorer model's
llama-server; the settings are all in config/pom.toml.

Nothing you say is kept. The words are shown here and nowhere else, so do not redirect this output to a file.

Usage:
    listen_to_room.py                              # listen to the microphone (on the Pi)
    listen_to_room.py --wav meeting.wav            # play a 16 kHz mono recording (ffmpeg -i in -ar 16000 -ac 1 out.wav)
    listen_to_room.py --wav meeting.wav --speed 0  # as fast as possible; the answers are the same as a live run's

To start whisper the way that worked in the room:
    whisper-server -m ggml-base.en.bin --vad -vm ggml-silero-v6.2.0.bin -vt 0.5 --port 8110 -t 4
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from pom.config import Config  # noqa: E402
from pom.listening import Listener, MicSource, SegmentFragmenter, WavSource, WhisperServerClient  # noqa: E402
from pom.log_setup import configure_logging  # noqa: E402
from pom.scoring import (HingeFinder, ScoreEffect, Scorer, ScorerModelClient,  # noqa: E402
                         WordTagger, tracker_from_settings)


def stamp(seconds: float) -> str:
    """A place in the audio stream as +hh:mm:ss."""
    whole = int(seconds)
    return f"+{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"


def standing(sentence) -> str:
    """Where a score stands, for the end of its line."""
    if sentence.effect is ScoreEffect.IGNORED or sentence.trigger_level is None:
        return "[no score]"
    return (("<-- would trigger  " if sentence.is_hinge else "") +
            f"[window {sentence.window_size} scores, bar {sentence.trigger_level:.2f}" +
            ("; cooling down" if sentence.effect is ScoreEffect.COOLING_DOWN else "") + "]")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", default=str(ROOT / "config" / "pom.toml"), help="the config file to read")
    parser.add_argument("--wav", help="play this 16 kHz mono recording instead of listening to the microphone")
    parser.add_argument("--speed", type=float, default=1.0, help="recording speed: 1 is real time, 0 is as fast as possible")
    args = parser.parse_args()

    configure_logging("WARNING")
    config = Config.load(args.config)
    heard_settings, trigger = config.listening, config.trigger
    client = WhisperServerClient(heard_settings.whisper_url, heard_settings.whisper_timeout)
    if not client.ping():
        sys.exit(f"the whisper-server is not answering at {heard_settings.whisper_url}; see the top of this file for how to start it")
    source = (WavSource(args.wav, speed=args.speed) if args.wav
              else MicSource(heard_settings.device, buffer_seconds=heard_settings.buffer_seconds))
    listener = Listener(source, client, heard_settings.step, heard_settings.hold, heard_settings.rewind,
                        heard_settings.max_buffer, heard_settings.sound_every_steps, heard_settings.sound_seconds,
                        heard_settings.ambient_memory)

    nltk_dir = ROOT / "data" / "nltk"
    settings = config.scorer
    scorer_client = ScorerModelClient(settings.url, settings.prompt, settings.timeout, explain_prompt=settings.prompt_debug)
    scorer = Scorer(scorer_client, WordTagger(nltk_data=str(nltk_dir) if nltk_dir.exists() else None),
                    fragment_filter_score=settings.fragment_filter_score, min_content_words=settings.min_content_words,
                    failure_limit=settings.failure_limit, retry_seconds=settings.retry_seconds)
    tracker = tracker_from_settings(trigger)
    finder = HingeFinder(listener, SegmentFragmenter(), scorer, tracker, heard_settings.consider_ambient_sounds)

    print("hear: what you say is shown here and kept nowhere. Do not redirect this output to a file.")
    print(f"  whisper at {heard_settings.whisper_url}; scorer at {settings.url}")
    print(f"  listening to {'the recording ' + args.wav if args.wav else 'the microphone ' + heard_settings.device}; "
          f"a whisper run every {heard_settings.step:g} s")
    print(f"  trigger ({trigger.algorithm}): a score above {tracker.describe()}")
    print(f"  ambient sounds {'are' if heard_settings.consider_ambient_sounds else 'are not'} scored")
    print(f"  scorer model: {'ready' if scorer.warm_up() else 'NOT answering (sentences will go unscored)'}")
    print("  Listening. ctrl-C quits.", flush=True)

    try:
        for sentence in finder.find():
            when = stamp(sentence.heard_at)
            if sentence.score is None and sentence.effect is None:
                print(f"\n[{when}] ambient, not scored: {sentence.text}", flush=True)
                continue
            shown = f"{sentence.score:5.2f}" if sentence.score is not None else "  -  "
            print(f"\n[{when}] {sentence.kind:<7} {shown}  {sentence.scored_by.value:<16} {sentence.text}   "
                  f"{standing(sentence)}", flush=True)
    except KeyboardInterrupt:
        print()
    finally:
        finder.close()
    for line in [*source.report.lines(), *finder.report_lines(), *scorer_client.report.lines()]:
        print(line)


if __name__ == "__main__":
    main()
