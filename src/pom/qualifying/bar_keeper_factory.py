"""The BarKeeper factory: builds the BarKeeper the config asks for.

The BarKeeper can decide where its bar sits in more than one way. Each way is a Bar class with a small factory of its own
(`from_settings`); this module is the list of them and the one function the entry point and the testbenches call:

    bar_keeper = bar_keeper_from_settings(config.qualifying)

`settings.algorithm` names the way: "percentile", "decaying_quantile" or "moving_average". Each bar reads only the settings it needs,
so one `[qualifying]` section can hold the settings of all of them and switching is a single line. A new way of deciding is a new class,
its name in `BAR_ALGORITHMS` (pom.defaults, which the config checks the setting against and which holds the default) and one new entry
in ALGORITHMS here; a test checks the two name the same bars.

This module never reads Config; it is handed the `[qualifying]` settings, whose algorithm the config has already checked.
"""
from typing import Callable, Dict, Optional, Type

from pom.bookkeeper.bookkeeper import Bookkeeper
from pom.defaults import QualifyingSettings

from pom.qualifying.bar_keeper import BarKeeper
from pom.qualifying.decaying_quantile_bar import DecayingQuantileBar
from pom.qualifying.moving_average_bar import MovingAverageBar
from pom.qualifying.percentile_bar import PercentileBar
from pom.qualifying.bar import Bar

ALGORITHMS: Dict[str, Type[Bar]] = {
    "percentile": PercentileBar,
    "decaying_quantile": DecayingQuantileBar,
    "moving_average": MovingAverageBar,
}


def bar_from_settings(settings: QualifyingSettings, started_at: float, strict: bool = False, bookkeeper: Optional[Bookkeeper] = None,
                      stream_now: Optional[Callable[[], float]] = None) -> Bar:
    """The bar the `[qualifying]` settings name (`settings.algorithm`), built from them, to begin at stream time `started_at`, on the stream
    clock `stream_now` (None gives the bar one of its own)."""
    return ALGORITHMS[settings.algorithm].from_settings(settings, started_at, strict=strict, bookkeeper=bookkeeper, stream_now=stream_now)


def bar_keeper_from_settings(settings: QualifyingSettings, started_at: float, strict: bool = False, bookkeeper: Optional[Bookkeeper] = None,
                             stream_now: Optional[Callable[[], float]] = None) -> BarKeeper:
    """A BarKeeper over the bar the `[qualifying]` settings name (beginning at stream time `started_at`), handed the same settings and the
    stream clock (`stream_now`, the Printer's Timekeeper's `now`; None gives the BarKeeper one of its own)."""
    return BarKeeper(bar_from_settings(settings, started_at, strict=strict, bookkeeper=bookkeeper, stream_now=stream_now), settings, strict=strict,
                     bookkeeper=bookkeeper, stream_now=stream_now)
