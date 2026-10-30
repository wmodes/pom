"""The scoring module: scoring each fragment, so the trigger can choose the hinge."""
from pom.scoring.convo_interest_tracker import ConvoInterestTracker, ConvoInterestTrackerError, ScoreEffect
from pom.scoring.decaying_quantile_bar import DecayingQuantileBar, DecayingQuantileBarError
from pom.scoring.moving_average_bar import MovingAverageBar, MovingAverageBarError
from pom.scoring.percentile_bar import PercentileBar, PercentileBarError
from pom.scoring.hinge_finder import HingeFinder, ScoredSentence
from pom.scoring.scorer import ScoredBy, ScoredFragment, Scorer, ScorerError
from pom.scoring.tracker_factory import ALGORITHMS, bar_from_settings, tracker_from_settings
from pom.scoring.trigger_bar import TriggerBar
from pom.scoring.scorer_model_client import ScorerModelClient, ScorerModelClientError
from pom.scoring.word_tagger import Word, WordTagger, WordTaggerError

__all__ = ["ALGORITHMS", "DecayingQuantileBar", "DecayingQuantileBarError", "MovingAverageBar", "MovingAverageBarError", "PercentileBar", "PercentileBarError", "TriggerBar", "bar_from_settings", "tracker_from_settings", "HingeFinder", "ScoredSentence", "ConvoInterestTracker", "ConvoInterestTrackerError", "ScoreEffect", "ScoredBy", "ScoredFragment", "Scorer",
           "ScorerError", "ScorerModelClient", "ScorerModelClientError", "Word", "WordTagger", "WordTaggerError"]
