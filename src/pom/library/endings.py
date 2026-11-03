"""Unfinished endings: the one definition of a passage that stops before its thought is done.

The oracle is told to match the form, tone and length of its exemplar, so it copies an exemplar's ending as well:
an exemplar that stops at "...is the", or that is a lead-in ending on a colon ("The following bugle signals may be
used:") with its list gone, makes a portent that stops there too. The Bibliomancer will not draw such a passage as an
exemplar, and the library pipeline will not keep one. Both ask this module, so they cannot disagree.

A passage ends UNFINISHED when its last character is not a closing mark and either
  * it is a colon (a lead-in whose content is somewhere else), or
  * its last word cannot end a thought: an article, preposition, conjunction or possessive such as "the", "of", "and".

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
    if not stripped or stripped[-1] in CLOSING_MARKS:
        return False
    if stripped[-1] == ":":
        return True
    words = _TRAILING_NON_WORD.sub("", stripped).split()       # "and then the --" and "and then the," both end on "the"
    return bool(words) and words[-1].lower() in DANGLING_WORDS
