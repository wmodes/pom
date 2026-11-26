"""Tests for pom.printing.terminal_port_finder, with the serial ports described by the test instead of read from a machine.

The rule itself (auto, a name, the fallback, the log) is tested in tests/test_device_finder.py; here is what is particular to serial ports."""
import logging

import pytest

from pom.device_finder import DeviceFinder
from pom.printing.terminal_port_finder import AUTO, TerminalPortFinder, TerminalPortFinderError

BY_ID = "/dev/serial/by-id"
PROLIFIC = f"{BY_ID}/usb-Prolific_Technology_Inc._USB-Serial_Controller-if00-port0"
ARDUINO = f"{BY_ID}/usb-Arduino__www.arduino.cc__0043_85735313437351E0E1E1-if00"
REAL = {PROLIFIC: "/dev/ttyUSB0", ARDUINO: "/dev/ttyACM0"}


def finder(paths, strict=False, pattern=f"{BY_ID}/*"):
    asked = []

    def lister(searched):
        asked.append(searched)
        return list(paths)
    found = TerminalPortFinder(pattern, lister=lister, real_path=lambda path: REAL.get(path, path), strict=strict)
    found.asked = asked
    return found


def test_it_is_a_device_finder_and_the_keyword_is_auto():
    assert issubclass(TerminalPortFinder, DeviceFinder) and AUTO == "auto"
    assert issubclass(TerminalPortFinderError, ValueError)


def test_it_lists_what_the_search_pattern_matches():
    found = finder([PROLIFIC], pattern="/somewhere/*")
    found.resolve(AUTO)
    assert found.asked == ["/somewhere/*"]


def test_auto_opens_the_stable_name_not_the_number():
    assert finder([PROLIFIC]).resolve(AUTO) == PROLIFIC


@pytest.mark.parametrize("name", ["Prolific", "prolific", "USB-Serial_Controller", "port0", "ttyUSB0", "/dev/ttyUSB0", PROLIFIC])
def test_a_name_is_any_part_of_the_usb_name_or_of_the_real_device_it_points_to(name):
    found = finder([ARDUINO, PROLIFIC])
    assert found.resolve(name) == PROLIFIC and found.report.ok


def test_a_name_picks_the_other_port_when_it_names_that_one():
    found = finder([ARDUINO, PROLIFIC])
    assert found.resolve("arduino") == ARDUINO and found.report.ok


def test_auto_with_several_takes_the_first_in_sorted_order_whatever_order_the_machine_listed_them_in_and_warns():
    for listed in ([PROLIFIC, ARDUINO], [ARDUINO, PROLIFIC]):
        found = finder(listed)
        assert found.resolve(AUTO) == ARDUINO
        assert len(found.report.warnings) == 1
        assert "several serial ports" in found.report.warnings[0] and PROLIFIC in found.report.warnings[0] and ARDUINO in found.report.warnings[0]


def test_a_name_that_is_not_found_falls_back_to_the_port_that_is_there_and_warns():
    found = finder([PROLIFIC])
    assert found.resolve("/dev/ttyACM0") == PROLIFIC
    assert len(found.report.warnings) == 1 and "/dev/ttyACM0" in found.report.warnings[0]


def test_with_no_port_at_all_a_setting_is_handed_back_as_written():
    assert finder([]).resolve("/dev/ttyACM0") == "/dev/ttyACM0"
    assert finder([]).resolve(AUTO) == AUTO


def test_it_looks_again_each_time_so_a_terminal_plugged_in_later_is_found():
    plugged = []
    found = TerminalPortFinder("/x/*", lister=lambda pattern: list(plugged), real_path=lambda path: path)
    assert found.resolve(AUTO) == AUTO
    plugged.append(PROLIFIC)
    assert found.resolve(AUTO) == PROLIFIC


def test_a_search_that_fails_is_the_same_as_nothing_there_and_does_not_carry_the_error_text():
    def lister(pattern):
        raise OSError("secret detail")
    found = TerminalPortFinder("/x/*", lister=lister)
    assert found.resolve(AUTO) == AUTO
    assert len(found.report.warnings) == 1 and "OSError" in found.report.warnings[0] and "secret detail" not in found.report.warnings[0]


@pytest.mark.parametrize("paths, setting", [([], AUTO), ([PROLIFIC, ARDUINO], AUTO), ([PROLIFIC], "Nonesuch")])
def test_strict_raises_instead_of_recording(paths, setting):
    with pytest.raises(TerminalPortFinderError):
        finder(paths, strict=True).resolve(setting)


def test_with_the_real_directory_and_links_a_symlink_is_found_by_the_name_of_the_device_it_points_to(tmp_path):
    (tmp_path / "ttyUSB7").write_text("")
    by_id = tmp_path / "by-id"
    by_id.mkdir()
    (by_id / "usb-Some_Adapter-port0").symlink_to(tmp_path / "ttyUSB7")
    found = TerminalPortFinder(str(by_id / "*"))
    assert found.resolve("ttyUSB7") == str(by_id / "usb-Some_Adapter-port0") and found.report.ok
    assert found.resolve(AUTO) == str(by_id / "usb-Some_Adapter-port0")


def test_with_the_real_directory_missing_there_is_nothing_there(tmp_path):
    assert TerminalPortFinder(str(tmp_path / "no-such-directory" / "*")).resolve(AUTO) == AUTO
