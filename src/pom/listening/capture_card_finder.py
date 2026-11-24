"""The CaptureCardFinder: which sound card is the microphone, found by asking the machine instead of by a number.

ALSA numbers the sound cards in the order the kernel meets them, and that order is not the same from one boot to the next: on the Pi the two
HDMI outputs and a USB microphone take turns at card 0. A device written as `plughw:0,0` therefore hears nothing after the boot that
puts the microphone elsewhere. This asks `arecord -l`, which lists only the cards that can record, and names the card by its id
(`plughw:CARD=ATR4697USB,DEV=0`), which does not move.

Design decisions, and why:
  * THE SETTING STAYS THE PERSON'S CHOICE. Only the keyword `auto` is looked up; any other device name is handed back as it was written, so a
    clone with two microphones can still pin one by name. The keyword is the whole feature: nobody has to know a card's name to copy the project.
  * IT LOOKS AGAIN EVERY TIME IT IS ASKED. The Printer asks whenever it starts listening, so a microphone plugged in later, or one that came
    back under another number, is found on the next try with no restart.
  * SEVERAL CARDS: THE FIRST IS USED, WITH A WARNING naming them, so the person can pin the one they meant. NO CARD: the keyword is handed
    back unchanged, so `arecord` fails loudly and the Printer's usual "cannot hear" fault says so; it is looked up again at the next try.
  * IT NEVER RAISES (a helper must never stop the oracle; see pom/__init__.py). A missing `arecord`, a slow or failing listing is a warning
    and the keyword comes back. `strict=True` raises CaptureCardFinderError instead, for tests and dev tools.
  * The listing is passed in (`lister`), so a test can describe any machine's cards without having one.
"""
import re
import shutil
import subprocess
from typing import Callable, Optional

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

AUTO = "auto"                           # the setting's keyword for "find the microphone yourself"; nothing else is looked up
_CARD_LINE = re.compile(r"^card\s+\d+:\s+(?P<id>\S+)\s+\[.*?\],\s+device\s+(?P<device>\d+):", re.MULTILINE)


class CaptureCardFinderError(ValueError):
    """Raised for any lookup problem, but only in strict mode (development and tests)."""


class CaptureCardFinder:
    """Turns the device setting into a device `arecord -D` can open.

    Attributes:
        report: Problems met (no `arecord`, a failed listing, no card, several cards). Never raised outside strict mode.
    """

    def __init__(self, timeout_seconds: float, arecord: str = "arecord",
                 lister: Optional[Callable[[], str]] = None, strict: bool = False):
        """Args:
            timeout_seconds: How long to wait for `arecord -l` before giving up, above 0 (setting `listening.device_lookup_seconds`).
            arecord: The program to run (a path or a name on the PATH). Tests give a stand-in.
            lister: Returns the text `arecord -l` prints. Defaults to running the program.
            strict: Raise CaptureCardFinderError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=CaptureCardFinderError, logger=_log)
        self._timeout_seconds = timeout_seconds          # validated once, by the config module
        self._arecord = arecord
        self._lister = lister if lister is not None else self._run_arecord

    def resolve(self, device: str) -> str:
        """The device to record from. `auto` becomes the first capture card's `plughw:CARD=<id>,DEV=<n>`; anything else is returned as given."""
        if device != AUTO:
            return device
        try:
            cards = self._cards(self._lister())
        except Exception as problem:                       # a failed listing is the same as no card
            self.report.warn(f"could not list the sound cards ({type(problem).__name__}); the microphone was not found")
            return device
        if not cards:
            self.report.warn("no sound card that can record was found; the Printer cannot hear until a microphone is plugged in")
            return device
        if len({card for card, _ in cards}) > 1:
            names = ", ".join(dict.fromkeys(card for card, _ in cards))      # in the order listed, so the first is the one used
            self.report.warn(f"several sound cards can record ({names}); using {cards[0][0]}. "
                             f"Set listening.device to a card's name to choose one")
        card, number = cards[0]
        return f"plughw:CARD={card},DEV={number}"

    @staticmethod
    def _cards(listing: str) -> list:
        """[(card id, device number)] in the order `arecord -l` printed them."""
        return [(m["id"], m["device"]) for m in _CARD_LINE.finditer(listing)]

    def _run_arecord(self) -> str:
        """Runs `arecord -l` and returns what it printed."""
        program = shutil.which(self._arecord) if isinstance(self._arecord, str) else None
        if program is None:
            raise FileNotFoundError(self._arecord)
        done = subprocess.run([program, "-l"], capture_output=True, text=True, timeout=self._timeout_seconds, check=False)
        return done.stdout
