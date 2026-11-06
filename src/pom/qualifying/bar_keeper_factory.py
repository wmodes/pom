"""The BarKeeper factory: builds the BarKeeper the config asks for.

The BarKeeper can decide where its bar sits in more than one way. Each way is a Bar class with a small factory of its own
(`from_settings`); this module is the list of them and the one function the entry point and the testbenches call:

    bar_keeper = bar_keeper_from_settings(config.qualifying)

`settings.algorithm` names the way: "percentile", "decaying_quantile" or "moving_average". Each bar reads only the settings it needs,
so one `[qualifying]` section can hold the settings of all of them and switching is a single line. A new way of deciding is a new class
and one new entry in ALGORITHMS.

Nothing here may stop the oracle: an unknown name is recorded as a warning on the bar and the default (percentile) is used.
This module never reads Config; it reads attributes off the settings object it is given.
"""
from typing import Dict, Type

from pom.qualifying.bar_keeper import BarKeeper
from pom.qualifying.decaying_quantile_bar import DecayingQuantileBar
from pom.qualifying.moving_average_bar import MovingAverageBar
from pom.qualifying.percentile_bar import PercentileBar
from pom.qualifying.bar import Bar

DEFAULT_ALGORITHM = "percentile"
ALGORITHMS: Dict[str, Type[Bar]] = {
    "percentile": PercentileBar,
    "decaying_quantile": DecayingQuantileBar,
    "moving_average": MovingAverageBar,
}


def bar_from_settings(settings, strict: bool = False) -> Bar:
    """The bar the settings name (`settings.algorithm`), built from the settings. An unknown name gives the default with a warning."""
    name = getattr(settings, "algorithm", DEFAULT_ALGORITHM)
    known = name in ALGORITHMS if isinstance(name, str) else False
    bar = ALGORITHMS[name if known else DEFAULT_ALGORITHM].from_settings(settings, strict=strict)
    if not known:
        bar.report.warn(f"the qualifying algorithm must be one of {', '.join(ALGORITHMS)}, got {name!r}; using {DEFAULT_ALGORITHM}")
    return bar


def bar_keeper_from_settings(settings, strict: bool = False) -> BarKeeper:
    """A BarKeeper with the bar the settings name, `settings.cooldown_seconds` and `settings.qualifier_bar_influence`."""
    return BarKeeper(bar_from_settings(settings, strict=strict), settings.cooldown_seconds, settings.qualifier_bar_influence, strict=strict)
