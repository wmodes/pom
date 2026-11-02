"""The scoring module: scoring each fragment, so the trigger can choose the hinge."""
from pom.scoring.bar_keeper import BarKeeper, BarKeeperError, Ruling
from pom.scoring.decaying_quantile_bar import DecayingQuantileBar, DecayingQuantileBarError
from pom.scoring.moving_average_bar import MovingAverageBar, MovingAverageBarError
from pom.scoring.percentile_bar import PercentileBar, PercentileBarError
from pom.scoring.hinge_finder import HingeFinder, Attempt
from pom.scoring.clip_scorer import ScoredBy, ScoredClip, ClipScorer, ClipScorerError
from pom.scoring.bar_keeper_factory import ALGORITHMS, bar_from_settings, bar_keeper_from_settings
from pom.scoring.bar import Bar
from pom.scoring.typed_line_scorer import TypedLineScorer, TypedLineScorerError
from pom.scoring.scorer_model_client import ScorerModelClient, ScorerModelClientError
from pom.scoring.word_tagger import TaggedWord, WordTagger, WordTaggerError

__all__ = ["ALGORITHMS", "DecayingQuantileBar", "DecayingQuantileBarError", "MovingAverageBar", "MovingAverageBarError", "PercentileBar", "PercentileBarError", "Bar", "TypedLineScorer", "TypedLineScorerError", "bar_from_settings", "bar_keeper_from_settings", "HingeFinder", "Attempt", "BarKeeper", "BarKeeperError", "Ruling", "ScoredBy", "ScoredClip", "ClipScorer",
           "ClipScorerError", "ScorerModelClient", "ScorerModelClientError", "TaggedWord", "WordTagger", "WordTaggerError"]
