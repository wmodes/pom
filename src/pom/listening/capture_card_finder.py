"""The CaptureCardFinder: which sound card is the microphone, found by asking the machine instead of by a number.

ALSA numbers the sound cards in the order the kernel meets them, and that order is not the same from one boot to the next: on the Pi the two
HDMI outputs and a USB microphone take turns at card 0. A device written as `plughw:0,0` therefore hears nothing after the boot that
puts the microphone elsewhere. This asks `arecord -l`, which lists only the cards that can record, and names the card by its id
(`plughw:CARD=ATR4697USB,DEV=0`), which does not move.

The rule (`auto`, a name, the fallback, the log, the retry) is the DeviceFinder's, shared with the terminal's TerminalPortFinder; see
pom/device_finder.py. What is particular to sound cards is here:
  * ONE DEVICE PER CARD. A card with several devices counts once (its lowest device), so it is not "several cards".
  * A NAME MAY BE ANY PART of the card's id (`ATR4697USB`), its description (`ATR4697-USB`), its device name (`USB Audio`) or the whole
    device string.
  * `arecord` IS ASKED IN ENGLISH (`LC_ALL=C`), because its output is read by its words and is translated on a machine set to another language.
  * The listing is passed in (`lister`), so a test can describe any machine's cards without having one.
"""
import os
import re
import shutil
import subprocess
from typing import Callable, List, Optional

from pom.device_finder import AUTO, Device, DeviceFinder, DeviceFinderError
from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

__all__ = ["AUTO", "CaptureCardFinder", "CaptureCardFinderError"]

_CARD_LINE = re.compile(r"^card\s+\d+:\s+(?P<id>\S+)\s+\[(?P<description>.*?)\],\s+device\s+(?P<device>\d+):"
                        r"(?:\s+(?P<name>[^\[\n]*?)\s*(?:\[(?P<device_description>.*?)\])?)?$", re.MULTILINE)


class CaptureCardFinderError(DeviceFinderError):
    """Raised for any lookup problem, but only in strict mode (development and tests)."""


class CaptureCardFinder(DeviceFinder):
    """Turns the microphone setting into a device `arecord -D` can open."""
    KIND = "sound card"

    def __init__(self, timeout_seconds: float, log_limit: int, arecord: str = "arecord",
                 lister: Optional[Callable[[], str]] = None, strict: bool = False):
        """Args:
            timeout_seconds: How long to wait for `arecord -l` before giving up, above 0 (setting `listening.device_lookup_seconds`).
            log_limit: How many lookups in a row that find nothing are logged (setting `device_search_log_limit`).
            arecord: The program to run (a path or a name on the PATH). Tests give a stand-in.
            lister: Returns the text `arecord -l` prints. Defaults to running the program.
            strict: Raise CaptureCardFinderError on any problem instead of recording it.
        """
        super().__init__(log_limit, strict=strict, error=CaptureCardFinderError)
        self._timeout_seconds = timeout_seconds          # validated once, by the config module
        self._arecord = arecord
        self._lister = lister if lister is not None else self._run_arecord

    def _available(self) -> List[Device]:
        """One Device per card that can record, by the card's id, with the lowest device number."""
        cards = {}
        for match in _CARD_LINE.finditer(self._lister()):
            card, number = match["id"], int(match["device"])
            if card not in cards or number < cards[card][0]:
                names = tuple(text for text in (match["description"], match["name"], match["device_description"]) if text)       # (the id is in the address)
                cards[card] = (number, names)
        return [Device(f"plughw:CARD={card},DEV={number}", names) for card, (number, names) in cards.items()]

    def _run_arecord(self) -> str:
        """Runs `arecord -l` (in English) and returns what it printed."""
        program = shutil.which(self._arecord) if isinstance(self._arecord, str) else None
        if program is None:
            raise FileNotFoundError(self._arecord)
        done = subprocess.run([program, "-l"], capture_output=True, text=True, timeout=self._timeout_seconds, check=False,
                              env={**os.environ, "LC_ALL": "C"})
        return done.stdout
