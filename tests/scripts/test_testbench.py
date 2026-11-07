"""Smoke tests for the testbenches in scripts/dev/: each one starts, and the ones that need no server do their job."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TESTBENCHES = ["scripts/dev/listening/cut_speech_into_hinges.py", "scripts/dev/listening/split_whisper_tracks.py", "scripts/dev/listening/listen_to_room.py",
               "scripts/dev/scoring/score_typed_sentences.py", "scripts/dev/scoring/score_typed_lines.py",
               "scripts/dev/writing/write_portent_from_hinge.py",
               "scripts/dev/pipeline/listening_to_portent.py", "scripts/dev/monitor/serve_fake_turns.py",
               "scripts/dev/printing/format_text_for_typewriter.py",
               "scripts/dev/printing/type_text_on_typewriter.py", "scripts/dev/qualifying/replay_scores_through_bar.py",
               "scripts/dev/qualifying/report_score_percentiles.py"]


def run(script, *args, env=None):
    return subprocess.run([sys.executable, str(ROOT / script), *args], capture_output=True, text=True, timeout=120,
                          env={**os.environ, **(env or {})}, stdin=subprocess.DEVNULL)


@pytest.mark.parametrize("script", TESTBENCHES)
def test_every_testbench_starts_and_shows_its_help(script):
    result = run(script, "--help")
    assert result.returncode == 0 and "usage" in result.stdout.lower(), result.stderr


def test_the_sort_testbench_sorts_whispers_text():
    result = run("scripts/dev/listening/split_whisper_tracks.py", "(dog barks) Hello there. [BLANK_AUDIO]")
    assert result.stdout.splitlines() == ["words:     Hello there.", "kind:      speech", "room tone: (dog barks)"]


def test_the_sort_testbench_can_treat_the_text_as_coming_without_the_voice_detector():
    result = run("scripts/dev/listening/split_whisper_tracks.py", "--lyrics-only", "hello ♪ la la ♪ there")
    assert result.stdout.splitlines() == ["words:     la la", "kind:      lyrics", "room tone: (none)"]


def test_the_sort_testbench_reads_standard_input_too():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/dev/listening/split_whisper_tracks.py")], input="[Music] and then we left",
                            capture_output=True, text=True, timeout=60)
    assert "words:     and then we left" in result.stdout and "[Music]" in result.stdout


def test_the_score_testbench_says_so_when_the_scorer_model_is_not_answering():
    result = run("scripts/dev/scoring/score_typed_sentences.py", "Hello there.", env={"POM_SCORER_URL": "http://127.0.0.1:9/v1/chat/completions"})
    assert result.returncode == 0 and "NOT answering" in result.stdout and "unscored" in result.stdout


def test_the_typed_line_testbench_says_so_when_the_scorer_model_is_not_answering():
    result = run("scripts/dev/scoring/score_typed_lines.py", "Hello there.", env={"POM_SCORER_URL": "http://127.0.0.1:9/v1/chat/completions"})
    assert result.returncode == 0 and "NOT answering" in result.stdout and "silence" in result.stdout
    assert "Hello there" not in result.stdout          # what was typed is not echoed back


def test_the_hear_testbench_stops_politely_when_whisper_is_not_answering():
    result = run("scripts/dev/listening/listen_to_room.py", "--wav", "nothing.wav", env={"POM_WHISPER_URL": "http://127.0.0.1:9"})
    assert result.returncode != 0 and "not answering" in (result.stdout + result.stderr)


def test_the_whole_pipeline_testbench_stops_politely_when_whisper_is_not_answering():
    result = run("scripts/dev/pipeline/listening_to_portent.py", "--mac-mic", env={"POM_WHISPER_URL": "http://127.0.0.1:9"})
    assert result.returncode != 0 and "whisper-server is not answering" in (result.stdout + result.stderr)


def test_the_whole_pipeline_testbench_wants_one_sound_source_only():
    result = run("scripts/dev/pipeline/listening_to_portent.py", "--wav", "a.wav", "--mac-mic")
    assert result.returncode != 0 and "not both" in (result.stdout + result.stderr)


def test_the_fake_turn_site_serves_its_feed_and_exits():
    result = run("scripts/dev/monitor/serve_fake_turns.py", "--check")
    assert result.returncode == 0 and "the site answered: 5 turns" in result.stdout, result.stderr


def test_the_whole_pipeline_testbench_wants_a_password_for_the_monitor(tmp_path):
    config = tmp_path / "pom.toml"
    config.write_text("[monitor]\npassword = \"\"\n", encoding="utf-8")
    result = run("scripts/dev/pipeline/listening_to_portent.py", "--monitor", "--config", str(config), "--wav", "nothing.wav")
    assert result.returncode != 0 and "--monitor needs a password" in (result.stdout + result.stderr)


def test_the_monitor_flag_is_offered():
    result = run("scripts/dev/pipeline/listening_to_portent.py", "--help")
    assert "--monitor" in result.stdout and "--config" in result.stdout


def test_the_format_testbench_indents_a_quote_and_wraps_to_the_width():
    text = 'Say it:\n```\n"Go now."\n```\nDone.'
    result = run("scripts/dev/printing/format_text_for_typewriter.py", "--width", "30", "--indent", "4", "--feeds", "1", text)
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert lines[1:6] == ["Say it:", "", '    "Go now."', "", "Done."]
    assert "5 lines" in lines[-1] and "1 line feeds after" in lines[-1]


def test_the_format_testbench_takes_the_wheels_pitch_from_the_command_line():
    result = run("scripts/dev/printing/format_text_for_typewriter.py", "--cpi", "12", "word")
    assert result.stdout.splitlines()[0] == "---------|" * 7 + "--------"


def test_the_format_testbench_swaps_the_composed_characters_unless_told_to_keep_them():
    swapped = run("scripts/dev/printing/format_text_for_typewriter.py", "a `b` ~ c < d").stdout.splitlines()
    kept = run("scripts/dev/printing/format_text_for_typewriter.py", "--keep", "`~", "a `b` ~ c < d").stdout.splitlines()
    ascii_wheel = run("scripts/dev/printing/format_text_for_typewriter.py", "--ascii-wheel", "a `b` ~ c < d").stdout.splitlines()
    assert swapped[1] == "a 'b' - c < d" and kept[1] == "a `b` ~ c < d" and ascii_wheel[1] == "a `b` ~ c < d"


def test_the_type_testbench_types_the_formatted_text_on_the_screen():
    result = run("scripts/dev/printing/type_text_on_typewriter.py", "--fast", "Say it:\n```\n\"Go.\"\n```\nDone.")
    assert result.returncode == 0
    assert 'Say it:\n\n    "Go."\n\nDone.' in result.stdout and "all typed" in result.stdout


def test_the_type_testbench_says_so_when_the_real_port_will_not_open():
    result = run("scripts/dev/printing/type_text_on_typewriter.py", "--fast", "--port", "/dev/does-not-exist", "Hello")
    assert result.returncode == 0 and "NOT all typed" in result.stdout and "would not open" in result.stdout


def test_the_type_testbench_throws_away_keys_typed_before_the_portent_ends():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/dev/printing/type_text_on_typewriter.py"), "--fast", "--reply", "1", "Hello"],
                            input="typed over the portent\n", capture_output=True, text=True, timeout=60)
    assert "nothing typed back" in result.stdout


def test_the_type_testbench_returns_a_line_typed_after_the_portent():
    import time
    process = subprocess.Popen([sys.executable, str(ROOT / "scripts/dev/printing/type_text_on_typewriter.py"), "--fast", "--reply", "20", "Hello"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    time.sleep(3)                                            # the portent is typed (fast) long before this
    output, _ = process.communicate("a reply\n", timeout=60)
    assert "typed back: 'a reply'" in output


def test_the_replay_testbench_takes_the_bar_percentile_from_its_flag(tmp_path):
    """A low bar percentile sits the bar low, so more of the same scores stand above it than at a high one."""
    scores = tmp_path / "scores.csv"
    scores.write_text("seconds_into_recording,score\n" + "".join(f"{i * 10},{1 + (i * 7) % 8}\n" for i in range(120)), encoding="utf-8")

    def qualified(percentile):
        result = run("scripts/dev/qualifying/replay_scores_through_bar.py", "--scores", str(scores), "--influence", "1",
                     "--bar-percentile", str(percentile))
        assert result.returncode == 0, result.stderr
        return int(result.stdout.strip().splitlines()[-1].split()[1])           # the "qualified" column of the one row

    assert qualified(5) > qualified(100)


def test_the_replay_testbench_takes_the_anchor_from_its_flags(tmp_path):
    """Steady middling scores under ticks at 9 sit the bar near 9; under ticks at 1, or with no ticks at all, they do not."""
    scores = tmp_path / "scores.csv"
    scores.write_text("seconds_into_recording,score\n" + "".join(f"{i * 10},5\n" for i in range(240)), encoding="utf-8")

    def mean_bar(*flags):
        result = run("scripts/dev/qualifying/replay_scores_through_bar.py", "--scores", str(scores), "--influence", "1", *flags)
        assert result.returncode == 0, result.stderr
        return float(result.stdout.strip().splitlines()[-1].split()[6])          # the "bar mean" column of the one row

    high = mean_bar("--anchor-score", "9", "--anchor-interval-minutes", "0.5")
    low = mean_bar("--anchor-score", "1", "--anchor-interval-minutes", "0.5")
    none = mean_bar("--anchor-score", "9", "--anchor-interval-minutes", "1000000")
    assert high > low and high > none


def test_the_replay_testbench_can_ask_where_the_bar_sits_after_the_room_goes_quiet(tmp_path):
    """A steady room of 8s, then an hour of quiet: with ticks the bar has settled at the anchor, without them it is still at the room's level."""
    scores = tmp_path / "scores.csv"
    scores.write_text("seconds_into_recording,score\n" + "".join(f"{i * 10},8\n" for i in range(60)), encoding="utf-8")

    def after_an_hour(interval):
        result = run("scripts/dev/qualifying/replay_scores_through_bar.py", "--scores", str(scores), "--influence", "1", "--anchor-score", "5",
                     "--anchor-interval-minutes", interval, "--after-silence-minutes", "0", "--after-silence-minutes", "60")
        assert result.returncode == 0, result.stderr
        return [float(value) for value in result.stdout.strip().splitlines()[-1].split()[1:]]       # the +0m and +60m columns

    on, off = after_an_hour("1"), after_an_hour("1000000")
    assert on[0] == pytest.approx(off[0], abs=0.15) and 7.5 < off[0] < 8.1                                    # right after the room, ticks have changed the bar hardly at all
    assert on[1] == pytest.approx(5.0, abs=0.05) and off[1] == pytest.approx(off[0], abs=0.01)              # an hour on: settled at the anchor, or frozen where it was
