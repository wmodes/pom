"""Tests for pom.library.catalog: loading passage files and answering layout questions.

The module follows the project principle that nothing may stop the oracle: by default every bad
file or record is skipped or repaired and recorded in a FaultRecorder. In strict mode, used by tests and
dev tools, the same problems raise. Most bad-input cases are tested both ways.

Most tests build a tiny library in a temp folder. The last one loads the committed passages and
hands the layout to Circulation, which is how the two modules are meant to meet.
"""
import dataclasses
import json
import logging
from pathlib import Path

import pytest

from pom.library import Passage, CatalogError, Catalog, Circulation

REPO = Path(__file__).resolve().parents[2]


def record(shelf, holding, index, text, **extra):
    """One passage record as cataloguer.py would write it."""
    return {"id": f"{holding}#{index:05d}", "holding": holding, "shelf": shelf, "text": text,
            "words": len(text.split()), "repeats": 1, "sha1": "abc", **extra}


def write(root, shelf, holding, records, raw=None):
    """Writes <root>/<shelf>/<holding>.jsonl; `raw` replaces the content verbatim."""
    folder = root / shelf
    folder.mkdir(parents=True, exist_ok=True)
    body = raw if raw is not None else "".join(json.dumps(r) + "\n" for r in records)
    (folder / f"{holding}.jsonl").write_text(body, encoding="utf-8")


@pytest.fixture
def library(tmp_path):
    """news: ads(2), obits(1); poetry: dickinson(3)."""
    write(tmp_path, "news", "ads", [record("news", "ads", 0, "Wanted, a cook."),
                                     record("news", "ads", 1, "Lost, a cow.")])
    write(tmp_path, "news", "obits", [record("news", "obits", 0, "He is gone.")])
    write(tmp_path, "poetry", "dickinson", [record("poetry", "dickinson", i, f"Line {i}.\nLine b.")
                                            for i in range(3)])
    return tmp_path


# --- Loading and questions -------------------------------------------------------------

def test_loads_every_passage(library):
    store = Catalog.load(library)
    assert len(store) == 6
    assert store.count() == 6


def test_shelves_and_holdings_are_sorted(library):
    store = Catalog.load(library)
    assert store.shelves() == ["news", "poetry"]
    assert store.holdings("news") == ["ads", "obits"]
    assert store.holdings("poetry") == ["dickinson"]


def test_layout_maps_shelf_to_holdings(library):
    assert Catalog.load(library).layout() == {"news": ["ads", "obits"], "poetry": ["dickinson"]}


def test_counts_by_shelf_and_holding(library):
    store = Catalog.load(library)
    assert store.count("news") == 3
    assert store.count("news", "ads") == 2
    assert store.count("poetry") == 3
    assert store.count("poetry", "nothing") == 0
    assert store.count("cooking") == 0


def test_passages_of_a_holding_keep_file_order(library):
    texts = [c.text for c in Catalog.load(library).passages("news", "ads")]
    assert texts == ["Wanted, a cook.", "Lost, a cow."]


def test_passages_of_a_shelf_group_by_holding_in_order(library):
    ids = [c.id for c in Catalog.load(library).passages("news")]
    assert ids == ["ads#00000", "ads#00001", "obits#00000"]


def test_unknown_shelf_or_holding_gives_nothing_not_an_error(library):
    store = Catalog.load(library)
    assert store.passages("cooking") == ()
    assert store.passages("news", "ghost") == ()
    assert store.holdings("cooking") == []


def test_get_by_id(library):
    passage = Catalog.load(library).get("obits#00000")
    assert passage.text == "He is gone." and passage.shelf == "news" and passage.holding == "obits"


def test_get_unknown_id_is_an_error(library):
    with pytest.raises(CatalogError, match="no passage with id"):
        Catalog.load(library).get("nope#1")


def test_iteration_is_deterministic_and_complete(library):
    ids = [c.id for c in Catalog.load(library)]
    assert ids == ["ads#00000", "ads#00001", "obits#00000",
                   "dickinson#00000", "dickinson#00001", "dickinson#00002"]
    assert ids == [c.id for c in Catalog.load(library)]


def test_verse_line_breaks_survive(library):
    assert Catalog.load(library).get("dickinson#00000").text == "Line 0.\nLine b."


def test_passages_are_immutable(library):
    passage = Catalog.load(library).get("ads#00000")
    with pytest.raises(dataclasses.FrozenInstanceError):
        passage.text = "changed"


def test_fields_are_read(tmp_path):
    write(tmp_path, "news", "ads", [record("news", "ads", 0, "Wanted, a cook.", repeats=3, sha1="deadbeef")])
    passage = Catalog.load(tmp_path).get("ads#00000")
    assert (passage.words, passage.repeats, passage.sha1) == (3, 3, "deadbeef")


def test_missing_optional_fields_get_defaults(tmp_path):
    write(tmp_path, "news", "ads", [{"id": "ads#0", "holding": "ads", "shelf": "news", "text": "Two words"}])
    passage = Catalog.load(tmp_path).get("ads#0")
    assert (passage.words, passage.repeats, passage.sha1) == (2, 1, "")


@pytest.mark.parametrize("bad", [True, False, "7", None, 2.5])
def test_unusable_word_count_is_recomputed_from_the_text(tmp_path, bad):
    write(tmp_path, "news", "ads", [record("news", "ads", 0, "Wanted, a cook.") | {"words": bad}])
    assert Catalog.load(tmp_path).get("ads#00000").words == 3


def test_extra_fields_are_ignored(tmp_path):
    write(tmp_path, "news", "ads", [record("news", "ads", 0, "Hi there", future_field="x")])
    assert len(Catalog.load(tmp_path)) == 1


def test_blank_lines_are_skipped(tmp_path):
    body = json.dumps(record("news", "ads", 0, "One")) + "\n\n   \n" + json.dumps(record("news", "ads", 1, "Two")) + "\n"
    write(tmp_path, "news", "ads", None, raw=body)
    assert len(Catalog.load(tmp_path)) == 2


def test_only_shelf_folders_with_jsonl_files_are_read(library):
    (library / "circulation.json").write_text("{}", encoding="utf-8")
    (library / "news" / "notes.txt").write_text("not a passage file", encoding="utf-8")
    write(library, ".hidden", "secret", [record(".hidden", "secret", 0, "Never loaded")])
    store = Catalog.load(library)
    assert len(store) == 6
    assert ".hidden" not in store.shelves()
    assert store.report.ok


def test_shelves_and_holdings_come_back_sorted_whatever_order_they_were_made_in(tmp_path):
    write(tmp_path, "poetry", "zeta", [record("poetry", "zeta", 0, "Late one")])
    write(tmp_path, "news", "obits", [record("news", "obits", 0, "Gone now")])
    write(tmp_path, "poetry", "alpha", [record("poetry", "alpha", 0, "Early one")])
    write(tmp_path, "news", "ads", [record("news", "ads", 0, "For sale")])
    store = Catalog.load(tmp_path)
    assert store.shelves() == ["news", "poetry"]
    assert store.holdings("poetry") == ["alpha", "zeta"]
    assert list(store.layout()) == ["news", "poetry"]
    assert [c.id for c in store] == ["ads#00000", "obits#00000", "alpha#00000", "zeta#00000"]


def test_a_store_built_from_unsorted_passages_still_answers_in_sorted_order():
    store = Catalog.from_passages([Passage("z#0", "zeta", "poetry", "Late one", 2),
                                    Passage("o#0", "obits", "news", "Gone now", 2),
                                    Passage("a#0", "alpha", "poetry", "Early one", 2)])
    assert store.shelves() == ["news", "poetry"]
    assert store.holdings("poetry") == ["alpha", "zeta"]
    assert list(store.layout().items()) == [("news", ["obits"]), ("poetry", ["alpha", "zeta"])]
    assert [c.id for c in store] == ["o#0", "a#0", "z#0"]


def test_empty_directory_gives_an_empty_store(tmp_path):
    store = Catalog.load(tmp_path)
    assert len(store) == 0 and store.layout() == {}


def test_from_passages_builds_a_store_in_memory():
    store = Catalog.from_passages([Passage("a#0", "a", "news", "Hi there", 2),
                                    Passage("b#0", "b", "poetry", "Oh me", 2)])
    assert store.layout() == {"news": ["a"], "poetry": ["b"]}


# --- Recovering from bad input (the default) -------------------------------------------

def test_missing_directory_gives_an_empty_store_and_a_critical_issue(tmp_path):
    store = Catalog.load(tmp_path / "nope")
    assert len(store) == 0 and store.layout() == {}
    assert store.report.has_critical
    assert any("not found" in m for m in store.report.criticals)


def test_an_empty_directory_is_critical_because_there_is_nothing_to_say(tmp_path):
    store = Catalog.load(tmp_path)
    assert len(store) == 0 and store.report.has_critical
    assert any("nothing to say" in m for m in store.report.criticals)


def test_a_good_library_has_a_clean_report(library):
    report = Catalog.load(library).report
    assert report.ok and not report.has_critical


def test_a_bad_json_line_is_skipped_and_the_rest_still_load(tmp_path):
    body = (json.dumps(record("news", "ads", 0, "Fine")) + "\n{broken\n"
            + json.dumps(record("news", "ads", 2, "Also fine")) + "\n")
    write(tmp_path, "news", "ads", None, raw=body)
    store = Catalog.load(tmp_path)
    assert [c.text for c in store.passages("news", "ads")] == ["Fine", "Also fine"]
    assert any("ads.jsonl:2: not valid JSON" in w for w in store.report.warnings)


def test_a_non_object_record_is_skipped(tmp_path):
    body = "[1, 2]\n" + json.dumps(record("news", "ads", 1, "Fine")) + "\n"
    write(tmp_path, "news", "ads", None, raw=body)
    store = Catalog.load(tmp_path)
    assert len(store) == 1
    assert any("ads.jsonl:1: a passage must be a JSON object" in w for w in store.report.warnings)


def test_a_record_without_text_is_skipped(tmp_path):
    bad = record("news", "ads", 0, "x")
    del bad["text"]
    write(tmp_path, "news", "ads", [bad, record("news", "ads", 1, "Fine")])
    store = Catalog.load(tmp_path)
    assert [c.id for c in store] == ["ads#00001"]
    assert any("missing field(s): text" in w for w in store.report.warnings)


@pytest.mark.parametrize("text", ["", "   \n ", 5, None])
def test_empty_or_non_string_text_is_skipped(tmp_path, text):
    write(tmp_path, "news", "ads", [record("news", "ads", 0, "x") | {"text": text},
                                     record("news", "ads", 1, "Fine")])
    store = Catalog.load(tmp_path)
    assert len(store) == 1
    assert any("non-empty string" in w for w in store.report.warnings)


def test_a_missing_id_is_generated_from_the_holding_and_line(tmp_path):
    bad = record("news", "ads", 0, "Fine")
    del bad["id"]
    write(tmp_path, "news", "ads", [bad])
    store = Catalog.load(tmp_path)
    assert [c.id for c in store] == ["ads#line1"]
    assert any("missing field(s): id" in w for w in store.report.warnings)


@pytest.mark.parametrize("field", ["holding", "shelf"])
def test_a_missing_shelf_or_holding_is_filled_in_from_the_location(tmp_path, field):
    bad = record("news", "ads", 0, "Fine")
    del bad[field]
    write(tmp_path, "news", "ads", [bad])
    passage = Catalog.load(tmp_path).get("ads#00000")
    assert (passage.shelf, passage.holding) == ("news", "ads")


def test_shelf_mismatch_trusts_the_folder_and_warns(tmp_path):
    write(tmp_path, "news", "ads", [record("poetry", "ads", 0, "Fine")])
    store = Catalog.load(tmp_path)
    assert store.get("ads#00000").shelf == "news"
    assert store.layout() == {"news": ["ads"]}
    assert any("shelf 'poetry' does not match its folder 'news'" in w for w in store.report.warnings)


def test_holding_mismatch_trusts_the_file_and_warns(tmp_path):
    write(tmp_path, "news", "ads", [record("news", "obits", 0, "Fine")])
    store = Catalog.load(tmp_path)
    assert store.get("obits#00000").holding == "ads"
    assert store.layout() == {"news": ["ads"]}
    assert any("holding 'obits' does not match its file 'ads'" in w for w in store.report.warnings)


def test_duplicate_ids_keep_the_first_and_warn(tmp_path):
    write(tmp_path, "news", "ads", [record("news", "ads", 0, "One"), record("news", "ads", 0, "Two")])
    store = Catalog.load(tmp_path)
    assert len(store) == 1 and store.get("ads#00000").text == "One"
    assert any("duplicate passage id" in w for w in store.report.warnings)


@pytest.mark.parametrize("bad", [0, -1, "3", None, True, 2.5])
def test_bad_repeats_become_one_with_a_warning(tmp_path, bad):
    write(tmp_path, "news", "ads", [record("news", "ads", 0, "Fine") | {"repeats": bad}])
    store = Catalog.load(tmp_path)
    assert store.get("ads#00000").repeats == 1
    assert any("repeats must be" in w for w in store.report.warnings)


def test_an_unreadable_file_is_skipped_and_the_others_load(library):
    (library / "news" / "broken.jsonl").write_bytes(b"\xff\xfe not utf-8 \x80")
    store = Catalog.load(library)
    assert len(store) == 6                      # the six good passages are all there
    assert any("broken.jsonl" in w and "skipping the file" in w for w in store.report.warnings)


def test_a_corrupt_file_cannot_flood_the_report(tmp_path):
    write(tmp_path, "news", "ads", None, raw="{bad\n" * 5000)
    store = Catalog.load(tmp_path)
    assert len(store.report.warnings) <= 50 and store.report.suppressed > 4000


def test_problems_are_logged_so_they_reach_journald(tmp_path, caplog):
    write(tmp_path, "news", "ads", None, raw="{bad\n")
    with caplog.at_level(logging.WARNING, logger="pom.library.catalog"):
        Catalog.load(tmp_path)
    assert any(r.levelno == logging.WARNING and "not valid JSON" in r.message for r in caplog.records)
    assert any(r.levelno == logging.CRITICAL and "nothing to say" in r.message for r in caplog.records)


def test_loading_never_raises_whatever_is_wrong(tmp_path):
    write(tmp_path, "news", "ads", None, raw='{"text": 5}\n[1]\n\x00\n{"id": 1, "text": "x", "words": [], "repeats": {}}\n')
    Catalog.load(tmp_path)


def test_duplicate_ids_in_memory_keep_the_first_and_warn():
    store = Catalog.from_passages([Passage("a#0", "a", "news", "First one", 2),
                                    Passage("a#0", "a", "news", "Second one", 2)])
    assert len(store) == 1 and store.get("a#0").text == "First one"
    assert any("duplicate" in w for w in store.report.warnings)


def test_an_empty_in_memory_store_is_critical():
    assert Catalog.from_passages([]).report.has_critical


def test_load_logs_a_summary_at_info_and_per_file_detail_at_debug(library, caplog):
    from pom.log_setup import get_logger
    get_logger("pom.library.catalog", True)
    try:
        with caplog.at_level(logging.DEBUG, logger="pom.library.catalog"):
            Catalog.load(library)
    finally:
        get_logger("pom.library.catalog", None)
    info = [r.message for r in caplog.records if r.levelno == logging.INFO]
    debug = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    assert info == ["loaded 6 passages from 3 holding(s) in 2 shelf(s)"]
    assert any("news/ads.jsonl: 2 passage(s)" in m for m in debug)


def test_per_file_detail_is_hidden_when_the_module_flag_is_off(library, caplog):
    from pom.log_setup import get_logger
    get_logger("pom.library.catalog", False)
    try:
        with caplog.at_level(logging.DEBUG):   # root only: naming the logger would reset its level
            Catalog.load(library)
    finally:
        get_logger("pom.library.catalog", None)
    assert [r for r in caplog.records if r.levelno == logging.DEBUG] == []
    assert any(r.levelno == logging.INFO for r in caplog.records)   # the summary still shows


# --- The same problems in strict mode (development and tests) --------------------------

def raw_library(tmp_path, body):
    write(tmp_path, "news", "ads", None, raw=body)
    return tmp_path


STRICT_CASES = [
    ("invalid json", lambda p: raw_library(p, "{broken\n"), r"ads\.jsonl:1: not valid JSON"),
    ("a non-object record", lambda p: raw_library(p, "[1, 2]\n"), r"ads\.jsonl:1: a passage must be a JSON object"),
    ("missing text", lambda p: raw_library(p, '{"id": "a", "holding": "ads", "shelf": "news"}\n'),
     "missing field.*text"),
    ("missing id", lambda p: raw_library(p, '{"holding": "ads", "shelf": "news", "text": "Hi"}\n'),
     "missing field.*id"),
    ("missing shelf", lambda p: raw_library(p, '{"id": "a", "holding": "ads", "text": "Hi"}\n'),
     "missing field.*shelf"),
    ("empty text", lambda p: raw_library(p, '{"id": "a", "holding": "ads", "shelf": "news", "text": " "}\n'),
     "non-empty string"),
    ("shelf mismatch", lambda p: raw_library(p, json.dumps(record("poetry", "ads", 0, "Hi")) + "\n"),
     "shelf 'poetry' does not match its folder 'news'"),
    ("holding mismatch", lambda p: raw_library(p, json.dumps(record("news", "obits", 0, "Hi")) + "\n"),
     "holding 'obits' does not match its file 'ads'"),
    ("duplicate ids", lambda p: raw_library(p, "".join(json.dumps(record("news", "ads", 0, t)) + "\n" for t in ("A b", "C d"))),
     "duplicate passage id"),
    ("bad repeats", lambda p: raw_library(p, json.dumps(record("news", "ads", 0, "Hi") | {"repeats": 0}) + "\n"),
     "repeats must be"),
    ("an unreadable file", lambda p: (raw_library(p, json.dumps(record("news", "ads", 0, "Hi")) + "\n"),
                                      (p / "news" / "x.jsonl").write_bytes(b"\xff\xfe\x80"), p)[2],
     "cannot read"),
]


@pytest.mark.parametrize("label,build,pattern", STRICT_CASES, ids=[c[0] for c in STRICT_CASES])
def test_strict_mode_raises_where_default_mode_recovers(tmp_path, label, build, pattern):
    directory = build(tmp_path)
    with pytest.raises(CatalogError, match=pattern):
        Catalog.load(directory, strict=True)


def test_strict_missing_directory_raises(tmp_path):
    with pytest.raises(CatalogError, match="not found"):
        Catalog.load(tmp_path / "nope", strict=True)


def test_strict_empty_store_raises(tmp_path):
    with pytest.raises(CatalogError, match="nothing to say"):
        Catalog.load(tmp_path, strict=True)


def test_strict_accepts_a_clean_library(library):
    assert len(Catalog.load(library, strict=True)) == 6


def test_strict_from_passages_rejects_duplicates():
    with pytest.raises(CatalogError, match="duplicate"):
        Catalog.from_passages([Passage("a#0", "a", "news", "One two", 2),
                                Passage("a#0", "a", "news", "Three four", 2)], strict=True)


# --- Meeting Circulation, and the committed library -----------------------------------------

def test_layout_feeds_weights_check(library):
    weights = Circulation.from_dict({"shelves": {"news": {"holdings": {"ads": 1, "obits": 1}},
                                                "poetry": {"holdings": {"dickinson": 1}}}})
    assert weights.check(Catalog.load(library).layout()).ok


@pytest.mark.skipif(not (REPO / "library" / "passages" / "circulation.json").exists(),
                    reason="committed passages not present")
def test_real_passages_load_and_agree_with_the_real_weights_file():
    store = Catalog.load(REPO / "library" / "passages", strict=True)   # raises on any bad record
    assert store.report.ok
    assert len(store) > 50000
    folders = sorted(f.name for f in (REPO / "library" / "passages").iterdir() if f.is_dir())
    assert store.shelves() == folders      # every shelf folder loaded, however many there are
    assert all(passage.words <= 150 for passage in store)
    weights = Circulation.from_file(REPO / "library" / "passages" / "circulation.json", strict=True)
    assert weights.check(store.layout()).ok
