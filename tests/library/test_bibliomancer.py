"""Tests for pom.library.bibliomancer: weighted random draws that balance shelves over time.

Statistical tests use a seeded random generator, so they are deterministic: a pass is a pass every
time. Tolerances are generous (several standard deviations) so they check the odds, not the luck.

The module follows the project principle that nothing may stop the oracle: by default problems are
recorded in a FaultRecorder and the Bibliomancer returns what it can. In strict mode the same problems raise.
"""
import collections
import logging
import random
from pathlib import Path

import pytest

from pom.library import Passage, Catalog, Circulation, Bibliomancer, BibliomancerError

REPO = Path(__file__).resolve().parents[2]


def make_store(layout):
    """Builds a store: layout is {shelf: {holding: how_many_passages}}."""
    passages = []
    for shelf, holdings in layout.items():
        for holding, n in holdings.items():
            for i in range(n):
                passages.append(Passage(f"{holding}#{i:05d}", holding, shelf, f"{holding} number {i}", 3))
    return Catalog.from_passages(passages)


def circulation_for(**shelves):
    """circulation_for(red={"share": 1, "holdings": {"a": 0.5}}, ...)"""
    return Circulation.from_dict({"shelves": shelves})


def tiles(**extra):
    """The tile example: red 40, blue 20, green 10 tiles, equal shares."""
    store = make_store({"red": {"r": 40}, "blue": {"b": 20}, "green": {"g": 10}})
    return store, circulation_for(red={}, blue={}, green={}, **extra)


def shares_of(selector, draws, count=1):
    """The fraction of drawn passages that came from each shelf over many pulls."""
    seen = collections.Counter()
    for _ in range(draws):
        for passage in selector.draw(count):
            seen[passage.shelf] += 1
    total = sum(seen.values())
    return {shelf: n / total for shelf, n in seen.items()}


# --- The odds --------------------------------------------------------------------------

def test_the_tile_example_gives_each_tile_the_right_chance():
    store, circulation = tiles()
    selector = Bibliomancer(store, circulation, random.Random(1))
    red, blue, green = (store.passages(r)[0] for r in ("red", "blue", "green"))
    assert selector.probability(red) == pytest.approx((1 / 3) / 40)
    assert selector.probability(blue) == pytest.approx((1 / 3) / 20)
    assert selector.probability(green) == pytest.approx((1 / 3) / 10)
    assert sum(selector.probability(c) for c in store) == pytest.approx(1.0)


def test_shelf_shares_are_normalized():
    store, circulation = tiles()
    shares = Bibliomancer(store, circulation).shelf_shares()
    assert shares == pytest.approx({"red": 1 / 3, "blue": 1 / 3, "green": 1 / 3})


def test_a_thumb_on_the_scale_changes_the_shares():
    store = make_store({"red": {"r": 40}, "blue": {"b": 20}, "green": {"g": 10}})
    circulation = circulation_for(red={"share": 0.8}, blue={"share": 1}, green={"share": 1})
    shares = Bibliomancer(store, circulation).shelf_shares()
    assert shares["red"] == pytest.approx(0.8 / 2.8)
    assert shares["blue"] == pytest.approx(1 / 2.8)


def test_holding_weights_shape_the_odds_within_a_shelf_only():
    store = make_store({"news": {"ads": 10, "obits": 10}, "poetry": {"p": 10}})
    circulation = circulation_for(news={"holdings": {"ads": 1, "obits": 0.5}}, poetry={})
    selector = Bibliomancer(store, circulation)
    ads, obits = store.passages("news", "ads")[0], store.passages("news", "obits")[0]
    assert selector.probability(ads) == pytest.approx(2 * selector.probability(obits))
    news_total = sum(selector.probability(c) for c in store.passages("news"))
    assert news_total == pytest.approx(0.5)            # the shelf still gets its full share


def test_an_unlisted_shelf_counts_one():
    store = make_store({"red": {"r": 5}, "blue": {"b": 5}})
    shares = Bibliomancer(store, circulation_for(red={"share": 3})).shelf_shares()   # blue unlisted
    assert shares == pytest.approx({"red": 0.75, "blue": 0.25})


def test_probability_of_an_unknown_passage_is_zero():
    store, circulation = tiles()
    assert Bibliomancer(store, circulation).probability(Passage("x#0", "x", "red", "Not here", 2)) == 0.0


def test_a_zero_weight_holding_has_no_chance():
    store = make_store({"news": {"ads": 5, "obits": 5}})
    selector = Bibliomancer(store, circulation_for(news={"holdings": {"obits": 0}}))
    assert selector.probability(store.passages("news", "obits")[0]) == 0.0
    assert selector.probability(store.passages("news", "ads")[0]) == pytest.approx(1 / 5)


def test_a_zero_share_shelf_has_no_chance():
    store = make_store({"red": {"r": 5}, "blue": {"b": 5}})
    selector = Bibliomancer(store, circulation_for(red={"share": 0}, blue={}))
    assert selector.probability(store.passages("red")[0]) == 0.0
    assert selector.shelf_shares()["blue"] == pytest.approx(1.0)


def test_a_shelf_whose_holdings_all_weigh_zero_is_skipped_with_a_warning():
    store = make_store({"red": {"r": 5}, "blue": {"b": 5}})
    selector = Bibliomancer(store, circulation_for(red={"holdings": {"r": 0}}, blue={}))
    assert selector.ready
    assert selector.shelf_shares() == pytest.approx({"blue": 1.0})
    assert any("red" in w and "no drawable passages" in w for w in selector.report.warnings)


# --- Nothing to draw -------------------------------------------------------------------

def test_an_empty_store_is_critical_and_draws_nothing_without_raising():
    selector = Bibliomancer(Catalog.from_passages([]), Circulation.from_dict({}))
    assert not selector.ready and selector.report.has_critical
    assert any("nothing to draw from" in m for m in selector.report.criticals)
    assert selector.draw(5) == [] and selector.draw() == []


def test_when_every_weight_is_zero_it_is_critical_and_draws_nothing():
    store = make_store({"red": {"r": 5}})
    selector = Bibliomancer(store, circulation_for(red={"holdings": {"r": 0}}))
    assert not selector.ready and selector.report.has_critical
    assert any("no passage has a positive weight" in m for m in selector.report.criticals)
    assert any("no drawable passages" in w for w in selector.report.warnings)
    assert selector.draw(3) == []


def test_when_every_share_is_zero_the_shelves_count_equally_instead():
    """Circulation rescues an all-zero share set, so there is still something to say."""
    store = make_store({"red": {"r": 5}, "blue": {"b": 5}})
    selector = Bibliomancer(store, circulation_for(red={"share": 0}, blue={"share": 0}))
    assert selector.ready
    assert selector.shelf_shares() == pytest.approx({"red": 0.5, "blue": 0.5})


# --- Drawing ---------------------------------------------------------------------------

def test_draw_returns_the_requested_number_of_distinct_passages():
    store, circulation = tiles()
    passages = Bibliomancer(store, circulation, random.Random(2)).draw(5)
    assert len(passages) == 5 and len({c.id for c in passages}) == 5
    assert all(isinstance(c, Passage) for c in passages)


@pytest.mark.parametrize("count", [0, -3])
def test_zero_or_negative_counts_draw_nothing(count):
    store, circulation = tiles()
    assert Bibliomancer(store, circulation).draw(count) == []


def test_asking_for_more_than_exist_returns_all_of_them():
    store = make_store({"red": {"r": 3}})
    passages = Bibliomancer(store, circulation_for(red={}), random.Random(3)).draw(10)
    assert sorted(c.id for c in passages) == ["r#00000", "r#00001", "r#00002"]


def test_a_pull_never_repeats_a_passage():
    store = make_store({"red": {"r": 3}, "blue": {"b": 3}})
    selector = Bibliomancer(store, circulation_for(red={}, blue={}), random.Random(4))
    for _ in range(1000):
        ids = [c.id for c in selector.draw(5)]
        assert len(ids) == len(set(ids)) == 5


def test_skewed_odds_still_complete_a_pull_without_repeats():
    """With one passage at ~98% a plain redraw would rarely finish; the exact fallback must."""
    passages = [Passage("big#0", "big", "red", "Big one", 2), Passage("a#0", "a", "red", "Small a", 2),
              Passage("b#0", "b", "red", "Small b", 2)]
    store = Catalog.from_passages(passages)
    circulation = circulation_for(red={"holdings": {"big": 98, "a": 1, "b": 1}})
    for seed in range(50):
        pulled = Bibliomancer(store, circulation, random.Random(seed)).draw(3)
        assert sorted(c.id for c in pulled) == ["a#0", "b#0", "big#0"]


def test_zero_chance_passages_are_never_returned_even_when_more_are_asked_for():
    """Ask for ten when only five can be drawn: the zero-weight passages must not fill the gap."""
    store = make_store({"news": {"ads": 5, "off": 5}})
    selector = Bibliomancer(store, circulation_for(news={"holdings": {"off": 0}}), random.Random(6))
    pulled = selector.draw(10)
    assert len(pulled) == 5 and {c.holding for c in pulled} == {"ads"}


def test_the_exact_fallback_still_follows_the_odds():
    """Skewed odds make the fallback path common; it must stay proportional, not just complete."""
    passages = [Passage("big#0", "big", "red", "Big one", 2), Passage("a#0", "a", "red", "Small a", 2),
              Passage("b#0", "b", "red", "Small b", 2)]
    store = Catalog.from_passages(passages)
    circulation = circulation_for(red={"holdings": {"big": 98, "a": 1, "b": 4}})
    selector = Bibliomancer(store, circulation, random.Random(9))
    pulls = 4000
    with_b = sum(any(c.holding == "b" for c in selector.draw(2)) for _ in range(pulls))
    # P(b in a pull of 2) = 4/103 + (98/103)(4/5) + (1/103)(4/102), about 0.80
    assert with_b / pulls == pytest.approx(0.80, abs=0.04)


def test_the_largest_random_value_below_one_cannot_pick_past_the_end():
    import math

    class Top(random.Random):
        def random(self):
            return math.nextafter(1.0, 0.0)

    for layout in ({"red": {"r": 40}, "blue": {"b": 20}, "green": {"g": 10}},
                   {"red": {"r": 3}}, {"red": {"r": 7}, "blue": {"b": 11}}):
        store = make_store(layout)
        selector = Bibliomancer(store, circulation_for(**{name: {} for name in layout}), Top())
        assert len(selector.draw(1)) == 1          # no IndexError


def test_a_generator_that_returns_exactly_one_still_cannot_pick_past_the_end():
    """random() should stay below 1, but rounding can land a pick exactly on the total; the table
    must clamp to its last passage instead of raising IndexError."""
    class One(random.Random):
        def random(self):
            return 1.0

    store, circulation = tiles()
    pulled = Bibliomancer(store, circulation, One()).draw(1)
    assert len(pulled) == 1


def test_the_same_seed_gives_the_same_draws():
    store, circulation = tiles()
    one = [c.id for c in Bibliomancer(store, circulation, random.Random(7)).draw(5)]
    two = [c.id for c in Bibliomancer(store, circulation, random.Random(7)).draw(5)]
    other = [c.id for c in Bibliomancer(store, circulation, random.Random(8)).draw(5)]
    assert one == two and one != other


def test_without_an_injected_generator_it_still_draws():
    store, circulation = tiles()
    assert len(Bibliomancer(store, circulation).draw(3)) == 3


def test_the_injected_generator_is_the_one_used():
    store, circulation = tiles()

    class Fixed(random.Random):
        calls = 0

        def random(self):
            Fixed.calls += 1
            return super().random()

    Bibliomancer(store, circulation, Fixed(1)).draw(5)
    assert Fixed.calls >= 5


# --- Any combination is possible, and the long run matches the targets ------------------

def test_five_from_one_shelf_is_possible_and_so_is_a_mix():
    store = make_store({"red": {"r": 8}, "blue": {"b": 8}})
    selector = Bibliomancer(store, circulation_for(red={}, blue={}), random.Random(11))
    spreads = collections.Counter()
    for _ in range(4000):
        pull = selector.draw(5)
        spreads[len({c.shelf for c in pull})] += 1
    assert spreads[1] > 0, "an all-one-shelf pull never happened"
    assert spreads[2] > 0, "a mixed pull never happened"


def test_over_many_draws_each_shelf_gets_its_target_share():
    """The tile example: 40, 20 and 10 tiles, equal shares, so a third each despite the sizes."""
    store, circulation = tiles()
    shares = shares_of(Bibliomancer(store, circulation, random.Random(21)), 30000)
    for shelf in ("red", "blue", "green"):
        assert shares[shelf] == pytest.approx(1 / 3, abs=0.02)


def test_over_many_draws_a_thumb_on_the_scale_shows_up():
    store = make_store({"red": {"r": 40}, "blue": {"b": 20}, "green": {"g": 10}})
    circulation = circulation_for(red={"share": 0.8}, blue={"share": 1}, green={"share": 1})
    shares = shares_of(Bibliomancer(store, circulation, random.Random(22)), 30000)
    assert shares["red"] == pytest.approx(0.8 / 2.8, abs=0.02)
    assert shares["blue"] == pytest.approx(1 / 2.8, abs=0.02)


def test_drawing_several_at_a_time_keeps_the_same_long_run_shares():
    store, circulation = tiles()
    shares = shares_of(Bibliomancer(store, circulation, random.Random(23)), 6000, count=5)
    for shelf in ("red", "blue", "green"):
        assert shares[shelf] == pytest.approx(1 / 3, abs=0.03)


def test_within_a_shelf_every_passage_is_about_equally_likely():
    store = make_store({"red": {"r": 20}, "blue": {"b": 5}})
    selector = Bibliomancer(store, circulation_for(red={}, blue={}), random.Random(24))
    counts = collections.Counter(c.id for _ in range(20000) for c in selector.draw(1))
    expected = 20000 * 0.5 / 20
    for passage in store.passages("red"):
        assert counts[passage.id] == pytest.approx(expected, rel=0.25)


def test_holding_weights_show_up_in_the_long_run():
    store = make_store({"news": {"ads": 10, "obits": 10}})
    selector = Bibliomancer(store, circulation_for(news={"holdings": {"obits": 0.5}}), random.Random(25))
    seen = collections.Counter(c.holding for _ in range(20000) for c in selector.draw(1))
    assert seen["ads"] / seen["obits"] == pytest.approx(2.0, rel=0.1)


def test_passages_with_no_chance_are_never_drawn():
    store = make_store({"red": {"r": 5}, "blue": {"b": 5}, "off": {"o": 5}})
    circulation = circulation_for(red={"holdings": {"r": 0}}, blue={}, off={"share": 0})
    selector = Bibliomancer(store, circulation, random.Random(26))
    seen = {c.shelf for _ in range(5000) for c in selector.draw(3)}
    assert seen == {"blue"}


# --- Counting draws --------------------------------------------------------------------

def test_draw_counts_track_which_shelves_supplied_passages():
    store = make_store({"red": {"r": 5}, "blue": {"b": 5}})
    selector = Bibliomancer(store, circulation_for(red={}, blue={}), random.Random(31))
    assert selector.draw_counts() == {"red": 0, "blue": 0}
    passages = selector.draw(4)
    counts = selector.draw_counts()
    assert sum(counts.values()) == 4
    assert counts["red"] == sum(1 for c in passages if c.shelf == "red")


def test_reset_counts_returns_them_to_zero():
    store, circulation = tiles()
    selector = Bibliomancer(store, circulation, random.Random(32))
    selector.draw(5)
    selector.reset_counts()
    assert set(selector.draw_counts().values()) == {0}


def test_draw_counts_are_a_copy():
    store, circulation = tiles()
    selector = Bibliomancer(store, circulation)
    selector.draw_counts()["red"] = 99
    assert selector.draw_counts()["red"] == 0


# --- How many passages a pull holds ------------------------------------------------------

def test_the_default_pull_is_three_to_five_passages():
    store, circulation = tiles()
    selector = Bibliomancer(store, circulation, random.Random(41))
    assert selector.exemplar_range == (3, 5)
    sizes = collections.Counter(len(selector.draw()) for _ in range(3000))
    assert set(sizes) == {3, 4, 5}          # all three sizes occur, and nothing outside the range


def test_a_custom_range_is_used():
    store, circulation = tiles()
    selector = Bibliomancer(store, circulation, random.Random(42), exemplar_range=(2, 2))
    assert {len(selector.draw()) for _ in range(50)} == {2}


def test_an_explicit_count_overrides_the_range():
    store, circulation = tiles()
    assert len(Bibliomancer(store, circulation, random.Random(43), exemplar_range=(2, 2)).draw(4)) == 4


def test_a_reversed_range_is_swapped_with_a_warning():
    store, circulation = tiles()
    selector = Bibliomancer(store, circulation, exemplar_range=(5, 3))
    assert selector.exemplar_range == (3, 5)
    assert any("backwards" in w for w in selector.report.warnings)


@pytest.mark.parametrize("bad", [(0, 3), (-1, 5), (3, 0), ("3", "5"), (None, 5), (3,), 5, None,
                                 (True, 5), (2.5, 4), (3, 4, 5)])
def test_a_bad_range_falls_back_to_the_default_with_a_warning(bad):
    store, circulation = tiles()
    selector = Bibliomancer(store, circulation, exemplar_range=bad)
    assert selector.exemplar_range == (3, 5)
    assert any("exemplar_range must be" in w for w in selector.report.warnings)


def test_the_range_choice_is_repeatable_with_a_seed():
    store, circulation = tiles()
    a = [len(Bibliomancer(store, circulation, random.Random(5)).draw()) for _ in range(3)]
    b = [len(Bibliomancer(store, circulation, random.Random(5)).draw()) for _ in range(3)]
    assert a == b


# --- Strict mode (development and tests) -----------------------------------------------

def test_strict_raises_for_an_empty_store():
    with pytest.raises(BibliomancerError, match="nothing to draw from"):
        Bibliomancer(Catalog.from_passages([]), Circulation.from_dict({}), strict=True)


def test_strict_raises_when_every_weight_is_zero():
    """Strict raises on the first problem found, here the shelf with nothing drawable."""
    store = make_store({"red": {"r": 5}})
    with pytest.raises(BibliomancerError, match="no drawable passages"):
        Bibliomancer(store, circulation_for(red={"holdings": {"r": 0}}), strict=True)


def test_strict_raises_for_a_shelf_with_nothing_drawable():
    store = make_store({"red": {"r": 5}, "blue": {"b": 5}})
    with pytest.raises(BibliomancerError, match="no drawable passages"):
        Bibliomancer(store, circulation_for(red={"holdings": {"r": 0}}, blue={}), strict=True)


def test_strict_raises_for_a_bad_range():
    store, circulation = tiles()
    with pytest.raises(BibliomancerError, match="exemplar_range must be"):
        Bibliomancer(store, circulation, exemplar_range=(0, 3), strict=True)
    with pytest.raises(BibliomancerError, match="backwards"):
        Bibliomancer(store, circulation, exemplar_range=(5, 3), strict=True)


def test_strict_accepts_a_clean_setup():
    store, circulation = tiles()
    assert Bibliomancer(store, circulation, random.Random(1), strict=True).ready


# --- Layout drift, logging -------------------------------------------------------------

def test_layout_drift_is_reported_but_does_not_stop_the_sampler():
    store = make_store({"red": {"r": 5}})
    circulation = circulation_for(red={"holdings": {"ghost": 2}}, cooking={})
    selector = Bibliomancer(store, circulation, random.Random(1))
    assert selector.ready
    assert any("ghost" in w for w in selector.check_report.warnings)
    assert any("cooking" in w for w in selector.check_report.warnings)


def test_building_logs_a_summary_and_problems(caplog):
    store = make_store({"red": {"r": 5}, "blue": {"b": 5}})
    with caplog.at_level(logging.INFO, logger="pom.library.bibliomancer"):
        Bibliomancer(store, circulation_for(red={"holdings": {"r": 0}}, blue={}))
    messages = [r.message for r in caplog.records if r.name == "pom.library.bibliomancer"]
    assert any("no drawable passages" in m for m in messages)
    assert any("selector ready: 5 drawable passage(s) in 1 shelf(s)" in m for m in messages)


def test_an_empty_store_logs_a_critical_line(caplog):
    with caplog.at_level(logging.INFO, logger="pom.library.bibliomancer"):
        Bibliomancer(Catalog.from_passages([]), Circulation.from_dict({}))
    assert any(r.levelno == logging.CRITICAL and "nothing to draw from" in r.message
               for r in caplog.records)


def test_draws_are_logged_at_debug_when_the_module_flag_is_on(caplog):
    from pom.log_setup import get_logger
    store, circulation = tiles()
    get_logger("pom.library.bibliomancer", True)
    try:
        selector = Bibliomancer(store, circulation, random.Random(1))
        with caplog.at_level(logging.DEBUG):
            selector.draw(3)
    finally:
        get_logger("pom.library.bibliomancer", None)
    assert any(r.levelno == logging.DEBUG and r.message.startswith("drew 3 passage(s):")
               for r in caplog.records)


def test_draws_are_not_logged_when_the_module_flag_is_off(caplog):
    from pom.log_setup import get_logger
    store, circulation = tiles()
    get_logger("pom.library.bibliomancer", False)
    try:
        selector = Bibliomancer(store, circulation, random.Random(1))
        with caplog.at_level(logging.DEBUG):
            selector.draw(3)
    finally:
        get_logger("pom.library.bibliomancer", None)
    assert [r for r in caplog.records if r.levelno == logging.DEBUG] == []


# --- The committed library --------------------------------------------------------------

@pytest.mark.skipif(not (REPO / "library" / "passages" / "circulation.json").exists(),
                    reason="committed passages not present")
def test_the_real_library_builds_cleanly_and_balances_the_shelves():
    passages = REPO / "library" / "passages"
    store = Catalog.load(passages, strict=True)
    circulation = Circulation.from_file(passages / "circulation.json", strict=True)
    selector = Bibliomancer(store, circulation, random.Random(51), strict=True)
    assert selector.report.ok and selector.check_report.ok
    assert sum(selector.shelf_shares().values()) == pytest.approx(1.0)
    targets = selector.shelf_shares()
    shares = shares_of(selector, 6000, count=3)
    assert set(shares) == set(targets)          # every shelf turns up, whatever the shelf count
    for shelf, target in targets.items():
        assert shares[shelf] == pytest.approx(target, abs=0.015), shelf
