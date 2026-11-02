"""The Attempt: one score trying to qualify, with what came of it.

The HingeFinder makes one for each thing heard, and the TypedLineScorer makes one for each line typed; the BarKeeper's
ruling is in it, and the one that qualified (`is_hinge`) is the hinge: the sentence the oracle will be asked about.
"""
from dataclasses import dataclass
from typing import Optional

from pom.qualifying.bar_keeper import Ruling
from pom.scoring.clip_scorer import ScoredBy


@dataclass(frozen=True, slots=True)
class Attempt:
    """One thing heard, with what the Scorer and the BarKeeper made of it.

    Attributes:
        text: The sentence (or the caption of a sound), word for word.
        kind: "speech" or "lyrics" for words, "room_tone" for the caption of a sound.
        score: 0 (dull) to 9 (extremely interesting), or None if it was not scored (a caption while room tone is
            not scored, or the Scorer could not).
        scored_by: What made the score (the model, the clip filter) or left it without one.
        ruling: What the BarKeeper ruled, or None if it never saw the score.
        heard_at: Where in the audio stream the words ended, in seconds from the start of listening.
        bar: The bar a score had to clear, as it stood before this one joined the window (None until it exists).
        window_size: How many scores were in the window when this one arrived.
        counted: How many scores the BarKeeper has counted so far, this one included.
    """
    text: str
    kind: str
    score: Optional[float]
    scored_by: ScoredBy
    ruling: Optional[Ruling]
    heard_at: float
    bar: Optional[float]
    window_size: int
    counted: int

    @property
    def is_hinge(self) -> bool:
        """True if this qualified: the sentence to answer."""
        return self.ruling is Ruling.QUALIFIED

