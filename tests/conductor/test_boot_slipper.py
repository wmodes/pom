"""Tests for pom.conductor.boot_slipper: the slip typed when the typewriter is online.

The terminal is a fake the test scripts (offline for a while, then online, a port that dies); the FloorKeeper and the formatter are the
real ones, because how the slip takes the floor and is set for the paper is the point. Waiting is real but zero seconds long.
"""
import logging
import threading
from types import SimpleNamespace

import pytest

from pom.conductor import BootSlipper, FloorKeeper, FloorState
from pom.printing import PortentFormatter, PrintResult

TEMPLATE = "CADETWRITER\nHOST {hostname}\nLINE 1 ....... {address}\nON LINE"


class FakeTerminal:
    """Offline for `offline_asks` questions, then online; types by recording. `results` scripts what each transmit returns."""

    def __init__(self, offline_asks=0, results=None):
        self.offline_asks, self.asked, self.typed, self.results = offline_asks, 0, [], list(results or [])
        self.heartbeat_seen = []

    def online(self):
        self.asked += 1
        return self.asked > self.offline_asks

    def transmit(self, text, still_alive=None):
        self.typed.append(text)
        self.heartbeat_seen.append(still_alive is not None and still_alive())
        if self.results:
            return self.results.pop(0)
        return PrintResult(len(text), 0.0, True)


def slipper_for(terminal, addresses=None, floor=None, template=TEMPLATE, **options):
    options.setdefault("grace_seconds", 0.0)
    options.setdefault("retry_seconds", 0.0)
    options.setdefault("ears_shut_seconds", 0.0)
    found = list(addresses if addresses is not None else [])
    floor = floor or FloorKeeper(recovering_seconds=0)
    slipper = BootSlipper(floor, terminal, PortentFormatter(65, 4, 4, ""), template, lambda: list(found), hostname=lambda: "pom1", **options)
    return slipper, floor


class TestTheSlip:
    def test_the_template_is_filled_with_the_address_and_the_name_and_typed(self):
        terminal = FakeTerminal()
        slipper, _ = slipper_for(terminal, ["192.168.1.23"])
        assert slipper.run() is True and slipper.typed is True
        assert terminal.typed[0].startswith("CADETWRITER\nHOST pom1\nLINE 1 ....... 192.168.1.23\nON LINE")

    def test_it_is_set_for_the_paper_like_a_portent_with_line_feeds_after(self):
        terminal = FakeTerminal()
        slipper_for(terminal, ["10.0.0.5"])[0].run()
        assert terminal.typed[0].endswith("ON LINE" + "\n" * 4)

    def test_a_machine_with_no_address_shows_the_loopback_address(self):
        terminal = FakeTerminal()
        slipper_for(terminal, [])[0].run()
        assert "LINE 1 ....... 127.0.0.1\n" in terminal.typed[0]

    def test_the_log_says_the_loopback_address_when_there_is_no_network(self, caplog):
        with caplog.at_level(logging.INFO, logger="pom.conductor.boot_slipper"):
            slipper_for(FakeTerminal(), [])[0].run()
        assert any("address 127.0.0.1" in r.getMessage() for r in caplog.records)

    def test_every_address_is_listed_when_there_is_more_than_one(self):
        terminal = FakeTerminal()
        slipper_for(terminal, ["192.168.1.23", "10.41.0.1"])[0].run()
        assert "LINE 1 ....... 192.168.1.23, 10.41.0.1\n" in terminal.typed[0]

    def test_the_text_that_was_typed_is_kept(self):
        terminal = FakeTerminal()
        slipper, _ = slipper_for(terminal, ["10.0.0.5"])
        slipper.run()
        assert slipper.text == terminal.typed[0]

    def test_a_machine_whose_name_cannot_be_read_still_gets_its_slip(self):
        terminal = FakeTerminal()
        floor = FloorKeeper(recovering_seconds=0)

        def no_name():
            raise OSError("no name")
        slipper = BootSlipper(floor, terminal, PortentFormatter(65, 4, 4, ""), TEMPLATE, lambda: ["10.0.0.5"], hostname=no_name,
                              grace_seconds=0, retry_seconds=0, ears_shut_seconds=0)
        assert slipper.run() is True and "HOST unknown" in terminal.typed[0]

    def test_a_template_with_no_slots_is_typed_as_it_is(self):
        terminal = FakeTerminal()
        slipper_for(terminal, ["10.0.0.5"], template="JUST A TERMINAL")[0].run()
        assert terminal.typed[0].startswith("JUST A TERMINAL")

    def test_braces_that_are_not_slots_are_left_for_the_formatter_not_filled(self):
        terminal = FakeTerminal()
        slipper_for(terminal, ["10.0.0.5"], template="MODE {terminal} {address}")[0].run()
        assert terminal.typed[0].startswith("MODE (terminal) 10.0.0.5")      # the print wheel has no braces, so the formatter types parentheses

    def test_the_addresses_are_logged_and_no_other_text(self, caplog):
        terminal = FakeTerminal()
        with caplog.at_level(logging.INFO, logger="pom.conductor.boot_slipper"):
            slipper_for(terminal, ["192.168.1.23"])[0].run()
        messages = [r.getMessage() for r in caplog.records]
        assert any("192.168.1.23" in m for m in messages) and not any("CADETWRITER" in m for m in messages)


class TestWaitingForTheTypewriter:
    def test_nothing_is_typed_and_the_floor_is_not_held_while_it_is_offline(self):
        terminal = FakeTerminal(offline_asks=3)
        slipper, floor = slipper_for(terminal, ["10.0.0.5"])
        assert slipper.run() is True
        assert terminal.asked == 4 and len(terminal.typed) == 1

    def test_the_floor_stays_free_while_waiting(self):
        gate, seen = threading.Event(), []

        class Waiting(FakeTerminal):
            def online(self):
                seen.append(floor.state)
                return gate.is_set()
        terminal = Waiting()
        slipper, floor = slipper_for(terminal, ["10.0.0.5"], retry_seconds=0.01)
        slipper.start()
        threading.Event().wait(0.1)
        assert seen and set(seen) == {FloorState.LISTENING} and terminal.typed == []
        gate.set()
        slipper._thread.join(timeout=3)
        assert slipper.typed is True

    def test_a_stopped_slipper_that_never_saw_the_typewriter_types_nothing(self):
        terminal = FakeTerminal(offline_asks=10 ** 9)
        slipper, _ = slipper_for(terminal, [], retry_seconds=0.01)
        slipper.start()
        thread = slipper._thread
        slipper.stop()
        assert slipper.typed is False and terminal.typed == []
        assert not thread.is_alive()                          # stop ends the waiting; it is not left to run on

    def test_it_waits_the_retry_time_between_asks_and_not_busily(self):
        waited = []
        terminal = FakeTerminal(offline_asks=2)
        slipper, _ = slipper_for(terminal, ["10.0.0.5"], retry_seconds=4.5)
        slipper._stopped = SimpleNamespace(wait=lambda s: waited.append(s) or False, is_set=lambda: False, set=lambda: None)
        slipper.run()
        assert waited.count(4.5) >= 2


class TestWaitingForAnAddress:
    def test_it_types_at_once_when_an_address_is_already_there(self):
        calls = []
        terminal = FakeTerminal()
        floor = FloorKeeper(recovering_seconds=0)

        def find():
            calls.append(1)
            return ["10.0.0.5"]
        slipper = BootSlipper(floor, terminal, PortentFormatter(65, 4, 4, ""), TEMPLATE, find, hostname=lambda: "pom1",
                              grace_seconds=60, retry_seconds=0.0, ears_shut_seconds=0)
        slipper.run()
        assert len(calls) == 1 and "10.0.0.5" in terminal.typed[0]

    def test_an_address_that_arrives_within_the_grace_is_the_one_typed(self):
        answers = [[], [], ["192.168.1.23"]]
        terminal, floor = FakeTerminal(), FloorKeeper(recovering_seconds=0)
        slipper = BootSlipper(floor, terminal, PortentFormatter(65, 4, 4, ""), TEMPLATE, lambda: answers.pop(0) if len(answers) > 1 else answers[0],
                              hostname=lambda: "pom1", grace_seconds=60, retry_seconds=0.0, ears_shut_seconds=0)
        slipper.run()
        assert "192.168.1.23" in terminal.typed[0]

    def test_with_no_address_after_the_grace_it_types_anyway_with_the_loopback_address(self):
        now = [0.0]

        def clock():
            now[0] += 1.0                          # every look at the clock is a second
            return now[0]
        terminal, floor = FakeTerminal(), FloorKeeper(recovering_seconds=0)
        slipper = BootSlipper(floor, terminal, PortentFormatter(65, 4, 4, ""), TEMPLATE, lambda: [], hostname=lambda: "pom1",
                              grace_seconds=5, retry_seconds=0.0, ears_shut_seconds=0, clock=clock)
        assert slipper.run() is True and "127.0.0.1" in terminal.typed[0]

    def test_a_zero_grace_does_not_wait_at_all(self):
        calls = []
        terminal, floor = FakeTerminal(), FloorKeeper(recovering_seconds=0)

        def find():
            calls.append(1)
            return []
        BootSlipper(floor, terminal, PortentFormatter(65, 4, 4, ""), TEMPLATE, find, hostname=lambda: "pom1", grace_seconds=0,
                    retry_seconds=0.0, ears_shut_seconds=0).run()
        assert len(calls) == 1


class TestTheFloor:
    def test_the_floor_is_taken_for_the_slip_and_given_back_to_recovery(self):
        states = []

        class Watching(FakeTerminal):
            def transmit(self, text, still_alive=None):
                states.append((floor.state, floor.holder))
                return super().transmit(text, still_alive)
        terminal = Watching()
        slipper, floor = slipper_for(terminal, ["10.0.0.5"])
        slipper.run()
        assert states == [(FloorState.PRINTING, "boot slip")]
        assert floor.state in (FloorState.RECOVERING, FloorState.LISTENING)

    def test_the_heartbeat_is_handed_to_the_terminal_so_a_long_wait_is_not_a_hang(self):
        terminal = FakeTerminal()
        slipper_for(terminal, ["10.0.0.5"])[0].run()
        assert terminal.heartbeat_seen == [True]

    def test_a_held_floor_is_waited_for_not_taken(self):
        floor = FloorKeeper(recovering_seconds=0)
        turn = floor.take_floor("room")
        terminal = FakeTerminal()
        slipper, _ = slipper_for(terminal, ["10.0.0.5"], floor=floor, retry_seconds=0.01)
        slipper.start()
        threading.Event().wait(0.1)
        assert terminal.typed == [] and floor.holder == "room"
        turn.finish()
        slipper._thread.join(timeout=3)
        assert slipper.typed is True and len(terminal.typed) == 1

    def test_the_slip_waits_for_the_ears_to_shut_before_typing(self):
        waited = []
        terminal = FakeTerminal()
        slipper, _ = slipper_for(terminal, ["10.0.0.5"], ears_shut_seconds=2.5)
        slipper._stopped = SimpleNamespace(wait=lambda s: waited.append(s) or False, is_set=lambda: False, set=lambda: None)
        slipper.run()
        assert 2.5 in waited

    def test_stopping_while_waiting_for_the_floor_gives_up_and_types_nothing(self):
        floor = FloorKeeper(recovering_seconds=0)
        floor.take_floor("room")
        terminal = FakeTerminal()
        slipper, _ = slipper_for(terminal, ["10.0.0.5"], floor=floor, retry_seconds=0.01)
        slipper.start()
        slipper.stop()
        assert terminal.typed == [] and slipper.typed is False


class TestWhenTheTypewriterFails:
    def test_a_typewriter_that_vanishes_before_the_first_character_is_waited_for_again(self):
        terminal = FakeTerminal(results=[PrintResult(0, 0.0, False)])
        slipper, floor = slipper_for(terminal, ["10.0.0.5"])
        assert slipper.run() is True
        assert len(terminal.typed) == 2 and slipper.typed is True

    def test_a_typewriter_that_fails_part_way_is_not_typed_to_again(self, caplog):
        terminal = FakeTerminal(results=[PrintResult(12, 0.0, False)])
        slipper, _ = slipper_for(terminal, ["10.0.0.5"])
        with caplog.at_level(logging.WARNING, logger="pom.conductor.boot_slipper"):
            assert slipper.run() is False
        assert len(terminal.typed) == 1 and slipper.typed is False
        assert any("not typed again" in r.getMessage() for r in caplog.records)

    def test_the_floor_is_given_back_after_a_failure(self):
        terminal = FakeTerminal(results=[PrintResult(12, 0.0, False)])
        slipper, floor = slipper_for(terminal, ["10.0.0.5"])
        slipper.run()
        assert floor.state is not FloorState.PRINTING and floor.holder is None


class TestItNeverStopsThePrinter:
    def test_an_error_in_the_thread_is_a_warning_not_a_crash(self, caplog):
        class Broken(FakeTerminal):
            def online(self):
                raise RuntimeError("boom")
        slipper, _ = slipper_for(Broken(), ["10.0.0.5"])
        with caplog.at_level(logging.WARNING, logger="pom.conductor.boot_slipper"):
            slipper.start()
            slipper._thread.join(timeout=3)
        assert slipper.typed is False
        assert any("RuntimeError" in r.getMessage() for r in caplog.records)

    def test_stop_is_safe_to_repeat_and_before_start(self):
        slipper, _ = slipper_for(FakeTerminal(), [])
        slipper.stop()
        slipper.stop()

    def test_start_twice_runs_one_thread(self):
        terminal = FakeTerminal()
        slipper, _ = slipper_for(terminal, ["10.0.0.5"])
        slipper.start()
        first = slipper._thread
        slipper.start()
        assert slipper._thread is first
        first.join(timeout=3)
        assert len(terminal.typed) == 1
