"""The StatusBoard: the one place every part's status is kept, for the monitoring page to read.

Each part says how it is (a Light) by posting it here; nothing else is kept. The page reads the lights and never asks a part anything.

THE RULE. A part is expected to post, and to keep posting: each part is registered with how long a post stays good (`down_after_seconds`).
A part whose last post is older than that is DOWN, whatever it last said, and a part that has never posted is DOWN. No news is bad news: a part
that hangs or dies cannot post "I am down", so its silence says it. (This is the usual heartbeat, or dead man's switch, pattern.)

Design decisions, and why:
  * THE BOARD ONLY KEEPS AND JUDGES. It does not know how to check anything. Parts that can post themselves (the microphone, as sound arrives)
    do; parts that have an address (the servers, the printer) are asked by a ServicePoller, which posts their answers here.
  * THE ORDER IS THE ORDER THEY WERE REGISTERED IN, which is the order the page shows them.
  * Time is the injected clock (monotonic seconds), so a test can age a post without waiting.
  * It never raises (a helper must never stop the oracle; see pom/__init__.py). A post from a name nobody registered is ignored and recorded
    once in the log. `strict=True` raises StatusBoardError instead, for tests and dev tools.
  * One lock guards the posts; the lights are returned as a copy.
"""
import threading
import time
from typing import Callable, Dict, Optional, Tuple

from pom.bookkeeper.bookkeeper import Bookkeeper
from pom.monitor.light import Light

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module


class StatusBoardError(ValueError):
    """Raised for any board problem, but only in strict mode (development and tests)."""


class StatusBoard:
    """Keeps each part's last Light and when it was posted; says DOWN for a part that has gone quiet.

    Attributes:
        logger: Where this class says what it has to say (a LogRecorder from the Bookkeeper).
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic, strict: bool = False, bookkeeper: Optional[Bookkeeper] = None):
        """Args:
            clock: Returns seconds, steadily increasing (monotonic).
            strict: Raise StatusBoardError on any problem instead of logging it.
            bookkeeper: The Bookkeeper the entry point built; it hands this class its logger. None (a test or a tool) gives a plain one.
        """
        self.logger = (bookkeeper or Bookkeeper()).logger_for(__name__, StatusBoardError, DEBUG, strict)
        self._clock = clock
        self._lock = threading.Lock()
        self._down_after: Dict[str, float] = {}                       # name -> how long a post stays good, in registration order
        self._posted: Dict[str, Optional[Tuple[Light, float]]] = {}   # name -> (the light, when it was posted), or None before the first post
        self._unknown: set = set()                                    # names posted but never registered, so each is recorded once

    def expect(self, name: str, down_after_seconds: float) -> None:
        """Registers a part that is expected to post, and how long a post stays good (above 0; the config module checks it)."""
        with self._lock:
            self._down_after[name] = down_after_seconds
            self._posted.setdefault(name, None)

    def post(self, name: str, light: Light) -> None:
        """Records how a part is now. Cheap and safe from any thread."""
        with self._lock:
            if name in self._down_after:
                self._posted[name] = (Light.of(light), self._clock())
                return
            first = name not in self._unknown
            self._unknown.add(name)
        if first:
            self.logger.warning("a status was posted for %r, which is not on the board", name)

    def lights(self) -> Dict[str, Light]:
        """The light for each registered part, in order: what it last posted, or DOWN if that is too old or it never posted."""
        now = self._clock()
        with self._lock:
            lights = {}
            for name, down_after in self._down_after.items():
                posted = self._posted[name]
                lights[name] = posted[0] if posted is not None and now - posted[1] <= down_after else Light.DOWN
            return lights
