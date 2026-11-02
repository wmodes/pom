"""Smoke tests for the playgrounds in scripts/dev/: each one starts, and the ones that need no server do their job."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLAYGROUNDS = ["scripts/dev/listening/cut_speech_into_hinges.py", "scripts/dev/listening/sort_whisper_text.py", "scripts/dev/listening/listen_to_room.py",
               "scripts/dev/scoring/score_typed_sentences.py", "scripts/dev/scoring/score_typed_lines.py",
               "scripts/dev/writing/write_portent_from_hinge.py",
               "scripts/dev/pipeline/listening_to_portent.py", "scripts/dev/monitor/serve_fake_turns.py",
               "scripts/dev/printing/format_text_for_typewriter.py",
               "scripts/dev/printing/type_text_on_typewriter.py"]


def run(script, *args, env=None):
    return subprocess.run([sys.executable, str(ROOT / script), *args], capture_output=True, text=True, timeout=120,
                          env={**os.environ, **(env or {})}, stdin=subprocess.DEVNULL)


@pytest.mark.parametrize("script", PLAYGROUNDS)
def test_every_playground_starts_and_shows_its_help(script):
    result = run(script, "--help")
    assert result.returncode == 0 and "usage" in result.stdout.lower(), result.stderr


def test_the_sort_playground_sorts_whispers_text():
    result = run("scripts/dev/listening/sort_whisper_text.py", "(dog barks) Hello there. [BLANK_AUDIO]")
    assert result.stdout.splitlines() == ["words:   Hello there.", "kind:    speech", "ambient: (dog barks)"]


def test_the_sort_playground_can_treat_the_text_as_coming_without_the_voice_detector():
    result = run("scripts/dev/listening/sort_whisper_text.py", "--lyrics-only", "hello ♪ la la ♪ there")
    assert result.stdout.splitlines() == ["words:   la la", "kind:    lyrics", "ambient: (none)"]


def test_the_sort_playground_reads_standard_input_too():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/dev/listening/sort_whisper_text.py")], input="[Music] and then we left",
                            capture_output=True, text=True, timeout=60)
    assert "words:   and then we left" in result.stdout and "[Music]" in result.stdout


def test_the_score_playground_says_so_when_the_scorer_model_is_not_answering():
    result = run("scripts/dev/scoring/score_typed_sentences.py", "Hello there.", env={"POM_SCORER_URL": "http://127.0.0.1:9/v1/chat/completions"})
    assert result.returncode == 0 and "NOT answering" in result.stdout and "unscored" in result.stdout


def test_the_typed_line_playground_says_so_when_the_scorer_model_is_not_answering():
    result = run("scripts/dev/scoring/score_typed_lines.py", "Hello there.", env={"POM_SCORER_URL": "http://127.0.0.1:9/v1/chat/completions"})
    assert result.returncode == 0 and "NOT answering" in result.stdout and "silence" in result.stdout
    assert "Hello there" not in result.stdout          # what was typed is not echoed back


def test_the_hear_playground_stops_politely_when_whisper_is_not_answering():
    result = run("scripts/dev/listening/listen_to_room.py", "--wav", "nothing.wav", env={"POM_WHISPER_URL": "http://127.0.0.1:9"})
    assert result.returncode != 0 and "not answering" in (result.stdout + result.stderr)


def test_the_whole_pipeline_playground_stops_politely_when_whisper_is_not_answering():
    result = run("scripts/dev/pipeline/listening_to_portent.py", "--mac-mic", env={"POM_WHISPER_URL": "http://127.0.0.1:9"})
    assert result.returncode != 0 and "whisper-server is not answering" in (result.stdout + result.stderr)


def test_the_whole_pipeline_playground_wants_one_sound_source_only():
    result = run("scripts/dev/pipeline/listening_to_portent.py", "--wav", "a.wav", "--mac-mic")
    assert result.returncode != 0 and "not both" in (result.stdout + result.stderr)


def test_the_fake_trigger_site_serves_its_feed_and_exits():
    result = run("scripts/dev/monitor/serve_fake_turns.py", "--check")
    assert result.returncode == 0 and "the site answered: 5 triggers" in result.stdout, result.stderr


def test_the_whole_pipeline_playground_wants_a_password_for_the_monitor(tmp_path):
    config = tmp_path / "pom.toml"
    config.write_text("[monitor]\npassword = \"\"\n", encoding="utf-8")
    result = run("scripts/dev/pipeline/listening_to_portent.py", "--monitor", "--config", str(config), "--wav", "nothing.wav")
    assert result.returncode != 0 and "--monitor needs a password" in (result.stdout + result.stderr)


def test_the_monitor_flag_is_offered():
    result = run("scripts/dev/pipeline/listening_to_portent.py", "--help")
    assert "--monitor" in result.stdout and "--config" in result.stdout


def test_the_format_playground_indents_a_quote_and_wraps_to_the_width():
    text = 'Say it:\n```\n"Go now."\n```\nDone.'
    result = run("scripts/dev/printing/format_text_for_typewriter.py", "--width", "30", "--indent", "4", "--feeds", "1", text)
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert lines[1:6] == ["Say it:", "", '    "Go now."', "", "Done."]
    assert "5 lines" in lines[-1] and "1 line feeds after" in lines[-1]


def test_the_format_playground_takes_the_wheels_pitch_from_the_command_line():
    result = run("scripts/dev/printing/format_text_for_typewriter.py", "--cpi", "12", "word")
    assert result.stdout.splitlines()[0] == "---------|" * 7 + "--------"


def test_the_format_playground_swaps_the_composed_characters_unless_told_to_keep_them():
    swapped = run("scripts/dev/printing/format_text_for_typewriter.py", "a `b` ~ c < d").stdout.splitlines()
    kept = run("scripts/dev/printing/format_text_for_typewriter.py", "--keep", "`~", "a `b` ~ c < d").stdout.splitlines()
    ascii_wheel = run("scripts/dev/printing/format_text_for_typewriter.py", "--ascii-wheel", "a `b` ~ c < d").stdout.splitlines()
    assert swapped[1] == "a 'b' - c < d" and kept[1] == "a `b` ~ c < d" and ascii_wheel[1] == "a `b` ~ c < d"


def test_the_type_playground_types_the_formatted_text_on_the_screen():
    result = run("scripts/dev/printing/type_text_on_typewriter.py", "--fast", "Say it:\n```\n\"Go.\"\n```\nDone.")
    assert result.returncode == 0
    assert 'Say it:\n\n    "Go."\n\nDone.' in result.stdout and "all typed" in result.stdout


def test_the_type_playground_says_so_when_the_real_port_will_not_open():
    result = run("scripts/dev/printing/type_text_on_typewriter.py", "--fast", "--port", "/dev/does-not-exist", "Hello")
    assert result.returncode == 0 and "NOT all typed" in result.stdout and "would not open" in result.stdout


def test_the_type_playground_throws_away_keys_typed_before_the_portent_ends():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/dev/printing/type_text_on_typewriter.py"), "--fast", "--reply", "1", "Hello"],
                            input="typed over the portent\n", capture_output=True, text=True, timeout=60)
    assert "nothing typed back" in result.stdout


def test_the_type_playground_returns_a_line_typed_after_the_portent():
    import time
    process = subprocess.Popen([sys.executable, str(ROOT / "scripts/dev/printing/type_text_on_typewriter.py"), "--fast", "--reply", "20", "Hello"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    time.sleep(3)                                            # the portent is typed (fast) long before this
    output, _ = process.communicate("a reply\n", timeout=60)
    assert "typed back: 'a reply'" in output
