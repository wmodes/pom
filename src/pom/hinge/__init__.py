"""The hinge module: deciding which piece of what the room said is worth answering."""
from pom.hinge.scorer_model_client import ScorerModelClient, ScorerModelClientError
from pom.hinge.scorer import ScoredFragment, Scorer, ScorerError, Source
from pom.hinge.word_tagger import Word, WordTagger, WordTaggerError

__all__ = ["ScorerModelClient", "ScorerModelClientError", "ScoredFragment", "Scorer", "ScorerError",
           "Source", "Word", "WordTagger", "WordTaggerError"]
