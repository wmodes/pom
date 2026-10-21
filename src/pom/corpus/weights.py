"""Draw weights: how often each register and source should come up.

This module owns one file, corpus/chunks/weights.json, and nothing else. It parses the file,
applies the defaults, normalizes register shares, and checks the file against what is actually
in the corpus. It does not draw anything (that is the Sampler's job) and it does not read
chunks (that is the ChunkStore's job), so it can be tested with nothing but a small JSON file.

The file is a tree that mirrors the folders in chunks/:

    {"registers": {"news": {"share": 1.0, "sources": {"news-annual-monitor-...": 0.2}}}}

Design decisions, and why:
  * Anything not listed counts 1.0. The file is edited by hand and the corpus grows, so it will
    drift out of sync; an unlisted register or source must still work. Drift is reported as a
    warning, never an error, so a new source never stops the oracle.
  * A listed register or source with no chunks IS an error. That is almost always a typo or a
    removed source, and quietly ignoring it would hide a weight that does nothing.
  * Shares are relative and normalized only over the registers that have chunks, so the numbers
    can be written however is convenient (0.8, 1, 1) and a register with no chunks cannot steal
    probability from the others.
  * Bad numbers (negative, NaN, strings, booleans) are errors. A typo such as "shares" for
    "share" is an error too, rather than a silently ignored key.
  * The module never prints or logs. Problems come back as data (a WeightsReport) or raise
    WeightsError, and the caller decides what to do with them.
"""
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

DEFAULT_WEIGHT = 1.0
_REGISTER_KEYS = {"share", "sources"}


class WeightsError(ValueError):
    """The weights file is unreadable, malformed, or contradicts the corpus."""


@dataclass
class WeightsReport:
    """What `Weights.check` found that is worth knowing but not worth stopping for.

    Attributes:
        warnings: Human-readable lines, one per problem (for example an unlisted source).
    """
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when there is nothing to report."""
        return not self.warnings


class Weights:
    """Register shares and per-source weights, with defaults and validation.

    Build one with `Weights.from_file` or `Weights.from_dict`, then ask it for shares and
    weights. `check` compares it with the corpus layout.
    """

    def __init__(self, shares: Mapping[str, float], source_weights: Mapping[str, float],
                 listed_under: Mapping[str, str]):
        """Use the `from_*` constructors; this takes already-validated values.

        Args:
            shares: Relative share per listed register.
            source_weights: Weight per listed source.
            listed_under: For each listed source, the register it is listed beneath.
        """
        self._shares = dict(shares)
        self._weights = dict(source_weights)
        self._listed_under = dict(listed_under)

    # --- Construction ---------------------------------------------------------------

    @classmethod
    def from_file(cls, path) -> "Weights":
        """Reads and validates a weights file.

        Raises:
            WeightsError: If the file is missing, is not JSON, or has bad contents.
        """
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            raise WeightsError(f"cannot read weights file {path}: {error}") from error
        try:
            document = json.loads(text)
        except json.JSONDecodeError as error:
            raise WeightsError(f"weights file {path} is not valid JSON: {error}") from error
        return cls.from_dict(document)

    @classmethod
    def from_dict(cls, document) -> "Weights":
        """Validates an already-parsed weights document.

        Keys starting with an underscore (such as `_about`) are notes and are ignored.

        Raises:
            WeightsError: On any structural or numeric problem.
        """
        if not isinstance(document, dict):
            raise WeightsError("weights file must be a JSON object")
        registers = document.get("registers", {})
        if not isinstance(registers, dict):
            raise WeightsError("'registers' must be an object")
        shares, weights, listed_under = {}, {}, {}
        for name, entry in registers.items():
            if name.startswith("_"):
                continue
            if not isinstance(entry, dict):
                raise WeightsError(f"register '{name}' must be an object with 'share' and 'sources'")
            unknown = set(entry) - _REGISTER_KEYS - {k for k in entry if k.startswith("_")}
            if unknown:
                raise WeightsError(f"register '{name}' has unknown key(s): {', '.join(sorted(unknown))}")
            shares[name] = _number(entry.get("share", DEFAULT_WEIGHT), f"register '{name}' share")
            sources = entry.get("sources", {})
            if not isinstance(sources, dict):
                raise WeightsError(f"register '{name}' 'sources' must be an object")
            for source, weight in sources.items():
                if source.startswith("_"):
                    continue
                if source in weights:
                    raise WeightsError(f"source '{source}' is listed under more than one register")
                weights[source] = _number(weight, f"source '{source}' weight")
                listed_under[source] = name
        return cls(shares, weights, listed_under)

    # --- Lookups --------------------------------------------------------------------

    def share(self, register: str) -> float:
        """A register's relative share (not normalized); 1.0 if it is not listed."""
        return self._shares.get(register, DEFAULT_WEIGHT)

    def source_weight(self, source: str) -> float:
        """A source's weight within its register; 1.0 if it is not listed."""
        return self._weights.get(source, DEFAULT_WEIGHT)

    def shares_for(self, registers: Iterable[str]) -> dict[str, float]:
        """Normalized shares over the given registers, summing to 1.

        Args:
            registers: The registers that actually have chunks. Others get no probability.

        Raises:
            WeightsError: If there are no registers, or every share is zero.
        """
        names = list(registers)
        total = sum(self.share(name) for name in names)
        if not names or total <= 0:
            raise WeightsError("no register has a positive share, so nothing can be drawn")
        return {name: self.share(name) / total for name in names}

    # --- Validation -----------------------------------------------------------------

    def check(self, layout: Mapping[str, Iterable[str]]) -> WeightsReport:
        """Compares the weights with the corpus layout.

        Args:
            layout: Register name -> the source ids that have chunks in it.

        Returns:
            A report of drift worth a warning: unlisted registers or sources, and sources
            listed under a different register than the one their chunks live in.

        Raises:
            WeightsError: If a listed register or source has no chunks.
        """
        actual = {register: set(sources) for register, sources in layout.items()}
        located = {source: register for register, sources in actual.items() for source in sources}

        empty_registers = sorted(r for r in self._shares if not actual.get(r))
        if empty_registers:
            raise WeightsError(f"weights list register(s) with no chunks: {', '.join(empty_registers)}")
        missing_sources = sorted(s for s in self._weights if s not in located)
        if missing_sources:
            raise WeightsError(f"weights list source(s) with no chunks: {', '.join(missing_sources)}")

        report = WeightsReport()
        unlisted_registers = sorted(r for r in actual if r not in self._shares)
        if unlisted_registers:
            report.warnings.append(f"{len(unlisted_registers)} register(s) not in weights file, "
                                   f"counted {DEFAULT_WEIGHT}: {', '.join(unlisted_registers)}")
        unlisted_sources = sorted(s for s in located if s not in self._weights)
        if unlisted_sources:
            report.warnings.append(f"{len(unlisted_sources)} source(s) not in weights file, "
                                   f"counted {DEFAULT_WEIGHT}: {', '.join(unlisted_sources)}")
        for source, register in sorted(self._listed_under.items()):
            if located[source] != register:
                report.warnings.append(f"source '{source}' is listed under '{register}' but its "
                                       f"chunks are in '{located[source]}'")
        return report


def _number(value, label: str) -> float:
    """A finite, non-negative number, or WeightsError. Booleans and strings are rejected."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeightsError(f"{label} must be a number, got {value!r}")
    if not math.isfinite(value) or value < 0:
        raise WeightsError(f"{label} must be a finite number of at least 0, got {value!r}")
    return float(value)
