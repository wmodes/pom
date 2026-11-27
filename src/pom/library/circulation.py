"""Circulation: the draw weights, how often each shelf and holding should come up.

This module owns one file, library/passages/circulation.json, and nothing else. It parses the file,
applies the defaults, normalizes shelf shares, and checks the file against what is actually
in the library. It does not draw anything (that is the Bibliomancer's job) and it does not read
passages (that is the Catalog's job), so it can be tested with nothing but a small JSON file.

The file is a tree that mirrors the folders in passages/:

    {"shelves": {"news": {"share": 1.0, "holdings": {"news-annual-monitor-...": 0.2}}}}

Design decisions, and why:
  * Nothing here may stop the oracle. The file is edited by hand, so typos will happen; per the
    project principle (see pom/__init__.py) every problem is recovered from and recorded in a
    log, never raised. A bad number counts 1.0, a bad or unknown entry is skipped, a missing
    or unreadable file means all defaults. `strict=True` raises instead, for tests and dev tools.
  * Anything not listed counts 1.0. The file is hand-edited and the library grows, so it will
    drift out of sync; an unlisted shelf or holding must still work.
  * A listed shelf or holding with no passages is reported (it is probably a typo or a removed
    holding and does nothing) but is harmless.
  * Shares are relative and normalized only over the shelves that have passages, so the numbers
    can be written however is convenient (0.8, 1, 1) and a shelf with no passages cannot steal
    probability from the others. If every share is zero the shelves count 1.0 each, since
    drawing nothing is worse than drawing evenly.
  * The module never prints. Each problem is logged through the
    standard `logging` module (logger `pom.library.circulation`), which reaches journald once the
    entry point has configured logging.
"""
import json
import math
from pathlib import Path
from typing import Iterable, Mapping, Optional

from pom.bookkeeper.bookkeeper import Bookkeeper

DEFAULT_WEIGHT = 1.0
_SHELF_KEYS = {"share", "holdings"}
DEBUG = None   # None follows the project debug flag; True/False overrides it for this module


class CirculationError(ValueError):
    """Raised for any circulation problem, but only in strict mode (development and tests)."""


class Circulation:
    """The draw weights (shelf shares and per-holding weights), with defaults and validation.

    Build one with `Circulation.from_file` or `Circulation.from_dict`, then ask it for shares and
    weights. Whatever was recovered from while reading is logged; `check` compares the
    circulation with the library layout and logs what has drifted.
    """

    def __init__(self, document, strict: Optional[bool] = None, bookkeeper: Optional[Bookkeeper] = None):
        """Reads an already-parsed circulation document, recovering from anything wrong in it. `from_file` reads the file first.

        Keys starting with an underscore (such as `_about`) are notes and are ignored.

        Args:
            document: The circulation, as parsed from its JSON (anything else gives all-default weights and a warning).
            strict: Raise CirculationError on any problem instead of logging it; None (the default) follows the project's `strict` setting.
            bookkeeper: The Bookkeeper the entry point built; it hands this class its logger. None (a test or a tool) gives a plain one.
        """
        self.logger = (bookkeeper or Bookkeeper()).logger_for(__name__, CirculationError, DEBUG, strict)
        self._shares: dict[str, float] = {}
        self._weights: dict[str, float] = {}
        self._listed_under: dict[str, str] = {}
        self._read(document)

    # --- Construction ---------------------------------------------------------------

    @classmethod
    def from_file(cls, path, strict: Optional[bool] = None, bookkeeper: Optional[Bookkeeper] = None) -> "Circulation":
        """Reads a circulation file.

        A missing, unreadable or invalid file gives all-default weights and a warning, so the
        oracle still runs. With strict=True it raises CirculationError instead.
        """
        logger = (bookkeeper or Bookkeeper()).logger_for(__name__, CirculationError, DEBUG, strict)   # the file is read before there is a Circulation
        path = Path(path)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except OSError as error:
            logger.warning("cannot read circulation file %s: %s; every weight counts 1.0", path, error)
            document = {}
        except json.JSONDecodeError as error:
            logger.warning("circulation file %s is not valid JSON: %s; every weight counts 1.0", path, error)
            document = {}
        return cls(document, strict, bookkeeper)

    @classmethod
    def from_dict(cls, document, strict: Optional[bool] = None, bookkeeper: Optional[Bookkeeper] = None) -> "Circulation":
        """Reads an already-parsed circulation document (the same as building one directly)."""
        return cls(document, strict, bookkeeper)

    def _read(self, document) -> None:
        """Walks the document, keeping what is valid and logging what is not."""
        if not isinstance(document, dict):
            self.logger.warning("circulation file must be a JSON object; every weight counts 1.0")
            return
        shelves = document.get("shelves", {})
        if not isinstance(shelves, dict):
            self.logger.warning("'shelves' must be an object; every weight counts 1.0")
            return
        for name, entry in shelves.items():
            if name.startswith("_"):
                continue
            self.logger.debug("shelf %r: %r", name, entry)
            if not isinstance(entry, dict):
                self.logger.warning("shelf '%s' must be an object with 'share' and 'holdings'; ignoring it", name)
                continue
            unknown = sorted(k for k in entry if k not in _SHELF_KEYS and not k.startswith("_"))
            if unknown:
                self.logger.warning("shelf '%s' has unknown key(s): %s; ignoring them", name, ", ".join(unknown))
            self._shares[name] = self._number(entry.get("share", DEFAULT_WEIGHT), f"shelf '{name}' share")
            holdings = entry.get("holdings", {})
            if not isinstance(holdings, dict):
                self.logger.warning("shelf '%s' 'holdings' must be an object; ignoring it", name)
                continue
            for holding, weight in holdings.items():
                if holding.startswith("_"):
                    continue
                if holding in self._weights:
                    self.logger.warning("holding '%s' is listed under more than one shelf; keeping the first", holding)
                    continue
                self._weights[holding] = self._number(weight, f"holding '{holding}' weight")
                self._listed_under[holding] = name
        self.logger.debug("parsed %d shelf share(s) and %d holding weight(s)", len(self._shares), len(self._weights))

    def _number(self, value, label: str) -> float:
        """A finite, non-negative number; otherwise a warning and the default (1.0).

        Booleans and strings are rejected: `true` is almost certainly a typo, not a weight of 1.
        """
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.logger.warning("%s must be a number, got %r; using %s", label, value, DEFAULT_WEIGHT)
            return DEFAULT_WEIGHT
        if not math.isfinite(value) or value < 0:
            self.logger.warning("%s must be a finite number of at least 0, got %r; using %s", label, value, DEFAULT_WEIGHT)
            return DEFAULT_WEIGHT
        return float(value)

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
            warning is logged (strict mode raises instead).
        """
        names = list(shelves)
        if not names:
            return {}
        total = sum(self.share(name) for name in names)
        if total <= 0:
            self.logger.warning("no shelf has a positive share, so nothing can be drawn; "
                             "counting every shelf 1.0")
            return {name: 1.0 / len(names) for name in names}
        result = {name: self.share(name) / total for name in names}
        self.logger.debug("normalized shares over %d shelf(s): %s", len(names),
                          ", ".join(f"{n}={v:.3f}" for n, v in sorted(result.items())))
        return result

    # --- Validation -----------------------------------------------------------------

    def check(self, layout: Mapping[str, Iterable[str]]) -> None:
        """Compares the circulation with the library layout.

        Args:
            layout: Shelf name -> the holding ids that have passages in it.

        Logs a warning for everything that has drifted: entries listed for a shelf or holding with no passages (harmless, but probably a typo),
        shelves and holdings the file does not mention (counted 1.0), and holdings listed under a different shelf than the one their passages
        live in. Strict mode raises on the first of these.
        """
        logger = self.logger
        actual = {shelf: set(holdings) for shelf, holdings in layout.items()}
        located = {holding: shelf for shelf, holdings in actual.items() for holding in holdings}

        empty_shelves = sorted(r for r in self._shares if not actual.get(r))
        if empty_shelves:
            logger.warning("circulation lists shelf(s) with no passages: %s", ", ".join(empty_shelves))
        missing_holdings = sorted(s for s in self._weights if s not in located)
        if missing_holdings:
            logger.warning("circulation lists holding(s) with no passages: %s", ", ".join(missing_holdings))
        unlisted_shelves = sorted(r for r in actual if r not in self._shares)
        if unlisted_shelves:
            logger.warning("%s shelf(s) not in circulation file, counted %s: %s", len(unlisted_shelves), DEFAULT_WEIGHT, ", ".join(unlisted_shelves))
        unlisted_holdings = sorted(s for s in located if s not in self._weights)
        if unlisted_holdings:
            logger.warning("%s holding(s) not in circulation file, counted %s: %s",
                           len(unlisted_holdings), DEFAULT_WEIGHT, ", ".join(unlisted_holdings))
        for holding, shelf in sorted(self._listed_under.items()):
            if holding in located and located[holding] != shelf:
                logger.warning("holding '%s' is listed under '%s' but its passages are in '%s'", holding, shelf, located[holding])
