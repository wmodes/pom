"""Tests for pom.library.circulation: parsing, defaults, normalization and validation.

The module follows the project principle that nothing may stop the oracle: in its default
(lenient) mode every bad input recovers and is recorded in a FaultRecorder. In strict mode, used by
tests and dev tools, the same inputs raise. Most bad-input cases are therefore tested both ways.

Everything runs against tiny in-memory documents or temp files, except the last test, a smoke
check of the committed circulation file.
"""
import json
import logging
from pathlib import Path

import pytest

from pom.library import Circulation, CirculationError

REPO = Path(__file__).resolve().parents[2]


def doc(**shelves):
    """Builds a circulation document: doc(red={"share": 1, "holdings": {"a": 0.5}})."""
    return {"shelves": shelves}


# --- Parsing valid input ---------------------------------------------------------------

def test_reads_shares_and_holding_weights():
    circulation = Circulation.from_dict(doc(news={"share": 2, "holdings": {"ads": 0.5}}))
    assert circulation.share("news") == 2.0
    assert circulation.holding_weight("ads") == 0.5
    assert circulation.report.ok


def test_unlisted_shelf_and_holding_count_one():
    circulation = Circulation.from_dict(doc(news={"share": 2, "holdings": {"ads": 0.5}}))
    assert circulation.share("poetry") == 1.0
    assert circulation.holding_weight("anything") == 1.0


def test_shelf_without_share_or_holdings_defaults():
    circulation = Circulation.from_dict(doc(news={}))
    assert circulation.share("news") == 1.0 and circulation.report.ok


def test_empty_document_is_valid_and_all_defaults():
    circulation = Circulation.from_dict({})
    assert circulation.share("x") == 1.0 and circulation.holding_weight("y") == 1.0 and circulation.report.ok


def test_underscore_keys_are_notes_and_ignored():
    circulation = Circulation.from_dict({
        "_about": "a note",
        "shelves": {"_comment": "ignored", "news": {"_why": "x", "share": 3, "holdings": {"_n": 9, "ads": 2}}},
    })
    assert circulation.share("news") == 3.0
    assert circulation.holding_weight("ads") == 2.0
    assert circulation.holding_weight("_n") == 1.0   # a note, not a weight
    assert circulation.report.ok


def test_zero_is_allowed_to_switch_something_off():
    circulation = Circulation.from_dict(doc(news={"share": 0, "holdings": {"ads": 0}}))
    assert circulation.share("news") == 0.0 and circulation.holding_weight("ads") == 0.0
    assert circulation.report.ok


def test_from_file_reads_json(tmp_path):
    path = tmp_path / "circulation.json"
    path.write_text(json.dumps(doc(news={"share": 4})), encoding="utf-8")
    circulation = Circulation.from_file(path)
    assert circulation.share("news") == 4.0 and circulation.report.ok


# --- Recovering from bad input (the default) -------------------------------------------

def test_missing_file_gives_defaults_and_a_warning_not_a_crash(tmp_path):
    circulation = Circulation.from_file(tmp_path / "nope.json")
    assert circulation.share("news") == 1.0
    assert any("cannot read" in w and "1.0" in w for w in circulation.report.warnings)


def test_invalid_json_gives_defaults_and_a_warning(tmp_path):
    path = tmp_path / "circulation.json"
    path.write_text("{not json", encoding="utf-8")
    circulation = Circulation.from_file(path)
    assert circulation.holding_weight("x") == 1.0
    assert any("not valid JSON" in w for w in circulation.report.warnings)


@pytest.mark.parametrize("bad", [[], "text", 5, None])
def test_top_level_not_an_object_gives_defaults(bad):
    circulation = Circulation.from_dict(bad)
    assert circulation.share("news") == 1.0
    assert any("JSON object" in w for w in circulation.report.warnings)


def test_shelves_not_an_object_gives_defaults():
    circulation = Circulation.from_dict({"shelves": []})
    assert circulation.share("news") == 1.0
    assert any("'shelves' must be an object" in w for w in circulation.report.warnings)


def test_shelf_entry_not_an_object_is_skipped_and_the_rest_survive():
    circulation = Circulation.from_dict(doc(news=3, poetry={"share": 4}))
    assert circulation.share("news") == 1.0           # skipped: default
    assert circulation.share("poetry") == 4.0         # the good sibling is untouched
    assert any("shelf 'news' must be an object" in w for w in circulation.report.warnings)


def test_holdings_not_an_object_is_skipped_but_the_share_survives():
    circulation = Circulation.from_dict(doc(news={"share": 3, "holdings": ["ads"]}))
    assert circulation.share("news") == 3.0 and circulation.holding_weight("ads") == 1.0
    assert any("'holdings' must be an object" in w for w in circulation.report.warnings)


def test_typo_in_a_shelf_key_warns_and_the_valid_keys_still_apply():
    circulation = Circulation.from_dict(doc(news={"shares": 2, "share": 3, "holdings": {"ads": 0.5}}))
    assert circulation.share("news") == 3.0 and circulation.holding_weight("ads") == 0.5
    assert any("unknown key" in w and "shares" in w for w in circulation.report.warnings)


@pytest.mark.parametrize("bad", ["2", None, True, False, [1], -1, -0.5, float("nan"), float("inf")])
def test_bad_share_falls_back_to_one_with_a_warning(bad):
    circulation = Circulation.from_dict(doc(news={"share": bad}))
    assert circulation.share("news") == 1.0
    assert any("shelf 'news' share" in w and "using 1.0" in w for w in circulation.report.warnings)


@pytest.mark.parametrize("bad", ["2", None, True, -1, float("nan"), float("inf")])
def test_bad_holding_weight_falls_back_to_one_with_a_warning(bad):
    circulation = Circulation.from_dict(doc(news={"holdings": {"ads": bad, "obits": 0.2}}))
    assert circulation.holding_weight("ads") == 1.0
    assert circulation.holding_weight("obits") == 0.2   # one bad weight does not spoil the others
    assert any("holding 'ads'" in w for w in circulation.report.warnings)


def test_a_holding_listed_twice_keeps_the_first():
    circulation = Circulation.from_dict(doc(news={"holdings": {"ads": 0.5}}, poetry={"holdings": {"ads": 9}}))
    assert circulation.holding_weight("ads") == 0.5
    assert any("more than one shelf" in w for w in circulation.report.warnings)


def test_many_problems_at_once_never_raise_and_all_are_reported():
    circulation = Circulation.from_dict(doc(news={"share": "x", "holdings": {"a": -1, "b": None}},
                                    poetry=7, cooking={"shares": 1}))
    assert len(circulation.report.warnings) >= 4
    assert circulation.share("news") == 1.0


def test_problems_are_logged_so_they_reach_journald(caplog):
    with caplog.at_level(logging.WARNING, logger="pom.library.circulation"):
        Circulation.from_dict(doc(news={"share": "oops"}))
    assert any(r.levelno == logging.WARNING and "shelf 'news' share" in r.message
               for r in caplog.records)


def test_a_clean_file_logs_no_warnings(caplog):
    with caplog.at_level(logging.DEBUG, logger="pom.library.circulation"):
        Circulation.from_dict(doc(news={"share": 2}))
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


@pytest.fixture
def module_debug():
    """Lets a test force this module's debug flag, and restores 'follow the project' after."""
    from pom.log_setup import get_logger
    yield lambda flag: get_logger("pom.library.circulation", flag)
    get_logger("pom.library.circulation", None)


def test_debug_lines_appear_only_when_the_module_flag_is_on(caplog, module_debug):
    module_debug(True)
    with caplog.at_level(logging.DEBUG, logger="pom.library.circulation"):
        Circulation.from_dict(doc(news={"share": 2})).shares_for(["news"])
    debug = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("parsed 1 shelf share" in m for m in debug)
    assert any("normalized shares" in m for m in debug)


def test_debug_lines_are_hidden_when_the_module_flag_is_off(caplog, module_debug):
    module_debug(False)
    with caplog.at_level(logging.DEBUG):   # root only: naming the logger would reset its level
        Circulation.from_dict(doc(news={"share": 2}))
    assert [r for r in caplog.records if r.levelno == logging.DEBUG] == []


def test_check_problems_are_logged_too(caplog):
    with caplog.at_level(logging.WARNING, logger="pom.library.circulation"):
        Circulation.from_dict(doc(news={"holdings": {"ads": 1}})).check(LAYOUT)
    assert any("not in circulation file" in r.message for r in caplog.records)


# --- The same problems in strict mode (development and tests) --------------------------

STRICT_CASES = [
    ("a missing file", None, "cannot read"),
    ("top level not an object", [], "JSON object"),
    ("shelves not an object", {"shelves": []}, "'shelves' must be an object"),
    ("shelf not an object", doc(news=3), "shelf 'news' must be an object"),
    ("holdings not an object", doc(news={"holdings": ["ads"]}), "'holdings' must be an object"),
    ("a typo in a key", doc(news={"shares": 2}), "unknown key.*shares"),
    ("a bad share", doc(news={"share": "2"}), "shelf 'news' share"),
    ("a negative share", doc(news={"share": -1}), "shelf 'news' share"),
    ("a bad holding weight", doc(news={"holdings": {"ads": None}}), "holding 'ads'"),
    ("a duplicate holding", doc(news={"holdings": {"ads": 1}}, poetry={"holdings": {"ads": 1}}),
     "more than one shelf"),
]


@pytest.mark.parametrize("label,document,pattern", STRICT_CASES, ids=[c[0] for c in STRICT_CASES])
def test_strict_mode_raises_where_lenient_mode_recovers(tmp_path, label, document, pattern):
    with pytest.raises(CirculationError, match=pattern):
        if document is None:
            Circulation.from_file(tmp_path / "nope.json", strict=True)
        else:
            Circulation.from_dict(document, strict=True)


def test_strict_invalid_json_raises(tmp_path):
    path = tmp_path / "circulation.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(CirculationError, match="not valid JSON"):
        Circulation.from_file(path, strict=True)


def test_strict_mode_accepts_a_clean_document():
    assert Circulation.from_dict(doc(news={"share": 2, "holdings": {"ads": 1}}), strict=True).share("news") == 2.0


# --- Normalized shares -----------------------------------------------------------------

def test_equal_shares_over_three_shelves_are_thirds():
    """The tile example: red, green and blue, 1:1:1."""
    circulation = Circulation.from_dict(doc(red={}, green={}, blue={}))
    shares = circulation.shares_for(["red", "green", "blue"])
    assert shares == pytest.approx({"red": 1 / 3, "green": 1 / 3, "blue": 1 / 3})


def test_thumb_on_the_scale_is_normalized():
    """0.8 : 1 : 1 becomes about 0.286, 0.357, 0.357."""
    circulation = Circulation.from_dict(doc(red={"share": 0.8}, green={"share": 1}, blue={"share": 1}))
    shares = circulation.shares_for(["red", "green", "blue"])
    assert shares["red"] == pytest.approx(0.8 / 2.8)
    assert shares["green"] == pytest.approx(1 / 2.8)
    assert sum(shares.values()) == pytest.approx(1.0)


def test_unlisted_shelf_gets_one_in_the_normalization():
    circulation = Circulation.from_dict(doc(red={"share": 1}, blue={"share": 1}))
    shares = circulation.shares_for(["red", "blue", "green"])   # green unlisted
    assert shares == pytest.approx({"red": 1 / 3, "blue": 1 / 3, "green": 1 / 3})


def test_shares_are_normalized_only_over_shelves_given():
    """A listed shelf that has no passages must not take probability from the others."""
    circulation = Circulation.from_dict(doc(red={"share": 5}, blue={"share": 1}))
    assert circulation.shares_for(["blue"]) == {"blue": 1.0}


def test_zero_share_shelf_gets_zero_probability():
    circulation = Circulation.from_dict(doc(red={"share": 0}, blue={"share": 1}))
    assert circulation.shares_for(["red", "blue"]) == pytest.approx({"red": 0.0, "blue": 1.0})


def test_no_shelves_gives_an_empty_result_not_an_error():
    assert Circulation.from_dict({}).shares_for([]) == {}


def test_all_zero_shares_fall_back_to_equal_with_a_warning():
    circulation = Circulation.from_dict(doc(red={"share": 0}, blue={"share": 0}))
    shares = circulation.shares_for(["red", "blue"])
    assert shares == pytest.approx({"red": 0.5, "blue": 0.5})
    assert any("no shelf has a positive share" in w for w in circulation.report.warnings)


def test_all_zero_shares_raise_in_strict_mode():
    circulation = Circulation.from_dict(doc(red={"share": 0}, blue={"share": 0}), strict=True)
    with pytest.raises(CirculationError, match="no shelf has a positive share"):
        circulation.shares_for(["red", "blue"])


# --- Checking against the library layout ------------------------------------------------

LAYOUT = {"news": ["ads", "obits"], "poetry": ["dickinson"]}


def matching():
    return Circulation.from_dict(doc(news={"holdings": {"ads": 1, "obits": 0.2}},
                                 poetry={"holdings": {"dickinson": 1}}))


def test_matching_layout_has_nothing_to_report():
    report = matching().check(LAYOUT)
    assert report.ok and report.warnings == []


def test_unlisted_shelf_is_reported():
    report = Circulation.from_dict(doc(news={"holdings": {"ads": 1, "obits": 1}})).check(LAYOUT)
    assert any("shelf" in w and "poetry" in w and "1.0" in w for w in report.warnings)


def test_unlisted_holding_is_reported():
    report = Circulation.from_dict(doc(news={"holdings": {"ads": 1}}, poetry={"holdings": {"dickinson": 1}})).check(LAYOUT)
    assert any("holding" in w and "obits" in w for w in report.warnings)


def test_listed_holding_with_no_passages_is_reported_not_fatal():
    circulation = Circulation.from_dict(doc(news={"holdings": {"ads": 1, "obits": 1, "ghost": 1}}))
    report = circulation.check(LAYOUT)
    assert any("no passages" in w and "ghost" in w for w in report.warnings)


def test_listed_shelf_with_no_passages_is_reported_not_fatal():
    report = Circulation.from_dict(doc(news={}, poetry={}, cooking={})).check(LAYOUT)
    assert any("no passages" in w and "cooking" in w for w in report.warnings)


def test_shelf_with_an_empty_holding_list_in_the_layout_counts_as_no_passages():
    report = Circulation.from_dict(doc(news={})).check({"news": []})
    assert any("no passages" in w and "news" in w for w in report.warnings)


def test_holding_listed_under_the_wrong_shelf_is_reported():
    """The Sun Tzu case: the id says one thing, the folder says another."""
    circulation = Circulation.from_dict(doc(news={"holdings": {"ads": 1, "obits": 1, "dickinson": 1}}, poetry={}))
    report = circulation.check(LAYOUT)
    assert any("dickinson" in w and "listed under 'news'" in w and "'poetry'" in w
               for w in report.warnings)


def test_check_returns_its_own_report_and_leaves_the_loading_report_alone():
    circulation = Circulation.from_dict(doc(news={"holdings": {"ads": 1}}))
    before = len(circulation.report)
    report = circulation.check(LAYOUT)
    assert len(report) > 0 and len(circulation.report) == before


def test_check_does_not_print(capsys):
    Circulation.from_dict(doc(news={"holdings": {"ads": 1}})).check(LAYOUT)
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


STRICT_CHECKS = [
    ("an unlisted shelf", doc(news={"holdings": {"ads": 1, "obits": 1}}), "poetry"),
    ("an unlisted holding", doc(news={"holdings": {"ads": 1}}, poetry={"holdings": {"dickinson": 1}}), "obits"),
    ("a listed holding with no passages", doc(news={"holdings": {"ads": 1, "obits": 1, "ghost": 1}}), "ghost"),
    ("a listed shelf with no passages", doc(news={}, poetry={}, cooking={}), "cooking"),
    ("a holding under the wrong shelf",
     doc(news={"holdings": {"ads": 1, "obits": 1, "dickinson": 1}}, poetry={}), "dickinson"),
]


@pytest.mark.parametrize("label,document,pattern", STRICT_CHECKS, ids=[c[0] for c in STRICT_CHECKS])
def test_strict_check_raises_for_any_drift(label, document, pattern):
    with pytest.raises(CirculationError, match=pattern):
        Circulation.from_dict(document, strict=True).check(LAYOUT)


def test_strict_check_accepts_a_matching_layout():
    assert Circulation.from_dict(matching_document(), strict=True).check(LAYOUT).ok


def matching_document():
    return doc(news={"holdings": {"ads": 1, "obits": 0.2}}, poetry={"holdings": {"dickinson": 1}})


# --- The committed file ----------------------------------------------------------------

@pytest.mark.skipif(not (REPO / "library" / "passages" / "circulation.json").exists(),
                    reason="library/passages/circulation.json not present")
def test_real_weights_file_is_clean_even_in_strict_mode():
    passages = REPO / "library" / "passages"
    layout = {folder.name: [f.stem for f in sorted(folder.glob("*.jsonl"))]
              for folder in sorted(passages.iterdir()) if folder.is_dir()}
    circulation = Circulation.from_file(passages / "circulation.json", strict=True)   # raises on any typo
    assert circulation.check(layout).ok
    shares = circulation.shares_for(layout)
    assert sum(shares.values()) == pytest.approx(1.0)
    assert set(shares) == set(layout)
