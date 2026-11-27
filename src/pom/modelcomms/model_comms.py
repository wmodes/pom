"""ModelComms: how the Printer talks to the server of one of its models (whisper, the scorer or the oracle).

Each model runs in a server of its own (whisper-server, llama-server). Every request to that server, and every "is it up?", goes through
the model's one ModelComms, which owns the server's address. The model clients (WhisperModelClient, ScorerModelClient, OracleModelClient)
say what to ask and what the answer means; ModelComms only carries it.

Design decisions, and why:
  * One place for the talking (Design Audit 04 item 5). Before, each model client wrote its own HTTP post, and "is it up?" was written
    five ways with five timeouts, none of them in the config.
  * It uses requests, the common Python library for talking HTTP, as designed: `json=` for a JSON request, `files=` for whisper's file
    upload (a multipart form, which requests builds itself).
  * A request that fails raises. The model client that asked decides what a failure means (the scorer's circuit breaker, the oracle's
    retries in the TurnTaker, a warning for whisper); a transport cannot know that. `health` never raises.
  * "Is it up?" is one rule for every model's server: its `/health` address, at the root of the server's address (whisper-server and
    llama-server both answer there). 200 is up; 503 is waiting (it answers but is still loading its model); anything else, or no answer
    within the `[modelcomms] health_timeout_seconds` setting, is down.
  * Each request is its own connection (no requests.Session, which is not promised to be safe across threads), so one ModelComms can be
    used from any number of threads: the listening thread, the scorer's warm-up and the monitor's poller all use the scorer's.
  * It logs nothing: what it carries is what the room said, and the client that asked logs the kind of failure.
"""
import urllib.parse

import requests

from pom.vocabulary import MonitorLight

HEALTH_PATH = "/health"        # where whisper-server and llama-server both say whether they are up (their API, not a setting)
WAITING_STATUS = 503           # what both say while they are still loading their model (their API, not a setting)


class ModelComms:
    """Talks to the server of one model: sends it a request, and says whether it is up.

    Attributes:
        address: The server's address, as the config gives it (a llama-server's chat address, or whisper-server's root).
        health_address: Where the server says whether it is up: `/health` at the root of `address`.
        health_timeout_seconds: How long `health` waits for an answer before calling the server down.
    """

    def __init__(self, address: str, health_timeout_seconds: float):
        """Args:
            address: The server's address from the config (checked there: http or https only).
            health_timeout_seconds: The `[modelcomms] health_timeout_seconds` setting.
        """
        self.address = address.rstrip("/")
        parts = urllib.parse.urlsplit(self.address)
        self.health_address = f"{parts.scheme}://{parts.netloc}{HEALTH_PATH}"
        self.health_timeout_seconds = health_timeout_seconds

    def post_json(self, body: dict, timeout_seconds: float, path: str = ""):
        """Sends a JSON request and returns the server's JSON reply.

        Args:
            body: The request, as JSON-ready values.
            timeout_seconds: How long to wait for the reply (the asking client's own setting).
            path: Added to the address (for a server whose address is its root).

        Raises:
            requests.RequestException: The server did not answer, or answered with an error status.
            ValueError: The reply was not JSON.
        """
        response = requests.post(self.address + path, json=body, timeout=timeout_seconds)
        response.raise_for_status()
        return response.json()

    def post_form(self, fields: dict, files: dict, timeout_seconds: float, path: str = "") -> str:
        """Sends a form with files (a multipart upload) and returns the server's reply as text.

        Args:
            fields: The form's text fields.
            files: The files, as requests takes them: {field name: (file name, bytes, content type)}.
            timeout_seconds: How long to wait for the reply (the asking client's own setting).
            path: Added to the address (whisper-server's `/inference`).

        Raises:
            requests.RequestException: The server did not answer, or answered with an error status.
        """
        response = requests.post(self.address + path, data=fields, files=files, timeout=timeout_seconds)
        response.raise_for_status()
        return response.content.decode("utf-8", "replace")

    def health(self) -> MonitorLight:
        """Whether the server is up: UP, WAITING (it answers but is still loading its model) or DOWN. Never raises."""
        try:
            response = requests.get(self.health_address, timeout=self.health_timeout_seconds)
        except (requests.RequestException, ValueError):
            return MonitorLight.DOWN
        if response.ok:
            return MonitorLight.UP
        return MonitorLight.WAITING if response.status_code == WAITING_STATUS else MonitorLight.DOWN

    def is_up(self) -> bool:
        """True if the server says it is up and ready (`health` is UP)."""
        return self.health() is MonitorLight.UP
