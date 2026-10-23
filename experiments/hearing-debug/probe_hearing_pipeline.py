#!/usr/bin/env python3
"""Probe: watch how the Printer interprets what it hears, live, and refine the prompt as you go.

Part of experiments/hearing-debug. A prototype of the front half of the Printer, run on the Pi (or on
the Mac against a recording):

    microphone -> cut at pauses -> level normalized -> whisper -> sentences -> Scorer -> interpretation

For every sentence it prints the Scorer's score (the real `pom` Scorer, built from config/pom.toml) and,
a little later, the model's own written account of why: the content it found that earned the score,
from the debug prompt. Lines at or above the threshold are marked as ones that would trigger.

THE CLOSED LOOP. The config file is checked before every utterance. Edit the prompts in
config/pom.toml (push it to the Pi with probe_hearing_push.sh watch) and the next thing you say is
scored with the new wording. To compare the same words under two prompts, type `r` and Enter in this
terminal: it re-scores the last few sentences with the current prompts and shows old -> new.

WHAT IT KEEPS. This prints what the room says to YOUR TERMINAL and keeps nothing: audio is cut into a
temporary file in memory-backed storage (/dev/shm where there is one) and deleted as soon as whisper
has read it, the last few sentences sit in memory for the `r` command, and nothing is written to a log.
Do not redirect its output to a file, and do not run it as a service. The Printer's own rule is that no
transcript of the room is kept; this is a development tool used on purpose, by someone who knows.

Usage:
    probe_hearing_pipeline.py                       # listen on the Pi's microphone
    probe_hearing_pipeline.py --wav clip.wav        # replay a 16 kHz mono recording instead
    probe_hearing_pipeline.py --threshold 7.0 --model small.en --no-explain

While running, type: r (re-score the recent sentences), ? (help), q (quit).

Needs, besides Python 3.11 and the `pom` package (the repo's src/ is found automatically): arecord for
the microphone, whisper.cpp's whisper-cli with a model (WHISPER_DIR, WHISPER_CLI, WHISPER_MODEL_FILE; see
the README), and a running llama-server at the config's [scorer] url (probe_hearing_run.sh starts one).
Everything else is the standard library.
"""
import argparse
import array
import collections
import hashlib
import math
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import warnings

try:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import audioop                   # fast C routines for levels; gone from Python 3.13, so optional
except ImportError:                      # pragma: no cover
    audioop = None

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from pom.config import Config                                  # noqa: E402
from pom.hinge import InterestClient, Scorer, WordTools        # noqa: E402
from pom.hinge.interest_client import parse_written_score      # noqa: E402
from pom.listening.fragmenter import Fragmenter, Kind          # noqa: E402

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2               # 16-bit mono
FRAME_SECONDS = FRAME_MS / 1000


# --- Sound ------------------------------------------------------------------------------------

def frame_db(frame: bytes) -> float:
    """The level of one frame in dB relative to full scale (0 is the loudest possible)."""
    frame = frame[: len(frame) // 2 * 2]
    if audioop is not None:
        rms = audioop.rms(frame, 2) if frame else 0
        return 20 * math.log10(rms / 32768.0) if rms else -100.0
    samples = array.array("h")
    samples.frombytes(frame)
    if not samples:
        return -100.0
    mean_square = sum(s * s for s in samples) / len(samples)
    return 10 * math.log10(mean_square / 32768.0 ** 2) if mean_square > 0 else -100.0


def peak_db(pcm: bytes) -> float:
    """The loudest sample in dB relative to full scale."""
    pcm = pcm[: len(pcm) // 2 * 2]
    if audioop is not None:
        peak = audioop.max(pcm, 2) if pcm else 0
    else:
        samples = array.array("h")
        samples.frombytes(pcm)
        peak = max((abs(s) for s in samples), default=0)
    return 20 * math.log10(peak / 32768.0) if peak else -100.0


def normalize_pcm(pcm: bytes, target_peak_db: float = -3.0, max_gain_db: float = 30.0) -> bytes:
    """Raises a quiet utterance so its loudest sample sits at the target, never by more than max_gain_db.

    Whisper does much better on audio near full scale than on the quiet audio a microphone gives. Loud
    audio is left alone. This is the per-utterance level control the real Segmenter will need.
    """
    gain_db = min(target_peak_db - peak_db(pcm), max_gain_db)
    if gain_db <= 0.5:
        return pcm
    gain = 10 ** (gain_db / 20)
    if audioop is not None:
        return audioop.mul(pcm[: len(pcm) // 2 * 2], 2, gain)       # saturates instead of wrapping
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    louder = array.array("h", (max(-32768, min(32767, int(s * gain))) for s in samples))
    return louder.tobytes()


@dataclass
class Utterance:
    """A stretch of speech cut from the audio."""
    pcm: bytes
    seconds: float
    peak_db: float


class Segmenter:
    """Cuts a stream of 30 ms frames into utterances at pauses.

    It learns the room's noise floor from the first few seconds, then treats a frame as speech if it is
    `margin_db` above the floor (and above an absolute minimum). An utterance starts after a few speech
    frames in a row (with a little sound from before), and ends after `pause_s` of quiet or at `max_s`.
    Utterances with too little real speech in them are dropped. A simple stand-in for the real Segmenter.
    """

    def __init__(self, calibrate_s=2.0, margin_db=6.0, min_threshold_db=-58.0, pause_s=0.7, max_s=20.0,
                 min_speech_s=0.4, preroll_s=0.3, post_s=0.3, start_frames=3):
        self._calibrate_frames = int(calibrate_s / FRAME_SECONDS)
        self._margin, self._min_threshold = margin_db, min_threshold_db
        self._pause_frames = int(pause_s / FRAME_SECONDS)
        self._max_frames = int(max_s / FRAME_SECONDS)
        self._min_speech_frames = int(min_speech_s / FRAME_SECONDS)
        self._post_frames = int(post_s / FRAME_SECONDS)
        self._start_frames = start_frames
        self._preroll = collections.deque(maxlen=int(preroll_s / FRAME_SECONDS))
        self._seen = []                    # frame levels while calibrating
        self.floor_db: Optional[float] = None
        self.threshold_db: Optional[float] = None
        self._candidate = []               # consecutive loud frames not yet an utterance
        self._active = None                # frames of the utterance in progress
        self._silence = 0
        self._speech = 0

    def feed(self, frame: bytes) -> Optional[Utterance]:
        """Takes one frame; returns an Utterance when one has just ended."""
        level = frame_db(frame)
        if self.floor_db is None:
            self._seen.append(level)
            if len(self._seen) >= self._calibrate_frames:
                self.floor_db = sorted(self._seen)[len(self._seen) // 4]      # a low quartile: speech may be present
                self._set_threshold()
            return None
        loud = level > self.threshold_db
        if self._active is None:
            if not loud:
                self.floor_db = 0.99 * self.floor_db + 0.01 * level           # follow slow drift in the room
                self._set_threshold()
                self._preroll.append(frame)
                self._candidate = []
                return None
            self._candidate.append(frame)
            if len(self._candidate) >= self._start_frames:
                self._active = list(self._preroll) + self._candidate
                self._speech, self._silence, self._candidate = len(self._candidate), 0, []
                self._preroll.clear()
            return None
        self._active.append(frame)
        if loud:
            self._speech += 1
            self._silence = 0
        else:
            self._silence += 1
        if self._silence >= self._pause_frames or len(self._active) >= self._max_frames:
            return self._finish()
        return None

    def flush(self) -> Optional[Utterance]:
        """Ends any utterance in progress (at the end of a recording)."""
        return self._finish() if self._active is not None else None

    def _set_threshold(self) -> None:
        self.threshold_db = max(self.floor_db + self._margin, self._min_threshold)

    def _finish(self) -> Optional[Utterance]:
        frames, speech, silence = self._active, self._speech, self._silence
        self._active, self._silence, self._speech = None, 0, 0
        if speech < self._min_speech_frames:
            return None
        if silence > self._post_frames:                      # keep a little quiet after, not all of it
            frames = frames[: len(frames) - (silence - self._post_frames)]
        pcm = b"".join(frames)
        return Utterance(pcm, len(frames) * FRAME_SECONDS, peak_db(pcm))


# --- Where the sound comes from ---------------------------------------------------------------

class MicSource:
    """Frames from the microphone, through arecord."""

    def __init__(self, device: str):
        if not shutil.which("arecord"):
            sys.exit("arecord not found; this is meant to run on the Pi (use --wav to replay a recording)")
        self._proc = subprocess.Popen(["arecord", "-q", "-D", device, "-f", "S16_LE", "-r", str(SAMPLE_RATE),
                                       "-c", "1", "-t", "raw"], stdout=subprocess.PIPE)

    def frames(self):
        while True:
            data = self._proc.stdout.read(FRAME_BYTES)
            if len(data) < FRAME_BYTES:
                return
            yield data

    def close(self) -> None:
        self._proc.terminate()


class WavSource:
    """Frames from a recording, paced like real time (speed 1) or faster (speed 0 means flat out)."""

    def __init__(self, path: str, speed: float = 1.0):
        self._wav, self._speed = wave.open(path, "rb"), speed
        if (self._wav.getframerate(), self._wav.getnchannels(), self._wav.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            sys.exit(f"{path} must be 16 kHz mono 16-bit; convert it with: ffmpeg -i in -ar 16000 -ac 1 out.wav")

    def frames(self):
        per_frame = FRAME_BYTES // 2
        while True:
            data = self._wav.readframes(per_frame)
            if len(data) < FRAME_BYTES:
                break
            yield data
            if self._speed > 0:
                time.sleep(FRAME_SECONDS / self._speed)
        for _ in range(int(1.2 / FRAME_SECONDS)):          # a pause at the end so the last utterance ends
            yield b"\x00" * FRAME_BYTES

    def close(self) -> None:
        self._wav.close()


# --- Words ------------------------------------------------------------------------------------

class Transcriber:
    """Turns an utterance into text with whisper.cpp's whisper-cli."""

    def __init__(self, model: str, threads: int):
        root = Path(os.environ.get("WHISPER_DIR", Path.home() / "whisper.cpp"))
        candidates = [os.environ.get("WHISPER_CLI"), str(root / "build" / "bin" / "whisper-cli"),
                      shutil.which("whisper-cli")]
        self._cli = next((c for c in candidates if c and Path(c).exists()), None)
        models = [os.environ.get("WHISPER_MODEL_FILE"), str(root / "models" / f"ggml-{model}.bin"),
                  str(ROOT / "data" / "whisper" / f"ggml-{model}.bin")]
        self._model = next((m for m in models if m and Path(m).exists()), None)
        if not self._cli or not self._model:
            sys.exit(f"whisper not found (cli: {self._cli}, model: {self._model}); set WHISPER_DIR, "
                     f"WHISPER_CLI or WHISPER_MODEL_FILE (see the README)")
        self._threads = threads
        self._scratch = "/dev/shm" if os.access("/dev/shm", os.W_OK) else tempfile.gettempdir()

    def transcribe(self, pcm: bytes) -> str:
        """The cleaned text of an utterance ("" if whisper heard nothing). The audio file is deleted at once."""
        handle = tempfile.NamedTemporaryFile(suffix=".wav", dir=self._scratch, delete=False)
        try:
            with wave.open(handle, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(SAMPLE_RATE)
                wav.writeframes(pcm)
            done = subprocess.run([self._cli, "-m", self._model, "-f", handle.name, "-t", str(self._threads),
                                   "-nt"], capture_output=True, text=True, timeout=180)
            return clean_transcript(done.stdout)
        except subprocess.TimeoutExpired:
            return ""
        finally:
            handle.close()
            os.unlink(handle.name)


def clean_transcript(raw: str) -> str:
    """Whisper's text with the music symbol removed (the words stay) and its silence marker dropped.

    Whisper's other bracketed sound captions, like "(dog barks)", are kept on purpose: they give the
    model more to go on.
    """
    text = " ".join(raw.replace("♪", " ").split())
    return " ".join(text.replace("[BLANK_AUDIO]", " ").split())


# --- The Printer's judgment, from the real pom modules ----------------------------------------

def short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:6]


class Brain:
    """The real Scorer and interest client, rebuilt whenever config/pom.toml changes."""

    def __init__(self, config_path: Path, words: WordTools):
        self._path, self._words = config_path, words
        self._mtime = None
        self.settings = None
        self.client = None
        self.scorer = None
        self.refresh()

    def refresh(self) -> Optional[str]:
        """Reloads the config if the file changed; returns a one-line description of what changed, or None."""
        try:
            mtime = self._path.stat().st_mtime_ns
        except OSError:
            mtime = -1                                       # no file: Config falls back to its defaults
        if mtime == self._mtime:
            return None
        first = self.settings is None
        before = self.settings
        self._mtime = mtime
        config = Config.load(self._path)
        settings = config.scorer
        client = InterestClient(settings.url, settings.prompt, settings.timeout, debug_prompt=settings.prompt_debug)
        self.scorer = Scorer(client, self._words, floor=settings.floor, min_content_words=settings.min_content_words,
                             failure_limit=settings.failure_limit, retry_seconds=settings.retry_seconds)
        self.client, self.settings = client, settings
        problems = config.report.lines() + client.report.lines()
        note = "; ".join(problems)
        if first:
            return f"config loaded{(' (problems: ' + note + ')') if note else ''}"
        changed = [name for name in ("prompt_scoring", "prompt_output", "prompt_output_debug", "url", "floor",
                                     "min_content_words")
                   if getattr(before, name) != getattr(settings, name)]
        return (f"config reloaded: {', '.join(changed) if changed else 'no scorer setting changed'}"
                f"{(' (problems: ' + note + ')') if note else ''}")

    def fingerprint(self) -> str:
        s = self.settings
        return (f"scoring {short_hash(s.prompt_scoring)}  output {short_hash(s.prompt_output)}  "
                f"debug {short_hash(s.prompt_output_debug)}")


# --- The display ------------------------------------------------------------------------------

class Display:
    """Prints to the terminal, one thread at a time."""

    def __init__(self, threshold: float):
        self._lock = threading.Lock()
        self.threshold = threshold

    def line(self, text: str = "") -> None:
        with self._lock:
            print(text, flush=True)

    def stamp(self) -> str:
        return time.strftime("%H:%M:%S")

    def sentence(self, number: int, score: float, source: str, text: str, previous: Optional[float] = None) -> None:
        mark = f"   <-- would trigger (>= {self.threshold:g})" if score >= self.threshold else ""
        was = f"   (was {previous:.2f})" if previous is not None else ""
        self.line(f"  #{number:<3} {score:5.2f}  {source:<8} {text}{was}{mark}")

    def interpretation(self, number: int, reply: str, digit_score: float, seconds: float) -> None:
        written = parse_written_score(reply)
        drift = f", written {written:.2f} vs scored {digit_score:.2f}" if written is not None else ""
        wrapped = textwrap.fill(reply, width=96, initial_indent=f"  #{number:<3} says: ",
                                subsequent_indent=" " * 12)
        self.line(f"{wrapped}\n        ({seconds:.1f} s{drift})")


# --- The pipeline -----------------------------------------------------------------------------

@dataclass
class Heard:
    number: int
    text: str
    score: float
    source: str


class Pipeline:
    def __init__(self, args, brain: Brain, display: Display):
        self.args, self.brain, self.display = args, brain, display
        self.transcriber = Transcriber(args.model, args.whisper_threads)
        self.fragmenter = Fragmenter(max_words=40)
        self.utterances = queue.Queue(maxsize=6)
        self.explain_queue = queue.Queue(maxsize=8)
        self.history = collections.deque(maxlen=args.history)       # in memory only; for the `r` command
        self.count = 0
        self.last_text = ""
        self.stop = threading.Event()

    # capture and cutting
    def listen(self, source) -> None:
        segmenter = Segmenter(margin_db=self.args.margin_db)
        announced = False
        try:
            for frame in source.frames():
                if self.stop.is_set():
                    break
                utterance = segmenter.feed(frame)
                if segmenter.floor_db is not None and not announced:
                    announced = True
                    self.display.line(f"  room noise floor {segmenter.floor_db:.0f} dB; speech is anything above "
                                      f"{segmenter.threshold_db:.0f} dB. Listening.")
                if utterance:
                    self._offer(utterance)
            tail = segmenter.flush()
            if tail:
                self._offer(tail)
        finally:
            source.close()
            self.utterances.put(None)

    def _offer(self, utterance: Utterance) -> None:
        try:
            self.utterances.put_nowait(utterance)
        except queue.Full:
            try:
                self.utterances.get_nowait()                     # fall behind by dropping the oldest
                self.display.line("  (transcription is behind; dropped the oldest utterance)")
            except queue.Empty:
                pass
            self.utterances.put_nowait(utterance)

    # transcribing and scoring
    def transcribe_and_score(self) -> None:
        while True:
            utterance = self.utterances.get()
            if utterance is None:
                self.explain_queue.put(None)
                if self.args.wav:
                    self.display.line("\n  (the recording is done; type r to re-score its sentences with the "
                                      "current prompts, q to quit)")
                return
            try:
                self._handle(utterance)
            except Exception as error:               # a surprise must not end the session
                self.display.line(f"  (error handling an utterance: {type(error).__name__})")

    def _handle(self, utterance: Utterance) -> None:
        """Transcribes one utterance, then scores each sentence of it and queues the interpretations."""
        changed = self.brain.refresh()
        if changed:
            self.display.line(f"\n[{self.display.stamp()}] {changed}  [{self.brain.fingerprint()}]")
        started = time.perf_counter()
        text = self.transcriber.transcribe(normalize_pcm(utterance.pcm))
        took = time.perf_counter() - started
        if not text or text == self.last_text:
            return
        self.last_text = text
        self.display.line(f"\n[{self.display.stamp()}] heard {utterance.seconds:.1f} s (peak {utterance.peak_db:.0f} dB, "
                          f"whisper {took:.1f} s): \"{text}\"")
        for fragment in self.fragmenter.split(text):
            if fragment.kind is not Kind.SENTENCE:
                continue
            scored = self.brain.scorer.score(fragment)
            self.count += 1
            self.history.append(Heard(self.count, fragment.text, scored.score, scored.source.value))
            self.display.sentence(self.count, scored.score, scored.source.value, fragment.text)
            if self.args.explain and scored.source.value == "model" and scored.score >= self.args.explain_above:
                self._explain(self.count, fragment.text, scored.score)

    def _explain(self, number: int, text: str, score: float) -> None:
        try:
            self.explain_queue.put_nowait((number, text, score))
        except queue.Full:
            try:
                skipped = self.explain_queue.get_nowait()
                self.display.line(f"  (explanations are behind; skipped #{skipped[0]})")
            except queue.Empty:
                pass
            self.explain_queue.put_nowait((number, text, score))

    def explain_worker(self) -> None:
        while True:
            item = self.explain_queue.get()
            if item is None:
                return
            number, text, score = item
            client = self.brain.client
            if not client.can_interpret:
                continue
            reply = client.interpret(text)
            if reply:
                self.display.interpretation(number, reply, score, client.last_seconds or 0.0)
            else:
                self.display.line(f"  #{number:<3} (no interpretation: {'; '.join(client.report.lines()[-1:]) or 'no reply'})")

    # commands typed at the terminal
    def rescore(self) -> None:
        changed = self.brain.refresh()
        self.display.line(f"\n[{self.display.stamp()}] re-scoring the last {len(self.history)} sentences "
                          f"with [{self.brain.fingerprint()}]" + (f" ({changed})" if changed else ""))
        for item in list(self.history):
            scored = self.brain.scorer.score_text(item.text)
            self.display.sentence(item.number, scored.score, scored.source.value, item.text, previous=item.score)
            item.score, item.source = scored.score, scored.source.value
            if self.args.explain and scored.source.value == "model":
                self._explain(item.number, item.text, scored.score)

    def commands(self) -> None:
        for line in sys.stdin:
            word = line.strip().lower()
            if word == "r":
                self.rescore()
            elif word == "q":
                break
            elif word in ("?", "h", "help"):
                self.display.line("  r = re-score the recent sentences with the current prompts   q = quit")
        self.stop.set()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", default=str(ROOT / "config" / "pom.toml"), help="the config file to read and watch")
    parser.add_argument("--wav", help="replay this 16 kHz mono recording instead of listening")
    parser.add_argument("--speed", type=float, default=1.0, help="replay speed (0 = as fast as possible)")
    parser.add_argument("--device", default="plughw:0,0", help="the arecord capture device")
    parser.add_argument("--model", default="base.en", help="the whisper model (base.en or small.en)")
    parser.add_argument("--whisper-threads", type=int, default=4)
    parser.add_argument("--threshold", type=float, default=7.0, help="mark scores at or above this (default 7.0)")
    parser.add_argument("--no-explain", dest="explain", action="store_false", help="skip the written interpretations")
    parser.add_argument("--explain-above", type=float, default=0.0, help="only interpret scores at or above this")
    parser.add_argument("--history", type=int, default=12, help="sentences kept in memory for the r command")
    parser.add_argument("--margin-db", type=float, default=6.0,
                        help="how far above the room noise counts as speech (lower it if music or noise masks speech)")
    args = parser.parse_args()

    nltk_dir = ROOT / "data" / "nltk"
    brain = Brain(Path(args.config), WordTools(nltk_data=str(nltk_dir) if nltk_dir.exists() else None))
    display = Display(args.threshold)
    display.line("hearing-debug: what you say is shown here and kept nowhere. Do not redirect this output to a file.")
    display.line(f"  {brain.refresh() or 'config loaded'}  [{brain.fingerprint()}]")
    display.line(f"  scorer at {brain.settings.url}; whisper {args.model}; threshold {args.threshold:g}; "
                 f"{'interpretations on' if args.explain and brain.client.can_interpret else 'interpretations off'}")
    display.line(f"  model warm-up: {'ready' if brain.scorer.warm_up() else 'NOT answering (scores will be fallback)'}")
    if args.explain and not brain.client.can_interpret:
        display.line("  (no prompt_output_debug in the config, so there are no interpretations)")

    pipeline = Pipeline(args, brain, display)
    source = WavSource(args.wav, args.speed) if args.wav else MicSource(args.device)
    threads = [threading.Thread(target=pipeline.listen, args=(source,), daemon=True),
               threading.Thread(target=pipeline.transcribe_and_score, daemon=True),
               threading.Thread(target=pipeline.explain_worker, daemon=True)]
    for thread in threads:
        thread.start()
    display.line("  type r + Enter to re-score the recent sentences with the current prompts, q to quit\n")
    try:
        if sys.stdin.isatty():
            pipeline.commands()                           # a recording can be re-scored after it has played
        else:
            for thread in threads:
                thread.join()                             # no terminal to type at: play through, then finish
    except KeyboardInterrupt:
        pass
    pipeline.stop.set()
    source.close()


if __name__ == "__main__":
    main()
