"""Tests for pom.monitor.status_board: each part's last light, and no news is DOWN."""
import threading

import pytest

from pom.vocabulary import MonitorLight
from pom.monitor.status_board import StatusBoard, StatusBoardError


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def board(strict=False):
    clock = Clock()
    return StatusBoard(clock=clock, strict=strict), clock


def test_a_part_that_has_never_posted_is_down():
    b, _ = board()
    b.expect("mic", 5)
    assert b.lights() == {"mic": MonitorLight.DOWN}


def test_a_part_shows_what_it_last_posted():
    b, _ = board()
    b.expect("whisper", 45)
    b.post("whisper", MonitorLight.UP)
    assert b.lights()["whisper"] is MonitorLight.UP
    b.post("whisper", MonitorLight.WAITING)
    assert b.lights()["whisper"] is MonitorLight.WAITING
    b.post("whisper", MonitorLight.NONE)
    assert b.lights()["whisper"] is MonitorLight.NONE


def test_a_post_stays_good_for_exactly_its_time_and_then_the_part_is_down():
    b, clock = board()
    b.expect("mic", 5)
    b.post("mic", MonitorLight.UP)
    clock.now += 5.0
    assert b.lights()["mic"] is MonitorLight.UP                      # exactly as old as it may be
    clock.now += 0.01
    assert b.lights()["mic"] is MonitorLight.DOWN


def test_every_light_goes_down_when_it_is_old_whatever_it_said_and_a_new_post_brings_it_back():
    b, clock = board()
    for name in ("a", "b", "c"):
        b.expect(name, 10)
    b.post("a", MonitorLight.UP)
    b.post("b", MonitorLight.WAITING)
    b.post("c", MonitorLight.NONE)
    clock.now += 11
    assert set(b.lights().values()) == {MonitorLight.DOWN}
    b.post("b", MonitorLight.UP)
    assert b.lights() == {"a": MonitorLight.DOWN, "b": MonitorLight.UP, "c": MonitorLight.DOWN}


def test_each_part_has_its_own_time():
    b, clock = board()
    b.expect("mic", 5)
    b.expect("whisper", 45)
    b.post("mic", MonitorLight.UP)
    b.post("whisper", MonitorLight.UP)
    clock.now += 10
    assert b.lights() == {"mic": MonitorLight.DOWN, "whisper": MonitorLight.UP}


def test_the_lights_come_in_the_order_the_parts_were_registered():
    b, _ = board()
    for name in ("mic", "whisper", "scorer", "oracle", "printer"):
        b.expect(name, 5)
    assert list(b.lights()) == ["mic", "whisper", "scorer", "oracle", "printer"]


def test_registering_again_changes_the_time_and_keeps_the_last_post_and_the_place():
    b, clock = board()
    b.expect("a", 5)
    b.expect("b", 5)
    b.post("a", MonitorLight.UP)
    b.expect("a", 50)
    clock.now += 20
    assert b.lights() == {"a": MonitorLight.UP, "b": MonitorLight.DOWN} and list(b.lights()) == ["a", "b"]


def test_the_lights_are_a_copy():
    b, _ = board()
    b.expect("a", 5)
    b.lights()["a"] = MonitorLight.UP
    assert b.lights()["a"] is MonitorLight.DOWN


def test_the_old_true_and_false_are_taken_as_lights():
    b, _ = board()
    b.expect("a", 5)
    b.expect("b", 5)
    b.post("a", True)
    b.post("b", False)
    assert b.lights() == {"a": MonitorLight.UP, "b": MonitorLight.DOWN}


def test_a_post_from_a_part_that_was_never_registered_is_ignored_and_recorded_once(logged):
    b, _ = board()
    b.expect("mic", 5)
    b.post("nonesuch", MonitorLight.UP)
    b.post("nonesuch", MonitorLight.UP)
    assert list(b.lights()) == ["mic"]
    assert len(logged.of(b).warnings()) == 1 and "'nonesuch'" in logged.of(b).warnings()[0]


def test_strict_raises_for_a_part_that_was_never_registered():
    b, _ = board(strict=True)
    with pytest.raises(StatusBoardError):
        b.post("nonesuch", MonitorLight.UP)


def test_posting_from_several_threads_at_once_loses_nothing():
    b = StatusBoard()
    names = [f"part{n}" for n in range(8)]
    for name in names:
        b.expect(name, 60)

    def post_many(name):
        for _ in range(500):
            b.post(name, MonitorLight.UP)
    threads = [threading.Thread(target=post_many, args=(name,)) for name in names]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert set(b.lights().values()) == {MonitorLight.UP} and len(b.lights()) == 8
