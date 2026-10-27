"""Tests for the Pi oracle benchmark: which cells it times, how it reads a streamed reply, and how it sums up.

The benchmark's numbers are the whole point of the experiment, so the parts that turn a stream and a list of samples
into numbers are tested here with fake input. The module lives with the experiment, not in src/pom, so it is put on
the path here. No model is run; the streaming is tested against a tiny local stand-in for llama-server.
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments" / "pi-oracle-benchmark"))
from benchmark_oracle_pi import (StreamTimer, build_report, percentile, pick_cells,   # noqa: E402
                                 portent_record, read_stream, summarize_portents, summarize_samples)


def data_line(payload) -> str:
    """One server-sent-events line the way llama-server writes it."""
    return "data: " + json.dumps(payload)


def piece(text, finish=None, timings=None) -> str:
    """A streamed chunk holding a piece of text."""
    chunk = {"choices": [{"delta": {"content": text}, "finish_reason": finish}]}
    if timings is not None:
        chunk["timings"] = timings
    return data_line(chunk)


class Clock:
    """A fake clock that moves on one step each time it is read."""

    def __init__(self, step=1.0, start=100.0):
        self.now, self.step = start, step

    def __call__(self):
        self.now += self.step
        return self.now


class TestPickCells:
    def test_every_third_hinge_from_the_first_at_two_draws(self):
        hinges = [f"h{n}" for n in range(16)]
        cells = pick_cells(hinges)
        assert [h for h, _ in cells][::2] == ["h0", "h3", "h6", "h9", "h12", "h15"]
        assert len(cells) == 12

    def test_each_hinge_is_timed_at_draws_one_and_two(self):
        assert pick_cells(["a", "b", "c", "d"]) == [("a", 1), ("a", 2), ("d", 1), ("d", 2)]

    def test_stride_and_draws_can_be_given(self):
        assert pick_cells(["a", "b", "c"], stride=2, draws=(5,)) == [("a", 5), ("c", 5)]

    def test_no_hinges_gives_no_cells(self):
        assert pick_cells([]) == []


class TestReadStream:
    def test_joins_the_pieces_in_order(self):
        result = read_stream([piece("Hel"), piece("lo "), piece("there"), "data: [DONE]"], Clock(), 0.0)
        assert result.text == "Hello there"

    def test_first_token_time_is_the_clock_at_the_first_piece_minus_the_start(self):
        clock = Clock(step=2.0, start=10.0)        # reads 12.0 at the first piece
        result = read_stream([piece("a"), piece("b")], clock, 10.0)
        assert result.first_token_seconds == pytest.approx(2.0)

    def test_the_clock_is_read_for_the_first_piece_only(self):
        clock = Clock(step=2.0, start=10.0)
        read_stream([piece("a"), piece("b"), piece("c")], clock, 10.0)
        assert clock.now == 12.0

    def test_an_empty_piece_is_not_the_first_token(self):
        clock = Clock(step=1.0, start=0.0)
        result = read_stream([piece(""), piece(None), piece("x")], clock, 0.0)
        assert result.first_token_seconds == pytest.approx(1.0)
        assert result.text == "x"

    def test_no_text_gives_no_first_token(self):
        result = read_stream(["data: [DONE]"], Clock(), 0.0)
        assert result.first_token_seconds is None
        assert result.text == ""

    def test_the_finish_reason_and_timings_are_kept(self):
        timings = {"prompt_n": 120, "cache_n": 100, "predicted_n": 40, "predicted_per_second": 3.2}
        result = read_stream([piece("a"), piece("", finish="length", timings=timings)], Clock(), 0.0)
        assert result.finish_reason == "length"
        assert result.timings == timings

    def test_bytes_lines_are_read_too(self):
        result = read_stream([piece("é").encode("utf-8") + b"\n", b"data: [DONE]\n"], Clock(), 0.0)
        assert result.text == "é"

    def test_lines_that_are_not_data_or_not_json_are_skipped(self):
        lines = [": keep-alive", "", "event: ping", "data: {not json", piece("ok")]
        assert read_stream(lines, Clock(), 0.0).text == "ok"

    def test_a_line_without_the_data_prefix_is_skipped_even_if_it_would_parse(self):
        # With its first five characters dropped this line is valid JSON; only the missing prefix rules it out.
        stray = 'event{"choices": [{"delta": {"content": "BAD"}}]}'
        assert read_stream([stray, piece("ok")], Clock(), 0.0).text == "ok"

    def test_nothing_after_done_is_read(self):
        result = read_stream([piece("a"), "data: [DONE]", piece("b")], Clock(), 0.0)
        assert result.text == "a"

    def test_a_chunk_without_choices_is_skipped(self):
        result = read_stream([data_line({"id": "x"}), piece("a")], Clock(), 0.0)
        assert result.text == "a"


class _StreamingHandler(BaseHTTPRequestHandler):
    """A stand-in llama-server: answers any POST with a short event stream and remembers the request body."""
    received = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _StreamingHandler.received.append(body)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        timings = {"prompt_n": 7, "predicted_n": 2}
        for line in (piece("Hello "), piece("world", finish="stop", timings=timings), "data: [DONE]"):
            self.wfile.write((line + "\n\n").encode("utf-8"))

    def log_message(self, *args):
        pass


class TestStreamTimer:
    @pytest.fixture
    def server_url(self):
        _StreamingHandler.received = []
        server = HTTPServer(("127.0.0.1", 0), _StreamingHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
        server.shutdown()
        server.server_close()

    def test_asks_for_a_stream_and_leaves_the_rest_of_the_body_alone(self, server_url):
        body = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 9, "temperature": 0.3}
        StreamTimer()(server_url, body, 5)
        assert _StreamingHandler.received == [{**body, "stream": True}]

    def test_returns_the_reply_in_the_shape_of_an_ordinary_one(self, server_url):
        reply = StreamTimer()(server_url, {"messages": []}, 5)
        assert reply == {"choices": [{"message": {"content": "Hello world"}, "finish_reason": "stop"}]}

    def test_remembers_the_timings_and_the_first_token_time(self, server_url):
        timer = StreamTimer()
        assert timer.last is None
        timer(server_url, {"messages": []}, 5)
        assert timer.last.timings == {"prompt_n": 7, "predicted_n": 2}
        assert timer.last.first_token_seconds is not None and timer.last.first_token_seconds >= 0

    def test_works_as_the_posting_function_of_the_real_client(self, server_url):
        from pom.voice import OracleModelClient
        client = OracleModelClient(server_url, 5, 300, 0.3, post=StreamTimer())
        assert client.portent("a prompt") == "Hello world"
        assert client.last_cut_off is False


class FakeClient:
    def __init__(self, seconds=5.0, cut_off=False):
        self.last_seconds, self.last_cut_off = seconds, cut_off


class FakeTimer:
    def __init__(self, last):
        self.last = last


class TestPortentRecord:
    def stream(self, **overrides):
        from benchmark_oracle_pi import StreamResult
        timings = {"prompt_n": 150, "cache_n": 90, "prompt_ms": 4200.0, "predicted_n": 60, "predicted_per_second": 3.456}
        return StreamResult(text="x", first_token_seconds=4.567, timings={**timings, **overrides})

    def test_holds_the_cell_the_times_and_the_counts(self):
        record = portent_record("alone", "hinge", 2, FakeClient(12.346), FakeTimer(self.stream()), "one two three")
        assert record["phase"] == "alone" and record["hinge"] == "hinge" and record["draw"] == 2
        assert record["seconds"] == 12.35
        assert record["first_token_seconds"] == 4.57
        assert record["prompt_tokens"] == 150 and record["cached_tokens"] == 90
        assert record["prompt_seconds"] == 4.2
        assert record["tokens"] == 60 and record["tokens_per_second"] == 3.46
        assert record["words"] == 3

    def test_the_portent_itself_is_never_in_the_record(self):
        record = portent_record("alone", "hinge", 1, FakeClient(), FakeTimer(self.stream()), "secret words here")
        assert "secret" not in json.dumps(record)

    def test_no_portent_is_zero_words(self):
        assert portent_record("p", "h", 1, FakeClient(), FakeTimer(self.stream()), None)["words"] == 0

    def test_cut_off_is_carried(self):
        assert portent_record("p", "h", 1, FakeClient(cut_off=True), FakeTimer(self.stream()), "a")["cut_off"] is True

    def test_missing_timings_give_nones_not_an_error(self):
        from benchmark_oracle_pi import StreamResult
        record = portent_record("p", "h", 1, FakeClient(), FakeTimer(StreamResult(text="a")), "a")
        assert record["tokens_per_second"] is None and record["prompt_tokens"] is None
        assert record["first_token_seconds"] is None

    def test_no_stream_at_all_gives_nones(self):
        record = portent_record("p", "h", 1, FakeClient(), FakeTimer(None), None)
        assert record["first_token_seconds"] is None and record["tokens"] is None

    def test_no_request_time_is_none(self):
        assert portent_record("p", "h", 1, FakeClient(seconds=None), FakeTimer(None), None)["seconds"] is None


class TestPercentile:
    def test_nearest_rank(self):
        values = list(range(1, 21))                # 1..20
        assert percentile(values, 0.95) == 19
        assert percentile(values, 0.5) == 10
        assert percentile(values, 1.0) == 20

    def test_unsorted_input(self):
        assert percentile([9, 1, 5], 0.5) == 5

    def test_one_value(self):
        assert percentile([7], 0.95) == 7

    def test_no_values(self):
        assert percentile([], 0.95) is None


class TestSummarizePortents:
    def records(self):
        return [{"words": 10, "seconds": 10.0, "first_token_seconds": 4.0, "prompt_tokens": 100,
                 "tokens_per_second": 3.0, "cut_off": False},
                {"words": 20, "seconds": 20.0, "first_token_seconds": 6.0, "prompt_tokens": 200,
                 "tokens_per_second": 2.0, "cut_off": True},
                {"words": 0, "seconds": 1.0, "first_token_seconds": None, "prompt_tokens": None,
                 "tokens_per_second": None, "cut_off": False}]

    def test_only_portents_that_were_made_are_summed(self):
        summary = summarize_portents(self.records())
        assert summary["requested"] == 3 and summary["made"] == 2

    def test_the_medians_and_the_worst(self):
        summary = summarize_portents(self.records())
        assert summary["seconds_median"] == 15.0
        assert summary["seconds_max"] == 20.0
        assert summary["first_token_median"] == 5.0
        assert summary["prompt_tokens_median"] == 150
        assert summary["tokens_per_second_median"] == 2.5
        assert summary["words_median"] == 15

    def test_the_time_is_the_median_not_the_mean(self):
        records = [{"words": 5, "seconds": s} for s in (1.0, 2.0, 9.0)]
        assert summarize_portents(records)["seconds_median"] == 2.0

    def test_cut_off_portents_are_counted(self):
        assert summarize_portents(self.records())["cut_off"] == 1

    def test_the_95th_percentile_of_the_times(self):
        records = [{"words": 5, "seconds": float(n)} for n in range(1, 21)]
        assert summarize_portents(records)["seconds_p95"] == 19.0

    def test_nothing_made_gives_nones(self):
        summary = summarize_portents([{"words": 0, "seconds": 1.0}])
        assert summary["made"] == 0 and summary["seconds_median"] is None and summary["seconds_max"] is None

    def test_no_records_at_all(self):
        assert summarize_portents([])["requested"] == 0


class TestSummarizeSamples:
    def sample(self, rss=None, free=None, temp=None, throttled=None):
        return {"rss_mb": rss or {}, "mem_available_mb": free, "temp_c": temp, "throttled": throttled}

    def test_the_peak_memory_of_each_server(self):
        samples = [self.sample(rss={"9b": 5000.4, "4b": 2500.0}), self.sample(rss={"9b": 6100.6, "4b": 2400.0})]
        assert summarize_samples(samples)["peak_rss_mb"] == {"9b": 6101, "4b": 2500}

    def test_the_least_free_memory_and_the_hottest_temperature(self):
        samples = [self.sample(free=9000.0, temp=61.0), self.sample(free=4200.4, temp=74.5), self.sample(free=8000.0, temp=70.0)]
        summary = summarize_samples(samples)
        assert summary["min_mem_available_mb"] == 4200 and summary["peak_temp_c"] == 74.5

    def test_missing_readings_are_ignored(self):
        samples = [self.sample(rss={"9b": None}), self.sample(rss={"9b": 100.0}, free=None, temp=None)]
        summary = summarize_samples(samples)
        assert summary["peak_rss_mb"] == {"9b": 100}
        assert summary["min_mem_available_mb"] is None and summary["peak_temp_c"] is None

    def test_throttling_flags_from_every_sample_are_combined(self):
        samples = [self.sample(throttled=0x0), self.sample(throttled=0x1), self.sample(throttled=0x4)]
        assert "under-voltage" in summarize_samples(samples)["throttled"]
        assert "currently throttled" in summarize_samples(samples)["throttled"]

    def test_never_throttled(self):
        assert summarize_samples([self.sample(throttled=0)])["throttled"] == "none"

    def test_no_throttle_readings_say_not_available(self):
        assert summarize_samples([self.sample()])["throttled"] == "n/a"

    def test_no_samples(self):
        summary = summarize_samples([])
        assert summary["samples"] == 0 and summary["peak_rss_mb"] == {} and summary["peak_temp_c"] is None


class TestBuildReport:
    def write_phase(self, directory, phase="dormant", with_summary=True):
        rows = [{"type": "run", "phase": phase, "prompt_sha256": "abc123", "corpus": "c0ffee", "commit": "1234567",
                 "uncommitted": False}]
        if with_summary:
            rows.append({"type": "summary", "phase": phase, "requested": 12, "made": 12, "cut_off": 0,
                         "seconds_median": 31.4, "seconds_p95": 60.2, "seconds_max": 70.0, "first_token_median": 12.3,
                         "prompt_tokens_median": 300, "tokens_per_second_median": 2.6, "words_median": 60,
                         "resources": {"peak_rss_mb": {"oracle-9b": 6500}, "min_mem_available_mb": 4000,
                                       "peak_temp_c": 71.2, "throttled": "none"}})
        (directory / f"{phase}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    def test_the_report_names_the_phase_and_the_numbers(self, tmp_path):
        self.write_phase(tmp_path)
        text = build_report(tmp_path)
        assert "Phase dormant" in text and "prompt abc123" in text
        assert "median 31.4" in text and "95th percentile 60.2" in text and "slowest 70.0" in text
        assert "first token (median): 12.3" in text
        assert "2.6 tokens/s" in text and "{'oracle-9b': 6500}" in text and "hottest 71.2" in text

    def test_startup_times_come_first(self, tmp_path):
        self.write_phase(tmp_path)
        (tmp_path / "startup.jsonl").write_text(
            json.dumps({"phase": "dormant", "what": "oracle-9b cold", "seconds": 48.5}) + "\n", encoding="utf-8")
        text = build_report(tmp_path)
        assert "oracle-9b cold" in text and "48.5 s" in text
        assert text.index("oracle-9b cold") < text.index("Phase dormant")

    def test_a_run_cut_short_says_so(self, tmp_path):
        self.write_phase(tmp_path, with_summary=False)
        assert "the run was cut short" in build_report(tmp_path)

    def test_sample_files_are_not_read_as_phases(self, tmp_path):
        self.write_phase(tmp_path)
        (tmp_path / "dormant-samples.jsonl").write_text(json.dumps({"t": 1.0}) + "\n", encoding="utf-8")
        assert build_report(tmp_path).count("Phase ") == 1

    def test_uncommitted_work_is_flagged(self, tmp_path):
        self.write_phase(tmp_path)
        path = tmp_path / "dormant.jsonl"
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        rows[0]["uncommitted"] = True
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        assert "+ uncommitted" in build_report(tmp_path)
