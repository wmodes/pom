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

from pom.bookkeeper.bookkeeper import Bookkeeper
from pom.defaults import BootSlipSettings

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module

WHO = "boot slip"                       # the label the floor knows this holder by
NO_NETWORK_ADDRESS = "127.0.0.1"


class BootSlipper:
    """Types the boot slip once, when the typewriter is online.

    Attributes:
        boot_slip: The `[boot_slip]` settings it was handed (it reads `text`, `address_grace_seconds` and `retry_seconds`).
        typed: True once the slip has been typed.
        text: The slip as it was typed (the template filled in and formatted), or None until then.
    """

    def __init__(self, floor, terminal, formatter, find_addresses: Callable[[], list], *, boot_slip: BootSlipSettings, ears_shut_seconds: float,
                 hostname: Callable[[], str] = socket.gethostname, clock: Callable[[], float] = time.monotonic,
                 bookkeeper: Optional[Bookkeeper] = None):
        """Args:
            floor: The FloorKeeper (`take_floor(who)` gives a Turn or None).
            terminal: The TerminalPort or ConsoleTerminal (`online()` and `transmit(text, still_alive)`).
            formatter: The PortentFormatter that sets text for the paper (`format(text)`).
            find_addresses: Returns the machine's usable IPv4 addresses as a list of strings (the AddressFinder's `find`).
            boot_slip: The `[boot_slip]` section: `text` (the slip's words, with `{address}` and `{hostname}` slots),
                `address_grace_seconds` (how long, once the typewriter is online, to wait for an address before typing without one) and
                `retry_seconds` (seconds between asks while the typewriter is offline or the floor is held).
            ears_shut_seconds: Seconds to wait after taking the floor, so the ears are shut before the first character (worked out by the
                entry point from the Conductor's check interval).
            hostname: Returns the machine's name.
            clock: Returns seconds; injected so tests need no waiting.
            bookkeeper: The Bookkeeper the entry point built; it hands this class its logger. None (a test or a tool) gives a plain one.
        """
        self.logger = (bookkeeper or Bookkeeper()).logger_for(__name__, debug=DEBUG)
        self._floor, self._terminal, self._formatter = floor, terminal, formatter
        self.boot_slip = boot_slip
        self._find_addresses, self._hostname = find_addresses, hostname
        self._ears_shut, self._clock = ears_shut_seconds, clock
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
                self._stopped.wait(self.boot_slip.retry_seconds)
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
                self.logger.info("the boot slip is typed (%d characters; address %s)", result.characters, ", ".join(addresses) or NO_NETWORK_ADDRESS)
                return True
            if result.characters > 0:
                self.logger.warning("the boot slip stopped after %d characters; it is not typed again", result.characters)
                return False
            self.logger.info("the typewriter went away before the boot slip began; waiting for it again")
        return False

    def _wait_for_an_address(self) -> list:
        """The machine's addresses, waiting up to the grace period for the first one to appear. May be empty."""
        deadline = self._clock() + self.boot_slip.address_grace_seconds
        while True:
            addresses = self._find_addresses()
            if addresses or self._clock() >= deadline or self._stopped.is_set():
                return addresses
            self._stopped.wait(min(self.boot_slip.retry_seconds, max(0.0, deadline - self._clock())))

    def _take_the_floor(self):
        """A Turn once the floor is free (first come, first served), or None if stopped first."""
        while not self._stopped.is_set():
            turn = self._floor.take_floor(WHO)
            if turn is not None:
                return turn
            self._stopped.wait(self.boot_slip.retry_seconds)
        return None

    def _filled(self, addresses: list) -> str:
        """The template with the address (or the loopback address, if there is none) and the machine's name filled in."""
        try:
            name = self._hostname()
        except Exception:                                     # a machine with no name still gets a slip
            name = "unknown"
        return self.boot_slip.text.replace("{address}", ", ".join(addresses) or NO_NETWORK_ADDRESS).replace("{hostname}", name)

    def _run_safely(self) -> None:
        """The thread's body: `run`, with anything that goes wrong made a warning, since a slip is never worth a crash."""
        try:
            self.run()
        except Exception as error:                    # nothing here may stop the oracle
            self.logger.warning("the boot slip stopped on an error (%s)", type(error).__name__)
