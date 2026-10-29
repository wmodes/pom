"""The MacMicSource: the Mac's microphone, through `ffmpeg`, for trying the listening side on a laptop.

The Printer's microphone is read on the Pi with `arecord`, which a Mac does not have. This is the same MicSource (the
same reader thread, bounded buffer, drop-the-oldest rule and never-stop faults) with one difference: the program that
captures the sound is `ffmpeg` reading macOS's AVFoundation, converted to the format whisper takes. It exists so the
whole pipeline can be tried at a desk; it does not test the Pi's microphone path.

Design decisions, and why:
  * It is a subclass of MicSource that changes only the capture command and the wording of its faults, so there is one
    copy of everything that matters (the buffering, the dropping, the faults), not two that could drift.
  * The default device is the system's default input (`default`), so it follows whatever the Mac is set to listen to in
    System Settings > Sound. A name from `ffmpeg -f avfoundation -list_devices true -i ""` (for example
    `MacBook Pro Microphone`) picks a particular one. On the Mac that is often not the built-in microphone: a webcam or
    headset may be the default input.
  * `-nostdin` keeps ffmpeg from waiting for a keypress, and `-loglevel error` keeps its banner out of the way
    (its messages are discarded, since nothing that was heard may be kept).
  * macOS asks the first time a program records from the microphone; the terminal app that runs this needs the
    permission under System Settings > Privacy & Security > Microphone.
"""
from pom.listening.audio_format import SAMPLE_RATE
from pom.listening.mic_source import DEFAULT_BUFFER_SECONDS, MicSource, MicSourceError
from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_MAC_DEVICE = "default"


class MacMicSource(MicSource):
    """Frames from the Mac's microphone.

    Attributes:
        report: Problems met (no ffmpeg, the microphone stopped, frames dropped). CRITICAL means no sound will come.
        dropped_frames: How many frames were thrown away because the listener fell behind.
    """

    _TOOL = "ffmpeg"
    _FALLBACK_DEVICE = DEFAULT_MAC_DEVICE
    _MISSING_HINT = "install it with `brew install ffmpeg`"

    def __init__(self, device: str = DEFAULT_MAC_DEVICE, ffmpeg: str = "ffmpeg",
                 buffer_seconds: float = DEFAULT_BUFFER_SECONDS, strict: bool = False):
        """Args:
            device: The AVFoundation audio input: `default` for the system's default input, or a device name.
            ffmpeg: The program to run (a path or a name on the PATH). Tests give a stand-in.
            buffer_seconds: How much sound to hold before the oldest is dropped, above 0.
            strict: Raise MicSourceError on any problem instead of recording it.
        """
        super().__init__(device, ffmpeg, buffer_seconds, strict)

    def _command(self, program: str, device: str) -> list[str]:
        """ffmpeg reading the AVFoundation input and writing raw 16 kHz mono 16-bit samples to its standard output."""
        return [program, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "avfoundation", "-i", f":{device}",
                "-ar", str(SAMPLE_RATE), "-ac", "1", "-f", "s16le", "pipe:1"]


__all__ = ["MacMicSource", "MicSourceError"]
