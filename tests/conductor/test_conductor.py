"""Tests for pom.conductor.conductor: the threads, the floor, and the ears that follow it.

The parts under it are small fakes (what they do is tested elsewhere); the FloorKeeper is the real one, because how the Conductor keeps the
ears in step with the floor is the point. The threads are real, so the tests wait for things to happen (polling, with a generous limit).
"""
import contextlib
import logging
import threading
import time

import pytest

from pom.conductor import Conductor, ConductorError, FloorKeeper, FloorState
from pom.fault_recorder import FaultRecorder
from pom.qualifying import Attempt, Ruling
from pom.scoring import ScoredBy

LISTENING = FloorState.LISTENING
SECRET = "My neighbor keeps a secret cellar."


def attempt(text="A sentence.", hinge=False, kind="speech"):
    return Attempt(text, kind, 7.0, ScoredBy.MODEL, Ruling.QUALIFIED if hinge else Ruling.DID_NOT_QUALIFY, 10.0, 6.0, 20, 5)


def hinge(text="A hinge."):
    return attempt(text, hinge=True)


def wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class FakeEars:
    """Stands in for the Eavesdropper: remembers what it was told."""

    def __init__(self):
        self.events, self.paused, self.finder = [], False, None

    def pause(self):
        self.events.append("pause")
        self.paused = True

    def skip_ahead(self):
        self.events.append("skip_ahead")
        self.paused = False

    def close(self):
        self.events.append("close")
        if self.finder is not None:
            self.finder.closed.set()               # closing the real Eavesdropper's deck is what ends hear(), and so find()


class FakeFinder:
    """Stands in for the HingeFinder: yields the scripted attempts (a callable item is run instead), then ends or waits to be closed."""

    def __init__(self, *script, block=True, raises=None):
        self.script, self.block, self.raises = list(script), block, raises
        self.closed = threading.Event()

    def find(self):
        for item in self.script:
            if callable(item):
                item()
            else:
                yield item
        if self.raises is not None:
            raise self.raises
        if self.block:
            self.closed.wait(30)

    def close(self):
        self.closed.set()


class FakeKeyboard:
    """Stands in for the KeyboardWatcher."""

    def __init__(self, *script, raises_first=False, raises_always=False):
        self.script, self.events, self.paused = list(script), [], False
        self.closed, self.raises_first, self.raises_always, self.watches = threading.Event(), raises_first, raises_always, 0
        self.report = FaultRecorder()

    def watch(self):
        self.watches += 1
        if self.raises_always or (self.raises_first and self.watches == 1):
            raise OSError("keyboard gone")
        script, self.script = self.script, []
        yield from script
        self.closed.wait(30)

    def pause(self):
        self.events.append("pause")
        self.paused = True

    def skip_ahead(self):
        self.events.append("skip_ahead")
        self.paused = False

    def close(self):
        self.events.append("close")
        self.closed.set()


class Die(BaseException):
    """Not an Exception: takes a thread down, as nothing is supposed to."""


class FakeTurnTaker:
    """Stands in for the TurnTaker: takes a turn by holding it until the test lets go (or at once), and yields the floor."""

    def __init__(self, hold=None, fail=None, ears=None, keyboard=None, fail_before_the_turn_is_closed=None):
        self.turns, self.hold, self.fail, self.ears, self.keyboard = [], hold, fail, ears, keyboard
        self.fail_early = fail_before_the_turn_is_closed
        self.started, self.ears_shut_at_start, self.report = threading.Event(), None, FaultRecorder()

    def take_turn(self, turn, attempt):
        self.turns.append((turn.holder, attempt.text))
        self.ears_shut_at_start = (self.ears.paused if self.ears else None, self.keyboard.paused if self.keyboard else None)
        self.started.set()
        if self.fail_early is not None:
            raise self.fail_early                   # a TurnTaker bug: it raises without closing the turn it was given
        with turn:
            if self.hold is not None:
                self.hold.wait(10)
            if self.fail is not None:
                raise self.fail
        return f"result for {turn.holder}"

    def report_lines(self):
        return ["taker: a line"]


class Rig:
    def __init__(self, rooms, taker=None, keyboard=None, recovering=0.05, writing=300.0, **options):
        self.floor = FloorKeeper(writing, 180.0, recovering)
        self.rooms = list(rooms)
        self.factory_calls = 0
        self.keyboard = keyboard
        self.taker = taker or FakeTurnTaker()
        self.seen, self.turned = [], []
        options.setdefault("check_seconds", 0.01)
        options.setdefault("restart_pause_seconds", 0.01)
        self.conductor = Conductor(self.floor, self.taker, self.factory, keyboard, on_attempt=self.seen.append,
                                   on_turn=lambda a, r: self.turned.append((a.text, r)), **options)

    def factory(self):
        self.factory_calls += 1
        if not self.rooms:
            return FakeFinder(), FakeEars()
        room = self.rooms.pop(0)
        if isinstance(room, Exception):
            raise room
        return room

    @contextlib.contextmanager
    def running(self):
        thread = threading.Thread(target=self.conductor.run, daemon=True)
        thread.start()
        try:
            yield thread
        finally:
            self.conductor.stop()
            thread.join(10)


def room(*script, **options):
    finder, ears = FakeFinder(*script, **options), FakeEars()
    ears.finder = finder
    return finder, ears


class TestWhatIsOffered:
    def test_every_attempt_is_shown_and_only_a_hinge_starts_a_turn(self):
        finder, ears = room(attempt("One."), attempt("Two."), hinge("Three."))
        rig = Rig([(finder, ears)])
        rig.taker.ears = ears
        with rig.running():
            assert wait_for(lambda: len(rig.taker.turns) == 1)
            assert wait_for(lambda: len(rig.seen) == 3)
        assert [a.text for a in rig.seen] == ["One.", "Two.", "Three."] and rig.taker.turns == [("room", "Three.")]

    def test_the_result_of_a_turn_is_handed_to_the_callback(self):
        rig = Rig([room(hinge("H."))])
        with rig.running():
            assert wait_for(lambda: len(rig.turned) == 1)
        assert rig.turned == [("H.", "result for room")] and rig.conductor.turns_taken == 1

    def test_a_hinge_from_the_keyboard_gets_a_turn_too(self):
        keyboard = FakeKeyboard(hinge("Typed."))
        rig = Rig([room()], keyboard=keyboard)
        with rig.running():
            assert wait_for(lambda: len(rig.taker.turns) == 1)
        assert rig.taker.turns == [("keyboard", "Typed.")]


class TestTheEarsFollowTheFloor:
    def test_they_shut_the_moment_the_floor_is_taken_before_the_turn_begins(self):
        finder, ears = room(hinge("H."))
        keyboard = FakeKeyboard()
        rig = Rig([(finder, ears)], taker=FakeTurnTaker(ears=ears, keyboard=keyboard), keyboard=keyboard)
        with rig.running():
            assert wait_for(lambda: rig.taker.started.is_set())
        assert rig.taker.ears_shut_at_start == (True, True)

    def test_they_open_again_when_the_turn_is_over_and_the_recovery_has_passed(self):
        finder, ears = room(hinge("H."))
        keyboard = FakeKeyboard()
        rig = Rig([(finder, ears)], keyboard=keyboard)
        with rig.running():
            assert wait_for(lambda: "skip_ahead" in ears.events and "skip_ahead" in keyboard.events)
            assert rig.floor.state is LISTENING and not ears.paused and not keyboard.paused

    def test_they_stay_shut_for_the_whole_of_a_long_turn(self):
        hold = threading.Event()
        finder, ears = room(hinge("H."))
        rig = Rig([(finder, ears)], taker=FakeTurnTaker(hold=hold))
        with rig.running():
            assert wait_for(lambda: rig.taker.started.is_set())
            time.sleep(0.15)                                   # many checks go by
            assert ears.paused and "skip_ahead" not in ears.events
            hold.set()
            assert wait_for(lambda: "skip_ahead" in ears.events)

    def test_ears_that_are_open_while_the_floor_is_held_are_shut_again(self):
        finder, ears = room()
        rig = Rig([(finder, ears)])
        with rig.running():
            assert wait_for(lambda: rig.conductor._ears is ears)
            turn = rig.floor.take_floor("somebody")           # the floor is taken by some route that did not shut the ears
            assert wait_for(lambda: ears.paused)
            turn.finish()
            assert wait_for(lambda: not ears.paused)

    def test_a_turn_the_watchdog_cut_off_still_gets_the_ears_opened(self):
        hold = threading.Event()
        finder, ears = room(hinge("H."))
        rig = Rig([(finder, ears)], taker=FakeTurnTaker(hold=hold), writing=0.05, recovering=0.02)
        with rig.running():
            assert wait_for(lambda: rig.floor.overruns == 1)           # the turn thread is stuck holding the floor
            assert wait_for(lambda: "skip_ahead" in ears.events)       # yet the ears open: the Printer is not deaf
            hold.set()

    def test_the_ears_of_a_room_started_while_the_floor_is_held_are_shut(self):
        hold = threading.Event()
        first, first_ears = room(hinge("H."), block=False)             # the first room ends right after its hinge
        second, second_ears = room()
        rig = Rig([(first, first_ears), (second, second_ears)], taker=FakeTurnTaker(hold=hold))
        with rig.running():
            assert wait_for(lambda: rig.factory_calls == 2)
            assert wait_for(lambda: second_ears.paused)
            hold.set()
            assert wait_for(lambda: not second_ears.paused)


class TestFirstComeFirstServed:
    def test_a_hinge_that_comes_during_a_turn_is_dropped(self):
        hold = threading.Event()
        keyboard = FakeKeyboard()
        finder, ears = room(hinge("First."), lambda: wait_for(lambda: rig.taker.started.is_set()), hinge("Second."))
        rig = Rig([(finder, ears)], taker=FakeTurnTaker(hold=hold), keyboard=keyboard)
        with rig.running():
            assert wait_for(lambda: rig.conductor.hinges_dropped == 1)
            hold.set()
        assert rig.taker.turns == [("room", "First.")]

    def test_the_keyboard_can_win_and_the_room_is_dropped(self):
        hold = threading.Event()
        gate = threading.Event()
        keyboard = FakeKeyboard(hinge("Typed."))
        finder, ears = room(lambda: gate.wait(5), hinge("Spoken."))
        rig = Rig([(finder, ears)], taker=FakeTurnTaker(hold=hold), keyboard=keyboard)
        with rig.running():
            assert wait_for(lambda: rig.taker.started.is_set())
            gate.set()
            assert wait_for(lambda: rig.conductor.hinges_dropped == 1)
            hold.set()
        assert rig.taker.turns == [("keyboard", "Typed.")]

    def test_after_a_turn_the_next_hinge_gets_its_turn(self):
        finder, ears = room(hinge("One."), lambda: wait_for(lambda: len(rig.turned) == 1 and rig.floor.state is LISTENING), hinge("Two."))
        rig = Rig([(finder, ears)])
        with rig.running():
            assert wait_for(lambda: len(rig.taker.turns) == 2)
        assert [t[1] for t in rig.taker.turns] == ["One.", "Two."] and rig.conductor.hinges_dropped == 0


class TestTheRoomKeepsGoing:
    def test_a_room_that_ends_is_built_again(self):
        first, first_ears = room(attempt("From the first."), block=False)
        second, second_ears = room(attempt("From the second."))
        rig = Rig([(first, first_ears), (second, second_ears)])
        with rig.running():
            assert wait_for(lambda: len(rig.seen) == 2)
        assert [a.text for a in rig.seen] == ["From the first.", "From the second."] and rig.factory_calls == 2
        assert any("the room ended" in w for w in rig.conductor.report.warnings)

    def test_it_goes_on_building_for_as_long_as_it_takes(self):
        rig = Rig([room(block=False) for _ in range(6)] + [room(attempt("Finally."))])
        with rig.running():
            assert wait_for(lambda: len(rig.seen) == 1)
        assert rig.factory_calls == 7

    def test_a_factory_that_raises_is_tried_again(self):
        rig = Rig([OSError("no microphone"), room(attempt("Found it."))])
        with rig.running():
            assert wait_for(lambda: len(rig.seen) == 1)
        assert rig.conductor.report.warnings[0] == "the room could not be built (OSError); trying again"

    def test_a_finder_that_raises_is_replaced(self):
        rig = Rig([room(attempt("Before."), raises=RuntimeError("boom")), room(attempt("After."))])
        with rig.running():
            assert wait_for(lambda: len(rig.seen) == 2)
        assert any("the room failed (RuntimeError)" in w for w in rig.conductor.report.warnings)

    def test_the_finder_is_closed_when_the_room_ends(self):
        finder, ears = room(block=False)
        rig = Rig([(finder, ears), room()])
        with rig.running():
            assert wait_for(lambda: finder.closed.is_set())

    def test_a_recording_that_ends_ends_the_run(self):
        rig = Rig([room(hinge("Last."), block=False)], restart_room=False)
        thread = threading.Thread(target=rig.conductor.run, daemon=True)
        thread.start()
        thread.join(10)
        assert not thread.is_alive() and rig.taker.turns == [("room", "Last.")] and rig.factory_calls == 1


class TestTheKeyboardKeepsGoing:
    def test_a_watcher_that_raises_is_run_again(self):
        keyboard = FakeKeyboard(hinge("Typed."), raises_first=True)
        rig = Rig([room()], keyboard=keyboard)
        with rig.running():
            assert wait_for(lambda: len(rig.taker.turns) == 1)
        assert keyboard.watches >= 2 and any("the keyboard failed (OSError)" in w for w in rig.conductor.report.warnings)

    def test_a_watcher_that_keeps_failing_is_started_again_only_after_a_pause_not_in_a_hot_loop(self):
        keyboard = FakeKeyboard(raises_always=True)
        rig = Rig([room()], keyboard=keyboard, restart_pause_seconds=0.2)
        with rig.running():
            time.sleep(0.5)
        assert 2 <= keyboard.watches <= 5

    def test_with_no_keyboard_there_is_no_keyboard_thread_and_no_complaint(self):
        rig = Rig([room(attempt("x"))])
        with rig.running():
            assert wait_for(lambda: len(rig.seen) == 1)
            time.sleep(0.05)
            assert "keyboard" not in rig.conductor._threads
        assert rig.conductor.report.ok


class TestWhenAThingFails:
    def test_a_taker_that_raises_still_gives_the_floor_back_and_the_next_turn_works(self):
        finder, ears = room(hinge("One."), lambda: wait_for(lambda: rig.floor.state is LISTENING), hinge("Two."))
        rig = Rig([(finder, ears)], taker=FakeTurnTaker(fail=RuntimeError("boom")))
        with rig.running():
            assert wait_for(lambda: len(rig.taker.turns) == 2)
        assert any("a turn raised (RuntimeError)" in w for w in rig.conductor.report.warnings)

    def test_a_taker_that_raises_without_closing_the_turn_still_has_the_floor_given_back_by_the_conductor(self):
        finder, ears = room(hinge("One."))
        rig = Rig([(finder, ears)], taker=FakeTurnTaker(fail_before_the_turn_is_closed=RuntimeError("boom")), recovering=0.02)
        with rig.running():
            assert wait_for(lambda: len(rig.taker.turns) == 1)
            assert wait_for(lambda: rig.floor.state is LISTENING)           # it was not left held until the watchdog (300 s)
            assert wait_for(lambda: "skip_ahead" in ears.events)

    def test_a_callback_that_raises_is_ignored_with_a_warning(self):
        finder, ears = room(attempt("One."), attempt("Two."))
        rig = Rig([(finder, ears)])
        rig.conductor._on_attempt = lambda a: (_ for _ in ()).throw(ValueError("callback broke"))
        with rig.running():
            assert wait_for(lambda: len([w for w in rig.conductor.report.warnings if "callback raised" in w]) == 2)

    def test_a_part_whose_pause_raises_is_a_warning_and_the_turn_goes_on(self):
        finder, ears = room(hinge("H."))
        ears.pause = lambda: (_ for _ in ()).throw(RuntimeError("pause broke"))
        rig = Rig([(finder, ears)])
        with rig.running():
            assert wait_for(lambda: len(rig.turned) == 1)
        assert any("pause failed on a part (RuntimeError)" in w for w in rig.conductor.report.warnings)

    @pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
    def test_a_thread_that_dies_unexpectedly_is_started_again(self):
        finder, ears = room(hinge("One."), lambda: wait_for(lambda: rig.floor.state is LISTENING), hinge("Two."))
        taker = FakeTurnTaker(fail=Die())
        rig = Rig([(finder, ears)], taker=taker)
        with rig.running():
            assert wait_for(lambda: len(taker.turns) == 1)
            taker.fail = None
            assert wait_for(lambda: len(taker.turns) == 2)        # the second turn is taken by a turn thread that was started again
        assert any("the turn thread had stopped" in w for w in rig.conductor.report.warnings)


class TestStopping:
    def test_stopping_an_idle_conductor_ends_run_and_shuts_the_parts(self):
        finder, ears = room()
        keyboard = FakeKeyboard()
        rig = Rig([(finder, ears)], keyboard=keyboard)
        thread = threading.Thread(target=rig.conductor.run, daemon=True)
        thread.start()
        assert wait_for(lambda: rig.conductor._ears is ears)
        started = time.monotonic()
        rig.conductor.stop()
        thread.join(10)
        assert not thread.is_alive() and "close" in ears.events and "close" in keyboard.events
        assert time.monotonic() - started < 2.0                              # promptly: no waiting out a join limit

    def test_stopping_twice_or_from_another_thread_is_fine(self):
        rig = Rig([room()])
        thread = threading.Thread(target=rig.conductor.run, daemon=True)
        thread.start()
        wait_for(lambda: rig.factory_calls == 1)
        stopper = threading.Thread(target=lambda: [rig.conductor.stop(), rig.conductor.stop()])
        stopper.start()
        stopper.join(5)
        thread.join(10)
        assert not thread.is_alive()

    def test_a_turn_in_progress_is_allowed_to_finish_its_work(self):
        hold = threading.Event()
        rig = Rig([room(hinge("H."))], taker=FakeTurnTaker(hold=hold))
        thread = threading.Thread(target=rig.conductor.run, daemon=True)
        thread.start()
        assert wait_for(lambda: rig.taker.started.is_set())
        rig.conductor.stop()
        hold.set()
        thread.join(10)
        assert not thread.is_alive() and len(rig.turned) == 1

    def test_the_ears_are_not_opened_again_by_the_stop(self):
        finder, ears = room()
        rig = Rig([(finder, ears)])
        thread = threading.Thread(target=rig.conductor.run, daemon=True)
        thread.start()
        wait_for(lambda: rig.conductor._ears is ears)
        rig.conductor.stop()
        thread.join(10)
        assert "skip_ahead" not in ears.events and rig.conductor._shut is True


class TestBadSettings:
    @pytest.mark.parametrize("bad", [0, -1, float("nan"), "x", None, True])
    def test_a_bad_check_time_is_the_default_with_a_warning(self, bad):
        conductor = Conductor(FloorKeeper(), FakeTurnTaker(), lambda: room(), check_seconds=bad)
        assert len(conductor.report.warnings) == 1 and "check_seconds" in conductor.report.warnings[0]

    @pytest.mark.parametrize("bad", [-1, "x", None])
    def test_a_bad_restart_pause_is_the_default_with_a_warning(self, bad):
        conductor = Conductor(FloorKeeper(), FakeTurnTaker(), lambda: room(), restart_pause_seconds=bad)
        assert len(conductor.report.warnings) == 1

    def test_a_restart_pause_of_zero_is_allowed(self):
        assert Conductor(FloorKeeper(), FakeTurnTaker(), lambda: room(), restart_pause_seconds=0).report.ok

    def test_in_strict_mode_a_bad_setting_raises(self):
        with pytest.raises(ConductorError):
            Conductor(FloorKeeper(), FakeTurnTaker(), lambda: room(), check_seconds=0, strict=True)


class TestTheLogs:
    def lines(self, caplog):
        return [r.getMessage() for r in caplog.records if r.name == "pom.conductor.conductor"]

    def test_the_story_of_a_turn_is_in_the_log(self, caplog):
        finder, ears = room(hinge("H."))
        rig = Rig([(finder, ears)])
        with caplog.at_level(logging.INFO, logger="pom.conductor.conductor"):
            with rig.running():
                assert wait_for(lambda: "skip_ahead" in ears.events)
        lines = self.lines(caplog)
        assert "conductor: starting" in lines
        assert any("the room's hinge takes the floor (turn 1)" in line for line in lines)
        assert any("the floor is free, so the ears open" in line for line in lines)
        assert any(line.startswith("conductor: stopped (1 turns, 0 hinges dropped)") for line in lines)

    def test_a_dropped_hinge_is_logged_with_the_floor_state(self, caplog):
        hold = threading.Event()
        finder, ears = room(hinge("First."), lambda: wait_for(lambda: rig.taker.started.is_set()), hinge("Second."))
        rig = Rig([(finder, ears)], taker=FakeTurnTaker(hold=hold))
        with caplog.at_level(logging.INFO, logger="pom.conductor.conductor"):
            with rig.running():
                assert wait_for(lambda: rig.conductor.hinges_dropped == 1)
                hold.set()
        assert any("a hinge from the room was dropped; the floor is writing (1 dropped so far)" in line for line in self.lines(caplog))

    def test_nothing_said_reaches_a_log_or_a_report(self, caplog):
        finder, ears = room(attempt(SECRET), hinge(SECRET + " Again."))
        rig = Rig([(finder, ears), room(raises=RuntimeError(SECRET))])
        with caplog.at_level(logging.DEBUG):
            with rig.running():
                assert wait_for(lambda: len(rig.turned) == 1)
        text = caplog.text + "\n".join(rig.conductor.report.lines())
        for word in ("neighbor", "secret", "cellar"):
            assert word not in text


class TestReporting:
    def test_report_lines_include_the_floor_and_the_parts_under_it(self):
        rig = Rig([room()], keyboard=FakeKeyboard())
        rig.floor.report.warn("the floor met a problem")
        rig.conductor.report.warn("the conductor met a problem")
        lines = rig.conductor.report_lines()
        assert "warning: the floor met a problem" in lines and "warning: the conductor met a problem" in lines and "taker: a line" in lines
