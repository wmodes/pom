"""Tests for pom.corpus.weights: parsing, defaults, normalization and validation.

The module follows the project principle that nothing may stop the oracle: in its default
(lenient) mode every bad input recovers and is recorded in a FaultRecorder. In strict mode, used by
tests and dev tools, the same inputs raise. Most bad-input cases are therefore tested both ways.

Everything runs against tiny in-memory documents or temp files, except the last test, a smoke
check of the committed weights file.
"""
import json
import logging
from pathlib import Path

import pytest

from pom.corpus import Weights, WeightsError

REPO = Path(__file__).resolve().parents[2]


def doc(**registers):
    """Builds a weights document: doc(red={"share": 1, "sources": {"a": 0.5}})."""
    return {"registers": registers}


# --- Parsing valid input ---------------------------------------------------------------

def test_reads_shares_and_source_weights():
    weights = Weights.from_dict(doc(news={"share": 2, "sources": {"ads": 0.5}}))
    assert weights.share("news") == 2.0
    assert weights.source_weight("ads") == 0.5
    assert weights.report.ok


def test_unlisted_register_and_source_count_one():
    weights = Weights.from_dict(doc(news={"share": 2, "sources": {"ads": 0.5}}))
    assert weights.share("poetry") == 1.0
    assert weights.source_weight("anything") == 1.0


def test_register_without_share_or_sources_defaults():
    weights = Weights.from_dict(doc(news={}))
    assert weights.share("news") == 1.0 and weights.report.ok


def test_empty_document_is_valid_and_all_defaults():
    weights = Weights.from_dict({})
    assert weights.share("x") == 1.0 and weights.source_weight("y") == 1.0 and weights.report.ok


def test_underscore_keys_are_notes_and_ignored():
    weights = Weights.from_dict({
        "_about": "a note",
        "registers": {"_comment": "ignored", "news": {"_why": "x", "share": 3, "sources": {"_n": 9, "ads": 2}}},
    })
    assert weights.share("news") == 3.0
    assert weights.source_weight("ads") == 2.0
    assert weights.source_weight("_n") == 1.0   # a note, not a weight
    assert weights.report.ok


def test_zero_is_allowed_to_switch_something_off():
    weights = Weights.from_dict(doc(news={"share": 0, "sources": {"ads": 0}}))
    assert weights.share("news") == 0.0 and weights.source_weight("ads") == 0.0
    assert weights.report.ok


def test_from_file_reads_json(tmp_path):
    path = tmp_path / "weights.json"
    path.write_text(json.dumps(doc(news={"share": 4})), encoding="utf-8")
    weights = Weights.from_file(path)
    assert weights.share("news") == 4.0 and weights.report.ok


# --- Recovering from bad input (the default) -------------------------------------------

def test_missing_file_gives_defaults_and_a_warning_not_a_crash(tmp_path):
    weights = Weights.from_file(tmp_path / "nope.json")
    assert weights.share("news") == 1.0
    assert any("cannot read" in w and "1.0" in w for w in weights.report.warnings)


def test_invalid_json_gives_defaults_and_a_warning(tmp_path):
    path = tmp_path / "weights.json"
    path.write_text("{not json", encoding="utf-8")
    weights = Weights.from_file(path)
    assert weights.source_weight("x") == 1.0
    assert any("not valid JSON" in w for w in weights.report.warnings)


@pytest.mark.parametrize("bad", [[], "text", 5, None])
def test_top_level_not_an_object_gives_defaults(bad):
    weights = Weights.from_dict(bad)
    assert weights.share("news") == 1.0
    assert any("JSON object" in w for w in weights.report.warnings)


def test_registers_not_an_object_gives_defaults():
    weights = Weights.from_dict({"registers": []})
    assert weights.share("news") == 1.0
    assert any("'registers' must be an object" in w for w in weights.report.warnings)


def test_register_entry_not_an_object_is_skipped_and_the_rest_survive():
    weights = Weights.from_dict(doc(news=3, poetry={"share": 4}))
    assert weights.share("news") == 1.0           # skipped: default
    assert weights.share("poetry") == 4.0         # the good sibling is untouched
    assert any("register 'news' must be an object" in w for w in weights.report.warnings)


def test_sources_not_an_object_is_skipped_but_the_share_survives():
    weights = Weights.from_dict(doc(news={"share": 3, "sources": ["ads"]}))
    assert weights.share("news") == 3.0 and weights.source_weight("ads") == 1.0
    assert any("'sources' must be an object" in w for w in weights.report.warnings)


def test_typo_in_a_register_key_warns_and_the_valid_keys_still_apply():
    weights = Weights.from_dict(doc(news={"shares": 2, "share": 3, "sources": {"ads": 0.5}}))
    assert weights.share("news") == 3.0 and weights.source_weight("ads") == 0.5
    assert any("unknown key" in w and "shares" in w for w in weights.report.warnings)


@pytest.mark.parametrize("bad", ["2", None, True, False, [1], -1, -0.5, float("nan"), float("inf")])
def test_bad_share_falls_back_to_one_with_a_warning(bad):
    weights = Weights.from_dict(doc(news={"share": bad}))
    assert weights.share("news") == 1.0
    assert any("register 'news' share" in w and "using 1.0" in w for w in weights.report.warnings)


@pytest.mark.parametrize("bad", ["2", None, True, -1, float("nan"), float("inf")])
def test_bad_source_weight_falls_back_to_one_with_a_warning(bad):
    weights = Weights.from_dict(doc(news={"sources": {"ads": bad, "obits": 0.2}}))
    assert weights.source_weight("ads") == 1.0
    assert weights.source_weight("obits") == 0.2   # one bad weight does not spoil the others
    assert any("source 'ads'" in w for w in weights.report.warnings)


def test_a_source_listed_twice_keeps_the_first():
    weights = Weights.from_dict(doc(news={"sources": {"ads": 0.5}}, poetry={"sources": {"ads": 9}}))
    assert weights.source_weight("ads") == 0.5
    assert any("more than one register" in w for w in weights.report.warnings)


def test_many_problems_at_once_never_raise_and_all_are_reported():
    weights = Weights.from_dict(doc(news={"share": "x", "sources": {"a": -1, "b": None}},
                                    poetry=7, cooking={"shares": 1}))
    assert len(weights.report.warnings) >= 4
    assert weights.share("news") == 1.0


def test_problems_are_logged_so_they_reach_journald(caplog):
    with caplog.at_level(logging.WARNING, logger="pom.corpus.weights"):
        Weights.from_dict(doc(news={"share": "oops"}))
    assert any(r.levelno == logging.WARNING and "register 'news' share" in r.message
               for r in caplog.records)


def test_a_clean_file_logs_no_warnings(caplog):
    with caplog.at_level(logging.DEBUG, logger="pom.corpus.weights"):
        Weights.from_dict(doc(news={"share": 2}))
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


@pytest.fixture
def module_debug():
    """Lets a test force this module's debug flag, and restores 'follow the project' after."""
    from pom.log_setup import get_logger
    yield lambda flag: get_logger("pom.corpus.weights", flag)
    get_logger("pom.corpus.weights", None)


def test_debug_lines_appear_only_when_the_module_flag_is_on(caplog, module_debug):
    module_debug(True)
    with caplog.at_level(logging.DEBUG, logger="pom.corpus.weights"):
        Weights.from_dict(doc(news={"share": 2})).shares_for(["news"])
    debug = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("parsed 1 register share" in m for m in debug)
    assert any("normalized shares" in m for m in debug)


def test_debug_lines_are_hidden_when_the_module_flag_is_off(caplog, module_debug):
    module_debug(False)
    with caplog.at_level(logging.DEBUG):   # root only: naming the logger would reset its level
        Weights.from_dict(doc(news={"share": 2}))
    assert [r for r in caplog.records if r.levelno == logging.DEBUG] == []


def test_check_problems_are_logged_too(caplog):
    with caplog.at_level(logging.WARNING, logger="pom.corpus.weights"):
        Weights.from_dict(doc(news={"sources": {"ads": 1}})).check(LAYOUT)
    assert any("not in weights file" in r.message for r in caplog.records)


# --- The same problems in strict mode (development and tests) --------------------------

STRICT_CASES = [
    ("a missing file", None, "cannot read"),
    ("top level not an object", [], "JSON object"),
    ("registers not an object", {"registers": []}, "'registers' must be an object"),
    ("register not an object", doc(news=3), "register 'news' must be an object"),
    ("sources not an object", doc(news={"sources": ["ads"]}), "'sources' must be an object"),
    ("a typo in a key", doc(news={"shares": 2}), "unknown key.*shares"),
    ("a bad share", doc(news={"share": "2"}), "register 'news' share"),
    ("a negative share", doc(news={"share": -1}), "register 'news' share"),
    ("a bad source weight", doc(news={"sources": {"ads": None}}), "source 'ads'"),
    ("a duplicate source", doc(news={"sources": {"ads": 1}}, poetry={"sources": {"ads": 1}}),
     "more than one register"),
]


@pytest.mark.parametrize("label,document,pattern", STRICT_CASES, ids=[c[0] for c in STRICT_CASES])
def test_strict_mode_raises_where_lenient_mode_recovers(tmp_path, label, document, pattern):
    with pytest.raises(WeightsError, match=pattern):
        if document is None:
            Weights.from_file(tmp_path / "nope.json", strict=True)
        else:
            Weights.from_dict(document, strict=True)


def test_strict_invalid_json_raises(tmp_path):
    path = tmp_path / "weights.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(WeightsError, match="not valid JSON"):
        Weights.from_file(path, strict=True)


def test_strict_mode_accepts_a_clean_document():
    assert Weights.from_dict(doc(news={"share": 2, "sources": {"ads": 1}}), strict=True).share("news") == 2.0


# --- Normalized shares -----------------------------------------------------------------

def test_equal_shares_over_three_registers_are_thirds():
    """The tile example: red, green and blue, 1:1:1."""
    weights = Weights.from_dict(doc(red={}, green={}, blue={}))
    shares = weights.shares_for(["red", "green", "blue"])
    assert shares == pytest.approx({"red": 1 / 3, "green": 1 / 3, "blue": 1 / 3})


def test_thumb_on_the_scale_is_normalized():
    """0.8 : 1 : 1 becomes about 0.286, 0.357, 0.357."""
    weights = Weights.from_dict(doc(red={"share": 0.8}, green={"share": 1}, blue={"share": 1}))
    shares = weights.shares_for(["red", "green", "blue"])
    assert shares["red"] == pytest.approx(0.8 / 2.8)
    assert shares["green"] == pytest.approx(1 / 2.8)
    assert sum(shares.values()) == pytest.approx(1.0)


def test_unlisted_register_gets_one_in_the_normalization():
    weights = Weights.from_dict(doc(red={"share": 1}, blue={"share": 1}))
    shares = weights.shares_for(["red", "blue", "green"])   # green unlisted
    assert shares == pytest.approx({"red": 1 / 3, "blue": 1 / 3, "green": 1 / 3})


def test_shares_are_normalized_only_over_registers_given():
    """A listed register that has no chunks must not take probability from the others."""
    weights = Weights.from_dict(doc(red={"share": 5}, blue={"share": 1}))
    assert weights.shares_for(["blue"]) == {"blue": 1.0}


def test_zero_share_register_gets_zero_probability():
    weights = Weights.from_dict(doc(red={"share": 0}, blue={"share": 1}))
    assert weights.shares_for(["red", "blue"]) == pytest.approx({"red": 0.0, "blue": 1.0})


def test_no_registers_gives_an_empty_result_not_an_error():
    assert Weights.from_dict({}).shares_for([]) == {}


def test_all_zero_shares_fall_back_to_equal_with_a_warning():
    weights = Weights.from_dict(doc(red={"share": 0}, blue={"share": 0}))
    shares = weights.shares_for(["red", "blue"])
    assert shares == pytest.approx({"red": 0.5, "blue": 0.5})
    assert any("no register has a positive share" in w for w in weights.report.warnings)


def test_all_zero_shares_raise_in_strict_mode():
    weights = Weights.from_dict(doc(red={"share": 0}, blue={"share": 0}), strict=True)
    with pytest.raises(WeightsError, match="no register has a positive share"):
        weights.shares_for(["red", "blue"])


# --- Checking against the corpus layout ------------------------------------------------

LAYOUT = {"news": ["ads", "obits"], "poetry": ["dickinson"]}


def matching():
    return Weights.from_dict(doc(news={"sources": {"ads": 1, "obits": 0.2}},
                                 poetry={"sources": {"dickinson": 1}}))


def test_matching_layout_has_nothing_to_report():
    report = matching().check(LAYOUT)
    assert report.ok and report.warnings == []


def test_unlisted_register_is_reported():
    report = Weights.from_dict(doc(news={"sources": {"ads": 1, "obits": 1}})).check(LAYOUT)
    assert any("register" in w and "poetry" in w and "1.0" in w for w in report.warnings)


def test_unlisted_source_is_reported():
    report = Weights.from_dict(doc(news={"sources": {"ads": 1}}, poetry={"sources": {"dickinson": 1}})).check(LAYOUT)
    assert any("source" in w and "obits" in w for w in report.warnings)


def test_listed_source_with_no_chunks_is_reported_not_fatal():
    weights = Weights.from_dict(doc(news={"sources": {"ads": 1, "obits": 1, "ghost": 1}}))
    report = weights.check(LAYOUT)
    assert any("no chunks" in w and "ghost" in w for w in report.warnings)


def test_listed_register_with_no_chunks_is_reported_not_fatal():
    report = Weights.from_dict(doc(news={}, poetry={}, cooking={})).check(LAYOUT)
    assert any("no chunks" in w and "cooking" in w for w in report.warnings)


def test_register_with_an_empty_source_list_in_the_layout_counts_as_no_chunks():
    report = Weights.from_dict(doc(news={})).check({"news": []})
    assert any("no chunks" in w and "news" in w for w in report.warnings)


def test_source_listed_under_the_wrong_register_is_reported():
    """The Sun Tzu case: the id says one thing, the folder says another."""
    weights = Weights.from_dict(doc(news={"sources": {"ads": 1, "obits": 1, "dickinson": 1}}, poetry={}))
    report = weights.check(LAYOUT)
    assert any("dickinson" in w and "listed under 'news'" in w and "'poetry'" in w
               for w in report.warnings)


def test_check_returns_its_own_report_and_leaves_the_loading_report_alone():
    weights = Weights.from_dict(doc(news={"sources": {"ads": 1}}))
    before = len(weights.report)
    report = weights.check(LAYOUT)
    assert len(report) > 0 and len(weights.report) == before


def test_check_does_not_print(capsys):
    Weights.from_dict(doc(news={"sources": {"ads": 1}})).check(LAYOUT)
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


STRICT_CHECKS = [
    ("an unlisted register", doc(news={"sources": {"ads": 1, "obits": 1}}), "poetry"),
    ("an unlisted source", doc(news={"sources": {"ads": 1}}, poetry={"sources": {"dickinson": 1}}), "obits"),
    ("a listed source with no chunks", doc(news={"sources": {"ads": 1, "obits": 1, "ghost": 1}}), "ghost"),
    ("a listed register with no chunks", doc(news={}, poetry={}, cooking={}), "cooking"),
    ("a source under the wrong register",
     doc(news={"sources": {"ads": 1, "obits": 1, "dickinson": 1}}, poetry={}), "dickinson"),
]


@pytest.mark.parametrize("label,document,pattern", STRICT_CHECKS, ids=[c[0] for c in STRICT_CHECKS])
def test_strict_check_raises_for_any_drift(label, document, pattern):
    with pytest.raises(WeightsError, match=pattern):
        Weights.from_dict(document, strict=True).check(LAYOUT)


def test_strict_check_accepts_a_matching_layout():
    assert Weights.from_dict(matching_document(), strict=True).check(LAYOUT).ok


def matching_document():
    return doc(news={"sources": {"ads": 1, "obits": 0.2}}, poetry={"sources": {"dickinson": 1}})


# --- The committed file ----------------------------------------------------------------

@pytest.mark.skipif(not (REPO / "corpus" / "chunks" / "weights.json").exists(),
                    reason="corpus/chunks/weights.json not present")
def test_real_weights_file_is_clean_even_in_strict_mode():
    chunks = REPO / "corpus" / "chunks"
    layout = {folder.name: [f.stem for f in sorted(folder.glob("*.jsonl"))]
              for folder in sorted(chunks.iterdir()) if folder.is_dir()}
    weights = Weights.from_file(chunks / "weights.json", strict=True)   # raises on any typo
    assert weights.check(layout).ok
    shares = weights.shares_for(layout)
    assert sum(shares.values()) == pytest.approx(1.0)
    assert set(shares) == set(layout)
