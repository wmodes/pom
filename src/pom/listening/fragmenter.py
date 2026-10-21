"""The Fragmenter: cuts a transcript into the pieces a hinge can be chosen from.

Whisper turns speech into punctuated text. The oracle does not answer the whole conversation; it
picks a word, a phrase or a sentence from what was just said and lets it in sideways. This module
cuts the text into those candidates, mechanically, using only the punctuation Whisper assigned:

    SENTENCE   ends at  .  ?  !
    CLAUSE     a sentence cut at  ;  :  dashes  and ellipses
    PHRASE     a clause cut at commas

All three levels are offered together, so "Honestly, my dog ate the couch yesterday." yields the
whole sentence AND "Honestly" AND "my dog ate the couch yesterday". Which one becomes the hinge is
for the scorer to decide, not for this module. A piece that would just repeat a higher level (a
sentence with no commas has no separate phrases) is not repeated. A single word is a valid
fragment.

Order is depth first and in speech order: a sentence, then its first clause, that clause's phrases,
its next clause, and so on, then the next sentence. Each fragment carries the `at` time it was
given (the segment's timestamp, used later to fade old fragments) and the index of its sentence.

Design decisions, and why:
  * Pure text in, text out. No clock, no audio, no model, so it can be tested with nothing but
    strings and run on anything.
  * Splitting is deliberately cheap and forgiving, because every level is offered: a wrong split
    at one level (an abbreviation taken for a sentence end) still leaves the other levels.
    Abbreviations that never end a sentence (Mr., Dr., e.g.) and initials are protected;
    decimals, times and thousands separators are safe because a break needs whitespace after it.
  * Nothing here may stop the oracle (see pom/__init__.py). Text that is not a string, or has no
    letters or digits, gives an empty list; a bad `max_words` falls back to the default with a
    warning. `strict=True` raises FragmenterError instead, for tests and dev tools.
  * A fragment longer than `max_words` (Whisper missed the punctuation) is cut into consecutive
    windows of that many words, so a hinge is always a bounded size.
"""
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from pom.logs import get_logger
from pom.report import Report

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_MAX_WORDS = 40

# A sentence ends at . ! ? (with optional closing quotes or brackets) followed by whitespace or the end.
_SENTENCE_END = re.compile(r"([.!?]+)([\"')\]”’]*)(?:\s+|$)")
# Abbreviations that do not end a sentence. "No." is left out on purpose: it is a common answer.
_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "st", "mt", "vs", "jr", "sr", "e.g", "i.e", "approx"}
# Clause breaks: ; or : then whitespace, a dash (spaced hyphens or any em/en dash), or an ellipsis.
_CLAUSE_BREAK = re.compile(r"\s*[;:]\s+|\s+-{1,2}\s+|\s*[—–]+\s*|\s*(?:\.{3,}|…)\s*")
# Phrase breaks: a comma followed by whitespace (so 1,000 stays whole).
_PHRASE_BREAK = re.compile(r",\s+")
_EDGES = " \t\r\n,;:-—–"
_QUOTES = "\"“”"


class FragmenterError(ValueError):
    """Raised for any fragmenter problem, but only in strict mode (development and tests)."""


class Kind(Enum):
    """How a fragment was cut."""
    SENTENCE = "sentence"
    CLAUSE = "clause"
    PHRASE = "phrase"


@dataclass(frozen=True, slots=True)
class Fragment:
    """One candidate hinge.

    Attributes:
        text: The words, as spoken (internal punctuation kept; surrounding separators trimmed).
        kind: SENTENCE, CLAUSE or PHRASE.
        at: The timestamp given for the text this came from (seconds; 0.0 if none was given).
        sentence: Index of the sentence it belongs to within the input text, from 0.
    """
    text: str
    kind: Kind
    at: float = 0.0
    sentence: int = 0

    @property
    def words(self) -> int:
        """Number of words."""
        return len(self.text.split())


class Fragmenter:
    """Cuts transcript text into sentence, clause and phrase fragments.

    Attributes:
        report: Problems recovered from (a bad setting, input that was not text).
    """

    def __init__(self, max_words: int = DEFAULT_MAX_WORDS, strict: bool = False):
        """Args:
            max_words: The most words a fragment may have; longer ones are cut into windows of
                this size. A whole number of at least 1.
            strict: Raise FragmenterError on any problem instead of recording it.
        """
        self.report = Report(strict=strict, error=FragmenterError, logger=_log)
        if isinstance(max_words, int) and not isinstance(max_words, bool) and max_words >= 1:
            self._max_words = max_words
        else:
            self.report.warn(f"max_words must be a whole number of at least 1, got {max_words!r}; "
                             f"using {DEFAULT_MAX_WORDS}")
            self._max_words = DEFAULT_MAX_WORDS

    @property
    def max_words(self) -> int:
        """The word limit in use after validation."""
        return self._max_words

    def split(self, text, at: float = 0.0) -> list:
        """Cuts text into fragments at every level.

        Args:
            text: Punctuated transcript text (one Whisper segment, or several).
            at: The timestamp to stamp on every fragment, in seconds.

        Returns:
            A list of Fragments, depth first and in speech order; empty if there is no text.
        """
        if not isinstance(text, str):
            if text is not None:
                self.report.warn(f"fragmenter expects text, got {type(text).__name__}; ignoring it")
            return []
        fragments: list = []
        sentences = self._sentences(" ".join(text.split()))
        for index, sentence in enumerate(sentences):
            fragments.extend(self._levels(sentence, index, at))
        _log.debug("split %d character(s) into %d sentence(s) and %d fragment(s)",
                   len(text), len(sentences), len(fragments))
        return fragments

    # --- Sentences ------------------------------------------------------------------

    def _sentences(self, text: str) -> list:
        """Splits at sentence ends, skipping abbreviations, initials and ellipses."""
        pieces, start = [], 0
        for match in _SENTENCE_END.finditer(text):
            punctuation = match.group(1)
            at_end = match.end() >= len(text)
            if set(punctuation) == {"."} and len(punctuation) >= 2 and not at_end:
                continue                                   # an ellipsis is a pause, not an end
            if punctuation == "." and self._is_abbreviation(text[:match.start()], text[match.end():]):
                continue
            pieces.append(text[start:match.end()])
            start = match.end()
        pieces.append(text[start:])
        return [piece.strip() for piece in pieces if piece.strip()]

    @staticmethod
    def _is_abbreviation(before: str, after: str) -> bool:
        """True if the word before a period is an abbreviation or an initial, not a sentence end."""
        found = re.search(r"([\w.]+)$", before)
        if not found:
            return False
        word = found.group(1).lower().strip(".")
        if word in _ABBREVIATIONS:
            return True
        # An initial ("J. R. Smith"): one capital letter, other than the words I and A, then a capital.
        return bool(re.fullmatch(r"[B-HJ-Z]", found.group(1)) and re.match(r"[A-Z]", after))

    # --- The three levels -----------------------------------------------------------

    def _levels(self, sentence: str, index: int, at: float) -> list:
        """The sentence, its clauses, and their phrases, skipping any that only repeat a level above."""
        out, seen = [], set()

        def add(text: str, kind: Kind) -> None:
            text = self._clean(text)
            key = re.sub(r"[.!?]+$", "", text).casefold()
            if key in seen:
                return          # a repeat of a higher level, or of an earlier piece ("no, no, no")
            seen.add(key)
            for window in self._windows(text):
                if any(c.isalnum() for c in window):      # a window of bare punctuation is no hinge
                    out.append(Fragment(window, kind, at, index))

        add(sentence, Kind.SENTENCE)
        for clause in _CLAUSE_BREAK.split(sentence):
            add(clause, Kind.CLAUSE)
            for phrase in _PHRASE_BREAK.split(clause):
                add(phrase, Kind.PHRASE)
        return out

    @staticmethod
    def _clean(piece: str) -> str:
        """Trims whitespace, separators and unbalanced quote marks from the ends of a piece."""
        piece = piece.strip(_EDGES)
        if sum(piece.count(q) for q in _QUOTES) % 2:
            piece = piece.strip(_QUOTES + _EDGES)
        return piece

    def _windows(self, text: str) -> list:
        """The text whole, or cut into consecutive windows of at most `max_words` words."""
        words = text.split()
        if len(words) <= self._max_words:
            return [text]
        return [" ".join(words[i:i + self._max_words]) for i in range(0, len(words), self._max_words)]
