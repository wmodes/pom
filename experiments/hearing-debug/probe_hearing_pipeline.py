#!/usr/bin/env python3
"""Probe: watch how the Printer interprets what it hears, live, and refine the prompt as you go.

Part of experiments/hearing-debug. A prototype of the front half of the Printer, run on the Pi (or on
the Mac against a recording):

    microphone -> cut at pauses -> level normalized -> whisper -> sentences -> Scorer -> explanation

For every sentence it prints the Scorer's score (the real `pom` Scorer, built from config/pom.toml) and,
a little later, the model's own written account of why: the content it found that earned the score,
from the debug prompt. Each score also goes to the real `pom` ConvoInterestTracker, which keeps the room's
average interest, and a sentence that stands out from that average by the configured margin is marked as one
that would trigger. There is no fixed threshold: the bar moves with the room.

THE CLOSED LOOP. The config file is checked before every utterance. Edit the prompts in
config/pom.toml (deliver it with git using probe_hearing_tune.sh watch) and the next thing you say is
scored with the new wording. To compare the same words under two prompts, type `r` and Enter in this
terminal: it re-scores the last few sentences with the current prompts and shows old -> new.

WHAT IT KEEPS. This prints what the room says to YOUR TERMINAL and keeps nothing: audio is cut into a
temporary file in memory-backed storage (/dev/shm where there is one) and deleted as soon as whisper
has read it (a slice left by a run that was stopped mid-transcription is cleared at the next start), the last few sentences sit in memory for the `r` command, and nothing is written to a log.
Do not redirect its output to a file, and do not run it as a service. The Printer's own rule is that no
transcript of the room is kept; this is a development tool used on purpose, by someone who knows.

Usage:
    probe_hearing_pipeline.py                       # listen on the Pi's microphone
    probe_hearing_pipeline.py --wav clip.wav        # replay a 16 kHz mono recording instead
    probe_hearing_pipeline.py --model small.en --no-explain

While running, type: r (re-score the recent sentences), ? (help), q (quit).

Needs, besides Python 3.11 and the `pom` package (the repo's src/ is found automatically): arecord for
the microphone, a running whisper-server (whisper.cpp's own server, with its voice detection on; its address
is --whisper-url or POM_WHISPER_URL) and a running llama-server at the config's [scorer] url.
probe_hearing_run.sh starts both on the Pi. Everything else is the standard library.
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
from rolling_cutter import RollingCutter                         # noqa: E402  (next to this file)
from whisper_server_client import WhisperServerClient          # noqa: E402  (next to this file)
from pom.scoring import ConvoInterestTracker, ScoreEffect, ScorerModelClient, Scorer, WordTagger   # noqa: E402
from pom.scoring.scorer_model_client import parse_written_score      # noqa: E402
from pom.listening.segment_fragmenter import SegmentFragmenter, FragmentLevel          # noqa: E402

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
    """Turns an utterance into text with whisper.cpp's whisper-server, using whisper's own voice detection.

    Whether something is speech is decided by whisper's built-in Silero VAD, the intended tool, not by a
    threshold of mine. With it, whisper only sees the speech, so it stops inventing words for silence and
    noise ("You", "I'm sorry") and hears quiet speech better. The server keeps the models loaded, and the
    audio goes to it from memory, so no audio file is ever written to disk.

    The VAD is a speech detector and treats singing, and most other sounds, as not speech. The Printer is
    meant to react to what it hears, so when pass 1 (VAD on) finds no speech, pass 2 runs whisper without
    the VAD and keeps only what whisper itself marks as a non-speech sound: song lyrics between music
    symbols and captions like "(dog barks)". Plain text from that pass is where whisper hallucinates, so it
    is thrown away.

    Attributes:
        client: The WhisperServerClient (its `error` says why the last request failed, or None).
    """

    def __init__(self, url: str, use_vad: bool = True, sounds: bool = True):
        self.client = WhisperServerClient(url)
        self._vad = use_vad
        self._sounds = sounds and use_vad                 # pass 2 only makes sense after a VAD pass 1

    @property
    def vad_in_use(self) -> bool:
        return self._vad

    def transcribe(self, pcm: bytes) -> "WhisperText":
        """What whisper heard in an utterance: words for the Scorer, and ambient captions kept apart."""
        raw = self.client.text(pcm, vad=self._vad)
        heard = split_whisper(raw)
        if heard.words or heard.ambient or self.client.error:
            return heard
        if self._sounds and not raw.strip():              # the VAD found no speech at all; if whisper said "silence" it is
            # Without the VAD, plain text is where whisper invents words, so only lyrics and captions are kept.
            return split_whisper(self.client.text(pcm, vad=False), plain_text=False)
        return heard


# Whisper's own way of saying "there is nothing here". They carry no information, so they are not used at all.
EMPTY_MARKS = re.compile(r"[\[(]\s*(blank[_ ]audio|silence|silent|no audio|no speech)\s*[\])]", re.IGNORECASE)
CAPTION = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")                  # (dog barks) or [Music]: a guess at a sound
LYRICS = re.compile(r"\u266a[^\u266a]+\u266a")                        # words between music symbols


@dataclass
class WhisperText:
    """What whisper said about one utterance, sorted into the kinds the Printer treats differently.

    Attributes:
        words: Speech and song lyrics, with the music symbols removed. This is what the Scorer is given.
        ambient: Whisper's captions of sounds, such as "(dog barks)" or "(dramatic music)". They describe the
            room, not something said, so they are shown but never scored; later they can go into the voice's
            prompt as context ("a dog is barking").
        kind: "speech", "lyrics" (the words came only from between music symbols), or "" if there are no words.
    """
    words: str
    ambient: list
    kind: str


def split_whisper(raw: str, plain_text: bool = True) -> WhisperText:
    """Sorts whisper's output into words, ambient captions and nothing-here markers (which are dropped).

    With plain_text False (the pass without the VAD, where plain text is more likely invented than heard) only
    lyrics count as words.
    """
    raw = EMPTY_MARKS.sub(" ", raw)
    ambient = list(dict.fromkeys(" ".join(caption.split()) for caption in CAPTION.findall(raw)))   # once each, in order
    uncaptioned = CAPTION.sub(" ", raw)
    plain = " ".join(LYRICS.sub(" ", uncaptioned).replace("\u266a", " ").split())
    lyrics = " ".join(" ".join(piece.replace("\u266a", " ").split()) for piece in LYRICS.findall(uncaptioned))
    if not plain_text:
        return WhisperText(lyrics, ambient, "lyrics" if lyrics else "")
    words = " ".join(uncaptioned.replace("\u266a", " ").split())
    return WhisperText(words, ambient, ("speech" if plain else "lyrics") if words else "")


# --- The Printer's judgment, from the real pom modules ----------------------------------------

def short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:6]


class Brain:
    """The real Scorer and scorer model client, rebuilt whenever config/pom.toml changes."""

    def __init__(self, config_path: Path, word_tagger: WordTagger, score_timeout: float = 0.0):
        self._path, self._word_tagger = config_path, word_tagger
        self._score_timeout = score_timeout        # at least this long to wait for a score (0 = as configured)
        self._mtime = None
        self.settings = None
        self.trigger = None
        self.client = None
        self.scorer = None
        self.tracker = None
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
        client = ScorerModelClient(settings.url, settings.prompt, max(settings.timeout, self._score_timeout),
                                explain_prompt=settings.prompt_debug)
        self.scorer = Scorer(client, self._word_tagger, fragment_filter_score=settings.fragment_filter_score, min_content_words=settings.min_content_words,
                             failure_limit=settings.failure_limit, retry_seconds=settings.retry_seconds)
        self.client, self.settings, self.trigger = client, settings, config.trigger
        # A new config can mean new prompts, and a score under new wording is not the same measure, so the
        # room's average interest starts over whenever the config is reloaded.
        self.tracker = ConvoInterestTracker(self.trigger.floor_ignore_fraction, self.trigger.trigger_fraction,
                                            self.trigger.interest_half_life, self.trigger.min_scores)
        problems = config.report.lines() + client.report.lines() + self.tracker.report.lines()
        note = "; ".join(problems)
        if first:
            return f"config loaded{(' (problems: ' + note + ')') if note else ''}"
        changed = [name for name in ("prompt_scoring", "prompt_output", "prompt_output_debug", "url", "fragment_filter_score",
                                     "min_content_words")
                   if getattr(before, name) != getattr(settings, name)]
        return (f"config reloaded: {', '.join(changed) if changed else 'no scorer setting changed'}; "
                f"the room's average interest starts over{(' (problems: ' + note + ')') if note else ''}")

    def fingerprint(self) -> str:
        s = self.settings
        return (f"scoring {short_hash(s.prompt_scoring)}  output {short_hash(s.prompt_output)}  "
                f"debug {short_hash(s.prompt_output_debug)}")


# --- The display ------------------------------------------------------------------------------

class Display:
    """Prints to the terminal, one thread at a time."""

    def __init__(self):
        self._lock = threading.Lock()

    def line(self, text: str = "") -> None:
        with self._lock:
            print(text, flush=True)

    def stamp(self) -> str:
        return time.strftime("%H:%M:%S")

    @staticmethod
    def recording_stamp(seconds: float) -> str:
        """A position in a replayed recording, as +hh:mm:ss. The wall clock means nothing when a recording is
        replayed faster than real time, so a replay stamps what it hears with where in the recording it was."""
        whole = int(seconds)
        return f"+{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"

    def sentence(self, number: int, score: Optional[float], source: str, text: str,
                 previous: Optional[float] = None, standing: str = "") -> None:
        was = f"   (was {previous:.2f})" if previous is not None else ""
        shown = f"{score:5.2f}" if score is not None else "  -  "          # None: the scorer model gave no score
        self.line(f"  #{number:<3} {shown}  {source:<15} {text}{was}{standing}")

    def pending(self, number: int, text: str) -> None:
        """A sentence that has been heard and is waiting for its one debug run."""
        self.line(f"  #{number:<3}  ....  waiting  {text}")

    def result(self, number: int, score: Optional[float], run, seconds: float, text: str,
               previous: Optional[float] = None, standing: str = "") -> None:
        """One debug run: the score and the reasons, which came out of the same output."""
        shown = f"{score:5.2f}" if score is not None else "  ?  "
        was = f"   (was {previous:.2f})" if previous is not None else ""
        self.line(f"  #{number:<3} {shown}  debug    {text}{was}{standing}")
        wrote = parse_written_score(run.text)
        foot = f"{seconds:.1f} s; score from this run's first-token probabilities"
        if wrote is not None:
            foot += f", it wrote {wrote:.2f}"
        wrapped = textwrap.fill(run.text, width=96, initial_indent="        says: ", subsequent_indent=" " * 14)
        self.line(f"{wrapped}\n        ({foot})")


# --- The pipeline -----------------------------------------------------------------------------

@dataclass
class Heard:
    number: int
    text: str
    score: Optional[float]      # None until the sentence's debug run has finished
    source: str


class Pipeline:
    def __init__(self, args, brain: Brain, display: Display):
        self.args, self.brain, self.display = args, brain, display
        self.transcriber = Transcriber(args.whisper_url, use_vad=args.vad, sounds=args.sounds)
        self.fragmenter = SegmentFragmenter(max_words=40)
        self.utterances = queue.Queue(maxsize=6)
        self.run_queue = queue.Queue(maxsize=40)                     # sentences waiting for their debug run (cheap to hold)
        self.history = collections.deque(maxlen=args.history)       # in memory only; for the `r` command
        self.count = 0
        self.last_text = ""
        self.ambient = collections.deque(maxlen=6)                   # recent captions of the room, for the voice's prompt later
        self.last_ambient = []                                       # to show a caption once, not every time it repeats
        self.segmenter = None                                        # the energy cutter; set once listening starts (--energy-cutter only)
        # By default whisper's own segment boundaries decide where the audio is cut (RollingCutter). The energy
        # cutter, which cuts by loudness, is still here behind --energy-cutter for comparison.
        self.cutter = None if args.energy_cutter else RollingCutter(
            self.transcriber.client, step=args.step, hold=args.hold, rewind=args.rewind, vad=args.vad)
        self.source_ended = threading.Event()
        self.quiet_steps = 0                                         # whisper runs in a row that found no speech
        self.interactive = sys.stdin.isatty()                        # someone can type r, e, l, q
        self.stop = threading.Event()
        self.tracker_lock = threading.Lock()                         # two threads hand scores to the tracker
        self.whisper_down = False                                    # so a dead whisper-server is reported once, not every time

    def standing(self, score: Optional[float]) -> str:
        """Hands one score to the tracker and describes where it stands, for the end of its line.

        The bar it had to clear is read before the score goes in, because the tracker judges a score against
        the average as it stood before that score was added.
        """
        tracker, min_scores = self.brain.tracker, self.brain.trigger.min_scores
        with self.tracker_lock:
            before, bar = tracker.avg_convo_interest, tracker.trigger_level
            effect = tracker.record(score)
            after, counted = tracker.avg_convo_interest, tracker.counted
        if effect is ScoreEffect.IGNORED:
            why = "no score" if score is None else "below the floor, ignored"
            return f"   [{why}; interest {before:.2f}, unchanged]" if before is not None else f"   [{why}; no average yet]"
        interest = f"interest {after:.2f}" if before is None else f"interest {before:.2f} -> {after:.2f}"
        detail = interest + (f", bar {bar:.2f}" if bar is not None else "")
        if counted <= min_scores:                  # this score came before the trigger was allowed to fire
            return f"   [warming up {counted}/{min_scores}; {detail}]"
        return f"{'   <-- would trigger' if effect is ScoreEffect.TRIGGERED else ''}   [{detail}]"

    @staticmethod
    def vad_note(args) -> Optional[str]:
        """A sentence if the VAD is switched off, else None."""
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
            self.utterances.put(None)

    def listen_stream(self, source) -> None:
        """Feeds the microphone (or a recording) into the RollingCutter; whisper decides where to cut."""
        self.display.line("  Listening.")
        try:
            for frame in source.frames():
                if self.stop.is_set():
                    break
                self.cutter.feed(frame)
        finally:
            source.close()
            self.source_ended.set()

    def stream_worker(self) -> None:
        """Every few seconds of new audio, asks whisper for the segments it has finished and handles them."""
        while not self.stop.is_set():
            if self.cutter.due():
                final = False
            elif self.source_ended.is_set():
                final = True
            else:
                time.sleep(0.2)
                continue
            try:
                self._cut_and_hear(final)
            except Exception as error:               # a surprise must not end the session
                self.display.line(f"  (error handling the audio: {type(error).__name__})")
            if final:
                break
        if not self.interactive:
            self.run_queue.put(None)                     # nobody can type r later: finish up
        if self.args.wav:
            self.display.line("\n  (the recording is done; type r to re-score its sentences with the "
                              "current prompts, q to quit)")

    def _cut_and_hear(self, final: bool) -> None:
        changed = self.brain.refresh()
        if changed:
            self.display.line(f"\n[{self.display.stamp()}] {changed}  [{self.brain.fingerprint()}]")
        segments = self.cutter.cut(final=final)
        took = self.cutter.took
        self._note_whisper_failure()
        for _, end, text in segments:
            self._hear(split_whisper(text), f"{len(text.split())} words", "", took, at=end)
        if segments:
            self.quiet_steps = 0
        elif self.args.sounds and self.args.vad and not self.whisper_down:
            # Nothing the voice detection calls speech. Every few quiet rounds, listen once without it, for song
            # lyrics and sound captions (plain text from this pass is where whisper invents words, so it is dropped).
            self.quiet_steps += 1
            if self.quiet_steps % 3 == 0:
                started = time.perf_counter()
                heard = split_whisper(self.transcriber.client.text(self.cutter.recent(10.0), vad=False), plain_text=False)
                if heard.words or heard.ambient:
                    self._hear(heard, "a sound", "", time.perf_counter() - started)

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
                    self.run_queue.put(None)                       # nobody can type e later: finish up
                if self.args.wav:
                    self.display.line("\n  (the recording is done; type r to re-score its sentences with the "
                                      "current prompts, q to quit)")
                return
            try:
                self._handle(utterance)
            except Exception as error:               # a surprise must not end the session
                self.display.line(f"  (error handling an utterance: {type(error).__name__})")

    def _handle(self, utterance: Utterance) -> None:
        """Transcribes one utterance, then scores each sentence of it and queues the explanations."""
        changed = self.brain.refresh()
        if changed:
            self.display.line(f"\n[{self.display.stamp()}] {changed}  [{self.brain.fingerprint()}]")
        started = time.perf_counter()
        heard = self.transcriber.transcribe(normalize_pcm(utterance.pcm))
        took = time.perf_counter() - started
        self._note_whisper_failure()
        self._hear(heard, f"{utterance.seconds:.1f} s", f", peak {utterance.peak_db:.0f} dB", took)

    def _note_whisper_failure(self) -> None:
        """Says once that the whisper-server is not answering, not every time."""
        failed = self.transcriber.client.error
        if failed and not self.whisper_down:
            self.display.line(f"\n[{self.display.stamp()}] the whisper-server did not answer ({failed}); this audio was not heard")
        self.whisper_down = bool(failed)

    def _hear(self, heard: "WhisperText", size: str, extra: str, took: float, at: Optional[float] = None) -> None:
        """Shows what whisper heard, then scores each sentence of it and queues the explanations.

        `at` is where in the audio stream the utterance ended, in seconds. A replayed recording (`--wav`) shows it
        in place of the wall-clock time; live, the wall clock stays.
        """
        if heard.ambient and heard.ambient != self.last_ambient:
            self.last_ambient = heard.ambient
            self.ambient.extend(heard.ambient)
            self.display.line(f"\n[{self.display.stamp()}] ambient, not scored (whisper's guess at the room): "
                              f"{', '.join(heard.ambient)}")
        text, kind = heard.words, heard.kind
        if not text or text == self.last_text:
            return
        self.last_text = text
        label = "lyrics, not speech" if kind == "lyrics" else "speech"
        stamp = self.display.recording_stamp(at) if self.args.wav and at is not None else self.display.stamp()
        self.display.line(f"\n[{stamp}] heard {size} ({label}{extra}, whisper {took:.1f} s): \"{text}\"")
        for fragment in self.fragmenter.split(text):
            if fragment.level is not FragmentLevel.SENTENCE:
                continue
            self.count += 1
            number = self.count
            if self.debug_mode() and not self.brain.scorer.is_fragment_filtered(fragment.text):
                # ONE request per sentence: the score and the reasons both come out of it.
                self.history.append(Heard(number, fragment.text, None, "waiting"))
                self.display.pending(number, fragment.text)
                self._queue_run(number, fragment.text)
            else:
                # Fast mode, or a sentence the filter keeps from the model: the Printer's one-token score, straight away.
                scored = self.brain.scorer.score(fragment)
                self.history.append(Heard(number, fragment.text, scored.score, scored.scored_by.value))
                self.display.sentence(number, scored.score, scored.scored_by.value, fragment.text,
                                      standing=self.standing(scored.score))

    def debug_mode(self) -> bool:
        """True if each sentence gets one debug run (score and reasons together) instead of the one-token score."""
        return self.args.explain and self.brain.client.can_explain

    def _queue_run(self, number: int, text: str, previous: Optional[float] = None, feed: bool = True) -> None:
        """Queues a sentence for its debug run; the model takes them one at a time, in order.

        `feed` is False for a re-run (the r command), so the same sentence is not counted twice.
        """
        item = (number, text, previous, feed)
        try:
            self.run_queue.put_nowait(item)
        except queue.Full:
            try:
                skipped = self.run_queue.get_nowait()
                self.display.line(f"  (the model is behind; skipped #{skipped[0]}; --no-explain scores every sentence at once)")
            except queue.Empty:
                pass
            self.run_queue.put_nowait(item)

    def debug_worker(self) -> None:
        while True:
            item = self.run_queue.get()
            if item is None:
                return
            number, text, previous, feed = item
            client = self.brain.client
            if not client.can_explain:
                continue
            run = client.explain(text)
            if run is None:
                self.display.line(f"  #{number:<3} (no result: {'; '.join(client.report.lines()[-1:]) or 'no reply'})")
                continue
            score = run.score if run.score is not None else parse_written_score(run.text)
            for entry in self.history:
                if entry.number == number:
                    entry.score, entry.source = score, "debug"
            self.display.result(number, score, run, client.last_seconds or 0.0, text, previous,
                                standing=self.standing(score) if feed else "")

    # commands typed at the terminal
    def rescore(self) -> None:
        """Runs the recent sentences again with the current prompts, showing the old score beside the new one."""
        changed = self.brain.refresh()
        self.display.line(f"\n[{self.display.stamp()}] re-scoring the last {len(self.history)} sentences "
                          f"with [{self.brain.fingerprint()}]" + (f" ({changed})" if changed else ""))
        for item in list(self.history):
            if self.debug_mode() and not self.brain.scorer.is_fragment_filtered(item.text):
                self._queue_run(item.number, item.text, previous=item.score, feed=False)
            else:
                scored = self.brain.scorer.score_text(item.text)
                self.display.sentence(item.number, scored.score, scored.scored_by.value, item.text, previous=item.score)
                item.score, item.source = scored.score, scored.scored_by.value

    def commands(self) -> None:
        for line in sys.stdin:
            word = line.strip().lower()
            if word == "r":
                self.rescore()
            elif word == "q":
                self.run_queue.put(None)
                break
            elif word == "l":
                self.display.line(f"  levels: {self.segmenter.describe_levels() if self.segmenter else 'only measured with --energy-cutter'}")
            elif word in ("?", "h", "help"):
                self.display.line("  r = run the recent sentences again with the current prompts   "
                                  "l = show the room's levels   q = quit")
        self.stop.set()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", default=str(ROOT / "config" / "pom.toml"), help="the config file to read and watch")
    parser.add_argument("--wav", help="replay this 16 kHz mono recording instead of listening")
    parser.add_argument("--speed", type=float, default=1.0, help="replay speed (0 = as fast as possible)")
    parser.add_argument("--device", default="plughw:0,0", help="the arecord capture device")
    parser.add_argument("--whisper-url", default=os.environ.get("POM_WHISPER_URL", "http://127.0.0.1:8110"),
                        help="the whisper-server's address (default: POM_WHISPER_URL, else http://127.0.0.1:8110)")
    parser.add_argument("--no-vad", dest="vad", action="store_false",
                        help="do not use whisper's built-in voice-activity detection (it is what stops invented words)")
    parser.add_argument("--no-sounds", dest="sounds", action="store_false",
                        help="skip the second pass that keeps song lyrics and sound captions the VAD ignores")
    parser.add_argument("--no-explain", dest="explain", action="store_false",
                        help="fast mode: score each sentence at once with the Printer's one-token scorer, no reasons")
    parser.add_argument("--history", type=int, default=12, help="sentences kept in memory for the r command")
    parser.add_argument("--score-timeout", type=float, default=30.0,
                        help="seconds to wait for a score (at least; the config's value if larger)")
    parser.add_argument("--energy-cutter", action="store_true",
                        help="cut the audio by loudness (the old way) instead of by whisper's own segments")
    parser.add_argument("--step", type=float, default=5.0,
                        help="seconds of new audio between whisper runs (default 5; not used with --energy-cutter)")
    parser.add_argument("--hold", type=float, default=2.0,
                        help="a segment is kept once it ends this many seconds before the end of the audio (default 2)")
    parser.add_argument("--rewind", type=float, default=0.0,
                        help="each round starts this many seconds before the last kept segment's end (default 0, off; it re-hears the last word)")
    parser.add_argument("--margin-db", type=float, default=6.0,
                        help="with --energy-cutter: how far above the room noise counts as speech")
    args = parser.parse_args()

    nltk_dir = ROOT / "data" / "nltk"
    brain = Brain(Path(args.config), WordTagger(nltk_data=str(nltk_dir) if nltk_dir.exists() else None),
                  score_timeout=args.score_timeout)
    display = Display()
    display.line("hearing-debug: what you say is shown here and kept nowhere. Do not redirect this output to a file.")
    display.line(f"  {brain.refresh() or 'config loaded'}  [{brain.fingerprint()}]")
    display.line(f"  scorer at {brain.settings.url}; whisper-server at {args.whisper_url}; "
                 f"{'explanations on' if args.explain and brain.client.can_explain else 'explanations off'}")
    trigger = brain.trigger
    display.line(f"  trigger: ignore scores below {brain.tracker.avg_convo_floor:.2f}; mark a score {trigger.trigger_fraction:.0%} "
                 f"above the room's average interest (which covers about the last {trigger.interest_half_life:g} scores); "
                 f"nothing is marked until {trigger.min_scores} scores have counted")
    display.line(f"  hearing: whisper, {'Silero VAD on' if Pipeline.vad_note(args) is None else Pipeline.vad_note(args)}"
                 f"{', then lyrics and sound captions the VAD ignores' if args.vad and args.sounds else ''}; "
                 f"sound captions are shown, not scored")
    display.line("  cutting the audio by loudness (--energy-cutter)" if args.energy_cutter else
                 f"  cutting the audio by whisper's own segments: a whisper run every {args.step:g} s on a rolling buffer")
    display.line(f"  model warm-up: {'ready' if brain.scorer.warm_up() else 'NOT answering (fragments will go unscored)'}")
    if args.explain and not brain.client.can_explain:
        display.line("  (no prompt_output_debug in the config, so there are no explanations)")

    pipeline = Pipeline(args, brain, display)
    if not pipeline.transcriber.client.ping():
        sys.exit(f"the whisper-server is not answering at {args.whisper_url}. probe_hearing_run.sh starts it on the Pi; "
                 f"on its own: whisper-server -m ggml-base.en.bin --vad -vm ggml-silero-v6.2.0.bin --port 8110")
    source = WavSource(args.wav, args.speed) if args.wav else MicSource(args.device)
    if args.energy_cutter:
        threads = [threading.Thread(target=pipeline.listen, args=(source,), daemon=True),
                   threading.Thread(target=pipeline.transcribe_and_score, daemon=True),
                   threading.Thread(target=pipeline.debug_worker, daemon=True)]
    else:
        threads = [threading.Thread(target=pipeline.listen_stream, args=(source,), daemon=True),
                   threading.Thread(target=pipeline.stream_worker, daemon=True),
                   threading.Thread(target=pipeline.debug_worker, daemon=True)]
    for thread in threads:
        thread.start()
    if args.explain and brain.client.can_explain:
        display.line("  nothing to do: it listens by itself. Each sentence makes ONE trip through your debug prompt as soon as "
                     "whisper has produced it,\n  and the score and the reasons come out of that same output. --no-explain "
                     "scores every sentence at once instead, with no reasons.")
    else:
        display.line("  nothing to do: it listens and scores every sentence at once with the one-token scorer.")
    display.line("  Optional: r + Enter runs the recent sentences again with the current prompts, l shows the levels, q quits\n")
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
