"""Passages and the store that holds them.

A passage is one utterance sized for the oracle's output. The passage files are made by
scripts/dev/library/cataloguer.py, one JSON object per line, one file per holding:

    library/passages/<shelf>/<holding>.jsonl

This module reads those files and answers questions about them. It does not decide how likely a
passage is to be drawn (that is the Circulation and the Bibliomancer) and it never writes anything.

Design decisions, and why:
  * Nothing here may stop the oracle. Per the project principle (see pom/__init__.py) a bad line,
    an unreadable file or a mismatched record is skipped or repaired and logged (and
    logged), never raised. One corrupt record must not cost the gallery its library. Only a store
    with no passages at all is CRITICAL, and even then nothing is raised: the store is simply empty
    and says so. `strict=True` raises instead, for tests and dev tools.
  * Where a record disagrees with where it lives, the location wins. The files are generated, so
    a record claiming another shelf or holding is drift; trusting the folder and file name keeps
    the layout that Circulation checks against exactly what is on disk.
  * Passages are immutable. Nothing downstream should be able to edit the library by accident.
  * Loading is eager. The library is about 15 MB of text, which is nothing next to the Pi's 16 GB,
    and doing the work at startup keeps the session itself free of file reads.
  * The log messages name the file and line, because the files are generated and a person has to
    find the bad record.
  * Order is deterministic (sorted shelves, holdings, then file order), so a seeded selector
    gives the same draws on every machine.
  * The folder may hold other files (circulation.json). Only `<shelf>/<holding>.jsonl` are read.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional

from pom.bookkeeper.bookkeeper import Bookkeeper
from pom.bookkeeper.log_recorder import LogRecorder

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module


class CatalogError(ValueError):
    """Raised for any passage problem, but only in strict mode (development and tests)."""


@dataclass(frozen=True, slots=True)
class Passage:
    """One utterance, with where it came from.

    Attributes:
        id: Stable identifier, `<holding>#<index>`.
        holding: The holding it came from (a book or document).
        shelf: The shelf (folder) the holding belongs to.
        text: The utterance. Verse keeps its line breaks.
        words: Word count.
        repeats: How many exact copies existed before duplicates were dropped.
        sha1: Short hash of the text, for noticing changes.
    """
    id: str
    holding: str
    shelf: str
    text: str
    words: int
    repeats: int = 1
    sha1: str = ""


class Catalog:
    """All the passages, indexed by shelf and holding.

    Build one with `Catalog.load` (from disk) or `Catalog.from_passages` (in memory).
    """

    def __init__(self, passages: Iterable[Passage], logger: Optional[LogRecorder] = None,
                 strict: bool = False, bookkeeper: Optional[Bookkeeper] = None):
        """Indexes the passages. Use the `load` / `from_passages` constructors.

        A passage whose id was already seen is skipped and reported. A store with no passages at all
        logs a CRITICAL (and raises CatalogError in strict mode).
        """
        self.logger = logger if logger is not None else _new_logger(strict, bookkeeper)
        self._tree: dict[str, dict[str, list[Passage]]] = {}
        self._by_id: dict[str, Passage] = {}
        for passage in passages:
            if passage.id in self._by_id:
                self.logger.warning("duplicate passage id %s; keeping the first", passage.id)
                continue
            self._by_id[passage.id] = passage
            self._tree.setdefault(passage.shelf, {}).setdefault(passage.holding, []).append(passage)
        if not self._by_id:
            self.logger.critical("no passages loaded: the oracle has nothing to say")

    # --- Construction ---------------------------------------------------------------

    @classmethod
    def from_passages(cls, passages: Iterable[Passage], strict: bool = False, bookkeeper: Optional[Bookkeeper] = None) -> "Catalog":
        """Builds a store from passage objects already in memory (used by tests)."""
        return cls(passages, strict=strict, bookkeeper=bookkeeper)

    @classmethod
    def load(cls, directory, strict: bool = False, bookkeeper: Optional[Bookkeeper] = None) -> "Catalog":
        """Reads every `<shelf>/<holding>.jsonl` under `directory`.

        A missing directory, an unreadable file or a bad record is logged
        and skipped; the rest still loads. With strict=True the first problem raises CatalogError.
        """
        logger = _new_logger(strict, bookkeeper)
        root = Path(directory)
        passages = []
        if not root.is_dir():
            logger.critical("passage directory not found: %s", root)
        else:
            for folder in sorted(p for p in root.iterdir()
                                 if p.is_dir() and not p.name.startswith(".")):
                for path in sorted(folder.glob("*.jsonl")):
                    read = _read_file(path, folder.name, path.stem, logger)
                    logger.debug("read %s/%s: %d passage(s)", folder.name, path.name, len(read))
                    passages.extend(read)
        store = cls(passages, logger, strict)
        logger.info("loaded %d passages from %d holding(s) in %d shelf(s)", len(store),
                    sum(len(store.holdings(r)) for r in store.shelves()), len(store.shelves()))
        return store

    # --- Questions ------------------------------------------------------------------

    def shelves(self) -> list[str]:
        """The shelf names, sorted."""
        return sorted(self._tree)

    def holdings(self, shelf: str) -> list[str]:
        """The holding ids in a shelf, sorted; empty if the shelf is unknown."""
        return sorted(self._tree.get(shelf, {}))

    def layout(self) -> dict[str, list[str]]:
        """Shelf -> its holdings. This is what `Circulation.check` compares its file against."""
        return {shelf: self.holdings(shelf) for shelf in self.shelves()}

    def passages(self, shelf: str, holding: Optional[str] = None) -> tuple[Passage, ...]:
        """The passages of a shelf, or of one holding in it, in file order.

        Returns an empty tuple for an unknown shelf or holding.
        """
        by_holding = self._tree.get(shelf, {})
        if holding is not None:
            return tuple(by_holding.get(holding, ()))
        return tuple(passage for name in sorted(by_holding) for passage in by_holding[name])

    def count(self, shelf: Optional[str] = None, holding: Optional[str] = None) -> int:
        """How many passages: in total, in a shelf, or in one holding of a shelf."""
        if shelf is None:
            return len(self._by_id)
        return len(self.passages(shelf, holding))

    def get(self, passage_id: str) -> Passage:
        """The passage with this id.

        Raises:
            CatalogError: If there is none. This is a programming error (the caller asked for an id
                it never received), not a data problem, so it is not softened.
        """
        try:
            return self._by_id[passage_id]
        except KeyError:
            raise CatalogError(f"no passage with id {passage_id!r}") from None

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[Passage]:
        """Every passage in deterministic order: shelf, holding, then file order."""
        for shelf in self.shelves():
            yield from self.passages(shelf)


def _new_logger(strict: bool, bookkeeper: Optional[Bookkeeper] = None) -> LogRecorder:
    """This module's logger: from the Bookkeeper the entry point built (a plain one when a test or tool gives none); strict mode raises CatalogError."""
    return (bookkeeper or Bookkeeper()).logger_for(__name__, CatalogError, DEBUG, strict)


def _read_file(path: Path, shelf: str, holding: str, logger: LogRecorder) -> list[Passage]:
    """Parses one passage file, repairing or skipping what is wrong and reporting each problem.

    The folder and file name are trusted over what a record claims.
    """
    passages = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        logger.warning("cannot read %s: %s; skipping the file", path, error)
        return passages
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        where = f"{path.name}:{number}"
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            logger.warning("%s: not valid JSON (%s); skipping the record", where, error)
            continue
        if not isinstance(record, dict):
            logger.warning("%s: a passage must be a JSON object; skipping the record", where)
            continue
        text = record.get("text")
        if "text" not in record:
            logger.warning("%s: missing field(s): text; skipping the record", where)
            continue
        if not isinstance(text, str) or not text.strip():
            logger.warning("%s: text must be a non-empty string; skipping the record", where)
            continue
        passage_id = record.get("id")
        if passage_id is None:
            passage_id = f"{holding}#line{number}"
            logger.warning("%s: missing field(s): id; using %s", where, passage_id)
        for field, expected in (("shelf", shelf), ("holding", holding)):
            if field not in record:
                logger.warning("%s: missing field(s): %s; using %r", where, field, expected)
            elif record[field] != expected:
                what = "folder" if field == "shelf" else "file"
                logger.warning("%s: %s %r does not match its %s %r; using %r", where, field, record[field], what, expected, expected)
        words = record.get("words")
        passages.append(Passage(
            id=str(passage_id), holding=holding, shelf=shelf, text=text,
            words=words if isinstance(words, int) and not isinstance(words, bool)
            else len(text.split()),
            repeats=_repeats(record.get("repeats", 1), where, logger),
            sha1=str(record.get("sha1", "")),
        ))
    return passages


def _repeats(value, where: str, logger: LogRecorder) -> int:
    """The repeat count; 1 (with a warning) if it is not a positive whole number."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    logger.warning("%s: repeats must be a whole number of at least 1, got %r; using 1", where, value)
    return 1
