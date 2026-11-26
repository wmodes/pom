"""The rules a setting's value must follow: one small object per kind of value, used by defaults.py to declare each setting and by config.py to read it.

A SettingRule says, in words, what a value must be (`wanted`, which finishes the sentence "must be ...") and how a good value is stored (`read`).
`read` raises SettingRejected when the value breaks the rule (the reader then uses the setting's default and says so), and SettingCorrected
when part of the value can be kept (the reader keeps that part and says what it dropped). It never logs and never reads the config: it is a
stateless rule, so the same object can serve any number of settings.

Design decisions, and why:
  * The rules are written once, here, and named by what they ask for (`above_zero`, `whole_at_least(1)`, `one_of(...)`), so a line in
    defaults.py reads as one sentence: `freshness_seconds: float = setting(360.0, above_zero())`.
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


class SettingRejected(ValueError):
    """The value breaks its rule; the setting keeps its default."""


class SettingCorrected(ValueError):
    """Part of the value breaks its rule; `kept` is the part that is used, and `how` finishes the sentence "<setting> ..." to say so."""

    def __init__(self, kept: Any, how: str):
        super().__init__(how)
        self.kept = kept
        self.how = how


@dataclass(frozen=True)
class SettingRule:
    """What one setting's value must be.

    Attributes:
        wanted: Finishes "<setting> must be ...", such as "a number above 0".
        read: The value to store for a good value (a whole number becomes a float where a number is wanted); raises SettingRejected or
            SettingCorrected otherwise.
        when_unreadable: The value used when a value breaks the rule, where that must differ from the default (a flag that cannot be read
            and leans toward privacy); None uses the default.
    """
    wanted: str
    read: Callable[[Any], Any]
    when_unreadable: Optional[Any] = None


def setting(default: Any, rule: SettingRule):
    """A settings field: its default, and the rule its value must follow (kept in the field's metadata for the reader)."""
    return field(default=default, metadata={"rule": rule})


def rule_of(settings_field) -> SettingRule:
    """The rule a settings field was declared with."""
    return settings_field.metadata["rule"]


def _is_number(value) -> bool:
    """An int or a float, never a boolean."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_whole(value) -> bool:
    """An int, never a boolean or a decimal."""
    return isinstance(value, int) and not isinstance(value, bool)


def _number_rule(wanted: str, is_good: Callable[[float], bool]) -> SettingRule:
    """A rule for a number (stored as a float) that `is_good` accepts."""
    def read(value):
        if _is_number(value) and is_good(value):
            return float(value)
        raise SettingRejected(wanted)
    return SettingRule(wanted, read)


def at_least(minimum: float) -> SettingRule:
    """A number of at least `minimum`."""
    return _number_rule(f"a number of at least {minimum:g}", lambda v: v >= minimum)


def above_zero() -> SettingRule:
    """A number above 0, and not infinity."""
    return _number_rule("a number above 0", lambda v: 0.0 < v < math.inf)


def not_negative() -> SettingRule:
    """A number of 0 or more, and not infinity."""
    return _number_rule("a number of at least 0", lambda v: 0 <= v < math.inf)


def percent() -> SettingRule:
    """A number above 0 and at most 100."""
    return _number_rule("a number above 0 and at most 100", lambda v: 0.0 < v <= 100.0)


def below_one() -> SettingRule:
    """A number from 0 up to but not including 1."""
    return _number_rule("a number from 0 up to but not including 1", lambda v: 0.0 <= v < 1.0)


def any_number() -> SettingRule:
    """Any finite number."""
    return _number_rule("a number", lambda v: -math.inf < v < math.inf)


def whole_at_least(minimum: int) -> SettingRule:
    """A whole number of at least `minimum`."""
    wanted = f"a whole number of at least {minimum}"

    def read(value):
        if _is_whole(value) and value >= minimum:
            return value
        raise SettingRejected(wanted)
    return SettingRule(wanted, read)


def count() -> SettingRule:
    """A whole number of at least 1, also as digits in text (an environment variable is always text)."""
    wanted = "a whole number of at least 1"

    def read(value):
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if _is_whole(value) and value >= 1:
            return value
        raise SettingRejected(wanted)
    return SettingRule(wanted, read)


def port_number() -> SettingRule:
    """A network port: a whole number from 1 to 65535."""
    wanted = "a whole number from 1 to 65535"

    def read(value):
        if _is_whole(value) and 1 <= value <= 65535:
            return value
        raise SettingRejected(wanted)
    return SettingRule(wanted, read)


def one_of(choices: Sequence[str]) -> SettingRule:
    """One of the named choices (text)."""
    wanted = f"one of {', '.join(choices)}"

    def read(value):
        if isinstance(value, str) and value in choices:
            return value
        raise SettingRejected(wanted)
    return SettingRule(wanted, read)


def whole_one_of(choices: Sequence[int], wanted: str = "") -> SettingRule:
    """One of the listed whole numbers. `wanted` replaces the list in the words, where the list is long (the baud rates)."""
    wanted = wanted or f"one of {', '.join(str(choice) for choice in choices)}"

    def read(value):
        if _is_whole(value) and value in choices:
            return value
        raise SettingRejected(wanted)
    return SettingRule(wanted, read)


def flag(when_unreadable: Optional[bool] = None) -> SettingRule:
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
        raise SettingRejected(wanted)
    return SettingRule(wanted, read, when_unreadable)


def text() -> SettingRule:
    """Any text, empty included."""
    def read(value):
        if isinstance(value, str):
            return value
        raise SettingRejected("text")
    return SettingRule("text", read)


def nonblank_text() -> SettingRule:
    """Text with something in it."""
    wanted = "text with something in it"

    def read(value):
        if isinstance(value, str) and value.strip():
            return value
        raise SettingRejected(wanted)
    return SettingRule(wanted, read)


def list_of_numbers() -> SettingRule:
    """A list of finite numbers, at least one, stored as a tuple of floats."""
    wanted = "a list of numbers"

    def read(value):
        if isinstance(value, (list, tuple)) and value and all(_is_number(v) and -math.inf < v < math.inf for v in value):
            return tuple(float(v) for v in value)
        raise SettingRejected(wanted)
    return SettingRule(wanted, read)


def list_of_text() -> SettingRule:
    """A list of text (it may be empty), stored as a tuple."""
    wanted = "a list of text"

    def read(value):
        if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
            return tuple(value)
        raise SettingRejected(wanted)
    return SettingRule(wanted, read)


def log_level() -> SettingRule:
    """A log level's name, in any case; stored in capitals."""
    wanted = f"one of {', '.join(_LEVELS)}"

    def read(value):
        name = str(value).strip().upper()
        if name in _LEVELS:
            return name
        raise SettingRejected(wanted)
    return SettingRule(wanted, read)


def only_characters(allowed: str) -> SettingRule:
    """Text made only of the `allowed` characters; any other character is left out (and said), each kept once."""
    def read(value):
        if not isinstance(value, str):
            raise SettingRejected("text")
        kept = "".join(dict.fromkeys(c for c in value if c in allowed))
        if kept != value:
            raise SettingCorrected(kept, f"may hold only the characters {allowed}, got {value!r}; keeping {kept!r}")
        return kept
    return SettingRule("text", read)
