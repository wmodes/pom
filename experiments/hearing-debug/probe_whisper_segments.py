#!/usr/bin/env python3
"""Probe: can whisper's own voice detection find the cut points, so we need no energy chunker?

Part of experiments/hearing-debug. The live pipeline cuts the microphone into utterances with a simple
energy detector, which in a room with fluctuating noise (street, music, a hookah) never sees a pause and
cuts every utterance by the clock, in the middle of a thought. This probe tests the alternative on a
recording, with no microphone and no model server:

  * A rolling buffer is handed to whisper-cli with its own voice detection (--vad) every `--step` seconds,
    as a live system would. Whisper returns segments with timestamps.
  * A segment is kept once it is finished: it ends at least `--hold` seconds before the end of the buffer.
    The audio after the last kept segment stays in the buffer for the next round (starting a little before it,
    `--rewind`, because whisper's segment ends can run slightly late and would clip the next sentence's first
    syllables), so a thought that was still going is not cut in two. A segment that does not end like a sentence (no . ? or !) is joined to the
    one after it first, because whisper sometimes breaks at a pause in the middle of a sentence.
  * Each kept segment goes through the Printer's own SegmentFragmenter, which splits it into sentence
    fragments (whisper may put several sentences in one segment).
  * For comparison, whisper reads the whole file at once, and the sentences it finds that way are the
    answer key: how many of them did the streaming version produce unbroken? Whisper words things a little
    differently depending on where its audio starts, so a sentence counts as matched if it is at least 85%
    similar to its answer-key sentence; the exact-match count is printed too.

It prints numbers by default (no words). `--text` also prints the words of each segment to your terminal;
nothing is ever written to a file except a temporary audio slice, deleted straight away.

Usage:
    probe_whisper_segments.py recording.wav
    probe_whisper_segments.py recording.wav --step 8 --hold 2 --text
"""
import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from pom.listening.segment_fragmenter import FragmentLevel, SegmentFragmenter    # noqa: E402

SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2                     # 16-bit mono
Segment = Tuple[float, float, str]                     # absolute start, absolute end, text


class Whisper:
    """Runs whisper-cli on a slice of audio and returns its segments, with its own voice detection on."""

    def __init__(self, cli: str, model: Path, vad_model: Path, threads: int):
        self._cli, self._model, self._vad, self._threads = cli, model, vad_model, threads
        scratch = "/dev/shm" if os.path.isdir("/dev/shm") else None      # memory-backed where there is one
        self._dir = tempfile.mkdtemp(prefix="seg-probe-", dir=scratch)
        self.runs, self.seconds_spent = 0, 0.0

    def close(self) -> None:
        shutil.rmtree(self._dir, ignore_errors=True)

    def segments(self, pcm: bytes, offset_s: float) -> List[Segment]:
        """Transcribes the audio; times are shifted by `offset_s` so they are positions in the whole recording."""
        wav_path, prefix = os.path.join(self._dir, "slice.wav"), os.path.join(self._dir, "out")
        with wave.open(wav_path, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(SAMPLE_RATE)
            out.writeframes(pcm)
        started = time.perf_counter()
        subprocess.run([self._cli, "-m", str(self._model), "-f", wav_path, "-t", str(self._threads), "--vad", "-vm",
                        str(self._vad), "-np", "-oj", "-of", prefix], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
        self.seconds_spent += time.perf_counter() - started
        self.runs += 1
        try:
            with open(prefix + ".json", encoding="utf-8") as handle:
                found = json.load(handle)["transcription"]
        except (OSError, ValueError, KeyError):
            return []
        finally:
            for name in (wav_path, prefix + ".json"):
                if os.path.exists(name):
                    os.remove(name)
        return [(offset_s + item["offsets"]["from"] / 1000, offset_s + item["offsets"]["to"] / 1000,
                 item["text"].strip()) for item in found if item["text"].strip()]


def read_pcm(path: str) -> bytes:
    with wave.open(path, "rb") as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            sys.exit(f"{path}: needs 16 kHz mono 16-bit audio")
        return source.readframes(source.getnframes())


FINISHED = re.compile(r"[.?!][\"')\]]*$")


def join_unfinished(found: List[Segment], longest: float = 25.0) -> List[Segment]:
    """Joins each segment that does not end like a sentence to the one after it (up to `longest` seconds)."""
    out: List[Segment] = []
    for begin, finish, text in found:
        if out and not FINISHED.search(out[-1][2]) and finish - out[-1][0] <= longest:
            out[-1] = (out[-1][0], finish, out[-1][2] + " " + text)
        else:
            out.append((begin, finish, text))
    return out


def stream(whisper: Whisper, pcm: bytes, step: float, hold: float, max_buffer: float,
           rewind: float = 0.0) -> Tuple[List[Segment], int]:
    """Replays the recording as a live system would hear it. Returns (kept segments, how many were forced)."""
    total = len(pcm) / BYTES_PER_SECOND
    kept, forced = [], 0
    start, position, last_end = 0.0, step, 0.0
    while True:
        end = min(position, total)
        final = end >= total
        window = pcm[int(start * BYTES_PER_SECOND) // 2 * 2: int(end * BYTES_PER_SECOND) // 2 * 2]
        found = join_unfinished(whisper.segments(window, start)) if end - start >= 1.0 else []
        if not found:
            start = max(start, end - 2.0)                    # only noise in the buffer: keep a short lead-in
        else:
            found = [s for s in found if s[1] > last_end + 0.2]       # drop what was already kept (heard again in the rewind)
            done = [s for s in found if final or s[1] <= end - hold]
            if not done and end - start >= max_buffer:       # one long thought: do not let the buffer grow forever
                done, forced = found, forced + 1
            kept.extend(done)
            if done:
                last_end = done[-1][1]
                start = max(start, last_end - rewind)
        if final:
            return kept, forced
        position += step


def sentences(segments: List[Segment], fragmenter: SegmentFragmenter) -> List[str]:
    """The sentence fragments of each segment, in order."""
    out = []
    for _, _, text in segments:
        out.extend(f.text for f in fragmenter.split(text) if f.level is FragmentLevel.SENTENCE)
    return out


def plain(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]", " ", text.lower()).split())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("wav", help="a 16 kHz mono 16-bit recording")
    parser.add_argument("--step", type=float, default=10.0, help="seconds of new audio between whisper runs (default 10)")
    parser.add_argument("--hold", type=float, default=2.0,
                        help="a segment is kept once it ends this many seconds before the buffer's end (default 2)")
    parser.add_argument("--max-buffer", type=float, default=30.0, help="longest buffer before a segment is forced out")
    parser.add_argument("--rewind", type=float, default=0.4,
                        help="the next buffer starts this many seconds before the last kept segment's end (default 0.4)")
    parser.add_argument("--model", default="base.en", help="the whisper model (default base.en)")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--text", action="store_true", help="also print the words of each segment to the terminal")
    args = parser.parse_args()

    cli = os.environ.get("WHISPER_CLI") or shutil.which("whisper-cli") or sys.exit("whisper-cli not found")
    data = ROOT / "data" / "whisper"
    model, vad = data / f"ggml-{args.model}.bin", data / "ggml-silero-v6.2.0.bin"
    for needed in (model, vad):
        if not needed.exists():
            sys.exit(f"missing {needed}")
    pcm = read_pcm(args.wav)
    total = len(pcm) / BYTES_PER_SECOND
    fragmenter = SegmentFragmenter(max_words=40)
    whisper = Whisper(cli, model, vad, args.threads)
    try:
        streamed, forced = stream(whisper, pcm, args.step, args.hold, args.max_buffer, args.rewind)
        stream_runs, stream_seconds = whisper.runs, whisper.seconds_spent
        whole = join_unfinished(whisper.segments(pcm, 0.0), longest=1e9)   # the answer key: the whole file at once, joined the same way
    finally:
        whisper.close()

    streamed_sentences, key_sentences = sentences(streamed, fragmenter), sentences(whole, fragmenter)
    key = [plain(s) for s in key_sentences]
    got = [plain(s) for s in streamed_sentences]
    exact = len(set(key) & set(got))
    close = sum(max((difflib.SequenceMatcher(None, k, g).ratio() for g in got), default=0) >= 0.85 for k in key)
    stray = [s for s in streamed_sentences if max((difflib.SequenceMatcher(None, plain(s), k).ratio() for k in key),
                                                  default=0) < 0.8]
    unfinished = [s for s in streamed if not FINISHED.search(s[2])]
    several = [s for s in streamed if len(fragmenter.split(s[2])) and
               sum(f.level is FragmentLevel.SENTENCE for f in fragmenter.split(s[2])) > 1]

    print(f"{os.path.basename(args.wav)}: {total:.0f} s of audio; step {args.step:g} s, hold {args.hold:g} s")
    print(f"  streaming: {len(streamed)} segments, {len(streamed_sentences)} sentences, {forced} forced out at the "
          f"{args.max_buffer:g} s buffer limit")
    print(f"  whisper runs: {stream_runs}, {stream_seconds:.0f} s of whisper time for {total:.0f} s of audio "
          f"({stream_seconds / total:.2f}x real time)")
    print(f"  whole file at once (the answer key): {len(whole)} segments, {len(key_sentences)} sentences")
    print(f"  answer-key sentences found in the streaming output: {close} of {len(key)} "
          f"({100 * close / max(1, len(key)):.0f}%) at 85% similarity, {exact} word for word")
    print(f"  streaming sentences with no close answer-key sentence (possible broken pieces): {len(stray)}")
    print(f"  segments holding more than one sentence (the fragmenter splits them): {len(several)} of {len(streamed)}")
    print(f"  segments that do not end like a finished sentence (possible mid-sentence cuts): {len(unfinished)} of {len(streamed)}")
    print("\n  kept segments (start-end s, words" + (", text" if args.text else "") + "):")
    for begin, finish, text in streamed:
        line = f"    {begin:6.1f}-{finish:6.1f}  {len(text.split()):3d} words"
        print(line + (f"  {text}" if args.text else ""))


if __name__ == "__main__":
    main()
