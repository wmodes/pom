"""The tracker factory: builds the ConvoInterestTracker the config asks for.

The trigger can decide where its bar sits in more than one way. Each way is a TriggerBar class with a small factory of its own
(`from_settings`); this module is the list of them and the one function the entry point and the playgrounds call:

    tracker = tracker_from_settings(config.trigger)

`settings.algorithm` names the way: "percentile", "decaying_quantile" or "moving_average". Each bar reads only the settings it needs,
so one `[trigger]` section can hold the settings of all of them and switching is a single line. A new way of deciding is a new class
and one new entry in ALGORITHMS.

Nothing here may stop the oracle: an unknown name is recorded as a warning on the bar and the default (percentile) is used.
This module never reads Config; it reads attributes off the settings object it is given.
"""
from typing import Dict, Type

from pom.scoring.convo_interest_tracker import ConvoInterestTracker
from pom.scoring.decaying_quantile_bar import DecayingQuantileBar
from pom.scoring.moving_average_bar import MovingAverageBar
from pom.scoring.percentile_bar import PercentileBar
from pom.scoring.trigger_bar import TriggerBar

DEFAULT_ALGORITHM = "percentile"
ALGORITHMS: Dict[str, Type[TriggerBar]] = {
    "percentile": PercentileBar,
    "decaying_quantile": DecayingQuantileBar,
    "moving_average": MovingAverageBar,
}


def bar_from_settings(settings, strict: bool = False) -> TriggerBar:
    """The bar the settings name (`settings.algorithm`), built from the settings. An unknown name gives the default with a warning."""
    name = getattr(settings, "algorithm", DEFAULT_ALGORITHM)
    known = name in ALGORITHMS if isinstance(name, str) else False
    bar = ALGORITHMS[name if known else DEFAULT_ALGORITHM].from_settings(settings, strict=strict)
    if not known:
        bar.report.warn(f"the trigger algorithm must be one of {', '.join(ALGORITHMS)}, got {name!r}; using {DEFAULT_ALGORITHM}")
    return bar


def tracker_from_settings(settings, strict: bool = False) -> ConvoInterestTracker:
    """A ConvoInterestTracker with the bar the settings name and `settings.cooldown_seconds`."""
    return ConvoInterestTracker(bar_from_settings(settings, strict=strict), settings.cooldown_seconds, strict=strict)
