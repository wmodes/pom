"""The ScorerWarmer: sends the scorer model one throwaway request as soon as its server is up, so no real sentence pays for the first.

Every request to the scorer model starts with the same long instruction (the scoring prompt) followed by one sentence. llama-server keeps
the evaluated start of a prompt and reuses it for the next request, so a request costs only its sentence (about 20 tokens, about 1 s on the
Pi). The very first request after the server starts has nothing to reuse and evaluates the whole prompt (300 tokens, 15.7 s measured on
the Pi). The warm-up pays that once, before anyone has said anything, and its answer is thrown away.

Design decisions, and why:
  * IT RUNS IN THE BACKGROUND AND WAITS FOR THE SERVER. At boot the Printer may start before the scorer's server has loaded (it did, on the
    Pi's reboot test), and the Printer must listen at once whatever state the servers are in (see pom/printer.py). So this does not run
    once at start-up and give up: a thread polls the server's health until it answers, warms it, and ends.
  * It uses the same ClipScorer the Printer scores with, so the warm-up request is built exactly as a real one (same prompt, same sentence
    format) and the cached start matches. It is a request to the server only: it touches no bar, no history and no monitor page.
  * Nothing here may stop the oracle (see pom/__init__.py). A warm-up that fails is a warning, never an exception; the first real
    clip then pays for it, as it would have without a warmer.
  * It only covers the start. A server restarted later (a crash and systemd's restart) loses its cache again; the scorer's longer
    timeout covers that clip.
  * The warm-up sentence is a fixed stand-in (`WARM_UP_CLIP` in the ScorerModelClient). Nothing that was said in the room is used here.
"""
import threading
import time
from typing import Callable

from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_POLL_SECONDS = 5.0           # how often to ask whether the server is up yet
DEFAULT_ATTEMPTS = 3                 # warm-up requests that may fail, with the server up, before it gives up


class ScorerWarmer:
    """Warms the scorer model once its server answers.

    Attributes:
        warmed: True once a warm-up request has been answered with a score.
    """

    def __init__(self, scorer, server_answers: Callable[[], bool], poll_seconds: float = DEFAULT_POLL_SECONDS,
                 attempts: int = DEFAULT_ATTEMPTS, clock: Callable[[], float] = time.monotonic):
        """Args:
            scorer: Anything with `warm_up() -> bool` (the ClipScorer): True if the model answered with a score.
            server_answers: Returns True when the scorer's server is up and ready for a request.
            poll_seconds: Seconds between checks while the server is not up (and between failed warm-ups).
            attempts: How many warm-up requests may fail, with the server up, before it gives up.
            clock: Returns seconds; injected so tests need no waiting.
        """
        self._scorer, self._server_answers = scorer, server_answers
        self._poll_seconds, self._attempts, self._clock = poll_seconds, attempts, clock
        self._stopped = threading.Event()
        self._thread = None
        self.warmed = False

    def start(self) -> None:
        """Begins waiting and warming in a background thread. Safe to call once; it ends by itself when done or stopped."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._run_safely, name="scorer-warmer", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Ends the waiting. Safe from a signal handler or another thread, and safe to repeat."""
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)           # a request in flight is left to end with the process (the thread is a daemon)

    def run(self) -> bool:
        """Waits for the server, then warms it; blocks until warmed, out of attempts, or stopped. True if the model answered."""
        failures = 0
        while not self._stopped.is_set():
            if self._server_answers():
                started = self._clock()
                if self._scorer.warm_up():
                    self.warmed = True
                    _log.info("the scorer model is warmed up (its scoring prompt is cached): %.1f s", self._clock() - started)
                    return True
                failures += 1
                if failures >= self._attempts:
                    _log.warning("the scorer model did not answer %d warm-up requests; the first clip will pay for the first request", failures)
                    return False
            self._stopped.wait(self._poll_seconds)
        return False

    def _run_safely(self) -> None:
        """The thread's body: `run`, with anything that goes wrong made a warning, since a warm-up is never worth a crash."""
        try:
            self.run()
        except Exception as error:                    # nothing here may stop the oracle
            _log.warning("the scorer warm-up stopped on an error (%s); the first clip will pay for the first request", type(error).__name__)
