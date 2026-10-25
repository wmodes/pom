#!/usr/bin/env python3
"""Probe: can whisper's own voice detection find the cut points, so we need no energy chunker?

Part of experiments/hearing-debug. The live pipeline cuts the microphone into utterances with a simple
energy detector, which in a room with fluctuating noise (street, music, a hookah) never sees a pause and
cuts every utterance by the clock, in the middle of a thought. This probe tests the alternative on a
recording, with no microphone and no language model:

  * A rolling buffer is handed to whisper-server, with whisper's own voice detection on, every `--step`
    seconds, as a live system would. Whisper returns segments with timestamps. The script starts its own
    whisper-server for the run and stops it at the end (or uses one you name with `--server`).
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
nothing is written to a file: audio goes to the server from memory.

Usage:
    probe_whisper_segments.py recording.wav
    probe_whisper_segments.py recording.wav --step 8 --hold 2 --text
    probe_whisper_segments.py recording.wav --server http://127.0.0.1:8110     # use a server that is already running
"""
import argparse
import difflib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from pom.listening.segment_fragmenter import FragmentLevel, SegmentFragmenter    # noqa: E402
from rolling_cutter import BYTES_PER_SECOND, FINISHED, RollingCutter, join_unfinished   # noqa: E402  (next to this file)
from whisper_server_client import SAMPLE_RATE, WhisperServerClient                   # noqa: E402  (next to this file)

Segment = Tuple[float, float, str]                     # absolute start, absolute end, text


class Whisper:
    """Transcribes slices of audio through a whisper-server (one of its own, or one already running).

    Counts the requests and the time they took, so the cost of the rolling buffer can be reported.
    """

    def __init__(self, url: Optional[str], binary: Optional[str], model: Path, vad_model: Path, threads: int):
        self._process = None
        if url is None:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]                         # a free port for our own server
            url = f"http://127.0.0.1:{port}"
            self._process = subprocess.Popen([binary, "-m", str(model), "--vad", "-vm", str(vad_model), "--port", str(port),
                                              "-t", str(threads)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.client = WhisperServerClient(url)
        for _ in range(120):                                          # the model takes a moment to load
            if self.client.ping():
                break
            if self._process is not None and self._process.poll() is not None:
                sys.exit("the whisper-server exited at startup")
            time.sleep(0.5)
        else:
            sys.exit(f"the whisper-server is not answering at {url}")
        self.runs, self.seconds_spent = 0, 0.0

    def close(self) -> None:
        if self._process is not None:
            self._process.terminate()
            self._process.wait(timeout=10)

    @property
    def error(self) -> Optional[str]:
        return self.client.error

    def segments(self, pcm: bytes, offset_s: float, vad: bool = True) -> List[Segment]:
        """Transcribes the audio; times are shifted by `offset_s` so they are positions in the whole recording."""
        started = time.perf_counter()
        found = self.client.segments(pcm, offset_s, vad=vad)
        self.seconds_spent += time.perf_counter() - started
        self.runs += 1
        return found


def read_pcm(path: str) -> bytes:
    with wave.open(path, "rb") as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            sys.exit(f"{path}: needs 16 kHz mono 16-bit audio")
        return source.readframes(source.getnframes())


def stream(whisper: Whisper, pcm: bytes, step: float, hold: float, max_buffer: float,
           rewind: float = 0.0) -> Tuple[List[Segment], int]:
    """Replays the recording through the RollingCutter as a live stream would arrive. Returns (kept segments, forced)."""
    cutter = RollingCutter(whisper, step, hold, rewind, max_buffer)
    kept: List[Segment] = []
    size = int(step * BYTES_PER_SECOND) // 2 * 2
    for index in range(0, len(pcm), size):
        cutter.feed(pcm[index:index + size])
        while cutter.due():
            kept.extend(cutter.cut())
    kept.extend(cutter.cut(final=True))
    return kept, cutter.forced


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
    parser.add_argument("--rewind", type=float, default=0.0,
                        help="the next buffer starts this many seconds before the last kept segment's end (default 0, off; it re-hears the last word)")
    parser.add_argument("--server", help="the address of a whisper-server that is already running (default: start our own)")
    parser.add_argument("--model", default="base.en", help="the whisper model for our own server (default base.en)")
    parser.add_argument("--threads", type=int, default=4, help="threads for our own server")
    parser.add_argument("--text", action="store_true", help="also print the words of each segment to the terminal")
    args = parser.parse_args()

    binary = os.environ.get("WHISPER_SERVER") or shutil.which("whisper-server")
    data = ROOT / "data" / "whisper"
    model, vad = data / f"ggml-{args.model}.bin", data / "ggml-silero-v6.2.0.bin"
    if not args.server:
        for needed in (binary, model, vad):
            if not needed or not Path(needed).exists():
                sys.exit(f"missing {needed or 'whisper-server (set WHISPER_SERVER or put it on the PATH)'}")
    pcm = read_pcm(args.wav)
    total = len(pcm) / BYTES_PER_SECOND
    fragmenter = SegmentFragmenter(max_words=40)
    whisper = Whisper(args.server, binary, model, vad, args.threads)
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
