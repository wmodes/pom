"""The PortentWriter: a hinge goes in, a portent comes out.

It joins the two halves of the voice side. The PromptBuilder draws exemplars and fills the oracle's prompt with the
hinge; the OracleModelClient sends that prompt to the oracle model and returns what it wrote. `write(hinge)` runs
both and hands back a Portent: the text, the exemplars that were shown to the model, and how long it took. It is the
one call the Conductor makes when the trigger fires.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). If the prompt cannot be built or the model writes nothing,
    `write` returns None and the reason is in the report of the part that met the problem. `strict=True` raises
    PortentWriterError for the writer's own problem; the builder and the client have a strict mode of their own.
  * The hinge and the portent are never logged or put in a report: nothing that was said in the room is kept.
  * The parts are passed in, already built, so a test can give the writer fakes and a dev tool can build them from
    the config again for each hinge (to see prompt edits at once). This module never reads Config.
  * REPEATABLE EXEMPLARS. `write(hinge, seed=N)` seeds the random generator before the exemplars are drawn, so the
    same hinge and seed show the model the same exemplars, which makes two prompts or two models comparable. The
    generator must be the very one the ExemplarSelector was built with, passed in as `rng`; the writer cannot reach it
    any other way. Without a seed, draws stay random, which is what the Printer does. A seed given to a writer that
    has no `rng` is recorded, since it would otherwise be silently ignored.
  * The report of the writer, the builder and the client are read together with `report_lines()`, so whoever shows
    them needs only one call.
"""
import random
from dataclasses import dataclass
from typing import Optional

from pom.corpus.chunk_store import Chunk
from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger
from pom.prompt.prompt_builder import PromptBuilder
from pom.voice.oracle_model_client import OracleModelClient

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)


class PortentWriterError(ValueError):
    """Raised for the writer's own problem, but only in strict mode (development and tests)."""


@dataclass(frozen=True, slots=True)
class Portent:
    """What the oracle wrote and what it was shown.

    Attributes:
        text: The portent, as the model wrote it (only the whitespace around it is trimmed).
        exemplars: The corpus chunks that were put into the prompt, in the order they were shown.
        seconds: How long the model took to write it.
        cut_off: True if the model stopped because it reached the most it may write, rather than finishing.
    """
    text: str
    exemplars: tuple[Chunk, ...]
    seconds: float
    cut_off: bool

    @property
    def words(self) -> int:
        """How many words the portent has."""
        return len(self.text.split())


class PortentWriter:
    """Writes the portent for one hinge.

    Attributes:
        report: The writer's own problems (a seed it could not use).
    """

    def __init__(self, builder: PromptBuilder, client: OracleModelClient, rng: Optional[random.Random] = None,
                 strict: bool = False):
        """Args:
            builder: Draws the exemplars and builds the prompt.
            client: Sends the prompt to the oracle model.
            rng: The random generator the builder's ExemplarSelector was built with; needed only for seeded writes.
            strict: Raise PortentWriterError on the writer's own problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=PortentWriterError, logger=_log)
        self._builder = builder
        self._client = client
        self._rng = rng

    def write(self, hinge: str, seed: Optional[int] = None) -> Optional[Portent]:
        """The portent for this hinge, or None if none could be had.

        Args:
            hinge: The sentence that triggered, word for word.
            seed: Seeds the exemplar draw so the same hinge and seed show the same exemplars. None leaves it random.

        Returns None if there is no prompt, the hinge is empty, or the model wrote nothing; the part that met the
        problem says why in its report. Never raises (except in strict mode).
        """
        if seed is not None:
            if self._rng is None:
                self.report.warn("a seed was given but the writer has no random generator, so the exemplars stay random")
            else:
                self._rng.seed(seed)
        prompt = self._builder.build(hinge)
        if prompt is None:
            return None
        text = self._client.portent(prompt.text)
        if text is None:
            return None
        _log.debug("wrote a portent of %d words from %d exemplar(s)", len(text.split()), len(prompt.exemplars))
        return Portent(text, prompt.exemplars, self._client.last_seconds, self._client.last_cut_off)

    def report_lines(self) -> list[str]:
        """The problems met by the writer, the prompt builder and the model client, as lines to show."""
        return [*self.report.lines(), *self._builder.report.lines(), *self._client.report.lines()]
