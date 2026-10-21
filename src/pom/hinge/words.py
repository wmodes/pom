"""Word tools: reading a fragment as words, with what each word is.

The Scorer rates a fragment by its words, not by the raw string, so it first needs each word's
base form ("went" is "go"), its part of speech, and whether it carries any interest at all. This
module does that and nothing else: text in, a list of `Word` objects out.

For each word it works out:
    lemma        the base form, used to look the word up in the corpus and in WordNet
    tag          the part of speech (Penn Treebank: NN, NNP, VB, JJ, UH, CD...), "" if untagged
    is_name      a proper noun (tag NNP/NNPS): Whisper capitalizes names and the tagger sees them
    is_number    a digit or a number word, or tagged CD
    is_stopword  a small function word ("the", "of", "and") from NLTK's English list
    is_filler    spoken padding ("uh", "like", "you know", "okay") from a configurable list, plus
                 any interjection the tagger finds (UH)
    content      what is left: a word that can carry interest (not a stopword, filler or number)

Design decisions, and why:
  * NLTK does the linguistics (tagger, lemmatizer, stopwords, WordNet), so nothing is hand-rolled
    that a well-used library already does. Its data lives in the project's data/nltk folder, set up
    by scripts/setup/setup-nltk-data.sh; the entry point passes that path in.
  * Nothing here may stop the oracle (see pom/__init__.py). If the NLTK data is missing, the tools
    fall back to plain rules (no tags, lowercase as the lemma, a short built-in stopword list, a
    capitalized word that is not the first as a name) and say so in `report`, so the Scorer is
    weaker but still works. `strict=True` raises WordToolsError instead, for tests and dev tools.
  * The tagger, lemmatizer and stopword list can be passed in, so tests run with fakes and need no
    data. The defaults load the real ones.
  * Tokenizing is a simple pattern, not NLTK's tokenizer, so there is no extra data to fetch.
    Apostrophes and hyphens inside a word are kept ("don't", "well-known").
  * Lemmas are cached: lemmatizing is the slow step and the same words recur constantly.
  * The filler list is a setting: it is the kind of thing a person tunes, so it will move into
    config when the Scorer's settings do.
"""
import logging
import re
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from pom.logs import get_logger
from pom.report import Report

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_FILLERS = (
    "uh", "um", "umm", "uhh", "er", "erm", "hmm", "mm", "mhm", "like", "you know", "i mean",
    "kind of", "sort of", "kinda", "sorta", "okay", "ok", "yeah", "yep", "nope", "well", "so",
    "anyway", "anyways", "actually", "basically", "literally", "totally", "really", "just",
    "whatever",
)

# Used only if NLTK's stopword list cannot be loaded.
_FALLBACK_STOPWORDS = frozenset(
    "a an the and or but if of at by for with about as into to from in on off over under again "
    "i me my we our you your he him his she her it its they them their this that these those am is "
    "are was were be been being have has had do does did not no nor so than too very can will "
    "would should could may might must shall s t".split())

_NUMBER_WORDS = frozenset(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty "
    "ninety hundred thousand million billion dozen".split())

_TOKEN = re.compile(r"[A-Za-z0-9]+(?:['’.\-][A-Za-z0-9]+)*")
_WORDNET_POS = {"N": "n", "V": "v", "J": "a", "R": "r"}


class WordToolsError(ValueError):
    """Raised for any word-tools problem, but only in strict mode (development and tests)."""


@dataclass(frozen=True, slots=True)
class Word:
    """One word of a fragment, and what it is.

    Attributes:
        text: The word as spoken (case kept).
        lemma: Its base form, lowercase.
        tag: Penn Treebank part-of-speech tag; "" if no tagger was available.
        is_name: A proper noun.
        is_number: A digit, a number word, or tagged as a number.
        is_stopword: A small function word.
        is_filler: Spoken padding or an interjection.
    """
    text: str
    lemma: str
    tag: str = ""
    is_name: bool = False
    is_number: bool = False
    is_stopword: bool = False
    is_filler: bool = False

    @property
    def lower(self) -> str:
        """The word in lowercase, as spoken."""
        return self.text.lower()

    @property
    def content(self) -> bool:
        """True if the word can carry interest: not a stopword, filler or number."""
        return not (self.is_stopword or self.is_filler or self.is_number)

    @property
    def wordnet_pos(self) -> Optional[str]:
        """The part of speech as WordNet writes it (n, v, a, r), or None if it has no match."""
        return _WORDNET_POS.get(self.tag[:1]) if self.tag else None


class WordTools:
    """Turns text into `Word` objects.

    Attributes:
        report: Problems recovered from (missing NLTK data, input that was not text).
        tagging: True if a real part-of-speech tagger is in use.
    """

    def __init__(self, nltk_data: Optional[str] = None, fillers: Iterable[str] = DEFAULT_FILLERS,
                 strict: bool = False, tagger: Optional[Callable] = None,
                 lemmatizer: Optional[Callable] = None, stopwords: Optional[Iterable[str]] = None):
        """Args:
            nltk_data: Folder holding the NLTK data (the project's data/nltk). None uses NLTK's
                own search path.
            fillers: Words and short phrases that are spoken padding.
            strict: Raise WordToolsError on any problem instead of recording it.
            tagger: A function taking a list of words and returning (word, tag) pairs. Default:
                NLTK's perceptron tagger.
            lemmatizer: A function (lowercase word, wordnet pos or None) -> base form. Default:
                NLTK's WordNet lemmatizer.
            stopwords: The stopword set. Default: NLTK's English list.
        """
        self.report = Report(strict=strict, error=WordToolsError, logger=_log)
        self._fillers = {f.strip().lower() for f in fillers if str(f).strip()}
        self._phrase_fillers = sorted((f for f in self._fillers if " " in f), key=len, reverse=True)
        self._cache: dict = {}
        self._tagger, self._lemmatizer, self._stopwords = tagger, lemmatizer, stopwords
        if tagger is None or lemmatizer is None or stopwords is None:
            self._load_nltk(nltk_data)
        self.tagging = self._tagger is not None
        if self._lemmatizer is None:
            self._lemmatizer = lambda word, pos: word
        if self._stopwords is None:
            self._stopwords = _FALLBACK_STOPWORDS
        self._stopwords = frozenset(self._stopwords)

    def _load_nltk(self, nltk_data: Optional[str]) -> None:
        """Loads whichever of the tagger, lemmatizer and stopwords were not passed in.

        A resource that cannot be loaded is reported and left to the plain-rule fallback.
        """
        try:
            import nltk
        except ImportError as error:
            self.report.warn(f"nltk is not installed ({error}); using plain rules for words")
            return
        if nltk_data:
            nltk.data.path.insert(0, str(nltk_data))
        if self._tagger is None:
            try:
                nltk.pos_tag(["probe"])
                self._tagger = nltk.pos_tag
            except Exception as error:      # missing data raises LookupError; never stop on it
                self.report.warn(f"no part-of-speech tagger ({_first_line(error)}); words will be "
                                 f"untagged and names guessed from capital letters")
        if self._lemmatizer is None:
            try:
                from nltk.stem import WordNetLemmatizer
                lemmatize = WordNetLemmatizer().lemmatize
                lemmatize("probes", "n")
                self._lemmatizer = lambda word, pos: lemmatize(word, pos) if pos else word
            except Exception as error:
                self.report.warn(f"no WordNet lemmatizer ({_first_line(error)}); using words as "
                                 f"they were spoken")
        if self._stopwords is None:
            try:
                from nltk.corpus import stopwords
                self._stopwords = stopwords.words("english")
            except Exception as error:
                self.report.warn(f"no NLTK stopword list ({_first_line(error)}); using a short "
                                 f"built-in list")

    # --- Reading text ---------------------------------------------------------------

    def analyze(self, text) -> list:
        """Reads text as a list of Words, in order. Non-text or empty text gives an empty list."""
        if not isinstance(text, str):
            if text is not None:
                self.report.warn(f"word tools expect text, got {type(text).__name__}; ignoring it")
            return []
        tokens = _TOKEN.findall(text)
        if not tokens:
            return []
        tags = self._tag(tokens)
        padding = self._filler_positions([t.lower() for t in tokens])
        words = []
        for index, (token, tag) in enumerate(zip(tokens, tags)):
            lower = token.lower()
            number = tag == "CD" or lower in _NUMBER_WORDS or any(c.isdigit() for c in lower)
            filler = index in padding or lower in self._fillers or tag == "UH"
            if self.tagging:
                name = tag in ("NNP", "NNPS")
            else:
                name = token[0].isupper() and index > 0 and lower != "i"
            # A capitalized "Uh" at the start of a sentence gets tagged as a name; padding never is.
            words.append(Word(
                text=token, lemma=self._lemma(lower, tag), tag=tag, is_name=name and not filler,
                is_number=number, is_stopword=lower in self._stopwords, is_filler=filler))
        return words

    def content_words(self, text) -> list:
        """Just the words of the text that can carry interest."""
        return [word for word in self.analyze(text) if word.content]

    def _tag(self, tokens: list) -> list:
        """Part-of-speech tags for the tokens, or empty strings if tagging is unavailable."""
        if self._tagger is None:
            return [""] * len(tokens)
        try:
            tagged = self._tagger(tokens)
            return [tag for _, tag in tagged]
        except Exception as error:
            self.report.warn(f"tagging failed ({_first_line(error)}); treating words as untagged")
            return [""] * len(tokens)

    def _lemma(self, lower: str, tag: str) -> str:
        """The base form of a word, cached because lemmatizing is the slow step."""
        key = (lower, tag[:1])
        if key not in self._cache:
            try:
                self._cache[key] = self._lemmatizer(lower, _WORDNET_POS.get(tag[:1]))
            except Exception:
                self._cache[key] = lower
        return self._cache[key]

    def _filler_positions(self, lowers: list) -> set:
        """Indexes of words inside a multi-word filler such as "you know" or "kind of"."""
        found = set()
        for phrase in self._phrase_fillers:
            parts = phrase.split()
            for start in range(len(lowers) - len(parts) + 1):
                if lowers[start:start + len(parts)] == parts:
                    found.update(range(start, start + len(parts)))
        return found


def _first_line(error: Exception) -> str:
    """The first meaningful line of an exception message, for a short log line."""
    for line in str(error).splitlines():
        line = line.strip().strip("*")
        if line and not line.startswith("Resource") and "Attempted" not in line:
            return line[:120]
    return type(error).__name__
