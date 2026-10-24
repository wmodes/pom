"""A small client for whisper.cpp's whisper-server, shared by the hearing-debug tools.

Part of experiments/hearing-debug. The server keeps the whisper model (and its Silero voice detection
model) loaded, so each request is only the transcription. Audio goes to it straight from memory, in the
request, so no audio file is ever written to disk. It uses only the standard library.

The server is started once with its voice detection on, for example:

    whisper-server -m ggml-base.en.bin --vad -vm ggml-silero-v6.2.0.bin --port 8110

(probe_hearing_run.sh does this on the Pi). Voice-detection settings other than on/off (threshold, minimum
silence) are the server's own, set when it starts, and left at whisper's defaults.

KNOWN BUG in the whisper.cpp 1.9.5 server: a request for `verbose_json` with voice detection on CRASHES the
server when the audio holds no speech (found with a noise-only slice), so timestamps are asked for as SRT
subtitles instead, which is safe. `text` and `json` are safe too.
"""
import io
import re
import urllib.error
import urllib.request
import uuid
import wave
from typing import List, Optional, Tuple

SAMPLE_RATE = 16000
DEFAULT_URL = "http://127.0.0.1:8110"
Segment = Tuple[float, float, str]        # start, end (seconds in the whole recording) and text
SRT_ENTRY = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d\d\d)\s*-->\s*(\d+):(\d\d):(\d\d)[,.](\d\d\d)[^\n]*\n(.*?)(?:\n\s*\n|\Z)",
                       re.DOTALL)


def parse_srt(text: str) -> List[Tuple[float, float, str]]:
    """The (start, end, text) of each entry of an SRT or WebVTT reply, in seconds. [] for an empty reply."""
    out = []
    for h1, m1, s1, ms1, h2, m2, s2, ms2, words in SRT_ENTRY.findall(text.replace("\r\n", "\n")):
        start = int(h1) * 3600 + int(m1) * 60 + int(s1) + int(ms1) / 1000
        end = int(h2) * 3600 + int(m2) * 60 + int(s2) + int(ms2) / 1000
        out.append((start, end, " ".join(words.split())))
    return out


def wav_bytes(pcm: bytes) -> bytes:
    """16 kHz mono 16-bit audio as the bytes of a WAV file, made in memory."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
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


class WhisperServerClient:
    """Asks a running whisper-server to transcribe audio.

    Attributes:
        url: The server's address.
        error: A short description of the last failure, or None if the last request worked.
    """

    def __init__(self, url: str = DEFAULT_URL, timeout: float = 180.0):
        self.url, self.timeout = url.rstrip("/"), timeout
        self.error: Optional[str] = None

    def ping(self) -> bool:
        """True if the server answers."""
        try:
            with urllib.request.urlopen(self.url + "/", timeout=5):
                return True
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def _post(self, pcm: bytes, fields: dict) -> str:
        body, content_type = multipart(fields, wav_bytes(pcm))
        request = urllib.request.Request(self.url + "/inference", body, {"Content-Type": content_type})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read().decode("utf-8", "replace")

    def text(self, pcm: bytes, vad: bool = True) -> str:
        """Plain text of what whisper heard (no timestamps). With vad False whisper hears everything, noise included.

        Returns "" if the request failed; the reason is in `error`.
        """
        try:
            reply = self._post(pcm, {"response_format": "text", "no_timestamps": "true",
                                     "vad": "true" if vad else "false"})
            self.error = None
            return reply
        except (urllib.error.URLError, OSError, ValueError) as problem:
            self.error = f"{type(problem).__name__}"
            return ""

    def segments(self, pcm: bytes, offset_s: float = 0.0, vad: bool = True) -> List[Segment]:
        """Whisper's segments with timestamps, shifted by `offset_s`. [] if the request failed (see `error`)."""
        try:
            reply = self._post(pcm, {"response_format": "srt", "vad": "true" if vad else "false"})
            self.error = None
        except (urllib.error.URLError, OSError, ValueError) as problem:
            self.error = f"{type(problem).__name__}"
            return []
        return [(offset_s + start, offset_s + end, text) for start, end, text in parse_srt(reply) if text]
