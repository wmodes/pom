"""The Light: what one status light on the monitoring page can say.

  * UP      doing its job right now, on a real signal (a request answered, sound arriving, the port open). Green.
  * WAITING alive but cannot do its job yet (a model still loading). Amber.
  * DOWN    not answering, missing, or silent past its limit. No news is DOWN. Red.
  * NONE    nothing to check (no printer is configured). Grey.

A Light is its word, so it is the same text on the page, in the feed and in the log.
"""
from enum import StrEnum


class Light(StrEnum):
    UP = "up"
    WAITING = "waiting"
    DOWN = "down"
    NONE = "none"

    @classmethod
    def of(cls, value) -> "Light":
        """The Light for a Light, its word (any case, spaces round it allowed), or the old True, False and None; anything else is UP or DOWN as it would be as a bool."""
        if isinstance(value, cls):
            return value
        if value is None:
            return cls.NONE
        if isinstance(value, str):
            word = value.strip().lower()
            if word in cls._value2member_map_:
                return cls(word)
        return cls.UP if value else cls.DOWN
