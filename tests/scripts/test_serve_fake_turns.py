"""Tests for scripts/dev/monitor/serve_fake_turns.py --chart: the real BarKeeper and anchor over invented attempts, on a stream clock that runs fast."""
import importlib.util
import random
import subprocess
import sys
from pathlib import Path

import pytest

from pom.config import Config
from pom.monitor import TurnHistory

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "dev" / "monitor" / "serve_fake_turns.py"


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("serve_fake_turns", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def room(script, seed=7, speed=30.0, head_start_minutes=35.0):
    return script.InventedRoom(Config.load(ROOT / "config" / "pom.toml"), TurnHistory(), random.Random(seed), speed, head_start_minutes)


def test_the_chart_flag_serves_the_feed_and_exits():
    result = subprocess.run([sys.executable, str(SCRIPT), "--chart", "--check"], capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert result.returncode == 0 and "the site answered:" in result.stdout, result.stderr


def test_the_head_start_is_played_at_once_so_the_chart_is_not_empty(script):
    r = room(script)
    points = r.trace.recent()
    assert len(points) > 10 and points[-1].at <= r.now() and r.heard == len(points) == r.keeper.counted


def test_a_longer_head_start_plays_more(script):
    assert len(room(script, head_start_minutes=60.0).trace.recent()) > len(room(script, head_start_minutes=10.0).trace.recent())


def test_the_stream_clock_runs_at_the_speed_given(script, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(script.time, "monotonic", lambda: clock[0])
    r = room(script, speed=60.0, head_start_minutes=0.0)
    assert r.now() == pytest.approx(0.0)
    clock[0] += 10.0
    assert r.now() == pytest.approx(600.0)


def test_attempts_are_played_up_to_now_and_no_further(script):
    r = room(script)
    r.advance_to(r.now() + 3600.0)
    assert r.trace.recent()[-1].at <= r.now() + 3600.0 and r._next_at > r.now() + 3600.0


def test_the_same_seed_gives_the_same_room(script):
    assert [(p.at, p.score) for p in room(script, seed=3).trace.recent()] == [(p.at, p.score) for p in room(script, seed=3).trace.recent()]


def test_the_room_goes_quiet_now_and_then(script):
    points = room(script, head_start_minutes=600.0).trace.recent()
    gaps = [b.at - a.at for a, b in zip(points, points[1:])]
    assert max(gaps) > 6 * 60 and min(gaps) >= 15.0


def test_a_qualifier_gets_a_turn_in_the_history(script):
    r = room(script, head_start_minutes=600.0)
    qualifiers = [p for p in r.trace.recent() if p.qualified]
    heard = {record.heard_at for record in r.history.recent()}
    assert qualifiers and all(p.at in heard for p in qualifiers)          # the trace keeps an hour of attempts, the history every turn


def test_the_bar_it_uses_is_the_one_in_the_config_with_its_anchor(script):
    r = room(script)
    assert r.keeper.ticks_between(0.0, 600.0) and "steady tick" in r.keeper.describe()
