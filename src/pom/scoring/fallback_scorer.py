"""The FallbackScorer: a crude, word-counting stand-in for the scorer model. NOT IN USE.

This is shelved work, kept in case it is wanted again. Nothing imports it; the Scorer does not call
it. To remove it for good, delete this file and `tests/scoring/test_fallback_scorer.py`; nothing else
depends on it.

WHAT IT DOES
Given a fragment's text and its tagged words (from the WordTagger), it guesses a score: a base,
plus a little for each content word, name and number, plus a little for a question mark, capped at
a ceiling. It never asks a model anything, so it works when the scorer model does not.

WHY IT WAS SHELVED
The Scorer used to fall back to this whenever the scorer model gave no score. It was taken out
because:
  * Every number below is arbitrary. They were chosen by guess, never measured against what
    people find interesting, and they were hard-coded, not in config.
  * It measures length and the presence of names and numbers, not interest. A long dull sentence
    outscores a short striking one.
  * Its scores mean something different from the scorer model's, so they cannot sit beside them.
    The ceiling of 5.0 is an absolute number, which the project avoids (scores are judged
    relative to the room's recent scores, by the ConvoInterestTracker), and its scores would feed that
    window and distort it.
  * The scorer model is local. If it is down, something larger is wrong, and answering from a
    word count would only hide that. The Scorer now returns no score for a fragment it cannot
    score, and the Printer stays quiet and keeps listening.

WHEN IT MIGHT BE NEEDED AGAIN
Only if running without the scorer model ever becomes a real requirement (for example a machine
too small to run it). Before using it again: put every number below into config under
`[scorer]`, decide how its scores are marked so the ConvoInterestTracker can leave them
out, and measure it against real sentences.

Design decisions, and why:
  * It is a plain class with no state, no clock and no model, so it is trivial to test and to
    delete.
  * It never raises. Words it is given are only read.
  * Fragment text is never logged; the debug line holds a number of words and a score.
"""
from typing import Sequence

from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_BASE = 1.0

# A base plus a little per content word, name, number and question, capped at CEILING.
PER_CONTENT_WORD = 0.35
CONTENT_WORDS_COUNTED = 10
PER_NAME = 0.8
NAMES_COUNTED = 2
PER_NUMBER = 0.4
NUMBERS_COUNTED = 2
QUESTION_BONUS = 0.4
CEILING = 5.0


class FallbackScorer:
    """Guesses a score from a fragment's words alone. Not in use; see the module docstring."""

    def __init__(self, base: float = DEFAULT_BASE):
        """Args:
            base: The score a fragment gets before anything is added to it.
        """
        self._base = base

    def score(self, text: str, words: Sequence) -> float:
        """A crude score for the text, from its tagged words (Word objects from the WordTagger)."""
        content = [word for word in words if word.is_content]
        names = sum(1 for word in words if word.is_name)
        numbers = sum(1 for word in words if word.is_number)
        value = (self._base
                 + PER_CONTENT_WORD * min(len(content), CONTENT_WORDS_COUNTED)
                 + PER_NAME * min(names, NAMES_COUNTED)
                 + PER_NUMBER * min(numbers, NUMBERS_COUNTED)
                 + (QUESTION_BONUS if "?" in text else 0.0))
        value = min(value, CEILING)
        _log.debug("guessed a score for a fragment of %d words: %.2f", len(words), value)
        return value
