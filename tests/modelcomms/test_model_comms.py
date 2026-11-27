"""Tests for pom.modelcomms.model_comms: ModelComms carries one model's requests and says whether its server is up.

A real little HTTP server runs on this machine for each test (127.0.0.1, a free port), so the requests go through requests itself, as
they do on the Pi; nothing leaves the machine.
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from pom.vocabulary import MonitorLight
from pom.modelcomms import ModelComms


class FakeModelServer:
    """A server that answers like whisper-server or llama-server: `/health` with the status it is told, and echoes what it was sent."""

    def __init__(self):
        self.health_status = 200
        self.health_delay_seconds = 0.0
        self.reply_status = 200
        self.reply_body = None                     # bytes to send back instead of the echo
        self.received = []                         # (path, content type, body bytes) of each POST
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path != "/health":
                    self.send_response(404)
                    self.end_headers()
                    return
                time.sleep(server.health_delay_seconds)
                self.send_response(server.health_status)
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                server.received.append((self.path, self.headers["Content-Type"], body))
                self.send_response(server.reply_status)
                self.end_headers()
                self.wfile.write(server.reply_body if server.reply_body is not None else json.dumps({"echo": body.decode("utf-8", "replace")}).encode())

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.root = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()


@pytest.fixture
def server():
    fake = FakeModelServer()
    yield fake
    fake.close()


class TestTheAddresses:
    def test_the_health_address_is_at_the_root_of_a_chat_address(self):
        comms = ModelComms("http://127.0.0.1:8098/v1/chat/completions", 3.0)
        assert comms.health_address == "http://127.0.0.1:8098/health"

    def test_the_health_address_of_a_root_address_is_its_own_health(self):
        assert ModelComms("http://127.0.0.1:8110/", 3.0).health_address == "http://127.0.0.1:8110/health"

    def test_the_address_is_kept_without_a_trailing_slash(self):
        assert ModelComms("http://127.0.0.1:8110/", 3.0).address == "http://127.0.0.1:8110"


class TestHealth:
    def test_a_server_that_says_ok_is_up(self, server):
        comms = ModelComms(server.root + "/v1/chat/completions", 3.0)
        assert comms.health() is MonitorLight.UP and comms.is_up() is True

    def test_a_server_still_loading_its_model_is_waiting(self, server):
        server.health_status = 503
        comms = ModelComms(server.root, 3.0)
        assert comms.health() is MonitorLight.WAITING and comms.is_up() is False

    @pytest.mark.parametrize("status", [500, 404, 401])
    def test_any_other_status_is_down(self, server, status):
        server.health_status = status
        assert ModelComms(server.root, 3.0).health() is MonitorLight.DOWN

    def test_no_server_is_down(self, server):
        address = server.root
        server.close()
        assert ModelComms(address, 3.0).health() is MonitorLight.DOWN

    def test_a_server_slower_than_the_health_timeout_is_down(self, server):
        server.health_delay_seconds = 1.0
        assert ModelComms(server.root, 0.2).health() is MonitorLight.DOWN

    def test_a_server_within_the_health_timeout_is_up(self, server):
        server.health_delay_seconds = 0.2
        assert ModelComms(server.root, 3.0).health() is MonitorLight.UP

    def test_an_address_that_is_not_an_address_is_down_and_never_raises(self):
        assert ModelComms("not an address", 3.0).health() is MonitorLight.DOWN


class TestPostJson:
    def test_it_sends_json_to_the_address_and_returns_the_parsed_reply(self, server):
        reply = ModelComms(server.root + "/v1/chat/completions", 3.0).post_json({"max_tokens": 1}, 3.0)
        path, content_type, body = server.received[0]
        assert path == "/v1/chat/completions" and content_type == "application/json" and json.loads(body) == {"max_tokens": 1}
        assert json.loads(reply["echo"]) == {"max_tokens": 1}

    def test_a_path_is_added_to_the_address(self, server):
        ModelComms(server.root, 3.0).post_json({}, 3.0, path="/v1/chat/completions")
        assert server.received[0][0] == "/v1/chat/completions"

    def test_an_error_status_raises(self, server):
        server.reply_status = 500
        with pytest.raises(requests.HTTPError):
            ModelComms(server.root, 3.0).post_json({}, 3.0)

    def test_a_reply_that_is_not_json_raises_a_value_error(self, server):
        server.reply_body = b"not json"
        with pytest.raises(ValueError):
            ModelComms(server.root, 3.0).post_json({}, 3.0)

    def test_no_server_raises(self, server):
        address = server.root
        server.close()
        with pytest.raises(requests.RequestException):
            ModelComms(address, 3.0).post_json({}, 3.0)


class TestPostForm:
    def test_it_sends_the_fields_and_the_file_as_a_multipart_form_and_returns_the_text(self, server):
        server.reply_body = "heard: ♪ la".encode("utf-8")
        reply = ModelComms(server.root, 3.0).post_form({"response_format": "srt"}, {"file": ("audio.wav", b"RIFFdata", "audio/wav")}, 3.0,
                                                       path="/inference")
        path, content_type, body = server.received[0]
        assert path == "/inference" and content_type.startswith("multipart/form-data; boundary=")
        assert b'name="response_format"' in body and b"srt" in body
        assert b'name="file"; filename="audio.wav"' in body and b"Content-Type: audio/wav" in body and b"RIFFdata" in body
        assert reply == "heard: ♪ la"

    def test_a_reply_that_is_not_utf8_is_still_text(self, server):
        server.reply_body = b"\xff\xfe words"
        assert ModelComms(server.root, 3.0).post_form({}, {"file": ("a.wav", b"x", "audio/wav")}, 3.0).endswith(" words")

    def test_an_error_status_raises(self, server):
        server.reply_status = 503
        with pytest.raises(requests.HTTPError):
            ModelComms(server.root, 3.0).post_form({}, {"file": ("a.wav", b"x", "audio/wav")}, 3.0)


class TestTheSetting:
    def test_the_health_timeout_is_read_from_the_config_with_its_default(self, tmp_path):
        from pom.config import Config
        path = tmp_path / "pom.toml"
        path.write_text("[modelcomms]\nhealth_timeout_seconds = 7.5\n", encoding="utf-8")
        assert Config.load(path, environ={}).modelcomms.health_timeout_seconds == 7.5
        assert Config.load(environ={}).modelcomms.health_timeout_seconds == 3.0

    @pytest.mark.parametrize("bad", [0, -1, "soon"])
    def test_a_bad_health_timeout_is_the_default_with_a_warning(self, tmp_path, bad):
        from pom.config import Config
        path = tmp_path / "pom.toml"
        path.write_text(f"[modelcomms]\nhealth_timeout_seconds = {json.dumps(bad)}\n", encoding="utf-8")
        config = Config.load(path, environ={})
        assert config.modelcomms.health_timeout_seconds == 3.0
        assert any("modelcomms.health_timeout_seconds" in warning for warning in config.problems.warnings)

    def test_the_shipped_config_sets_it(self):
        from pathlib import Path
        text = (Path(__file__).resolve().parents[2] / "config" / "pom.toml").read_text(encoding="utf-8")
        assert "[modelcomms]\n" in text and "\nhealth_timeout_seconds = " in text
