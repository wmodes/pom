"""The WhisperServerClient: asks a running whisper-server to turn audio into text.

whisper.cpp's `whisper-server` keeps the whisper model (and its Silero voice detection model) loaded, so each request
is only the transcription. This module is only the conversation with that server: audio in, text or timed segments
out, or nothing if the server could not be reached. It knows nothing about what the text means; the
WhisperTextSorter does that.

The server is started once with its voice detection on, for example (probe_hearing_run.sh does this on the Pi):

    whisper-server -m ggml-base.en.bin --vad -vm ggml-silero-v6.2.0.bin -vt 0.5 --port 8110

Voice-detection settings other than on/off per request (the threshold, the minimum silence) belong to the server and
are set when it starts.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A server that is down, slow or answering oddly gives an
    empty result and a warning in `report`, never an exception. `error` says what went wrong with the latest request
    (or is None), so a caller can tell "heard nothing" from "could not ask". `strict=True` raises
    WhisperServerClientError instead, for tests and dev tools.
  * The audio goes to the server from memory, as a WAV made in memory, so no audio file is ever written to disk.
  * Neither the audio nor the text is logged or put in a FaultRecorder message: nothing that was said in the room is
    kept. Reports hold the kind of failure only.
  * Timestamps are asked for as SRT subtitles. KNOWN BUG in the whisper.cpp 1.9.5 server: a request for
    `verbose_json` with voice detection on CRASHES the server when the audio holds no speech (found with a noise-only
    slice). `text` and `srt` are safe.
  * The transport is injected (`post`), so tests can use a fake; the default uses the standard library, so there is
    nothing to install. Only http and https addresses are accepted, so a mistyped setting cannot open a file.
  * The settings are passed in (the entry point takes them from Config); this module never reads Config.
"""
import io
import re
import urllib.error
import urllib.request
import uuid
import wave
from typing import Callable, List, NamedTuple, Optional, Tuple

from pom.fault_recorder import FaultRecorder
from pom.listening.audio_format import SAMPLE_BYTES, SAMPLE_RATE
from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_URL = "http://127.0.0.1:8110"
DEFAULT_TIMEOUT = 180.0
PING_TIMEOUT = 5.0
_SRT_ENTRY = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d\d\d)\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d\d\d)[^\n]*\n(.*?)(?:\n\s*\n|\Z)",
                        re.DOTALL)


class WhisperServerClientError(ValueError):
    """Raised for any client problem, but only in strict mode (development and tests)."""


class Segment(NamedTuple):
    """One piece of text whisper wrote, with where it lies in the audio.

    Attributes:
        start: Seconds from the start of the whole stream (the request's offset is already added).
        end: Seconds from the start of the whole stream.
        text: What whisper wrote, on one line.
    """
    start: float
    end: float
    text: str


def parse_srt(text: str) -> List[Tuple[float, float, str]]:
    """The (start, end, text) of each entry of an SRT or WebVTT reply, in seconds. [] for an empty reply."""
    out = []
    for h1, m1, s1, ms1, h2, m2, s2, ms2, words in _SRT_ENTRY.findall(text.replace("\r\n", "\n")):
        start = int(h1) * 3600 + int(m1) * 60 + int(s1) + int(ms1) / 1000
        end = int(h2) * 3600 + int(m2) * 60 + int(s2) + int(ms2) / 1000
        out.append((start, end, " ".join(words.split())))
    return out


def wav_bytes(pcm: bytes) -> bytes:
    """16 kHz mono 16-bit audio as the bytes of a WAV file, made in memory."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(SAMPLE_BYTES)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(pcm)
    return buffer.getvalue()


def multipart(fields: dict, audio: bytes) -> Tuple[bytes, str]:
    """A multipart/form-data body holding the audio file and some text fields. Returns (body, content type)."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
                  f'Content-Type: audio/wav\r\n\r\n').encode() + audio + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _http_post(url: str, fields: dict, audio: bytes, timeout: float) -> str:
    """POSTs the audio and fields to the server's /inference and returns the reply text. Raises on any problem."""
    body, content_type = multipart(fields, audio)
    request = urllib.request.Request(url + "/inference", body, {"Content-Type": content_type})
    with urllib.request.urlopen(request, timeout=timeout) as response:      # nosec: scheme checked
        return response.read().decode("utf-8", "replace")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class WhisperServerClient:
    """Asks a running whisper-server to transcribe audio.

    Attributes:
        report: Problems met (server down, bad setting). Never holds audio or text.
        error: The kind of failure of the most recent request, or None if it worked.
    """

    def __init__(self, url: str = DEFAULT_URL, timeout: float = DEFAULT_TIMEOUT,
                 post: Optional[Callable[[str, dict, bytes, float], str]] = None, strict: bool = False):
        """Args:
            url: The server's address, e.g. http://127.0.0.1:8110.
            timeout: Seconds to wait for one transcription, above 0.
            post: A function (url, fields, wav bytes, timeout) -> the reply text. Default: real HTTP.
            strict: Raise WhisperServerClientError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=WhisperServerClientError, logger=_log)
        self.error: Optional[str] = None
        self._usable = isinstance(url, str) and url.startswith(("http://", "https://"))
        self._url = url.rstrip("/") if self._usable else DEFAULT_URL
        if not self._usable:
            self.report.warn(f"the whisper address must start with http:// or https://, got {url!r}; "
                             f"whisper will not be asked")
        if _is_number(timeout) and timeout > 0:
            self._timeout = float(timeout)
        else:
            self.report.warn(f"the whisper timeout must be a positive number of seconds, got {timeout!r}; "
                             f"using {DEFAULT_TIMEOUT:g}")
            self._timeout = DEFAULT_TIMEOUT
        self._post = post if post is not None else _http_post

    @property
    def available(self) -> bool:
        """False if the address was unusable, so whisper will never be asked."""
        return self._usable

    def ping(self) -> bool:
        """True if the server answers."""
        if not self._usable:
            return False
        try:
            with urllib.request.urlopen(self._url + "/", timeout=PING_TIMEOUT):      # nosec: scheme checked
                return True
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def text(self, pcm: bytes, vad: bool = True) -> str:
        """Plain text of what whisper heard in this audio (no timestamps).

        With `vad` False whisper hears everything, noise included, which is where it invents words. Returns "" when
        there was nothing to say or the request failed; `error` tells which.
        """
        reply = self._ask(pcm, {"response_format": "text", "no_timestamps": "true", "vad": "true" if vad else "false"})
        return reply if reply is not None else ""

    def segments(self, pcm: bytes, offset_s: float = 0.0, vad: bool = True) -> List[Segment]:
        """Whisper's segments with timestamps, shifted by `offset_s`. [] if there were none or the request failed."""
        reply = self._ask(pcm, {"response_format": "srt", "vad": "true" if vad else "false"})
        if reply is None:
            return []
        return [Segment(offset_s + start, offset_s + end, text) for start, end, text in parse_srt(reply) if text]

    def _ask(self, pcm: bytes, fields: dict) -> Optional[str]:
        """Sends the audio and returns the reply, or None if it could not be had (the kind is in `error`)."""
        if not self._usable:
            self.error = "UnusableAddress"                      # warned about once, when the client was made
            return None
        if not isinstance(pcm, (bytes, bytearray, memoryview)):
            self.error = "BadAudio"
            self.report.warn(f"the audio for whisper must be bytes, got {type(pcm).__name__}")
            return None
        if len(pcm) < SAMPLE_BYTES:                             # not even one sample: nothing to transcribe, nothing failed
            self.error = None
            return None
        try:
            reply = self._post(self._url, fields, wav_bytes(bytes(pcm)), self._timeout)
        except Exception as problem:                 # a dead or slow server must never stop the oracle
            self.error = type(problem).__name__
            self.report.warn(f"whisper did not answer ({self.error})")
            return None
        if not isinstance(reply, str):
            self.error = "BadReply"
            self.report.warn("whisper's reply was not text")
            return None
        self.error = None
        return reply
