"""Tests for pom.corpus.weights: parsing, defaults, normalization and validation.

Everything runs against tiny in-memory documents or temp files, so nothing depends on the real
corpus except the last test, which is a smoke check of the committed weights file.
"""
import json
from pathlib import Path

import pytest

from pom.corpus import Weights, WeightsError

REPO = Path(__file__).resolve().parents[2]


def doc(**registers):
    """Builds a weights document: doc(red={"share": 1, "sources": {"a": 0.5}})."""
    return {"registers": registers}


# --- Parsing ---------------------------------------------------------------------------

def test_reads_shares_and_source_weights():
    weights = Weights.from_dict(doc(news={"share": 2, "sources": {"ads": 0.5}}))
    assert weights.share("news") == 2.0
    assert weights.source_weight("ads") == 0.5


def test_unlisted_register_and_source_count_one():
    weights = Weights.from_dict(doc(news={"share": 2, "sources": {"ads": 0.5}}))
    assert weights.share("poetry") == 1.0
    assert weights.source_weight("anything") == 1.0


def test_register_without_share_or_sources_defaults():
    weights = Weights.from_dict(doc(news={}))
    assert weights.share("news") == 1.0


def test_empty_document_is_valid_and_all_defaults():
    weights = Weights.from_dict({})
    assert weights.share("x") == 1.0
    assert weights.source_weight("y") == 1.0


def test_underscore_keys_are_notes_and_ignored():
    weights = Weights.from_dict({
        "_about": "a note",
        "registers": {"_comment": "ignored", "news": {"_why": "x", "share": 3, "sources": {"_n": 9, "ads": 2}}},
    })
    assert weights.share("news") == 3.0
    assert weights.source_weight("ads") == 2.0
    assert weights.source_weight("_n") == 1.0   # a note, not a weight


def test_zero_is_allowed_to_switch_something_off():
    weights = Weights.from_dict(doc(news={"share": 0, "sources": {"ads": 0}}))
    assert weights.share("news") == 0.0
    assert weights.source_weight("ads") == 0.0


def test_from_file_reads_json(tmp_path):
    path = tmp_path / "weights.json"
    path.write_text(json.dumps(doc(news={"share": 4})), encoding="utf-8")
    assert Weights.from_file(path).share("news") == 4.0


# --- Bad input -------------------------------------------------------------------------

def test_missing_file_is_an_error(tmp_path):
    with pytest.raises(WeightsError, match="cannot read"):
        Weights.from_file(tmp_path / "nope.json")


def test_invalid_json_is_an_error(tmp_path):
    path = tmp_path / "weights.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(WeightsError, match="not valid JSON"):
        Weights.from_file(path)


@pytest.mark.parametrize("bad", [[], "text", 5, None])
def test_top_level_must_be_an_object(bad):
    with pytest.raises(WeightsError, match="must be a JSON object"):
        Weights.from_dict(bad)


def test_registers_must_be_an_object():
    with pytest.raises(WeightsError, match="'registers' must be an object"):
        Weights.from_dict({"registers": []})


def test_register_entry_must_be_an_object():
    with pytest.raises(WeightsError, match="register 'news' must be an object"):
        Weights.from_dict({"registers": {"news": 3}})


def test_sources_must_be_an_object():
    with pytest.raises(WeightsError, match="'sources' must be an object"):
        Weights.from_dict(doc(news={"sources": ["ads"]}))


def test_typo_in_a_register_key_is_an_error():
    with pytest.raises(WeightsError, match="unknown key.*shares"):
        Weights.from_dict(doc(news={"shares": 2}))


@pytest.mark.parametrize("bad", ["2", None, True, False, [1], -1, -0.5, float("nan"), float("inf")])
def test_bad_share_values_are_errors(bad):
    with pytest.raises(WeightsError, match="share"):
        Weights.from_dict(doc(news={"share": bad}))


@pytest.mark.parametrize("bad", ["2", None, True, -1, float("nan"), float("inf")])
def test_bad_source_weights_are_errors(bad):
    with pytest.raises(WeightsError, match="source 'ads'"):
        Weights.from_dict(doc(news={"sources": {"ads": bad}}))


def test_a_source_listed_twice_is_an_error():
    with pytest.raises(WeightsError, match="more than one register"):
        Weights.from_dict(doc(news={"sources": {"ads": 1}}, poetry={"sources": {"ads": 1}}))


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
    shares = weights.shares_for(["blue"])
    assert shares == {"blue": 1.0}


def test_zero_share_register_gets_zero_probability():
    weights = Weights.from_dict(doc(red={"share": 0}, blue={"share": 1}))
    assert weights.shares_for(["red", "blue"]) == pytest.approx({"red": 0.0, "blue": 1.0})


def test_no_registers_is_an_error():
    with pytest.raises(WeightsError, match="nothing can be drawn"):
        Weights.from_dict({}).shares_for([])


def test_all_zero_shares_is_an_error():
    weights = Weights.from_dict(doc(red={"share": 0}, blue={"share": 0}))
    with pytest.raises(WeightsError, match="nothing can be drawn"):
        weights.shares_for(["red", "blue"])


# --- Checking against the corpus layout ------------------------------------------------

LAYOUT = {"news": ["ads", "obits"], "poetry": ["dickinson"]}


def test_matching_layout_has_nothing_to_report():
    weights = Weights.from_dict(doc(news={"sources": {"ads": 1, "obits": 0.2}},
                                    poetry={"sources": {"dickinson": 1}}))
    report = weights.check(LAYOUT)
    assert report.ok
    assert report.warnings == []


def test_unlisted_register_is_a_warning_not_an_error():
    weights = Weights.from_dict(doc(news={"sources": {"ads": 1, "obits": 1}}))
    report = weights.check(LAYOUT)   # poetry is missing from the file
    assert not report.ok
    assert any("register" in w and "poetry" in w and "1.0" in w for w in report.warnings)


def test_unlisted_source_is_a_warning_not_an_error():
    weights = Weights.from_dict(doc(news={"sources": {"ads": 1}}, poetry={"sources": {"dickinson": 1}}))
    report = weights.check(LAYOUT)   # obits is missing
    assert any("source" in w and "obits" in w for w in report.warnings)


def test_listed_source_with_no_chunks_is_an_error():
    weights = Weights.from_dict(doc(news={"sources": {"ads": 1, "obits": 1, "ghost": 1}}))
    with pytest.raises(WeightsError, match="no chunks.*ghost"):
        weights.check(LAYOUT)


def test_listed_register_with_no_chunks_is_an_error():
    weights = Weights.from_dict(doc(news={}, poetry={}, cooking={}))
    with pytest.raises(WeightsError, match="no chunks.*cooking"):
        weights.check(LAYOUT)


def test_register_with_an_empty_source_list_in_the_layout_counts_as_no_chunks():
    weights = Weights.from_dict(doc(news={}))
    with pytest.raises(WeightsError, match="no chunks.*news"):
        weights.check({"news": []})


def test_source_listed_under_the_wrong_register_is_a_warning():
    """The Sun Tzu case: the id says one thing, the folder says another."""
    weights = Weights.from_dict(doc(news={"sources": {"ads": 1, "obits": 1, "dickinson": 1}},
                                    poetry={}))
    report = weights.check(LAYOUT)
    assert any("dickinson" in w and "listed under 'news'" in w and "'poetry'" in w
               for w in report.warnings)


def test_check_does_not_print(capsys):
    Weights.from_dict(doc(news={"sources": {"ads": 1}})).check(LAYOUT)
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


# --- The committed file ----------------------------------------------------------------

@pytest.mark.skipif(not (REPO / "corpus" / "chunks" / "weights.json").exists(),
                    reason="corpus/chunks/weights.json not present")
def test_real_weights_file_loads_and_matches_the_real_layout():
    chunks = REPO / "corpus" / "chunks"
    layout = {folder.name: [f.stem for f in sorted(folder.glob("*.jsonl"))]
              for folder in sorted(chunks.iterdir()) if folder.is_dir()}
    weights = Weights.from_file(chunks / "weights.json")
    report = weights.check(layout)             # raises if the file lists something that is gone
    assert report.warnings == []               # and every source and register is listed
    shares = weights.shares_for(layout)
    assert sum(shares.values()) == pytest.approx(1.0)
    assert set(shares) == set(layout)
