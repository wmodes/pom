"""Tests for pom.listening.capture_card_finder, with the sound-card listing described by the test instead of read from a machine.

The rule itself (auto, a name, the fallback, the log) is tested in tests/test_device_finder.py; here is what is particular to sound cards."""
import logging
import stat
import sys

import pytest

from pom.device_finder import DeviceFinder
from pom.listening.capture_card_finder import AUTO, CaptureCardFinder, CaptureCardFinderError

# What `arecord -l` printed on the Pi (it lists only the cards that can record; the two HDMI outputs are not in it).
PI_LISTING = """**** List of CAPTURE Hardware Devices ****
card 2: ATR4697USB [ATR4697-USB], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
"""
TWO_CARDS = PI_LISTING + """card 3: Camera [USB Camera], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
  Subdevice #0: subdevice #0
"""
NO_CARDS = "arecord: device_list:274: no soundcards found...\n"


def finder(listing, strict=False):
    return CaptureCardFinder(5.0, lister=lambda: listing, strict=strict)


def test_it_is_a_device_finder_and_the_keyword_is_auto():
    assert issubclass(CaptureCardFinder, DeviceFinder) and AUTO == "auto"
    assert issubclass(CaptureCardFinderError, ValueError)


def test_auto_becomes_the_recording_card_by_its_name_not_its_number():
    assert finder(PI_LISTING).resolve(AUTO) == "plughw:CARD=ATR4697USB,DEV=0"


def test_the_name_is_the_same_whichever_number_the_card_has():
    on_card_zero = PI_LISTING.replace("card 2:", "card 0:")
    assert finder(on_card_zero).resolve(AUTO) == finder(PI_LISTING).resolve(AUTO)


def test_the_device_number_is_kept():
    assert finder(PI_LISTING.replace("device 0:", "device 1:")).resolve(AUTO) == "plughw:CARD=ATR4697USB,DEV=1"


@pytest.mark.parametrize("name", ["ATR4697USB", "atr4697", "ATR4697-USB", "usb audio", "4697"])
def test_a_name_is_any_part_of_the_cards_id_description_or_device_name(name, logged):
    found = finder(PI_LISTING)
    assert found.resolve(name) == "plughw:CARD=ATR4697USB,DEV=0" and logged.of(found).ok()       # one card, found by the name: no fallback warning


@pytest.mark.parametrize("name", ["Camera", "USB Camera", "usb camera"])
def test_a_name_picks_the_other_card_when_it_names_that_one_by_its_id_or_its_description(name, logged):
    found = finder(TWO_CARDS)
    assert found.resolve(name) == "plughw:CARD=Camera,DEV=0" and logged.of(found).ok()


def test_a_full_device_string_that_names_a_card_in_the_list_still_works():
    assert finder(TWO_CARDS).resolve("plughw:CARD=Camera,DEV=0") == "plughw:CARD=Camera,DEV=0"


def test_a_device_written_by_number_is_not_in_the_list_so_it_falls_back_to_the_card_that_is_there_with_a_warning(logged):
    found = finder(PI_LISTING)
    assert found.resolve("plughw:0,0") == "plughw:CARD=ATR4697USB,DEV=0"
    assert len(logged.of(found).warnings()) == 1 and "plughw:0,0" in logged.of(found).warnings()[0]


def test_with_no_card_at_all_a_device_written_by_number_is_handed_back_as_written():
    assert finder(NO_CARDS).resolve("plughw:0,0") == "plughw:0,0"


def test_several_cards_use_the_first_in_sorted_order_and_warn_naming_them_all(logged):
    found = finder(TWO_CARDS)
    assert found.resolve(AUTO) == "plughw:CARD=ATR4697USB,DEV=0"
    assert len(logged.of(found).warnings()) == 1
    warning = logged.of(found).warnings()[0]
    assert "several sound cards" in warning and "plughw:CARD=ATR4697USB,DEV=0" in warning and "plughw:CARD=Camera,DEV=0" in warning


def test_the_cards_are_taken_in_sorted_order_not_in_the_order_the_kernel_numbered_them():
    listing = ("card 0: Zeta [Z Mic], device 0: USB Audio [USB Audio]\ncard 1: Alpha [A Mic], device 0: USB Audio [USB Audio]\n")
    assert finder(listing).resolve(AUTO) == "plughw:CARD=Alpha,DEV=0"
    assert finder(listing.replace("card 0: Zeta", "card 5: Zeta")).resolve(AUTO) == "plughw:CARD=Alpha,DEV=0"


def test_two_devices_on_one_card_are_one_card_and_do_not_warn(logged):
    listing = PI_LISTING + "card 2: ATR4697USB [ATR4697-USB], device 1: USB Audio [USB Audio #1]\n"
    found = finder(listing)
    assert found.resolve(AUTO) == "plughw:CARD=ATR4697USB,DEV=0" and logged.of(found).ok()


def test_no_card_hands_the_keyword_back_and_warns(logged):
    found = finder(NO_CARDS)
    assert found.resolve(AUTO) == AUTO
    assert len(logged.of(found).warnings()) == 1 and "no sound card" in logged.of(found).warnings()[0]


def test_an_empty_listing_is_no_card():
    assert finder("").resolve(AUTO) == AUTO


def test_a_failing_listing_hands_the_keyword_back_and_warns_without_the_error_text(logged):
    def lister():
        raise OSError("secret detail")
    found = CaptureCardFinder(5.0, lister=lister)
    assert found.resolve(AUTO) == AUTO
    assert len(logged.of(found).warnings()) == 1 and "OSError" in logged.of(found).warnings()[0] and "secret detail" not in logged.of(found).warnings()[0]


def test_it_looks_again_each_time_so_a_microphone_plugged_in_later_is_found():
    listings = iter([NO_CARDS, PI_LISTING])
    found = CaptureCardFinder(5.0, lister=lambda: next(listings))
    assert found.resolve(AUTO) == AUTO
    assert found.resolve(AUTO) == "plughw:CARD=ATR4697USB,DEV=0"


@pytest.mark.parametrize("listing", [NO_CARDS, TWO_CARDS])
def test_strict_raises_instead_of_recording(listing):
    with pytest.raises(CaptureCardFinderError):
        finder(listing, strict=True).resolve(AUTO)


def test_strict_raises_for_a_failing_listing():
    def lister():
        raise OSError("gone")
    with pytest.raises(CaptureCardFinderError):
        CaptureCardFinder(5.0, lister=lister, strict=True).resolve(AUTO)


# --- running the real program (a stand-in for arecord) ---------------------------------------

def fake_arecord(tmp_path, body):
    script = tmp_path / "arecord"
    script.write_text(f"#!{sys.executable}\nimport os, sys, time\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_it_runs_arecord_with_the_list_flag_and_reads_what_it_prints(tmp_path):
    program = fake_arecord(tmp_path, "assert sys.argv[1:] == ['-l'], sys.argv\nprint('card 7: Mic [Some Mic], device 0: USB Audio [USB Audio]')")
    assert CaptureCardFinder(5.0, arecord=program).resolve(AUTO) == "plughw:CARD=Mic,DEV=0"


def test_it_asks_in_english_whatever_the_machines_language_because_it_reads_the_words(tmp_path, monkeypatch):
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    program = fake_arecord(tmp_path, "assert os.environ.get('LC_ALL') == 'C', os.environ.get('LC_ALL')\n"
                                     "print('card 7: Mic [Some Mic], device 0: USB Audio [USB Audio]')")
    assert CaptureCardFinder(5.0, arecord=program).resolve(AUTO) == "plughw:CARD=Mic,DEV=0"


def test_a_missing_arecord_is_a_warning_not_a_crash(tmp_path, logged):
    found = CaptureCardFinder(5.0, arecord=str(tmp_path / "no-such-program"))
    assert found.resolve(AUTO) == AUTO
    assert len(logged.of(found).warnings()) == 1 and "FileNotFoundError" in logged.of(found).warnings()[0]


def test_a_listing_that_hangs_is_given_up_on_after_the_timeout(tmp_path, logged):
    program = fake_arecord(tmp_path, "time.sleep(30)")
    found = CaptureCardFinder(0.3, arecord=program)
    assert found.resolve(AUTO) == AUTO
    assert len(logged.of(found).warnings()) == 1 and "TimeoutExpired" in logged.of(found).warnings()[0]
