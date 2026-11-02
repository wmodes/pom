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
    """One thing heard, with what the Scorer and the trigger made of it.

    Attributes:
        text: The sentence (or the caption of a sound), word for word.
        kind: "speech" or "lyrics" for words, "ambient" for the caption of a sound.
        score: 0 (dull) to 9 (extremely interesting), or None if it was not scored (a caption while ambient sounds are
            not considered, or the Scorer could not).
        scored_by: What made the score (the model, the clip filter) or left it without one.
        effect: What the trigger did with the score, or None if the trigger never saw it.
        heard_at: Where in the audio stream the words ended, in seconds from the start of listening.
        trigger_level: The bar a score had to clear, as it stood before this one joined the window (None until it exists).
        window_size: How many scores were in the window when this one arrived.
        counted: How many scores the trigger has counted so far, this one included.
    """
    text: str
    kind: str
    score: Optional[float]
    scored_by: ScoredBy
    effect: Optional[Ruling]
    heard_at: float
    trigger_level: Optional[float]
    window_size: int
    counted: int

    @property
    def is_hinge(self) -> bool:
        """True if the trigger fired on this: the sentence to answer."""
        return self.effect is Ruling.QUALIFIED

