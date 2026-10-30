"""Tests for pom.config: shared settings from a TOML file and the environment, never raising."""
import logging
from pathlib import Path

import pytest

from pom.config import (DEFAULT_SCORER_FAILURE_LIMIT, DEFAULT_SCORER_FRAGMENT_FILTER_SCORE, DEFAULT_SCORER_MIN_CONTENT_WORDS,
                        DEFAULT_SCORER_RETRY_SECONDS, DEFAULT_SCORER_TIMEOUT, DEFAULT_SCORER_URL,
                        Config, ListeningSettings, OracleSettings, ScorerSettings, TriggerSettings)


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


# --- How many chunks per pull ----------------------------------------------------------

def test_samples_default_to_three_to_five():
    config = Config.load(environ={})
    assert config.exemplar_range == (3, 5) and config.report.ok


def test_the_defaults_match_the_exemplar_selectors_defaults():
    """Config and ExemplarSelector each carry the default (neither imports the other), so keep them equal."""
    from pom.corpus.exemplar_selector import DEFAULT_EXEMPLAR_RANGE
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
    from pom.corpus import Chunk, ChunkStore, DrawWeights, ExemplarSelector
    config = Config.load(environ={"POM_EXEMPLARS_MIN": "2", "POM_EXEMPLARS_MAX": "2"})
    store = ChunkStore.from_chunks([Chunk(f"a#{i}", "a", "red", f"Chunk {i}", 2) for i in range(9)])
    selector = ExemplarSelector(store, DrawWeights.from_dict({}), random.Random(1),
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
    assert (config.scorer.timeout, config.scorer.fragment_filter_score, config.scorer.min_content_words,
            config.scorer.failure_limit, config.scorer.retry_seconds) == (15.0, 1.0, 1, 3, 30.0)
    assert config.scorer.prompt_scoring == "" and config.scorer.prompt_output == ""
    assert config.scorer.prompt_output_debug == "" and config.scorer.prompt == "" and config.scorer.prompt_debug == ""


def test_the_scorer_defaults_match_the_scorers_own():
    from pom.scoring import scorer, scorer_model_client
    assert DEFAULT_SCORER_TIMEOUT == scorer_model_client.DEFAULT_TIMEOUT
    assert DEFAULT_SCORER_FRAGMENT_FILTER_SCORE == scorer.DEFAULT_FRAGMENT_FILTER_SCORE
    assert DEFAULT_SCORER_MIN_CONTENT_WORDS == scorer.DEFAULT_MIN_CONTENT_WORDS
    assert DEFAULT_SCORER_FAILURE_LIMIT == scorer.DEFAULT_FAILURE_LIMIT
    assert DEFAULT_SCORER_RETRY_SECONDS == scorer.DEFAULT_RETRY_SECONDS
    assert DEFAULT_SCORER_URL.startswith("http://")


def test_scorer_values_are_read_from_the_section(tmp_path):
    text = ('[scorer]\nurl = "http://10.0.0.5:9000/v1/chat/completions"\ntimeout = 4\nfragment_filter_score = 2\n'
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
    ("fragment_filter_score", "-1"), ("fragment_filter_score", '"x"'), ("fragment_filter_score", "true"),
   
    ("min_content_words", "-1"), ("min_content_words", "1.5"), ("min_content_words", "true"),
    ("failure_limit", "0"), ("failure_limit", "2.5"), ("failure_limit", '"3"'),
    ("retry_seconds", "-5"), ("retry_seconds", '"soon"')])
def test_a_bad_scorer_value_means_the_default_with_a_warning(tmp_path, key, value):
    config = Config.load(write(tmp_path, f"[scorer]\n{key} = {value}\n"), environ={})
    assert config.scorer == ScorerSettings()
    assert len(config.report.warnings) == 1 and f"scorer.{key}" in config.report.warnings[0]


def test_the_smallest_allowed_scorer_values_are_accepted(tmp_path):
    text = "[scorer]\ntimeout = 0.1\nfragment_filter_score = 0\nmin_content_words = 0\nfailure_limit = 1\nretry_seconds = 0\n"
    config = Config.load(write(tmp_path, text), environ={})
    assert config.report.ok
    assert (config.scorer.timeout, config.scorer.fragment_filter_score, config.scorer.min_content_words,
            config.scorer.failure_limit, config.scorer.retry_seconds) == (0.1, 0.0, 0, 1, 0.0)


def test_an_unknown_scorer_key_is_warned_about(tmp_path):
    config = Config.load(write(tmp_path, "[scorer]\nurll = 5\n"), environ={})
    assert config.scorer == ScorerSettings()
    assert any("scorer.urll" in w for w in config.report.warnings)


def test_an_unknown_section_is_warned_about(tmp_path):
    config = Config.load(write(tmp_path, "[voice]\nmodel = 5\n"), environ={})
    assert any("voice.model" in w for w in config.report.warnings)


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
    from pom.scoring.scorer import ScoredBy, Scorer
    from pom.scoring.word_tagger import WordTagger
    config = Config.load(write(tmp_path, '[scorer]\nprompt_scoring = "Score it."\nprompt_output = "Reply."\nfragment_filter_score = 2\nfailure_limit = 2\n'), environ={})
    settings = config.scorer
    client = ScorerModelClient(settings.url, settings.prompt, settings.timeout,
                            post=lambda url, body, timeout: {"choices": [{"logprobs": {"content": [
                                {"top_logprobs": [{"token": "6", "logprob": 0.0}]}]}}]})
    scorer = Scorer(client, WordTagger(tagger=lambda t: [(w, "NN") for w in t], lemmatizer=lambda w, p: w,
                                      stopwords={"the"}),
                    fragment_filter_score=settings.fragment_filter_score, min_content_words=settings.min_content_words,
                    failure_limit=settings.failure_limit, retry_seconds=settings.retry_seconds)
    assert client.available and scorer.report.ok
    assert scorer.score_text("The lamp hums.").score == 6.0
    assert scorer.score_text("The").scored_by is ScoredBy.FRAGMENT_FILTER and scorer.score_text("The").score == 2.0


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


# --- The [trigger] section ---------------------------------------------------------------

def test_the_trigger_defaults_when_nothing_is_set():
    config = Config.load(environ={})
    assert config.trigger == TriggerSettings() and config.trigger.percentile == 99.5 and config.report.ok


def test_the_trigger_defaults_match_the_bars_defaults():
    from pom.scoring import convo_interest_tracker, decaying_quantile_bar, moving_average_bar, percentile_bar
    s = TriggerSettings()
    assert (s.percentile, s.window_seconds, s.min_scores, s.starting_bar) == (
        percentile_bar.DEFAULT_PERCENTILE, percentile_bar.DEFAULT_WINDOW_SECONDS, percentile_bar.DEFAULT_MIN_SCORES,
        percentile_bar.DEFAULT_STARTING_BAR)
    assert (s.percentile, s.half_life_seconds, s.starting_bar) == (
        decaying_quantile_bar.DEFAULT_PERCENTILE, decaying_quantile_bar.DEFAULT_HALF_LIFE_SECONDS,
        decaying_quantile_bar.DEFAULT_STARTING_BAR)
    assert (s.average_fraction, s.average_half_life_scores, s.average_floor_fraction, s.starting_bar) == (
        moving_average_bar.DEFAULT_FRACTION, moving_average_bar.DEFAULT_HALF_LIFE_SCORES, moving_average_bar.DEFAULT_FLOOR_FRACTION,
        moving_average_bar.DEFAULT_STARTING_BAR)
    assert s.cooldown_seconds == convo_interest_tracker.DEFAULT_COOLDOWN_SECONDS


def test_the_starting_trigger_values():
    assert TriggerSettings() == TriggerSettings(99.5, 300.0, 20, 60.0, 6.8, False, 300.0, 0.25, 10.0, 0.05, "percentile")


def test_all_the_trigger_values_are_read_from_the_section(tmp_path):
    text = ("[trigger]\nalgorithm = \"decaying_quantile\"\npercentile = 95\nwindow_seconds = 120\nmin_scores = 8\ncooldown_seconds = 90\n"
            "starting_bar = 5.5\nexclude_triggers = true\nhalf_life_seconds = 180\naverage_fraction = 0.5\n"
            "average_half_life_scores = 4\naverage_floor_fraction = 0.1\n")
    config = Config.load(write(tmp_path, text), environ={})
    assert config.trigger == TriggerSettings(95.0, 120.0, 8, 90.0, 5.5, True, 180.0, 0.5, 4.0, 0.1, "decaying_quantile")
    assert config.report.ok


def test_the_smallest_allowed_trigger_values_are_accepted(tmp_path):
    text = ("[trigger]\npercentile = 0.001\nwindow_seconds = 0.5\nmin_scores = 2\ncooldown_seconds = 0\nstarting_bar = -3\n"
            "half_life_seconds = 0.5\naverage_fraction = 0\naverage_half_life_scores = 0.5\naverage_floor_fraction = 0\n")
    config = Config.load(write(tmp_path, text), environ={})
    assert config.trigger == TriggerSettings(0.001, 0.5, 2, 0.0, -3.0, False, 0.5, 0.0, 0.5, 0.0, "percentile") and config.report.ok


@pytest.mark.parametrize("key,value", [
    ("percentile", "0"), ("percentile", "-1"), ("percentile", "100.1"), ("percentile", '"99"'), ("percentile", "true"),
    ("percentile", "[99]"), ("percentile", "inf"), ("percentile", "nan"),
    ("window_seconds", "0"), ("window_seconds", "-1"), ("window_seconds", '"300"'), ("window_seconds", "true"),
    ("window_seconds", "inf"), ("window_seconds", "nan"),
    ("min_scores", "1"), ("min_scores", "0"), ("min_scores", "-1"), ("min_scores", "2.5"), ("min_scores", '"5"'),
    ("min_scores", "true"),
    ("cooldown_seconds", "-1"), ("cooldown_seconds", '"60"'), ("cooldown_seconds", "true"),
    ("cooldown_seconds", "nan"),
    ("algorithm", '"median"'), ("algorithm", "5"), ("algorithm", "true"), ("algorithm", '"Percentile"'),
    ("starting_bar", '"6.8"'), ("starting_bar", "true"), ("starting_bar", "inf"), ("starting_bar", "nan"), ("starting_bar", "[6.8]"),
    ("exclude_triggers", "5"), ("exclude_triggers", '"maybe"'), ("exclude_triggers", "[true]"),
    ("half_life_seconds", "0"), ("half_life_seconds", "-1"), ("half_life_seconds", '"300"'), ("half_life_seconds", "true"),
    ("half_life_seconds", "inf"),
    ("average_fraction", "-0.1"), ("average_fraction", '"0.25"'), ("average_fraction", "true"), ("average_fraction", "[1]"),
    ("average_half_life_scores", "0"), ("average_half_life_scores", "-2"), ("average_half_life_scores", '"10"'),
    ("average_half_life_scores", "true"), ("average_half_life_scores", "inf"),
    ("average_floor_fraction", "-0.01"), ("average_floor_fraction", "1"), ("average_floor_fraction", "1.5"),
    ("average_floor_fraction", '"0.05"'), ("average_floor_fraction", "true"), ("average_floor_fraction", "false")])
def test_a_bad_trigger_value_means_the_default_with_a_warning(tmp_path, key, value):
    config = Config.load(write(tmp_path, f"[trigger]\n{key} = {value}\n"), environ={})
    assert config.trigger == TriggerSettings()
    assert len(config.report.warnings) == 1 and f"trigger.{key}" in config.report.warnings[0]


@pytest.mark.parametrize("key", ["floor_ignore_fraction", "trigger_fraction", "interest_half_life", "floor_fractoin", "fence_multiple"])
def test_an_unknown_or_removed_trigger_key_is_warned_about(tmp_path, key):
    config = Config.load(write(tmp_path, f"[trigger]\n{key} = 0.1\n"), environ={})
    assert any(f"trigger.{key}" in w for w in config.report.warnings) and config.trigger == TriggerSettings()


def test_the_trigger_section_merges_key_by_key_with_the_local_file(tmp_path):
    main = write(tmp_path, "[trigger]\npercentile = 95\nmin_scores = 8\n")
    (tmp_path / "pom.local.toml").write_text("[trigger]\npercentile = 90\nalgorithm = \"moving_average\"\n", encoding="utf-8")
    assert Config.load(main, environ={}).trigger == TriggerSettings(percentile=90.0, min_scores=8, algorithm="moving_average")


def test_the_trigger_settings_plug_straight_into_the_tracker(tmp_path):
    from pom.scoring import ScoreEffect, tracker_from_settings
    text = "[trigger]\npercentile = 60\nwindow_seconds = 60\nmin_scores = 5\nstarting_bar = 1000\ncooldown_seconds = 0\n"
    settings = Config.load(write(tmp_path, text), environ={}).trigger
    tracker = tracker_from_settings(settings)
    assert tracker.report.ok
    assert [tracker.record(score, now) for now, score in enumerate((1, 2, 3, 4, 5))] == [ScoreEffect.COUNTED] * 5
    assert tracker.trigger_level_at(5) == 3.0                     # 60% of five scores 1 to 5: the third
    assert tracker.record(3.5, 5) is ScoreEffect.TRIGGERED


@pytest.mark.parametrize("algorithm", ["percentile", "decaying_quantile", "moving_average"])
def test_every_algorithm_builds_from_the_settings_the_config_makes(tmp_path, algorithm):
    from pom.scoring import ALGORITHMS, tracker_from_settings
    settings = Config.load(write(tmp_path, f'[trigger]\nalgorithm = "{algorithm}"\n'), environ={}).trigger
    tracker = tracker_from_settings(settings)
    assert isinstance(tracker._bar, ALGORITHMS[algorithm]) and tracker.report.ok and tracker._bar.report.ok


def test_the_shipped_config_loads_cleanly_with_usable_trigger_values():
    """The trigger values are tuned often, so they are not pinned: only that the shipped file reads without a warning
    and that each algorithm accepts the values."""
    import dataclasses
    from pom.scoring import ALGORITHMS, tracker_from_settings
    shipped = Config.load(Path(__file__).parent.parent / "config" / "pom.toml", environ={})
    assert shipped.report.ok
    for algorithm in ALGORITHMS:
        tracker = tracker_from_settings(dataclasses.replace(shipped.trigger, algorithm=algorithm))
        assert tracker.report.ok and tracker._bar.report.ok


def test_the_shipped_algorithm_is_one_the_factory_knows():
    """Which algorithm is shipped is a tuning choice, so it is not pinned."""
    from pom.scoring import ALGORITHMS
    assert Config.load(Path(__file__).parent.parent / "config" / "pom.toml", environ={}).trigger.algorithm in ALGORITHMS


# --- The [listening] section ---------------------------------------------------------------

KNOWN_GOOD = ListeningSettings("http://127.0.0.1:8110", 180.0, "plughw:0,0", 10.0, 5.0, 2.0, 0.0, 30.0, 3, 10.0, 6, False)


def test_the_listening_defaults_when_nothing_is_set():
    config = Config.load(environ={})
    assert config.listening == ListeningSettings() and config.report.ok


def test_the_starting_listening_values_are_the_known_good_ones():
    assert ListeningSettings() == KNOWN_GOOD


def test_the_listening_defaults_match_the_modules_defaults():
    from pom.listening import listener, mic_source, rolling_cutter, whisper_server_client
    assert ListeningSettings() == ListeningSettings(
        whisper_server_client.DEFAULT_URL, whisper_server_client.DEFAULT_TIMEOUT, mic_source.DEFAULT_DEVICE,
        mic_source.DEFAULT_BUFFER_SECONDS, rolling_cutter.DEFAULT_STEP, rolling_cutter.DEFAULT_HOLD,
        rolling_cutter.DEFAULT_REWIND, rolling_cutter.DEFAULT_MAX_BUFFER, listener.DEFAULT_SOUND_EVERY_STEPS,
        listener.DEFAULT_SOUND_SECONDS, listener.DEFAULT_AMBIENT_MEMORY)


def test_all_the_listening_values_are_read_from_the_section(tmp_path):
    text = ("[listening]\nwhisper_url = \"http://pi.test:9000\"\nwhisper_timeout = 60\ndevice = \"hw:1,0\"\n"
            "buffer_seconds = 4\nstep = 3\nhold = 1.5\nrewind = 0.5\nmax_buffer = 20\nsound_every_steps = 5\n"
            "sound_seconds = 8\nambient_memory = 9\nconsider_ambient_sounds = true\n")
    config = Config.load(write(tmp_path, text), environ={})
    assert config.listening == ListeningSettings("http://pi.test:9000", 60.0, "hw:1,0", 4.0, 3.0, 1.5, 0.5, 20.0, 5, 8.0, 9, True)
    assert config.report.ok


def test_the_smallest_allowed_listening_values_are_accepted(tmp_path):
    text = "[listening]\nhold = 0\nrewind = 0\nsound_every_steps = 0\nambient_memory = 1\n"
    config = Config.load(write(tmp_path, text), environ={})
    assert config.listening.hold == 0.0 and config.listening.rewind == 0.0
    assert config.listening.sound_every_steps == 0 and config.listening.ambient_memory == 1 and config.report.ok


@pytest.mark.parametrize("key,value", [
    ("whisper_url", "5"), ("whisper_url", "true"), ("whisper_url", "[1]"),
    ("whisper_timeout", "0"), ("whisper_timeout", "-1"), ("whisper_timeout", '"180"'), ("whisper_timeout", "true"),
    ("whisper_timeout", "inf"),
    ("device", "5"), ("device", "true"),
    ("buffer_seconds", "0"), ("buffer_seconds", "-1"), ("buffer_seconds", '"10"'), ("buffer_seconds", "true"),
    ("step", "0"), ("step", "-1"), ("step", '"5"'), ("step", "true"), ("step", "nan"),
    ("hold", "-1"), ("hold", '"2"'), ("hold", "true"),
    ("rewind", "-0.5"), ("rewind", '"0"'), ("rewind", "false"),
    ("max_buffer", "0"), ("max_buffer", "-30"), ("max_buffer", '"30"'), ("max_buffer", "true"),
    ("sound_every_steps", "-1"), ("sound_every_steps", "2.5"), ("sound_every_steps", '"3"'), ("sound_every_steps", "true"),
    ("sound_seconds", "0"), ("sound_seconds", "-10"), ("sound_seconds", '"10"'), ("sound_seconds", "true"),
    ("ambient_memory", "0"), ("ambient_memory", "-1"), ("ambient_memory", "2.5"), ("ambient_memory", '"6"'),
    ("ambient_memory", "true"),
    ("consider_ambient_sounds", "5"), ("consider_ambient_sounds", '"maybe"'), ("consider_ambient_sounds", "[true]")])
def test_a_bad_listening_value_means_the_default_with_a_warning(tmp_path, key, value):
    config = Config.load(write(tmp_path, f"[listening]\n{key} = {value}\n"), environ={})
    assert config.listening == ListeningSettings()
    assert len(config.report.warnings) == 1 and f"listening.{key}" in config.report.warnings[0]


def test_an_unknown_listening_key_is_warned_about(tmp_path):
    config = Config.load(write(tmp_path, "[listening]\nstepp = 5\n"), environ={})
    assert any("listening.stepp" in w for w in config.report.warnings) and config.listening == ListeningSettings()


def test_the_listening_section_merges_key_by_key_with_the_local_file(tmp_path):
    main = write(tmp_path, "[listening]\nstep = 3\nhold = 1\n")
    (tmp_path / "pom.local.toml").write_text("[listening]\nhold = 0.5\n", encoding="utf-8")
    listening = Config.load(main, environ={}).listening
    assert (listening.step, listening.hold) == (3.0, 0.5)


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
    from pom.listening import Listener, RollingCutter, WhisperServerClient

    class NoSound:
        def frames(self):
            return iter(())

        def close(self):
            pass

    settings = Config.load(environ={}).listening
    client = WhisperServerClient(settings.whisper_url, settings.whisper_timeout)
    cutter = RollingCutter(client, settings.step, settings.hold, settings.rewind, settings.max_buffer)
    listener = Listener(NoSound(), client, settings.step, settings.hold, settings.rewind, settings.max_buffer,
                        settings.sound_every_steps, settings.sound_seconds, settings.ambient_memory)
    assert client.report.ok and cutter.report.ok and listener.report.ok


# --- The [oracle] section --------------------------------------------------------------

def test_oracle_defaults_when_nothing_is_set():
    config = Config.load(environ={})
    assert config.oracle == OracleSettings() and config.report.ok
    assert config.oracle.url == "http://127.0.0.1:8099/v1/chat/completions"
    assert (config.oracle.timeout, config.oracle.max_tokens, config.oracle.temperature) == (120.0, 300, 0.8)
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
    from pom.voice import oracle_model_client
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
    from pom.prompt import PromptBuilder
    sample = Path(__file__).resolve().parents[1] / "config" / "pom.toml"
    copy = tmp_path / "pom.toml"
    copy.write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")
    config = Config.load(copy, environ={})
    assert config.report.ok and config.oracle.prompt.strip()
    assert PromptBuilder(config.oracle.prompt, selector=None).report.ok


@pytest.mark.parametrize("value,expected", [("true", True), ("false", False), ('"yes"', True), ('"off"', False)])
def test_consider_ambient_sounds_takes_a_boolean_or_an_on_off_word(tmp_path, value, expected):
    config = Config.load(write(tmp_path, f"[listening]\nconsider_ambient_sounds = {value}\n"), environ={})
    assert config.listening.consider_ambient_sounds is expected and config.report.ok


def test_ambient_sounds_are_not_considered_unless_asked():
    assert Config.load(environ={}).listening.consider_ambient_sounds is False


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


# --- The [output] section ------------------------------------------------------------------

def test_the_output_defaults_are_a_65_character_line_for_letter_paper_at_10_pitch():
    from pom.config import OutputSettings
    config = Config.load(environ={})
    assert config.output == OutputSettings() and config.report.ok
    assert (config.output.paper_width_inches, config.output.margin_inches, config.output.characters_per_inch) == (8.5, 1.0, 10.0)
    assert (config.output.quote_indent, config.output.linefeeds_after_portent, config.output.line_width) == (4, 4, 65)


def test_all_the_output_values_are_read_from_the_section(tmp_path):
    from pom.config import OutputSettings
    text = ("[output]\npaper_width_inches = 11\nmargin_inches = 0.5\ncharacters_per_inch = 12\n"
            "quote_indent = 6\nlinefeeds_after_portent = 2\n")
    config = Config.load(write(tmp_path, text), environ={})
    assert config.output == OutputSettings(11.0, 0.5, 12.0, 6, 2) and config.report.ok
    assert config.output.line_width == 120


@pytest.mark.parametrize("cpi, width", [(10, 65), (12, 78), (15, 97)])
def test_the_line_width_for_letter_paper_with_1_inch_margins_at_each_pitch(tmp_path, cpi, width):
    config = Config.load(write(tmp_path, f"[output]\ncharacters_per_inch = {cpi}\n"), environ={})
    assert config.output.line_width == width


def test_zero_for_the_indent_and_the_line_feeds_is_allowed(tmp_path):
    config = Config.load(write(tmp_path, "[output]\nquote_indent = 0\nlinefeeds_after_portent = 0\nmargin_inches = 0\n"), environ={})
    assert (config.output.quote_indent, config.output.linefeeds_after_portent, config.output.margin_inches) == (0, 0, 0.0)
    assert config.report.ok


@pytest.mark.parametrize("key, value", [
    ("paper_width_inches", "0"), ("paper_width_inches", '"wide"'), ("margin_inches", "-1"), ("margin_inches", "true"),
    ("characters_per_inch", "0"), ("characters_per_inch", '"ten"'), ("quote_indent", "-1"), ("quote_indent", "2.5"),
    ("linefeeds_after_portent", "-1"), ("linefeeds_after_portent", "true"),
])
def test_a_bad_output_value_means_the_default_with_a_warning(tmp_path, key, value):
    from pom.config import OutputSettings
    config = Config.load(write(tmp_path, f"[output]\n{key} = {value}\n"), environ={})
    assert config.output == OutputSettings()
    assert len(config.report.warnings) == 1 and f"output.{key}" in config.report.warnings[0]


def test_margins_that_leave_too_short_a_line_mean_the_default_paper_and_pitch_with_a_warning(tmp_path):
    from pom.config import OutputSettings
    config = Config.load(write(tmp_path, "[output]\nmargin_inches = 4\nquote_indent = 6\nlinefeeds_after_portent = 1\n"), environ={})
    assert config.output == OutputSettings(quote_indent=6, linefeeds_after_portent=1)
    assert len(config.report.warnings) == 1 and "20" in config.report.warnings[0]


def test_a_line_of_exactly_the_shortest_width_is_allowed(tmp_path):
    config = Config.load(write(tmp_path, "[output]\npaper_width_inches = 4\nmargin_inches = 1\ncharacters_per_inch = 10\n"), environ={})
    assert config.output.line_width == 20 and config.report.ok


def test_the_committed_config_sets_every_output_key():
    import tomllib
    with open(Path(__file__).resolve().parents[1] / "config" / "pom.toml", "rb") as f:
        section = tomllib.load(f)["output"]
    assert set(section) == {"paper_width_inches", "margin_inches", "characters_per_inch", "quote_indent", "linefeeds_after_portent"}


def test_the_line_width_is_not_lost_to_floating_point_rounding(tmp_path):
    # (8 - 2 x 2.2) x 15 comes to 53.99999999999999 in floating point; the line is 54 characters
    text = "[output]\npaper_width_inches = 8\nmargin_inches = 2.2\ncharacters_per_inch = 15\n"
    assert Config.load(write(tmp_path, text), environ={}).output.line_width == 54


def test_a_config_built_without_output_settings_has_the_defaults():
    from pom.config import OutputSettings
    assert Config().output == OutputSettings()
