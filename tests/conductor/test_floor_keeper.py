"""Tests for pom.conductor.floor_keeper: who has the floor, one turn at a time, and the watchdog that takes it back.

The clock is a small fake the test moves by hand, so a 5-minute watchdog is tested without waiting.
"""
import logging
import threading
import time

import pytest

from pom.conductor import FloorKeeper, FloorKeeperError, FloorState, Turn
from pom.conductor.floor_keeper import (DEFAULT_PRINTING_SECONDS, DEFAULT_RECOVERING_SECONDS, DEFAULT_WRITING_SECONDS)

LISTENING, WRITING, PRINTING, RECOVERING = FloorState.LISTENING, FloorState.WRITING, FloorState.PRINTING, FloorState.RECOVERING


class Clock:
    """A clock the test moves."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def keeper(writing=100.0, printing=50.0, recovering=3.0, **options):
    clock = Clock()
    return FloorKeeper(writing, printing, recovering, clock=clock, **options), clock


class TestTheDefaults:
    def test_the_limits_cover_the_longest_likely_run(self):
        # writing: longer than the oracle's own timeout (120 s) with room for a slower one; printing: the longest portent (about 90 s)
        assert DEFAULT_WRITING_SECONDS >= 300.0 and DEFAULT_PRINTING_SECONDS >= 180.0 and DEFAULT_RECOVERING_SECONDS == 3.0

    def test_it_starts_listening_with_nobody_holding_the_floor(self):
        k, _ = keeper()
        assert k.state is LISTENING and k.holder is None and k.overruns == 0 and k.turns_taken == 0

    def test_good_settings_are_quiet(self):
        assert keeper()[0].report.ok

    def test_with_no_settings_given_it_uses_the_defaults_and_a_real_clock(self):
        k = FloorKeeper()
        assert k.report.ok and k.state is LISTENING


class TestTakingTheFloor:
    def test_the_first_to_ask_gets_a_turn(self):
        k, _ = keeper()
        turn = k.take_floor("room")
        assert isinstance(turn, Turn) and turn.holder == "room"
        assert k.state is WRITING and k.holder == "room" and k.turns_taken == 1

    def test_the_second_to_ask_is_refused_whoever_it_is(self):
        k, _ = keeper()
        assert k.take_floor("room") is not None
        assert k.take_floor("keyboard") is None
        assert k.take_floor("room") is None
        assert k.holder == "room" and k.turns_taken == 1

    def test_the_keyboard_can_come_first_and_the_room_is_refused(self):
        k, _ = keeper()
        assert k.take_floor("keyboard") is not None
        assert k.take_floor("room") is None and k.holder == "keyboard"

    @pytest.mark.parametrize("who", ["", None, 5, ["room"]])
    def test_a_label_that_is_not_a_name_is_recorded_as_unknown(self, who):
        k, _ = keeper()
        assert k.take_floor(who).holder == "unknown"

    def test_the_floor_is_not_given_during_a_turn_in_any_state(self):
        k, _ = keeper()
        turn = k.take_floor("room")
        turn.start_printing()
        assert k.state is PRINTING and k.take_floor("keyboard") is None


class TestAGoodTurn:
    def test_it_goes_through_the_states_in_order_and_back_to_listening(self):
        k, clock = keeper()
        turn = k.take_floor("room")
        assert k.state is WRITING
        assert turn.start_printing() is True and k.state is PRINTING
        assert turn.finish() is True and k.state is RECOVERING and k.holder is None
        clock.advance(2.9)
        assert k.state is RECOVERING
        clock.advance(0.1)
        assert k.state is LISTENING

    def test_the_floor_is_not_given_during_the_recovery(self):
        k, clock = keeper()
        k.take_floor("room").finish()
        assert k.take_floor("keyboard") is None
        clock.advance(3.0)
        assert k.take_floor("keyboard") is not None

    def test_a_recovery_of_zero_seconds_is_over_at_once(self):
        k, _ = keeper(recovering=0)
        k.take_floor("room").finish()
        assert k.state is LISTENING

    def test_a_clock_that_steps_back_never_extends_the_recovery_or_trips_the_watchdog(self):
        k, clock = keeper(writing=100, recovering=0)
        k.take_floor("room").finish()
        clock.advance(-5)
        assert k.state is LISTENING and k.report.ok
        k.take_floor("room")
        clock.advance(-50)
        assert k.state is WRITING and k.report.ok

    def test_a_turn_that_never_prints_can_finish_from_writing(self):
        k, _ = keeper()
        turn = k.take_floor("room")
        assert turn.finish() is True and k.state is RECOVERING

    def test_a_with_block_yields_the_floor_when_it_ends(self):
        k, _ = keeper()
        with k.take_floor("room") as turn:
            turn.start_printing()
            assert k.state is PRINTING
        assert k.state is RECOVERING

    def test_a_with_block_yields_the_floor_even_when_the_work_fails_and_the_error_still_shows(self):
        k, _ = keeper()
        with pytest.raises(RuntimeError, match="boom"):
            with k.take_floor("room"):
                raise RuntimeError("boom")
        assert k.state is RECOVERING

    def test_finishing_twice_is_harmless(self):
        k, _ = keeper()
        turn = k.take_floor("room")
        assert turn.finish() is True and turn.finish() is False
        assert k.state is RECOVERING

    def test_printing_cannot_start_twice_or_after_the_end(self):
        k, _ = keeper()
        turn = k.take_floor("room")
        assert turn.start_printing() is True and turn.start_printing() is False
        turn.finish()
        assert turn.start_printing() is False and k.state is RECOVERING

    def test_each_state_is_timed_from_when_it_began(self):
        k, clock = keeper(writing=100, printing=50)
        turn = k.take_floor("room")
        clock.advance(90)
        turn.start_printing()
        clock.advance(40)                        # 130 s in all, but only 40 s of printing
        assert k.state is PRINTING and k.report.ok


class TestTheWatchdog:
    def test_a_turn_still_writing_past_its_limit_loses_the_floor(self):
        k, clock = keeper(writing=100)
        k.take_floor("room")
        clock.advance(100.0)
        assert k.state is WRITING                  # at the limit is still allowed
        clock.advance(0.1)
        assert k.state is RECOVERING and k.holder is None and k.overruns == 1
        assert len(k.report.warnings) == 1 and "writing ran more than 100 s" in k.report.warnings[0]

    def test_a_turn_still_printing_past_its_limit_loses_the_floor(self):
        k, clock = keeper(printing=50)
        k.take_floor("room").start_printing()
        clock.advance(50.1)
        assert k.state is RECOVERING and k.overruns == 1
        assert "printing ran more than 50 s" in k.report.warnings[0]

    def test_after_an_overrun_the_recovery_runs_and_then_the_floor_is_free(self):
        k, clock = keeper(writing=100)
        k.take_floor("room")
        clock.advance(101)
        assert k.state is RECOVERING
        clock.advance(3)
        assert k.state is LISTENING and k.take_floor("keyboard") is not None

    def test_the_recovery_counts_from_when_the_overrun_is_noticed(self):
        k, clock = keeper(writing=100, recovering=3)
        k.take_floor("room")
        clock.advance(500)                         # nobody looked for a long time
        assert k.state is RECOVERING               # noticed now: the recovery begins now, so the ears stay shut a full 3 s more
        clock.advance(2.9)
        assert k.state is RECOVERING
        clock.advance(0.1)
        assert k.state is LISTENING

    def test_nothing_has_to_tick_the_next_ask_for_the_floor_clears_a_stuck_state(self):
        k, clock = keeper(writing=100)
        k.take_floor("room")
        clock.advance(500)                         # long past the limit and the recovery; nobody read the state meanwhile
        assert k.take_floor("keyboard") is None    # that ask settles the overrun: the recovery has just begun
        clock.advance(3)
        assert k.take_floor("keyboard") is not None

    def test_a_long_silence_does_not_trigger_the_watchdog_while_listening(self):
        k, clock = keeper()
        clock.advance(100000)
        assert k.state is LISTENING and k.report.ok and k.overruns == 0

    def test_a_fault_is_a_warning_not_a_critical_because_the_keeper_recovered(self):
        k, clock = keeper(writing=10)
        k.take_floor("room")
        clock.advance(11)
        k.state
        assert k.report.criticals == [] and len(k.report.warnings) == 1

    def test_in_strict_mode_an_overrun_raises(self):
        k, clock = keeper(writing=10, strict=True)
        k.take_floor("room")
        clock.advance(11)
        with pytest.raises(FloorKeeperError):
            k.state


class TestAStaleTurn:
    """The watchdog takes the floor from a slow turn; when that turn wakes up later, it must not disturb the next one."""

    def test_a_late_finish_does_nothing_to_the_next_turn(self):
        k, clock = keeper(writing=100)
        slow = k.take_floor("room")
        clock.advance(101)
        k.state                                    # the watchdog acts
        clock.advance(3)
        fresh = k.take_floor("keyboard")
        assert slow.finish() is False
        assert k.state is WRITING and k.holder == "keyboard"
        assert fresh.finish() is True

    def test_a_late_start_printing_does_nothing_either(self):
        k, clock = keeper(writing=100)
        slow = k.take_floor("room")
        clock.advance(101)
        k.state                                    # the watchdog acts
        clock.advance(3)
        fresh = k.take_floor("keyboard")
        assert slow.start_printing() is False and k.state is WRITING
        assert fresh.start_printing() is True

    def test_a_with_block_of_a_stale_turn_leaves_the_next_turn_alone(self):
        k, clock = keeper(writing=100)
        with k.take_floor("room"):
            clock.advance(104)                     # the work was stuck for a long time
            fresh = k.take_floor("keyboard")       # settles the overrun, then the recovery is not over yet
            assert fresh is None
            clock.advance(3)
            fresh = k.take_floor("keyboard")
            assert fresh is not None
        # leaving the block called finish() on the stale turn
        assert k.state is WRITING and k.holder == "keyboard"

    def test_a_call_from_a_finished_turn_is_ignored_too(self):
        k, _ = keeper()
        done = k.take_floor("room")
        done.finish()
        assert done.start_printing() is False and done.finish() is False


class TestTheLogs:
    """A problem has to be readable afterwards in the logs: states, who, how long. Never words."""

    def lines(self, caplog):
        return [r.getMessage() for r in caplog.records if r.name == "pom.conductor.floor_keeper"]

    def test_every_change_of_state_is_logged_with_who_and_how_long(self, caplog):
        k, clock = keeper(printing=100)
        with caplog.at_level(logging.INFO, logger="pom.conductor.floor_keeper"):
            turn = k.take_floor("room")
            clock.advance(41.25)
            turn.start_printing()
            clock.advance(63.0)
            turn.finish()
            clock.advance(3.0)
            k.state
        lines = self.lines(caplog)
        assert "floor: listening -> writing (room) after 0.0 s" in lines[0]
        assert "floor: writing -> printing (room) after 41.2 s" in lines[1] or "after 41.3 s" in lines[1]
        assert "floor: printing -> recovering (room) after 63.0 s" in lines[2]
        assert "the room's turn is over after 104.2 s in all" in lines[3] or "after 104.3 s in all" in lines[3]
        assert "floor: recovering -> listening (room) after 3.0 s" in lines[4]

    def test_a_refused_ask_is_logged_with_who_holds_the_floor(self, caplog):
        k, _ = keeper()
        k.take_floor("room")
        with caplog.at_level(logging.INFO, logger="pom.conductor.floor_keeper"):
            k.take_floor("keyboard")
        assert any("the keyboard asked for the floor but it is writing (held by the room)" in line for line in self.lines(caplog))

    def test_a_refused_ask_during_the_recovery_says_so(self, caplog):
        k, _ = keeper()
        k.take_floor("room").finish()
        with caplog.at_level(logging.INFO, logger="pom.conductor.floor_keeper"):
            k.take_floor("keyboard")
        assert any("it is recovering (nobody's, recovering)" in line for line in self.lines(caplog))

    def test_an_overrun_is_a_warning_in_the_log(self, caplog):
        k, clock = keeper(writing=10)
        k.take_floor("room")
        clock.advance(11)
        with caplog.at_level(logging.INFO, logger="pom.conductor.floor_keeper"):
            k.state
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1 and "the room's turn lost the floor" in warnings[0].getMessage()

    def test_a_late_call_from_a_turn_that_lost_the_floor_is_logged(self, caplog):
        k, clock = keeper(writing=10)
        slow = k.take_floor("room")
        clock.advance(11)
        with caplog.at_level(logging.INFO, logger="pom.conductor.floor_keeper"):
            slow.finish()
        assert any("the room's turn called finish after it lost the floor; ignored" in line for line in self.lines(caplog))

    def test_a_repeated_finish_of_a_normal_turn_is_not_news(self, caplog):
        k, _ = keeper()
        turn = k.take_floor("room")
        turn.finish()
        with caplog.at_level(logging.INFO, logger="pom.conductor.floor_keeper"):
            turn.finish()
        assert self.lines(caplog) == []

    def test_only_labels_and_seconds_are_logged(self, caplog):
        k, clock = keeper()
        with caplog.at_level(logging.DEBUG, logger="pom.conductor.floor_keeper"):
            with k.take_floor("room") as turn:
                turn.start_printing()
            clock.advance(3)
            k.state
        text = " ".join(self.lines(caplog))
        assert set(text.replace(".", " ").split()) <= {"floor:", "listening", "writing", "printing", "recovering", "->", "(room)", "(-)",
                                                      "after", "s", "0", "the", "room's", "turn", "is", "over", "in", "all"} | set(
            str(n) for n in range(100))


class TestBadSettings:
    @pytest.mark.parametrize("name", ["writing_seconds", "printing_seconds"])
    @pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), "x", None, True])
    def test_a_bad_limit_is_the_default_with_a_warning(self, name, bad):
        k = FloorKeeper(**{name: bad}, clock=Clock())
        assert len(k.report.warnings) == 1 and name in k.report.warnings[0]
        k.take_floor("room")

    @pytest.mark.parametrize("bad", [-1, float("nan"), "x", None, True])
    def test_a_bad_recovery_is_the_default_with_a_warning(self, bad):
        k = FloorKeeper(recovering_seconds=bad, clock=Clock())
        assert len(k.report.warnings) == 1 and "recovering_seconds" in k.report.warnings[0]

    def test_a_clock_that_is_not_a_function_is_replaced_with_a_warning(self):
        k = FloorKeeper(clock=5)
        assert len(k.report.warnings) == 1 and "clock" in k.report.warnings[0] and k.state is LISTENING

    def test_in_strict_mode_a_bad_setting_raises(self):
        with pytest.raises(FloorKeeperError):
            FloorKeeper(writing_seconds=-1, strict=True)

    def test_a_default_is_used_when_a_limit_is_bad(self):
        k = FloorKeeper(writing_seconds=-1, clock=(clock := Clock()))
        k.take_floor("room")
        clock.advance(DEFAULT_WRITING_SECONDS - 1)
        assert k.state is WRITING
        clock.advance(2)
        assert k.state is RECOVERING


class TestAStuckLock:
    """If something ever holds the lock and never lets go, a call gives up, says so, and never blocks."""

    @pytest.fixture
    def stuck(self, monkeypatch):
        monkeypatch.setattr("pom.conductor.floor_keeper.LOCK_TIMEOUT_SECONDS", 0.05)
        k, clock = keeper()
        turn = k.take_floor("room")
        k._lock.acquire()
        yield k, turn
        k._lock.release()

    def test_no_turn_is_given(self, stuck):
        k, _ = stuck
        assert k.take_floor("keyboard") is None

    def test_the_questions_answer_safely(self, stuck):
        k, _ = stuck
        assert k.state is LISTENING and k.holder is None

    def test_a_turn_cannot_move_on_and_says_so(self, stuck):
        _, turn = stuck
        assert turn.start_printing() is False and turn.finish() is False

    def test_each_give_up_is_a_critical_fault_that_names_the_call(self, stuck):
        k, turn = stuck
        k.take_floor("keyboard")
        k.state
        turn.finish()
        assert len(k.report.criticals) == 3
        assert "take_floor" in k.report.criticals[0] and "state" in k.report.criticals[1] and "finish" in k.report.criticals[2]

    def test_it_waits_no_longer_than_the_timeout(self, stuck):
        k, _ = stuck
        started = time.monotonic()
        k.take_floor("keyboard")
        assert time.monotonic() - started < 1.0

    def test_after_it_is_freed_everything_works_again(self, monkeypatch):
        monkeypatch.setattr("pom.conductor.floor_keeper.LOCK_TIMEOUT_SECONDS", 0.05)
        k, _ = keeper()
        k._lock.acquire()
        assert k.take_floor("room") is None
        k._lock.release()
        assert k.take_floor("room") is not None

    def test_a_failing_clock_does_not_leave_the_lock_held(self):
        calls = {"n": 0}

        def clock():
            calls["n"] += 1
            if calls["n"] > 2:
                raise RuntimeError("clock broke")
            return 1000.0
        k = FloorKeeper(clock=clock)
        with pytest.raises(RuntimeError):
            k.take_floor("room")
        assert k._lock.acquire(timeout=0.5)
        k._lock.release()


class TestManyThreads:
    def test_exactly_one_of_many_simultaneous_askers_gets_the_floor(self):
        slow = Clock()

        def lurching_clock():
            time.sleep(0.001)                      # inside the lock: gives a rival thread every chance to barge in
            return slow()
        k = FloorKeeper(clock=lurching_clock)
        winners, barrier = [], threading.Barrier(20)

        def ask(number):
            barrier.wait()
            turn = k.take_floor(f"asker{number}")
            if turn is not None:
                winners.append(turn.holder)
        threads = [threading.Thread(target=ask, args=(n,)) for n in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert len(winners) == 1 and k.turns_taken == 1

    def test_the_room_and_the_keyboard_taking_turns_never_overlap(self):
        k = FloorKeeper(recovering_seconds=0)
        inside, overlaps = [0], [0]
        guard = threading.Lock()

        def source(name):
            for _ in range(200):
                turn = k.take_floor(name)
                if turn is None:
                    continue
                with turn:
                    with guard:
                        inside[0] += 1
                        if inside[0] > 1:
                            overlaps[0] += 1
                    time.sleep(0.0002)
                    with guard:
                        inside[0] -= 1
        threads = [threading.Thread(target=source, args=(name,)) for name in ("room", "keyboard")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        assert overlaps[0] == 0 and k.turns_taken > 0 and k.state is LISTENING
