"""The corpus module: what the oracle may say, and how likely each piece is to be drawn."""
from pom.corpus.chunk_store import Chunk, ChunkError, ChunkStore
from pom.corpus.weights import Weights, WeightsError, WeightsReport

__all__ = ["Chunk", "ChunkError", "ChunkStore", "Weights", "WeightsError", "WeightsReport"]
