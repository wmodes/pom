"""Tests for pom.device_finder: the one rule that finds the microphone and the terminal, through a small finder that lists what the test says."""
import logging

import pytest

from pom.device_finder import AUTO, Device, DeviceFinder, DeviceFinderError

LOGGER = "pom.device_finder"


class ListedFinder(DeviceFinder):
    """A finder whose list of devices is whatever the test sets (or an exception to raise)."""
    KIND = "gadget"

    def __init__(self, devices, log_limit=3, strict=False):
        super().__init__(log_limit, strict=strict)
        self.devices = devices

    def _available(self):
        if isinstance(self.devices, Exception):
            raise self.devices
        return list(self.devices)


def gadget(address, *names):
    return Device(address, names or (address,))


MIC = gadget("/dev/gadget/mic-1", "MicOne", "Big Studio Mic")
CAM = gadget("/dev/gadget/cam-2", "WebCam", "Camera Mic")


def lines(caplog, level):
    return [r.getMessage() for r in caplog.records if r.name == LOGGER and r.levelno == level]


# --- auto -----------------------------------------------------------------------------------

def test_the_keyword_is_auto():
    assert AUTO == "auto"


def test_auto_takes_the_only_device_there_is():
    assert ListedFinder([MIC]).resolve("auto") == "/dev/gadget/mic-1"


@pytest.mark.parametrize("written", ["auto", "Auto", " AUTO ", "", "   "])
def test_auto_is_not_fussy_about_case_or_spaces_and_a_blank_setting_means_auto(written):
    finder = ListedFinder([MIC, CAM])
    assert finder.resolve(written) == "/dev/gadget/cam-2"
    assert len(finder.report.warnings) == 1 and "several gadgets" in finder.report.warnings[0]       # the only complaint is that there are two


def test_auto_with_several_takes_the_first_in_sorted_order_whatever_order_they_were_listed_in():
    assert ListedFinder([MIC, CAM]).resolve("auto") == "/dev/gadget/cam-2"
    assert ListedFinder([CAM, MIC]).resolve("auto") == "/dev/gadget/cam-2"


def test_auto_with_several_warns_with_what_was_chosen_and_what_was_there(caplog):
    finder = ListedFinder([MIC, CAM])
    with caplog.at_level(logging.INFO, logger=LOGGER):
        finder.resolve("auto")
    warnings = lines(caplog, logging.WARNING)
    assert len(warnings) == 1 and "several gadgets" in warnings[0]
    assert "chosen: /dev/gadget/cam-2" in warnings[0] and "/dev/gadget/mic-1" in warnings[0]
    assert len(finder.report.warnings) == 1


def test_auto_with_one_device_has_no_complaints(caplog):
    finder = ListedFinder([MIC])
    with caplog.at_level(logging.INFO, logger=LOGGER):
        finder.resolve("auto")
    assert finder.report.ok and lines(caplog, logging.WARNING) == []


# --- a name -----------------------------------------------------------------------------------

@pytest.mark.parametrize("part", ["MicOne", "micone", "Studio", "STUDIO MIC", "mic-1", "gadget/mic"])
def test_a_name_is_any_part_of_any_of_the_devices_names_whatever_the_case(part):
    assert ListedFinder([CAM, MIC]).resolve(part) == "/dev/gadget/mic-1"


def test_a_name_that_matches_one_device_is_not_a_complaint(caplog):
    finder = ListedFinder([CAM, MIC])
    with caplog.at_level(logging.INFO, logger=LOGGER):
        finder.resolve("Studio")
    assert finder.report.ok and lines(caplog, logging.WARNING) == []


def test_a_name_that_matches_several_takes_the_first_in_sorted_order_and_warns(caplog):
    finder = ListedFinder([MIC, CAM])
    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert finder.resolve("mic") == "/dev/gadget/cam-2"          # both have "mic" in a name; the sorted first is the camera
    assert len(lines(caplog, logging.WARNING)) == 1 and "several gadgets" in lines(caplog, logging.WARNING)[0]


def test_a_name_that_is_not_found_falls_back_to_whatever_is_there_and_warns(caplog):
    finder = ListedFinder([MIC])
    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert finder.resolve("Nonesuch") == "/dev/gadget/mic-1"
    warnings = lines(caplog, logging.WARNING)
    assert len(warnings) == 1 and "'Nonesuch'" in warnings[0] and "/dev/gadget/mic-1" in warnings[0]


def test_a_name_that_is_not_found_with_several_there_falls_back_and_says_what_was_chosen_and_what_was_there(caplog):
    finder = ListedFinder([MIC, CAM])
    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert finder.resolve("Nonesuch") == "/dev/gadget/cam-2"
    text = " ".join(lines(caplog, logging.WARNING))
    assert "'Nonesuch'" in text and "chosen: /dev/gadget/cam-2" in text and "/dev/gadget/mic-1" in text


# --- the log ----------------------------------------------------------------------------------

@pytest.mark.parametrize("setting, devices", [("auto", [MIC]), ("Studio", [MIC, CAM]), ("Nonesuch", [MIC])])
def test_every_lookup_that_finds_something_logs_the_setting_what_was_there_and_what_was_chosen_at_info(caplog, setting, devices):
    with caplog.at_level(logging.INFO, logger=LOGGER):
        ListedFinder(devices).resolve(setting)
    infos = lines(caplog, logging.INFO)
    assert len(infos) == 1
    assert f"{setting!r}" in infos[0] and "available:" in infos[0] and "chosen: /dev/gadget/mic-1" in infos[0]
    assert "gadget" in infos[0]
    for device in devices:
        assert device.address in infos[0]


def test_each_lookup_that_finds_something_logs_again_so_the_log_shows_every_time_it_looked(caplog):
    finder = ListedFinder([MIC])
    with caplog.at_level(logging.INFO, logger=LOGGER):
        finder.resolve("auto")
        finder.resolve("auto")
    assert len(lines(caplog, logging.INFO)) == 2


# --- found ----------------------------------------------------------------------------------

def test_found_says_whether_the_last_lookup_found_a_device():
    finder = ListedFinder([])
    assert finder.found is False                             # nothing has been looked up yet
    finder.resolve("auto")
    assert finder.found is False
    finder.devices = [MIC]
    finder.resolve("auto")
    assert finder.found is True
    finder.devices = []
    finder.resolve("auto")
    assert finder.found is False


def test_found_is_true_for_a_name_that_fell_back_to_what_is_there_and_false_when_the_listing_failed():
    finder = ListedFinder([MIC])
    finder.resolve("Nonesuch")
    assert finder.found is True
    finder.devices = OSError("x")
    finder.resolve("auto")
    assert finder.found is False


# --- nothing there ----------------------------------------------------------------------------

def test_nothing_there_hands_the_setting_back_as_written_so_opening_it_fails_loudly(caplog):
    finder = ListedFinder([])
    assert finder.resolve("auto") == "auto"
    assert ListedFinder([]).resolve("/dev/ttyACM0") == "/dev/ttyACM0"


def test_nothing_there_is_a_warning_each_time_up_to_the_limit_and_then_no_more(caplog):
    finder = ListedFinder([], log_limit=3)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        for _ in range(10):
            finder.resolve("auto")
    warnings = lines(caplog, logging.WARNING)
    assert len(warnings) == 3 and all("no gadget" in w for w in warnings)
    assert "not logging" in warnings[-1]                      # the last one says why the rest are quiet


def test_the_count_starts_again_when_a_device_is_found(caplog):
    finder = ListedFinder([], log_limit=2)
    with caplog.at_level(logging.INFO, logger=LOGGER):
        for _ in range(5):
            finder.resolve("auto")
        finder.devices = [MIC]
        finder.resolve("auto")
        finder.devices = []
        for _ in range(5):
            finder.resolve("auto")
    assert len(lines(caplog, logging.WARNING)) == 4           # two before it was found, two after it went away again


def test_finding_a_device_after_trying_says_how_many_tries_it_took(caplog):
    finder = ListedFinder([], log_limit=2)
    for _ in range(4):
        finder.resolve("auto")
    finder.devices = [MIC]
    with caplog.at_level(logging.INFO, logger=LOGGER):
        finder.resolve("auto")
    assert any("after 4 tries" in m for m in lines(caplog, logging.INFO))


def test_a_listing_that_fails_hands_back_a_name_as_written_too():
    assert ListedFinder(OSError("x")).resolve("Studio") == "Studio"


def test_a_name_with_spaces_round_it_is_still_a_name_and_gives_no_complaint():
    finder = ListedFinder([MIC, CAM])
    assert finder.resolve("  studio ") == "/dev/gadget/mic-1" and finder.report.ok


def test_a_listing_that_fails_counts_as_nothing_there_and_names_the_kind_of_error_only(caplog):
    finder = ListedFinder(OSError("secret detail"))
    with caplog.at_level(logging.INFO, logger=LOGGER):
        assert finder.resolve("auto") == "auto"
    warnings = lines(caplog, logging.WARNING)
    assert len(warnings) == 1 and "OSError" in warnings[0] and "secret detail" not in warnings[0]


def test_it_looks_again_each_time_so_a_device_plugged_in_later_is_found():
    finder = ListedFinder([])
    assert finder.resolve("auto") == "auto"
    finder.devices = [MIC]
    assert finder.resolve("auto") == "/dev/gadget/mic-1"


# --- strict -----------------------------------------------------------------------------------

@pytest.mark.parametrize("devices, setting", [([], "auto"), ([MIC, CAM], "auto"), ([MIC], "Nonesuch"), (OSError("x"), "auto")])
def test_strict_raises_instead_of_recording(devices, setting):
    with pytest.raises(DeviceFinderError):
        ListedFinder(devices, strict=True).resolve(setting)


def test_strict_is_quiet_when_all_is_well():
    assert ListedFinder([MIC], strict=True).resolve("auto") == "/dev/gadget/mic-1"
