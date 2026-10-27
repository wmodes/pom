"""The checks a program can make on a portent, for the oracle bake-off.

Part of experiments/oracle-bakeoff. Three of the rubric's seven yes/no items are mechanical, so they are
decided here, the same way for every model, instead of by eye: the portent does not repeat the hinge, it has no
extra text, and its length is close to the exemplar's. (The other four, and any override of these three, are
judged by reading; see documentation/bakeoff-rubric.md.)

Nothing here reads a config file or a model: each function takes text and answers.
"""
import math
import re

# A portent "repeats the hinge" if it shares one unbroken run of words with it that is this long, or this share
# of the hinge's words if the hinge is shorter (whichever run length is smaller). Five words in a row is a
# phrase lifted out of the hinge, not a coincidence.
REPEAT_RUN = 5
REPEAT_SHARE = 0.6

# "Length was close": the portent's word count against the exemplar's, as a ratio. Half to double.
LENGTH_RATIO_LOW = 0.5
LENGTH_RATIO_HIGH = 2.0

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")

# Signs of text the portent was told not to carry. Each is (reason, pattern), matched per line, except the
# fence, which is found anywhere.
_FENCE = "```"
_FIRST_LINE_SIGNS = [
    ("a heading or bold title", re.compile(r"^\s*(#|\*\*)")),
    ("a preface", re.compile(r"^\s*(here('s| is| are)|sure|certainly|of course|okay|ok)\b", re.IGNORECASE)),
    ("a label", re.compile(r"^\s*(passage|theme|title|style example|example)\s*:", re.IGNORECASE)),
]
_ANY_LINE_SIGNS = [
    ("a note or explanation", re.compile(r"^\s*\(?(note|explanation|this (passage|portent|piece))\b", re.IGNORECASE)),
]


def words(text: str) -> list[str]:
    """The words of a text, lower case, with curly apostrophes made straight and punctuation dropped."""
    return _WORD.findall(text.lower().replace("’", "'"))


def longest_shared_run(first: list[str], second: list[str]) -> int:
    """The length of the longest run of words that appears, unbroken and in order, in both lists."""
    best = 0
    previous = [0] * (len(second) + 1)
    for word in first:
        current = [0] * (len(second) + 1)
        for column, other in enumerate(second, start=1):
            if word == other:
                current[column] = previous[column - 1] + 1
                best = max(best, current[column])
        previous = current
    return best


def repeats_hinge(hinge: str, portent: str) -> bool:
    """True if the portent lifts an unbroken run of the hinge's words (see REPEAT_RUN and REPEAT_SHARE)."""
    hinge_words = words(hinge)
    if not hinge_words:
        return False
    needed = min(REPEAT_RUN, math.ceil(REPEAT_SHARE * len(hinge_words)))
    return longest_shared_run(hinge_words, words(portent)) >= needed


def extra_text(portent: str) -> list[str]:
    """What extra text the portent carries: a fence, a heading, a preface, a label, a note. Empty if it is clean."""
    reasons = []
    if _FENCE in portent:
        reasons.append("a code fence")
    lines = [line for line in portent.splitlines() if line.strip()]
    if lines:
        reasons += [reason for reason, sign in _FIRST_LINE_SIGNS if sign.search(lines[0])]
    for reason, sign in _ANY_LINE_SIGNS:
        if any(sign.search(line) for line in lines):
            reasons.append(reason)
    return reasons


def length_ratio(portent: str, exemplar: str) -> float:
    """The portent's word count divided by the exemplar's (0.0 if the exemplar has no words)."""
    exemplar_count = len(words(exemplar))
    return len(words(portent)) / exemplar_count if exemplar_count else 0.0


def length_close(portent: str, exemplar: str) -> bool:
    """True if the portent is between half and double the exemplar's length."""
    return LENGTH_RATIO_LOW <= length_ratio(portent, exemplar) <= LENGTH_RATIO_HIGH


def check_portent(hinge: str, portent: str, exemplars: list[str]) -> dict:
    """All three mechanical checks, as the dictionary stored beside each portent.

    The length is judged against the exemplars joined together, so with more than one exemplar the portent is
    compared with their combined length.

    Returns:
        {"repeats_hinge": bool, "extra_text": [reasons], "length_ratio": float, "length_close": bool}
    """
    joined = "\n\n".join(exemplars)
    return {
        "repeats_hinge": repeats_hinge(hinge, portent),
        "extra_text": extra_text(portent),
        "length_ratio": round(length_ratio(portent, joined), 2),
        "length_close": length_close(portent, joined),
    }
