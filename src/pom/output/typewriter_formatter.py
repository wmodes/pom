"""The TypewriterFormatter: a portent goes in, text the typewriter can print comes out.

The oracle model writes text for a screen; the Wheelwriter prints plain ASCII on a fixed line. `format(text)` makes the
first fit the second: it takes out the code fences, turns what the typewriter cannot print into what it can (or drops
it), and wraps the lines. It is the step between the PortentWriter and the TypewriterPort.

Design decisions, and why:
  * LINE FEEDS AFTER. The text ends with `linefeeds_after` line feeds (4 by default: the last line's own, then three
    blank lines), so the paper has moved on when the next portent is typed. Text that comes to nothing stays empty,
    with no line feeds, so nothing is typed.
  * Pure text in, text out. No config is read here (the entry point passes `config.output`'s line width and quote
    indent), nothing is logged, and nothing can fail: whatever comes in, printable text comes out. Nothing that was
    said in the room is kept anywhere.
  * CODE FENCES. The prompt shows the model its exemplars and the hinge inside triple backticks, so a model may echo
    that style. A fenced piece is a quotation, and a screen would show it in italics or a block quote; the typewriter has
    neither, so it is indented `quote_indent` spaces, wrapped to what is left of the line, and set off with a blank
    line before and after (or the model's own blank line, never two). The fence lines (and a language tag after the
    opening fence) are never printed. A fence that is never closed, because the model was cut
    off, quotes everything after it. Fences that sit inside a line, or that are never closed, are handled the same way.
  * PRINTABLE ASCII only (space to tilde). Curly quotes, dashes, ellipses and the like become their plain equivalents;
    accented letters lose the accent; a few letters with no accent to lose are spelled out (ß as ss). Anything else,
    an emoji for one, is dropped. Tabs become spaces.
  * Line breaks the model wrote stay line breaks (a portent may be set as lines); a blank line stays one blank line, never
    more. Spaces inside a line are collapsed to one, which also drops any indentation the model wrote, so only a quote is
    ever indented.
  * A word longer than the line is cut at the line width rather than allowed to run into the margin.
"""
import re
import textwrap
import unicodedata

# An opening fence, an optional language tag that is alone on the fence's line, then the quoted text up to the closing
# fence or the end of the text. Lazy, so two fenced pieces are two quotes.
_FENCE = re.compile(r"```[ \t]*(?:[A-Za-z0-9_+-]+[ \t]*(?=\n))?(.*?)(?:```|\Z)", re.DOTALL)

# What the typewriter cannot print, as plain ASCII. Applied after NFKD, which already turns ligatures,
# no-break spaces and the ellipsis into their plain forms and splits accents off their letters (the accents are dropped later).
_SUBSTITUTES = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u00ab": '"', "\u00bb": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2212": "-", "\u2014": "--", "\u2015": "--",
    "\u2022": "*", "\u00b7": "*", "\u2044": "/", "\u00d7": "x",
    "\u00df": "ss", "\u00e6": "ae", "\u00c6": "AE", "\u0153": "oe", "\u0152": "OE", "\u00f8": "o", "\u00d8": "O",
    "\u0111": "d", "\u0110": "D", "\u0142": "l", "\u0141": "L", "\u00b0": " degrees ", "\u00a9": "(c)", "\u00ae": "(R)",
}


class TypewriterFormatter:
    """Makes a portent printable: fences out, ASCII only, wrapped to the line.

    Args:
        line_width: How many characters fit on a line (from `config.output.line_width`).
        quote_indent: How many spaces a fenced quotation is indented. At least one character of the line is kept for the
            quote itself, so an indent as wide as the line is cut down to fit.
        linefeeds_after: How many line feeds follow the portent, 0 or more (from `config.output.linefeeds_after_portent`).
    """

    def __init__(self, line_width: int = 65, quote_indent: int = 4, linefeeds_after: int = 4):
        self.line_width = max(1, line_width)
        self.quote_indent = max(0, min(quote_indent, self.line_width - 1))
        self.linefeeds_after = max(0, linefeeds_after)

    def format(self, text: str) -> str:
        """The text as it should be typed: lines joined with newlines, then `linefeeds_after` line feeds. Empty text gives ''."""
        lines: list[str] = []
        position = 0
        for fence in _FENCE.finditer(text):
            lines += self._plain_lines(text[position:fence.start()])
            lines += self._quote_lines(fence.group(1))
            position = fence.end()
        lines += self._plain_lines(text[position:])
        body = self._tidy(lines)
        return body + "\n" * self.linefeeds_after if body else ""

    def _plain_lines(self, text: str) -> list[str]:
        """The wrapped lines of ordinary text; blank lines are kept (as empty strings) for _tidy to limit."""
        text = _printable(text).strip(" \t")
        lines: list[str] = []
        for line in text.split("\n"):
            lines += textwrap.wrap(" ".join(line.split()), self.line_width) or [""]
        return lines

    def _quote_lines(self, quote: str) -> list[str]:
        """The lines of a fenced quotation, each indented, with a blank line before and after (_tidy keeps only one)."""
        indent = " " * self.quote_indent
        lines: list[str] = []
        for line in _printable(quote).strip().split("\n"):
            lines += textwrap.wrap(" ".join(line.split()), self.line_width - self.quote_indent) or [""]
        return [""] + [indent + line if line else "" for line in lines] + [""]

    @staticmethod
    def _tidy(lines: list[str]) -> str:
        """Join the lines: at most one blank line in a row, none at the start or the end."""
        kept: list[str] = []
        for line in lines:
            if line or (kept and kept[-1]):
                kept.append(line)
        while kept and not kept[-1]:
            kept.pop()
        return "\n".join(kept)


def _printable(text: str) -> str:
    """The text as printable ASCII: newlines made uniform, tabs as spaces, look-alikes swapped, the rest dropped."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(_SUBSTITUTES.get(character, character) for character in text)
    return "".join(c for c in text if c == "\n" or " " <= c <= "~")
