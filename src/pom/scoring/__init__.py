"""The scoring module: scoring each fragment, so the trigger can choose the hinge."""
from pom.scoring.scorer import ScoredBy, ScoredFragment, Scorer, ScorerError
from pom.scoring.scorer_model_client import ScorerModelClient, ScorerModelClientError
from pom.scoring.word_tagger import Word, WordTagger, WordTaggerError

__all__ = ["ScoredBy", "ScoredFragment", "Scorer", "ScorerError", "ScorerModelClient",
           "ScorerModelClientError", "Word", "WordTagger", "WordTaggerError"]
