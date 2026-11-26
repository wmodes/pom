"""Tests for pom.device_finder: the one rule that finds the microphone and the terminal, through a small finder that lists what the test says."""

import pytest

from pom.device_finder import AUTO, Device, DeviceFinder, DeviceFinderError

LOGGER = "pom.device_finder"


class ListedFinder(DeviceFinder):
    """A finder whose list of devices is whatever the test sets (or an exception to raise)."""
    KIND = "gadget"

    def __init__(self, devices, strict=False):
        super().__init__(strict=strict)
        self.devices = devices

    def _available(self):
        if isinstance(self.devices, Exception):
            raise self.devices
        return list(self.devices)


def gadget(address, *names):
    return Device(address, names or (address,))


MIC = gadget("/dev/gadget/mic-1", "MicOne", "Big Studio Mic")
CAM = gadget("/dev/gadget/cam-2", "WebCam", "Camera Mic")


# --- auto -----------------------------------------------------------------------------------

def test_the_keyword_is_auto():
    assert AUTO == "auto"


def test_auto_takes_the_only_device_there_is():
    assert ListedFinder([MIC]).resolve("auto") == "/dev/gadget/mic-1"


@pytest.mark.parametrize("written", ["auto", "Auto", " AUTO ", "", "   "])
def test_auto_is_not_fussy_about_case_or_spaces_and_a_blank_setting_means_auto(written, logged):
    finder = ListedFinder([MIC, CAM])
    assert finder.resolve(written) == "/dev/gadget/cam-2"
    assert len(logged.of(finder).warnings()) == 1 and "several gadgets" in logged.of(finder).warnings()[0]       # the only complaint is that there are two


def test_auto_with_several_takes_the_first_in_sorted_order_whatever_order_they_were_listed_in():
    assert ListedFinder([MIC, CAM]).resolve("auto") == "/dev/gadget/cam-2"
    assert ListedFinder([CAM, MIC]).resolve("auto") == "/dev/gadget/cam-2"


def test_auto_with_several_warns_with_what_was_chosen_and_what_was_there(logged):
    finder = ListedFinder([MIC, CAM])
    finder.resolve("auto")
    warnings = logged.warnings(LOGGER)
    assert len(warnings) == 1 and "several gadgets" in warnings[0]
    assert "chosen: /dev/gadget/cam-2" in warnings[0] and "/dev/gadget/mic-1" in warnings[0]
    assert len(logged.of(finder).warnings()) == 1


def test_auto_with_one_device_has_no_complaints(logged):
    finder = ListedFinder([MIC])
    finder.resolve("auto")
    assert logged.of(finder).ok() and logged.warnings(LOGGER) == []


# --- a name -----------------------------------------------------------------------------------

@pytest.mark.parametrize("part", ["MicOne", "micone", "Studio", "STUDIO MIC", "mic-1", "gadget/mic"])
def test_a_name_is_any_part_of_any_of_the_devices_names_whatever_the_case(part):
    assert ListedFinder([CAM, MIC]).resolve(part) == "/dev/gadget/mic-1"


def test_a_name_that_matches_one_device_is_not_a_complaint(logged):
    finder = ListedFinder([CAM, MIC])
    finder.resolve("Studio")
    assert logged.of(finder).ok() and logged.warnings(LOGGER) == []


def test_a_name_that_matches_several_takes_the_first_in_sorted_order_and_warns(logged):
    finder = ListedFinder([MIC, CAM])
    assert finder.resolve("mic") == "/dev/gadget/cam-2"          # both have "mic" in a name; the sorted first is the camera
    assert len(logged.warnings(LOGGER)) == 1 and "several gadgets" in logged.warnings(LOGGER)[0]


def test_a_name_that_is_not_found_falls_back_to_whatever_is_there_and_warns(logged):
    finder = ListedFinder([MIC])
    assert finder.resolve("Nonesuch") == "/dev/gadget/mic-1"
    warnings = logged.warnings(LOGGER)
    assert len(warnings) == 1 and "'Nonesuch'" in warnings[0] and "/dev/gadget/mic-1" in warnings[0]


def test_a_name_that_is_not_found_with_several_there_falls_back_and_says_what_was_chosen_and_what_was_there(logged):
    finder = ListedFinder([MIC, CAM])
    assert finder.resolve("Nonesuch") == "/dev/gadget/cam-2"
    text = " ".join(logged.warnings(LOGGER))
    assert "'Nonesuch'" in text and "chosen: /dev/gadget/cam-2" in text and "/dev/gadget/mic-1" in text


# --- the log ----------------------------------------------------------------------------------

@pytest.mark.parametrize("setting, devices", [("auto", [MIC]), ("Studio", [MIC, CAM]), ("Nonesuch", [MIC])])
def test_every_lookup_that_finds_something_logs_the_setting_what_was_there_and_what_was_chosen_at_info(logged, setting, devices):
    ListedFinder(devices).resolve(setting)
    infos = logged.infos(LOGGER)
    assert len(infos) == 1
    assert f"{setting!r}" in infos[0] and "available:" in infos[0] and "chosen: /dev/gadget/mic-1" in infos[0]
    assert "gadget" in infos[0]
    for device in devices:
        assert device.address in infos[0]


def test_each_lookup_that_finds_something_logs_again_so_the_log_shows_every_time_it_looked(logged):
    finder = ListedFinder([MIC])
    finder.resolve("auto")
    finder.resolve("auto")
    assert len(logged.infos(LOGGER)) == 2


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

def test_nothing_there_hands_the_setting_back_as_written_so_opening_it_fails_loudly():
    finder = ListedFinder([])
    assert finder.resolve("auto") == "auto"
    assert ListedFinder([]).resolve("/dev/ttyACM0") == "/dev/ttyACM0"


def test_nothing_there_is_a_warning_every_time_and_the_finder_does_no_muting_of_its_own(logged):
    finder = ListedFinder([])
    for _ in range(200):                                   # the log's LogMuter mutes the repeats; the finder just says it
        finder.resolve("auto")
    warnings = logged.warnings(LOGGER)
    assert len(warnings) == 200 and all("no gadget" in w and "trying again" in w for w in warnings)
    assert not any("not logging" in w for w in warnings)


def test_the_finder_counts_nothing_so_coming_back_is_just_the_found_line_after_the_warnings(logged):
    finder = ListedFinder([])
    for _ in range(4):
        finder.resolve("auto")
    finder.devices = [MIC]
    finder.resolve("auto")
    assert not any("tries" in m for m in logged.infos(LOGGER) + logged.warnings(LOGGER))
    assert len(logged.warnings(LOGGER)) == 4 and len(logged.infos(LOGGER)) == 1


def test_a_problem_is_reported_once_through_the_recorder_not_logged_a_second_way(logged):
    finder = ListedFinder([])
    finder.resolve("auto")
    assert len(logged.warnings(LOGGER)) == 1 and len(logged.of(finder).warnings()) == 1


def test_a_listing_that_fails_hands_back_a_name_as_written_too():
    assert ListedFinder(OSError("x")).resolve("Studio") == "Studio"


def test_a_name_with_spaces_round_it_is_still_a_name_and_gives_no_complaint(logged):
    finder = ListedFinder([MIC, CAM])
    assert finder.resolve("  studio ") == "/dev/gadget/mic-1" and logged.of(finder).ok()


def test_a_listing_that_fails_counts_as_nothing_there_and_names_the_kind_of_error_only(logged):
    finder = ListedFinder(OSError("secret detail"))
    assert finder.resolve("auto") == "auto"
    warnings = logged.warnings(LOGGER)
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


# --- The exact words of each warning --------------------------------------------------------

def test_the_listing_failed_warning_says_the_kind_the_error_and_the_setting(logged):
    finder = ListedFinder(OSError("secret detail"))
    finder.resolve("auto")
    assert logged.of(finder).warnings() == ["could not list the gadgets (OSError) (setting 'auto'); trying again"]


def test_the_nothing_found_warning_says_the_kind_and_the_setting(logged):
    finder = ListedFinder([])
    finder.resolve("MicOne")
    assert logged.of(finder).warnings() == ["no gadget was found (setting 'MicOne'); trying again"]


def test_the_not_found_warning_says_the_setting_what_is_there_and_what_was_chosen(logged):
    finder = ListedFinder([MIC, CAM])
    finder.resolve("Nonesuch")
    assert logged.of(finder).warnings()[0] == ("no gadget matches 'Nonesuch'; using what is there instead. "
                                               "available: /dev/gadget/cam-2, /dev/gadget/mic-1; chosen: /dev/gadget/cam-2")


def test_the_several_warning_says_which_setting_what_was_chosen_and_what_was_there(logged):
    finder = ListedFinder([MIC, CAM])
    finder.resolve("Mic")
    assert logged.of(finder).warnings() == ["several gadgets to choose from ('Mic'), so the first is used. chosen: /dev/gadget/cam-2; "
                                            "available: /dev/gadget/cam-2, /dev/gadget/mic-1. Set the name to choose another"]
