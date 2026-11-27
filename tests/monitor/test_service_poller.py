"""Tests for pom.monitor.service_poller: asking the parts that have an address, and posting the answers."""
import threading
import time

from pom.defaults import MonitorSettings
from pom.vocabulary import MonitorLight
from pom.monitor.service_poller import ServicePoller
from pom.monitor.status_board import StatusBoard


def board_with(*names):
    board = StatusBoard()
    for name in names:
        board.expect(name, 60)
    return board


def wait_for(condition, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_poll_once_runs_every_check_and_posts_its_light():
    board = board_with("a", "b", "c")
    ServicePoller(board, {"a": lambda: MonitorLight.UP, "b": lambda: MonitorLight.WAITING, "c": lambda: MonitorLight.NONE}, MonitorSettings(service_check_seconds=15)).poll_once()
    assert board.lights() == {"a": MonitorLight.UP, "b": MonitorLight.WAITING, "c": MonitorLight.NONE}


def test_a_check_that_raises_posts_down_and_the_others_still_run():
    def broken():
        raise RuntimeError("boom")
    board = board_with("a", "b")
    ServicePoller(board, {"a": broken, "b": lambda: MonitorLight.UP}, MonitorSettings(service_check_seconds=15)).poll_once()
    assert board.lights() == {"a": MonitorLight.DOWN, "b": MonitorLight.UP}


def test_a_check_that_says_true_or_false_is_taken_as_up_or_down():
    board = board_with("a", "b", "c")
    ServicePoller(board, {"a": lambda: True, "b": lambda: False, "c": lambda: None}, MonitorSettings(service_check_seconds=15)).poll_once()
    assert board.lights() == {"a": MonitorLight.UP, "b": MonitorLight.DOWN, "c": MonitorLight.NONE}


def test_the_first_round_is_at_once_when_it_starts_and_stop_ends_the_thread():
    board = board_with("a")
    poller = ServicePoller(board, {"a": lambda: MonitorLight.UP}, MonitorSettings(service_check_seconds=3600))
    poller.start()
    try:
        assert wait_for(lambda: board.lights()["a"] is MonitorLight.UP)
    finally:
        started = time.monotonic()
        poller.stop()
    assert time.monotonic() - started < 2.0                        # it did not sit out the hour


def test_it_asks_again_every_interval():
    calls = []
    board = board_with("a")
    poller = ServicePoller(board, {"a": lambda: calls.append(1) or MonitorLight.UP}, MonitorSettings(service_check_seconds=0.05))
    poller.start()
    try:
        assert wait_for(lambda: len(calls) >= 3)
    finally:
        poller.stop()


def test_a_part_that_changes_is_posted_again():
    answers = iter([MonitorLight.WAITING, MonitorLight.WAITING, MonitorLight.UP])
    state = {"light": MonitorLight.WAITING}

    def check():
        state["light"] = next(answers, MonitorLight.UP)
        return state["light"]
    board = board_with("a")
    poller = ServicePoller(board, {"a": check}, MonitorSettings(service_check_seconds=0.05))
    poller.start()
    try:
        assert wait_for(lambda: board.lights()["a"] is MonitorLight.UP)
    finally:
        poller.stop()


def test_start_twice_runs_one_thread_and_stop_is_safe_before_start_and_twice():
    poller = ServicePoller(board_with("a"), {"a": lambda: MonitorLight.UP}, MonitorSettings(service_check_seconds=3600))
    poller.stop()                                                   # before start
    poller.start()
    poller.start()
    names = [t.name for t in threading.enumerate() if t.name == "service-poller"]
    assert len(names) == 1
    poller.stop()
    poller.stop()
    assert not any(t.name == "service-poller" and t.is_alive() for t in threading.enumerate())


def test_a_slow_check_does_not_stop_stop_from_returning_in_time_when_it_finishes():
    release = threading.Event()
    poller = ServicePoller(board_with("a"), {"a": lambda: release.wait(5) and MonitorLight.UP}, MonitorSettings(service_check_seconds=3600))
    poller.start()
    time.sleep(0.05)
    release.set()
    poller.stop()
