#!/usr/bin/env python3
"""Play with the whole Printer except the typewriter: a room (or a recording) goes in, portents come out on the screen.

Sound goes through the real Listener (whisper cuts it), each thing heard is scored sentence by sentence and watched by the
trigger (the real HingeFinder), and when the trigger fires the sentence that fired it is the hinge: the real PortentWriter
asks the oracle model for a portent and prints it where the typewriter would. Then it listens again. Every part is the one
the Printer will use; the only things this script adds are the printing and, with --mac-mic, a different microphone.

Like the Printer, it does one thing at a time: while the oracle writes (20 to 60 seconds on the Pi, much less on a Mac) it
is not listening. A live microphone drops the sound that arrives meanwhile (the buffer keeps the last 10 seconds).

Three servers must be running first (addresses are in config/pom.toml):
    whisper-server -m ggml-base.en.bin --vad -vm ggml-silero-v6.2.0.bin -vt 0.5 --port 8110 -t 4
    the scorer model's llama-server (scorer.url), and the oracle model's llama-server (oracle.url).
The oracle's can be started with:
    llama-server -hf unsloth/Qwen3.5-9B-GGUF:Q4_K_M --port 8099 --ctx-size 4096 -ngl 99 --jinja

Nothing you say is kept. The words are shown here and nowhere else, so do not redirect this output to a file.

Usage:
    listening_to_portent.py --mac-mic                            # this Mac's system default input
    listening_to_portent.py --mac-mic "MacBook Pro Microphone"   # a particular input (ffmpeg -f avfoundation -list_devices true -i "")
    listening_to_portent.py                                      # the Pi's microphone (arecord, listening.device)
    listening_to_portent.py --wav meeting.wav --speed 0          # a 16 kHz mono recording, as fast as possible
    listening_to_portent.py --mac-mic --max-portents 1           # stop after the first portent

The config is read again before every portent, so a change to the oracle prompt shows at once.
"""
import argparse
import random
import sys
import textwrap
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from pom.config import Config                                                                      # noqa: E402
from pom.corpus import ChunkStore, DrawWeights, ExemplarSelector                                    # noqa: E402
from pom.listening import (Listener, MacMicSource, MicSource, SegmentFragmenter, WavSource,         # noqa: E402
                           WhisperServerClient)
from pom.log_setup import configure_logging                                                         # noqa: E402
from pom.prompt import PromptBuilder                                                                # noqa: E402
from pom.scoring import (HingeFinder, ScoreEffect, Scorer, ScorerModelClient,  # noqa: E402
                         WordTagger, tracker_from_settings)
from pom.voice import OracleModelClient, PortentWriter                                              # noqa: E402

CONFIG_PATH = ROOT / "config" / "pom.toml"
CHUNKS = ROOT / "corpus" / "chunks"
PAPER_WIDTH = 64                  # a line of the typewriter, for the look of it only; the real width is the formatter's


def stamp(seconds: float) -> str:
    """A place in the audio stream as +hh:mm:ss."""
    whole = int(seconds)
    return f"+{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"


def standing(sentence) -> str:
    """Where a score stands, for the end of its line."""
    if sentence.effect is ScoreEffect.IGNORED or sentence.trigger_level is None:
        return "[no score]"
    return (f"[window {sentence.window_size} scores, bar {sentence.trigger_level:.2f}" +
            ("; cooling down" if sentence.effect is ScoreEffect.COOLING_DOWN else "") + "]")


def answers(url: str) -> bool:
    """True if the llama-server behind this chat address says it is healthy."""
    parts = urllib.parse.urlparse(url)
    try:
        with urllib.request.urlopen(f"{parts.scheme}://{parts.netloc}/health", timeout=3):
            return True
    except (urllib.error.URLError, OSError):
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--wav", help="play this 16 kHz mono recording instead of listening to a microphone")
    parser.add_argument("--speed", type=float, default=1.0, help="recording speed: 1 is real time, 0 is as fast as possible")
    parser.add_argument("--mac-mic", nargs="?", const="default", metavar="DEVICE",
                        help="listen to this Mac's microphone through ffmpeg: the system default input, or the named one")
    parser.add_argument("--max-portents", type=int, default=0, help="stop after this many portents (0 means keep going)")
    args = parser.parse_args()
    if args.wav and args.mac_mic:
        sys.exit("give --wav or --mac-mic, not both")

    configure_logging("WARNING")
    config = Config.load(CONFIG_PATH)
    heard_settings, trigger, scoring = config.listening, config.trigger, config.scorer
    client = WhisperServerClient(heard_settings.whisper_url, heard_settings.whisper_timeout)
    if not client.ping():
        sys.exit(f"the whisper-server is not answering at {heard_settings.whisper_url}; see the top of this file for how to start it")
    if not answers(scoring.url):
        sys.exit(f"the scorer model's llama-server is not answering at {scoring.url}")
    if not answers(config.oracle.url):
        sys.exit(f"the oracle model's llama-server is not answering at {config.oracle.url}; see the top of this file for how to start it")

    if args.wav:
        source, where = WavSource(args.wav, speed=args.speed), f"the recording {args.wav}"
    elif args.mac_mic is not None:
        source, where = MacMicSource(args.mac_mic, buffer_seconds=heard_settings.buffer_seconds), f"this Mac's microphone ({args.mac_mic})"
    else:
        source, where = MicSource(heard_settings.device, buffer_seconds=heard_settings.buffer_seconds), f"the microphone {heard_settings.device}"
    listener = Listener(source, client, heard_settings.step, heard_settings.hold, heard_settings.rewind,
                        heard_settings.max_buffer, heard_settings.sound_every_steps, heard_settings.sound_seconds,
                        heard_settings.ambient_memory)

    nltk_dir = ROOT / "data" / "nltk"
    scorer_client = ScorerModelClient(scoring.url, scoring.prompt, scoring.timeout, explain_prompt=scoring.prompt_debug)
    scorer = Scorer(scorer_client, WordTagger(nltk_data=str(nltk_dir) if nltk_dir.exists() else None),
                    fragment_filter_score=scoring.fragment_filter_score, min_content_words=scoring.min_content_words,
                    failure_limit=scoring.failure_limit, retry_seconds=scoring.retry_seconds)
    tracker = tracker_from_settings(trigger)
    finder = HingeFinder(listener, SegmentFragmenter(), scorer, tracker, heard_settings.consider_ambient_sounds)

    rng = random.Random()
    selector = ExemplarSelector(ChunkStore.load(CHUNKS), DrawWeights.from_file(CHUNKS / "weights.json"), rng,
                                exemplar_range=config.exemplar_range)
    if not selector.ready:
        sys.exit("nothing to draw exemplars from; see the messages above")

    print("listening_to_portent: what you say is shown here and kept nowhere. Do not redirect this output to a file.")
    print(f"  listening to {where}; whisper at {heard_settings.whisper_url}; scorer at {scoring.url}; oracle at {config.oracle.url}")
    print(f"  trigger ({trigger.algorithm}): a score above {tracker.describe()}")
    print(f"  ambient sounds {'are' if heard_settings.consider_ambient_sounds else 'are not'} scored")
    print(f"  scorer model: {'ready' if scorer.warm_up() else 'NOT answering (sentences will go unscored)'}")
    print("  Listening. ctrl-C quits.", flush=True)

    writer = None
    portents = 0
    try:
        for sentence in finder.find():
            when = stamp(sentence.heard_at)
            if sentence.score is None and sentence.effect is None:
                print(f"\n[{when}] ambient, not scored: {sentence.text}", flush=True)
                continue
            shown = f"{sentence.score:5.2f}" if sentence.score is not None else "  -  "
            print(f"\n[{when}] {sentence.kind:<6} {shown}  {sentence.scored_by.value:<16} {sentence.text}   "
                  f"{standing(sentence)}", flush=True)
            if not sentence.is_hinge:
                continue

            print(f"\n  >>> HINGE: {sentence.text}")
            print("  >>> the oracle is writing; the Printer is not listening meanwhile ...", flush=True)
            oracle = Config.load(CONFIG_PATH).oracle                 # read again, so prompt edits show at once
            writer = PortentWriter(PromptBuilder(oracle.prompt, selector),
                                   OracleModelClient(oracle.url, oracle.timeout, oracle.max_tokens, oracle.temperature), rng)
            portent = writer.write(sentence.text)
            if portent is None:
                print("  >>> no portent this time:", *writer.report_lines(), sep="\n      ")
                continue
            portents += 1
            print("\n" + "\n".join("      " + line for line in textwrap.wrap(portent.text, PAPER_WIDTH)) + "\n")
            print(f"  ({portent.words} words, {portent.seconds:.1f} s{', cut off at max_tokens' if portent.cut_off else ''}; "
                  f"exemplars: {', '.join(chunk.id for chunk in portent.exemplars) or 'none'})", flush=True)
            if args.max_portents and portents >= args.max_portents:
                break
    except KeyboardInterrupt:
        print()
    finally:
        finder.close()
    lines = [*source.report.lines(), *finder.report_lines(), *scorer_client.report.lines(),
             *(writer.report_lines() if writer is not None else [])]
    for line in lines:
        print(line)


if __name__ == "__main__":
    main()
