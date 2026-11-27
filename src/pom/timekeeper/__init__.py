"""The timekeeper module: the stream's one clock.

The Timekeeper (one instance, built by the entry point) keeps stream time, the timeline the bar, the cooldown, the chart and the typed lines
all share; every part that needs "now" is handed its `now`.
"""
from pom.timekeeper.timekeeper import Timekeeper

__all__ = ["Timekeeper"]
