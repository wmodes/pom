"""Tests for pom.monitor.trigger_history: the last hour of triggers, in memory only."""
import logging
import threading

import pytest

from pom.corpus import Chunk
from pom.monitor import TriggerHistory, TriggerHistoryError, TriggerRecord
from pom.monitor.trigger_history import DEFAULT_MAX_AGE_SECONDS, DEFAULT_MAX_RECORDS
from pom.voice import Portent


class Clock:
    """A clock the test moves by hand."""

    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def portent(text="The ghosts keep the hours."):
    return Portent(text, (Chunk("book#1", "book", "verse", "an exemplar", 2),), 1.5, False)


def history(clock=None, **options):
    return TriggerHistory(clock=clock or Clock(), **options)


def add(h, hinge="The dog barks at ghosts.", **extra):
    values = dict(score=7.0, bar=6.5, window_size=120, heard_at=300.0)
    values.update(extra)
    return h.add(hinge, **values)


class TestTheDefaults:
    def test_the_starting_values(self):
        assert (DEFAULT_MAX_AGE_SECONDS, DEFAULT_MAX_RECORDS) == (3600.0, 500)

    def test_good_settings_are_quiet(self):
        assert history().report.ok
        assert history(max_age_seconds=0.001, max_records=1).report.ok

    def test_it_starts_empty(self):
        h = history()
        assert h.recent() == [] and h.total_added == 0 and h.get(1) is None


class TestRecording:
    def test_a_trigger_becomes_a_record_with_everything_given(self):
        clock = Clock(5000.0)
        h = history(clock)
        record = add(h, "Hello there.", score=8.25, bar=7.5, window_size=42, heard_at=61.5, portent=portent())
        assert isinstance(record, TriggerRecord)
        assert (record.id, record.at, record.heard_at) == (1, 5000.0, 61.5)
        assert (record.hinge, record.score, record.bar, record.window_size) == ("Hello there.", 8.25, 7.5, 42)
        assert record.portent == portent()

    def test_the_hinge_is_kept_word_for_word_but_trimmed(self):
        assert add(history(), "  Hello,  there!\n").hinge == "Hello,  there!"

    def test_numbers_only_go_up_from_one(self):
        h = history()
        assert [add(h).id for _ in range(3)] == [1, 2, 3]

    def test_a_trigger_without_a_portent_is_still_recorded(self):
        assert add(history(), portent=None).portent is None

    def test_a_trigger_with_no_bar_yet_is_recorded(self):
        assert add(history(), bar=None).bar is None

    def test_the_total_counts_what_was_accepted(self):
        h = history()
        add(h)
        add(h, "")
        add(h)
        assert h.total_added == 2

    def test_a_record_cannot_be_changed(self):
        record = add(history())
        with pytest.raises(AttributeError):
            record.hinge = "other"

    def test_the_time_is_the_clocks_when_it_was_added(self):
        clock = Clock(100.0)
        h = history(clock)
        first = add(h)
        clock.now = 160.0
        second = add(h)
        assert (first.at, second.at) == (100.0, 160.0)

    @pytest.mark.parametrize("bar", ["6", float("nan"), float("inf"), True, [1]])
    def test_a_bar_that_is_not_a_number_is_none(self, bar):
        assert add(history(), bar=bar).bar is None

    @pytest.mark.parametrize("window_size", [None, "5", 2.5, True])
    def test_a_window_size_that_is_not_a_whole_number_is_zero(self, window_size):
        assert add(history(), window_size=window_size).window_size == 0

    @pytest.mark.parametrize("heard_at", [None, "7", float("nan"), True])
    def test_a_place_in_the_audio_that_is_not_a_number_is_zero(self, heard_at):
        assert add(history(), heard_at=heard_at).heard_at == 0.0

    def test_a_score_is_stored_as_a_float(self):
        assert isinstance(add(history(), score=7).score, float)


class TestWhatCannotBeRecorded:
    @pytest.mark.parametrize("hinge", ["", "   ", "\n", None, 5, ["x"]])
    def test_a_missing_hinge_is_not_recorded(self, hinge):
        h = history()
        assert add(h, hinge) is None
        assert h.recent() == [] and len(h.report.warnings) == 1

    @pytest.mark.parametrize("score", [None, "7", float("nan"), float("inf"), True, [7]])
    def test_a_score_that_is_not_a_number_is_not_recorded(self, score):
        h = history()
        assert add(h, score=score) is None
        assert h.recent() == [] and len(h.report.warnings) == 1

    def test_strict_mode_raises_for_them(self):
        with pytest.raises(TriggerHistoryError):
            add(history(strict=True), "")
        with pytest.raises(TriggerHistoryError):
            add(history(strict=True), score="x")

    def test_a_zero_score_is_a_real_score(self):
        assert add(history(), score=0).score == 0.0


class TestReading:
    def test_the_newest_comes_first(self):
        h = history()
        for text in ("one", "two", "three"):
            add(h, text)
        assert [r.hinge for r in h.recent()] == ["three", "two", "one"]

    def test_the_list_is_a_copy(self):
        h = history()
        add(h)
        h.recent().clear()
        assert len(h.recent()) == 1

    def test_a_record_is_found_by_its_number(self):
        h = history()
        add(h, "one")
        second = add(h, "two")
        assert h.get(2) == second and h.get(1).hinge == "one"

    def test_an_unknown_number_finds_nothing(self):
        h = history()
        add(h)
        assert h.get(2) is None and h.get(0) is None and h.get(-1) is None and h.get("1") is None


class TestForgetting:
    def test_records_older_than_the_limit_are_dropped(self):
        clock = Clock()
        h = history(clock, max_age_seconds=3600)
        add(h, "old")
        clock.now += 1800
        add(h, "newer")
        clock.now += 1801                                  # the first is now 3601 s old, the second 1801
        assert [r.hinge for r in h.recent()] == ["newer"]

    def test_a_record_exactly_at_the_limit_is_still_kept(self):
        clock = Clock()
        h = history(clock, max_age_seconds=3600)
        add(h)
        clock.now += 3600
        assert len(h.recent()) == 1
        clock.now += 0.001
        assert h.recent() == []

    def test_an_expired_record_cannot_be_fetched_either(self):
        clock = Clock()
        h = history(clock, max_age_seconds=60)
        record = add(h)
        clock.now += 61
        assert h.get(record.id) is None

    def test_expiry_does_not_disturb_the_numbering(self):
        clock = Clock()
        h = history(clock, max_age_seconds=60)
        add(h)
        clock.now += 100
        assert add(h).id == 2

    def test_the_oldest_go_first_when_there_are_too_many(self):
        h = history(max_records=3)
        for i in range(5):
            add(h, f"hinge {i}")
        assert [r.hinge for r in h.recent()] == ["hinge 4", "hinge 3", "hinge 2"]
        assert h.total_added == 5

    def test_a_clock_that_steps_back_does_not_drop_records(self):
        clock = Clock(1000.0)
        h = history(clock, max_age_seconds=60)
        add(h)
        clock.now = 500.0
        assert len(h.recent()) == 1

    def test_the_age_limit_is_a_setting(self):
        clock = Clock()
        h = history(clock, max_age_seconds=10)
        add(h)
        clock.now += 11
        assert h.recent() == []


class TestWhenSeveralThreadsUseIt:
    def test_many_adders_and_a_reader_leave_every_record_whole(self):
        h = history(max_records=1000)
        errors = []

        def adder(name):
            for i in range(100):
                if add(h, f"{name} {i}") is None:
                    errors.append("not recorded")

        def reader():
            for _ in range(200):
                h.recent()

        threads = [threading.Thread(target=adder, args=(f"t{n}",)) for n in range(8)] + [threading.Thread(target=reader)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        records = h.recent()
        assert not errors and len(records) == 800 and h.total_added == 800
        assert sorted(r.id for r in records) == list(range(1, 801))


class TestTheLockAndTheMemory:
    def test_every_change_and_read_takes_the_lock(self):
        class SpyLock:
            entered = 0

            def __enter__(self):
                SpyLock.entered += 1

            def __exit__(self, *exc):
                return False

        h = history()
        h._lock = SpyLock()
        add(h)
        assert SpyLock.entered == 1
        h.recent()
        assert SpyLock.entered == 2
        h.get(1)
        assert SpyLock.entered == 3

    def test_old_records_are_dropped_when_a_new_one_arrives_even_if_nobody_reads(self):
        clock = Clock()
        h = history(clock, max_age_seconds=60)
        add(h, "old")
        clock.now += 100
        add(h, "new")
        assert len(h._records) == 1


class TestBadSettings:
    @pytest.mark.parametrize("bad", [0, -1, "3600", None, True, [60], float("nan"), float("inf")])
    def test_a_bad_age_means_the_default_with_a_warning(self, bad):
        h = history(max_age_seconds=bad)
        assert len(h.report.warnings) == 1 and "max_age_seconds" in h.report.warnings[0]
        clock = Clock()
        h = history(clock, max_age_seconds=bad)
        add(h)
        clock.now += 3599
        assert len(h.recent()) == 1
        clock.now += 2
        assert h.recent() == []                            # the default hour

    @pytest.mark.parametrize("bad", [0, -1, 2.5, "500", None, True])
    def test_a_bad_record_limit_means_the_default_with_a_warning(self, bad):
        h = history(max_records=bad)
        assert len(h.report.warnings) == 1 and "max_records" in h.report.warnings[0]
        for _ in range(DEFAULT_MAX_RECORDS + 5):
            add(h)
        assert len(h.recent()) == DEFAULT_MAX_RECORDS

    @pytest.mark.parametrize("option,bad", [("max_age_seconds", 0), ("max_records", 0)])
    def test_strict_mode_raises(self, option, bad):
        with pytest.raises(TriggerHistoryError, match=option):
            history(strict=True, **{option: bad})

    def test_strict_mode_with_good_settings_does_not_raise(self):
        assert history(strict=True).report.ok

    def test_two_bad_settings_give_two_warnings(self):
        assert len(history(max_age_seconds=0, max_records=0).report.warnings) == 2


class TestNothingSaidInTheRoomIsKept:
    def test_neither_the_hinge_nor_the_portent_reaches_the_log_or_the_report(self, caplog):
        caplog.set_level(logging.DEBUG)
        h = history()
        add(h, "My secret is the neighbor.", portent=portent("the neighbor keeps secret hours"))
        add(h, "", portent=None)
        add(h, "A hinge.", score="not a number")
        text = caplog.text + "\n".join(h.report.lines())
        assert "secret" not in text and "neighbor" not in text and "hinge" not in text.replace("without a hinge", "")
