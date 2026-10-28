"""AudioSource: what the listening side needs from anywhere sound comes from.

The Listener does not care whether the sound is a microphone or a recording. It needs only an object that hands out
audio in frames and can be closed. The Printer uses a MicSource; a WavSource replays a recording, for tests and for
trying a change on a meeting we already have.

A frame is `audio_format.FRAME_BYTES` bytes (30 ms of 16 kHz mono 16-bit sound). `frames()` yields frames until the
sound ends: a recording runs out, or the microphone stops. It is meant to be iterated once. A source that could not
start (a missing file, no microphone) yields nothing and says why in its `report`; it never raises.
"""
from typing import Iterator, Protocol


class AudioSource(Protocol):
    """Anything that gives out audio frames."""

    def frames(self) -> Iterator[bytes]:
        """Yields frames of `audio_format.FRAME_BYTES` bytes, in order, until the sound ends."""

    def close(self) -> None:
        """Lets go of the microphone or the file. Safe to call more than once."""
