"""The AddressFinder: the machine's own IPv4 addresses, for the boot slip.

When the Printer comes online it types a short slip that tells whoever is standing at the typewriter which address the Pi has (see
pom/conductor/boot_slipper.py), so the monitoring page and ssh can be reached without a screen. This finds that address.

Design decisions, and why:
  * IT ASKS THE OPERATING SYSTEM ABOUT EVERY INTERFACE (psutil), not about the route to somewhere. The usual stand-in, opening a UDP socket
    toward an outside address and reading its own end, finds nothing when there is no default route, and that is exactly when the Pi is
    its own Wi-Fi hotspot (the Comitup fallback) and the address is the one a person needs. psutil is the standard, maintained library for
    this, and it works the same on the Pi and on a Mac.
  * ONLY ADDRESSES A PERSON CAN USE. Loopback (127.x.x.x), link-local (169.254.x.x, assigned when DHCP failed) and the unspecified address
    are left out, so "no address" means no usable network. Interfaces are listed in name order, so the answer does not change between runs.
  * IT NEVER RAISES. A lookup that fails is an empty list, which the slip prints as the loopback address, since nothing here is worth
    stopping the oracle for (see pom/__init__.py).
  * The lookup is passed in (`interfaces`), so a test can describe any network without having one.
"""
import ipaddress
import socket
from typing import Callable, Optional

import psutil

from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

LOOPBACK_ADDRESS = "127.0.0.1"          # what the slip shows when the machine has no usable address


class AddressFinder:
    """Finds this machine's usable IPv4 addresses."""

    def __init__(self, interfaces: Optional[Callable[[], dict]] = None):
        """Args:
            interfaces: Returns {interface name: [address records]} as `psutil.net_if_addrs` does (each record has `family` and `address`).
                Defaults to the operating system's own list.
        """
        self._interfaces = interfaces if interfaces is not None else psutil.net_if_addrs

    def find(self) -> list:
        """The usable IPv4 addresses as strings, ordered by interface name. Empty if there are none or the lookup failed. Never raises."""
        try:
            found = []
            for name, records in sorted(self._interfaces().items()):
                for record in records:
                    if record.family == socket.AF_INET and self._usable(record.address):
                        found.append(record.address)
            return found
        except Exception as problem:                   # a failed lookup is the same as no network
            _log.warning("could not list the network addresses (%s)", type(problem).__name__)
            return []

    @staticmethod
    def _usable(address: str) -> bool:
        """True for an address another machine could reach this one by."""
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        return not (ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast)
