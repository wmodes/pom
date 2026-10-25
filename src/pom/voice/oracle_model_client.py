"""The OracleModelClient: asks the oracle model for a portent.

When the trigger fires, the PromptBuilder makes the oracle prompt and this module sends it to the oracle model
(served by llama.cpp's `llama-server`, apart from the scorer's) and returns what the model wrote. It is only the
conversation with that server: one prompt in, one portent out, or None if none could be had. It knows nothing
about hinges or exemplars; the PromptBuilder does that.

The prompt goes to the model as a single user message, exactly as built. The client adds nothing to it and
takes nothing out of the reply but the whitespace around it, so what a dev tool shows is what the Printer
would type. The model chooses its own words: unlike the scorer, the request is not at temperature 0.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A server that is down, slow, or answering oddly gives
    None and a warning in `report`, never an exception. `strict=True` raises OracleModelClientError instead, for
    tests and dev tools.
  * Neither the prompt (it holds the hinge) nor the portent is logged or put in a FaultRecorder message: nothing
    that was said in the room is kept. Logs and reports hold counts and timings.
  * The settings are passed in (the entry point takes them from Config); this module never reads Config.
  * Only http and https addresses are accepted, so a mistyped setting cannot make it open a file.
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

from pom.fault_recorder import FaultRecorder
from pom.log_setup import get_logger

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_TOKENS = 300
DEFAULT_TEMPERATURE = 0.8


class OracleModelClientError(ValueError):
    """Raised for any client problem, but only in strict mode (development and tests)."""


def _http_post(url: str, body: dict, timeout: float) -> dict:
    """POSTs JSON and returns the parsed JSON reply. Raises on any network or format problem."""
    request = urllib.request.Request(url, json.dumps(body).encode("utf-8"),
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:      # nosec: scheme checked
        return json.load(response)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class OracleModelClient:
    """Asks a language model served by llama-server for a portent.

    Attributes:
        report: Problems met (server down, odd reply, bad setting). Never holds the prompt or the portent.
        last_seconds: How long the most recent request took, or None before the first.
        last_cut_off: True if the most recent portent stopped because it reached `max_tokens`.
    """

    def __init__(self, url: str, timeout: float = DEFAULT_TIMEOUT, max_tokens: int = DEFAULT_MAX_TOKENS,
                 temperature: float = DEFAULT_TEMPERATURE, post: Optional[Callable[[str, dict, float], dict]] = None,
                 strict: bool = False):
        """Args:
            url: The server's chat-completions address, e.g. http://127.0.0.1:8099/v1/chat/completions.
            timeout: Seconds to wait for one portent.
            max_tokens: The most the model may write, a whole number of at least 1.
            temperature: How adventurous the word choice is, 0 or more.
            post: A function (url, body, timeout) -> parsed JSON reply. Default: real HTTP.
            strict: Raise OracleModelClientError on any problem instead of recording it.
        """
        self.report = FaultRecorder(strict=strict, error=OracleModelClientError, logger=_log)
        self._url = url
        self._timeout = self._valid(timeout, "timeout", DEFAULT_TIMEOUT, lambda v: _is_number(v) and v > 0,
                                    "a positive number of seconds")
        self._max_tokens = self._valid(max_tokens, "max_tokens", DEFAULT_MAX_TOKENS,
                                       lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 1,
                                       "a whole number of at least 1")
        self._temperature = self._valid(temperature, "temperature", DEFAULT_TEMPERATURE,
                                        lambda v: _is_number(v) and v >= 0, "a number of at least 0")
        self._post = post if post is not None else _http_post
        self.last_seconds: Optional[float] = None
        self.last_cut_off = False
        self._usable = True
        if not (isinstance(url, str) and url.startswith(("http://", "https://"))):
            self.report.warn(f"the oracle address must start with http:// or https://, got {url!r}; "
                             f"the model will not be asked")
            self._usable = False

    def _valid(self, value, name: str, default, is_good, wanted: str):
        """The value if it passes `is_good`, otherwise the default with a warning."""
        if is_good(value):
            return value
        self.report.warn(f"the oracle {name} must be {wanted}, got {value!r}; using {default:g}")
        return default

    @property
    def available(self) -> bool:
        """False if the address was unusable, so the model will never be asked."""
        return self._usable

    def portent(self, prompt: str) -> Optional[str]:
        """The model's portent for this prompt, or None if it wrote none.

        None means no portent could be had; the reason is in `report`. Never raises (except in strict mode).
        """
        if not self._usable or not isinstance(prompt, str) or not prompt.strip():
            return None
        body = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": self._max_tokens, "temperature": self._temperature,
            "chat_template_kwargs": {"enable_thinking": False},      # Qwen3 thinks otherwise; others ignore it
        }
        started = time.perf_counter()
        try:
            reply = self._post(self._url, body, self._timeout)
            choice = reply["choices"][0]
            text = choice["message"]["content"].strip()
        except Exception as error:               # a dead or slow server must never stop the oracle
            self.last_seconds = time.perf_counter() - started
            self.report.warn(f"the oracle model did not answer ({type(error).__name__}); no portent this time")
            return None
        self.last_seconds = time.perf_counter() - started
        self.last_cut_off = choice.get("finish_reason") == "length"
        if not text:
            self.report.warn("the oracle model's reply was empty; no portent this time")
            return None
        _log.debug("portent of %d characters in %.1f s%s", len(text), self.last_seconds,
                   " (cut off at max_tokens)" if self.last_cut_off else "")
        return text
