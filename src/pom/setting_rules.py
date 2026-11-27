"""The old name of pom.value_rules, kept so that `from pom.setting_rules import ...` still works. New code imports pom.value_rules."""
from pom.value_rules import *  # noqa: F401,F403
from pom.value_rules import SettingCorrected, SettingRejected, SettingRule, ValueCorrected, ValueRejected, ValueRule, rule_of, setting  # noqa: F401
