"""The DeviceFinder: the one rule that finds the microphone and the terminal, by what the machine says is there instead of by a number.

Device numbers (the sound card `plughw:0,0`, the serial port `/dev/ttyUSB0`) are given out in the order the kernel meets the devices, and
that order is not the same from one boot to the next. A setting that names a number therefore breaks after the boot that shuffles them.
This asks the machine what is there NOW and chooses by name. The sound cards (CaptureCardFinder) and the terminal's serial ports
(TerminalPortFinder) differ only in how they list what is there; the rule below is the same for both and is written once, here.

THE RULE. The setting is `auto` or a name.
  * `auto` (or a blank setting) takes the first available device in sorted order, so the choice is the same at every boot.
  * A name is any part of a device's name or address, in any case (a card's id or description, a USB device's name, a device path). It
    takes the first device it matches. A name that matches nothing falls back to whatever is there, as `auto`, with a WARNING.
  * More than one device to choose from (`auto`, or a name that matches several) is a WARNING that says what was chosen and what was there.
  * Nothing there: the setting is handed back as written, so opening it fails loudly in the owner's usual way, and the owner asks again at its
    own retry interval, for ever. Every lookup that finds a device logs one INFO line (the setting, what was available, what was chosen).
    A lookup that finds nothing logs only the first `log_limit` times in a row; the count starts again when a device is found.

Design decisions, and why:
  * IT LOOKS AGAIN EVERY TIME IT IS ASKED. A device plugged in later, or one that came back under another number, is found on the next try
    with no restart. The caller decides how often it asks (its own retry setting).
  * IT NEVER RAISES (a helper must never stop the oracle; see pom/__init__.py). A listing that fails is the same as nothing there, and the
    warning names only the kind of error. `strict=True` raises DeviceFinderError instead, for tests and dev tools.
  * The lines it logs hold device names, never anything that was said or heard.
  * A finder is asked from one thread at a time (its owner holds a lock, or has one thread), so the count of tries needs no lock of its own.
"""
from dataclasses import dataclass
from typing import List

from pom.bookkeeper.fault_recorder import FaultRecorder
from pom.bookkeeper.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

AUTO = "auto"                           # the setting's keyword for "find it yourself"


class DeviceFinderError(ValueError):
    """Raised for any lookup problem, but only in strict mode (development and tests)."""


@dataclass(frozen=True, slots=True)
class Device:
    """One thing the machine says is there.

    Attributes:
        address: What to open (an ALSA device such as `plughw:CARD=ATR4697USB,DEV=0`, or a serial device path). Devices are put in order by it.
        names: The other names a setting may match any part of (a card's id and description, a USB device's name).
    """
    address: str
    names: tuple = ()


class DeviceFinder:
    """Turns the device setting into a device that can be opened. A subclass says what is there by implementing `_available`.

    Attributes:
        report: Problems met (nothing there, several there, a name not found, a listing that failed). Never raised outside strict mode.
        found: True if the last lookup found a device to use (False before the first lookup, and when the last one found nothing).
    """
    KIND = "device"                     # what a subclass's devices are called in the log ("sound card", "serial port")

    def __init__(self, log_limit: int, strict: bool = False, error: type = DeviceFinderError):
        """Args:
            log_limit: How many lookups in a row that find nothing are logged, a whole number of at least 1 (validated by the config module).
            strict: Raise `error` on any problem instead of recording it.
            error: The exception type strict mode raises (a subclass gives its own).
        """
        self.report = FaultRecorder(strict=strict, error=error)      # it keeps the lines for tests; this class does the logging, so it is not capped
        self._log_limit = log_limit
        self._tries = 0                                              # lookups in a row that found nothing
        self.found = False

    def _available(self) -> List[Device]:
        """What the machine says is there right now. A subclass implements this; it may raise (that counts as nothing there)."""
        raise NotImplementedError

    def resolve(self, setting: str) -> str:
        """The device to open for this setting: see the module's rule. Never raises outside strict mode."""
        try:
            devices = sorted(self._available(), key=lambda device: device.address)
        except Exception as problem:                                   # a failed listing is the same as nothing there
            self.found = False
            self._found_nothing(f"could not list the {self.KIND}s ({type(problem).__name__})", setting)
            return setting
        if not devices:
            self.found = False
            self._found_nothing(f"no {self.KIND} was found", setting)
            return setting
        self.found = True
        if self._tries:
            _log.info("%s found after %d tries", self.KIND, self._tries)
        self._tries = 0

        wanted = str(setting).strip().lower()
        auto = wanted == AUTO                                            # (a blank setting matches every device, so it is auto too)
        pool = devices if auto else [d for d in devices if any(wanted in text.lower() for text in (d.address, *d.names))]
        available = ", ".join(device.address for device in devices)
        if not auto and not pool:
            pool = devices
            self._warn(f"no {self.KIND} matches {setting!r}; using what is there instead. available: {available}; chosen: {pool[0].address}")
        chosen = pool[0].address
        _log.info("%s: setting %r; available: %s; chosen: %s", self.KIND, setting, available, chosen)
        if len(pool) > 1:
            which = "auto" if auto else f"{setting!r}"
            self._warn(f"several {self.KIND}s to choose from ({which}), so the first is used. chosen: {chosen}; available: {available}. "
                       f"Set the name to choose another")
        return chosen

    def _found_nothing(self, what: str, setting: str) -> None:
        """Counts a lookup that found nothing, and logs it only while the count is within the limit."""
        self._tries += 1
        if self._tries > self._log_limit:
            return
        note = f"; not logging the next tries until one is found" if self._tries == self._log_limit else ""
        self._warn(f"{what} (setting {setting!r}); trying again{note}")

    def _warn(self, message: str) -> None:
        """Records a problem (raises in strict mode) and logs it."""
        self.report.warn(message)
        _log.warning(message)
