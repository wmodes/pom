"""Unfinished endings: the one definition of a passage that stops before its thought is done.

The oracle is told to match the form, tone and length of its exemplar, so it copies an exemplar's ending as well:
an exemplar that stops at "...is the", or that is a lead-in ending on a colon ("The following bugle signals may be
used:") with its list gone, makes a portent that stops there too. The Bibliomancer will not draw such a passage as an
exemplar, and the library pipeline will not keep one. Both ask this module, so they cannot disagree.

A passage ends UNFINISHED when it is
  * a lead-in whose content is somewhere else: it ends on a colon, even one followed by the dash and quotes old books print
    (`asks thee:--`, `as follows:"`), or on an abbreviation that needs something after it (`viz.`, `chap.`, `fol.`, `e.g.`), or
  * not closed by a mark and its last word cannot end a thought: an article, preposition, conjunction or possessive such as
    "the", "of", "and".

Verse and other open endings (a comma or semicolon, a line with no mark) are NOT unfinished by this test: a stanza may
end that way on purpose. "The" is the test of an ending, not of style.
"""
import re

# A passage ending in one of these is closed. Typographic quotes are included although the library is plain ASCII.
CLOSING_MARKS = ".!?\"')]”’"

# Words that cannot be the last word of a thought. Kept short and certain: a word that can end a sentence ("her", "that")
# is only here because, with no closing mark after it, the passage was cut.
DANGLING_WORDS = frozenset({
    "a", "an", "the", "of", "and", "or", "but", "to", "in", "on", "at", "by", "with", "for", "from", "into", "as",
    "is", "was", "that", "which", "its", "his", "her", "their", "our",
})

_TRAILING_NON_WORD = re.compile(r"\W+$")

# A sentence ENDS at a full stop, question mark or exclamation mark, then any closing quotes or brackets, and the dash some old books print
# after it: .   ?   !   ."   .'   ?"   !'"   .)   .")   .--   ."--   ?!   ...   Unlike CLOSING_MARKS this wants the stop: a quotation
# that ends on a bare closing quote is not a sentence end.
_CLOSERS = "'\")\\]\u201d\u2019"
SENTENCE_END = re.compile(rf"[.!?]+[{_CLOSERS}]*(?:--+|\u2014)?[{_CLOSERS}]*\s*$")


# A colon, then any dashes and closing quotes or brackets, ends a lead-in: "He therefore asks thee:--", 'as follows:"'.
_COLON_LEAD_IN = re.compile(rf":[\s\-\u2014]*[{_CLOSERS}]*\s*$")

# An abbreviation that is always followed by what it points to ("viz. the following", "chap. IV", "fol. 12"), so a passage ending on one
# stopped before its point. Not "etc.", "&c.", "Mr.", "No." or a lone letter: those end real sentences ("say No.", "press C.").
_ABBREVIATION_LEAD_IN = re.compile(rf"(?<![A-Za-z'])(?:viz|chaps?|fols?|vols?|pts?|sect|cf|pp|i\.e|e\.g)\.[{_CLOSERS}]*\s*$", re.IGNORECASE)


def ends_unfinished(text: str) -> bool:
    """True if the text stops before its thought is done (see the module docstring).

    Args:
        text: A passage. Trailing white space is ignored.

    Returns:
        False for text that is empty or not a string: this test judges an ending, and there is none to judge.
    """
    if not isinstance(text, str):
        return False
    stripped = text.rstrip()
    if not stripped:
        return False
    if _COLON_LEAD_IN.search(stripped) or _ABBREVIATION_LEAD_IN.search(stripped):
        return True
    if stripped[-1] in CLOSING_MARKS:
        return False
    words = _TRAILING_NON_WORD.sub("", stripped).split()       # "and then the --" and "and then the," both end on "the"
    return bool(words) and words[-1].lower() in DANGLING_WORDS


def ends_sentence(text: str) -> bool:
    """True if the text ends at a sentence end: . ! or ? followed by any closing quotes or brackets and an optional dash.

    This is the rule the library's cataloguer cuts by: a long passage is cut only after a sentence end, so no piece it makes can stop
    mid-thought. Not a string, or empty, is False.
    """
    return isinstance(text, str) and SENTENCE_END.search(text) is not None
