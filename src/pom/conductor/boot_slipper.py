"""The BootSlipper: types a short slip when the Printer and the typewriter are both online, telling whoever is there the Pi's address.

A Pi in a gallery has no screen. When it comes online, the typewriter types a few lines that look like a terminal announcing itself,
and one of them is the address the Pi has, so the monitoring page and ssh can be reached. It types every time the Printer starts
(which, on the Pi, is every time the machine boots), whether or not there is a network:

    CADETWRITER - IBM-1620
    MODE ......... TERMINAL
    SELF TEST .... OK
    LINE 1 ....... 192.168.1.23
    ON LINE

(The words are `[boot_slip] text` in config/pom.toml; `{address}` and `{hostname}` are filled in.)

Design decisions, and why:
  * IT WAITS IN THE BACKGROUND, LIKE THE SCORER'S WARMER. The Printer must listen at once, whatever state the world is in: the servers
    may still be loading, the typewriter may be switched on later than the Pi, Wi-Fi may not be up. So a thread does the waiting and
    nothing else waits on it. It holds nothing while it waits, so the room is heard the whole time.
  * IT SPEAKS ONLY WHEN THE TYPEWRITER IS ONLINE, whenever that is: it asks the terminal port, quietly, every `retry_seconds`.
  * THE ADDRESS IS READ AT THE LAST MOMENT, once the typewriter is there. If the machine has none yet it waits up to
    `address_grace_seconds` for Wi-Fi to come up, then prints anyway: the loopback address, 127.0.0.1, says plainly that there is no network.
    Every usable address is listed (the home network's and the hotspot's, if both are up).
  * PRINTING TAKES THE FLOOR, like a portent, so the Printer is deaf while the slip types and does not hear its own typing. The floor is
    first come, first served, so if a portent's turn holds it, the slip tries again every `retry_seconds`. Having got the floor it waits
    `ears_shut_seconds` before typing: the Conductor shuts the ears when its watching thread next looks at the floor, which is at most
    `check_seconds` away. After the slip the usual recovery follows.
  * IT TYPES ONCE. If the typewriter goes away before a single character is typed, it goes back to waiting. If it goes away part-way, the
    start is on the paper and the slip is not typed again, as with a portent.
  * NOTHING HERE MAY STOP THE ORACLE (see pom/__init__.py). The thread's body catches everything; a slip that fails is a warning.
  * The addresses go in the log (they are not anything said in the room); nothing else is. The monitor password is never printed.
  * Time and waiting are passed in or injectable, so tests need no real waiting.
"""
import socket
import threading
import time
from typing import Callable, Optional

from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_GRACE_SECONDS = 15.0            # how long to wait for an address once the typewriter is online
DEFAULT_RETRY_SECONDS = 5.0             # how often to ask whether the typewriter is online, and whether the floor is free
DEFAULT_EARS_SHUT_SECONDS = 2.0         # after taking the floor: the Conductor's check interval plus a margin
WHO = "boot slip"                       # the label the floor knows this holder by
NO_NETWORK_ADDRESS = "127.0.0.1"


class BootSlipper:
    """Types the boot slip once, when the typewriter is online.

    Attributes:
        typed: True once the slip has been typed.
        text: The slip as it was typed (the template filled in and formatted), or None until then.
    """

    def __init__(self, floor, terminal, formatter, template: str, find_addresses: Callable[[], list],
                 hostname: Callable[[], str] = socket.gethostname, grace_seconds: float = DEFAULT_GRACE_SECONDS,
                 retry_seconds: float = DEFAULT_RETRY_SECONDS, ears_shut_seconds: float = DEFAULT_EARS_SHUT_SECONDS,
                 clock: Callable[[], float] = time.monotonic):
        """Args:
            floor: The FloorKeeper (`take_floor(who)` gives a Turn or None).
            terminal: The TerminalPort or ConsoleTerminal (`online()` and `transmit(text, still_alive)`).
            formatter: The PortentFormatter that sets text for the paper (`format(text)`).
            template: The slip's words, with `{address}` and `{hostname}` slots.
            find_addresses: Returns the machine's usable IPv4 addresses as a list of strings (the AddressFinder's `find`).
            hostname: Returns the machine's name.
            grace_seconds: How long, once the typewriter is online, to wait for an address before typing without one. 0 or more.
            retry_seconds: Seconds between asks while the typewriter is offline or the floor is held. 0 or more.
            ears_shut_seconds: Seconds to wait after taking the floor, so the ears are shut before the first character. 0 or more.
            clock: Returns seconds; injected so tests need no waiting.
        """
        self._floor, self._terminal, self._formatter, self._template = floor, terminal, formatter, template
        self._find_addresses, self._hostname = find_addresses, hostname
        self._grace, self._retry, self._ears_shut, self._clock = grace_seconds, retry_seconds, ears_shut_seconds, clock
        self._stopped = threading.Event()
        self._thread = None
        self.typed = False
        self.text: Optional[str] = None

    def start(self) -> None:
        """Begins waiting and typing in a background thread. Safe to call once; it ends by itself when done or stopped."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._run_safely, name="boot-slipper", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Ends the waiting. Safe from a signal handler or another thread, and safe to repeat."""
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)           # a slip being typed is left to end with the process (the thread is a daemon)

    def run(self) -> bool:
        """Waits for the typewriter, then types the slip; blocks until typed, started and failed, or stopped. True if it was all typed."""
        while not self._stopped.is_set():
            if not self._terminal.online():
                self._stopped.wait(self._retry)
                continue
            addresses = self._wait_for_an_address()
            turn = self._take_the_floor()
            if turn is None:                                  # stopped while waiting
                return False
            with turn:                                        # the floor is given back whatever happens
                self._stopped.wait(self._ears_shut)
                if self._stopped.is_set() or not turn.start_printing():
                    return False
                text = self._formatter.format(self._filled(addresses))
                result = self._terminal.transmit(text, still_alive=turn.still_printing)
            if result.completed:
                self.typed, self.text = True, text
                _log.info("the boot slip is typed (%d characters; address %s)", result.characters, ", ".join(addresses) or NO_NETWORK_ADDRESS)
                return True
            if result.characters > 0:
                _log.warning("the boot slip stopped after %d characters; it is not typed again", result.characters)
                return False
            _log.info("the typewriter went away before the boot slip began; waiting for it again")
        return False

    def _wait_for_an_address(self) -> list:
        """The machine's addresses, waiting up to the grace period for the first one to appear. May be empty."""
        deadline = self._clock() + self._grace
        while True:
            addresses = self._find_addresses()
            if addresses or self._clock() >= deadline or self._stopped.is_set():
                return addresses
            self._stopped.wait(min(self._retry, max(0.0, deadline - self._clock())))

    def _take_the_floor(self):
        """A Turn once the floor is free (first come, first served), or None if stopped first."""
        while not self._stopped.is_set():
            turn = self._floor.take_floor(WHO)
            if turn is not None:
                return turn
            self._stopped.wait(self._retry)
        return None

    def _filled(self, addresses: list) -> str:
        """The template with the address (or the loopback address, if there is none) and the machine's name filled in."""
        try:
            name = self._hostname()
        except Exception:                                     # a machine with no name still gets a slip
            name = "unknown"
        return self._template.replace("{address}", ", ".join(addresses) or NO_NETWORK_ADDRESS).replace("{hostname}", name)

    def _run_safely(self) -> None:
        """The thread's body: `run`, with anything that goes wrong made a warning, since a slip is never worth a crash."""
        try:
            self.run()
        except Exception as error:                    # nothing here may stop the oracle
            _log.warning("the boot slip stopped on an error (%s)", type(error).__name__)
