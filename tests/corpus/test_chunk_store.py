"""Tests for pom.corpus.chunk_store: loading chunk files and answering layout questions.

Most tests build a tiny corpus in a temp folder. The last one loads the committed chunks and
hands the layout to Weights, which is how the two modules are meant to meet.
"""
import dataclasses
import json
from pathlib import Path

import pytest

from pom.corpus import Chunk, ChunkError, ChunkStore, Weights

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
    (corpus / ".hidden").mkdir()
    (corpus / ".hidden" / "x.jsonl").write_text("garbage", encoding="utf-8")
    assert len(ChunkStore.load(corpus)) == 6


def test_empty_directory_gives_an_empty_store(tmp_path):
    store = ChunkStore.load(tmp_path)
    assert len(store) == 0 and store.layout() == {}


def test_from_chunks_builds_a_store_in_memory():
    store = ChunkStore.from_chunks([Chunk("a#0", "a", "news", "Hi there", 2),
                                    Chunk("b#0", "b", "poetry", "Oh me", 2)])
    assert store.layout() == {"news": ["a"], "poetry": ["b"]}


# --- Bad input -------------------------------------------------------------------------

def test_missing_directory_is_an_error(tmp_path):
    with pytest.raises(ChunkError, match="not found"):
        ChunkStore.load(tmp_path / "nope")


def test_invalid_json_names_the_file_and_line(tmp_path):
    body = json.dumps(record("news", "ads", 0, "Fine")) + "\n{broken\n"
    write(tmp_path, "news", "ads", None, raw=body)
    with pytest.raises(ChunkError, match=r"ads\.jsonl:2: not valid JSON"):
        ChunkStore.load(tmp_path)


def test_a_record_must_be_an_object(tmp_path):
    write(tmp_path, "news", "ads", None, raw="[1, 2]\n")
    with pytest.raises(ChunkError, match=r"ads\.jsonl:1: a chunk must be a JSON object"):
        ChunkStore.load(tmp_path)


@pytest.mark.parametrize("field", ["id", "source", "register", "text"])
def test_missing_required_field_is_an_error(tmp_path, field):
    bad = record("news", "ads", 0, "Fine")
    del bad[field]
    write(tmp_path, "news", "ads", [bad])
    with pytest.raises(ChunkError, match=f"missing field.*{field}"):
        ChunkStore.load(tmp_path)


@pytest.mark.parametrize("text", ["", "   \n ", 5, None])
def test_empty_or_non_string_text_is_an_error(tmp_path, text):
    write(tmp_path, "news", "ads", [record("news", "ads", 0, "x") | {"text": text}])
    with pytest.raises(ChunkError, match="non-empty string"):
        ChunkStore.load(tmp_path)


def test_register_must_match_its_folder(tmp_path):
    write(tmp_path, "news", "ads", [record("poetry", "ads", 0, "Fine")])
    with pytest.raises(ChunkError, match="register 'poetry' does not match its folder 'news'"):
        ChunkStore.load(tmp_path)


def test_source_must_match_its_file(tmp_path):
    write(tmp_path, "news", "ads", [record("news", "obits", 0, "Fine")])
    with pytest.raises(ChunkError, match="source 'obits' does not match its file 'ads'"):
        ChunkStore.load(tmp_path)


def test_duplicate_ids_are_an_error(tmp_path):
    write(tmp_path, "news", "ads", [record("news", "ads", 0, "One"), record("news", "ads", 0, "Two")])
    with pytest.raises(ChunkError, match="duplicate chunk id"):
        ChunkStore.load(tmp_path)


# --- Meeting Weights, and the committed corpus -----------------------------------------

def test_layout_feeds_weights_check(corpus):
    weights = Weights.from_dict({"registers": {"news": {"sources": {"ads": 1, "obits": 1}},
                                                "poetry": {"sources": {"dickinson": 1}}}})
    assert weights.check(ChunkStore.load(corpus).layout()).ok


@pytest.mark.skipif(not (REPO / "corpus" / "chunks" / "weights.json").exists(),
                    reason="committed chunks not present")
def test_real_chunks_load_and_agree_with_the_real_weights_file():
    store = ChunkStore.load(REPO / "corpus" / "chunks")
    assert len(store) > 50000
    assert len(store.registers()) == 13
    assert all(chunk.words <= 150 for chunk in store)
    weights = Weights.from_file(REPO / "corpus" / "chunks" / "weights.json")
    assert weights.check(store.layout()).warnings == []
