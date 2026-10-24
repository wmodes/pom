"""Draw weights: how often each register and source should come up.

This module owns one file, corpus/chunks/weights.json, and nothing else. It parses the file,
applies the defaults, normalizes register shares, and checks the file against what is actually
in the corpus. It does not draw anything (that is the Sampler's job) and it does not read
chunks (that is the ChunkStore's job), so it can be tested with nothing but a small JSON file.

The file is a tree that mirrors the folders in chunks/:

    {"registers": {"news": {"share": 1.0, "sources": {"news-annual-monitor-...": 0.2}}}}

Design decisions, and why:
  * Nothing here may stop the oracle. The file is edited by hand, so typos will happen; per the
    project principle (see pom/__init__.py) every problem is recovered from and recorded in a
    FaultRecorder, never raised. A bad number counts 1.0, a bad or unknown entry is skipped, a missing
    or unreadable file means all defaults. `strict=True` raises instead, for tests and dev tools.
  * Anything not listed counts 1.0. The file is hand-edited and the corpus grows, so it will
    drift out of sync; an unlisted register or source must still work.
  * A listed register or source with no chunks is reported (it is probably a typo or a removed
    source and does nothing) but is harmless.
  * Shares are relative and normalized only over the registers that have chunks, so the numbers
    can be written however is convenient (0.8, 1, 1) and a register with no chunks cannot steal
    probability from the others. If every share is zero the registers count 1.0 each, since
    drawing nothing is worse than drawing evenly.
  * The module never prints. Each problem is recorded in a FaultRecorder and also logged through the
    standard `logging` module (logger `pom.corpus.weights`), which reaches journald once the
    entry point has configured logging.
"""
import json
import math
from pathlib import Path
from typing import Iterable, Mapping, Optional

from pom.log_setup import get_logger
from pom.fault_recorder import FaultRecorder

DEFAULT_WEIGHT = 1.0
_REGISTER_KEYS = {"share", "sources"}
DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)


class WeightsError(ValueError):
    """Raised for any weights problem, but only in strict mode (development and tests)."""


class Weights:
    """Register shares and per-source weights, with defaults and validation.

    Build one with `Weights.from_file` or `Weights.from_dict`, then ask it for shares and
    weights. `report` holds whatever was recovered from while reading; `check` compares the
    weights with the corpus layout and returns its own report.
    """

    def __init__(self, shares: Mapping[str, float], source_weights: Mapping[str, float],
                 listed_under: Mapping[str, str], report: Optional[FaultRecorder] = None,
                 strict: bool = False):
        """Use the `from_*` constructors; this takes already-validated values.

        Args:
            shares: Relative share per listed register.
            source_weights: Weight per listed source.
            listed_under: For each listed source, the register it is listed beneath.
            report: Problems found while reading (a fresh one if not given).
            strict: Whether later checks raise instead of reporting.
        """
        self._shares = dict(shares)
        self._weights = dict(source_weights)
        self._listed_under = dict(listed_under)
        self._strict = strict
        self.report = report if report is not None else _new_report(strict)

    # --- Construction ---------------------------------------------------------------

    @classmethod
    def from_file(cls, path, strict: bool = False) -> "Weights":
        """Reads a weights file.

        A missing, unreadable or invalid file gives all-default weights and a warning, so the
        oracle still runs. With strict=True it raises WeightsError instead.
        """
        report = _new_report(strict)
        path = Path(path)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except OSError as error:
            report.warn(f"cannot read weights file {path}: {error}; every weight counts 1.0")
            document = {}
        except json.JSONDecodeError as error:
            report.warn(f"weights file {path} is not valid JSON: {error}; every weight counts 1.0")
            document = {}
        return cls._parse(document, report, strict)

    @classmethod
    def from_dict(cls, document, strict: bool = False) -> "Weights":
        """Reads an already-parsed weights document, recovering from anything wrong in it.

        Keys starting with an underscore (such as `_about`) are notes and are ignored.
        """
        return cls._parse(document, _new_report(strict), strict)

    @classmethod
    def _parse(cls, document, report: FaultRecorder, strict: bool) -> "Weights":
        """Walks the document, keeping what is valid and reporting what is not."""
        shares, weights, listed_under = {}, {}, {}
        if not isinstance(document, dict):
            report.warn("weights file must be a JSON object; every weight counts 1.0")
            return cls(shares, weights, listed_under, report, strict)
        registers = document.get("registers", {})
        if not isinstance(registers, dict):
            report.warn("'registers' must be an object; every weight counts 1.0")
            return cls(shares, weights, listed_under, report, strict)
        for name, entry in registers.items():
            if name.startswith("_"):
                continue
            _log.debug("register %r: %r", name, entry)
            if not isinstance(entry, dict):
                report.warn(f"register '{name}' must be an object with 'share' and 'sources'; "
                            f"ignoring it")
                continue
            unknown = sorted(k for k in entry if k not in _REGISTER_KEYS and not k.startswith("_"))
            if unknown:
                report.warn(f"register '{name}' has unknown key(s): {', '.join(unknown)}; ignoring them")
            shares[name] = _number(entry.get("share", DEFAULT_WEIGHT), f"register '{name}' share", report)
            sources = entry.get("sources", {})
            if not isinstance(sources, dict):
                report.warn(f"register '{name}' 'sources' must be an object; ignoring it")
                continue
            for source, weight in sources.items():
                if source.startswith("_"):
                    continue
                if source in weights:
                    report.warn(f"source '{source}' is listed under more than one register; "
                                f"keeping the first")
                    continue
                weights[source] = _number(weight, f"source '{source}' weight", report)
                listed_under[source] = name
        _log.debug("parsed %d register share(s) and %d source weight(s)", len(shares), len(weights))
        return cls(shares, weights, listed_under, report, strict)

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

        Returns:
            Register -> share. Empty if no registers were given (nothing to draw from; the
            caller reports that). If every share is zero, each register counts 1.0 and a
            warning is recorded in `report` (strict mode raises instead).
        """
        names = list(registers)
        if not names:
            return {}
        total = sum(self.share(name) for name in names)
        if total <= 0:
            self.report.warn("no register has a positive share, so nothing can be drawn; "
                             "counting every register 1.0")
            return {name: 1.0 / len(names) for name in names}
        result = {name: self.share(name) / total for name in names}
        _log.debug("normalized shares over %d register(s): %s", len(names),
                   ", ".join(f"{n}={v:.3f}" for n, v in sorted(result.items())))
        return result

    # --- Validation -----------------------------------------------------------------

    def check(self, layout: Mapping[str, Iterable[str]]) -> FaultRecorder:
        """Compares the weights with the corpus layout.

        Args:
            layout: Register name -> the source ids that have chunks in it.

        Returns:
            A new FaultRecorder of everything that has drifted: weights listed for a register or
            source with no chunks (harmless, but probably a typo), registers and sources the
            file does not mention (counted 1.0), and sources listed under a different register
            than the one their chunks live in. Strict mode raises on the first of these.
        """
        report = _new_report(self._strict)
        actual = {register: set(sources) for register, sources in layout.items()}
        located = {source: register for register, sources in actual.items() for source in sources}

        empty_registers = sorted(r for r in self._shares if not actual.get(r))
        if empty_registers:
            report.warn(f"weights list register(s) with no chunks: {', '.join(empty_registers)}")
        missing_sources = sorted(s for s in self._weights if s not in located)
        if missing_sources:
            report.warn(f"weights list source(s) with no chunks: {', '.join(missing_sources)}")
        unlisted_registers = sorted(r for r in actual if r not in self._shares)
        if unlisted_registers:
            report.warn(f"{len(unlisted_registers)} register(s) not in weights file, "
                        f"counted {DEFAULT_WEIGHT}: {', '.join(unlisted_registers)}")
        unlisted_sources = sorted(s for s in located if s not in self._weights)
        if unlisted_sources:
            report.warn(f"{len(unlisted_sources)} source(s) not in weights file, "
                        f"counted {DEFAULT_WEIGHT}: {', '.join(unlisted_sources)}")
        for source, register in sorted(self._listed_under.items()):
            if source in located and located[source] != register:
                report.warn(f"source '{source}' is listed under '{register}' but its "
                            f"chunks are in '{located[source]}'")
        return report


def _new_report(strict: bool) -> FaultRecorder:
    """A FaultRecorder that raises WeightsError in strict mode; otherwise it collects and logs."""
    return FaultRecorder(strict=strict, error=WeightsError, logger=_log)


def _number(value, label: str, report: FaultRecorder) -> float:
    """A finite, non-negative number; otherwise a warning and the default (1.0).

    Booleans and strings are rejected: `true` is almost certainly a typo, not a weight of 1.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        report.warn(f"{label} must be a number, got {value!r}; using {DEFAULT_WEIGHT}")
        return DEFAULT_WEIGHT
    if not math.isfinite(value) or value < 0:
        report.warn(f"{label} must be a finite number of at least 0, got {value!r}; "
                    f"using {DEFAULT_WEIGHT}")
        return DEFAULT_WEIGHT
    return float(value)
