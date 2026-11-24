"""Tests for pom.listening.capture_card_finder, with the sound-card listing described by the test instead of read from a machine."""
import stat
import sys

import pytest

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


def test_auto_becomes_the_recording_card_by_its_name_not_its_number():
    assert finder(PI_LISTING).resolve(AUTO) == "plughw:CARD=ATR4697USB,DEV=0"


def test_the_name_is_the_same_whichever_number_the_card_has():
    on_card_zero = PI_LISTING.replace("card 2:", "card 0:")
    assert finder(on_card_zero).resolve(AUTO) == finder(PI_LISTING).resolve(AUTO)


def test_the_device_number_is_kept():
    assert finder(PI_LISTING.replace("device 0:", "device 1:")).resolve(AUTO) == "plughw:CARD=ATR4697USB,DEV=1"


def test_the_keyword_is_auto():
    assert AUTO == "auto"


@pytest.mark.parametrize("named", ["plughw:CARD=Other,DEV=0", "plughw:0,0", "hw:1,0", "default", "Auto", " auto"])
def test_any_other_device_is_handed_back_as_written_and_the_listing_is_not_asked_for(named):
    def lister():
        raise AssertionError("the listing was asked for")
    assert CaptureCardFinder(5.0, lister=lister).resolve(named) == named


def test_a_plain_lookup_has_no_complaints():
    found = finder(PI_LISTING)
    found.resolve(AUTO)
    assert found.report.ok


def test_several_cards_use_the_first_and_warn_naming_them():
    found = finder(TWO_CARDS)
    assert found.resolve(AUTO) == "plughw:CARD=ATR4697USB,DEV=0"
    assert len(found.report.warnings) == 1
    warning = found.report.warnings[0]
    assert "(ATR4697USB, Camera)" in warning and "using ATR4697USB" in warning and "listening.device" in warning


def test_the_warning_lists_the_cards_in_the_order_they_were_listed_so_the_first_is_the_one_used():
    listing = ("card 0: Zeta [Z Mic], device 0: USB Audio [USB Audio]\ncard 1: Alpha [A Mic], device 0: USB Audio [USB Audio]\n"
               "card 2: Zeta [Z Mic], device 1: USB Audio [USB Audio #1]\n")
    found = finder(listing)
    assert found.resolve(AUTO) == "plughw:CARD=Zeta,DEV=0"
    assert "(Zeta, Alpha)" in found.report.warnings[0] and "using Zeta" in found.report.warnings[0]


def test_two_devices_on_one_card_are_one_card_and_do_not_warn():
    listing = PI_LISTING + "card 2: ATR4697USB [ATR4697-USB], device 1: USB Audio [USB Audio #1]\n"
    found = finder(listing)
    assert found.resolve(AUTO) == "plughw:CARD=ATR4697USB,DEV=0" and found.report.ok


def test_no_card_hands_the_keyword_back_and_warns():
    found = finder(NO_CARDS)
    assert found.resolve(AUTO) == AUTO
    assert len(found.report.warnings) == 1 and "no sound card that can record" in found.report.warnings[0]


def test_an_empty_listing_is_no_card():
    assert finder("").resolve(AUTO) == AUTO


def test_a_failing_listing_hands_the_keyword_back_and_warns():
    def lister():
        raise OSError("sound system gone")
    found = CaptureCardFinder(5.0, lister=lister)
    assert found.resolve(AUTO) == AUTO
    assert len(found.report.warnings) == 1 and "OSError" in found.report.warnings[0]


def test_the_warning_does_not_carry_the_error_text():
    def lister():
        raise OSError("secret detail")
    found = CaptureCardFinder(5.0, lister=lister)
    found.resolve(AUTO)
    assert "secret detail" not in found.report.warnings[0]


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
    script.write_text(f"#!{sys.executable}\nimport sys, time\n{body}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_it_runs_arecord_with_the_list_flag_and_reads_what_it_prints(tmp_path):
    program = fake_arecord(tmp_path, "assert sys.argv[1:] == ['-l'], sys.argv\nprint('card 7: Mic [Some Mic], device 0: USB Audio [USB Audio]')")
    assert CaptureCardFinder(5.0, arecord=program).resolve(AUTO) == "plughw:CARD=Mic,DEV=0"


def test_a_missing_arecord_is_a_warning_not_a_crash(tmp_path):
    found = CaptureCardFinder(5.0, arecord=str(tmp_path / "no-such-program"))
    assert found.resolve(AUTO) == AUTO
    assert len(found.report.warnings) == 1 and "FileNotFoundError" in found.report.warnings[0]


def test_a_listing_that_hangs_is_given_up_on_after_the_timeout(tmp_path):
    program = fake_arecord(tmp_path, "time.sleep(30)")
    found = CaptureCardFinder(0.3, arecord=program)
    assert found.resolve(AUTO) == AUTO
    assert len(found.report.warnings) == 1 and "TimeoutExpired" in found.report.warnings[0]
