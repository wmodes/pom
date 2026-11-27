"""The ValueChecker: how a public method checks a value it was handed, against a rule, and what it does with one that breaks it.

A public method can be called by code its class does not control, so it checks what it receives against its own spec (Design Audit 04 item
3). Each receiving class makes one ValueChecker with its own logger and its own name, and checks each argument in one line:

    self.check = ValueChecker(self.logger, "TurnHistory")
    ...
    window_size = self.check(window_size, count(), fallback=0, method="add", argument="window_size")

A good value comes back as the rule stores it. A bad one comes back as:
  1. the part the rule could keep (ValueCorrected), or else
  2. the good value nearest to it (the rule's `nearest`: an end of the range it is past), or else
  3. the receiver's `fallback`, the one agreed for that kind of value, chosen by how the value is used downstream.
Every bad value is a warning, so the log shows which caller to correct; in strict mode the receiver's logger raises it instead.

Design decisions, and why:
  * THE WARNING IS THE RECEIVER'S. It goes through the receiver's own logger, so the log line carries the receiver's module name, and it
    names the receiver's class, method and argument: "TurnHistory.add was given a heard_at that is not a number ...". The checker is never
    named: the log is read to find the caller, and the receiver is where to start.
  * THE RULES ARE THE SETTINGS' RULES (pom.value_rules), so "a number above 0" means the same thing in the config and between parts.
  * A WARNING NEVER QUOTES WHAT WAS SAID. A number is shown (a score or a time is the Printer's own); anything else is named by its type
    only, since a string handed in by mistake could be words heard in the room.
  * It keeps no state beyond its logger and its owner's name, so one checker serves every call of its class from any thread.
"""
import math
from typing import Any

from pom.value_rules import ValueCorrected, ValueRejected, ValueRule


def described(value: Any) -> str:
    """The value as a warning may show it: a number as itself (NaN and infinity by name), anything else by its type only."""
    if isinstance(value, bool) or value is None:
        return repr(value)
    if isinstance(value, (int, float)):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "infinity" if value > 0 else "minus infinity"
        return f"{value:g}"
    return f"a {type(value).__name__}"


class ValueChecker:
    """Checks the values handed to one class's public methods, and warns, through that class's logger, about any that break their rule.

    Attributes:
        logger: The receiving class's own logger (a LogRecorder from its Bookkeeper), so the warning is the receiver's.
        owner: The receiving class's name, as the warning names it.
    """

    def __init__(self, logger, owner: str):
        """Args:
            logger: The receiving class's logger (`self.logger`); in strict mode it raises a warning instead of logging it.
            owner: The receiving class's name, such as "TurnHistory".
        """
        self.logger = logger
        self.owner = owner

    def __call__(self, value: Any, rule: ValueRule, *, fallback: Any, method: str, argument: str) -> Any:
        """The value to use for one argument: the value itself if it keeps the rule, else the kept part, the nearest good value, or `fallback`.

        Args:
            value: What the method was handed.
            rule: What it must be (a rule from pom.value_rules).
            fallback: What to use when the rule can keep nothing and has no nearest good value: the one agreed for this kind of value.
            method: The receiving method's name, for the warning.
            argument: The argument's name, for the warning.
        """
        try:
            return rule.read(value)
        except ValueCorrected as corrected:
            self.logger.warning("%s.%s was given a %s that %s", self.owner, method, argument, corrected.how)
            return corrected.kept
        except ValueRejected:
            nearest = rule.nearest(value) if rule.nearest is not None else None
            if nearest is not None:
                self.logger.warning("%s.%s was given a %s of %s, which is not %s; using the nearest, %s", self.owner, method, argument,
                                    described(value), rule.wanted, described(nearest))
                return nearest
            self.logger.warning("%s.%s was given a %s of %s, which is not %s; using %s", self.owner, method, argument, described(value),
                                rule.wanted, described(fallback))
            return fallback
