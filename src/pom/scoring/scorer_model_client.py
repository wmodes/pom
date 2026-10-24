"""The ScorerModelClient: asks the scorer model how interesting one fragment is.

The Scorer decides which overheard fragments are worth responding to by asking a small language
model (the scorer model, served by llama.cpp's `llama-server`) to score each one. This module is
only the conversation with that server: one fragment in, one number out, or None if no number could
be had. It knows nothing about fragments or filtering; the Scorer does that.

HOW THE SCORE IS READ. The model is asked to reply with a single digit, 0 to 9, and only that first
token is generated. But the model does not just pick a digit: for that one token it gives every digit a
probability (say 75% for 6 and 25% for 7). The score is the average of the digits weighted by those
probabilities (the expected value), here 6.25. A model that is certain gives a whole number. That makes
the score continuous (no ties between fragments), repeatable (no sampling, temperature 0), and cheap
(one generated token). Probabilities of tokens that are not digits are left out and the digits rescaled
to add up to 100%. Decimals written by a small model cluster on a few values, so the model is not asked
for them. This is the method the interest-scorer experiment settled on
(experiments/interest-scorer/README.md), and the request is built exactly as the experiment built it, so
scores here match the saved results.

ONE FRAGMENT PER REQUEST, NO CONTEXT. The model is never shown any other fragment. Batching several
fragments, or showing earlier ones as context, made a fragment's score depend on its neighbors and
their order. A fragment must score the same wherever it comes in the conversation.

Design decisions, and why:
  * Nothing here may stop the oracle (see pom/__init__.py). A server that is down, slow, or
    answering oddly gives None and a warning in `report`, never an exception. `strict=True` raises
    ScorerModelClientError instead, for tests and dev tools.
  * Fragment text is never logged and never put in a FaultRecorder message: nothing that was said in the
    room is kept. Logs and reports hold counts, scores and timings.
  * The prompt is passed in (the entry point takes it from Config); this module never reads Config
    and has no built-in prompt, so the words that define "interesting" live in one place, the
    config file. With no prompt the client reports it and is `available` = False.
  * Only http and https addresses are accepted, so a mistyped setting cannot make it open a file.
  * The transport is injected (`post`), so tests run with a fake and need no server. The default
    uses the standard library, so there is nothing to install.
  * `explain` is the debug view's request: the same fragment and the same system message plus the
    debug output instructions, but this time the model writes a reply (its score and the content that
    earned it). It is ONE run: the score is read from the probabilities of the reply's first token, exactly
    as `score` reads it, and the written reasons are the rest of that same output, so the number and
    the explanation belong together. (The reasons are still written after the number, so they are a
    story the model tells about it, not a readout of its insides.) Writing words is slow on a small
    machine, so it has its own, longer timeout. It is for watching how the model reads speech while
    refining the prompt, not for the running Printer.
  * The first request after a server starts is slow (the model is being loaded into memory, 12 to 28
    seconds measured on the Pi), so the entry point should call `warm_up` at startup.
"""
import json
import math
import re
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from pom.log_setup import get_logger
from pom.fault_recorder import FaultRecorder

DEBUG = None   # None follows the project debug flag; True/False overrides it for this module
_log = get_logger(__name__, DEBUG)

# The user message, word for word as in the experiment, so scores match its saved results.
USER_TEMPLATE = "Rate this line: {fragment_text}"
WARM_UP_FRAGMENT = "Hello there."
DEFAULT_TIMEOUT = 15.0
DEFAULT_EXPLAIN_TIMEOUT = 90.0
EXPLAIN_MAX_TOKENS = 80


class ScorerModelClientError(ValueError):
    """Raised for any client problem, but only in strict mode (development and tests)."""


def _http_post(url: str, body: dict, timeout: float) -> dict:
    """POSTs JSON and returns the parsed JSON reply. Raises on any network or format problem."""
    request = urllib.request.Request(url, json.dumps(body).encode("utf-8"),
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:      # nosec: scheme checked
        return json.load(response)


def score_from_probabilities(reply) -> Optional[float]:
    """The expected value of the digit 0 to 9 the model starts its reply with, or None.

    Reads the probabilities of the first generated token from an OpenAI-style reply that asked for
    logprobs. Tokens that are not a single digit 0 to 9 are ignored (a model that starts with words
    spreads its probability over them, and what is left is still the digit's relative weight); if no
    digit appears at all the reply cannot be scored. Anything malformed gives None.
    """
    try:
        candidates = reply["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
        weights = {}
        for candidate in candidates:
            token = candidate["token"].strip()
            if len(token) == 1 and token in "0123456789":
                weights[int(token)] = weights.get(int(token), 0.0) + math.exp(candidate["logprob"])
    except (KeyError, IndexError, TypeError, AttributeError, ValueError, OverflowError):
        return None
    total = sum(weights.values())
    if total <= 0:
        return None
    return sum(digit * weight for digit, weight in weights.items()) / total


@dataclass(frozen=True, slots=True)
class Explanation:
    """One debug run of the model: what it wrote, and the score its first token's probabilities give.

    Attributes:
        text: The written reply (by default the score, the fragment and the content that earned the score).
        score: The expected value of the first token's digit, as `ScorerModelClient.score` reads it, or None if
            the reply did not begin with a digit.
    """
    text: str
    score: Optional[float]


def parse_written_score(reply) -> Optional[float]:
    """The single digit (0 to 9) a debug reply starts with, or None.

    A debug reply looks like "7 | a striking image | vivid, death"; only the leading digit is read.
    A longer number ("85") or a decimal ("7.5") is not a digit and gives None.
    """
    found = re.match(r"\s*(\d)(?![\d.])", reply) if isinstance(reply, str) else None
    return float(found.group(1)) if found else None


class ScorerModelClient:
    """Scores one fragment at a time with a language model served by llama-server.

    Attributes:
        report: Problems met (server down, odd reply, bad address). Never holds fragment text.
        last_seconds: How long the most recent request took, or None before the first.
    """

    def __init__(self, url: str, prompt: str, timeout: float = DEFAULT_TIMEOUT,
                 post: Optional[Callable[[str, dict, float], dict]] = None, strict: bool = False,
                 explain_prompt: str = "", explain_timeout: float = DEFAULT_EXPLAIN_TIMEOUT):
        """Args:
            url: The server's chat-completions address, e.g. http://127.0.0.1:8098/v1/chat/completions.
            prompt: The system message that tells the model how to score (from Config).
            timeout: Seconds to wait for one reply.
            post: A function (url, body, timeout) -> parsed JSON reply. Default: real HTTP.
            strict: Raise ScorerModelClientError on any problem instead of recording it.
            explain_prompt: The system message for `explain` (from Config); without one `explain`
                does nothing.
            explain_timeout: Seconds to wait for a written reply, which takes much longer than a score.
        """
        self.report = FaultRecorder(strict=strict, error=ScorerModelClientError, logger=_log)
        self._url, self._prompt = url, (prompt or "").strip()
        self._timeout = (timeout if isinstance(timeout, (int, float)) and not isinstance(timeout, bool)
                         and timeout > 0 else DEFAULT_TIMEOUT)
        if self._timeout != timeout:
            self.report.warn(f"the scorer timeout must be a positive number of seconds, got "
                             f"{timeout!r}; using {DEFAULT_TIMEOUT:g}")
        self._explain_prompt = (explain_prompt or "").strip()
        self._explain_timeout = (explain_timeout if isinstance(explain_timeout, (int, float))
                                 and not isinstance(explain_timeout, bool) and explain_timeout > 0
                                 else DEFAULT_EXPLAIN_TIMEOUT)
        if self._explain_timeout != explain_timeout:
            self.report.warn(f"the explain timeout must be a positive number of seconds, got "
                             f"{explain_timeout!r}; using {DEFAULT_EXPLAIN_TIMEOUT:g}")
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
    def can_explain(self) -> bool:
        """True if the model can be asked to explain a score (usable, and a debug prompt was given)."""
        return self._usable and bool(self._explain_prompt)

    def score(self, fragment_text: str) -> Optional[float]:
        """The model's interest score for one fragment (0 to 9), or None if it got none.

        None means no score could be had; the reason is in `report`. Never raises (except in
        strict mode).
        """
        if not self._usable or not isinstance(fragment_text, str) or not fragment_text.strip():
            return None
        body = {
            "messages": [{"role": "system", "content": self._prompt},
                         {"role": "user", "content": USER_TEMPLATE.format(fragment_text=fragment_text.strip())}],
            "max_tokens": 1, "temperature": 0, "logprobs": True, "top_logprobs": 20,
            "chat_template_kwargs": {"enable_thinking": False},      # Qwen3 thinks otherwise; others ignore it
        }
        started = time.perf_counter()
        try:
            reply = self._post(self._url, body, self._timeout)
        except Exception as error:               # a dead or slow server must never stop the oracle
            self.last_seconds = time.perf_counter() - started
            self.report.warn(f"the scorer model did not answer ({type(error).__name__}); "
                             f"the fragment will go unscored")
            return None
        self.last_seconds = time.perf_counter() - started
        value = score_from_probabilities(reply)
        if value is None:
            self.report.warn("the scorer model's reply held no score; the fragment will go unscored")
            return None
        _log.debug("scored a fragment of %d characters: %.2f in %.0f ms",
                   len(fragment_text), value, self.last_seconds * 1000)
        return value

    def warm_up(self) -> bool:
        """Asks for one throwaway score so the model is loaded before a real fragment needs it.

        Returns True if the model answered with a score. Call this once at startup; the first
        request after a server starts can take many seconds.
        """
        return self.score(WARM_UP_FRAGMENT) is not None

    def explain(self, fragment_text: str) -> Optional[Explanation]:
        """One debug run for a fragment: the model's written reply and the score from the same run, or None.

        The reply is whatever the debug prompt asks for (by default the score and the content that
        earned it, as a comma-separated list). Slow: it writes words. Never raises (except in strict
        mode); with no debug prompt, or after a failure, it returns None.
        """
        if not self.can_explain or not isinstance(fragment_text, str) or not fragment_text.strip():
            return None
        body = {
            "messages": [{"role": "system", "content": self._explain_prompt},
                         {"role": "user", "content": USER_TEMPLATE.format(fragment_text=fragment_text.strip())}],
            "max_tokens": EXPLAIN_MAX_TOKENS, "temperature": 0, "logprobs": True, "top_logprobs": 20,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        started = time.perf_counter()
        try:
            reply = self._post(self._url, body, self._explain_timeout)
            text = reply["choices"][0]["message"]["content"].strip()
        except Exception as error:               # a dead server or an odd reply must not stop the debug view
            self.last_seconds = time.perf_counter() - started
            self.report.warn(f"the model did not give an explanation ({type(error).__name__})")
            return None
        self.last_seconds = time.perf_counter() - started
        _log.debug("explained a fragment of %d characters in %.0f ms", len(fragment_text), self.last_seconds * 1000)
        return Explanation(text, score_from_probabilities(reply)) if text else None
