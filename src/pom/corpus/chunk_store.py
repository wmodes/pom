"""Chunks and the store that holds them.

A chunk is one utterance sized for the oracle's output. The chunk files are made by
scripts/dev/corpus/chunk.py, one JSON object per line, one file per source:

    corpus/chunks/<register>/<source>.jsonl

This module reads those files and answers questions about them. It does not decide how likely a
chunk is to be drawn (that is the Weights and the Sampler) and it never writes anything.

Design decisions, and why:
  * Nothing here may stop the oracle. Per the project principle (see pom/__init__.py) a bad line,
    an unreadable file or a mismatched record is skipped or repaired and recorded in a FaultRecorder (and
    logged), never raised. One corrupt record must not cost the gallery its corpus. Only a store
    with no chunks at all is CRITICAL, and even then nothing is raised: the store is simply empty
    and says so. `strict=True` raises instead, for tests and dev tools.
  * Where a record disagrees with where it lives, the location wins. The files are generated, so
    a record claiming another register or source is drift; trusting the folder and file name keeps
    the layout that Weights checks against exactly what is on disk.
  * Chunks are immutable. Nothing downstream should be able to edit the corpus by accident.
  * Loading is eager. The corpus is about 15 MB of text, which is nothing next to the Pi's 16 GB,
    and doing the work at startup keeps the session itself free of file reads.
  * FaultRecorder messages name the file and line, because the files are generated and a person has to
    find the bad record.
  * Order is deterministic (sorted registers, sources, then file order), so a seeded sampler
    gives the same draws on every machine.
  * The folder may hold other files (weights.json). Only `<register>/<source>.jsonl` are read.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional

from pom.log_setup import get_logger
from pom.fault_recorder import FaultRecorder

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)


class ChunkError(ValueError):
    """Raised for any chunk problem, but only in strict mode (development and tests)."""


@dataclass(frozen=True, slots=True)
class Chunk:
    """One utterance, with where it came from.

    Attributes:
        id: Stable identifier, `<source>#<index>`.
        source: The source it came from (a book or document).
        register: The register (folder) the source belongs to.
        text: The utterance. Verse keeps its line breaks.
        words: Word count.
        repeats: How many exact copies existed before duplicates were dropped.
        sha1: Short hash of the text, for noticing changes.
    """
    id: str
    source: str
    register: str
    text: str
    words: int
    repeats: int = 1
    sha1: str = ""


class ChunkStore:
    """All the chunks, indexed by register and source.

    Build one with `ChunkStore.load` (from disk) or `ChunkStore.from_chunks` (in memory).
    """

    def __init__(self, chunks: Iterable[Chunk], report: Optional[FaultRecorder] = None,
                 strict: bool = False):
        """Indexes the chunks. Use the `load` / `from_chunks` constructors.

        A chunk whose id was already seen is skipped and reported. A store with no chunks at all
        records a CRITICAL fault (and raises ChunkError in strict mode).
        """
        self.report = report if report is not None else _new_report(strict)
        self._tree: dict[str, dict[str, list[Chunk]]] = {}
        self._by_id: dict[str, Chunk] = {}
        for chunk in chunks:
            if chunk.id in self._by_id:
                self.report.warn(f"duplicate chunk id {chunk.id}; keeping the first")
                continue
            self._by_id[chunk.id] = chunk
            self._tree.setdefault(chunk.register, {}).setdefault(chunk.source, []).append(chunk)
        if not self._by_id:
            self.report.critical("no chunks loaded: the oracle has nothing to say")

    # --- Construction ---------------------------------------------------------------

    @classmethod
    def from_chunks(cls, chunks: Iterable[Chunk], strict: bool = False) -> "ChunkStore":
        """Builds a store from chunk objects already in memory (used by tests)."""
        return cls(chunks, strict=strict)

    @classmethod
    def load(cls, directory, strict: bool = False) -> "ChunkStore":
        """Reads every `<register>/<source>.jsonl` under `directory`.

        A missing directory, an unreadable file or a bad record is recorded in `store.report`
        and skipped; the rest still loads. With strict=True the first problem raises ChunkError.
        """
        report = _new_report(strict)
        root = Path(directory)
        chunks = []
        if not root.is_dir():
            report.critical(f"chunk directory not found: {root}")
        else:
            for folder in sorted(p for p in root.iterdir()
                                 if p.is_dir() and not p.name.startswith(".")):
                for path in sorted(folder.glob("*.jsonl")):
                    read = _read_file(path, folder.name, path.stem, report)
                    _log.debug("read %s/%s: %d chunk(s)", folder.name, path.name, len(read))
                    chunks.extend(read)
        store = cls(chunks, report, strict)
        _log.info("loaded %d chunks from %d source(s) in %d register(s)", len(store),
                  sum(len(store.sources(r)) for r in store.registers()), len(store.registers()))
        return store

    # --- Questions ------------------------------------------------------------------

    def registers(self) -> list[str]:
        """The register names, sorted."""
        return sorted(self._tree)

    def sources(self, register: str) -> list[str]:
        """The source ids in a register, sorted; empty if the register is unknown."""
        return sorted(self._tree.get(register, {}))

    def layout(self) -> dict[str, list[str]]:
        """Register -> its sources. This is what `Weights.check` compares its file against."""
        return {register: self.sources(register) for register in self.registers()}

    def chunks(self, register: str, source: Optional[str] = None) -> tuple[Chunk, ...]:
        """The chunks of a register, or of one source in it, in file order.

        Returns an empty tuple for an unknown register or source.
        """
        by_source = self._tree.get(register, {})
        if source is not None:
            return tuple(by_source.get(source, ()))
        return tuple(chunk for name in sorted(by_source) for chunk in by_source[name])

    def count(self, register: Optional[str] = None, source: Optional[str] = None) -> int:
        """How many chunks: in total, in a register, or in one source of a register."""
        if register is None:
            return len(self._by_id)
        return len(self.chunks(register, source))

    def get(self, chunk_id: str) -> Chunk:
        """The chunk with this id.

        Raises:
            ChunkError: If there is none. This is a programming error (the caller asked for an id
                it never received), not a data problem, so it is not softened.
        """
        try:
            return self._by_id[chunk_id]
        except KeyError:
            raise ChunkError(f"no chunk with id {chunk_id!r}") from None

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[Chunk]:
        """Every chunk in deterministic order: register, source, then file order."""
        for register in self.registers():
            yield from self.chunks(register)


def _new_report(strict: bool) -> FaultRecorder:
    """A FaultRecorder that raises ChunkError in strict mode; otherwise it collects and logs."""
    return FaultRecorder(strict=strict, error=ChunkError, logger=_log)


def _read_file(path: Path, register: str, source: str, report: FaultRecorder) -> list[Chunk]:
    """Parses one chunk file, repairing or skipping what is wrong and reporting each problem.

    The folder and file name are trusted over what a record claims.
    """
    chunks = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        report.warn(f"cannot read {path}: {error}; skipping the file")
        return chunks
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        where = f"{path.name}:{number}"
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            report.warn(f"{where}: not valid JSON ({error}); skipping the record")
            continue
        if not isinstance(record, dict):
            report.warn(f"{where}: a chunk must be a JSON object; skipping the record")
            continue
        text = record.get("text")
        if "text" not in record:
            report.warn(f"{where}: missing field(s): text; skipping the record")
            continue
        if not isinstance(text, str) or not text.strip():
            report.warn(f"{where}: text must be a non-empty string; skipping the record")
            continue
        chunk_id = record.get("id")
        if chunk_id is None:
            chunk_id = f"{source}#line{number}"
            report.warn(f"{where}: missing field(s): id; using {chunk_id}")
        for field, expected in (("register", register), ("source", source)):
            if field not in record:
                report.warn(f"{where}: missing field(s): {field}; using {expected!r}")
            elif record[field] != expected:
                what = "folder" if field == "register" else "file"
                report.warn(f"{where}: {field} {record[field]!r} does not match its {what} "
                            f"{expected!r}; using {expected!r}")
        words = record.get("words")
        chunks.append(Chunk(
            id=str(chunk_id), source=source, register=register, text=text,
            words=words if isinstance(words, int) and not isinstance(words, bool)
            else len(text.split()),
            repeats=_repeats(record.get("repeats", 1), where, report),
            sha1=str(record.get("sha1", "")),
        ))
    return chunks


def _repeats(value, where: str, report: FaultRecorder) -> int:
    """The repeat count; 1 (with a warning) if it is not a positive whole number."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value
    report.warn(f"{where}: repeats must be a whole number of at least 1, got {value!r}; using 1")
    return 1
