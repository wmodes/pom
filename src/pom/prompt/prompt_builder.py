"""The PromptBuilder: puts the hinge and a few exemplars into the oracle's prompt.

When the trigger fires, the oracle model is asked for a portent. What it is asked is one block of text,
written by Wes in the config file (`oracle.prompt`), with two places marked for the pieces that change
every time:

  * `{exemplars}`: a handful of corpus chunks drawn by the ExemplarSelector, to show the model the registers
    the oracle speaks in. They are drawn at random and never matched to the hinge.
  * `{hinge}`: the fragment that triggered, word for word.

`build(hinge)` draws the exemplars, fills in both places and returns the finished prompt together with the
chunks it used, so a dev tool can show which exemplars went in. Nothing else is added: no persona, no
instructions, no formatting beyond what the prompt itself says. Anything the model needs to be told lives in
the prompt text, so it can be tuned in one place.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A missing prompt, an empty hinge, or a selector with
    nothing to draw is recorded in `report` and `build` carries on if it can (no exemplars) or returns None if
    it cannot. `strict=True` raises PromptBuilderError instead, for tests and dev tools.
  * The hinge is never logged or put in a FaultRecorder message: nothing that was said in the room is kept.
    Logs and reports hold counts only.
  * The two places are filled in one pass over the prompt, not one after the other, so a corpus chunk that
    happens to contain the text "{hinge}", or a hinge that contains "{exemplars}", is left as it is. Other
    braces in the prompt, the chunks or the hinge are plain text.
  * Exemplars are only drawn if the prompt has an `{exemplars}` place. A prompt without one (a voice that is
    shown just the hinge) gets no draw, so the chunks reported are always the ones the model was shown.
  * Exemplars are separated by a blank line; a chunk keeps its own line breaks (verse stays verse).
  * The prompt is passed in (the entry point takes it from Config); this module never reads Config and has no
    built-in prompt. The ExemplarSelector is passed in too, so a dev tool can give it a seeded random
    generator and get the same exemplars for the same hinge.
"""
import re
from dataclasses import dataclass
from typing import Optional

from pom.corpus.chunk_store import Chunk
from pom.corpus.exemplar_selector import ExemplarSelector
from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

PLACES = re.compile(r"\{(exemplars|hinge)\}")
EXEMPLAR_SEPARATOR = "\n\n"


class PromptBuilderError(ValueError):
    """Raised for any builder problem, but only in strict mode (development and tests)."""


@dataclass(frozen=True, slots=True)
class OraclePrompt:
    """A finished prompt and what went into it.

    Attributes:
        text: The prompt to send to the oracle model.
        exemplars: The chunks that were drawn and placed in it (empty if the prompt has no place for them).
    """
    text: str
    exemplars: tuple[Chunk, ...] = ()


class PromptBuilder:
    """Builds the oracle's prompt for one hinge.

    Attributes:
        report: Problems met (no prompt, an empty hinge, nothing to draw).
        available: True if there is a prompt to build from.
    """

    def __init__(self, prompt: str, selector: ExemplarSelector, strict: bool = False):
        """Args:
            prompt: The oracle prompt, with `{exemplars}` and `{hinge}` marking where those go.
            selector: Where the exemplars are drawn from.
            strict: Raise PromptBuilderError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=PromptBuilderError, logger=_log)
        self._selector = selector
        self._prompt = prompt.strip() if isinstance(prompt, str) else ""
        self._wants_exemplars = False
        if not self._prompt:
            self.report.warn("no oracle prompt was given, so no prompt can be built")
            return
        places = set(PLACES.findall(self._prompt))
        self._wants_exemplars = "exemplars" in places
        if "hinge" not in places:
            self.report.warn("the oracle prompt has no {hinge}, so the model would never be shown the hinge")

    @property
    def available(self) -> bool:
        """True if there is a prompt to build from."""
        return bool(self._prompt)

    def build(self, hinge: str) -> Optional[OraclePrompt]:
        """Draws exemplars and returns the prompt for this hinge.

        Returns None if there is no prompt or the hinge is empty. If nothing could be drawn the prompt is built
        with an empty exemplars place and the problem is recorded.
        """
        if not self.available:
            return None
        if not isinstance(hinge, str) or not hinge.strip():
            self.report.warn("the hinge is empty, so no prompt was built")
            return None
        exemplars = tuple(self._selector.draw()) if self._wants_exemplars else ()
        if self._wants_exemplars and not exemplars:
            self.report.warn("no exemplars could be drawn; the prompt was built without them")
        values = {"exemplars": EXEMPLAR_SEPARATOR.join(chunk.text for chunk in exemplars), "hinge": hinge.strip()}
        text = PLACES.sub(lambda place: values[place.group(1)], self._prompt)
        _log.debug("built a prompt of %d characters with %d exemplar(s)", len(text), len(exemplars))
        return OraclePrompt(text, exemplars)
