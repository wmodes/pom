"""Tests that every module honours its own `DEBUG = ...` line, and that none is committed switched on.

The per-module debug override is the line `DEBUG = None` near the top of a module. Changing it to
True turns that module's DEBUG output on whatever the project flag says; False keeps it quiet. These
tests prove the line works by running each module's source with the line edited, and they guard the
committed default, since a forgotten `DEBUG = True` would flood the journal in the gallery.
"""
import logging
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
MODULES = ["pom/config.py", "pom/corpus/draw_weights.py", "pom/corpus/chunk_store.py",
           "pom/corpus/exemplar_selector.py", "pom/listening/segment_fragmenter.py",
           "pom/scoring/word_tagger.py", "pom/scoring/scorer_model_client.py",
           "pom/scoring/scorer.py", "pom/scoring/fallback_scorer.py",
           "pom/scoring/convo_interest_tracker.py", "pom/prompt/prompt_builder.py", "pom/voice/oracle_model_client.py",
           "pom/startup.py",
           "pom/listening/whisper_server_client.py", "pom/listening/wav_source.py", "pom/listening/mic_source.py",
           "pom/listening/rolling_cutter.py",
           "pom/listening/listener.py", "pom/voice/portent_writer.py", "pom/listening/mac_mic_source.py",
           "pom/scoring/hinge_finder.py",
           "pom/monitor/trigger_history.py"]
DEBUG_LINE = re.compile(r"^DEBUG\s*=\s*(.*?)\s*(#.*)?$", re.MULTILINE)


def dotted(path: str) -> str:
    return path[:-3].replace("/", ".")


@pytest.mark.parametrize("path", MODULES)
def test_the_committed_default_follows_the_project_flag(path):
    source = (SRC / path).read_text(encoding="utf-8")
    matches = DEBUG_LINE.findall(source)
    assert len(matches) == 1, f"{path} must have exactly one top-level DEBUG line"
    assert matches[0][0] == "None", f"{path} is committed with DEBUG = {matches[0][0]}; put it back to None"


def test_every_runtime_module_that_logs_declares_the_override():
    """A module that gets a logger must offer the DEBUG line, so the pattern stays uniform."""
    assignment = re.compile(r"^_log = get_logger\(__name__", re.MULTILINE)
    found = 0
    for file in sorted((SRC / "pom").rglob("*.py")):
        source = file.read_text(encoding="utf-8")
        if assignment.search(source):
            found += 1
            assert DEBUG_LINE.search(source), f"{file.relative_to(SRC)} logs but has no DEBUG line"
            assert re.search(r"^_log = get_logger\(__name__, DEBUG\)", source, re.MULTILINE), \
                f"{file.name} ignores its DEBUG line"
    assert found == len(MODULES)   # a new logging module must also be added to MODULES above


@pytest.mark.parametrize("path", MODULES)
@pytest.mark.parametrize("flag,expected", [("True", logging.DEBUG), ("False", logging.INFO),
                                           ("None", logging.NOTSET)])
def test_a_module_follows_its_own_debug_line(path, flag, expected):
    source = (SRC / path).read_text(encoding="utf-8")
    patched = DEBUG_LINE.sub(f"DEBUG = {flag}", source, count=1)
    name = dotted(path)
    try:
        exec(compile(patched, path, "exec"), {"__name__": name})
        assert logging.getLogger(name).level == expected
    finally:
        logging.getLogger(name).setLevel(logging.NOTSET)
