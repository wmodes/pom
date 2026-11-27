"""The ValueChecker: how a public method checks a value it was handed, against a rule, and what it does with one that breaks it.

A public method can be called by code its class does not control, so it checks what it receives against its own spec (Design Audit 04 item
3). Each receiving class makes one ValueChecker with its own logger and its own name, and checks each argument in one line:

    self.check = ValueChecker(self.logger, "TurnHistory")
    ...
    window_size = self.check(window_size, count(), fallback_value=0, method="add", argument="window_size")

A good value comes back as the rule stores it. A bad one comes back as:
  1. the part the rule could keep (ValueCorrected), or else
  2. the good value nearest to it (the rule's `nearest`: an end of the range it is past), or else
  3. the receiver's fallback value, the one agreed for that kind of value, chosen by how the value is used downstream.
Every bad value is a warning, so the log shows which caller to correct; in strict mode the receiver's logger raises it instead.

THE KINDS OF VALUE THE PARTS HAND EACH OTHER have their rule and fallback value written once, here, as methods, so every receiver treats
each kind the same way (agreed with Wes, Design Audit 04 item 3, from the Design Document's principles: nothing blocks, every limit errs
long, printing has priority):

    | Method          | Rule                                            | Past an end | Fallback value (NaN, wrong type, no nearest)   |
    |-----------------|-------------------------------------------------|-------------|------------------------------------------------|
    | `score`         | at least 0, or None (unscored, quietly)          | below 0: 0  | None (unscored: a score is never guessed)       |
    | `stream_time`   | at least 0 (no upper end: the stream clock can   | below 0: 0  | the stream time now, so the score still counts  |
    |                 | step back a few milliseconds)                    |             | and a turn is fresh                             |
    | `bar`           | at least 0, or None (no bar yet, quietly)        | below 0: 0  | None (no bar yet)                               |
    | `count`         | a whole number of at least 0                     | below 0: 0  | 0                                              |

    now = self.check.stream_time(now, method="rule", argument="now")

Design decisions, and why:
  * THE WARNING IS THE RECEIVER'S. It goes through the receiver's own logger, so the log line carries the receiver's module name, and it
    names the receiver's class, method and argument: "TurnHistory.add was given a heard_at that is not a number ...". The checker is never
    named: the log is read to find the caller, and the receiver is where to start.
  * THE RULES ARE THE SETTINGS' RULES (pom.value_rules), so "a number above 0" means the same thing in the config and between parts.
  * A WARNING NEVER QUOTES WHAT WAS SAID. A number is shown (a score or a time is the Printer's own); anything else is named by its type
    only, since a string handed in by mistake could be words heard in the room.
  * It keeps no state beyond its logger, its owner's name and the stream clock it was handed, so one checker serves every call of its
    class from any thread. The stream clock is asked only when a stream time is not a number, and never while the receiver holds a lock
    (the receiver checks before it takes its lock).
"""
import math
from typing import Any, Callable, Optional

from pom.timekeeper import Timekeeper
from pom.value_rules import ValueCorrected, ValueRejected, ValueRule, is_number, not_negative, whole_at_least


def described(value: Any) -> str:
    """The value as a warning may show it: a number as itself (NaN and infinity by name), a function by its name, anything else by its
    type only."""
    if isinstance(value, bool) or value is None:
        return repr(value)
    if isinstance(value, (int, float)):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "infinity" if value > 0 else "minus infinity"
        return f"{value:g}"
    if callable(value) and hasattr(value, "__name__"):
        module = getattr(value, "__module__", None)
        return f"{module}.{value.__name__}" if module else value.__name__
    return _with_article(type(value).__name__)


def _with_article(word: str) -> str:
    """The word with "a" or "an" before it, by its first letter: "a score", "an at", "an object"."""
    return f"{'an' if word[:1].lower() in 'aeiou' else 'a'} {word}"


class ValueChecker:
    """Checks the values handed to one class's public methods, and warns, through that class's logger, about any that break their rule.

    Attributes:
        logger: The receiving class's own logger (a LogRecorder from its Bookkeeper), so the warning is the receiver's.
        owner: The receiving class's name, as the warning names it.
        stream_now: The stream clock whose time now is the fallback value for a stream time that is not a number (read-only; public so a
            builder's tests can see which clock a part was handed).
    """

    def __init__(self, logger, owner: str, stream_now: Optional[Callable[[], float]] = None):
        """Args:
            logger: The receiving class's logger (`self.logger`); in strict mode it raises a warning instead of logging it.
            owner: The receiving class's name, such as "TurnHistory".
            stream_now: The stream time now (the Printer's Timekeeper's `now`), the fallback value for a stream time that is not a
                number. None (a test, a tool, or a class that takes no stream times) gives a Timekeeper of its own, which runs on real
                seconds from when the checker was made.
        """
        self.logger = logger
        self.owner = owner
        self.stream_now = stream_now if stream_now is not None else Timekeeper().now

    def score(self, value: Any, *, method: str, argument: str = "score", may_be_unscored: bool = True) -> Optional[float]:
        """A score: below 0 is 0; one that is not a number is None (unscored), with a warning.

        `may_be_unscored` says whether None (unscored) is in this receiver's spec: then it stays None without a word. Where a score is
        required (a turn, a dot on the chart), None is a bad value like any other, with a warning.
        """
        if value is None and may_be_unscored:
            return None
        return self(value, not_negative(), fallback_value=None, method=method, argument=argument)

    def stream_time(self, value: Any, *, method: str, argument: str) -> float:
        """A stream time: below 0 is 0; one that is not a number is the stream time now (asked only then), with a warning."""
        fallback_value = None if is_number(value) else self.stream_now()
        return self(value, not_negative(), fallback_value=fallback_value, method=method, argument=argument)

    def bar(self, value: Any, *, method: str, argument: str = "bar") -> Optional[float]:
        """A bar: None (no bar yet) stays None without a word; below 0 is 0; anything else that is not a number is None, with a warning."""
        return None if value is None else self(value, not_negative(), fallback_value=None, method=method, argument=argument)

    def count(self, value: Any, *, method: str, argument: str) -> int:
        """A count: a whole number of at least 0; below 0 is 0, and anything else is 0, with a warning."""
        return self(value, whole_at_least(0), fallback_value=0, method=method, argument=argument)

    def __call__(self, value: Any, rule: ValueRule, *, fallback_value: Any, method: str, argument: str) -> Any:
        """The value to use for one argument: the value itself if it keeps the rule, else the kept part, the nearest good value, or the fallback value.

        Args:
            value: What the method was handed.
            rule: What it must be (a rule from pom.value_rules).
            fallback_value: What to use when the rule can keep nothing and has no nearest good value: the one agreed for this kind of value.
            method: The receiving method's name, for the warning.
            argument: The argument's name, for the warning.
        """
        try:
            return rule.read(value)
        except ValueCorrected as corrected:
            self.logger.warning("%s.%s was given %s that %s", self.owner, method, _with_article(argument), corrected.how)
            return corrected.kept
        except ValueRejected:
            nearest = rule.nearest(value) if rule.nearest is not None else None
            if nearest is not None:
                self.logger.warning("%s.%s was given %s of %s, which is not %s; using the nearest, %s", self.owner, method,
                                    _with_article(argument), described(value), rule.wanted, described(nearest))
                return nearest
            self.logger.warning("%s.%s was given %s of %s, which is not %s; using %s", self.owner, method, _with_article(argument),
                                described(value), rule.wanted, described(fallback_value))
            return fallback_value
