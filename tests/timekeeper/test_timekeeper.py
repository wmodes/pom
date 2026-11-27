"""Tests for pom.timekeeper: the stream's one clock, which every part reads."""
import threading

import pytest

from pom.timekeeper import Timekeeper


class FakeWall:
    """Real time, moved by hand."""

    def __init__(self, start=100.0):
        self.now = start

    def __call__(self):
        return self.now


class FakeRoom:
    """Stands in for an Eavesdropper: its clock is where it began plus the real seconds since, and it remembers what it was asked."""

    def __init__(self, began_at, wall_then):
        self.began_at, self.wall_then, self.asked = began_at, wall_then, []

    def stream_time_at(self, wall_now):
        self.asked.append(wall_now)
        return self.began_at + (wall_now - self.wall_then)


def test_it_stands_at_its_start_when_it_is_made():
    wall = FakeWall()
    assert Timekeeper(started_at=40.0, wall=wall).now() == 40.0


def test_the_stream_starts_at_zero_unless_told_otherwise():
    assert Timekeeper(wall=FakeWall()).now() == 0.0


def test_before_any_room_it_runs_on_real_seconds():
    wall = FakeWall()
    keeper = Timekeeper(started_at=40.0, wall=wall)
    wall.now += 7.0
    assert keeper.now() == pytest.approx(47.0)


def test_once_it_follows_a_room_the_time_is_the_rooms():
    wall = FakeWall()
    keeper = Timekeeper(wall=wall)
    room = FakeRoom(began_at=500.0, wall_then=wall.now)
    keeper.follow(room)
    wall.now += 3.0
    assert keeper.now() == pytest.approx(503.0)
    assert room.asked == [wall.now]                     # asked at the real time now, from the keeper's own wall


def test_a_room_followed_later_replaces_the_one_before():
    wall = FakeWall()
    keeper = Timekeeper(wall=wall)
    keeper.follow(FakeRoom(began_at=10.0, wall_then=wall.now))
    keeper.follow(FakeRoom(began_at=900.0, wall_then=wall.now))
    assert keeper.now() == pytest.approx(900.0)


def test_a_room_begun_at_now_carries_the_clock_on_without_a_jump():
    wall = FakeWall()
    keeper = Timekeeper(started_at=0.0, wall=wall)
    wall.now += 12.0                                    # the microphone took twelve seconds to come up
    keeper.follow(FakeRoom(began_at=keeper.now(), wall_then=wall.now))
    assert keeper.now() == pytest.approx(12.0)
    wall.now += 5.0
    assert keeper.now() == pytest.approx(17.0)


def test_the_room_is_asked_outside_the_lock():
    """A room that reads or swaps the clock while it is asked the time must not hang the caller (calls out are made outside the lock)."""
    wall = FakeWall()
    keeper = Timekeeper(wall=wall)

    class Reentrant(FakeRoom):
        def stream_time_at(self, wall_now):
            keeper.follow(self)
            return super().stream_time_at(wall_now)

    keeper.follow(Reentrant(began_at=1.0, wall_then=wall.now))
    answer = []
    thread = threading.Thread(target=lambda: answer.append(keeper.now()), daemon=True)
    thread.start()
    thread.join(timeout=2.0)
    assert answer == [pytest.approx(1.0)]


def test_now_and_follow_from_many_threads_never_fail():
    wall = FakeWall()
    keeper = Timekeeper(wall=wall)
    problems = []

    def read():
        try:
            for _ in range(2000):
                keeper.now()
        except Exception as problem:                    # pragma: no cover - only on a failure
            problems.append(problem)

    def swap():
        for n in range(2000):
            keeper.follow(FakeRoom(began_at=float(n), wall_then=wall.now))

    threads = [threading.Thread(target=read) for _ in range(3)] + [threading.Thread(target=swap)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)
    assert not problems
