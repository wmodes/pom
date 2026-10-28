"""Tests for pom.listening.whisper_server_client, against a small stand-in for whisper-server on a local port."""
import io
import logging
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pom.listening.audio_format import SAMPLE_RATE
from pom.listening.whisper_server_client import (DEFAULT_TIMEOUT, Segment, WhisperServerClient,
                                                 WhisperServerClientError, multipart, parse_srt, wav_bytes)

PCM = b"\x01\x00" * 1600                       # a tenth of a second
SRT = ("1\n00:00:00,000 --> 00:00:02,500\nHello there.\n\n"
       "2\n00:00:03,000 --> 00:00:05,250\nHow are you?\n\n")


class StandInHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        fake = self.server.fake
        fake.requests.append((self.path, self.headers.get("Content-Type", ""), body))
        if fake.delay:
            time.sleep(fake.delay)
        self.send_response(fake.status)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(fake.reply.encode("utf-8"))

    def log_message(self, *args):
        pass


class StandIn:
    """A stand-in whisper-server: records what it is sent and answers with `reply`."""

    def __init__(self):
        self.requests, self.reply, self.status, self.delay = [], SRT, 200, 0.0
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), StandInHandler)
        self._server.fake = self
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()

    def stop(self):
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def server():
    fake = StandIn()
    yield fake
    try:
        fake.stop()
    except Exception:
        pass


class TestParsingSrt:
    def test_one_entry(self):
        assert parse_srt("1\n00:00:01,500 --> 00:00:02,250\nHello.\n") == [(1.5, 2.25, "Hello.")]

    def test_several_entries(self):
        assert parse_srt(SRT) == [(0.0, 2.5, "Hello there."), (3.0, 5.25, "How are you?")]

    def test_hours_and_minutes_count(self):
        assert parse_srt("1\n01:02:03,004 --> 01:02:04,005\nx\n")[0][:2] == (3723.004, 3724.005)

    def test_a_webvtt_dot_for_the_milliseconds_works(self):
        assert parse_srt("00:00:01.500 --> 00:00:02.000\nHi\n") == [(1.5, 2.0, "Hi")]

    def test_windows_line_endings_work(self):
        assert parse_srt("1\r\n00:00:00,000 --> 00:00:01,000\r\nHello\r\n\r\n") == [(0.0, 1.0, "Hello")]

    def test_a_text_over_two_lines_is_joined(self):
        assert parse_srt("1\n00:00:00,000 --> 00:00:01,000\nHello\nthere  friend\n")[0][2] == "Hello there friend"

    @pytest.mark.parametrize("reply", ["", "   ", "no timestamps here", "WEBVTT\n"])
    def test_a_reply_with_no_entries_gives_nothing(self, reply):
        assert parse_srt(reply) == []


class TestTheRequestPieces:
    def test_the_wav_is_a_real_wav_of_the_right_shape(self):
        with wave.open(io.BytesIO(wav_bytes(PCM)), "rb") as wav:
            assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, SAMPLE_RATE)
            assert wav.readframes(wav.getnframes()) == PCM

    def test_the_multipart_body_holds_the_fields_and_the_audio(self):
        body, content_type = multipart({"vad": "true", "response_format": "srt"}, b"AUDIO")
        boundary = content_type.split("boundary=")[1]
        assert content_type.startswith("multipart/form-data")
        assert b'name="vad"\r\n\r\ntrue' in body and b'name="response_format"\r\n\r\nsrt' in body
        assert b'name="file"; filename="audio.wav"' in body and b"AUDIO" in body
        assert body.endswith(f"--{boundary}--\r\n".encode())


class TestAgainstAStandInServer:
    def test_ping_is_true_while_the_server_is_up_and_false_after(self, server):
        client = WhisperServerClient(server.url)
        assert client.ping() is True
        server.stop()
        assert client.ping() is False

    def test_text_asks_for_plain_text_with_the_voice_detector_on(self, server):
        server.reply = "Hello there.\n"
        client = WhisperServerClient(server.url)
        assert client.text(PCM) == "Hello there.\n"
        path, content_type, body = server.requests[0]
        assert path == "/inference" and content_type.startswith("multipart/form-data")
        assert b'name="response_format"\r\n\r\ntext' in body and b'name="no_timestamps"\r\n\r\ntrue' in body
        assert b'name="vad"\r\n\r\ntrue' in body and b"RIFF" in body
        assert client.error is None and client.report.ok

    def test_text_can_turn_the_voice_detector_off(self, server):
        WhisperServerClient(server.url).text(PCM, vad=False)
        assert b'name="vad"\r\n\r\nfalse' in server.requests[0][2]

    def test_segments_ask_for_srt_and_come_back_shifted_by_the_offset(self, server):
        client = WhisperServerClient(server.url)
        found = client.segments(PCM, offset_s=100.0)
        assert found == [Segment(100.0, 102.5, "Hello there."), Segment(103.0, 105.25, "How are you?")]
        assert b'name="response_format"\r\n\r\nsrt' in server.requests[0][2]
        assert b'name="vad"\r\n\r\ntrue' in server.requests[0][2]

    def test_a_segment_can_be_unpacked_like_a_tuple(self, server):
        start, end, text = WhisperServerClient(server.url).segments(PCM)[0]
        assert (start, end, text) == (0.0, 2.5, "Hello there.")

    def test_segments_with_no_text_are_left_out(self, server):
        server.reply = "1\n00:00:00,000 --> 00:00:01,000\n\n\n2\n00:00:02,000 --> 00:00:03,000\nreal\n"
        assert [s.text for s in WhisperServerClient(server.url).segments(PCM)] == ["real"]

    def test_no_speech_is_an_empty_list_and_not_an_error(self, server):
        server.reply = ""
        client = WhisperServerClient(server.url)
        assert client.segments(PCM) == [] and client.text(PCM) == "" and client.error is None and client.report.ok

    def test_a_server_error_gives_nothing_and_says_so(self, server):
        server.status = 500
        client = WhisperServerClient(server.url)
        assert client.segments(PCM) == [] and client.text(PCM) == ""
        assert client.error == "HTTPError" and len(client.report.warnings) == 2

    def test_a_dead_server_gives_nothing_and_says_so(self, server):
        url = server.url
        server.stop()
        client = WhisperServerClient(url)
        assert client.text(PCM) == "" and client.error is not None and not client.report.ok

    def test_the_error_clears_when_the_server_comes_back(self, server):
        client = WhisperServerClient(server.url)
        server.status = 500
        client.text(PCM)
        assert client.error == "HTTPError"
        server.status = 200
        client.text(PCM)
        assert client.error is None

    def test_a_slow_server_times_out(self, server):
        server.delay = 0.4
        client = WhisperServerClient(server.url, timeout=0.1)
        assert client.text(PCM) == "" and client.error is not None

    def test_a_trailing_slash_on_the_address_is_fine(self, server):
        assert WhisperServerClient(server.url + "/").text(PCM) == SRT
        assert server.requests[0][0] == "/inference"


class TestWithAFakePost:
    def test_the_post_is_given_the_address_fields_wav_and_timeout(self):
        calls = []

        def post(url, fields, audio, timeout):
            calls.append((url, fields, audio, timeout))
            return "hello"

        client = WhisperServerClient("http://example.test:8110/", timeout=7, post=post)
        assert client.text(PCM, vad=False) == "hello"
        url, fields, audio, timeout = calls[0]
        assert url == "http://example.test:8110" and timeout == 7.0
        assert fields == {"response_format": "text", "no_timestamps": "true", "vad": "false"}
        assert audio[:4] == b"RIFF"

    @pytest.mark.parametrize("problem", [RuntimeError("boom"), OSError("down"), ValueError("odd"), KeyError("k")])
    def test_any_problem_in_the_post_is_caught(self, problem):
        def post(*args):
            raise problem

        client = WhisperServerClient("http://x.test", post=post)
        assert client.text(PCM) == "" and client.segments(PCM) == []
        assert client.error == type(problem).__name__

    def test_a_reply_that_is_not_text_is_a_problem(self):
        client = WhisperServerClient("http://x.test", post=lambda *args: {"text": "hello"})
        assert client.text(PCM) == "" and client.error == "BadReply" and len(client.report.warnings) == 1

    def test_what_was_said_never_gets_into_the_report_or_the_log(self, caplog):
        def post(*args):
            raise RuntimeError("the secret phrase was abracadabra")

        with caplog.at_level(logging.DEBUG):
            client = WhisperServerClient("http://x.test", post=post)
            client.text(PCM)
            WhisperServerClient("http://x.test", post=lambda *args: "abracadabra said the room").text(PCM)
        assert "abracadabra" not in " ".join(client.report.lines()) and "abracadabra" not in caplog.text


class TestAudioItCannotSend:
    @pytest.mark.parametrize("not_audio", [None, "text", 5, [1, 2], {"a": 1}])
    def test_audio_that_is_not_bytes_is_a_problem_and_is_not_sent(self, not_audio):
        sent = []
        client = WhisperServerClient("http://x.test", post=lambda *args: sent.append(args) or "x")
        assert client.text(not_audio) == "" and client.error == "BadAudio" and len(client.report.warnings) == 1
        assert sent == []

    @pytest.mark.parametrize("silence", [b"", b"\x00"])
    def test_less_than_a_sample_is_not_sent_and_is_not_a_problem(self, silence):
        sent = []
        client = WhisperServerClient("http://x.test", post=lambda *args: sent.append(args) or "x")
        assert client.text(silence) == "" and client.segments(silence) == [] and client.error is None and client.report.ok
        assert sent == []

    @pytest.mark.parametrize("make", [bytearray, memoryview])
    def test_other_byte_containers_are_fine(self, make):
        client = WhisperServerClient("http://x.test", post=lambda *args: "heard")
        assert client.text(make(PCM)) == "heard"


class TestSettings:
    @pytest.mark.parametrize("bad", [None, "", "ftp://x.test", "127.0.0.1:8110", 5, ["http://x"]])
    def test_a_bad_address_means_whisper_is_never_asked(self, bad):
        sent = []
        client = WhisperServerClient(bad, post=lambda *args: sent.append(args) or "x")
        assert client.available is False and len(client.report.warnings) == 1 and "address" in client.report.warnings[0]
        assert client.text(PCM) == "" and client.segments(PCM) == [] and client.ping() is False
        assert client.error == "UnusableAddress" and sent == []

    @pytest.mark.parametrize("good", ["http://127.0.0.1:8110", "https://whisper.test"])
    def test_http_and_https_addresses_are_fine(self, good):
        assert WhisperServerClient(good).available is True and WhisperServerClient(good).report.ok

    @pytest.mark.parametrize("bad", [0, -1, "180", None, True, [180], float("nan")])
    def test_a_bad_timeout_means_the_default_with_a_warning(self, bad):
        seen = []
        client = WhisperServerClient("http://x.test", timeout=bad, post=lambda url, fields, audio, timeout: seen.append(timeout) or "x")
        client.text(PCM)
        assert seen == [DEFAULT_TIMEOUT] and len(client.report.warnings) == 1 and "timeout" in client.report.warnings[0]

    def test_the_defaults_are_the_pis_whisper_server(self):
        assert WhisperServerClient().available and WhisperServerClient().report.ok

    @pytest.mark.parametrize("make", [lambda: WhisperServerClient("nope", strict=True),
                                      lambda: WhisperServerClient("http://x.test", timeout=0, strict=True)])
    def test_strict_mode_raises_on_a_bad_setting(self, make):
        with pytest.raises(WhisperServerClientError):
            make()

    def test_strict_mode_raises_when_a_request_fails(self):
        def post(*args):
            raise OSError("down")

        with pytest.raises(WhisperServerClientError, match="did not answer"):
            WhisperServerClient("http://x.test", post=post, strict=True).text(PCM)

    def test_strict_mode_raises_on_audio_that_is_not_bytes(self):
        with pytest.raises(WhisperServerClientError, match="bytes"):
            WhisperServerClient("http://x.test", strict=True).text("not audio")

    def test_strict_mode_with_good_settings_and_a_good_request_does_not_raise(self):
        assert WhisperServerClient("http://x.test", post=lambda *args: "fine", strict=True).text(PCM) == "fine"
