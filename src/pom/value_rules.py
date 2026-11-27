"""The rules a value must follow: one small object per kind of value, used for the settings and for the values one part hands another.

A setting is declared with its rule in defaults.py and read by config.py. A value handed to a public method (a score, a stream time, a count)
is checked against a rule by the receiving class's ValueChecker (see value_checker.py). The same rules serve both, so there is one meaning
of "a number above 0" in the whole Printer.

A ValueRule says, in words, what a value must be (`wanted`, which finishes the sentence "must be ...") and how a good value is stored (`read`).
`read` raises ValueRejected when the value breaks the rule (a setting then keeps its default and says so), and ValueCorrected when part of
the value can be kept (the reader keeps that part and says what it dropped). A number rule with an end it includes also has `nearest`: for
a number past that end it gives the end itself (a -2 where at least 0 is wanted gives 0), which a ValueChecker uses before any fallback value.
A rule never logs and never reads the config: it is stateless, so the same object can serve any number of settings and receivers.

Design decisions, and why:
  * The rules are written once, here, and named by what they ask for (`above_zero`, `whole_at_least(1)`, `one_of(...)`), so a line in
    defaults.py reads as one sentence: `freshness_seconds: float = setting(360.0, above_zero())`.
  * The module was setting_rules.py, with SettingRule, SettingRejected and SettingCorrected; those names still work (see the end of this
    file, and the setting_rules module, which re-exports this one).
  * A setting past its range keeps its default, as it always has: config.py does not use `nearest`. Only a ValueChecker does.
  * The setting's rule travels with its default, in the field's own metadata (`dataclasses.field(metadata=...)`, the standard library's
    designed hook for this), so the reader walks the fields and needs no list of its own.
  * A boolean is never a number here, though Python counts True as 1: `true` where a number belongs is a typo, not a 1.
"""
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_LEVELS = ("CRITICAL", "DEBUG", "ERROR", "INFO", "WARNING")


class ValueRejected(ValueError):
    """The value breaks its rule; a setting keeps its default, a handed value gets the nearest good value or the receiver's fallback value."""


class ValueCorrected(ValueError):
    """Part of the value breaks its rule; `kept` is the part that is used, and `how` finishes the sentence "<name> ..." to say so."""

    def __init__(self, kept: Any, how: str):
        super().__init__(how)
        self.kept = kept
        self.how = how


@dataclass(frozen=True)
class ValueRule:
    """What one value must be.

    Attributes:
        wanted: Finishes "<name> must be ...", such as "a number above 0".
        read: The value to store for a good value (a whole number becomes a float where a number is wanted); raises ValueRejected or
            ValueCorrected otherwise.
        when_unreadable: The value used when a setting breaks the rule, where that must differ from the default (a flag that cannot be read
            and leans toward privacy); None uses the default.
        nearest: For a value `read` rejected: the good value nearest to it (an end of the range it is past), or None when there is none
            (not a number, or past an end the rule does not include). None for a rule with no such ends.
    """
    wanted: str
    read: Callable[[Any], Any]
    when_unreadable: Optional[Any] = None
    nearest: Optional[Callable[[Any], Any]] = None


def setting(default: Any, rule: ValueRule):
    """A settings field: its default, and the rule its value must follow (kept in the field's metadata for the reader)."""
    return field(default=default, metadata={"rule": rule})


def rule_of(settings_field) -> ValueRule:
    """The rule a settings field was declared with."""
    return settings_field.metadata["rule"]


def is_number(value) -> bool:
    """True for a real, finite int or float: not a boolean, not NaN, not infinity. What a score, a time or a count has to be.

    The one number check for the whole Printer: every receiver that asks "is this a number?" asks here.
    """
    return _is_int_or_float(value) and math.isfinite(value)


def _is_int_or_float(value) -> bool:
    """An int or a float, never a boolean; NaN and infinity included (the settings' rules then say which they take)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_whole(value) -> bool:
    """An int, never a boolean or a decimal."""
    return isinstance(value, int) and not isinstance(value, bool)


def _clamped_to(low: Optional[float] = None, high: Optional[float] = None) -> Callable[[Any], Optional[float]]:
    """A `nearest` for a range whose ends `low` and `high` are included (None: no such end): a number past an end gives that end."""
    def nearest(value):
        if not _is_int_or_float(value) or math.isnan(value):
            return None
        if low is not None and value < low:
            return low
        if high is not None and value > high:
            return high
        return None
    return nearest


def _number_rule(wanted: str, is_good: Callable[[float], bool], nearest: Optional[Callable[[Any], Any]] = None) -> ValueRule:
    """A rule for a number (stored as a float) that `is_good` accepts."""
    def read(value):
        if _is_int_or_float(value) and is_good(value):
            return float(value)
        raise ValueRejected(wanted)
    return ValueRule(wanted, read, nearest=nearest)


def at_least(minimum: float) -> ValueRule:
    """A number of at least `minimum`."""
    return _number_rule(f"a number of at least {minimum:g}", lambda v: v >= minimum, _clamped_to(low=float(minimum)))


def above_zero() -> ValueRule:
    """A number above 0, and not infinity. (No nearest: 0 itself is not above 0.)"""
    return _number_rule("a number above 0", lambda v: 0.0 < v < math.inf)


def not_negative() -> ValueRule:
    """A number of 0 or more, and not infinity."""
    return _number_rule("a number of at least 0", lambda v: 0 <= v < math.inf, _clamped_to(low=0.0))


def percent() -> ValueRule:
    """A number above 0 and at most 100."""
    return _number_rule("a number above 0 and at most 100", lambda v: 0.0 < v <= 100.0, _clamped_to(high=100.0))


def below_one() -> ValueRule:
    """A number from 0 up to but not including 1."""
    return _number_rule("a number from 0 up to but not including 1", lambda v: 0.0 <= v < 1.0, _clamped_to(low=0.0))


def any_number() -> ValueRule:
    """Any finite number."""
    return _number_rule("a number", lambda v: -math.inf < v < math.inf)


def whole_at_least(minimum: int) -> ValueRule:
    """A whole number of at least `minimum`."""
    wanted = f"a whole number of at least {minimum}"

    def read(value):
        if _is_whole(value) and value >= minimum:
            return value
        raise ValueRejected(wanted)
    return ValueRule(wanted, read, nearest=_whole_at_least(minimum))


def _whole_at_least(minimum: int) -> Callable[[Any], Optional[int]]:
    """A `nearest` for whole numbers of at least `minimum`: a whole number below it gives `minimum`; anything else has no nearest."""
    def nearest(value):
        return minimum if _is_whole(value) and value < minimum else None
    return nearest


def count() -> ValueRule:
    """A whole number of at least 1, also as digits in text (an environment variable is always text)."""
    wanted = "a whole number of at least 1"

    def read(value):
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if _is_whole(value) and value >= 1:
            return value
        raise ValueRejected(wanted)
    return ValueRule(wanted, read, nearest=_whole_at_least(1))


def port_number() -> ValueRule:
    """A network port: a whole number from 1 to 65535."""
    wanted = "a whole number from 1 to 65535"

    def read(value):
        if _is_whole(value) and 1 <= value <= 65535:
            return value
        raise ValueRejected(wanted)
    return ValueRule(wanted, read)


def one_of(choices: Sequence[str]) -> ValueRule:
    """One of the named choices (text)."""
    wanted = f"one of {', '.join(choices)}"

    def read(value):
        if isinstance(value, str) and value in choices:
            return value
        raise ValueRejected(wanted)
    return ValueRule(wanted, read)


def whole_one_of(choices: Sequence[int], wanted: str = "") -> ValueRule:
    """One of the listed whole numbers. `wanted` replaces the list in the words, where the list is long (the baud rates)."""
    wanted = wanted or f"one of {', '.join(str(choice) for choice in choices)}"

    def read(value):
        if _is_whole(value) and value in choices:
            return value
        raise ValueRejected(wanted)
    return ValueRule(wanted, read)


def flag(when_unreadable: Optional[bool] = None) -> ValueRule:
    """True or false: a TOML boolean, or text such as "on", "off", "1" or "no" (an environment variable is always text).

    `when_unreadable` is the value for a flag that cannot be read, where it must differ from the default; None uses the default.
    """
    wanted = "true or false"

    def read(value):
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise ValueRejected(wanted)
    return ValueRule(wanted, read, when_unreadable)


def function() -> ValueRule:
    """Something that can be called: a clock, a sleep, a step handed in. (Not for a setting: a setting is never a function.)"""
    wanted = "a function"

    def read(value):
        if callable(value):
            return value
        raise ValueRejected(wanted)
    return ValueRule(wanted, read)


def text() -> ValueRule:
    """Any text, empty included."""
    def read(value):
        if isinstance(value, str):
            return value
        raise ValueRejected("text")
    return ValueRule("text", read)


def web_address() -> ValueRule:
    """A server's address: text that starts with http:// or https://."""
    wanted = "a web address (http:// or https://)"

    def read(value):
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
        raise ValueRejected(wanted)
    return ValueRule(wanted, read)


def nonblank_text() -> ValueRule:
    """Text with something in it."""
    wanted = "text with something in it"

    def read(value):
        if isinstance(value, str) and value.strip():
            return value
        raise ValueRejected(wanted)
    return ValueRule(wanted, read)


def list_of_numbers() -> ValueRule:
    """A list of finite numbers, at least one, stored as a tuple of floats."""
    wanted = "a list of numbers"

    def read(value):
        if isinstance(value, (list, tuple)) and value and all(is_number(v) for v in value):
            return tuple(float(v) for v in value)
        raise ValueRejected(wanted)
    return ValueRule(wanted, read)


def list_of_text() -> ValueRule:
    """A list of text (it may be empty), stored as a tuple."""
    wanted = "a list of text"

    def read(value):
        if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
            return tuple(value)
        raise ValueRejected(wanted)
    return ValueRule(wanted, read)


def log_level() -> ValueRule:
    """A log level's name, in any case; stored in capitals."""
    wanted = f"one of {', '.join(_LEVELS)}"

    def read(value):
        name = str(value).strip().upper()
        if name in _LEVELS:
            return name
        raise ValueRejected(wanted)
    return ValueRule(wanted, read)


def only_characters(allowed: str) -> ValueRule:
    """Text made only of the `allowed` characters; any other character is left out (and said), each kept once."""
    def read(value):
        if not isinstance(value, str):
            raise ValueRejected("text")
        kept = "".join(dict.fromkeys(c for c in value if c in allowed))
        if kept != value:
            raise ValueCorrected(kept, f"may hold only the characters {allowed}, got {value!r}; keeping {kept!r}")
        return kept
    return ValueRule("text", read)


# The names this module had as setting_rules.py, kept so nothing that used them breaks.
SettingRule = ValueRule
SettingRejected = ValueRejected
SettingCorrected = ValueCorrected
