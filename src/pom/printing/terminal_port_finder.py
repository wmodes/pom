"""The TerminalPortFinder: which serial port is the terminal, found by asking the machine instead of by a number.

A USB serial device is `/dev/ttyUSB0` after one boot and `/dev/ttyUSB1` after another, so a setting that names the number breaks after the boot
that shuffles them. The machine also keeps a stable name for each USB serial device, the one that says what it is
(`/dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller-if00-port0`), and that does not move. This lists those.

The rule (`auto`, a name, the fallback, the log, the retry) is the DeviceFinder's, shared with the microphone's CaptureCardFinder; see
pom/device_finder.py. What is particular to serial ports is here:
  * WHAT IS AVAILABLE is whatever the search pattern (setting `terminal.port_search`) matches: by default the USB serial devices under
    /dev/serial/by-id. A terminal on a port that has no such name (a GPIO serial port) is chosen by writing its path as the setting, which is
    handed back as written when nothing is found.
  * A NAME MAY BE ANY PART of the device's path, which holds its by-id name (`Prolific`), or of the real device it points to (`ttyUSB0`).
  * The listing and the real-path lookup are passed in (`lister`, `real_path`), so a test can describe any machine's ports without having one.
"""
import glob
import os
from typing import Callable, List, Optional

from pom.bookkeeper.bookkeeper import Bookkeeper
from pom.device_finder import AUTO, Device, DeviceFinder, DeviceFinderError
from pom.bookkeeper.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

__all__ = ["AUTO", "TerminalPortFinder", "TerminalPortFinderError"]


class TerminalPortFinderError(DeviceFinderError):
    """Raised for any lookup problem, but only in strict mode (development and tests)."""


class TerminalPortFinder(DeviceFinder):
    """Turns the terminal's port setting into a serial device that can be opened."""
    KIND = "serial port"

    def __init__(self, search_pattern: str, lister: Callable[[str], list] = glob.glob,
                 real_path: Callable[[str], str] = os.path.realpath, strict: bool = False, bookkeeper: Optional[Bookkeeper] = None):
        """Args:
            search_pattern: Where the serial devices are looked for (setting `terminal.port_search`), a glob such as /dev/serial/by-id/*.
            lister: A function pattern -> list of matching paths. Default: glob.glob. Tests give a fake.
            real_path: A function path -> the real device it points to. Default: os.path.realpath. Tests give a fake.
            strict: Raise TerminalPortFinderError on any problem instead of recording it.
            bookkeeper: The Bookkeeper the entry point built; it hands this class its logger. None (a test or a tool) gives a plain one.
        """
        super().__init__(strict=strict, error=TerminalPortFinderError, bookkeeper=bookkeeper)
        self._search_pattern = search_pattern
        self._lister = lister
        self._real_path = real_path

    def _available(self) -> List[Device]:
        """One Device per path the search pattern matches: its address is the path (which holds the by-id name), and it also answers to the real device it points to."""
        return [Device(path, (self._real_path(path),)) for path in self._lister(self._search_pattern)]
