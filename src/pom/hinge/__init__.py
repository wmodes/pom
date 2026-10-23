"""The hinge module: deciding which piece of what the room said is worth answering."""
from pom.hinge.interest_client import InterestClient, InterestClientError
from pom.hinge.scorer import ScoredFragment, Scorer, ScorerError, Source
from pom.hinge.words import Word, WordTools, WordToolsError

__all__ = ["InterestClient", "InterestClientError", "ScoredFragment", "Scorer", "ScorerError",
           "Source", "Word", "WordTools", "WordToolsError"]
