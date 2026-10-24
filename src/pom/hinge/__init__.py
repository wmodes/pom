"""The hinge module: deciding which piece of what the room said is worth responding to."""
from pom.hinge.scorer import ScoredBy, ScoredFragment, Scorer, ScorerError
from pom.hinge.scorer_model_client import ScorerModelClient, ScorerModelClientError
from pom.hinge.word_tagger import Word, WordTagger, WordTaggerError

__all__ = ["ScoredBy", "ScoredFragment", "Scorer", "ScorerError", "ScorerModelClient",
           "ScorerModelClientError", "Word", "WordTagger", "WordTaggerError"]
