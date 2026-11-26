"""Tests for pom.defaults: every setting declared once, with a default and a rule, and the shipped config/pom.toml kept in step with it."""
import dataclasses
import tomllib
import warnings
from pathlib import Path

import pytest

from pom.config import KNOWN_KEYS
from pom.defaults import SECTIONS, ProjectSettings
from pom.setting_rules import SettingRule, rule_of

SHIPPED = Path(__file__).resolve().parents[1] / "config" / "pom.toml"
EVERY_CLASS = [ProjectSettings, *SECTIONS.values()]


def every_field():
    return [(settings.__name__, f) for settings in EVERY_CLASS for f in dataclasses.fields(settings)]


@pytest.mark.parametrize("owner,settings_field", every_field(), ids=[f"{o}.{f.name}" for o, f in every_field()])
def test_every_setting_has_a_default_and_a_rule(owner, settings_field):
    assert settings_field.default is not dataclasses.MISSING, f"{owner}.{settings_field.name} has no default"
    assert isinstance(rule_of(settings_field), SettingRule), f"{owner}.{settings_field.name} has no rule"


@pytest.mark.parametrize("owner,settings_field", every_field(), ids=[f"{o}.{f.name}" for o, f in every_field()])
def test_every_default_follows_its_own_rule(owner, settings_field):
    """A default that broke its own rule would be a value the Printer could never be given on purpose, used whenever the file is wrong."""
    assert rule_of(settings_field).read(settings_field.default) == settings_field.default


def test_every_settings_class_is_frozen_so_no_part_can_change_what_another_reads():
    for settings in EVERY_CLASS:
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(settings(), dataclasses.fields(settings)[0].name, None)


def shipped_keys() -> set:
    with SHIPPED.open("rb") as handle:
        document = tomllib.load(handle)
    return {f"{key}.{inner}" for key, value in document.items() if isinstance(value, dict) for inner in value} | {
        key for key, value in document.items() if not isinstance(value, dict)}


def test_the_shipped_config_and_the_defaults_name_the_same_settings__a_warning_only():
    """Wes, 2026-11-26: a key commented out of pom.toml (it takes its default) or an extra key added there is worth knowing, never a failure.
    The Printer says the same in its log when it starts."""
    shipped = shipped_keys()
    unset, unknown = sorted(KNOWN_KEYS - shipped), sorted(shipped - KNOWN_KEYS)
    if unset:
        warnings.warn(UserWarning(f"config/pom.toml does not set {len(unset)} setting(s), which take their defaults: {', '.join(unset)}"))
    if unknown:
        warnings.warn(UserWarning(f"config/pom.toml sets {len(unknown)} key(s) defaults.py does not declare: {', '.join(unknown)}"))
