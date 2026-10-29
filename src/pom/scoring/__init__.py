"""The scoring module: scoring each fragment, so the trigger can choose the hinge."""
from pom.scoring.convo_interest_tracker import ConvoInterestTracker, ConvoInterestTrackerError, ScoreEffect
from pom.scoring.hinge_finder import HingeFinder, ScoredSentence
from pom.scoring.scorer import ScoredBy, ScoredFragment, Scorer, ScorerError
from pom.scoring.scorer_model_client import ScorerModelClient, ScorerModelClientError
from pom.scoring.word_tagger import Word, WordTagger, WordTaggerError

__all__ = ["HingeFinder", "ScoredSentence", "ConvoInterestTracker", "ConvoInterestTrackerError", "ScoreEffect", "ScoredBy", "ScoredFragment", "Scorer",
           "ScorerError", "ScorerModelClient", "ScorerModelClientError", "Word", "WordTagger", "WordTaggerError"]
