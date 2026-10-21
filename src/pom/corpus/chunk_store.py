"""Chunks and the store that holds them.

A chunk is one utterance sized for the oracle's output. The chunk files are made by
scripts/dev/corpus/chunk.py, one JSON object per line, one file per source:

    corpus/chunks/<register>/<source>.jsonl

This module reads those files and answers questions about them. It does not decide how likely a
chunk is to be drawn (that is the Weights and the Sampler) and it never writes anything.

Design decisions, and why:
  * Chunks are immutable. Nothing downstream should be able to edit the corpus by accident.
  * Loading is eager and strict. The corpus is about 15 MB of text, which is nothing next to the
    Pi's 16 GB, and failing at startup beats failing mid-session. A record must agree with where
    it lives: its `register` must match its folder and its `source` its file name, so the layout
    that Weights checks against is exactly what the chunks claim.
  * Errors name the file and line, because the files are generated and a person has to find the
    bad record.
  * Order is deterministic (sorted registers, sources, then file order), so a seeded sampler
    gives the same draws on every machine.
  * The folder may hold other files (weights.json). Only `<register>/<source>.jsonl` are read.
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional


class ChunkError(ValueError):
    """A chunk file is missing, malformed, or inconsistent with where it lives."""


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

    def __init__(self, chunks: Iterable[Chunk]):
        """Indexes the chunks. Use the `load` / `from_chunks` constructors.

        Raises:
            ChunkError: If two chunks share an id.
        """
        self._tree: dict[str, dict[str, list[Chunk]]] = {}
        self._by_id: dict[str, Chunk] = {}
        for chunk in chunks:
            if chunk.id in self._by_id:
                raise ChunkError(f"duplicate chunk id: {chunk.id}")
            self._by_id[chunk.id] = chunk
            self._tree.setdefault(chunk.register, {}).setdefault(chunk.source, []).append(chunk)

    # --- Construction ---------------------------------------------------------------

    @classmethod
    def from_chunks(cls, chunks: Iterable[Chunk]) -> "ChunkStore":
        """Builds a store from chunk objects already in memory (used by tests)."""
        return cls(chunks)

    @classmethod
    def load(cls, directory) -> "ChunkStore":
        """Reads every `<register>/<source>.jsonl` under `directory`.

        Raises:
            ChunkError: If the directory is missing, or any file or record is bad.
        """
        root = Path(directory)
        if not root.is_dir():
            raise ChunkError(f"chunk directory not found: {root}")
        chunks = []
        for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
            for path in sorted(folder.glob("*.jsonl")):
                chunks.extend(_read_file(path, register=folder.name, source=path.stem))
        return cls(chunks)

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
            ChunkError: If there is none.
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


_REQUIRED = ("id", "source", "register", "text")


def _read_file(path: Path, register: str, source: str) -> list[Chunk]:
    """Parses one chunk file, checking every record against its folder and file name."""
    chunks = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ChunkError(f"cannot read {path}: {error}") from error
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        where = f"{path.name}:{number}"
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ChunkError(f"{where}: not valid JSON ({error})") from error
        if not isinstance(record, dict):
            raise ChunkError(f"{where}: a chunk must be a JSON object")
        missing = [key for key in _REQUIRED if key not in record]
        if missing:
            raise ChunkError(f"{where}: missing field(s): {', '.join(missing)}")
        if not isinstance(record["text"], str) or not record["text"].strip():
            raise ChunkError(f"{where}: text must be a non-empty string")
        if record["register"] != register:
            raise ChunkError(f"{where}: register {record['register']!r} does not match its folder "
                             f"{register!r}")
        if record["source"] != source:
            raise ChunkError(f"{where}: source {record['source']!r} does not match its file "
                             f"{source!r}")
        words = record.get("words")
        chunks.append(Chunk(
            id=str(record["id"]), source=source, register=register, text=record["text"],
            words=words if isinstance(words, int) and not isinstance(words, bool)
            else len(record["text"].split()),
            repeats=int(record.get("repeats", 1)), sha1=str(record.get("sha1", "")),
        ))
    return chunks
