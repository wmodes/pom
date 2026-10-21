"""The corpus module: what the oracle may say, and how likely each piece is to be drawn."""
from pom.corpus.weights import Weights, WeightsError, WeightsReport

__all__ = ["Weights", "WeightsError", "WeightsReport"]
