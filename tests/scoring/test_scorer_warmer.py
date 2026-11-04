"""Tests for pom.scoring.scorer_warmer: one throwaway request to the scorer once its server answers."""
import logging

import pytest

from pom.scoring import ScorerWarmer
from pom.scoring import scorer_warmer as warmer_module


class FakeScorer:
    """Answers warm-ups from a script of True and False (the last one repeats), counting the requests."""

    def __init__(self, *results):
        self.results, self.calls = list(results), 0

    def warm_up(self):
        self.calls += 1
        return self.results[min(self.calls, len(self.results)) - 1]


class FakeServer:
    """A health check that answers from a script of True and False (the last one repeats), counting the checks."""

    def __init__(self, *states):
        self.states, self.checks = list(states), 0

    def __call__(self):
        self.checks += 1
        return self.states[min(self.checks, len(self.states)) - 1]


def warmer_for(scorer, server, **options):
    options.setdefault("poll_seconds", 0.0)
    return ScorerWarmer(scorer, server, **options)


class TestWarmingOnceTheServerIsUp:
    def test_a_server_that_is_up_is_warmed_with_one_request(self):
        scorer, warmer = FakeScorer(True), None
        warmer = warmer_for(scorer, FakeServer(True))
        assert warmer.run() is True and warmer.warmed is True and scorer.calls == 1

    def test_nothing_is_sent_while_the_server_is_down(self):
        scorer, server = FakeScorer(True), FakeServer(False, False, False, True)
        assert warmer_for(scorer, server).run() is True
        assert server.checks == 4 and scorer.calls == 1

    def test_it_is_not_warmed_until_a_warm_up_has_been_answered(self):
        scorer = FakeScorer(False)
        warmer = warmer_for(scorer, FakeServer(True), attempts=1)
        assert warmer.run() is False and warmer.warmed is False

    def test_a_warm_up_that_fails_is_tried_again(self):
        scorer = FakeScorer(False, True)
        warmer = warmer_for(scorer, FakeServer(True))
        assert warmer.run() is True and scorer.calls == 2 and warmer.warmed is True

    @pytest.mark.parametrize("attempts", [1, 2, 3, 5])
    def test_it_gives_up_after_that_many_failed_warm_ups(self, attempts):
        scorer = FakeScorer(False)
        assert warmer_for(scorer, FakeServer(True), attempts=attempts).run() is False
        assert scorer.calls == attempts

    def test_a_server_that_is_down_does_not_use_up_the_attempts(self):
        scorer, server = FakeScorer(False, False, True), FakeServer(True, False, False, True, False, True)
        assert warmer_for(scorer, server, attempts=3).run() is True and scorer.calls == 3

    def test_the_default_attempts_are_three(self):
        scorer = FakeScorer(False)
        assert warmer_for(scorer, FakeServer(True)).run() is False and scorer.calls == warmer_module.DEFAULT_ATTEMPTS == 3

    def test_the_default_poll_is_a_few_seconds(self):
        assert warmer_module.DEFAULT_POLL_SECONDS == 5.0


class TestWhatItSaysInTheLog:
    def test_a_warm_up_is_logged_with_the_seconds_it_took(self, caplog):
        times = iter([100.0, 115.7])
        warmer = warmer_for(FakeScorer(True), FakeServer(True), clock=lambda: next(times))
        with caplog.at_level(logging.INFO, logger="pom.scoring.scorer_warmer"):
            warmer.run()
        assert any("warmed up" in r.getMessage() and r.getMessage().endswith("): 15.7 s") and r.levelno == logging.INFO
                   for r in caplog.records)

    def test_giving_up_is_a_warning_that_counts_the_requests(self, caplog):
        with caplog.at_level(logging.INFO, logger="pom.scoring.scorer_warmer"):
            warmer_for(FakeScorer(False), FakeServer(True), attempts=2).run()
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1 and "2 warm-up requests" in warnings[0].getMessage()
        assert not any("warmed up" in r.getMessage() for r in caplog.records)


class TestInTheBackground:
    def test_start_warms_the_scorer_in_a_thread_and_the_thread_ends(self):
        scorer = FakeScorer(True)
        warmer = warmer_for(scorer, FakeServer(False, True))
        warmer.start()
        warmer._thread.join(timeout=5)
        assert not warmer._thread.is_alive() and warmer.warmed and scorer.calls == 1

    def test_the_thread_does_not_keep_the_program_alive(self):
        warmer = warmer_for(FakeScorer(True), FakeServer(False))
        warmer.start()
        try:
            assert warmer._thread.daemon is True
        finally:
            warmer.stop()

    def test_starting_twice_starts_one_thread(self):
        scorer = FakeScorer(True)
        warmer = warmer_for(scorer, FakeServer(True))
        warmer.start()
        first = warmer._thread
        warmer.start()
        assert warmer._thread is first
        first.join(timeout=5)
        assert scorer.calls == 1

    def test_stop_ends_the_wait_for_a_server_that_never_comes(self):
        warmer = ScorerWarmer(FakeScorer(True), FakeServer(False), poll_seconds=60.0)
        warmer.start()
        warmer.stop()
        assert not warmer._thread.is_alive() and warmer.warmed is False

    def test_stopped_before_it_runs_it_asks_nothing(self):
        scorer, server = FakeScorer(True), FakeServer(True)
        warmer = warmer_for(scorer, server)
        warmer.stop()
        assert warmer.run() is False and server.checks == 0 and scorer.calls == 0

    def test_stop_before_start_and_stop_twice_are_safe(self):
        warmer = warmer_for(FakeScorer(True), FakeServer(True))
        warmer.stop()
        warmer.stop()

    def test_an_error_in_the_thread_is_a_warning_and_never_raises(self, caplog):
        def broken():
            raise RuntimeError("boom with some words")
        warmer = warmer_for(FakeScorer(True), broken)
        with caplog.at_level(logging.WARNING, logger="pom.scoring.scorer_warmer"):
            warmer.start()
            warmer._thread.join(timeout=5)
        messages = [r.getMessage() for r in caplog.records]
        assert not warmer._thread.is_alive() and warmer.warmed is False
        assert len(messages) == 1 and "RuntimeError" in messages[0] and "boom" not in messages[0]
