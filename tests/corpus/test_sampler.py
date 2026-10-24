"""Tests for pom.corpus.sampler: weighted random draws that balance registers over time.

Statistical tests use a seeded random generator, so they are deterministic: a pass is a pass every
time. Tolerances are generous (several standard deviations) so they check the odds, not the luck.

The module follows the project principle that nothing may stop the oracle: by default problems are
recorded in a FaultRecorder and the Sampler returns what it can. In strict mode the same problems raise.
"""
import collections
import logging
import random
from pathlib import Path

import pytest

from pom.corpus import Chunk, ChunkStore, Sampler, SamplerError, Weights

REPO = Path(__file__).resolve().parents[2]


def make_store(layout):
    """Builds a store: layout is {register: {source: how_many_chunks}}."""
    chunks = []
    for register, sources in layout.items():
        for source, n in sources.items():
            for i in range(n):
                chunks.append(Chunk(f"{source}#{i:05d}", source, register, f"{source} number {i}", 3))
    return ChunkStore.from_chunks(chunks)


def weights_for(**registers):
    """weights_for(red={"share": 1, "sources": {"a": 0.5}}, ...)"""
    return Weights.from_dict({"registers": registers})


def tiles(**extra):
    """The tile example: red 40, blue 20, green 10 tiles, equal shares."""
    store = make_store({"red": {"r": 40}, "blue": {"b": 20}, "green": {"g": 10}})
    return store, weights_for(red={}, blue={}, green={}, **extra)


def shares_of(sampler, draws, count=1):
    """The fraction of drawn chunks that came from each register over many pulls."""
    seen = collections.Counter()
    for _ in range(draws):
        for chunk in sampler.draw(count):
            seen[chunk.register] += 1
    total = sum(seen.values())
    return {register: n / total for register, n in seen.items()}


# --- The odds --------------------------------------------------------------------------

def test_the_tile_example_gives_each_tile_the_right_chance():
    store, weights = tiles()
    sampler = Sampler(store, weights, random.Random(1))
    red, blue, green = (store.chunks(r)[0] for r in ("red", "blue", "green"))
    assert sampler.probability(red) == pytest.approx((1 / 3) / 40)
    assert sampler.probability(blue) == pytest.approx((1 / 3) / 20)
    assert sampler.probability(green) == pytest.approx((1 / 3) / 10)
    assert sum(sampler.probability(c) for c in store) == pytest.approx(1.0)


def test_register_shares_are_normalized():
    store, weights = tiles()
    shares = Sampler(store, weights).register_shares()
    assert shares == pytest.approx({"red": 1 / 3, "blue": 1 / 3, "green": 1 / 3})


def test_a_thumb_on_the_scale_changes_the_shares():
    store = make_store({"red": {"r": 40}, "blue": {"b": 20}, "green": {"g": 10}})
    weights = weights_for(red={"share": 0.8}, blue={"share": 1}, green={"share": 1})
    shares = Sampler(store, weights).register_shares()
    assert shares["red"] == pytest.approx(0.8 / 2.8)
    assert shares["blue"] == pytest.approx(1 / 2.8)


def test_source_weights_shape_the_odds_within_a_register_only():
    store = make_store({"news": {"ads": 10, "obits": 10}, "poetry": {"p": 10}})
    weights = weights_for(news={"sources": {"ads": 1, "obits": 0.5}}, poetry={})
    sampler = Sampler(store, weights)
    ads, obits = store.chunks("news", "ads")[0], store.chunks("news", "obits")[0]
    assert sampler.probability(ads) == pytest.approx(2 * sampler.probability(obits))
    news_total = sum(sampler.probability(c) for c in store.chunks("news"))
    assert news_total == pytest.approx(0.5)            # the register still gets its full share


def test_an_unlisted_register_counts_one():
    store = make_store({"red": {"r": 5}, "blue": {"b": 5}})
    shares = Sampler(store, weights_for(red={"share": 3})).register_shares()   # blue unlisted
    assert shares == pytest.approx({"red": 0.75, "blue": 0.25})


def test_probability_of_an_unknown_chunk_is_zero():
    store, weights = tiles()
    assert Sampler(store, weights).probability(Chunk("x#0", "x", "red", "Not here", 2)) == 0.0


def test_a_zero_weight_source_has_no_chance():
    store = make_store({"news": {"ads": 5, "obits": 5}})
    sampler = Sampler(store, weights_for(news={"sources": {"obits": 0}}))
    assert sampler.probability(store.chunks("news", "obits")[0]) == 0.0
    assert sampler.probability(store.chunks("news", "ads")[0]) == pytest.approx(1 / 5)


def test_a_zero_share_register_has_no_chance():
    store = make_store({"red": {"r": 5}, "blue": {"b": 5}})
    sampler = Sampler(store, weights_for(red={"share": 0}, blue={}))
    assert sampler.probability(store.chunks("red")[0]) == 0.0
    assert sampler.register_shares()["blue"] == pytest.approx(1.0)


def test_a_register_whose_sources_all_weigh_zero_is_skipped_with_a_warning():
    store = make_store({"red": {"r": 5}, "blue": {"b": 5}})
    sampler = Sampler(store, weights_for(red={"sources": {"r": 0}}, blue={}))
    assert sampler.ready
    assert sampler.register_shares() == pytest.approx({"blue": 1.0})
    assert any("red" in w and "no drawable chunks" in w for w in sampler.report.warnings)


# --- Nothing to draw -------------------------------------------------------------------

def test_an_empty_store_is_critical_and_draws_nothing_without_raising():
    sampler = Sampler(ChunkStore.from_chunks([]), Weights.from_dict({}))
    assert not sampler.ready and sampler.report.has_critical
    assert any("nothing to draw from" in m for m in sampler.report.criticals)
    assert sampler.draw(5) == [] and sampler.draw() == []


def test_when_every_weight_is_zero_it_is_critical_and_draws_nothing():
    store = make_store({"red": {"r": 5}})
    sampler = Sampler(store, weights_for(red={"sources": {"r": 0}}))
    assert not sampler.ready and sampler.report.has_critical
    assert any("no chunk has a positive weight" in m for m in sampler.report.criticals)
    assert any("no drawable chunks" in w for w in sampler.report.warnings)
    assert sampler.draw(3) == []


def test_when_every_share_is_zero_the_registers_count_equally_instead():
    """Weights rescues an all-zero share set, so there is still something to say."""
    store = make_store({"red": {"r": 5}, "blue": {"b": 5}})
    sampler = Sampler(store, weights_for(red={"share": 0}, blue={"share": 0}))
    assert sampler.ready
    assert sampler.register_shares() == pytest.approx({"red": 0.5, "blue": 0.5})


# --- Drawing ---------------------------------------------------------------------------

def test_draw_returns_the_requested_number_of_distinct_chunks():
    store, weights = tiles()
    chunks = Sampler(store, weights, random.Random(2)).draw(5)
    assert len(chunks) == 5 and len({c.id for c in chunks}) == 5
    assert all(isinstance(c, Chunk) for c in chunks)


@pytest.mark.parametrize("count", [0, -3])
def test_zero_or_negative_counts_draw_nothing(count):
    store, weights = tiles()
    assert Sampler(store, weights).draw(count) == []


def test_asking_for_more_than_exist_returns_all_of_them():
    store = make_store({"red": {"r": 3}})
    chunks = Sampler(store, weights_for(red={}), random.Random(3)).draw(10)
    assert sorted(c.id for c in chunks) == ["r#00000", "r#00001", "r#00002"]


def test_a_pull_never_repeats_a_chunk():
    store = make_store({"red": {"r": 3}, "blue": {"b": 3}})
    sampler = Sampler(store, weights_for(red={}, blue={}), random.Random(4))
    for _ in range(1000):
        ids = [c.id for c in sampler.draw(5)]
        assert len(ids) == len(set(ids)) == 5


def test_skewed_odds_still_complete_a_pull_without_repeats():
    """With one chunk at ~98% a plain redraw would rarely finish; the exact fallback must."""
    chunks = [Chunk("big#0", "big", "red", "Big one", 2), Chunk("a#0", "a", "red", "Small a", 2),
              Chunk("b#0", "b", "red", "Small b", 2)]
    store = ChunkStore.from_chunks(chunks)
    weights = weights_for(red={"sources": {"big": 98, "a": 1, "b": 1}})
    for seed in range(50):
        pulled = Sampler(store, weights, random.Random(seed)).draw(3)
        assert sorted(c.id for c in pulled) == ["a#0", "b#0", "big#0"]


def test_zero_chance_chunks_are_never_returned_even_when_more_are_asked_for():
    """Ask for ten when only five can be drawn: the zero-weight chunks must not fill the gap."""
    store = make_store({"news": {"ads": 5, "off": 5}})
    sampler = Sampler(store, weights_for(news={"sources": {"off": 0}}), random.Random(6))
    pulled = sampler.draw(10)
    assert len(pulled) == 5 and {c.source for c in pulled} == {"ads"}


def test_the_exact_fallback_still_follows_the_odds():
    """Skewed odds make the fallback path common; it must stay proportional, not just complete."""
    chunks = [Chunk("big#0", "big", "red", "Big one", 2), Chunk("a#0", "a", "red", "Small a", 2),
              Chunk("b#0", "b", "red", "Small b", 2)]
    store = ChunkStore.from_chunks(chunks)
    weights = weights_for(red={"sources": {"big": 98, "a": 1, "b": 4}})
    sampler = Sampler(store, weights, random.Random(9))
    pulls = 4000
    with_b = sum(any(c.source == "b" for c in sampler.draw(2)) for _ in range(pulls))
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
        sampler = Sampler(store, weights_for(**{name: {} for name in layout}), Top())
        assert len(sampler.draw(1)) == 1          # no IndexError


def test_a_generator_that_returns_exactly_one_still_cannot_pick_past_the_end():
    """random() should stay below 1, but rounding can land a pick exactly on the total; the table
    must clamp to its last chunk instead of raising IndexError."""
    class One(random.Random):
        def random(self):
            return 1.0

    store, weights = tiles()
    pulled = Sampler(store, weights, One()).draw(1)
    assert len(pulled) == 1


def test_the_same_seed_gives_the_same_draws():
    store, weights = tiles()
    one = [c.id for c in Sampler(store, weights, random.Random(7)).draw(5)]
    two = [c.id for c in Sampler(store, weights, random.Random(7)).draw(5)]
    other = [c.id for c in Sampler(store, weights, random.Random(8)).draw(5)]
    assert one == two and one != other


def test_without_an_injected_generator_it_still_draws():
    store, weights = tiles()
    assert len(Sampler(store, weights).draw(3)) == 3


def test_the_injected_generator_is_the_one_used():
    store, weights = tiles()

    class Fixed(random.Random):
        calls = 0

        def random(self):
            Fixed.calls += 1
            return super().random()

    Sampler(store, weights, Fixed(1)).draw(5)
    assert Fixed.calls >= 5


# --- Any combination is possible, and the long run matches the targets ------------------

def test_five_from_one_register_is_possible_and_so_is_a_mix():
    store = make_store({"red": {"r": 8}, "blue": {"b": 8}})
    sampler = Sampler(store, weights_for(red={}, blue={}), random.Random(11))
    spreads = collections.Counter()
    for _ in range(4000):
        pull = sampler.draw(5)
        spreads[len({c.register for c in pull})] += 1
    assert spreads[1] > 0, "an all-one-register pull never happened"
    assert spreads[2] > 0, "a mixed pull never happened"


def test_over_many_draws_each_register_gets_its_target_share():
    """The tile example: 40, 20 and 10 tiles, equal shares, so a third each despite the sizes."""
    store, weights = tiles()
    shares = shares_of(Sampler(store, weights, random.Random(21)), 30000)
    for register in ("red", "blue", "green"):
        assert shares[register] == pytest.approx(1 / 3, abs=0.02)


def test_over_many_draws_a_thumb_on_the_scale_shows_up():
    store = make_store({"red": {"r": 40}, "blue": {"b": 20}, "green": {"g": 10}})
    weights = weights_for(red={"share": 0.8}, blue={"share": 1}, green={"share": 1})
    shares = shares_of(Sampler(store, weights, random.Random(22)), 30000)
    assert shares["red"] == pytest.approx(0.8 / 2.8, abs=0.02)
    assert shares["blue"] == pytest.approx(1 / 2.8, abs=0.02)


def test_drawing_several_at_a_time_keeps_the_same_long_run_shares():
    store, weights = tiles()
    shares = shares_of(Sampler(store, weights, random.Random(23)), 6000, count=5)
    for register in ("red", "blue", "green"):
        assert shares[register] == pytest.approx(1 / 3, abs=0.03)


def test_within_a_register_every_chunk_is_about_equally_likely():
    store = make_store({"red": {"r": 20}, "blue": {"b": 5}})
    sampler = Sampler(store, weights_for(red={}, blue={}), random.Random(24))
    counts = collections.Counter(c.id for _ in range(20000) for c in sampler.draw(1))
    expected = 20000 * 0.5 / 20
    for chunk in store.chunks("red"):
        assert counts[chunk.id] == pytest.approx(expected, rel=0.25)


def test_source_weights_show_up_in_the_long_run():
    store = make_store({"news": {"ads": 10, "obits": 10}})
    sampler = Sampler(store, weights_for(news={"sources": {"obits": 0.5}}), random.Random(25))
    seen = collections.Counter(c.source for _ in range(20000) for c in sampler.draw(1))
    assert seen["ads"] / seen["obits"] == pytest.approx(2.0, rel=0.1)


def test_chunks_with_no_chance_are_never_drawn():
    store = make_store({"red": {"r": 5}, "blue": {"b": 5}, "off": {"o": 5}})
    weights = weights_for(red={"sources": {"r": 0}}, blue={}, off={"share": 0})
    sampler = Sampler(store, weights, random.Random(26))
    seen = {c.register for _ in range(5000) for c in sampler.draw(3)}
    assert seen == {"blue"}


# --- Counting draws --------------------------------------------------------------------

def test_draw_counts_track_which_registers_supplied_chunks():
    store = make_store({"red": {"r": 5}, "blue": {"b": 5}})
    sampler = Sampler(store, weights_for(red={}, blue={}), random.Random(31))
    assert sampler.draw_counts() == {"red": 0, "blue": 0}
    chunks = sampler.draw(4)
    counts = sampler.draw_counts()
    assert sum(counts.values()) == 4
    assert counts["red"] == sum(1 for c in chunks if c.register == "red")


def test_reset_counts_returns_them_to_zero():
    store, weights = tiles()
    sampler = Sampler(store, weights, random.Random(32))
    sampler.draw(5)
    sampler.reset_counts()
    assert set(sampler.draw_counts().values()) == {0}


def test_draw_counts_are_a_copy():
    store, weights = tiles()
    sampler = Sampler(store, weights)
    sampler.draw_counts()["red"] = 99
    assert sampler.draw_counts()["red"] == 0


# --- How many chunks a pull holds ------------------------------------------------------

def test_the_default_pull_is_three_to_five_chunks():
    store, weights = tiles()
    sampler = Sampler(store, weights, random.Random(41))
    assert sampler.sample_range == (3, 5)
    sizes = collections.Counter(len(sampler.draw()) for _ in range(3000))
    assert set(sizes) == {3, 4, 5}          # all three sizes occur, and nothing outside the range


def test_a_custom_range_is_used():
    store, weights = tiles()
    sampler = Sampler(store, weights, random.Random(42), samples=(2, 2))
    assert {len(sampler.draw()) for _ in range(50)} == {2}


def test_an_explicit_count_overrides_the_range():
    store, weights = tiles()
    assert len(Sampler(store, weights, random.Random(43), samples=(2, 2)).draw(4)) == 4


def test_a_reversed_range_is_swapped_with_a_warning():
    store, weights = tiles()
    sampler = Sampler(store, weights, samples=(5, 3))
    assert sampler.sample_range == (3, 5)
    assert any("backwards" in w for w in sampler.report.warnings)


@pytest.mark.parametrize("bad", [(0, 3), (-1, 5), (3, 0), ("3", "5"), (None, 5), (3,), 5, None,
                                 (True, 5), (2.5, 4), (3, 4, 5)])
def test_a_bad_range_falls_back_to_the_default_with_a_warning(bad):
    store, weights = tiles()
    sampler = Sampler(store, weights, samples=bad)
    assert sampler.sample_range == (3, 5)
    assert any("samples must be" in w for w in sampler.report.warnings)


def test_the_range_choice_is_repeatable_with_a_seed():
    store, weights = tiles()
    a = [len(Sampler(store, weights, random.Random(5)).draw()) for _ in range(3)]
    b = [len(Sampler(store, weights, random.Random(5)).draw()) for _ in range(3)]
    assert a == b


# --- Strict mode (development and tests) -----------------------------------------------

def test_strict_raises_for_an_empty_store():
    with pytest.raises(SamplerError, match="nothing to draw from"):
        Sampler(ChunkStore.from_chunks([]), Weights.from_dict({}), strict=True)


def test_strict_raises_when_every_weight_is_zero():
    """Strict raises on the first problem found, here the register with nothing drawable."""
    store = make_store({"red": {"r": 5}})
    with pytest.raises(SamplerError, match="no drawable chunks"):
        Sampler(store, weights_for(red={"sources": {"r": 0}}), strict=True)


def test_strict_raises_for_a_register_with_nothing_drawable():
    store = make_store({"red": {"r": 5}, "blue": {"b": 5}})
    with pytest.raises(SamplerError, match="no drawable chunks"):
        Sampler(store, weights_for(red={"sources": {"r": 0}}, blue={}), strict=True)


def test_strict_raises_for_a_bad_range():
    store, weights = tiles()
    with pytest.raises(SamplerError, match="samples must be"):
        Sampler(store, weights, samples=(0, 3), strict=True)
    with pytest.raises(SamplerError, match="backwards"):
        Sampler(store, weights, samples=(5, 3), strict=True)


def test_strict_accepts_a_clean_setup():
    store, weights = tiles()
    assert Sampler(store, weights, random.Random(1), strict=True).ready


# --- Layout drift, logging -------------------------------------------------------------

def test_layout_drift_is_reported_but_does_not_stop_the_sampler():
    store = make_store({"red": {"r": 5}})
    weights = weights_for(red={"sources": {"ghost": 2}}, cooking={})
    sampler = Sampler(store, weights, random.Random(1))
    assert sampler.ready
    assert any("ghost" in w for w in sampler.check_report.warnings)
    assert any("cooking" in w for w in sampler.check_report.warnings)


def test_building_logs_a_summary_and_problems(caplog):
    store = make_store({"red": {"r": 5}, "blue": {"b": 5}})
    with caplog.at_level(logging.INFO, logger="pom.corpus.sampler"):
        Sampler(store, weights_for(red={"sources": {"r": 0}}, blue={}))
    messages = [r.message for r in caplog.records if r.name == "pom.corpus.sampler"]
    assert any("no drawable chunks" in m for m in messages)
    assert any("sampler ready: 5 drawable chunk(s) in 1 register(s)" in m for m in messages)


def test_an_empty_store_logs_a_critical_line(caplog):
    with caplog.at_level(logging.INFO, logger="pom.corpus.sampler"):
        Sampler(ChunkStore.from_chunks([]), Weights.from_dict({}))
    assert any(r.levelno == logging.CRITICAL and "nothing to draw from" in r.message
               for r in caplog.records)


def test_draws_are_logged_at_debug_when_the_module_flag_is_on(caplog):
    from pom.logs import get_logger
    store, weights = tiles()
    get_logger("pom.corpus.sampler", True)
    try:
        sampler = Sampler(store, weights, random.Random(1))
        with caplog.at_level(logging.DEBUG):
            sampler.draw(3)
    finally:
        get_logger("pom.corpus.sampler", None)
    assert any(r.levelno == logging.DEBUG and r.message.startswith("drew 3 chunk(s):")
               for r in caplog.records)


def test_draws_are_not_logged_when_the_module_flag_is_off(caplog):
    from pom.logs import get_logger
    store, weights = tiles()
    get_logger("pom.corpus.sampler", False)
    try:
        sampler = Sampler(store, weights, random.Random(1))
        with caplog.at_level(logging.DEBUG):
            sampler.draw(3)
    finally:
        get_logger("pom.corpus.sampler", None)
    assert [r for r in caplog.records if r.levelno == logging.DEBUG] == []


# --- The committed corpus --------------------------------------------------------------

@pytest.mark.skipif(not (REPO / "corpus" / "chunks" / "weights.json").exists(),
                    reason="committed chunks not present")
def test_the_real_corpus_builds_cleanly_and_balances_the_registers():
    chunks = REPO / "corpus" / "chunks"
    store = ChunkStore.load(chunks, strict=True)
    weights = Weights.from_file(chunks / "weights.json", strict=True)
    sampler = Sampler(store, weights, random.Random(51), strict=True)
    assert sampler.report.ok and sampler.check_report.ok
    assert sum(sampler.register_shares().values()) == pytest.approx(1.0)
    targets = sampler.register_shares()
    shares = shares_of(sampler, 6000, count=3)
    assert set(shares) == set(targets)          # every register turns up, whatever the register count
    for register, target in targets.items():
        assert shares[register] == pytest.approx(target, abs=0.015), register
