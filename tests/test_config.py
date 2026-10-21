"""Tests for pom.config: shared settings from a TOML file and the environment, never raising."""
import logging

import pytest

from pom.config import Config


def write(tmp_path, text):
    path = tmp_path / "pom.toml"
    path.write_text(text, encoding="utf-8")
    return path


# --- Defaults and sources --------------------------------------------------------------

def test_defaults_when_there_is_no_file_and_no_environment():
    config = Config.load(environ={})
    assert config.strict is False and config.log_level == "INFO" and config.report.ok


def test_a_missing_file_is_fine_and_silent(tmp_path):
    config = Config.load(tmp_path / "absent.toml", environ={})
    assert config.report.ok and config.strict is False


def test_file_values_are_read(tmp_path):
    config = Config.load(write(tmp_path, 'strict = true\nlog_level = "debug"\n'), environ={})
    assert config.strict is True and config.log_level == "DEBUG" and config.report.ok


def test_environment_overrides_the_file(tmp_path):
    path = write(tmp_path, 'strict = false\nlog_level = "INFO"\n')
    config = Config.load(path, environ={"POM_STRICT": "1", "POM_LOG_LEVEL": "error"})
    assert config.strict is True and config.log_level == "ERROR"


def test_environment_works_without_a_file():
    config = Config.load(environ={"POM_STRICT": "yes"})
    assert config.strict is True


@pytest.mark.parametrize("text,expected", [("1", True), ("true", True), ("YES", True), ("on", True),
                                           ("0", False), ("False", False), ("no", False), ("OFF", False)])
def test_strict_environment_spellings(text, expected):
    config = Config.load(environ={"POM_STRICT": text})
    assert config.strict is expected and config.report.ok


def test_debug_defaults_to_off():
    assert Config.load(environ={}).debug is False


def test_debug_is_read_from_the_file_and_the_environment(tmp_path):
    assert Config.load(write(tmp_path, "debug = true\n"), environ={}).debug is True
    assert Config.load(write(tmp_path, "debug = true\n"), environ={"POM_DEBUG": "0"}).debug is False
    assert Config.load(environ={"POM_DEBUG": "on"}).debug is True


def test_bad_debug_values_mean_off_with_a_warning(tmp_path):
    from_file = Config.load(write(tmp_path, 'debug = "loud"\n'), environ={})
    from_env = Config.load(environ={"POM_DEBUG": "banana"})
    assert from_file.debug is False and from_env.debug is False
    assert any("debug must be true or false" in w for w in from_file.report.warnings)
    assert any("POM_DEBUG" in w for w in from_env.report.warnings)


def test_local_logs_defaults_to_off():
    assert Config.load(environ={}).local_logs is False


def test_local_logs_is_read_from_the_file_and_the_environment(tmp_path):
    assert Config.load(write(tmp_path, "local_logs = true\n"), environ={}).local_logs is True
    assert Config.load(write(tmp_path, "local_logs = true\n"), environ={"POM_LOCAL_LOGS": "0"}).local_logs is False
    assert Config.load(environ={"POM_LOCAL_LOGS": "yes"}).local_logs is True


def test_bad_local_logs_values_mean_off_with_a_warning(tmp_path):
    from_file = Config.load(write(tmp_path, 'local_logs = "sure"\n'), environ={})
    from_env = Config.load(environ={"POM_LOCAL_LOGS": "banana"})
    assert from_file.local_logs is False and from_env.local_logs is False
    assert any("local_logs must be true or false" in w for w in from_file.report.warnings)
    assert any("POM_LOCAL_LOGS" in w for w in from_env.report.warnings)


def test_the_real_environment_is_used_by_default(monkeypatch):
    monkeypatch.setenv("POM_LOG_LEVEL", "WARNING")
    assert Config.load().log_level == "WARNING"


def test_the_committed_sample_config_is_clean_and_safe_for_the_pi(tmp_path):
    """Checked on a copy, so a developer's own config/pom.local.toml cannot change the result."""
    import shutil
    from pathlib import Path
    sample = Path(__file__).resolve().parents[1] / "config" / "pom.toml"
    copy = tmp_path / "pom.toml"
    shutil.copy(sample, copy)
    config = Config.load(copy, environ={})
    assert config.report.ok
    assert config.strict is False and config.debug is False and config.local_logs is False
    assert config.log_level == "INFO"


# --- The per-machine override file ----------------------------------------------------

def write_pair(tmp_path, base, local):
    main = tmp_path / "pom.toml"
    main.write_text(base, encoding="utf-8")
    if local is not None:
        (tmp_path / "pom.local.toml").write_text(local, encoding="utf-8")
    return main


def test_the_local_file_overrides_the_main_file(tmp_path):
    main = write_pair(tmp_path, 'log_level = "INFO"\nlocal_logs = false\n',
                      'log_level = "DEBUG"\nlocal_logs = true\n')
    config = Config.load(main, environ={})
    assert config.log_level == "DEBUG" and config.local_logs is True and config.report.ok


def test_keys_the_local_file_leaves_out_come_from_the_main_file(tmp_path):
    main = write_pair(tmp_path, 'strict = true\nlog_level = "WARNING"\n', 'local_logs = true\n')
    config = Config.load(main, environ={})
    assert config.strict is True and config.log_level == "WARNING" and config.local_logs is True


def test_no_local_file_is_fine_and_silent(tmp_path):
    config = Config.load(write_pair(tmp_path, "strict = true\n", None), environ={})
    assert config.strict is True and config.report.ok


def test_the_local_file_works_even_if_the_main_file_is_missing(tmp_path):
    (tmp_path / "pom.local.toml").write_text('log_level = "DEBUG"\n', encoding="utf-8")
    assert Config.load(tmp_path / "pom.toml", environ={}).log_level == "DEBUG"


def test_the_environment_still_beats_the_local_file(tmp_path):
    main = write_pair(tmp_path, "", 'log_level = "DEBUG"\n')
    assert Config.load(main, environ={"POM_LOG_LEVEL": "ERROR"}).log_level == "ERROR"


def test_a_broken_local_file_warns_and_the_main_file_still_applies(tmp_path):
    main = write_pair(tmp_path, "strict = true\n", "log_level = = oops")
    config = Config.load(main, environ={})
    assert config.strict is True
    assert any("pom.local.toml" in w and "cannot read config file" in w for w in config.report.warnings)


def test_unknown_keys_in_the_local_file_are_warned_about(tmp_path):
    main = write_pair(tmp_path, "", "debgu = true\n")
    assert any("pom.local.toml" in w and "debgu" in w for w in Config.load(main, environ={}).report.warnings)


def test_local_toml_files_are_gitignored():
    from pathlib import Path
    lines = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "*.local.toml" in lines


# --- Recovering from bad input ---------------------------------------------------------

def test_invalid_toml_gives_defaults_and_a_warning(tmp_path):
    config = Config.load(write(tmp_path, "strict = = oops"), environ={})
    assert config.strict is False and config.log_level == "INFO"
    assert any("cannot read config file" in w for w in config.report.warnings)


def test_an_unreadable_path_gives_defaults_and_a_warning(tmp_path):
    config = Config.load(tmp_path, environ={})   # a directory, not a file
    assert config.strict is False
    assert any("cannot read config file" in w for w in config.report.warnings)


def test_unknown_keys_are_ignored_with_a_warning(tmp_path):
    config = Config.load(write(tmp_path, 'strict = true\nstrcit = true\nvolume = 11\n'), environ={})
    assert config.strict is True
    assert any("unknown key" in w and "strcit" in w and "volume" in w for w in config.report.warnings)


@pytest.mark.parametrize("value", ['"maybe"', "7", '"truee"'])
def test_bad_strict_value_in_the_file_means_false_with_a_warning(tmp_path, value):
    config = Config.load(write(tmp_path, f"strict = {value}\n"), environ={})
    assert config.strict is False
    assert any("strict must be true or false" in w for w in config.report.warnings)


@pytest.mark.parametrize("value", ['"LOUD"', "5", '""'])
def test_bad_log_level_in_the_file_means_info_with_a_warning(tmp_path, value):
    config = Config.load(write(tmp_path, f"log_level = {value}\n"), environ={})
    assert config.log_level == "INFO"
    assert any("log_level must be one of" in w for w in config.report.warnings)


def test_bad_environment_values_fall_back_with_a_warning_naming_the_variable():
    config = Config.load(environ={"POM_STRICT": "banana", "POM_LOG_LEVEL": "LOUD"})
    assert config.strict is False and config.log_level == "INFO"
    assert any("POM_STRICT" in w for w in config.report.warnings)
    assert any("POM_LOG_LEVEL" in w for w in config.report.warnings)


def test_a_bad_environment_value_overrides_a_good_file_value_with_the_default(tmp_path):
    """The environment wins even when it is wrong; the fallback is the default, not the file."""
    config = Config.load(write(tmp_path, 'log_level = "DEBUG"\n'), environ={"POM_LOG_LEVEL": "LOUD"})
    assert config.log_level == "INFO"


def test_load_never_raises_whatever_the_input(tmp_path):
    Config.load(write(tmp_path, "\x00\x01 not toml at all"), environ={"POM_STRICT": "?", "POM_LOG_LEVEL": "?"})


def test_problems_are_logged_so_they_reach_journald(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="pom.config"):
        Config.load(write(tmp_path, "log_level = 5"), environ={})
    assert any("log_level must be one of" in r.message for r in caplog.records)
