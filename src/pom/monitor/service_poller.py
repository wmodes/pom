"""The ServicePoller: asks the parts that have an address how they are, on a timer, and posts the answers to the StatusBoard.

The three model servers and the printer's port cannot post for themselves, and a part that has hung cannot say so, so something has to ask. That
is this: one thread that runs each check every `every_seconds` and posts its Light. The first round runs at once when it starts. The board's rule
does the rest: if this thread stops, every light it feeds goes DOWN when its last post gets old.

Design decisions, and why:
  * THE CHECKS ARE PASSED IN (name -> a function that returns a Light), so this class knows nothing about whisper, llama-server or a serial
    port, and a test gives it any checks it likes. The entry point builds them.
  * A CHECK THAT RAISES IS A PART THAT DOES NOT ANSWER: it posts DOWN. One bad check never stops the others or the thread.
  * It runs on its own thread, always, so the lights are fresh whether or not anyone has the monitoring page open.
  * `stop()` ends the thread promptly (the wait between rounds is an event wait).
  * The settings are passed in (the entry point takes them from config); this module never reads Config.
"""
import threading
from typing import Callable, Mapping

from pom.defaults import MonitorSettings
from pom.monitor.light import Light
from pom.monitor.status_board import StatusBoard


JOIN_SECONDS = 5.0     # how long stopping waits for the thread


class ServicePoller:
    """Runs the checks every `[monitor] service_check_seconds` and posts each answer to the board.

    Attributes:
        monitor: The `[monitor]` settings it was handed (it reads `service_check_seconds`).
    """

    def __init__(self, board: StatusBoard, checks: Mapping[str, Callable[[], Light]], monitor: MonitorSettings):
        """Args:
            board: Where the answers are posted.
            checks: name -> a function returning the Light for that part. The names are the board's.
            monitor: The `[monitor]` section: `service_check_seconds`, how often to ask.
        """
        self._board = board
        self._checks = dict(checks)
        self.monitor = monitor
        self._stop = threading.Event()
        self._thread = None

    def poll_once(self) -> None:
        """Runs every check now and posts the answers. A check that raises posts DOWN."""
        for name, check in self._checks.items():
            try:
                light = Light.of(check())
            except Exception:                    # a check that fails is a part that does not answer
                light = Light.DOWN
            self._board.post(name, light)

    def start(self) -> None:
        """Starts asking, in a thread of its own; the first round is at once. Safe to call more than once."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="service-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stops asking. Safe to call more than once, or before start."""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(JOIN_SECONDS)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            if self._stop.wait(self.monitor.service_check_seconds):
                return
