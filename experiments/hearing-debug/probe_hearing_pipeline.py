#!/usr/bin/env python3
"""Probe: watch how the Printer interprets what it hears, live, and refine the prompt as you go.

Part of experiments/hearing-debug. A prototype of the front half of the Printer, run on the Pi (or on
the Mac against a recording):

    microphone -> cut at pauses -> level normalized -> whisper -> sentences -> Scorer -> interpretation

For every sentence it prints the Scorer's score (the real `pom` Scorer, built from config/pom.toml) and,
a little later, the model's own written account of why: the content it found that earned the score,
from the debug prompt. Lines at or above the threshold are marked as ones that would trigger.

THE CLOSED LOOP. The config file is checked before every utterance. Edit the prompts in
config/pom.toml (deliver it with git using probe_hearing_tune.sh watch) and the next thing you say is
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
import re
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

    The room's noise floor is not measured once at the start (the Printer may be switched on in a room
    that is already loud): it is re-estimated all the time as a low percentile of the last half minute of
    levels. Even in a room full of chatter, speech has dips between words and syllables, and those dips
    mark the floor. A frame counts as speech if it is `margin_db` above the floor (and above an absolute
    minimum). An utterance starts after a few speech frames in a row (with a little sound from before),
    and ends after `pause_s` of quiet or at `max_s`; utterances with too little real speech are dropped.
    A simple stand-in for the real Segmenter, and not able to tell speech from music or other steady noise.
    """

    def __init__(self, warmup_s=1.0, margin_db=6.0, min_threshold_db=-58.0, pause_s=0.7, max_s=20.0,
                 min_speech_s=0.4, preroll_s=0.3, post_s=0.3, start_frames=3, window_s=30.0,
                 floor_percentile=15.0):
        self._warmup_frames = int(warmup_s / FRAME_SECONDS)
        self._levels = collections.deque(maxlen=int(window_s / FRAME_SECONDS))   # the recent past
        self._percentile = floor_percentile
        self._since_update = 0
        self._margin, self._min_threshold = margin_db, min_threshold_db
        self._pause_frames = int(pause_s / FRAME_SECONDS)
        self._max_frames = int(max_s / FRAME_SECONDS)
        self._min_speech_frames = int(min_speech_s / FRAME_SECONDS)
        self._post_frames = int(post_s / FRAME_SECONDS)
        self._start_frames = start_frames
        self._preroll = collections.deque(maxlen=int(preroll_s / FRAME_SECONDS))
        self.floor_db: Optional[float] = None
        self.threshold_db: Optional[float] = None
        self._candidate = []               # consecutive loud frames not yet an utterance
        self._recent = collections.deque(maxlen=int(5 / FRAME_SECONDS))     # the last five seconds of levels
        self._frames_since_loud = 0        # how long ago anything was above the speech threshold, in frames
        self._active = None                # frames of the utterance in progress
        self._silence = 0
        self._speech = 0

    def feed(self, frame: bytes) -> Optional[Utterance]:
        """Takes one frame; returns an Utterance when one has just ended."""
        level = frame_db(frame)
        self._recent.append(level)
        self._levels.append(level)
        self._since_update += 1
        if self.floor_db is None and len(self._levels) >= self._warmup_frames:
            self._update_floor()
        elif self.floor_db is not None and self._since_update >= int(1.0 / FRAME_SECONDS):
            self._update_floor()                                       # once a second, from the last half minute
        if self.floor_db is None:
            return None
        loud = level > self.threshold_db
        self._frames_since_loud = 0 if loud else self._frames_since_loud + 1
        if self._active is None:
            if not loud:
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

    @property
    def quiet_seconds(self) -> float:
        """How long the audio has been below the speech threshold, in seconds of audio (0 while it is loud)."""
        return self._frames_since_loud * FRAME_SECONDS

    @property
    def loudest_recent_db(self) -> Optional[float]:
        """The loudest frame in the last five seconds, to explain why nothing is being heard."""
        return max(self._recent) if self._recent else None

    def describe_levels(self) -> str:
        """The room's levels in words: what the loudest recent sound was against what counts as speech."""
        if self.floor_db is None:
            return "still measuring the room's noise"
        return (f"noise floor {self.floor_db:.0f} dB, speech threshold {self.threshold_db:.0f} dB, "
                f"loudest sound in the last 5 s {self.loudest_recent_db:.0f} dB")

    def flush(self) -> Optional[Utterance]:
        """Ends any utterance in progress (at the end of a recording)."""
        return self._finish() if self._active is not None else None

    def _update_floor(self) -> None:
        """The floor is a low percentile of the recent levels; the speech threshold sits a margin above it."""
        ordered = sorted(self._levels)
        self.floor_db = ordered[min(len(ordered) - 1, int(len(ordered) * self._percentile / 100))]
        self.threshold_db = max(self.floor_db + self._margin, self._min_threshold)
        self._since_update = 0

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
    """Turns an utterance into text with whisper.cpp's whisper-cli, using whisper's own voice detection.

    Whether something is speech is decided by whisper's built-in Silero VAD (`--vad`), the intended tool,
    not by a threshold of mine. With it, whisper only sees the speech, so it stops inventing words for
    silence and noise ("You", "I'm sorry") and hears quiet speech better.

    The VAD is a speech detector and treats singing, and most other sounds, as not speech. The Printer is
    meant to react to what it hears, so when pass 1 (VAD on) finds no speech, pass 2 runs whisper without
    the VAD and keeps only what whisper itself marks as a non-speech sound: song lyrics between music
    symbols and captions like "(dog barks)". Plain text from that pass is where whisper hallucinates, so it
    is thrown away.
    """

    VAD_MODEL = "ggml-silero-v6.2.0.bin"

    def __init__(self, model: str, threads: int, use_vad: bool = True, vad_threshold: float = 0.5,
                 sounds: bool = True):
        root = Path(os.environ.get("WHISPER_DIR", ROOT / "data" / "tools" / "whisper.cpp"))
        candidates = [os.environ.get("WHISPER_CLI"), str(root / "build" / "bin" / "whisper-cli"),
                      shutil.which("whisper-cli")]
        self._cli = next((c for c in candidates if c and Path(c).exists()), None)
        models = [os.environ.get("WHISPER_MODEL_FILE"), str(root / "models" / f"ggml-{model}.bin"),
                  str(ROOT / "data" / "whisper" / f"ggml-{model}.bin")]
        self._model = next((m for m in models if m and Path(m).exists()), None)
        if not self._cli or not self._model:
            sys.exit(f"whisper not found (cli: {self._cli}, model: {self._model}); set WHISPER_DIR, "
                     f"WHISPER_CLI or WHISPER_MODEL_FILE (see the README)")
        vads = [os.environ.get("WHISPER_VAD_MODEL"), str(root / "models" / self.VAD_MODEL),
                str(ROOT / "data" / "whisper" / self.VAD_MODEL)]
        self._vad = next((v for v in vads if v and Path(v).exists()), None) if use_vad else None
        self.note = ""
        if use_vad and not self._vad:
            self.note = "no VAD model found (scripts/setup/setup-whisper.sh fetches it); transcribing without it"
        self._vad_threshold = vad_threshold
        self._sounds = sounds and self._vad is not None          # pass 2 only makes sense after a VAD pass 1
        self._threads = threads
        self._scratch = "/dev/shm" if os.access("/dev/shm", os.W_OK) else tempfile.gettempdir()

    @property
    def vad_in_use(self) -> bool:
        return self._vad is not None

    def transcribe(self, pcm: bytes):
        """(text, kind): kind is "speech", "sound" (music or a caption, not speech) or "" if nothing was heard.

        The audio file is in memory-backed storage and is deleted as soon as whisper has read it.
        """
        handle = tempfile.NamedTemporaryFile(suffix=".wav", dir=self._scratch, delete=False)
        try:
            with wave.open(handle, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(SAMPLE_RATE)
                wav.writeframes(pcm)
            raw = self._run(handle.name, vad=self._vad is not None)
            text = clean_transcript(raw)
            if text:
                return text, "sound" if is_only_sound(raw) else "speech"
            if self._sounds and not raw.strip():          # the VAD found no speech at all; if whisper said "silence" it is
                heard = sound_text(self._run(handle.name, vad=False))
                if heard:
                    return heard, "sound"
            return "", ""
        except subprocess.TimeoutExpired:
            return "", ""
        finally:
            handle.close()
            os.unlink(handle.name)

    def _run(self, path: str, vad: bool) -> str:
        command = [self._cli, "-m", self._model, "-f", path, "-t", str(self._threads), "-nt"]
        if vad:
            command += ["--vad", "-vm", self._vad, "-vt", str(self._vad_threshold)]
        return subprocess.run(command, capture_output=True, text=True, timeout=180).stdout


# Whisper's own way of saying "there is nothing here". They carry no information, so they are not scored.
EMPTY_MARKS = re.compile(r"[\[(]\s*(blank[_ ]audio|silence|silent|no audio|no speech)\s*[\])]", re.IGNORECASE)


def clean_transcript(raw: str) -> str:
    """Whisper's text with the music symbol removed (the words stay) and its "nothing here" markers dropped.

    Whisper's other bracketed sound captions, like "(dog barks)", are kept on purpose: they give the
    model more to go on.
    """
    text = " ".join(raw.replace("\u266a", " ").split())
    return " ".join(EMPTY_MARKS.sub(" ", text).split())


SOUND_MARKS = r"\u266a[^\u266a]+\u266a|\([^()]*\)|\[[^\[\]]*\]"


def is_only_sound(raw: str) -> bool:
    """True if whisper's text is nothing but non-speech marks: lyrics between music symbols, (captions), [captions]."""
    return not re.sub(SOUND_MARKS, " ", EMPTY_MARKS.sub(" ", raw)).strip()


def sound_text(raw: str) -> str:
    """Only the pieces whisper marks as non-speech: lyrics between music symbols and (captions) or [captions].

    Used on the pass without the VAD, where plain text is more likely invented than heard.
    """
    pieces = re.findall(SOUND_MARKS, raw)
    return clean_transcript(" ".join(pieces))


# --- The Printer's judgment, from the real pom modules ----------------------------------------

def short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:6]


class Brain:
    """The real Scorer and interest client, rebuilt whenever config/pom.toml changes."""

    def __init__(self, config_path: Path, words: WordTools, score_timeout: float = 0.0):
        self._path, self._words = config_path, words
        self._score_timeout = score_timeout        # at least this long to wait for a score (0 = as configured)
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
        client = InterestClient(settings.url, settings.prompt, max(settings.timeout, self._score_timeout),
                                debug_prompt=settings.prompt_debug)
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

    def interpretation(self, number: int, run, production_score: float, seconds: float) -> None:
        """One debug run: the number and the reasons from the same output, beside the score the Printer itself gave."""
        written = parse_written_score(run.text)
        this_run = f"this run's probabilities {run.score:.2f}" if run.score is not None else "no digit to score from"
        wrote = f", it wrote {written:.2f}" if written is not None else ""
        wrapped = textwrap.fill(run.text, width=96, initial_indent=f"  #{number:<3} says: ",
                                subsequent_indent=" " * 12)
        self.line(f"{wrapped}\n        ({seconds:.1f} s; {this_run}{wrote}; the Printer's own score {production_score:.2f})")


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
        self.transcriber = Transcriber(args.model, args.whisper_threads, use_vad=args.vad,
                                       vad_threshold=args.vad_threshold, sounds=args.sounds)
        self.fragmenter = Fragmenter(max_words=40)
        self.utterances = queue.Queue(maxsize=6)
        self.explain_queue = queue.Queue(maxsize=8)
        self.history = collections.deque(maxlen=args.history)       # in memory only; for the `r` command
        self.count = 0
        self.last_text = ""
        self.working = False                                         # True while an utterance is being handled
        self.source_done = False                                     # True once a recording has played out
        self.segmenter = None                                        # set once listening starts
        self.interactive = sys.stdin.isatty()                        # someone can type r, e, l, q
        self.stop = threading.Event()

    @staticmethod
    def vad_note(args) -> Optional[str]:
        """A sentence if the VAD is switched off, else None (the Transcriber reports a missing VAD model itself)."""
        return None if args.vad else "VAD off"

    # capture and cutting
    def listen(self, source) -> None:
        segmenter = self.segmenter = Segmenter(margin_db=self.args.margin_db)
        announced = False
        silent_frames = 0
        try:
            for frame in source.frames():
                if self.stop.is_set():
                    break
                utterance = segmenter.feed(frame)
                silent_frames = 0 if utterance else silent_frames + 1
                if announced and silent_frames and silent_frames % int(30 / FRAME_SECONDS) == 0:
                    self.display.line(f"  (nothing heard in the last 30 s: {segmenter.describe_levels()}; "
                                      f"if you are speaking, try --margin-db 3)")
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
            self.source_done = True
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
                if not self.interactive:
                    self.explain_queue.put(None)                       # nobody can type e later: finish up
                if self.args.wav:
                    self.display.line("\n  (the recording is done; type r to re-score its sentences with the "
                                      "current prompts, e to explain them, q to quit)")
                return
            self.working = True
            try:
                self._handle(utterance)
            except Exception as error:               # a surprise must not end the session
                self.display.line(f"  (error handling an utterance: {type(error).__name__})")
            finally:
                self.working = False

    def _handle(self, utterance: Utterance) -> None:
        """Transcribes one utterance, then scores each sentence of it and queues the interpretations."""
        changed = self.brain.refresh()
        if changed:
            self.display.line(f"\n[{self.display.stamp()}] {changed}  [{self.brain.fingerprint()}]")
        started = time.perf_counter()
        text, kind = self.transcriber.transcribe(normalize_pcm(utterance.pcm))
        took = time.perf_counter() - started
        if not text or text == self.last_text:
            return
        self.last_text = text
        label = "sound, not speech" if kind == "sound" else "speech"
        self.display.line(f"\n[{self.display.stamp()}] heard {utterance.seconds:.1f} s ({label}, peak {utterance.peak_db:.0f} dB, "
                          f"whisper {took:.1f} s): \"{text}\"")
        for fragment in self.fragmenter.split(text):
            if fragment.kind is not Kind.SENTENCE:
                continue
            scored = self.brain.scorer.score(fragment)
            self.count += 1
            self.history.append(Heard(self.count, fragment.text, scored.score, scored.source.value))
            self.display.sentence(self.count, scored.score, scored.source.value, fragment.text)
            worth_it = (scored.source.value == "model" and scored.score >= self.args.explain_above
                        and (kind == "speech" or self.args.explain_sounds))
            if self.args.explain and worth_it:
                self._explain(self.count, fragment.text, scored.score)

    def _explain(self, number: int, text: str, score: float, now: bool = False) -> None:
        """Queues a sentence for a written explanation. Unless `now`, it waits for a quiet moment."""
        item = (number, text, score, now)
        try:
            self.explain_queue.put_nowait(item)
        except queue.Full:
            try:
                skipped = self.explain_queue.get_nowait()
                self.display.line(f"  (explanations are behind; skipped #{skipped[0]})")
            except queue.Empty:
                pass
            self.explain_queue.put_nowait(item)

    def _wait_until_quiet(self) -> None:
        """Holds an explanation back until nothing has been heard or scored for a few seconds.

        Writing words is by far the slowest thing the model does on a Pi: while someone is talking, with
        whisper and the scorer busy on the same four cores, one explanation measured 1.25 tokens a second
        and took 45 s. So explanations never compete with listening; they take the quiet moments. If the
        room never goes quiet, type e to explain the recent sentences right away.
        """
        while not self.stop.is_set():
            # Quiet means the AUDIO is quiet, not that the pipeline has no news: someone speaking for twenty
            # seconds produces no event until the end, and an explanation must not start in the middle of it.
            heard_lately = (self.segmenter is not None and not self.source_done
                            and self.segmenter.quiet_seconds < self.args.explain_quiet)
            if not self.working and self.utterances.empty() and not heard_lately:
                return
            time.sleep(0.2)

    def explain_worker(self) -> None:
        while True:
            item = self.explain_queue.get()
            if item is None:
                return
            number, text, score, now = item
            if not now:
                self._wait_until_quiet()
            client = self.brain.client
            if not client.can_interpret:
                continue
            run = client.interpret(text)
            if run:
                self.display.interpretation(number, run, score, client.last_seconds or 0.0)
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

    def explain_recent(self, count: int) -> None:
        """Asks for written explanations of the last few sentences now, whether or not the room is quiet."""
        if not self.brain.client.can_interpret:
            self.display.line("  (no prompt_output_debug in the config, so there is nothing to explain with)")
            return
        recent = [item for item in self.history if item.source == "model"][-count:]
        if not recent:
            self.display.line("  (nothing has been scored by the model yet)")
            return
        self.display.line(f"\n[{self.display.stamp()}] explaining the last {len(recent)} sentences "
                          f"(each takes a few seconds; they appear as they finish)")
        for item in recent:
            self._explain(item.number, item.text, item.score, now=True)

    def commands(self) -> None:
        for line in sys.stdin:
            word = line.strip().lower()
            if word == "r":
                self.rescore()
            elif word.split()[:1] == ["e"]:
                parts = word.split()
                self.explain_recent(int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 4)
            elif word == "q":
                self.explain_queue.put(None)
                break
            elif word == "l":
                self.display.line(f"  levels: {self.segmenter.describe_levels() if self.segmenter else 'not listening yet'}")
            elif word in ("?", "h", "help"):
                self.display.line("  r = re-score the recent sentences with the current prompts   e [n] = explain the last n "
                                  "(default 4) now   l = show the room's levels   q = quit")
        self.stop.set()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", default=str(ROOT / "config" / "pom.toml"), help="the config file to read and watch")
    parser.add_argument("--wav", help="replay this 16 kHz mono recording instead of listening")
    parser.add_argument("--speed", type=float, default=1.0, help="replay speed (0 = as fast as possible)")
    parser.add_argument("--device", default="plughw:0,0", help="the arecord capture device")
    parser.add_argument("--model", default="base.en", help="the whisper model (base.en or small.en)")
    parser.add_argument("--whisper-threads", type=int, default=4)
    parser.add_argument("--no-vad", dest="vad", action="store_false",
                        help="do not use whisper's built-in voice-activity detection (it is what stops invented words)")
    parser.add_argument("--vad-threshold", type=float, default=0.5, help="how sure the VAD must be that it is speech")
    parser.add_argument("--no-sounds", dest="sounds", action="store_false",
                        help="skip the second pass that keeps song lyrics and sound captions the VAD ignores")
    parser.add_argument("--threshold", type=float, default=7.0, help="mark scores at or above this (default 7.0)")
    parser.add_argument("--no-explain", dest="explain", action="store_false", help="skip the written interpretations")
    parser.add_argument("--explain-above", type=float, default=4.0,
                        help="only explain scores at or above this (e explains anything, on request)")
    parser.add_argument("--explain-sounds", action="store_true",
                        help="also explain whisper's sound captions and lyrics (by default only speech is explained)")
    parser.add_argument("--explain-quiet", type=float, default=3.0,
                        help="seconds of quiet before a written explanation starts (they never compete with listening)")
    parser.add_argument("--history", type=int, default=12, help="sentences kept in memory for the r command")
    parser.add_argument("--score-timeout", type=float, default=30.0,
                        help="seconds to wait for a score (at least; the config's value if larger)")
    parser.add_argument("--margin-db", type=float, default=6.0,
                        help="how far above the room noise counts as speech (lower it if music or noise masks speech)")
    args = parser.parse_args()

    nltk_dir = ROOT / "data" / "nltk"
    brain = Brain(Path(args.config), WordTools(nltk_data=str(nltk_dir) if nltk_dir.exists() else None),
                  score_timeout=args.score_timeout)
    display = Display(args.threshold)
    display.line("hearing-debug: what you say is shown here and kept nowhere. Do not redirect this output to a file.")
    display.line(f"  {brain.refresh() or 'config loaded'}  [{brain.fingerprint()}]")
    display.line(f"  scorer at {brain.settings.url}; whisper {args.model}; threshold {args.threshold:g}; "
                 f"{'interpretations on' if args.explain and brain.client.can_interpret else 'interpretations off'}")
    display.line(f"  hearing: whisper {args.model}, {'Silero VAD on' if Pipeline.vad_note(args) is None else Pipeline.vad_note(args)}"
                 f"{', then lyrics and sound captions the VAD ignores' if args.vad and args.sounds else ''}")
    display.line(f"  model warm-up: {'ready' if brain.scorer.warm_up() else 'NOT answering (scores will be fallback)'}")
    if args.explain and not brain.client.can_interpret:
        display.line("  (no prompt_output_debug in the config, so there are no interpretations)")

    pipeline = Pipeline(args, brain, display)
    if pipeline.transcriber.note:
        display.line(f"  ({pipeline.transcriber.note})")
    source = WavSource(args.wav, args.speed) if args.wav else MicSource(args.device)
    threads = [threading.Thread(target=pipeline.listen, args=(source,), daemon=True),
               threading.Thread(target=pipeline.transcribe_and_score, daemon=True),
               threading.Thread(target=pipeline.explain_worker, daemon=True)]
    for thread in threads:
        thread.start()
    display.line("  nothing to do: it listens and scores by itself. Written explanations, for speech scoring "
                 f"{args.explain_above:g} or more, arrive in quiet moments (after {args.explain_quiet:g} s of quiet).\n  Optional: e + Enter explains the last few sentences now, r + Enter "
                 "re-scores them with the current prompts, l shows the levels, q quits\n")
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
