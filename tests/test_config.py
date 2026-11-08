"""Tests for pom.config: shared settings from a TOML file and the environment, never raising."""
import logging
import re
from pathlib import Path

import pytest

from pom.config import (DEFAULT_SCORER_FAILURE_LIMIT, DEFAULT_SCORER_CLIP_FILTER_SCORE, DEFAULT_SCORER_MIN_CONTENT_WORDS,
                        DEFAULT_SCORER_RETRY_SECONDS, DEFAULT_SCORER_TIMEOUT, DEFAULT_SCORER_URL,
                        Config, ListeningSettings, OracleSettings, ScorerSettings, QualifyingSettings)
import pom.qualifying.bar_keeper
import pom.qualifying.percentile_bar
import pom.qualifying.decaying_quantile_bar
import pom.qualifying.moving_average_bar


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


# --- How many passages per pull ----------------------------------------------------------

def test_samples_default_to_three_to_five():
    config = Config.load(environ={})
    assert config.exemplar_range == (3, 5) and config.report.ok


def test_the_defaults_match_the_bibliomancers_defaults():
    """Config and Bibliomancer each carry the default (neither imports the other), so keep them equal."""
    from pom.library.bibliomancer import DEFAULT_EXEMPLAR_RANGE
    from pom.config import DEFAULT_EXEMPLAR_RANGE as CONFIG_DEFAULT
    assert CONFIG_DEFAULT == DEFAULT_EXEMPLAR_RANGE


def test_samples_are_read_from_the_file_and_the_environment(tmp_path):
    path = write(tmp_path, "[oracle]\nexemplars_min = 2\nexemplars_max = 4\n")
    assert Config.load(path, environ={}).exemplar_range == (2, 4)
    assert Config.load(path, environ={"POM_EXEMPLARS_MIN": "1", "POM_EXEMPLARS_MAX": "6"}).exemplar_range == (1, 6)
    assert Config.load(environ={"POM_EXEMPLARS_MAX": "8"}).exemplar_range == (3, 8)


def test_one_end_alone_can_be_set(tmp_path):
    assert Config.load(write(tmp_path, "[oracle]\nexemplars_max = 9\n"), environ={}).exemplar_range == (3, 9)
    assert Config.load(write(tmp_path, "[oracle]\nexemplars_min = 2\n"), environ={}).exemplar_range == (2, 5)


@pytest.mark.parametrize("value", ["0", "-2", "2.5", "true", '"many"', '""'])
def test_a_bad_samples_value_in_the_file_means_the_default_with_a_warning(tmp_path, value):
    config = Config.load(write(tmp_path, f"[oracle]\nexemplars_max = {value}\n"), environ={})
    assert config.exemplar_range == (3, 5)
    assert any("oracle.exemplars_max must be a whole number" in w for w in config.report.warnings)


@pytest.mark.parametrize("text", ["0", "-1", "2.5", "many", "", "true"])
def test_a_bad_samples_value_in_the_environment_means_the_default_with_a_warning(text):
    config = Config.load(environ={"POM_EXEMPLARS_MIN": text})
    assert config.exemplar_range == (3, 5)
    assert any("POM_EXEMPLARS_MIN" in w for w in config.report.warnings)


def test_a_reversed_pair_is_swapped_with_a_warning(tmp_path):
    config = Config.load(write(tmp_path, "[oracle]\nexemplars_min = 7\nexemplars_max = 2\n"), environ={})
    assert config.exemplar_range == (2, 7)
    assert any("more than oracle.exemplars_max" in w for w in config.report.warnings)


def test_digits_in_a_string_are_accepted_from_the_file_too(tmp_path):
    assert Config.load(write(tmp_path, '[oracle]\nexemplars_min = "2"\n'), environ={}).exemplar_range == (2, 5)


def test_config_samples_plug_straight_into_the_sampler():
    import random
    from pom.library import Passage, Catalog, Circulation, Bibliomancer
    config = Config.load(environ={"POM_EXEMPLARS_MIN": "2", "POM_EXEMPLARS_MAX": "2"})
    store = Catalog.from_passages([Passage(f"a#{i}", "a", "red", f"Passage {i}", 2) for i in range(9)])
    selector = Bibliomancer(store, Circulation.from_dict({}), random.Random(1),
                                exemplar_range=config.exemplar_range)
    assert len(selector.draw()) == 2


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


# --- The [scorer] section --------------------------------------------------------------

def test_scorer_defaults_when_nothing_is_set():
    config = Config.load(environ={})
    assert config.scorer == ScorerSettings() and config.report.ok
    assert config.scorer.url == "http://127.0.0.1:8098/v1/chat/completions"
    assert (config.scorer.timeout, config.scorer.clip_filter_score, config.scorer.min_content_words,
            config.scorer.failure_limit, config.scorer.retry_seconds) == (30.0, 1.0, 1, 3, 30.0)
    assert config.scorer.prompt_scoring == "" and config.scorer.prompt_output == ""
    assert config.scorer.prompt_output_debug == "" and config.scorer.prompt == "" and config.scorer.prompt_debug == ""


def test_the_scorer_defaults_match_the_scorers_own():
    from pom.scoring import clip_scorer, scorer_model_client
    assert DEFAULT_SCORER_TIMEOUT == scorer_model_client.DEFAULT_TIMEOUT
    assert DEFAULT_SCORER_CLIP_FILTER_SCORE == clip_scorer.DEFAULT_CLIP_FILTER_SCORE
    assert DEFAULT_SCORER_MIN_CONTENT_WORDS == clip_scorer.DEFAULT_MIN_CONTENT_WORDS
    assert DEFAULT_SCORER_FAILURE_LIMIT == clip_scorer.DEFAULT_FAILURE_LIMIT
    assert DEFAULT_SCORER_RETRY_SECONDS == clip_scorer.DEFAULT_RETRY_SECONDS
    assert DEFAULT_SCORER_URL.startswith("http://")


def test_scorer_values_are_read_from_the_section(tmp_path):
    text = ('[scorer]\nurl = "http://10.0.0.5:9000/v1/chat/completions"\ntimeout = 4\nclip_filter_score = 2\n'
            'min_content_words = 0\nfailure_limit = 5\nretry_seconds = 12.5\nprompt_scoring = "Score it."\n'
            'prompt_output = "Reply with a number."\nprompt_output_debug = "Reply with a number and why."\n')
    scorer = Config.load(write(tmp_path, text), environ={}).scorer
    assert scorer == ScorerSettings("http://10.0.0.5:9000/v1/chat/completions", 4.0, "Score it.", 2.0, 0, 5, 12.5,
                                    "Reply with a number.", "Reply with a number and why.")


def test_a_multi_line_prompt_is_kept_whole(tmp_path):
    text = '[scorer]\nprompt_scoring = """\nLine one.\n\nLine "two".\n"""\nprompt_output = "Reply."\n'
    config = Config.load(write(tmp_path, text), environ={})
    assert config.scorer.prompt_scoring == 'Line one.\n\nLine "two".\n' and config.report.ok


def test_the_environment_can_point_the_scorer_somewhere_else(tmp_path):
    path = write(tmp_path, '[scorer]\nurl = "http://a.test/v1/chat/completions"\n')
    config = Config.load(path, environ={"POM_SCORER_URL": "http://b.test/v1/chat/completions"})
    assert config.scorer.url == "http://b.test/v1/chat/completions"


@pytest.mark.parametrize("key,value", [
    ("url", "5"), ("url", "true"), ("timeout", '"slow"'), ("timeout", "0"), ("timeout", "-3"), ("timeout", "true"),
    ("prompt_scoring", "5"), ("prompt_scoring", "[1, 2]"), ("prompt_output", "5"), ("prompt_output_debug", "true"), 
    ("clip_filter_score", "-1"), ("clip_filter_score", '"x"'), ("clip_filter_score", "true"),
   
    ("min_content_words", "-1"), ("min_content_words", "1.5"), ("min_content_words", "true"),
    ("failure_limit", "0"), ("failure_limit", "2.5"), ("failure_limit", '"3"'),
    ("retry_seconds", "-5"), ("retry_seconds", '"soon"')])
def test_a_bad_scorer_value_means_the_default_with_a_warning(tmp_path, key, value):
    config = Config.load(write(tmp_path, f"[scorer]\n{key} = {value}\n"), environ={})
    assert config.scorer == ScorerSettings()
    assert len(config.report.warnings) == 1 and f"scorer.{key}" in config.report.warnings[0]


def test_the_smallest_allowed_scorer_values_are_accepted(tmp_path):
    text = "[scorer]\ntimeout = 0.1\nclip_filter_score = 0\nmin_content_words = 0\nfailure_limit = 1\nretry_seconds = 0\n"
    config = Config.load(write(tmp_path, text), environ={})
    assert config.report.ok
    assert (config.scorer.timeout, config.scorer.clip_filter_score, config.scorer.min_content_words,
            config.scorer.failure_limit, config.scorer.retry_seconds) == (0.1, 0.0, 0, 1, 0.0)


def test_an_unknown_scorer_key_is_warned_about(tmp_path):
    config = Config.load(write(tmp_path, "[scorer]\nurll = 5\n"), environ={})
    assert config.scorer == ScorerSettings()
    assert any("scorer.urll" in w for w in config.report.warnings)


def test_an_unknown_section_is_warned_about(tmp_path):
    config = Config.load(write(tmp_path, "[bogus]\nmodel = 5\n"), environ={})
    assert any("bogus.model" in w for w in config.report.warnings)


def test_the_local_file_overrides_one_scorer_key_and_keeps_the_rest(tmp_path):
    write(tmp_path, '[scorer]\ntimeout = 4\nfailure_limit = 5\nprompt_scoring = "Main."\nprompt_output = "Reply."\n')
    (tmp_path / "pom.local.toml").write_text("[scorer]\ntimeout = 9\n", encoding="utf-8")
    scorer = Config.load(tmp_path / "pom.toml", environ={}).scorer
    assert (scorer.timeout, scorer.failure_limit, scorer.prompt_scoring) == (9.0, 5, "Main.")


def test_the_committed_sample_config_has_the_scorer_prompt_and_defaults(tmp_path):
    import shutil
    from pathlib import Path
    sample = Path(__file__).resolve().parents[1] / "config" / "pom.toml"
    copy = tmp_path / "pom.toml"
    shutil.copy(sample, copy)
    config = Config.load(copy, environ={})
    assert config.report.ok
    assert "interesting" in config.scorer.prompt_scoring and "Reply with" in config.scorer.prompt_output
    assert "comma-separated" in config.scorer.prompt_output_debug
    scorer = config.scorer
    assert scorer == ScorerSettings(prompt_scoring=scorer.prompt_scoring, prompt_output=scorer.prompt_output,
                                    prompt_output_debug=scorer.prompt_output_debug)   # the rest is the default
    for text in (scorer.prompt_scoring, scorer.prompt_output, scorer.prompt_output_debug):
        assert '"' not in text.strip()[-1:], "a stray quote ended a prompt"


def test_config_scorer_settings_plug_straight_into_the_client_and_scorer(tmp_path):
    from pom.scoring.scorer_model_client import ScorerModelClient
    from pom.scoring.clip_scorer import ScoredBy, ClipScorer
    from pom.scoring.word_tagger import WordTagger
    config = Config.load(write(tmp_path, '[scorer]\nprompt_scoring = "Score it."\nprompt_output = "Reply."\nclip_filter_score = 2\nfailure_limit = 2\n'), environ={})
    settings = config.scorer
    client = ScorerModelClient(settings.url, settings.prompt, settings.timeout,
                            post=lambda url, body, timeout: {"choices": [{"logprobs": {"content": [
                                {"top_logprobs": [{"token": "6", "logprob": 0.0}]}]}}]})
    scorer = ClipScorer(client, WordTagger(tagger=lambda t: [(w, "NN") for w in t], lemmatizer=lambda w, p: w,
                                      stopwords={"the"}),
                    clip_filter_score=settings.clip_filter_score, min_content_words=settings.min_content_words,
                    failure_limit=settings.failure_limit, retry_seconds=settings.retry_seconds)
    assert client.available and scorer.report.ok
    assert scorer.score_text("The lamp hums.").score == 6.0
    assert scorer.score_text("The").scored_by is ScoredBy.CLIP_FILTER and scorer.score_text("The").score == 2.0


def test_the_scoring_prompt_is_the_scoring_part_plus_the_output_part(tmp_path):
    text = ('[scorer]\nprompt_scoring = """Score it.  \n"""\n'
            'prompt_output = """\nReply with a number.\n"""\nprompt_output_debug = "Explain."\n')
    scorer = Config.load(write(tmp_path, text), environ={}).scorer
    assert scorer.prompt == "Score it.\n\nReply with a number."
    assert scorer.prompt_debug == "Score it.\n\nExplain."


def test_with_no_scoring_part_there_is_no_prompt_at_all(tmp_path):
    scorer = Config.load(write(tmp_path, '[scorer]\nprompt_output = "Reply."\nprompt_output_debug = "Explain."\n'),
                         environ={}).scorer
    assert scorer.prompt == "" and scorer.prompt_debug == ""


def test_a_missing_output_part_is_warned_about_but_the_scoring_part_still_works(tmp_path):
    config = Config.load(write(tmp_path, '[scorer]\nprompt_scoring = "Score it."\n'), environ={})
    assert len(config.report.warnings) == 1 and "prompt_output" in config.report.warnings[0]
    assert config.scorer.prompt == "Score it."


def test_the_old_prompt_key_is_now_an_unknown_key(tmp_path):
    config = Config.load(write(tmp_path, '[scorer]\nprompt = "Score it."\n'), environ={})
    assert any("scorer.prompt" in w for w in config.report.warnings) and config.scorer.prompt == ""


# --- The [qualifying] section ---------------------------------------------------------------

def test_the_qualifying_defaults_when_nothing_is_set():
    config = Config.load(environ={})
    assert config.qualifying == QualifyingSettings() and config.qualifying.bar_percentile == 99.5 and config.report.ok


def test_the_qualifying_defaults_match_the_bars_defaults():
    from pom.qualifying import bar_keeper, decaying_quantile_bar, moving_average_bar, percentile_bar
    s = QualifyingSettings()
    assert (s.bar_percentile, s.window_seconds, s.min_scores, s.starting_bar) == (
        pom.qualifying.percentile_bar.DEFAULT_BAR_PERCENTILE, pom.qualifying.percentile_bar.DEFAULT_WINDOW_SECONDS, pom.qualifying.percentile_bar.DEFAULT_MIN_SCORES,
        pom.qualifying.percentile_bar.DEFAULT_STARTING_BAR)
    assert (s.bar_percentile, s.half_life_seconds, s.starting_bar) == (
        pom.qualifying.decaying_quantile_bar.DEFAULT_BAR_PERCENTILE, pom.qualifying.decaying_quantile_bar.DEFAULT_HALF_LIFE_SECONDS,
        pom.qualifying.decaying_quantile_bar.DEFAULT_STARTING_BAR)
    assert (s.average_fraction, s.average_half_life_scores, s.average_floor_fraction, s.starting_bar) == (
        pom.qualifying.moving_average_bar.DEFAULT_FRACTION, pom.qualifying.moving_average_bar.DEFAULT_HALF_LIFE_SCORES, pom.qualifying.moving_average_bar.DEFAULT_FLOOR_FRACTION,
        pom.qualifying.moving_average_bar.DEFAULT_STARTING_BAR)
    assert s.cooldown_seconds == pom.qualifying.bar_keeper.DEFAULT_COOLDOWN_SECONDS


def test_the_starting_qualifying_values():
    assert QualifyingSettings() == QualifyingSettings(99.5, 300.0, 20, 60.0, 6.8, 0.5, 300.0, 0.25, 10.0, 0.05, "percentile")


def test_all_the_qualifying_values_are_read_from_the_section(tmp_path):
    text = ("[qualifying]\nalgorithm = \"decaying_quantile\"\nbar_percentile = 95\nwindow_seconds = 120\nmin_scores = 8\ncooldown_seconds = 90\n"
            "starting_bar = 5.5\nqualifier_bar_influence = 0.25\nhalf_life_seconds = 180\naverage_fraction = 0.5\n"
            "average_half_life_scores = 4\naverage_floor_fraction = 0.1\n")
    config = Config.load(write(tmp_path, text), environ={})
    assert config.qualifying == QualifyingSettings(95.0, 120.0, 8, 90.0, 5.5, 0.25, 180.0, 0.5, 4.0, 0.1, "decaying_quantile")
    assert config.report.ok


def test_the_typed_line_settings_start_as_a_multiplier_of_one_and_a_half_that_has_no_influence_on_the_bar():
    s = QualifyingSettings()
    assert s.typed_lines_score_multiplier == 1.5 and s.typed_lines_bar_influence == 0.0


def test_the_typed_line_settings_are_read_from_the_section(tmp_path):
    config = Config.load(write(tmp_path, "[qualifying]\ntyped_lines_score_multiplier = 2\ntyped_lines_bar_influence = 0.5\n"), environ={})
    assert (config.qualifying.typed_lines_score_multiplier, config.qualifying.typed_lines_bar_influence) == (2.0, 0.5) and config.report.ok


def test_a_typed_score_multiplier_of_zero_and_one_are_accepted(tmp_path):
    for text, expected in (("0", 0.0), ("1", 1.0)):
        config = Config.load(write(tmp_path, f"[qualifying]\ntyped_lines_score_multiplier = {text}\n"), environ={})
        assert config.qualifying.typed_lines_score_multiplier == expected and config.report.ok


@pytest.mark.parametrize("key, default", [("qualifier_bar_influence", 0.5), ("typed_lines_bar_influence", 0.0)])
@pytest.mark.parametrize("text, expected", [("0", 0.0), ("1", 1.0), ("0.25", 0.25), ("1.1", 1.1), ("-0.1", -0.1), ("2", 2.0), ("-5", -5.0)])
def test_a_bar_influence_is_any_number_and_is_not_limited_to_zero_to_one(tmp_path, key, default, text, expected):
    config = Config.load(write(tmp_path, f"[qualifying]\n{key} = {text}\n"), environ={})
    assert getattr(config.qualifying, key) == expected and config.report.ok


@pytest.mark.parametrize("text, expected", [("0", 0.0), ("5.85", 5.85), ("-2", -2.0), ("9", 9.0), ("12.5", 12.5)])
def test_the_anchor_score_is_any_number(tmp_path, text, expected):
    config = Config.load(write(tmp_path, f"[qualifying]\nanchor_score = {text}\n"), environ={})
    assert config.qualifying.anchor_score == expected and config.report.ok


@pytest.mark.parametrize("text, expected", [("1", 1.0), ("0.25", 0.25), ("2.5", 2.5), ("60", 60.0), ("0.001", 0.001)])
def test_the_anchor_interval_is_any_number_of_minutes_above_zero(tmp_path, text, expected):
    config = Config.load(write(tmp_path, f"[qualifying]\nanchor_interval_minutes = {text}\n"), environ={})
    assert config.qualifying.anchor_interval_minutes == expected and config.report.ok


@pytest.mark.parametrize("text, expected", [("1440", 1440.0), ("40", 40.0), ("0.5", 0.5), ("100000", 100000.0)])
def test_the_discard_age_is_any_number_of_minutes_above_zero(tmp_path, text, expected):
    config = Config.load(write(tmp_path, f"[qualifying]\ndiscard_scores_older_than_minutes = {text}\n"), environ={})
    assert config.qualifying.discard_scores_older_than_minutes == expected and config.report.ok


def test_the_anchor_and_discard_settings_fall_back_to_the_bars_own_fallbacks():
    from pom.qualifying.decaying_quantile_bar import (DEFAULT_ANCHOR_INTERVAL_MINUTES, DEFAULT_ANCHOR_SCORE,
                                                      DEFAULT_DISCARD_SCORES_OLDER_THAN_MINUTES)
    s = QualifyingSettings()
    assert (s.anchor_score, s.anchor_interval_minutes, s.discard_scores_older_than_minutes) == \
        (DEFAULT_ANCHOR_SCORE, DEFAULT_ANCHOR_INTERVAL_MINUTES, DEFAULT_DISCARD_SCORES_OLDER_THAN_MINUTES)


# ---- the settings that used to be constants in modules ----

MOVED = [  # section, key, a good value in TOML, what it reads as, the setting's default, bad values
    ("qualifying", "starting_bar_weight", "7.5", 7.5, 20.0, ["0", "-1", '"20"', "true", "inf", "nan"]),
    ("listening", "max_clip_words", "25", 25, 40, ["0", "-3", "2.5", '"40"', "true"]),
    ("scorer", "warmup_poll_seconds", "2.5", 2.5, 5.0, ["0", "-1", '"5"', "true", "inf"]),
    ("scorer", "warmup_attempts", "6", 6, 3, ["0", "-1", "1.5", '"3"', "true"]),
    ("monitor", "service_check_seconds", "40", 40.0, 15.0, ["0", "-1", '"15"', "true", "inf"]),
    ("monitor", "poll_seconds", "2.5", 2.5, 5.0, ["0", "-1", '"5"', "true", "inf"]),
    ("monitor", "password_on_localhost", "false", False, True, ['"maybe"', '"sideways"', '""']),
    ("monitor", "chart_notebook_max_entries", "1200", 1200, 5000, ["0", "-1", "2.5", '"5000"', "true"]),
    ("terminal", "max_typed_line_characters", "80", 80, 500, ["0", "-1", "2.5", '"500"', "true"]),
    ("terminal", "refused_character_retry_seconds", "0.5", 0.5, 1.0, ["0", "-1", '"1"', "true", "inf"]),
    ("terminal", "wait_log_reminder_seconds", "30", 30.0, 60.0, ["0", "-1", '"60"', "true", "inf"]),
]


@pytest.mark.parametrize("section, key, text, expected, default, bad", MOVED)
def test_a_moved_setting_is_read_and_starts_at_its_old_value(tmp_path, section, key, text, expected, default, bad):
    assert getattr(getattr(Config(), section), key) == default
    config = Config.load(write(tmp_path, f"[{section}]\n{key} = {text}\n"), environ={})
    assert getattr(getattr(config, section), key) == expected and config.report.ok


@pytest.mark.parametrize("section, key, text, expected, default, bad", MOVED)
def test_a_bad_moved_setting_means_its_default_with_a_warning(tmp_path, section, key, text, expected, default, bad):
    for value in bad:
        config = Config.load(write(tmp_path, f"[{section}]\n{key} = {value}\n"), environ={})
        assert getattr(getattr(config, section), key) == default
        assert len(config.report.warnings) == 1 and f"{section}.{key}" in config.report.warnings[0]


def test_the_committed_config_sets_every_moved_setting():
    import tomllib
    with open(Path(__file__).resolve().parents[1] / "config" / "pom.toml", "rb") as f:
        document = tomllib.load(f)
    for section, key, *_ in MOVED:
        assert key in document[section], f"[{section}] {key} is not in config/pom.toml"


# ---- the [display] section ----

DISPLAY_GOOD = [("browser", '"/usr/bin/other"', "/usr/bin/other"), ("browser_flags", '["--a", "--b"]', ("--a", "--b")), ("browser_flags", "[]", ()),
                ("browser_profile_dir", '"/var/kiosk"', "/var/kiosk")]
DISPLAY_BAD = [("browser", '""'), ("browser", '"   "'), ("browser", "5"), ("browser", "true"), ("browser_flags", '"--kiosk"'), ("browser_flags", "[1, 2]"),
               ("browser_flags", '["--a", 3]'), ("browser_flags", "5"), ("browser_profile_dir", '""'), ("browser_profile_dir", "[]")]


def test_the_display_defaults_are_the_shipped_ones():
    from pom.config import DisplaySettings
    d = Config().display
    assert d == DisplaySettings() and d.browser == "chromium" and d.browser_profile_dir == "~/.config/pom-kiosk"
    assert d.browser_flags[0] == "--kiosk" and "--ozone-platform=wayland" in d.browser_flags and isinstance(d.browser_flags, tuple)


@pytest.mark.parametrize("key, text, expected", DISPLAY_GOOD)
def test_a_display_setting_is_read(tmp_path, key, text, expected):
    config = Config.load(write(tmp_path, f"[display]\n{key} = {text}\n"), environ={})
    assert getattr(config.display, key) == expected and config.report.ok


@pytest.mark.parametrize("key, value", DISPLAY_BAD)
def test_a_bad_display_setting_means_the_default_with_a_warning(tmp_path, key, value):
    from pom.config import DisplaySettings
    config = Config.load(write(tmp_path, f"[display]\n{key} = {value}\n"), environ={})
    assert config.display == DisplaySettings()
    assert len(config.report.warnings) == 1 and f"display.{key}" in config.report.warnings[0]


def test_an_unknown_display_key_is_a_warning(tmp_path):
    config = Config.load(write(tmp_path, "[display]\nbrowsr = 1\n"), environ={})
    assert any("display.browsr" in w for w in config.report.warnings)


def test_the_committed_config_sets_every_display_key_to_the_default():
    import tomllib
    from dataclasses import fields
    from pom.config import DisplaySettings
    with open(Path(__file__).resolve().parents[1] / "config" / "pom.toml", "rb") as f:
        section = tomllib.load(f)["display"]
    assert set(section) == {field.name for field in fields(DisplaySettings)}
    assert Config.load(Path(__file__).resolve().parents[1] / "config" / "pom.toml", environ={}).display == DisplaySettings()


# ---- the [chart] section ----

CHART_GOOD = [("window_minutes", "12", 12.0), ("gridline_every_minutes", "2", 2.0), ("score_max", "10", 10.0),
              ("score_gridlines", "[0, 5, 10]", (0.0, 5.0, 10.0)), ("drawing_width", "600", 600.0), ("drawing_height", "120", 120.0),
              ("margin_left", "30", 30.0), ("margin_right", "0", 0.0), ("margin_top", "0", 0.0), ("margin_bottom", "20", 20.0),
              ("attempt_dot_radius", "2.5", 2.5), ("qualifier_dot_radius", "6", 6.0), ("bar_line_width", "2", 2.0),
              ("longest_silence_minutes", "8", 8.0), ("break_mark_size", "9", 9.0), ("break_gap_width", "80", 80.0)]
CHART_BAD = [("window_minutes", "0"), ("window_minutes", '"30"'), ("window_minutes", "true"), ("gridline_every_minutes", "0"),
             ("score_max", "0"), ("score_max", "nan"), ("score_gridlines", "[]"), ("score_gridlines", '["a"]'), ("score_gridlines", "[1, true]"),
             ("score_gridlines", "5"), ("drawing_width", "0"), ("drawing_height", "-5"), ("margin_left", "-1"), ("margin_right", '"6"'),
             ("margin_top", "true"), ("margin_top", "-1"), ("margin_right", "-1"), ("margin_bottom", "-1"), ("margin_bottom", "inf"), ("attempt_dot_radius", "0"), ("qualifier_dot_radius", "-2"), ("bar_line_width", '"1.6"'),
             ("longest_silence_minutes", "0"), ("longest_silence_minutes", "-5"), ("longest_silence_minutes", '"5"'), ("longest_silence_minutes", "inf"),
             ("break_mark_size", "0"), ("break_mark_size", "-1"), ("break_mark_size", "true"), ("break_gap_width", "0"), ("break_gap_width", '"64"'),
             ("break_gap_width", "nan")]


def test_the_chart_starts_at_the_old_hard_coded_numbers():
    from pom.config import ChartSettings
    c = Config().chart
    assert c == ChartSettings() and (c.window_minutes, c.gridline_every_minutes, c.score_max) == (30.0, 5.0, 9.0)
    assert c.score_gridlines == (0.0, 3.0, 6.0, 9.0) and (c.drawing_width, c.drawing_height) == (720.0, 150.0)
    assert (c.margin_left, c.margin_right, c.margin_top, c.margin_bottom) == (24.0, 6.0, 8.0, 18.0)
    assert (c.attempt_dot_radius, c.qualifier_dot_radius, c.bar_line_width) == (1.8, 4.0, 1.6)
    assert (c.longest_silence_minutes, c.break_mark_size, c.break_gap_width) == (5.0, 6.0, 64.0)


@pytest.mark.parametrize("key, text, expected", CHART_GOOD)
def test_a_chart_setting_is_read(tmp_path, key, text, expected):
    config = Config.load(write(tmp_path, f"[chart]\n{key} = {text}\n"), environ={})
    assert getattr(config.chart, key) == expected and config.report.ok


@pytest.mark.parametrize("key, value", CHART_BAD)
def test_a_bad_chart_setting_means_the_default_with_a_warning(tmp_path, key, value):
    from pom.config import ChartSettings
    config = Config.load(write(tmp_path, f"[chart]\n{key} = {value}\n"), environ={})
    assert config.chart == ChartSettings()
    assert len(config.report.warnings) == 1 and f"chart.{key}" in config.report.warnings[0]


def test_margins_that_leave_no_room_for_the_plot_mean_the_default_size_and_margins(tmp_path):
    from pom.config import ChartSettings
    config = Config.load(write(tmp_path, "[chart]\ndrawing_width = 100\nmargin_left = 60\nmargin_right = 40\nbar_line_width = 3\n"
                                         "longest_silence_minutes = 9\nbreak_mark_size = 4\nbreak_gap_width = 50\n"), environ={})
    assert config.chart.drawing_width == 720.0 and config.chart.margin_left == 24.0 and config.chart.bar_line_width == 3.0
    assert (config.chart.longest_silence_minutes, config.chart.break_mark_size, config.chart.break_gap_width) == (9.0, 4.0, 50.0)       # the rest is kept
    assert any("no room for the plot" in w for w in config.report.warnings)
    assert Config.load(write(tmp_path, "[chart]\ndrawing_height = 20\nmargin_top = 10\nmargin_bottom = 10\n"), environ={}).chart.drawing_height == 150.0


def test_the_committed_config_sets_every_chart_key():
    import tomllib
    from dataclasses import fields
    from pom.config import ChartSettings
    with open(Path(__file__).resolve().parents[1] / "config" / "pom.toml", "rb") as f:
        section = tomllib.load(f)["chart"]
    assert set(section) == {field.name for field in fields(ChartSettings)}


def test_the_qualifier_bar_influence_starts_at_a_half():
    assert QualifyingSettings().qualifier_bar_influence == 0.5


@pytest.mark.parametrize("key", ["exclude_qualifiers", "typed_handicap", "typed_lines_affect_bar"])
def test_the_old_names_are_gone_and_are_warned_about_as_unknown_keys(tmp_path, key):
    config = Config.load(write(tmp_path, f"[qualifying]\n{key} = 1\n"), environ={})
    assert any(f"qualifying.{key}" in w for w in config.report.warnings) and config.qualifying == QualifyingSettings()


def test_the_smallest_allowed_qualifying_values_are_accepted(tmp_path):
    text = ("[qualifying]\nbar_percentile = 0.001\nwindow_seconds = 0.5\nmin_scores = 2\ncooldown_seconds = 0\nstarting_bar = -3\n"
            "half_life_seconds = 0.5\naverage_fraction = 0\naverage_half_life_scores = 0.5\naverage_floor_fraction = 0\n")
    config = Config.load(write(tmp_path, text), environ={})
    assert config.qualifying == QualifyingSettings(0.001, 0.5, 2, 0.0, -3.0, 0.5, 0.5, 0.0, 0.5, 0.0, "percentile") and config.report.ok


@pytest.mark.parametrize("key,value", [
    ("bar_percentile", "0"), ("bar_percentile", "-1"), ("bar_percentile", "100.1"), ("bar_percentile", '"99"'), ("bar_percentile", "true"),
    ("bar_percentile", "[99]"), ("bar_percentile", "inf"), ("bar_percentile", "nan"),
    ("window_seconds", "0"), ("window_seconds", "-1"), ("window_seconds", '"300"'), ("window_seconds", "true"),
    ("window_seconds", "inf"), ("window_seconds", "nan"),
    ("min_scores", "1"), ("min_scores", "0"), ("min_scores", "-1"), ("min_scores", "2.5"), ("min_scores", '"5"'),
    ("min_scores", "true"),
    ("cooldown_seconds", "-1"), ("cooldown_seconds", '"60"'), ("cooldown_seconds", "true"),
    ("cooldown_seconds", "nan"),
    ("algorithm", '"median"'), ("algorithm", "5"), ("algorithm", "true"), ("algorithm", '"Percentile"'),
    ("starting_bar", '"6.8"'), ("starting_bar", "true"), ("starting_bar", "inf"), ("starting_bar", "nan"), ("starting_bar", "[6.8]"),
    ("qualifier_bar_influence", '"half"'), ("qualifier_bar_influence", "true"), ("qualifier_bar_influence", "[0.5]"),
    ("qualifier_bar_influence", "inf"), ("qualifier_bar_influence", "nan"),
    ("half_life_seconds", "0"), ("half_life_seconds", "-1"), ("half_life_seconds", '"300"'), ("half_life_seconds", "true"),
    ("half_life_seconds", "inf"),
    ("average_fraction", "-0.1"), ("average_fraction", '"0.25"'), ("average_fraction", "true"), ("average_fraction", "[1]"),
    ("average_half_life_scores", "0"), ("average_half_life_scores", "-2"), ("average_half_life_scores", '"10"'),
    ("average_half_life_scores", "true"), ("average_half_life_scores", "inf"),
    ("average_floor_fraction", "-0.01"), ("average_floor_fraction", "1"), ("average_floor_fraction", "1.5"),
    ("average_floor_fraction", '"0.05"'), ("average_floor_fraction", "true"), ("average_floor_fraction", "false"),
    ("typed_lines_score_multiplier", "-0.1"), ("typed_lines_score_multiplier", '"1.5"'), ("typed_lines_score_multiplier", "true"),
    ("typed_lines_score_multiplier", "[1.5]"),
    ("typed_lines_bar_influence", '"maybe"'), ("typed_lines_bar_influence", "true"), ("typed_lines_bar_influence", "[1]"),
    ("typed_lines_bar_influence", "inf"), ("typed_lines_bar_influence", "nan"),
    ("anchor_score", '"5.85"'), ("anchor_score", "true"), ("anchor_score", "[5.85]"), ("anchor_score", "inf"), ("anchor_score", "nan"),
    ("anchor_interval_minutes", "0"), ("anchor_interval_minutes", "-1"), ("anchor_interval_minutes", '"1"'), ("anchor_interval_minutes", "true"),
    ("anchor_interval_minutes", "inf"), ("anchor_interval_minutes", "nan"),
    ("discard_scores_older_than_minutes", "0"), ("discard_scores_older_than_minutes", "-5"), ("discard_scores_older_than_minutes", '"1440"'),
    ("discard_scores_older_than_minutes", "true"), ("discard_scores_older_than_minutes", "inf"), ("discard_scores_older_than_minutes", "nan")])
def test_a_bad_qualifying_value_means_the_default_with_a_warning(tmp_path, key, value):
    config = Config.load(write(tmp_path, f"[qualifying]\n{key} = {value}\n"), environ={})
    assert config.qualifying == QualifyingSettings()
    assert len(config.report.warnings) == 1 and f"qualifying.{key}" in config.report.warnings[0]


@pytest.mark.parametrize("key", ["floor_ignore_fraction", "turn_fraction", "interest_half_life", "floor_fractoin", "fence_multiple"])
def test_an_unknown_or_removed_qualifying_key_is_warned_about(tmp_path, key):
    config = Config.load(write(tmp_path, f"[qualifying]\n{key} = 0.1\n"), environ={})
    assert any(f"qualifying.{key}" in w for w in config.report.warnings) and config.qualifying == QualifyingSettings()


def test_the_qualifying_section_merges_key_by_key_with_the_local_file(tmp_path):
    main = write(tmp_path, "[qualifying]\nbar_percentile = 95\nmin_scores = 8\n")
    (tmp_path / "pom.local.toml").write_text("[qualifying]\nbar_percentile = 90\nalgorithm = \"moving_average\"\n", encoding="utf-8")
    assert Config.load(main, environ={}).qualifying == QualifyingSettings(bar_percentile=90.0, min_scores=8, algorithm="moving_average")


def test_the_qualifying_settings_plug_straight_into_the_bar_keeper(tmp_path):
    from pom.qualifying import bar_keeper_from_settings, Ruling
    text = "[qualifying]\nbar_percentile = 60\nwindow_seconds = 60\nmin_scores = 5\nstarting_bar = 1000\ncooldown_seconds = 0\n"
    settings = Config.load(write(tmp_path, text), environ={}).qualifying
    bar_keeper = bar_keeper_from_settings(settings, started_at=0.0)
    assert bar_keeper.report.ok
    assert [bar_keeper.rule(score, now) for now, score in enumerate((1, 2, 3, 4, 5))] == [Ruling.DID_NOT_QUALIFY] * 5
    assert bar_keeper.bar_at(5) == 3.0                     # 60% of five scores 1 to 5: the third
    assert bar_keeper.rule(3.5, 5) is Ruling.QUALIFIED


@pytest.mark.parametrize("algorithm", ["percentile", "decaying_quantile", "moving_average"])
def test_every_algorithm_builds_from_the_settings_the_config_makes(tmp_path, algorithm):
    from pom.qualifying import ALGORITHMS, bar_keeper_from_settings
    settings = Config.load(write(tmp_path, f'[qualifying]\nalgorithm = "{algorithm}"\n'), environ={}).qualifying
    bar_keeper = bar_keeper_from_settings(settings, started_at=0.0)
    assert isinstance(bar_keeper._bar, ALGORITHMS[algorithm]) and bar_keeper.report.ok and bar_keeper._bar.report.ok


def test_the_shipped_config_loads_cleanly_with_usable_qualifying_values():
    """The qualifying values are tuned often, so they are not pinned: only that the shipped file reads without a warning
    and that each algorithm accepts the values."""
    import dataclasses
    from pom.qualifying import ALGORITHMS, bar_keeper_from_settings
    shipped = Config.load(Path(__file__).parent.parent / "config" / "pom.toml", environ={})
    assert shipped.report.ok
    for algorithm in ALGORITHMS:
        bar_keeper = bar_keeper_from_settings(dataclasses.replace(shipped.qualifying, algorithm=algorithm), started_at=0.0)
        assert bar_keeper.report.ok and bar_keeper._bar.report.ok


def test_the_shipped_algorithm_is_one_the_factory_knows():
    """Which algorithm is shipped is a tuning choice, so it is not pinned."""
    from pom.qualifying import ALGORITHMS
    assert Config.load(Path(__file__).parent.parent / "config" / "pom.toml", environ={}).qualifying.algorithm in ALGORITHMS


# --- The [listening] section ---------------------------------------------------------------

KNOWN_GOOD = ListeningSettings("http://127.0.0.1:8110", 180.0, "plughw:0,0", 10.0, 5.0, 2.0, 0.0, 30.0, 3, 10.0, 6, False)


def test_the_listening_defaults_when_nothing_is_set():
    config = Config.load(environ={})
    assert config.listening == ListeningSettings() and config.report.ok


def test_the_starting_listening_values_are_the_known_good_ones():
    assert ListeningSettings() == KNOWN_GOOD


def test_the_listening_defaults_match_the_modules_defaults():
    from pom.listening import eavesdropper, mic_deck, rolling_cutter, whisper_model_client
    assert ListeningSettings() == ListeningSettings(
        whisper_model_client.DEFAULT_URL, whisper_model_client.DEFAULT_TIMEOUT, mic_deck.DEFAULT_DEVICE,
        mic_deck.DEFAULT_BUFFER_SECONDS, rolling_cutter.DEFAULT_CUT_EVERY_SECONDS, rolling_cutter.DEFAULT_HOLD_BACK_SECONDS,
        rolling_cutter.DEFAULT_REWIND_SECONDS, rolling_cutter.DEFAULT_MAX_CUT_SECONDS, eavesdropper.DEFAULT_ROOM_TONE_PASS_EVERY,
        eavesdropper.DEFAULT_ROOM_TONE_PASS_SECONDS, eavesdropper.DEFAULT_ROOM_TONE_MEMORY)


def test_all_the_listening_values_are_read_from_the_section(tmp_path):
    text = ("[listening]\nwhisper_url = \"http://pi.test:9000\"\nwhisper_timeout = 60\ndevice = \"hw:1,0\"\n"
            "buffer_seconds = 4\ncut_every_seconds = 3\nhold_back_seconds = 1.5\nrewind_seconds = 0.5\nmax_cut_seconds = 20\nroom_tone_pass_every = 5\n"
            "room_tone_pass_seconds = 8\nroom_tone_memory = 9\nscore_room_tone = true\n")
    config = Config.load(write(tmp_path, text), environ={})
    assert config.listening == ListeningSettings("http://pi.test:9000", 60.0, "hw:1,0", 4.0, 3.0, 1.5, 0.5, 20.0, 5, 8.0, 9, True)
    assert config.report.ok


def test_the_smallest_allowed_listening_values_are_accepted(tmp_path):
    text = "[listening]\nhold_back_seconds = 0\nrewind_seconds = 0\nroom_tone_pass_every = 0\nroom_tone_memory = 1\n"
    config = Config.load(write(tmp_path, text), environ={})
    assert config.listening.hold_back_seconds == 0.0 and config.listening.rewind_seconds == 0.0
    assert config.listening.room_tone_pass_every == 0 and config.listening.room_tone_memory == 1 and config.report.ok


@pytest.mark.parametrize("key,value", [
    ("whisper_url", "5"), ("whisper_url", "true"), ("whisper_url", "[1]"),
    ("whisper_timeout", "0"), ("whisper_timeout", "-1"), ("whisper_timeout", '"180"'), ("whisper_timeout", "true"),
    ("whisper_timeout", "inf"),
    ("device", "5"), ("device", "true"),
    ("buffer_seconds", "0"), ("buffer_seconds", "-1"), ("buffer_seconds", '"10"'), ("buffer_seconds", "true"),
    ("cut_every_seconds", "0"), ("cut_every_seconds", "-1"), ("cut_every_seconds", '"5"'), ("cut_every_seconds", "true"), ("cut_every_seconds", "nan"),
    ("hold_back_seconds", "-1"), ("hold_back_seconds", '"2"'), ("hold_back_seconds", "true"),
    ("rewind_seconds", "-0.5"), ("rewind_seconds", '"0"'), ("rewind_seconds", "false"),
    ("max_cut_seconds", "0"), ("max_cut_seconds", "-30"), ("max_cut_seconds", '"30"'), ("max_cut_seconds", "true"),
    ("room_tone_pass_every", "-1"), ("room_tone_pass_every", "2.5"), ("room_tone_pass_every", '"3"'), ("room_tone_pass_every", "true"),
    ("room_tone_pass_seconds", "0"), ("room_tone_pass_seconds", "-10"), ("room_tone_pass_seconds", '"10"'), ("room_tone_pass_seconds", "true"),
    ("room_tone_memory", "0"), ("room_tone_memory", "-1"), ("room_tone_memory", "2.5"), ("room_tone_memory", '"6"'),
    ("room_tone_memory", "true"),
    ("score_room_tone", "5"), ("score_room_tone", '"maybe"'), ("score_room_tone", "[true]")])
def test_a_bad_listening_value_means_the_default_with_a_warning(tmp_path, key, value):
    config = Config.load(write(tmp_path, f"[listening]\n{key} = {value}\n"), environ={})
    assert config.listening == ListeningSettings()
    assert len(config.report.warnings) == 1 and f"listening.{key}" in config.report.warnings[0]


def test_an_unknown_listening_key_is_warned_about(tmp_path):
    config = Config.load(write(tmp_path, "[listening]\nstepp = 5\n"), environ={})
    assert any("listening.stepp" in w for w in config.report.warnings) and config.listening == ListeningSettings()


def test_the_listening_section_merges_key_by_key_with_the_local_file(tmp_path):
    main = write(tmp_path, "[listening]\ncut_every_seconds = 3\nhold_back_seconds = 1\n")
    (tmp_path / "pom.local.toml").write_text("[listening]\nhold_back_seconds = 0.5\n", encoding="utf-8")
    listening = Config.load(main, environ={}).listening
    assert (listening.cut_every_seconds, listening.hold_back_seconds) == (3.0, 0.5)


def test_the_whisper_address_can_come_from_the_environment(tmp_path):
    main = write(tmp_path, '[listening]\nwhisper_url = "http://from-the-file.test"\n')
    assert Config.load(main, environ={}).listening.whisper_url == "http://from-the-file.test"
    assert Config.load(main, environ={"POM_WHISPER_URL": "http://from-the-env.test"}).listening.whisper_url == \
        "http://from-the-env.test"


def test_the_shipped_config_has_the_known_good_listening_values():
    """The combination that worked in the room is fragile, so a change to it must be on purpose: change this test with it."""
    shipped = Config.load(Path(__file__).parent.parent / "config" / "pom.toml", environ={})
    assert shipped.listening == KNOWN_GOOD and shipped.report.ok


def test_the_listening_settings_plug_straight_into_the_modules():
    from pom.listening import Eavesdropper, RollingCutter, WhisperModelClient

    class NoSound:
        def frames(self):
            return iter(())

        def close(self):
            pass

    settings = Config.load(environ={}).listening
    client = WhisperModelClient(settings.whisper_url, settings.whisper_timeout)
    cutter = RollingCutter(client, settings.cut_every_seconds, settings.hold_back_seconds, settings.rewind_seconds, settings.max_cut_seconds)
    eavesdropper = Eavesdropper(NoSound(), client, settings.cut_every_seconds, settings.hold_back_seconds, settings.rewind_seconds, settings.max_cut_seconds,
                        settings.room_tone_pass_every, settings.room_tone_pass_seconds, settings.room_tone_memory)
    assert client.report.ok and cutter.report.ok and eavesdropper.report.ok


# --- The [oracle] section --------------------------------------------------------------

def test_oracle_defaults_when_nothing_is_set():
    config = Config.load(environ={})
    assert config.oracle == OracleSettings() and config.report.ok
    assert config.oracle.url == "http://127.0.0.1:8099/v1/chat/completions"
    assert (config.oracle.timeout, config.oracle.max_tokens, config.oracle.temperature) == (240.0, 300, 0.8)
    assert config.oracle.prompt == ""


def test_the_exemplar_counts_live_in_the_oracle_section_and_the_old_top_level_keys_are_unknown(tmp_path):
    config = Config.load(write(tmp_path, "exemplars_min = 2\n"), environ={})
    assert config.exemplar_range == (3, 5) and any("exemplars_min" in w for w in config.report.warnings)


def test_the_local_file_overrides_the_prompt_and_keeps_the_exemplar_counts(tmp_path):
    write(tmp_path, '[oracle]\nexemplars_min = 2\nexemplars_max = 4\nprompt = "Main. {hinge}"\n')
    (tmp_path / "pom.local.toml").write_text('[oracle]\nprompt = "Mine. {hinge}"\n', encoding="utf-8")
    config = Config.load(tmp_path / "pom.toml", environ={})
    assert config.oracle.prompt == "Mine. {hinge}" and config.exemplar_range == (2, 4)


def test_the_oracle_defaults_match_the_oracle_clients_own():
    from pom.config import DEFAULT_ORACLE_MAX_TOKENS, DEFAULT_ORACLE_TEMPERATURE, DEFAULT_ORACLE_TIMEOUT
    from pom.writing import oracle_model_client
    assert DEFAULT_ORACLE_TIMEOUT == oracle_model_client.DEFAULT_TIMEOUT
    assert DEFAULT_ORACLE_MAX_TOKENS == oracle_model_client.DEFAULT_MAX_TOKENS
    assert DEFAULT_ORACLE_TEMPERATURE == oracle_model_client.DEFAULT_TEMPERATURE


def test_oracle_values_are_read_from_the_section(tmp_path):
    text = ('[oracle]\nurl = "http://10.0.0.5:9100/v1/chat/completions"\ntimeout = 30\nprompt = "Speak. {hinge}"\n'
            "max_tokens = 200\ntemperature = 1.1\n")
    config = Config.load(write(tmp_path, text), environ={})
    assert config.oracle == OracleSettings("http://10.0.0.5:9100/v1/chat/completions", 30.0, "Speak. {hinge}", 200, 1.1)
    assert config.report.ok


def test_a_multiline_oracle_prompt_keeps_its_line_breaks_and_braces(tmp_path):
    text = '[oracle]\nprompt = """\nLine one.\n\n{exemplars}\n\nThey said: {hinge}\n"""\n'
    config = Config.load(write(tmp_path, text), environ={})
    assert config.oracle.prompt == "Line one.\n\n{exemplars}\n\nThey said: {hinge}\n" and config.report.ok


@pytest.mark.parametrize("key,value", [
    ("url", "5"), ("url", "true"), ("timeout", '"slow"'), ("timeout", "0"), ("timeout", "-3"), ("timeout", "true"),
    ("prompt", "5"), ("prompt", "[1, 2]"), ("max_tokens", "0"), ("max_tokens", "2.5"), ("max_tokens", '"300"'),
    ("max_tokens", "true"), ("temperature", "-0.1"), ("temperature", '"hot"'), ("temperature", "true")])
def test_a_bad_oracle_value_means_the_default_with_a_warning(tmp_path, key, value):
    config = Config.load(write(tmp_path, f"[oracle]\n{key} = {value}\n"), environ={})
    assert config.oracle == OracleSettings()
    assert len(config.report.warnings) == 1 and f"oracle.{key}" in config.report.warnings[0]


def test_the_smallest_allowed_oracle_values_are_accepted(tmp_path):
    config = Config.load(write(tmp_path, "[oracle]\ntimeout = 0.1\nmax_tokens = 1\ntemperature = 0\n"), environ={})
    assert (config.oracle.timeout, config.oracle.max_tokens, config.oracle.temperature) == (0.1, 1, 0.0)
    assert config.report.ok


def test_an_unknown_oracle_key_is_warned_about(tmp_path):
    config = Config.load(write(tmp_path, "[oracle]\npromt = 5\n"), environ={})
    assert config.oracle == OracleSettings()
    assert any("oracle.promt" in w for w in config.report.warnings)


def test_the_local_file_overrides_one_oracle_key_and_keeps_the_rest(tmp_path):
    write(tmp_path, '[oracle]\ntemperature = 0.5\nmax_tokens = 100\nprompt = "Main. {hinge}"\n')
    (tmp_path / "pom.local.toml").write_text("[oracle]\ntemperature = 1.0\n", encoding="utf-8")
    oracle = Config.load(tmp_path / "pom.toml", environ={}).oracle
    assert (oracle.temperature, oracle.max_tokens, oracle.prompt) == (1.0, 100, "Main. {hinge}")


def test_the_committed_sample_config_has_an_oracle_prompt_that_the_builder_accepts(tmp_path):
    from pathlib import Path
    from pom.writing import PromptBuilder
    sample = Path(__file__).resolve().parents[1] / "config" / "pom.toml"
    copy = tmp_path / "pom.toml"
    copy.write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")
    config = Config.load(copy, environ={})
    assert config.report.ok and config.oracle.prompt.strip()
    assert PromptBuilder(config.oracle.prompt, selector=None).report.ok


@pytest.mark.parametrize("value,expected", [("true", True), ("false", False), ('"yes"', True), ('"off"', False)])
def test_score_room_tone_takes_a_boolean_or_an_on_off_word(tmp_path, value, expected):
    config = Config.load(write(tmp_path, f"[listening]\nscore_room_tone = {value}\n"), environ={})
    assert config.listening.score_room_tone is expected and config.report.ok


def test_room_tone_sounds_are_not_considered_unless_asked():
    assert Config.load(environ={}).listening.score_room_tone is False


# --- The [monitor] section -----------------------------------------------------------------

def test_the_monitor_defaults_when_nothing_is_set():
    from pom.config import MonitorSettings
    config = Config.load(environ={})
    assert config.monitor == MonitorSettings() and config.report.ok
    assert (config.monitor.enabled, config.monitor.host, config.monitor.port) == (False, "0.0.0.0", 8080)
    assert (config.monitor.password, config.monitor.show_hinges, config.monitor.history_seconds) == ("", True, 3600.0)


def test_all_the_monitor_values_are_read_from_the_section(tmp_path):
    from pom.config import MonitorSettings
    text = ('[monitor]\nenabled = true\nhost = "127.0.0.1"\nport = 9090\npassword = "s3cret"\nshow_hinges = false\n'
            'history_seconds = 1800\n')
    config = Config.load(write(tmp_path, text), environ={})
    assert config.monitor == MonitorSettings(True, "127.0.0.1", 9090, "s3cret", False, 1800.0) and config.report.ok


def test_the_smallest_and_largest_ports_are_accepted(tmp_path):
    assert Config.load(write(tmp_path, "[monitor]\nport = 1\n"), environ={}).monitor.port == 1
    assert Config.load(write(tmp_path, "[monitor]\nport = 65535\n"), environ={}).monitor.port == 65535


@pytest.mark.parametrize("key,value", [
    ("enabled", "5"), ("enabled", '"maybe"'), ("enabled", "[true]"),
    ("host", "5"), ("host", "true"),
    ("port", "0"), ("port", "65536"), ("port", "-1"), ("port", "80.5"), ("port", '"8080"'), ("port", "true"),
    ("password", "5"), ("password", "true"),
    ("history_seconds", "0"), ("history_seconds", "-5"), ("history_seconds", '"3600"'), ("history_seconds", "true"),
    ("history_seconds", "inf")])
def test_a_bad_monitor_value_means_the_default_with_a_warning(tmp_path, key, value):
    from pom.config import MonitorSettings
    config = Config.load(write(tmp_path, f"[monitor]\n{key} = {value}\n"), environ={})
    assert config.monitor == MonitorSettings()
    assert len(config.report.warnings) == 1 and f"monitor.{key}" in config.report.warnings[0]


@pytest.mark.parametrize("value", ["5", '"maybe"', "[true]"])
def test_a_bad_show_hinges_value_hides_the_hinges_and_warns(tmp_path, value):
    """A flag that cannot be read leans toward privacy: the hinge text is hidden."""
    config = Config.load(write(tmp_path, f"[monitor]\nshow_hinges = {value}\n"), environ={})
    assert config.monitor.show_hinges is False
    assert len(config.report.warnings) == 1 and "monitor.show_hinges" in config.report.warnings[0]


@pytest.mark.parametrize("value,expected", [("true", True), ("false", False), ('"on"', True), ('"no"', False)])
def test_monitor_flags_take_a_boolean_or_an_on_off_word(tmp_path, value, expected):
    config = Config.load(write(tmp_path, f"[monitor]\nenabled = {value}\nshow_hinges = {value}\n"), environ={})
    assert config.monitor.enabled is expected and config.monitor.show_hinges is expected and config.report.ok


def test_an_unknown_monitor_key_is_warned_about(tmp_path):
    from pom.config import MonitorSettings
    config = Config.load(write(tmp_path, "[monitor]\npasword = \"x\"\n"), environ={})
    assert any("monitor.pasword" in w for w in config.report.warnings) and config.monitor == MonitorSettings()


def test_the_monitor_section_merges_key_by_key_with_the_local_file(tmp_path):
    main = write(tmp_path, '[monitor]\nenabled = true\nport = 9000\npassword = ""\n')
    (tmp_path / "pom.local.toml").write_text('[monitor]\npassword = "from-the-local-file"\n', encoding="utf-8")
    monitor = Config.load(main, environ={}).monitor
    assert (monitor.enabled, monitor.port, monitor.password) == (True, 9000, "from-the-local-file")


def test_the_shipped_monitor_section_is_off_and_has_no_password():
    """The committed file goes to a public repository and the Pi: the site must be off and the password empty. Read the committed
    file alone (a pom.local.toml next to it, which git ignores, may well hold a password on this machine)."""
    import tomllib
    with open(Path(__file__).parent.parent / "config" / "pom.toml", "rb") as committed:
        monitor = tomllib.load(committed)["monitor"]
    assert monitor["enabled"] is False and monitor["password"] == ""


# --- The [printing] section ------------------------------------------------------------------

def test_the_output_defaults_are_a_65_character_line_for_letter_paper_at_10_pitch():
    from pom.config import PrintingSettings
    config = Config.load(environ={})
    assert config.printing == PrintingSettings() and config.report.ok
    assert (config.printing.paper_width_inches, config.printing.margin_inches, config.printing.characters_per_inch) == (8.5, 1.0, 10.0)
    assert (config.printing.quote_indent, config.printing.linefeeds_after_portent, config.printing.line_width) == (4, 4, 65)


def test_all_the_output_values_are_read_from_the_section(tmp_path):
    from pom.config import PrintingSettings
    text = ("[printing]\npaper_width_inches = 11\nmargin_inches = 0.5\ncharacters_per_inch = 12\n"
            "quote_indent = 6\nlinefeeds_after_portent = 2\n")
    config = Config.load(write(tmp_path, text), environ={})
    assert config.printing == PrintingSettings(11.0, 0.5, 12.0, 6, 2) and config.report.ok
    assert config.printing.line_width == 120


@pytest.mark.parametrize("cpi, width", [(10, 65), (12, 78), (15, 97)])
def test_the_line_width_for_letter_paper_with_1_inch_margins_at_each_pitch(tmp_path, cpi, width):
    config = Config.load(write(tmp_path, f"[printing]\ncharacters_per_inch = {cpi}\n"), environ={})
    assert config.printing.line_width == width


def test_zero_for_the_indent_and_the_line_feeds_is_allowed(tmp_path):
    config = Config.load(write(tmp_path, "[printing]\nquote_indent = 0\nlinefeeds_after_portent = 0\nmargin_inches = 0\n"), environ={})
    assert (config.printing.quote_indent, config.printing.linefeeds_after_portent, config.printing.margin_inches) == (0, 0, 0.0)
    assert config.report.ok


@pytest.mark.parametrize("key, value", [
    ("paper_width_inches", "0"), ("paper_width_inches", '"wide"'), ("margin_inches", "-1"), ("margin_inches", "true"),
    ("characters_per_inch", "0"), ("characters_per_inch", '"ten"'), ("quote_indent", "-1"), ("quote_indent", "2.5"),
    ("linefeeds_after_portent", "-1"), ("linefeeds_after_portent", "true"),
])
def test_a_bad_output_value_means_the_default_with_a_warning(tmp_path, key, value):
    from pom.config import PrintingSettings
    config = Config.load(write(tmp_path, f"[printing]\n{key} = {value}\n"), environ={})
    assert config.printing == PrintingSettings()
    assert len(config.report.warnings) == 1 and f"printing.{key}" in config.report.warnings[0]


def test_margins_that_leave_too_short_a_line_mean_the_default_paper_and_pitch_with_a_warning(tmp_path):
    from pom.config import PrintingSettings
    config = Config.load(write(tmp_path, "[printing]\nmargin_inches = 4\nquote_indent = 6\nlinefeeds_after_portent = 1\n"), environ={})
    assert config.printing == PrintingSettings(quote_indent=6, linefeeds_after_portent=1)
    assert len(config.report.warnings) == 1 and "20" in config.report.warnings[0]


def test_a_line_of_exactly_the_shortest_width_is_allowed(tmp_path):
    config = Config.load(write(tmp_path, "[printing]\npaper_width_inches = 4\nmargin_inches = 1\ncharacters_per_inch = 10\n"), environ={})
    assert config.printing.line_width == 20 and config.report.ok


def test_the_committed_config_sets_every_output_key():
    import tomllib
    with open(Path(__file__).resolve().parents[1] / "config" / "pom.toml", "rb") as f:
        section = tomllib.load(f)["printing"]
    assert set(section) == {"paper_width_inches", "margin_inches", "characters_per_inch", "quote_indent", "linefeeds_after_portent",
                              "keep_composed_characters", "ascii_printwheel"}


def test_the_line_width_is_not_lost_to_floating_point_rounding(tmp_path):
    # (8 - 2 x 2.2) x 15 comes to 53.99999999999999 in floating point; the line is 54 characters
    text = "[printing]\npaper_width_inches = 8\nmargin_inches = 2.2\ncharacters_per_inch = 15\n"
    assert Config.load(write(tmp_path, text), environ={}).printing.line_width == 54


def test_a_config_built_without_output_settings_has_the_defaults():
    from pom.config import PrintingSettings
    assert Config().printing == PrintingSettings()


# --- The [printing] composed characters ------------------------------------------------------

def test_by_default_no_composed_character_is_kept_and_the_wheel_is_not_ascii():
    printing = Config.load(environ={}).printing
    assert (printing.keep_composed_characters, printing.ascii_printwheel, printing.composed_characters_kept) == ("", False, "")


def test_the_listed_composed_characters_are_read_and_are_the_ones_kept(tmp_path):
    config = Config.load(write(tmp_path, '[printing]\nkeep_composed_characters = "^|"\n'), environ={})
    assert config.printing.keep_composed_characters == "^|" and config.printing.composed_characters_kept == "^|" and config.report.ok


def test_an_ascii_printwheel_keeps_all_the_composed_characters_whatever_is_listed(tmp_path):
    from pom.config import COMPOSED_CHARACTERS
    config = Config.load(write(tmp_path, '[printing]\nascii_printwheel = true\nkeep_composed_characters = "^"\n'), environ={})
    assert config.printing.ascii_printwheel is True and config.printing.composed_characters_kept == COMPOSED_CHARACTERS
    assert COMPOSED_CHARACTERS == "\\^`{|}~"


def test_a_character_that_is_not_swappable_in_the_keep_list_is_left_out_with_a_warning(tmp_path):
    config = Config.load(write(tmp_path, '[printing]\nkeep_composed_characters = "<a^^|"\n'), environ={})
    assert config.printing.keep_composed_characters == "^|"
    assert len(config.report.warnings) == 1 and "printing.keep_composed_characters" in config.report.warnings[0]


def test_a_keep_list_that_is_not_text_means_the_default_with_a_warning(tmp_path):
    config = Config.load(write(tmp_path, "[printing]\nkeep_composed_characters = 5\n"), environ={})
    assert config.printing.keep_composed_characters == ""
    assert len(config.report.warnings) == 1 and "printing.keep_composed_characters" in config.report.warnings[0]


def test_a_bad_ascii_printwheel_value_means_false_with_a_warning(tmp_path):
    config = Config.load(write(tmp_path, '[printing]\nascii_printwheel = "maybe"\n'), environ={})
    assert config.printing.ascii_printwheel is False
    assert len(config.report.warnings) == 1 and "printing.ascii_printwheel" in config.report.warnings[0]


def test_the_fallback_for_a_too_short_line_keeps_the_composed_settings(tmp_path):
    text = '[printing]\nmargin_inches = 4\nkeep_composed_characters = "|"\nascii_printwheel = true\n'
    printing = Config.load(write(tmp_path, text), environ={}).printing
    assert printing.keep_composed_characters == "|" and printing.ascii_printwheel is True and printing.line_width == 65


# --- The [terminal] section ----------------------------------------------------------------

def test_the_terminal_defaults_are_the_cadetwriters_own():
    from pom.config import TerminalSettings
    config = Config.load(environ={})
    assert config.terminal == TerminalSettings() and config.report.ok
    terminal = config.terminal
    assert (terminal.port, terminal.baud, terminal.flow_control, terminal.end_of_line) == ("/dev/ttyACM0", 9600, "xon_xoff", "crlf")
    assert (terminal.characters_per_second, terminal.finish_wait_seconds) == (16.0, 2.0)
    assert (terminal.echoplex, terminal.type_during_printing) == (True, False)
    assert (terminal.stall_patience_seconds, terminal.reconnect_seconds, terminal.mode) == (600.0, 2.0, "serial")


def test_all_the_terminal_values_are_read_from_the_section(tmp_path):
    from pom.config import TerminalSettings
    text = ('[terminal]\nport = "/dev/ttyUSB0"\nbaud = 115200\nflow_control = "rts_cts"\nend_of_line = "lf"\n'
            "characters_per_second = 12.5\nfinish_wait_seconds = 0\nechoplex = false\ntype_during_printing = true\n"
            "stall_patience_seconds = 90\nreconnect_seconds = 0.5\nmode = \"console\"\n")
    config = Config.load(write(tmp_path, text), environ={})
    assert config.terminal == TerminalSettings("/dev/ttyUSB0", 115200, "rts_cts", "lf", 12.5, 0.0, False, True, 90.0, 0.5, "console") \
        and config.report.ok


@pytest.mark.parametrize("key, value", [
    ("port", "5"), ("baud", "1234"), ("baud", '"9600"'), ("baud", "true"), ("baud", "9600.0"), ("flow_control", '"hardware"'),
    ("end_of_line", '"newline"'), ("characters_per_second", "0"), ("characters_per_second", '"fast"'),
    ("finish_wait_seconds", "-1"), ("finish_wait_seconds", "true"), ("echoplex", '"maybe"'), ("type_during_printing", "3"),
    ("stall_patience_seconds", "0"), ("stall_patience_seconds", '"long"'), ("reconnect_seconds", "-2"), ("reconnect_seconds", "true"),
    ("mode", '"paper"'), ("mode", "3"),
])
def test_a_bad_terminal_value_means_the_default_with_a_warning(tmp_path, key, value):
    from pom.config import TerminalSettings
    config = Config.load(write(tmp_path, f"[terminal]\n{key} = {value}\n"), environ={})
    assert config.terminal == TerminalSettings()
    assert len(config.report.warnings) == 1 and f"terminal.{key}" in config.report.warnings[0]


@pytest.mark.parametrize("baud", [50, 9600, 230400])
def test_every_standard_baud_rate_is_accepted(tmp_path, baud):
    assert Config.load(write(tmp_path, f"[terminal]\nbaud = {baud}\n"), environ={}).terminal.baud == baud


def test_the_terminal_choices_and_defaults_are_the_ones_the_port_uses():
    from pom import config as settings
    from pom.printing import terminal_port as port
    assert settings.TERMINAL_BAUD_RATES == port.BAUD_RATES and settings.TERMINAL_FLOW_CONTROLS == port.FLOW_CONTROLS
    assert settings.TERMINAL_END_OF_LINES == tuple(port.END_OF_LINES)
    assert (settings.DEFAULT_TERMINAL_BAUD, settings.DEFAULT_TERMINAL_FLOW_CONTROL, settings.DEFAULT_TERMINAL_END_OF_LINE) == (
        port.DEFAULT_BAUD, port.DEFAULT_FLOW_CONTROL, port.DEFAULT_END_OF_LINE)
    assert (settings.DEFAULT_TERMINAL_CHARACTERS_PER_SECOND, settings.DEFAULT_TERMINAL_FINISH_WAIT_SECONDS) == (
        port.DEFAULT_CHARACTERS_PER_SECOND, port.DEFAULT_FINISH_WAIT_SECONDS)
    assert (settings.DEFAULT_TERMINAL_STALL_PATIENCE_SECONDS, settings.DEFAULT_TERMINAL_RECONNECT_SECONDS) == (
        port.DEFAULT_STALL_PATIENCE_SECONDS, port.DEFAULT_RECONNECT_SECONDS)


def test_the_committed_config_sets_every_terminal_key():
    import tomllib
    with open(Path(__file__).resolve().parents[1] / "config" / "pom.toml", "rb") as f:
        section = tomllib.load(f)["terminal"]
    assert set(section) == {"port", "baud", "flow_control", "end_of_line", "characters_per_second", "finish_wait_seconds",
                            "echoplex", "type_during_printing", "stall_patience_seconds", "reconnect_seconds", "mode",
                            "max_typed_line_characters", "refused_character_retry_seconds", "wait_log_reminder_seconds"}


def test_a_config_built_without_terminal_settings_has_the_defaults():
    from pom.config import TerminalSettings
    assert Config().terminal == TerminalSettings()


def test_a_bad_flag_falls_back_to_that_flags_own_default_with_the_right_word_in_the_warning(tmp_path):
    config = Config.load(write(tmp_path, '[terminal]\nechoplex = "maybe"\n[monitor]\nenabled = "maybe"\n'), environ={})
    assert config.terminal.echoplex is True and config.monitor.enabled is False
    assert any("terminal.echoplex" in w and "using true" in w for w in config.report.warnings)
    assert any("monitor.enabled" in w and "using false" in w for w in config.report.warnings)


# --- The [conductor] section ---------------------------------------------------------------

CONDUCTOR_KEYS = ("writing_seconds", "printing_seconds", "recovering_seconds", "freshness_seconds", "write_attempts", "write_pause_seconds",
                  "write_budget_seconds", "print_pause_seconds", "restart_pause_seconds", "check_seconds")


def test_the_conductor_defaults_err_long():
    from pom.config import ConductorSettings
    config = Config.load(write_nothing_in(), environ={})
    assert config.conductor == ConductorSettings() and config.report.ok
    c = config.conductor
    assert (c.writing_seconds, c.printing_seconds, c.recovering_seconds, c.freshness_seconds) == (300.0, 180.0, 3.0, 360.0)
    assert (c.write_attempts, c.write_pause_seconds, c.write_budget_seconds, c.print_pause_seconds) == (3, 10.0, 270.0, 10.0)
    assert (c.restart_pause_seconds, c.check_seconds) == (5.0, 1.0)


def write_nothing_in():
    return Path("/nonexistent/pom.toml")


def test_all_the_conductor_values_are_read_from_the_section(tmp_path):
    from pom.config import ConductorSettings
    text = ("[conductor]\nwriting_seconds = 500\nprinting_seconds = 400\nrecovering_seconds = 0\nfreshness_seconds = 120\n"
            "write_attempts = 5\nwrite_pause_seconds = 0\nwrite_budget_seconds = 450\nprint_pause_seconds = 2\n"
            "restart_pause_seconds = 0\ncheck_seconds = 0.25\n")
    config = Config.load(write(tmp_path, text), environ={})
    assert config.conductor == ConductorSettings(500.0, 400.0, 0.0, 120.0, 5, 0.0, 450.0, 2.0, 0.0, 0.25) and config.report.ok


@pytest.mark.parametrize("key, value", [
    ("writing_seconds", "0"), ("writing_seconds", '"long"'), ("printing_seconds", "-1"), ("recovering_seconds", "-1"),
    ("recovering_seconds", "true"), ("freshness_seconds", "0"), ("write_attempts", "0"), ("write_attempts", "2.5"),
    ("write_attempts", '"3"'), ("write_pause_seconds", "-1"), ("write_budget_seconds", "0"), ("print_pause_seconds", "0"),
    ("restart_pause_seconds", "-1"), ("check_seconds", "0"), ("check_seconds", "true"),
])
def test_a_bad_conductor_value_means_the_default_with_a_warning(tmp_path, key, value):
    from pom.config import ConductorSettings
    config = Config.load(write(tmp_path, f"[conductor]\n{key} = {value}\n"), environ={})
    assert config.conductor == ConductorSettings()
    assert len(config.report.warnings) == 1 and f"conductor.{key}" in config.report.warnings[0]


def test_the_committed_config_sets_every_conductor_key_and_fits_its_own_limits():
    import tomllib
    path = Path(__file__).resolve().parents[1] / "config" / "pom.toml"
    with open(path, "rb") as f:
        section = tomllib.load(f)["conductor"]
    assert set(section) == set(CONDUCTOR_KEYS)
    assert Config.load(path, environ={}).report.ok                      # no warning: the shipped limits fit the shipped settings


def test_the_conductor_defaults_in_the_code_are_the_ones_the_parts_use():
    from pom import config as settings
    from pom.conductor import floor_keeper, turn_taker
    from pom.conductor import conductor
    assert (settings.DEFAULT_CONDUCTOR_WRITING_SECONDS, settings.DEFAULT_CONDUCTOR_PRINTING_SECONDS,
            settings.DEFAULT_CONDUCTOR_RECOVERING_SECONDS) == (floor_keeper.DEFAULT_WRITING_SECONDS, floor_keeper.DEFAULT_PRINTING_SECONDS,
                                                               floor_keeper.DEFAULT_RECOVERING_SECONDS)
    assert (settings.DEFAULT_CONDUCTOR_FRESHNESS_SECONDS, settings.DEFAULT_CONDUCTOR_WRITE_ATTEMPTS, settings.DEFAULT_CONDUCTOR_WRITE_PAUSE_SECONDS,
            settings.DEFAULT_CONDUCTOR_WRITE_BUDGET_SECONDS, settings.DEFAULT_CONDUCTOR_PRINT_PAUSE_SECONDS) == (
        turn_taker.DEFAULT_FRESHNESS_SECONDS, turn_taker.DEFAULT_WRITE_ATTEMPTS, turn_taker.DEFAULT_WRITE_PAUSE_SECONDS,
        turn_taker.DEFAULT_WRITE_BUDGET_SECONDS, turn_taker.DEFAULT_PRINT_PAUSE_SECONDS)
    assert (settings.DEFAULT_CONDUCTOR_RESTART_PAUSE_SECONDS, settings.DEFAULT_CONDUCTOR_CHECK_SECONDS) == (
        conductor.DEFAULT_RESTART_PAUSE_SECONDS, conductor.DEFAULT_CHECK_SECONDS)


class TestTheLimitsFitTheirSettings:
    """A watchdog shorter than what it watches cuts off healthy turns, so the limits are checked against the settings they depend on."""

    def warnings(self, tmp_path, text):
        return Config.load(write(tmp_path, text), environ={}).report.warnings

    def test_a_writing_limit_not_longer_than_the_oracle_timeout_is_warned_about(self, tmp_path):
        warnings = self.warnings(tmp_path, "[oracle]\ntimeout = 300\n[conductor]\nwriting_seconds = 300\n")
        assert any("conductor.writing_seconds (300) should be longer than oracle.timeout (300)" in w for w in warnings)

    def test_a_writing_limit_longer_than_the_oracle_timeout_is_fine(self, tmp_path):
        assert self.warnings(tmp_path, "[oracle]\ntimeout = 100\n[conductor]\nwriting_seconds = 300\n") == []

    def test_a_budget_not_shorter_than_the_writing_limit_is_warned_about(self, tmp_path):
        warnings = self.warnings(tmp_path, "[conductor]\nwriting_seconds = 300\nwrite_budget_seconds = 300\n")
        assert any("write_budget_seconds (300) should be shorter than conductor.writing_seconds (300)" in w for w in warnings)

    def test_a_budget_shorter_than_the_oracle_timeout_is_warned_about(self, tmp_path):
        warnings = self.warnings(tmp_path, "[conductor]\nwrite_budget_seconds = 100\n")
        assert any("so no second attempt to write can ever fit" in w for w in warnings)

    def test_a_second_attempt_needs_the_pause_as_well_as_a_whole_attempt(self, tmp_path):
        base = "[oracle]\ntimeout = 240\n[conductor]\nwrite_pause_seconds = 10\nwriting_seconds = 400\n"
        assert any("can ever fit" in w for w in self.warnings(tmp_path, base + "write_budget_seconds = 249\n"))
        assert not any("can ever fit" in w for w in self.warnings(tmp_path, base + "write_budget_seconds = 250\n"))

    def test_a_printing_limit_shorter_than_the_longest_portent_is_warned_about(self, tmp_path):
        warnings = self.warnings(tmp_path, "[conductor]\nprinting_seconds = 60\n")
        assert any("printing_seconds (60) is shorter than the time to type the longest portent (94 s for 300 tokens" in w for w in warnings)

    def test_a_slow_typewriter_makes_the_printing_limit_too_short(self, tmp_path):
        warnings = self.warnings(tmp_path, "[terminal]\ncharacters_per_second = 4\n")
        assert any("conductor.printing_seconds" in w for w in warnings)

    def test_with_no_printer_the_printing_limit_does_not_matter(self, tmp_path):
        assert self.warnings(tmp_path, '[terminal]\nmode = "none"\n[conductor]\nprinting_seconds = 1\n') == []

    def test_the_values_are_kept_as_given(self, tmp_path):
        config = Config.load(write(tmp_path, "[oracle]\ntimeout = 300\n[conductor]\nwriting_seconds = 100\n"), environ={})
        assert config.conductor.writing_seconds == 100.0 and config.oracle.timeout == 300.0

    def test_a_config_built_without_conductor_settings_has_the_defaults(self):
        from pom.config import ConductorSettings
        assert Config().conductor == ConductorSettings()


# --- [oracle] exemplar_redraws ----------------------------------------------------------

def test_exemplar_redraws_defaults_to_fifty_and_the_shipped_config_says_so():
    assert OracleSettings().exemplar_redraws == 50
    assert Config.load(environ={}).oracle.exemplar_redraws == 50
    shipped = Config.load(Path(__file__).resolve().parents[1] / "config" / "pom.toml", environ={})
    assert shipped.oracle.exemplar_redraws == 50 and not shipped.report.warnings


@pytest.mark.parametrize("value,expected", [("0", 0), ("1", 1), ("200", 200)])
def test_exemplar_redraws_takes_any_whole_number_from_zero(tmp_path, value, expected):
    config = Config.load(write(tmp_path, f"[oracle]\nexemplar_redraws = {value}\n"), environ={})
    assert config.oracle.exemplar_redraws == expected and config.report.ok


@pytest.mark.parametrize("value", ["-1", "2.5", '"50"', "true"])
def test_a_bad_exemplar_redraws_means_the_default_with_a_warning(tmp_path, value):
    config = Config.load(write(tmp_path, f"[oracle]\nexemplar_redraws = {value}\n"), environ={})
    assert config.oracle.exemplar_redraws == 50
    assert len(config.report.warnings) == 1 and "oracle.exemplar_redraws" in config.report.warnings[0]


# --- The shipped config and the local template -------------------------------------------

def _keys(path):
    """Every key in a TOML file as 'section.key' (or just 'key' above the first section), with its value."""
    import tomllib
    flat = {}
    for name, value in tomllib.loads(Path(path).read_text(encoding="utf-8")).items():
        if isinstance(value, dict):
            flat.update({f"{name}.{key}": inner for key, inner in value.items()})
        else:
            flat[name] = value
    return flat


CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def test_every_key_the_local_template_sets_is_also_in_the_shipped_config():
    """Someone installing reads config/pom.toml to learn what can be set, so a key a local file sets must be there too."""
    shipped, template = _keys(CONFIG_DIR / "pom.toml"), _keys(CONFIG_DIR / "pom.local.toml.example")
    assert template, "the template sets nothing"
    assert not (set(template) - set(shipped)), f"in the template but not in pom.toml: {sorted(set(template) - set(shipped))}"


def test_the_shipped_config_and_the_template_hold_no_secret():
    """pom.toml is committed to a public repository. A key that looks like a secret must be empty in both files."""
    for name in ("pom.toml", "pom.local.toml.example"):
        for key, value in _keys(CONFIG_DIR / name).items():
            if re.search(r"(^|[._])(password|passwd|secret|token|api_?key)$", key.lower()):      # not "max_tokens", which is a count
                assert value == "", f"{name}: {key} must be empty, not a real value"


def test_the_template_has_the_password_empty_so_the_page_stays_off_until_one_is_chosen():
    template = _keys(CONFIG_DIR / "pom.local.toml.example")
    assert template["monitor.password"] == "" and template["monitor.enabled"] is True


def test_the_template_loads_cleanly_over_the_shipped_config(tmp_path):
    config = Config.load(CONFIG_DIR / "pom.local.toml.example", environ={})
    assert config.report.ok
    assert config.monitor.enabled is True and config.monitor.password == ""


def test_the_shipped_config_says_how_to_make_a_local_file():
    text = (CONFIG_DIR / "pom.toml").read_text(encoding="utf-8")
    assert "cp config/pom.local.toml.example config/pom.local.toml" in text and "public" in text
    assert (CONFIG_DIR / "pom.local.toml.example").exists()


# --- The [boot_slip] section ----------------------------------------------------------------

BOOT_SLIP_KEYS = ("enabled", "text", "address_grace_seconds", "retry_seconds")


def test_the_boot_slip_defaults_type_the_slip_with_a_short_grace():
    from pom.config import BootSlipSettings
    config = Config.load(write_nothing_in(), environ={})
    assert config.boot_slip == BootSlipSettings() and config.report.ok
    s = config.boot_slip
    assert (s.enabled, s.address_grace_seconds, s.retry_seconds) == (True, 15.0, 5.0)
    assert s.text.splitlines()[0] == "CADETWRITER - IBM-1620" and "{address}" in s.text


def test_all_the_boot_slip_values_are_read_from_the_section(tmp_path):
    from pom.config import BootSlipSettings
    text = '[boot_slip]\nenabled = false\ntext = "HELLO {hostname}\\n{address}"\naddress_grace_seconds = 0\nretry_seconds = 2.5\n'
    config = Config.load(write(tmp_path, text), environ={})
    assert config.boot_slip == BootSlipSettings(False, "HELLO {hostname}\n{address}", 0.0, 2.5) and config.report.ok


@pytest.mark.parametrize("key, value", [
    ("enabled", '"yes please"'), ("enabled", "7"), ("text", "5"), ("text", "true"), ("address_grace_seconds", "-1"),
    ("address_grace_seconds", '"soon"'), ("retry_seconds", "-1"), ("retry_seconds", "true"),
])
def test_a_bad_boot_slip_value_means_the_default_with_a_warning(tmp_path, key, value):
    from pom.config import BootSlipSettings
    config = Config.load(write(tmp_path, f"[boot_slip]\n{key} = {value}\n"), environ={})
    assert config.boot_slip == BootSlipSettings()
    assert len(config.report.warnings) == 1 and f"boot_slip.{key}" in config.report.warnings[0]


def test_the_committed_config_sets_every_boot_slip_key_and_the_slip_is_the_default_one():
    import tomllib
    from pom.config import BootSlipSettings
    path = Path(__file__).resolve().parents[1] / "config" / "pom.toml"
    with open(path, "rb") as f:
        section = tomllib.load(f)["boot_slip"]
    assert set(section) == set(BOOT_SLIP_KEYS)
    config = Config.load(path, environ={})
    assert config.report.ok
    assert config.boot_slip.text.strip() == BootSlipSettings().text         # the code's default and the shipped words agree
    assert "{address}" in config.boot_slip.text


def test_the_committed_config_lets_the_printers_own_display_in():
    import tomllib
    with open(Path(__file__).resolve().parents[1] / "config" / "pom.toml", "rb") as f:
        assert tomllib.load(f)["monitor"]["password_on_localhost"] is False


def test_the_code_default_asks_everyone_for_the_password():
    from pom.config import MonitorSettings
    assert Config().monitor.password_on_localhost is True and MonitorSettings().password_on_localhost is True
