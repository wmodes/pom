"""The interest client: asks the local language model how interesting one sentence is.

The Scorer decides which overheard sentences are worth answering by asking a small language model
(served by llama.cpp's `llama-server`) to score each one. This module is only the conversation with
that server: one sentence in, one number out, or None if no number could be had. It knows nothing
about fragments, filtering or fallbacks; the Scorer does that.

HOW THE SCORE IS READ. The model is asked to reply with a number, but only its first token is
generated, and the score is read from the probabilities of the digits 1 to 9 in that one token (the
expected value of the digit). That makes it continuous (no ties between sentences), repeatable (no
sampling, temperature 0), and cheap (one generated token). Written decimals from a small model
cluster on a few values, so they are not used. This is the method the interest-scorer experiment
settled on (experiments/interest-scorer/README.md), and the request is built exactly as the
experiment built it, so scores here match the saved results.

ONE SENTENCE PER REQUEST, NO CONTEXT. The model is never shown any other sentence. Batching several
sentences, or showing earlier ones as context, made a sentence's score depend on its neighbors and
their order. A sentence must score the same wherever it comes in the conversation.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A server that is down, slow, or
    answering oddly gives None and a warning in `report`, never an exception. `strict=True` raises
    InterestClientError instead, for tests and dev tools.
  * Sentence text is never logged and never put in a Report message: nothing that was said in the
    room is kept. Logs and reports hold counts, scores and timings.
  * The prompt is passed in (the entry point takes it from Config); this module never reads Config
    and has no built-in prompt, so the words that define "interesting" live in one place, the
    config file. With no prompt the client reports it and is `available` = False.
  * Only http and https addresses are accepted, so a mistyped setting cannot make it open a file.
  * The transport is injected (`post`), so tests run with a fake and need no server. The default
    uses the standard library, so there is nothing to install.
  * `interpret` is the debug view's request: the same sentence and the same system message plus the
    debug output instructions, but this time the model writes a reply (its score and the content that
    earned it). Writing words is slow on a small machine, so it has its own, longer timeout. It is
    for watching how the model reads speech while refining the prompt, not for the running Printer.
  * The first request after a server starts is slow (the model is being loaded into memory, 12 to 28
    seconds measured on the Pi), so the entry point should call `warm_up` at startup.
"""
import json
import math
import re
import time
import urllib.request
from typing import Callable, Optional

from pom.logs import get_logger
from pom.report import Report

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

# The user message, word for word as in the experiment, so scores match its saved results.
USER_TEMPLATE = "Rate this line: {sentence}"
WARM_UP_SENTENCE = "Hello there."
DEFAULT_TIMEOUT = 15.0
DEFAULT_INTERPRET_TIMEOUT = 90.0
INTERPRET_MAX_TOKENS = 120


class InterestClientError(ValueError):
    """Raised for any client problem, but only in strict mode (development and tests)."""


def _http_post(url: str, body: dict, timeout: float) -> dict:
    """POSTs JSON and returns the parsed JSON reply. Raises on any network or format problem."""
    request = urllib.request.Request(url, json.dumps(body).encode("utf-8"),
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:      # nosec: scheme checked
        return json.load(response)


def expected_digit(reply) -> Optional[float]:
    """The expected value of the digit 1 to 9 the model starts its reply with, or None.

    Reads the probabilities of the first generated token from an OpenAI-style reply that asked for
    logprobs. Tokens that are not a single digit 1 to 9 are ignored (a model that starts with words
    spreads its probability over them, and what is left is still the digit's relative weight); if no
    digit appears at all the reply cannot be scored. Anything malformed gives None.
    """
    try:
        candidates = reply["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
        weights = {}
        for candidate in candidates:
            token = candidate["token"].strip()
            if len(token) == 1 and token in "123456789":
                weights[int(token)] = weights.get(int(token), 0.0) + math.exp(candidate["logprob"])
    except (KeyError, IndexError, TypeError, AttributeError, ValueError, OverflowError):
        return None
    total = sum(weights.values())
    if total <= 0:
        return None
    return sum(digit * weight for digit, weight in weights.items()) / total


def parse_written_score(reply) -> Optional[float]:
    """The number a debug reply starts with (1.00 to 9.99), or None.

    A debug reply looks like "7.89, a striking image, a death"; only the leading number is read.
    """
    found = re.match(r"\s*(\d+(?:\.\d+)?)", reply) if isinstance(reply, str) else None
    if found is None:
        return None
    value = float(found.group(1))
    return value if 1.0 <= value <= 9.99 else None


class InterestClient:
    """Scores one sentence at a time with a language model served by llama-server.

    Attributes:
        report: Problems met (server down, odd reply, bad address). Never holds sentence text.
        last_seconds: How long the most recent request took, or None before the first.
    """

    def __init__(self, url: str, prompt: str, timeout: float = DEFAULT_TIMEOUT,
                 post: Optional[Callable[[str, dict, float], dict]] = None, strict: bool = False,
                 debug_prompt: str = "", interpret_timeout: float = DEFAULT_INTERPRET_TIMEOUT):
        """Args:
            url: The server's chat-completions address, e.g. http://127.0.0.1:8098/v1/chat/completions.
            prompt: The system message that tells the model how to score (from Config).
            timeout: Seconds to wait for one reply.
            post: A function (url, body, timeout) -> parsed JSON reply. Default: real HTTP.
            strict: Raise InterestClientError on any problem instead of recording it.
            debug_prompt: The system message for `interpret` (from Config); without one `interpret`
                does nothing.
            interpret_timeout: Seconds to wait for a written reply, which takes much longer than a score.
        """
        self.report = Report(strict=strict, error=InterestClientError, logger=_log)
        self._url, self._prompt = url, (prompt or "").strip()
        self._timeout = (timeout if isinstance(timeout, (int, float)) and not isinstance(timeout, bool)
                         and timeout > 0 else DEFAULT_TIMEOUT)
        if self._timeout != timeout:
            self.report.warn(f"the scorer timeout must be a positive number of seconds, got "
                             f"{timeout!r}; using {DEFAULT_TIMEOUT:g}")
        self._debug_prompt = (debug_prompt or "").strip()
        self._interpret_timeout = (interpret_timeout if isinstance(interpret_timeout, (int, float))
                                   and not isinstance(interpret_timeout, bool) and interpret_timeout > 0
                                   else DEFAULT_INTERPRET_TIMEOUT)
        if self._interpret_timeout != interpret_timeout:
            self.report.warn(f"the interpret timeout must be a positive number of seconds, got "
                             f"{interpret_timeout!r}; using {DEFAULT_INTERPRET_TIMEOUT:g}")
        self._post = post if post is not None else _http_post
        self.last_seconds: Optional[float] = None
        self._usable = True
        if not (isinstance(url, str) and url.startswith(("http://", "https://"))):
            self.report.warn(f"the scorer address must start with http:// or https://, got {url!r}; "
                             f"the model will not be asked")
            self._usable = False
        if not self._prompt:
            self.report.warn("the scorer has no prompt; the model will not be asked")
            self._usable = False

    @property
    def available(self) -> bool:
        """False if the address or prompt was unusable, so the model will never be asked."""
        return self._usable

    @property
    def can_interpret(self) -> bool:
        """True if the model can be asked to explain a score (usable, and a debug prompt was given)."""
        return self._usable and bool(self._debug_prompt)

    def score(self, sentence: str) -> Optional[float]:
        """The model's interest score for one sentence (about 1 to 9.99), or None if it got none.

        None means the caller should fall back; the reason is in `report`. Never raises (except in
        strict mode).
        """
        if not self._usable or not isinstance(sentence, str) or not sentence.strip():
            return None
        body = {
            "messages": [{"role": "system", "content": self._prompt},
                         {"role": "user", "content": USER_TEMPLATE.format(sentence=sentence.strip())}],
            "max_tokens": 1, "temperature": 0, "logprobs": True, "top_logprobs": 20,
            "chat_template_kwargs": {"enable_thinking": False},      # Qwen3 thinks otherwise; others ignore it
        }
        started = time.perf_counter()
        try:
            reply = self._post(self._url, body, self._timeout)
        except Exception as error:               # a dead or slow server must never stop the oracle
            self.last_seconds = time.perf_counter() - started
            self.report.warn(f"the scorer model did not answer ({type(error).__name__}); "
                             f"the caller will fall back")
            return None
        self.last_seconds = time.perf_counter() - started
        value = expected_digit(reply)
        if value is None:
            self.report.warn("the scorer model's reply held no score; the caller will fall back")
            return None
        _log.debug("scored a sentence of %d characters: %.2f in %.0f ms",
                   len(sentence), value, self.last_seconds * 1000)
        return value

    def warm_up(self) -> bool:
        """Asks for one throwaway score so the model is loaded before a real sentence needs it.

        Returns True if the model answered with a score. Call this once at startup; the first
        request after a server starts can take many seconds.
        """
        return self.score(WARM_UP_SENTENCE) is not None

    def interpret(self, sentence: str) -> Optional[str]:
        """The model's written explanation of one sentence's score, or None.

        The reply is whatever the debug prompt asks for (by default the score and the content that
        earned it, as a comma-separated list). Slow: it writes words. Never raises (except in strict
        mode); with no debug prompt, or after a failure, it returns None.
        """
        if not self.can_interpret or not isinstance(sentence, str) or not sentence.strip():
            return None
        body = {
            "messages": [{"role": "system", "content": self._debug_prompt},
                         {"role": "user", "content": USER_TEMPLATE.format(sentence=sentence.strip())}],
            "max_tokens": INTERPRET_MAX_TOKENS, "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        started = time.perf_counter()
        try:
            reply = self._post(self._url, body, self._interpret_timeout)
            text = reply["choices"][0]["message"]["content"].strip()
        except Exception as error:               # a dead server or an odd reply must not stop the debug view
            self.last_seconds = time.perf_counter() - started
            self.report.warn(f"the model did not give an interpretation ({type(error).__name__})")
            return None
        self.last_seconds = time.perf_counter() - started
        _log.debug("interpreted a sentence of %d characters in %.0f ms", len(sentence), self.last_seconds * 1000)
        return text or None
