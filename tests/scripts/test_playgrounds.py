"""Smoke tests for the playgrounds in scripts/dev/: each one starts, and the ones that need no server do their job."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PLAYGROUNDS = ["scripts/dev/listening/cut_speech_into_hinges.py", "scripts/dev/listening/sort_whisper_text.py", "scripts/dev/listening/listen_to_room.py",
               "scripts/dev/scoring/score_typed_sentences.py", "scripts/dev/voice/write_portent_from_hinge.py",
               "scripts/dev/pipeline/listening_to_portent.py"]


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


def test_the_hear_playground_stops_politely_when_whisper_is_not_answering():
    result = run("scripts/dev/listening/listen_to_room.py", "--wav", "nothing.wav", env={"POM_WHISPER_URL": "http://127.0.0.1:9"})
    assert result.returncode != 0 and "not answering" in (result.stdout + result.stderr)


def test_the_whole_pipeline_playground_stops_politely_when_whisper_is_not_answering():
    result = run("scripts/dev/pipeline/listening_to_portent.py", "--mac-mic", env={"POM_WHISPER_URL": "http://127.0.0.1:9"})
    assert result.returncode != 0 and "whisper-server is not answering" in (result.stdout + result.stderr)


def test_the_whole_pipeline_playground_wants_one_sound_source_only():
    result = run("scripts/dev/pipeline/listening_to_portent.py", "--wav", "a.wav", "--mac-mic")
    assert result.returncode != 0 and "not both" in (result.stdout + result.stderr)
