"""The OracleModelClient: asks the oracle model for a portent.

When a hinge qualifies, the PromptBuilder makes the oracle prompt and this module sends it to the oracle model
(served by llama.cpp's `llama-server`, apart from the scorer's) and returns what the model wrote. It is only the
conversation with that server: one prompt in, one portent out, or None if none could be had. It knows nothing
about hinges or exemplars; the PromptBuilder does that.

The prompt goes to the model as a single user message, exactly as built. The client adds nothing to it and
takes nothing out of the reply but the whitespace around it, so what a dev tool shows is what the Printer
would type. The model chooses its own words: unlike the scorer, the request is not at temperature 0.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A server that is down, slow, or answering oddly gives
    None and a warning in the log, never an exception. `strict=True` raises OracleModelClientError instead, for
    tests and dev tools.
  * Neither the prompt (it holds the hinge) nor the portent is logged: nothing
    that was said in the room is kept. Logs and reports hold counts and timings.
  * The `[oracle]` settings object is passed in (the entry point takes it from Config); this module never reads Config.
  * The address is checked by the config (http or https only, so a mistyped setting cannot make it open a file).
  * The transport is injected (`post`), so tests run with a fake and need no server. The default uses the standard
    library, so there is nothing to install.
  * `chat_template_kwargs {"enable_thinking": False}` is sent because Qwen3 models think out loud first
    otherwise; other models ignore it.
  * A reply cut off at `max_tokens` is returned as it is; only a debug line notes it.
"""
import json
import time
import urllib.request
from typing import Callable, Optional

from pom.bookkeeper.bookkeeper import Bookkeeper
from pom.defaults import OracleSettings

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module



class OracleModelClientError(ValueError):
    """Raised for any client problem, but only in strict mode (development and tests)."""


def _http_post(url: str, body: dict, timeout: float) -> dict:
    """POSTs JSON and returns the parsed JSON reply. Raises on any network or format problem."""
    request = urllib.request.Request(url, json.dumps(body).encode("utf-8"),
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:      # nosec: scheme checked
        return json.load(response)


class OracleModelClient:
    """Asks a language model served by llama-server for a consult.

    Attributes:
        logger: Where this class says what it has to say (a LogRecorder from the Bookkeeper).
        oracle: The `[oracle]` settings it was handed (it reads `url`, `timeout`, `max_tokens` and `temperature`).
        last_seconds: How long the most recent request took, or None before the first.
        last_cut_off: True if the most recent consult stopped because it reached `max_tokens`.
    """

    def __init__(self, oracle: OracleSettings, post: Optional[Callable[[str, dict, float], dict]] = None, strict: Optional[bool] = None,
                 bookkeeper: Optional[Bookkeeper] = None):
        """Args:
            oracle: The `[oracle]` section: `url` (the server's chat-completions address), `timeout` (seconds to wait for one portent),
                `max_tokens` (the most the model may write) and `temperature` (how adventurous the word choice is).
            post: A function (url, body, timeout) -> parsed JSON reply. Default: real HTTP.
            strict: Raise OracleModelClientError on any problem instead of logging it; None (the default) follows the project's `strict` setting.
            bookkeeper: The Bookkeeper the entry point built; it hands this class its logger. None (a test or a tool) gives a plain one.
        """
        self.logger = (bookkeeper or Bookkeeper()).logger_for(__name__, OracleModelClientError, DEBUG, strict)
        self.oracle = oracle
        self._post = post if post is not None else _http_post
        self.last_seconds: Optional[float] = None
        self.last_cut_off = False

    def consult(self, prompt: str) -> Optional[str]:
        """The model's portent for this prompt, or None if it wrote none.

        None means no portent could be had; the reason is in the log. Never raises (except in strict mode).
        """
        if not isinstance(prompt, str) or not prompt.strip():
            return None
        body = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self.oracle.max_tokens, "temperature": self.oracle.temperature,
            "chat_template_kwargs": {"enable_thinking": False},      # Qwen3 thinks otherwise; others ignore it
        }
        started = time.perf_counter()
        try:
            reply = self._post(self.oracle.url, body, self.oracle.timeout)
            choice = reply["choices"][0]
            text = choice["message"]["content"].strip()
        except Exception as error:               # a dead or slow server must never stop the oracle
            self.last_seconds = time.perf_counter() - started
            self.logger.warning("the oracle model did not answer (%s); no portent this time", type(error).__name__)
            return None
        self.last_seconds = time.perf_counter() - started
        self.last_cut_off = choice.get("finish_reason") == "length"
        if not text:
            self.logger.warning("the oracle model's reply was empty; no portent this time")
            return None
        self.logger.debug("portent of %d characters in %.1f s%s", len(text), self.last_seconds,
                          " (cut off at max_tokens)" if self.last_cut_off else "")
        return text
