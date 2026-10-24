"""The Sampler: draws chunks at random, weighted so each register gets its target share over time.

The oracle shows the model a few random exemplar chunks. The corpus is lopsided (one register has
30,000 chunks, another 400), so a plain random pick would swamp the small registers. The Sampler
gives every chunk its own chance of being drawn, so that over many draws each register gets its
target share, while any combination of draws stays possible: five obituaries in a row is rare,
never impossible. Nothing is chosen category-first.

    chance(chunk) = register_share * source_weight / register_total

where register_share is the register's normalized share (from DrawWeights), source_weight is the
chunk's source weight, and register_total is the sum of source_weight over the register's chunks.
Drawing a register by its share and then a chunk within it by source weight, on every draw, gives
exactly the same odds; this class does it with one cumulative table instead.

These odds are computed from the corpus as it stands, once, when the Sampler is built. They are
never stored: a chunk's chance depends on its register's total, so adding a chunk changes only
its own register's numbers, and rebuilding takes a few milliseconds.

KNOWN LIMIT: NO MEMORY ACROSS PULLS. A pull never repeats a chunk, but separate pulls are
independent, so a chunk can come back. Balancing the registers makes small ones repeat more: each
of the 400 horoscope chunks is about 14 times likelier than in a flat draw. Measured in a
simulation of this corpus (3 to 5 chunks per pull, no decay), the share of drawn chunks already
seen earlier in the same run was:

    one evening (16 pulls)        0.1%        two months (1,000 pulls)    9% (horoscope 30%)
    a week of evenings (100)      1%          a year (5,000 pulls)        31% (horoscope 75%)

That is fine for a gallery session, so no recency decay was added. If an installation ever runs
for months without a restart, the fix is to make a chunk less likely right after it is drawn and
let that fade (propose by base odds, accept with a multiplier, count time in draws); it was
prototyped, with a floor and a half-life taken from Config, and found workable.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A register whose sources all weigh 0
    is skipped with a warning and the others renormalize. If nothing at all can be drawn that is
    CRITICAL: `draw` returns an empty list and the Conductor decides what to do. Asking for more
    chunks than exist returns fewer. `strict=True` raises SamplerError instead, for tests and dev
    tools.
  * One pull never repeats a chunk. Drawing without repeats keeps the odds proportional to the
    weights among the chunks still available.
  * How many chunks a pull holds is a range, (3, 5) by default, passed in by whoever builds the
    Sampler (the entry point takes it from Config; this module never reads Config itself).
    `draw()` with no argument picks a count in that range with the same random generator, so a
    seeded run is repeatable; `draw(n)` overrides it. A bad range falls back to (3, 5) with a
    warning, and a reversed one is swapped.
  * The random generator is injected, so a test (or a debugging session) can seed it and get the
    same draws every time.
  * The Sampler counts draws per register. That is how you notice a register coming up too often
    and decide to adjust a share; the counts hold no text, so they are safe to log.
  * Chunks with zero chance (a zero source weight, or a zero-share register) are left out of the
    table entirely, so they can never be drawn.
"""
import bisect
import random
from typing import Optional, Sequence

from pom.corpus.chunk_store import Chunk, ChunkStore
from pom.corpus.draw_weights import DrawWeights
from pom.log_setup import get_logger
from pom.fault_recorder import FaultRecorder

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_SAMPLES = (3, 5)


class SamplerError(ValueError):
    """Raised for any sampler problem, but only in strict mode (development and tests)."""


class Sampler:
    """Draws weighted random chunks from a ChunkStore.

    Attributes:
        report: Problems found while building the odds (an empty corpus, a register with nothing
            drawable). `check_report` holds what `DrawWeights.check` found about the layout.
    """

    def __init__(self, store: ChunkStore, draw_weights: DrawWeights,
                 rng: Optional[random.Random] = None, strict: bool = False,
                 samples: Sequence[int] = DEFAULT_SAMPLES):
        """Builds the draw odds from the store and the weights.

        Args:
            store: The chunks to draw from.
            draw_weights: Register shares and source weights.
            rng: The random generator; a fresh `random.Random()` if not given. Pass a seeded one
                for repeatable draws.
            strict: Raise SamplerError on any problem instead of recording it.
            samples: The (fewest, most) chunks `draw()` returns when not told how many.
        """
        self._rng = rng if rng is not None else random.Random()
        self.report = FaultRecorder(strict=strict, error=SamplerError, logger=_log)
        self._samples = self._valid_samples(samples)
        self.check_report = draw_weights.check(store.layout())   # logs any drift; the odds still work
        self._chunks: list[Chunk] = []
        self._probability: list[float] = []
        self._cumulative: list[float] = []
        self._shares: dict[str, float] = {}
        self._counts: dict[str, int] = {}
        self._build(store, draw_weights)

    def _valid_samples(self, samples) -> tuple[int, int]:
        """A usable (fewest, most) pair: whole numbers of at least 1, in order.

        Anything else is reported and replaced by the default; a reversed pair is swapped.
        """
        def whole(value):
            return isinstance(value, int) and not isinstance(value, bool) and value >= 1
        try:
            low, high = samples
        except (TypeError, ValueError):
            low = high = None
        if not (whole(low) and whole(high)):
            self.report.warn(f"samples must be two whole numbers of at least 1, got {samples!r}; "
                             f"using {DEFAULT_SAMPLES}")
            return DEFAULT_SAMPLES
        if low > high:
            self.report.warn(f"samples ({low}, {high}) is backwards; using ({high}, {low})")
            low, high = high, low
        return (low, high)

    # --- Building the odds ----------------------------------------------------------

    def _build(self, store: ChunkStore, draw_weights: DrawWeights) -> None:
        """Computes every chunk's chance of being drawn and the cumulative table."""
        if len(store) == 0:
            self.report.critical("nothing to draw from: the store has no chunks")
            return
        totals = {}      # register -> sum of source weights over its chunks
        for register in store.registers():
            totals[register] = sum(draw_weights.source_weight(c.source) for c in store.chunks(register))
        drawable = []
        for register in store.registers():
            if totals[register] > 0:
                drawable.append(register)
            else:
                self.report.warn(f"register '{register}' has no drawable chunks (every source "
                                 f"weight is 0); skipping it")
        if not drawable:
            self.report.critical("nothing to draw from: no chunk has a positive weight")
            return
        self._shares = draw_weights.shares_for(drawable)
        running = 0.0
        for register in drawable:
            for chunk in store.chunks(register):
                chance = (self._shares[register] * draw_weights.source_weight(chunk.source)
                          / totals[register])
                if chance <= 0:
                    continue          # a zero-share register or a zero-weight source
                self._chunks.append(chunk)
                self._probability.append(chance)
                running += chance
                self._cumulative.append(running)
        self._counts = {register: 0 for register in drawable}
        if not self._chunks:
            self.report.critical("nothing to draw from: no chunk has a positive chance")
            return
        _log.info("sampler ready: %d drawable chunk(s) in %d register(s)",
                  len(self._chunks), len(drawable))
        _log.debug("register shares: %s",
                   ", ".join(f"{r}={s:.3f}" for r, s in sorted(self._shares.items())))

    # --- Questions ------------------------------------------------------------------

    @property
    def sample_range(self) -> tuple[int, int]:
        """The (fewest, most) chunks a no-argument `draw()` returns."""
        return self._samples

    @property
    def ready(self) -> bool:
        """True if there is anything to draw."""
        return bool(self._chunks)

    def register_shares(self) -> dict[str, float]:
        """Each drawable register's normalized share of draws (they sum to 1)."""
        return dict(self._shares)

    def probability(self, chunk: Chunk) -> float:
        """The chance that one draw returns this chunk; 0.0 if it can never be drawn."""
        try:
            return self._probability[self._index_of(chunk)]
        except ValueError:
            return 0.0

    def _index_of(self, chunk: Chunk) -> int:
        """Position of a chunk in the table (linear; for questions, not for drawing)."""
        for index, candidate in enumerate(self._chunks):
            if candidate.id == chunk.id:
                return index
        raise ValueError(chunk.id)

    def draw_counts(self) -> dict[str, int]:
        """How many chunks each register has supplied since the last reset (counts only)."""
        return dict(self._counts)

    def reset_counts(self) -> None:
        """Sets every register's draw count back to zero."""
        self._counts = {register: 0 for register in self._counts}

    # --- Drawing --------------------------------------------------------------------

    def draw(self, count: Optional[int] = None) -> list[Chunk]:
        """Draws chunks at random by weight, never repeating one within the pull.

        Args:
            count: How many chunks. None (the default) picks a number in `sample_range`, which
                is what the oracle normally does. Zero or less gives an empty list.

        Returns:
            The chunks, in the order drawn. Fewer than `count` if that many distinct drawable
            chunks do not exist; empty if there is nothing to draw (see `report`).
        """
        if count is None:
            count = self._rng.randint(*self._samples)
        wanted = min(max(int(count), 0), len(self._chunks))
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
        chunks = [self._chunks[i] for i in picked]
        for chunk in chunks:
            self._counts[chunk.register] = self._counts.get(chunk.register, 0) + 1
        _log.debug("drew %d chunk(s): %s", len(chunks), ", ".join(c.id for c in chunks))
        return chunks

    def _pick(self) -> int:
        """One weighted pick: a point in [0, total) located in the cumulative table."""
        point = self._rng.random() * self._cumulative[-1]
        return min(bisect.bisect_right(self._cumulative, point), len(self._chunks) - 1)

    def _finish_exactly(self, picked: list[int], seen: set[int], wanted: int) -> None:
        """Completes a pull among the chunks not yet picked, still in proportion to their odds."""
        rest = [i for i in range(len(self._chunks)) if i not in seen]
        while rest and len(picked) < wanted:
            choice = self._rng.choices(rest, weights=[self._probability[i] for i in rest])[0]
            rest.remove(choice)
            seen.add(choice)
            picked.append(choice)
