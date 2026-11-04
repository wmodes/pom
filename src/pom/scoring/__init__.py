"""The scoring module: scoring each clip, so the BarKeeper can choose the hinge."""
from pom.scoring.clip_scorer import ClipScorer, ClipScorerError, ScoredBy, ScoredClip
from pom.scoring.scorer_model_client import ScorerModelClient, ScorerModelClientError
from pom.scoring.scorer_warmer import ScorerWarmer
from pom.scoring.word_tagger import TaggedWord, WordTagger, WordTaggerError

__all__ = ["ClipScorer", "ClipScorerError", "ScoredBy", "ScoredClip", "ScorerModelClient", "ScorerModelClientError", "ScorerWarmer",
           "TaggedWord", "WordTagger", "WordTaggerError"]
