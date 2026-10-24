"""Tests for pom.corpus.chunk_store: loading chunk files and answering layout questions.

The module follows the project principle that nothing may stop the oracle: by default every bad
file or record is skipped or repaired and recorded in a FaultRecorder. In strict mode, used by tests and
dev tools, the same problems raise. Most bad-input cases are tested both ways.

Most tests build a tiny corpus in a temp folder. The last one loads the committed chunks and
hands the layout to DrawWeights, which is how the two modules are meant to meet.
"""
import dataclasses
import json
import logging
from pathlib import Path

import pytest

from pom.corpus import Chunk, ChunkError, ChunkStore, DrawWeights

REPO = Path(__file__).resolve().parents[2]


def record(register, source, index, text, **extra):
    """One chunk record as chunk.py would write it."""
    return {"id": f"{source}#{index:05d}", "source": source, "register": register, "text": text,
            "words": len(text.split()), "repeats": 1, "sha1": "abc", **extra}


def write(root, register, source, records, raw=None):
    """Writes <root>/<register>/<source>.jsonl; `raw` replaces the content verbatim."""
    folder = root / register
    folder.mkdir(parents=True, exist_ok=True)
    body = raw if raw is not None else "".join(json.dumps(r) + "\n" for r in records)
    (folder / f"{source}.jsonl").write_text(body, encoding="utf-8")


@pytest.fixture
def corpus(tmp_path):
    """news: ads(2), obits(1); poetry: dickinson(3)."""
    write(tmp_path, "news", "ads", [record("news", "ads", 0, "Wanted, a cook."),
                                     record("news", "ads", 1, "Lost, a cow.")])
    write(tmp_path, "news", "obits", [record("news", "obits", 0, "He is gone.")])
    write(tmp_path, "poetry", "dickinson", [record("poetry", "dickinson", i, f"Line {i}.\nLine b.")
                                            for i in range(3)])
    return tmp_path


# --- Loading and questions -------------------------------------------------------------

def test_loads_every_chunk(corpus):
    store = ChunkStore.load(corpus)
    assert len(store) == 6
    assert store.count() == 6


def test_registers_and_sources_are_sorted(corpus):
    store = ChunkStore.load(corpus)
    assert store.registers() == ["news", "poetry"]
    assert store.sources("news") == ["ads", "obits"]
    assert store.sources("poetry") == ["dickinson"]


def test_layout_maps_register_to_sources(corpus):
    assert ChunkStore.load(corpus).layout() == {"news": ["ads", "obits"], "poetry": ["dickinson"]}


def test_counts_by_register_and_source(corpus):
    store = ChunkStore.load(corpus)
    assert store.count("news") == 3
    assert store.count("news", "ads") == 2
    assert store.count("poetry") == 3
    assert store.count("poetry", "nothing") == 0
    assert store.count("cooking") == 0


def test_chunks_of_a_source_keep_file_order(corpus):
    texts = [c.text for c in ChunkStore.load(corpus).chunks("news", "ads")]
    assert texts == ["Wanted, a cook.", "Lost, a cow."]


def test_chunks_of_a_register_group_by_source_in_order(corpus):
    ids = [c.id for c in ChunkStore.load(corpus).chunks("news")]
    assert ids == ["ads#00000", "ads#00001", "obits#00000"]


def test_unknown_register_or_source_gives_nothing_not_an_error(corpus):
    store = ChunkStore.load(corpus)
    assert store.chunks("cooking") == ()
    assert store.chunks("news", "ghost") == ()
    assert store.sources("cooking") == []


def test_get_by_id(corpus):
    chunk = ChunkStore.load(corpus).get("obits#00000")
    assert chunk.text == "He is gone." and chunk.register == "news" and chunk.source == "obits"


def test_get_unknown_id_is_an_error(corpus):
    with pytest.raises(ChunkError, match="no chunk with id"):
        ChunkStore.load(corpus).get("nope#1")


def test_iteration_is_deterministic_and_complete(corpus):
    ids = [c.id for c in ChunkStore.load(corpus)]
    assert ids == ["ads#00000", "ads#00001", "obits#00000",
                   "dickinson#00000", "dickinson#00001", "dickinson#00002"]
    assert ids == [c.id for c in ChunkStore.load(corpus)]


def test_verse_line_breaks_survive(corpus):
    assert ChunkStore.load(corpus).get("dickinson#00000").text == "Line 0.\nLine b."


def test_chunks_are_immutable(corpus):
    chunk = ChunkStore.load(corpus).get("ads#00000")
    with pytest.raises(dataclasses.FrozenInstanceError):
        chunk.text = "changed"


def test_fields_are_read(tmp_path):
    write(tmp_path, "news", "ads", [record("news", "ads", 0, "Wanted, a cook.", repeats=3, sha1="deadbeef")])
    chunk = ChunkStore.load(tmp_path).get("ads#00000")
    assert (chunk.words, chunk.repeats, chunk.sha1) == (3, 3, "deadbeef")


def test_missing_optional_fields_get_defaults(tmp_path):
    write(tmp_path, "news", "ads", [{"id": "ads#0", "source": "ads", "register": "news", "text": "Two words"}])
    chunk = ChunkStore.load(tmp_path).get("ads#0")
    assert (chunk.words, chunk.repeats, chunk.sha1) == (2, 1, "")


@pytest.mark.parametrize("bad", [True, False, "7", None, 2.5])
def test_unusable_word_count_is_recomputed_from_the_text(tmp_path, bad):
    write(tmp_path, "news", "ads", [record("news", "ads", 0, "Wanted, a cook.") | {"words": bad}])
    assert ChunkStore.load(tmp_path).get("ads#00000").words == 3


def test_extra_fields_are_ignored(tmp_path):
    write(tmp_path, "news", "ads", [record("news", "ads", 0, "Hi there", future_field="x")])
    assert len(ChunkStore.load(tmp_path)) == 1


def test_blank_lines_are_skipped(tmp_path):
    body = json.dumps(record("news", "ads", 0, "One")) + "\n\n   \n" + json.dumps(record("news", "ads", 1, "Two")) + "\n"
    write(tmp_path, "news", "ads", None, raw=body)
    assert len(ChunkStore.load(tmp_path)) == 2


def test_only_register_folders_with_jsonl_files_are_read(corpus):
    (corpus / "weights.json").write_text("{}", encoding="utf-8")
    (corpus / "news" / "notes.txt").write_text("not a chunk file", encoding="utf-8")
    write(corpus, ".hidden", "secret", [record(".hidden", "secret", 0, "Never loaded")])
    store = ChunkStore.load(corpus)
    assert len(store) == 6
    assert ".hidden" not in store.registers()
    assert store.report.ok


def test_registers_and_sources_come_back_sorted_whatever_order_they_were_made_in(tmp_path):
    write(tmp_path, "poetry", "zeta", [record("poetry", "zeta", 0, "Late one")])
    write(tmp_path, "news", "obits", [record("news", "obits", 0, "Gone now")])
    write(tmp_path, "poetry", "alpha", [record("poetry", "alpha", 0, "Early one")])
    write(tmp_path, "news", "ads", [record("news", "ads", 0, "For sale")])
    store = ChunkStore.load(tmp_path)
    assert store.registers() == ["news", "poetry"]
    assert store.sources("poetry") == ["alpha", "zeta"]
    assert list(store.layout()) == ["news", "poetry"]
    assert [c.id for c in store] == ["ads#00000", "obits#00000", "alpha#00000", "zeta#00000"]


def test_a_store_built_from_unsorted_chunks_still_answers_in_sorted_order():
    store = ChunkStore.from_chunks([Chunk("z#0", "zeta", "poetry", "Late one", 2),
                                    Chunk("o#0", "obits", "news", "Gone now", 2),
                                    Chunk("a#0", "alpha", "poetry", "Early one", 2)])
    assert store.registers() == ["news", "poetry"]
    assert store.sources("poetry") == ["alpha", "zeta"]
    assert list(store.layout().items()) == [("news", ["obits"]), ("poetry", ["alpha", "zeta"])]
    assert [c.id for c in store] == ["o#0", "a#0", "z#0"]


def test_empty_directory_gives_an_empty_store(tmp_path):
    store = ChunkStore.load(tmp_path)
    assert len(store) == 0 and store.layout() == {}


def test_from_chunks_builds_a_store_in_memory():
    store = ChunkStore.from_chunks([Chunk("a#0", "a", "news", "Hi there", 2),
                                    Chunk("b#0", "b", "poetry", "Oh me", 2)])
    assert store.layout() == {"news": ["a"], "poetry": ["b"]}


# --- Recovering from bad input (the default) -------------------------------------------

def test_missing_directory_gives_an_empty_store_and_a_critical_issue(tmp_path):
    store = ChunkStore.load(tmp_path / "nope")
    assert len(store) == 0 and store.layout() == {}
    assert store.report.has_critical
    assert any("not found" in m for m in store.report.criticals)


def test_an_empty_directory_is_critical_because_there_is_nothing_to_say(tmp_path):
    store = ChunkStore.load(tmp_path)
    assert len(store) == 0 and store.report.has_critical
    assert any("nothing to say" in m for m in store.report.criticals)


def test_a_good_corpus_has_a_clean_report(corpus):
    report = ChunkStore.load(corpus).report
    assert report.ok and not report.has_critical


def test_a_bad_json_line_is_skipped_and_the_rest_still_load(tmp_path):
    body = (json.dumps(record("news", "ads", 0, "Fine")) + "\n{broken\n"
            + json.dumps(record("news", "ads", 2, "Also fine")) + "\n")
    write(tmp_path, "news", "ads", None, raw=body)
    store = ChunkStore.load(tmp_path)
    assert [c.text for c in store.chunks("news", "ads")] == ["Fine", "Also fine"]
    assert any("ads.jsonl:2: not valid JSON" in w for w in store.report.warnings)


def test_a_non_object_record_is_skipped(tmp_path):
    body = "[1, 2]\n" + json.dumps(record("news", "ads", 1, "Fine")) + "\n"
    write(tmp_path, "news", "ads", None, raw=body)
    store = ChunkStore.load(tmp_path)
    assert len(store) == 1
    assert any("ads.jsonl:1: a chunk must be a JSON object" in w for w in store.report.warnings)


def test_a_record_without_text_is_skipped(tmp_path):
    bad = record("news", "ads", 0, "x")
    del bad["text"]
    write(tmp_path, "news", "ads", [bad, record("news", "ads", 1, "Fine")])
    store = ChunkStore.load(tmp_path)
    assert [c.id for c in store] == ["ads#00001"]
    assert any("missing field(s): text" in w for w in store.report.warnings)


@pytest.mark.parametrize("text", ["", "   \n ", 5, None])
def test_empty_or_non_string_text_is_skipped(tmp_path, text):
    write(tmp_path, "news", "ads", [record("news", "ads", 0, "x") | {"text": text},
                                     record("news", "ads", 1, "Fine")])
    store = ChunkStore.load(tmp_path)
    assert len(store) == 1
    assert any("non-empty string" in w for w in store.report.warnings)


def test_a_missing_id_is_generated_from_the_source_and_line(tmp_path):
    bad = record("news", "ads", 0, "Fine")
    del bad["id"]
    write(tmp_path, "news", "ads", [bad])
    store = ChunkStore.load(tmp_path)
    assert [c.id for c in store] == ["ads#line1"]
    assert any("missing field(s): id" in w for w in store.report.warnings)


@pytest.mark.parametrize("field", ["source", "register"])
def test_a_missing_register_or_source_is_filled_in_from_the_location(tmp_path, field):
    bad = record("news", "ads", 0, "Fine")
    del bad[field]
    write(tmp_path, "news", "ads", [bad])
    chunk = ChunkStore.load(tmp_path).get("ads#00000")
    assert (chunk.register, chunk.source) == ("news", "ads")


def test_register_mismatch_trusts_the_folder_and_warns(tmp_path):
    write(tmp_path, "news", "ads", [record("poetry", "ads", 0, "Fine")])
    store = ChunkStore.load(tmp_path)
    assert store.get("ads#00000").register == "news"
    assert store.layout() == {"news": ["ads"]}
    assert any("register 'poetry' does not match its folder 'news'" in w for w in store.report.warnings)


def test_source_mismatch_trusts_the_file_and_warns(tmp_path):
    write(tmp_path, "news", "ads", [record("news", "obits", 0, "Fine")])
    store = ChunkStore.load(tmp_path)
    assert store.get("obits#00000").source == "ads"
    assert store.layout() == {"news": ["ads"]}
    assert any("source 'obits' does not match its file 'ads'" in w for w in store.report.warnings)


def test_duplicate_ids_keep_the_first_and_warn(tmp_path):
    write(tmp_path, "news", "ads", [record("news", "ads", 0, "One"), record("news", "ads", 0, "Two")])
    store = ChunkStore.load(tmp_path)
    assert len(store) == 1 and store.get("ads#00000").text == "One"
    assert any("duplicate chunk id" in w for w in store.report.warnings)


@pytest.mark.parametrize("bad", [0, -1, "3", None, True, 2.5])
def test_bad_repeats_become_one_with_a_warning(tmp_path, bad):
    write(tmp_path, "news", "ads", [record("news", "ads", 0, "Fine") | {"repeats": bad}])
    store = ChunkStore.load(tmp_path)
    assert store.get("ads#00000").repeats == 1
    assert any("repeats must be" in w for w in store.report.warnings)


def test_an_unreadable_file_is_skipped_and_the_others_load(corpus):
    (corpus / "news" / "broken.jsonl").write_bytes(b"\xff\xfe not utf-8 \x80")
    store = ChunkStore.load(corpus)
    assert len(store) == 6                      # the six good chunks are all there
    assert any("broken.jsonl" in w and "skipping the file" in w for w in store.report.warnings)


def test_a_corrupt_file_cannot_flood_the_report(tmp_path):
    write(tmp_path, "news", "ads", None, raw="{bad\n" * 5000)
    store = ChunkStore.load(tmp_path)
    assert len(store.report.warnings) <= 50 and store.report.suppressed > 4000


def test_problems_are_logged_so_they_reach_journald(tmp_path, caplog):
    write(tmp_path, "news", "ads", None, raw="{bad\n")
    with caplog.at_level(logging.WARNING, logger="pom.corpus.chunk_store"):
        ChunkStore.load(tmp_path)
    assert any(r.levelno == logging.WARNING and "not valid JSON" in r.message for r in caplog.records)
    assert any(r.levelno == logging.CRITICAL and "nothing to say" in r.message for r in caplog.records)


def test_loading_never_raises_whatever_is_wrong(tmp_path):
    write(tmp_path, "news", "ads", None, raw='{"text": 5}\n[1]\n\x00\n{"id": 1, "text": "x", "words": [], "repeats": {}}\n')
    ChunkStore.load(tmp_path)


def test_duplicate_ids_in_memory_keep_the_first_and_warn():
    store = ChunkStore.from_chunks([Chunk("a#0", "a", "news", "First one", 2),
                                    Chunk("a#0", "a", "news", "Second one", 2)])
    assert len(store) == 1 and store.get("a#0").text == "First one"
    assert any("duplicate" in w for w in store.report.warnings)


def test_an_empty_in_memory_store_is_critical():
    assert ChunkStore.from_chunks([]).report.has_critical


def test_load_logs_a_summary_at_info_and_per_file_detail_at_debug(corpus, caplog):
    from pom.log_setup import get_logger
    get_logger("pom.corpus.chunk_store", True)
    try:
        with caplog.at_level(logging.DEBUG, logger="pom.corpus.chunk_store"):
            ChunkStore.load(corpus)
    finally:
        get_logger("pom.corpus.chunk_store", None)
    info = [r.message for r in caplog.records if r.levelno == logging.INFO]
    debug = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
    assert info == ["loaded 6 chunks from 3 source(s) in 2 register(s)"]
    assert any("news/ads.jsonl: 2 chunk(s)" in m for m in debug)


def test_per_file_detail_is_hidden_when_the_module_flag_is_off(corpus, caplog):
    from pom.log_setup import get_logger
    get_logger("pom.corpus.chunk_store", False)
    try:
        with caplog.at_level(logging.DEBUG):   # root only: naming the logger would reset its level
            ChunkStore.load(corpus)
    finally:
        get_logger("pom.corpus.chunk_store", None)
    assert [r for r in caplog.records if r.levelno == logging.DEBUG] == []
    assert any(r.levelno == logging.INFO for r in caplog.records)   # the summary still shows


# --- The same problems in strict mode (development and tests) --------------------------

def raw_corpus(tmp_path, body):
    write(tmp_path, "news", "ads", None, raw=body)
    return tmp_path


STRICT_CASES = [
    ("invalid json", lambda p: raw_corpus(p, "{broken\n"), r"ads\.jsonl:1: not valid JSON"),
    ("a non-object record", lambda p: raw_corpus(p, "[1, 2]\n"), r"ads\.jsonl:1: a chunk must be a JSON object"),
    ("missing text", lambda p: raw_corpus(p, '{"id": "a", "source": "ads", "register": "news"}\n'),
     "missing field.*text"),
    ("missing id", lambda p: raw_corpus(p, '{"source": "ads", "register": "news", "text": "Hi"}\n'),
     "missing field.*id"),
    ("missing register", lambda p: raw_corpus(p, '{"id": "a", "source": "ads", "text": "Hi"}\n'),
     "missing field.*register"),
    ("empty text", lambda p: raw_corpus(p, '{"id": "a", "source": "ads", "register": "news", "text": " "}\n'),
     "non-empty string"),
    ("register mismatch", lambda p: raw_corpus(p, json.dumps(record("poetry", "ads", 0, "Hi")) + "\n"),
     "register 'poetry' does not match its folder 'news'"),
    ("source mismatch", lambda p: raw_corpus(p, json.dumps(record("news", "obits", 0, "Hi")) + "\n"),
     "source 'obits' does not match its file 'ads'"),
    ("duplicate ids", lambda p: raw_corpus(p, "".join(json.dumps(record("news", "ads", 0, t)) + "\n" for t in ("A b", "C d"))),
     "duplicate chunk id"),
    ("bad repeats", lambda p: raw_corpus(p, json.dumps(record("news", "ads", 0, "Hi") | {"repeats": 0}) + "\n"),
     "repeats must be"),
    ("an unreadable file", lambda p: (raw_corpus(p, json.dumps(record("news", "ads", 0, "Hi")) + "\n"),
                                      (p / "news" / "x.jsonl").write_bytes(b"\xff\xfe\x80"), p)[2],
     "cannot read"),
]


@pytest.mark.parametrize("label,build,pattern", STRICT_CASES, ids=[c[0] for c in STRICT_CASES])
def test_strict_mode_raises_where_default_mode_recovers(tmp_path, label, build, pattern):
    directory = build(tmp_path)
    with pytest.raises(ChunkError, match=pattern):
        ChunkStore.load(directory, strict=True)


def test_strict_missing_directory_raises(tmp_path):
    with pytest.raises(ChunkError, match="not found"):
        ChunkStore.load(tmp_path / "nope", strict=True)


def test_strict_empty_store_raises(tmp_path):
    with pytest.raises(ChunkError, match="nothing to say"):
        ChunkStore.load(tmp_path, strict=True)


def test_strict_accepts_a_clean_corpus(corpus):
    assert len(ChunkStore.load(corpus, strict=True)) == 6


def test_strict_from_chunks_rejects_duplicates():
    with pytest.raises(ChunkError, match="duplicate"):
        ChunkStore.from_chunks([Chunk("a#0", "a", "news", "One two", 2),
                                Chunk("a#0", "a", "news", "Three four", 2)], strict=True)


# --- Meeting DrawWeights, and the committed corpus -----------------------------------------

def test_layout_feeds_weights_check(corpus):
    weights = DrawWeights.from_dict({"registers": {"news": {"sources": {"ads": 1, "obits": 1}},
                                                "poetry": {"sources": {"dickinson": 1}}}})
    assert weights.check(ChunkStore.load(corpus).layout()).ok


@pytest.mark.skipif(not (REPO / "corpus" / "chunks" / "weights.json").exists(),
                    reason="committed chunks not present")
def test_real_chunks_load_and_agree_with_the_real_weights_file():
    store = ChunkStore.load(REPO / "corpus" / "chunks", strict=True)   # raises on any bad record
    assert store.report.ok
    assert len(store) > 50000
    folders = sorted(f.name for f in (REPO / "corpus" / "chunks").iterdir() if f.is_dir())
    assert store.registers() == folders      # every register folder loaded, however many there are
    assert all(chunk.words <= 150 for chunk in store)
    weights = DrawWeights.from_file(REPO / "corpus" / "chunks" / "weights.json", strict=True)
    assert weights.check(store.layout()).ok
