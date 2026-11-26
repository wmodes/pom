"""Tests for pom.bookkeeper.log_book: the book that holds a module's faults, through its public interface only."""
import threading
import time

import pytest

from pom.bookkeeper.fault import Fault, Severity
from pom.bookkeeper.log_book import LogBook


def warning(text="w"):
    return Fault(Severity.WARNING, text)


def critical(text="c"):
    return Fault(Severity.CRITICAL, text)


def test_a_new_book_is_empty_and_ok():
    book = LogBook(3)
    assert book.ok and len(book) == 0 and book.lines() == [] and book.faults == () and book.suppressed == 0 and not book.has_critical


def test_it_keeps_the_first_faults_up_to_the_limit_and_counts_the_rest():
    book = LogBook(2)
    for text in ("a", "b", "c", "d"):
        book.add(warning(text))
    assert book.warnings == ["a", "b"] and book.suppressed == 2 and len(book) == 4 and not book.ok
    assert book.lines() == ["warning: a", "warning: b", "... and 2 more"]


def test_a_critical_is_always_kept_even_past_the_limit():
    book = LogBook(1)
    book.add(warning("a"))
    book.add(warning("b"))
    book.add(critical("must keep"))
    assert book.criticals == ["must keep"] and book.has_critical and book.suppressed == 1


def test_the_readers_pick_each_severity():
    book = LogBook(10)
    book.add(warning("w"))
    book.add(Fault(Severity.ERROR, "e"))
    book.add(critical("c"))
    assert book.warnings == ["w"] and book.errors == ["e"] and book.criticals == ["c"]
    assert [fault.message for fault in book] == ["w", "e", "c"]


def test_only_a_critical_makes_has_critical_true():
    book = LogBook(10)
    book.add(warning())
    book.add(Fault(Severity.ERROR, "e"))
    assert not book.has_critical


def test_faults_comes_back_as_a_copy_the_caller_cannot_use_to_change_the_book():
    book = LogBook(10)
    book.add(warning("a"))
    taken = book.faults
    book.add(warning("b"))
    assert [fault.message for fault in taken] == ["a"] and len(book.faults) == 2
    assert isinstance(book.warnings, list) and book.warnings is not book.warnings


def test_iterating_while_it_grows_is_safe():
    book = LogBook(10)
    book.add(warning("a"))
    seen = []
    for fault in book:
        book.add(warning("more"))
        seen.append(fault.message)
    assert seen == ["a"]


def test_counting_suppressed_ones_from_another_book_adds_to_the_count_and_makes_it_not_ok():
    book = LogBook(10)
    book.count_suppressed(3)
    assert book.suppressed == 3 and not book.ok and book.lines() == ["... and 3 more"] and len(book) == 3


def test_a_limit_of_zero_keeps_nothing_but_criticals():
    book = LogBook(0)
    book.add(warning())
    book.add(critical())
    assert book.warnings == [] and book.criticals == ["c"] and book.suppressed == 1


def test_two_threads_are_never_adding_at_the_same_time_so_the_limit_holds():
    """A fault whose severity is slow to read, and that notices company, shows whether the threads are kept apart while the book decides."""
    gate = threading.Lock()
    inside = []
    most_at_once = [0]

    class WatchingFault(Fault):
        @property
        def severity(self):
            with gate:
                inside.append(1)
                most_at_once[0] = max(most_at_once[0], len(inside))
            time.sleep(0.002)
            with gate:
                inside.pop()
            return Severity.WARNING

        def __init__(self):
            object.__setattr__(self, "message", "slow")

    book = LogBook(5)
    threads = [threading.Thread(target=lambda: [book.add(WatchingFault()) for _ in range(3)]) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert most_at_once[0] == 1
    assert len(book.faults) == 5 and book.suppressed == 8 * 3 - 5
