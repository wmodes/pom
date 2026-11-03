"""The Bibliomancer: draws passages at random, weighted so each shelf gets its target share over time.

The oracle shows the model a few random exemplar passages. The library is lopsided (one shelf has
30,000 passages, another 400), so a plain random pick would swamp the small shelves. The Bibliomancer
gives every passage its own chance of being drawn, so that over many draws each shelf gets its
target share, while any combination of draws stays possible: five obituaries in a row is rare,
never impossible. Nothing is chosen category-first.

    chance(passage) = shelf_share * holding_weight / shelf_total

where shelf_share is the shelf's normalized share (from Circulation), holding_weight is the
passage's holding weight, and shelf_total is the sum of holding_weight over the shelf's passages.
Drawing a shelf by its share and then a passage within it by holding weight, on every draw, gives
exactly the same odds; this class does it with one cumulative table instead.

These odds are computed from the library as it stands, once, when the Bibliomancer is built. They are
never stored: a passage's chance depends on its shelf's total, so adding a passage changes only
its own shelf's numbers, and rebuilding takes a few milliseconds.

KNOWN LIMIT: NO MEMORY ACROSS PULLS. A pull never repeats a passage, but separate pulls are
independent, so a passage can come back. Balancing the shelves makes small ones repeat more: each
of the 400 horoscope passages is about 14 times likelier than in a flat draw. Measured in a
simulation of this library (3 to 5 passages per pull, no decay), the share of drawn passages already
seen earlier in the same run was:

    one evening (16 pulls)        0.1%        two months (1,000 pulls)    9% (horoscope 30%)
    a week of evenings (100)      1%          a year (5,000 pulls)        31% (horoscope 75%)

That is fine for a gallery session, so no recency decay was added. If an installation ever runs
for months without a restart, the fix is to make a passage less likely right after it is drawn and
let that fade (propose by base odds, accept with a multiplier, count time in draws); it was
prototyped, with a floor and a half-life taken from Config, and found workable.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A shelf whose holdings all weigh 0
    is skipped with a warning and the others renormalize. If nothing at all can be drawn that is
    CRITICAL: `draw` returns an empty list and the Conductor decides what to do. Asking for more
    passages than exist returns fewer. `strict=True` raises BibliomancerError instead, for tests and dev
    tools.
  * One pull never repeats a passage. Drawing without repeats keeps the odds proportional to the
    weights among the passages still available.
  * How many passages a pull holds is a range, (3, 5) by default, passed in by whoever builds the
    Bibliomancer (the entry point takes it from Config; this module never reads Config itself).
    `draw()` with no argument picks a count in that range with the same random generator, so a
    seeded run is repeatable; `draw(n)` overrides it. A bad range falls back to (3, 5) with a
    warning, and a reversed one is swapped.
  * The random generator is injected, so a test (or a debugging session) can seed it and get the
    same draws every time.
  * The Bibliomancer counts draws per shelf. That is how you notice a shelf coming up too often
    and decide to adjust a share; the counts hold no text, so they are safe to log.
  * Passages with zero chance (a zero holding weight, or a zero-share shelf) are left out of the
    table entirely, so they can never be drawn.
"""
import bisect
import random
from typing import Optional, Sequence

from pom.library.catalog import Passage, Catalog
from pom.library.circulation import Circulation
from pom.log_setup import get_logger
from pom.fault_recorder import FaultRecorder

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_EXEMPLAR_RANGE = (3, 5)


class BibliomancerError(ValueError):
    """Raised for any selector problem, but only in strict mode (development and tests)."""


class Bibliomancer:
    """Draws weighted random passages from a Catalog.

    Attributes:
        report: Problems found while building the odds (an empty library, a shelf with nothing
            drawable). `check_report` holds what `Circulation.check` found about the layout.
    """

    def __init__(self, store: Catalog, circulation: Circulation,
                 rng: Optional[random.Random] = None, strict: bool = False,
                 exemplar_range: Sequence[int] = DEFAULT_EXEMPLAR_RANGE):
        """Builds the draw odds from the store and the weights.

        Args:
            store: The passages to draw from.
            circulation: The draw weights (shelf shares and holding weights).
            rng: The random generator; a fresh `random.Random()` if not given. Pass a seeded one
                for repeatable draws.
            strict: Raise BibliomancerError on any problem instead of recording it.
            exemplar_range: The (fewest, most) passages `draw()` returns when not told how many.
        """
        self._rng = rng if rng is not None else random.Random()
        self.report = FaultRecorder(strict=strict, error=BibliomancerError, logger=_log)
        self._exemplar_range = self._valid_exemplar_range(exemplar_range)
        self.check_report = circulation.check(store.layout())   # logs any drift; the odds still work
        self._passages: list[Passage] = []
        self._probability: list[float] = []
        self._cumulative: list[float] = []
        self._shares: dict[str, float] = {}
        self._counts: dict[str, int] = {}
        self._build(store, circulation)

    def _valid_exemplar_range(self, exemplar_range) -> tuple[int, int]:
        """A usable (fewest, most) pair: whole numbers of at least 1, in order.

        Anything else is reported and replaced by the default; a reversed pair is swapped.
        """
        def whole(value):
            return isinstance(value, int) and not isinstance(value, bool) and value >= 1
        try:
            low, high = exemplar_range
        except (TypeError, ValueError):
            low = high = None
        if not (whole(low) and whole(high)):
            self.report.warn(f"exemplar_range must be two whole numbers of at least 1, "
                             f"got {exemplar_range!r}; using {DEFAULT_EXEMPLAR_RANGE}")
            return DEFAULT_EXEMPLAR_RANGE
        if low > high:
            self.report.warn(f"exemplar_range ({low}, {high}) is backwards; using ({high}, {low})")
            low, high = high, low
        return (low, high)

    # --- Building the odds ----------------------------------------------------------

    def _build(self, store: Catalog, circulation: Circulation) -> None:
        """Computes every passage's chance of being drawn and the cumulative table."""
        if len(store) == 0:
            self.report.critical("nothing to draw from: the store has no passages")
            return
        totals = {}      # shelf -> sum of holding weights over its passages
        for shelf in store.shelves():
            totals[shelf] = sum(circulation.holding_weight(c.holding) for c in store.passages(shelf))
        drawable = []
        for shelf in store.shelves():
            if totals[shelf] > 0:
                drawable.append(shelf)
            else:
                self.report.warn(f"shelf '{shelf}' has no drawable passages (every holding "
                                 f"weight is 0); skipping it")
        if not drawable:
            self.report.critical("nothing to draw from: no passage has a positive weight")
            return
        self._shares = circulation.shares_for(drawable)
        running = 0.0
        for shelf in drawable:
            for passage in store.passages(shelf):
                chance = (self._shares[shelf] * circulation.holding_weight(passage.holding)
                          / totals[shelf])
                if chance <= 0:
                    continue          # a zero-share shelf or a zero-weight holding
                self._passages.append(passage)
                self._probability.append(chance)
                running += chance
                self._cumulative.append(running)
        self._counts = {shelf: 0 for shelf in drawable}
        if not self._passages:
            self.report.critical("nothing to draw from: no passage has a positive chance")
            return
        _log.info("selector ready: %d drawable passage(s) in %d shelf(s)",
                  len(self._passages), len(drawable))
        _log.debug("shelf shares: %s",
                   ", ".join(f"{r}={s:.3f}" for r, s in sorted(self._shares.items())))

    # --- Questions ------------------------------------------------------------------

    @property
    def exemplar_range(self) -> tuple[int, int]:
        """The (fewest, most) passages a no-argument `draw()` returns."""
        return self._exemplar_range

    @property
    def ready(self) -> bool:
        """True if there is anything to draw."""
        return bool(self._passages)

    def shelf_shares(self) -> dict[str, float]:
        """Each drawable shelf's normalized share of draws (they sum to 1)."""
        return dict(self._shares)

    def probability(self, passage: Passage) -> float:
        """The chance that one draw returns this passage; 0.0 if it can never be drawn."""
        try:
            return self._probability[self._index_of(passage)]
        except ValueError:
            return 0.0

    def _index_of(self, passage: Passage) -> int:
        """Position of a passage in the table (linear; for questions, not for drawing)."""
        for index, candidate in enumerate(self._passages):
            if candidate.id == passage.id:
                return index
        raise ValueError(passage.id)

    def draw_counts(self) -> dict[str, int]:
        """How many passages each shelf has supplied since the last reset (counts only)."""
        return dict(self._counts)

    def reset_counts(self) -> None:
        """Sets every shelf's draw count back to zero."""
        self._counts = {shelf: 0 for shelf in self._counts}

    # --- Drawing --------------------------------------------------------------------

    def draw(self, count: Optional[int] = None) -> list[Passage]:
        """Draws passages at random by weight, never repeating one within the pull.

        Args:
            count: How many passages. None (the default) picks a number in `exemplar_range`, which
                is what the oracle normally does. Zero or less gives an empty list.

        Returns:
            The passages, in the order drawn. Fewer than `count` if that many distinct drawable
            passages do not exist; empty if there is nothing to draw (see `report`).
        """
        if count is None:
            count = self._rng.randint(*self._exemplar_range)
        wanted = min(max(int(count), 0), len(self._passages))
        if wanted == 0:
            return []
        picked: list[int] = []
        seen: set[int] = set()
        attempts, limit = 0, max(50, 20 * wanted)
        while len(picked) < wanted and attempts < limit:
            index = self._pick()
            attempts += 1
            if index not in seen:
                seen.add(index)
                picked.append(index)
        if len(picked) < wanted:             # skewed odds made repeats too common: finish exactly
            self._finish_exactly(picked, seen, wanted)
        passages = [self._passages[i] for i in picked]
        for passage in passages:
            self._counts[passage.shelf] = self._counts.get(passage.shelf, 0) + 1
        _log.debug("drew %d passage(s): %s", len(passages), ", ".join(c.id for c in passages))
        return passages

    def _pick(self) -> int:
        """One weighted pick: a point in [0, total) located in the cumulative table."""
        point = self._rng.random() * self._cumulative[-1]
        return min(bisect.bisect_right(self._cumulative, point), len(self._passages) - 1)

    def _finish_exactly(self, picked: list[int], seen: set[int], wanted: int) -> None:
        """Completes a pull among the passages not yet picked, still in proportion to their odds."""
        rest = [i for i in range(len(self._passages)) if i not in seen]
        while rest and len(picked) < wanted:
            choice = self._rng.choices(rest, weights=[self._probability[i] for i in rest])[0]
            rest.remove(choice)
            seen.add(choice)
            picked.append(choice)
