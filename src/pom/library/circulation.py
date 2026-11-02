"""circulation: how often each shelf and holding should come up.

This module owns one file, library/passages/circulation.json, and nothing else. It parses the file,
applies the defaults, normalizes shelf shares, and checks the file against what is actually
in the library. It does not draw anything (that is the Bibliomancer's job) and it does not read
passages (that is the Catalog's job), so it can be tested with nothing but a small JSON file.

The file is a tree that mirrors the folders in passages/:

    {"shelves": {"news": {"share": 1.0, "holdings": {"news-annual-monitor-...": 0.2}}}}

Design decisions, and why:
  * Nothing here may stop the oracle. The file is edited by hand, so typos will happen; per the
    project principle (see pom/__init__.py) every problem is recovered from and recorded in a
    FaultRecorder, never raised. A bad number counts 1.0, a bad or unknown entry is skipped, a missing
    or unreadable file means all defaults. `strict=True` raises instead, for tests and dev tools.
  * Anything not listed counts 1.0. The file is hand-edited and the library grows, so it will
    drift out of sync; an unlisted shelf or holding must still work.
  * A listed shelf or holding with no passages is reported (it is probably a typo or a removed
    holding and does nothing) but is harmless.
  * Shares are relative and normalized only over the shelves that have passages, so the numbers
    can be written however is convenient (0.8, 1, 1) and a shelf with no passages cannot steal
    probability from the others. If every share is zero the shelves count 1.0 each, since
    drawing nothing is worse than drawing evenly.
  * The module never prints. Each problem is recorded in a FaultRecorder and also logged through the
    standard `logging` module (logger `pom.library.circulation`), which reaches journald once the
    entry point has configured logging.
"""
import json
import math
from pathlib import Path
from typing import Iterable, Mapping, Optional

from pom.log_setup import get_logger
from pom.fault_recorder import FaultRecorder

DEFAULT_WEIGHT = 1.0
_SHELF_KEYS = {"share", "holdings"}
DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)


class CirculationError(ValueError):
    """Raised for any weights problem, but only in strict mode (development and tests)."""


class Circulation:
    """Shelf shares and per-holding weights, with defaults and validation.

    Build one with `Circulation.from_file` or `Circulation.from_dict`, then ask it for shares and
    weights. `report` holds whatever was recovered from while reading; `check` compares the
    weights with the library layout and returns its own report.
    """

    def __init__(self, shares: Mapping[str, float], holding_weights: Mapping[str, float],
                 listed_under: Mapping[str, str], report: Optional[FaultRecorder] = None,
                 strict: bool = False):
        """Use the `from_*` constructors; this takes already-validated values.

        Args:
            shares: Relative share per listed shelf.
            holding_weights: Weight per listed holding.
            listed_under: For each listed holding, the shelf it is listed beneath.
            report: Problems found while reading (a fresh one if not given).
            strict: Whether later checks raise instead of reporting.
        """
        self._shares = dict(shares)
        self._weights = dict(holding_weights)
        self._listed_under = dict(listed_under)
        self._strict = strict
        self.report = report if report is not None else _new_report(strict)

    # --- Construction ---------------------------------------------------------------

    @classmethod
    def from_file(cls, path, strict: bool = False) -> "Circulation":
        """Reads a weights file.

        A missing, unreadable or invalid file gives all-default weights and a warning, so the
        oracle still runs. With strict=True it raises CirculationError instead.
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
    def from_dict(cls, document, strict: bool = False) -> "Circulation":
        """Reads an already-parsed weights document, recovering from anything wrong in it.

        Keys starting with an underscore (such as `_about`) are notes and are ignored.
        """
        return cls._parse(document, _new_report(strict), strict)

    @classmethod
    def _parse(cls, document, report: FaultRecorder, strict: bool) -> "Circulation":
        """Walks the document, keeping what is valid and reporting what is not."""
        shares, weights, listed_under = {}, {}, {}
        if not isinstance(document, dict):
            report.warn("weights file must be a JSON object; every weight counts 1.0")
            return cls(shares, weights, listed_under, report, strict)
        shelves = document.get("shelves", {})
        if not isinstance(shelves, dict):
            report.warn("'shelves' must be an object; every weight counts 1.0")
            return cls(shares, weights, listed_under, report, strict)
        for name, entry in shelves.items():
            if name.startswith("_"):
                continue
            _log.debug("shelf %r: %r", name, entry)
            if not isinstance(entry, dict):
                report.warn(f"shelf '{name}' must be an object with 'share' and 'holdings'; "
                            f"ignoring it")
                continue
            unknown = sorted(k for k in entry if k not in _SHELF_KEYS and not k.startswith("_"))
            if unknown:
                report.warn(f"shelf '{name}' has unknown key(s): {', '.join(unknown)}; ignoring them")
            shares[name] = _number(entry.get("share", DEFAULT_WEIGHT), f"shelf '{name}' share", report)
            holdings = entry.get("holdings", {})
            if not isinstance(holdings, dict):
                report.warn(f"shelf '{name}' 'holdings' must be an object; ignoring it")
                continue
            for holding, weight in holdings.items():
                if holding.startswith("_"):
                    continue
                if holding in weights:
                    report.warn(f"holding '{holding}' is listed under more than one shelf; "
                                f"keeping the first")
                    continue
                weights[holding] = _number(weight, f"holding '{holding}' weight", report)
                listed_under[holding] = name
        _log.debug("parsed %d shelf share(s) and %d holding weight(s)", len(shares), len(weights))
        return cls(shares, weights, listed_under, report, strict)

    # --- Lookups --------------------------------------------------------------------

    def share(self, shelf: str) -> float:
        """A shelf's relative share (not normalized); 1.0 if it is not listed."""
        return self._shares.get(shelf, DEFAULT_WEIGHT)

    def holding_weight(self, holding: str) -> float:
        """A holding's weight within its shelf; 1.0 if it is not listed."""
        return self._weights.get(holding, DEFAULT_WEIGHT)

    def shares_for(self, shelves: Iterable[str]) -> dict[str, float]:
        """Normalized shares over the given shelves, summing to 1.

        Args:
            shelves: The shelves that actually have passages. Others get no probability.

        Returns:
            Shelf -> share. Empty if no shelves were given (nothing to draw from; the
            caller reports that). If every share is zero, each shelf counts 1.0 and a
            warning is recorded in `report` (strict mode raises instead).
        """
        names = list(shelves)
        if not names:
            return {}
        total = sum(self.share(name) for name in names)
        if total <= 0:
            self.report.warn("no shelf has a positive share, so nothing can be drawn; "
                             "counting every shelf 1.0")
            return {name: 1.0 / len(names) for name in names}
        result = {name: self.share(name) / total for name in names}
        _log.debug("normalized shares over %d shelf(s): %s", len(names),
                   ", ".join(f"{n}={v:.3f}" for n, v in sorted(result.items())))
        return result

    # --- Validation -----------------------------------------------------------------

    def check(self, layout: Mapping[str, Iterable[str]]) -> FaultRecorder:
        """Compares the weights with the library layout.

        Args:
            layout: Shelf name -> the holding ids that have passages in it.

        Returns:
            A new FaultRecorder of everything that has drifted: weights listed for a shelf or
            holding with no passages (harmless, but probably a typo), shelves and holdings the
            file does not mention (counted 1.0), and holdings listed under a different shelf
            than the one their passages live in. Strict mode raises on the first of these.
        """
        report = _new_report(self._strict)
        actual = {shelf: set(holdings) for shelf, holdings in layout.items()}
        located = {holding: shelf for shelf, holdings in actual.items() for holding in holdings}

        empty_shelves = sorted(r for r in self._shares if not actual.get(r))
        if empty_shelves:
            report.warn(f"weights list shelf(s) with no passages: {', '.join(empty_shelves)}")
        missing_holdings = sorted(s for s in self._weights if s not in located)
        if missing_holdings:
            report.warn(f"weights list holding(s) with no passages: {', '.join(missing_holdings)}")
        unlisted_shelves = sorted(r for r in actual if r not in self._shares)
        if unlisted_shelves:
            report.warn(f"{len(unlisted_shelves)} shelf(s) not in weights file, "
                        f"counted {DEFAULT_WEIGHT}: {', '.join(unlisted_shelves)}")
        unlisted_holdings = sorted(s for s in located if s not in self._weights)
        if unlisted_holdings:
            report.warn(f"{len(unlisted_holdings)} holding(s) not in weights file, "
                        f"counted {DEFAULT_WEIGHT}: {', '.join(unlisted_holdings)}")
        for holding, shelf in sorted(self._listed_under.items()):
            if holding in located and located[holding] != shelf:
                report.warn(f"holding '{holding}' is listed under '{shelf}' but its "
                            f"passages are in '{located[holding]}'")
        return report


def _new_report(strict: bool) -> FaultRecorder:
    """A FaultRecorder that raises CirculationError in strict mode; otherwise it collects and logs."""
    return FaultRecorder(strict=strict, error=CirculationError, logger=_log)


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
