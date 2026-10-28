"""The WhisperTextSorter: sorts what whisper wrote about a stretch of sound into the kinds the Printer treats differently.

Whisper does not only write down words. It also writes captions of sounds, like "(dog barks)" or "[Music]", marks
for "there is nothing here", like "[BLANK_AUDIO]", and, for song, words between music symbols. The Printer treats
these three kinds differently, so this module sorts them before anything is scored:

  * WORDS: speech, and sung lyrics with the music symbols taken out. These are what the Scorer is given. The Printer
    reacts to what it hears, not only to conversation, so lyrics are words and compete on score like any sentence.
  * AMBIENT captions: whisper's guess at a sound in the room. They describe the room and are not something said, so
    they are never scored (a hallucinated "(gunshot)" once scored 8.0). They are kept, once each, to give the oracle
    model as context later.
  * NOTHING-HERE marks: "[BLANK_AUDIO]", "(silence)" and the like. They carry no information and are dropped.

`kind` says where the words came from: "speech", "lyrics" (all of them from between music symbols), or "" when there
are no words.

`plain_text=False` is for text from a pass without the voice detector. There, plain text is more likely invented than
heard (it is where whisper makes up words for noise), so only lyrics count as words; the captions are still kept.

Design decisions, and why:
  * Pure text in, text out. No clock, no audio, no model, so it runs anywhere and tests with nothing but strings.
  * Nothing here may stop the oracle (see pom/__init__.py). Anything that is not a string is treated as an empty
    reply. There are no settings, so there is nothing to record in a FaultRecorder.
  * The result is immutable, so a caller cannot change what another caller was given.
  * What was said is never logged here.
"""
import re
from dataclasses import dataclass
from typing import Tuple

KIND_SPEECH = "speech"
KIND_LYRICS = "lyrics"
KIND_NONE = ""

MUSIC_SYMBOL = "\u266a"
# Whisper's own way of saying "there is nothing here".
_EMPTY_MARKS = re.compile(r"[\[(]\s*(blank[_ ]audio|silence|silent|no audio|no speech)\s*[\])]", re.IGNORECASE)
_CAPTION = re.compile(r"\([^()]*\)|\[[^\[\]]*\]")                  # (dog barks) or [Music]: a guess at a sound
_LYRICS = re.compile(MUSIC_SYMBOL + "[^" + MUSIC_SYMBOL + "]+" + MUSIC_SYMBOL)       # words between music symbols


@dataclass(frozen=True)
class WhisperText:
    """What whisper said about one stretch of sound, sorted.

    Attributes:
        words: Speech and song lyrics, with the music symbols removed. This is what the Scorer is given.
        ambient: Whisper's captions of sounds, once each in the order they came, brackets included.
        kind: "speech", "lyrics" (the words came only from between music symbols), or "" if there are no words.
    """
    words: str
    ambient: Tuple[str, ...]
    kind: str


class WhisperTextSorter:
    """Sorts whisper's text into words, ambient captions and nothing-here marks. Has no state."""

    def sort(self, raw: str, plain_text: bool = True) -> WhisperText:
        """Sorts one reply from whisper.

        Args:
            raw: Whisper's text for a stretch of sound. Anything that is not a string counts as an empty reply.
            plain_text: False when the text came from a pass without the voice detector; then only lyrics count
                as words.

        Returns:
            The WhisperText. It is never None and this never raises.
        """
        if not isinstance(raw, str):
            return WhisperText("", (), KIND_NONE)
        raw = _EMPTY_MARKS.sub(" ", raw)
        ambient = tuple(dict.fromkeys(" ".join(caption.split()) for caption in _CAPTION.findall(raw)))
        uncaptioned = _CAPTION.sub(" ", raw)
        if not plain_text:
            pieces = (" ".join(piece.replace(MUSIC_SYMBOL, " ").split()) for piece in _LYRICS.findall(uncaptioned))
            lyrics = " ".join(piece for piece in pieces if piece)          # a pair of symbols around only space is nothing
            return WhisperText(lyrics, ambient, KIND_LYRICS if lyrics else KIND_NONE)
        words = " ".join(uncaptioned.replace(MUSIC_SYMBOL, " ").split())
        outside_lyrics = " ".join(_LYRICS.sub(" ", uncaptioned).replace(MUSIC_SYMBOL, " ").split())
        if not words:
            return WhisperText("", ambient, KIND_NONE)
        return WhisperText(words, ambient, KIND_SPEECH if outside_lyrics else KIND_LYRICS)
