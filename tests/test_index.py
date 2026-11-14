"""The search index on the shared package's tables (wts-core, ``wts_core.adapters.lancedb``), with this site's
columns: an index built by the site's own code before the move opens and searches the same, and the index now
says which embedding model its vectors were made with.

tests/golden/old-index.tar.gz is the tests' own small index as the old code built it (the passages, their 16-bit
vectors, the Parquet store and the reference data); tests/golden/index.json is what the old code found in it.
"""

from __future__ import annotations

import dataclasses
import json
import tarfile

import pytest
import shared
from wts_core.adapters.hashing import HashingEmbedder
from wts_core.adapters.lancedb import META_FILE, TRAINED_FILE
from wts_core.ports import IndexMeta

from hansard.engine import Engine, Filters, Scope
from hansard.index.lance import (
    IndexOutOfDate,
    ensure_portfolio_indices,
    search_index,
    similarities,
    vector_value_type,
)

GOLDEN = shared.read("index.json")
QUERIES = ["trial by jury", "Northern Ireland security", "Finance Bill", "high speed rail"]
WHERES = [None, "house = 'lords'", "year >= 2012 AND is_gov = true"]


@pytest.fixture
def old(settings):
    """A data folder holding the index the old code built."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(shared.GOLDEN / "old-index.tar.gz") as tar:
        tar.extractall(settings.data_dir, filter="data")
    return settings


def test_an_index_built_before_the_move_opens_and_searches_the_same(old):
    assert not (old.index_dir / META_FILE).exists()  # the old code kept no note of the model
    embedder = HashingEmbedder(64)
    index = search_index(old.index_dir, embedder)
    found = {"stats": index.stats(), "vector_type": str(vector_value_type(index.vectors().schema)),
             "words": [], "meaning": [], "engine": []}
    passages: list[str] = []
    for q in QUERIES:
        for where in WHERES:
            rows = index.search_words(q, where, 16)
            found["words"].append({"query": q, "where": where, "hits": shared.close_hits(rows, "_score")})
            passages += [r["passage_id"] for r in rows[:3]]
            found["meaning"].append({"query": q, "where": where, "hits": shared.close_hits(
                index.search_meaning(embedder.embed_query(q), where, 16, exact=True), "_distance")})
    engine = Engine(old, embedder=embedder, today=shared.TODAY)
    for q in QUERIES:
        for filters, scope in ((Filters(), Scope()), (Filters(houses=["lords"]), Scope(written=False))):
            res = engine.search(q, filters, scope, [], None, None, 6, diversify=True)
            found["engine"].append({"query": q, "houses": filters.houses, "candidates": res.candidates,
                                    "semantic": res.semantic_available, "no_records": res.no_records,
                                    "records": [[r["record_id"], round(r["ranking"]["score"], 4)]
                                                for r in res.records]})
    passages = sorted(set(passages))
    sims = similarities(index, embedder.embed_query(QUERIES[0]), passages)
    found["similarities"] = {p: round(sims[p], 4) for p in sorted(sims)}
    keys = sorted({r["source_key"] for r in index.passages_for(passages).values()})[:2]
    found["rows_for_keys"] = {"keys": keys, "passages": sorted(r["passage_id"] for r in index.rows_for_keys(keys))}
    found["passages_for"] = {p: {k: str(v) for k, v in sorted(row.items())
                                 if k in ("record_id", "source_key", "house", "date", "heading")}
                             for p, row in sorted(index.passages_for(passages[:3]).items())}
    # ``unmerged`` now counts vectors waiting for the meaning-search index too (wts-core docs/AS-BUILT.md,
    # adapters): here there are none of either, as before
    assert index.unmerged() == GOLDEN["unmerged"] == 0
    assert found == {k: v for k, v in GOLDEN.items() if k != "unmerged"}
    assert found["vector_type"] == "halffloat" and any(e["semantic"] for e in found["engine"])
    # nothing was trained or rebuilt by opening and searching: there are too few vectors for an index, as before
    assert not (old.index_dir / TRAINED_FILE).exists() and not index.has_index("vectors", "vector")
    assert ensure_portfolio_indices(index) is False  # the old code had built them


def test_the_index_now_says_which_embedding_model_made_its_vectors(old):
    embedder = HashingEmbedder(64)
    assert search_index(old.index_dir).meta.embedder_id == "unknown"  # opened as it stands: nothing is recorded
    assert not (old.index_dir / META_FILE).exists()
    index = search_index(old.index_dir, embedder)  # the first opening with a model takes the vectors for its own
    assert json.loads((old.index_dir / META_FILE).read_text()) == {
        "dimension": 64, "embedder_id": "hashing-64", "number_type": "float16", "schema_version": 1}
    assert index.meta == IndexMeta("hashing-64", 64, "float16", 1)
    # another model, or the same with another size, is refused: its vectors would be mixed with these
    with pytest.raises(IndexOutOfDate):
        search_index(old.index_dir, IndexMeta("another-model", 64, "float16", 1))
    with pytest.raises(IndexOutOfDate):
        search_index(old.index_dir, HashingEmbedder(32))
    # opened as it stands it still searches, by words and among the vectors there are; and adds none
    as_it_stands = search_index(old.index_dir)
    assert as_it_stands.search_words("trial by jury", None, 3)
    assert as_it_stands.search_meaning(embedder.embed_query("trial by jury"), None, 3)
    with pytest.raises(RuntimeError, match="adds no vectors"):
        as_it_stands.add_vectors([], [{}], embedder.embed_documents(["x"]))


def test_a_changed_embedding_model_leaves_search_by_words_and_says_so(old, caplog):
    """The engine checks the model once, when a search first needs it: with another model's vectors in the index,
    search carries on by words alone, as it does before anything is embedded."""
    search_index(old.index_dir, HashingEmbedder(64))
    other = dataclasses.replace(old, raw={**old.raw, "embedding": {"backend": "hashing", "dim": 32}})
    engine = Engine(other, today=shared.TODAY)
    with caplog.at_level("ERROR"):
        res = engine.search("trial by jury", Filters(), Scope(), [], None, None, 6, diversify=True)
    assert res.records and res.semantic_available is False
    assert "search by meaning is off" in caplog.text and "hansard embed --start-again" in caplog.text
    same = Engine(old, today=shared.TODAY).search("trial by jury", Filters(), Scope(), [], None, None, 6,
                                                  diversify=True)
    assert same.semantic_available is True


def test_embedding_with_another_model_is_refused_until_the_vectors_are_started_again(old, monkeypatch):
    from typer.testing import CliRunner

    from hansard import cli
    from hansard.store.state import StateDB

    search_index(old.index_dir, HashingEmbedder(64))
    other = dataclasses.replace(old, raw={**old.raw, "embedding": {"backend": "hashing", "dim": 32}})
    monkeypatch.setattr(cli, "_settings", lambda: other)
    state = StateDB(other.state_db)
    refused = CliRunner().invoke(cli.app, ["embed"])
    assert refused.exit_code != 0 and "hansard embed --start-again" in str(refused.exception)
    assert search_index(old.index_dir).stats()["vectors"] == GOLDEN["stats"]["vectors"]  # nothing was touched
    again = CliRunner().invoke(cli.app, ["embed", "--start-again"])
    assert again.exit_code == 0, again.output
    index = search_index(old.index_dir, HashingEmbedder(32))
    assert index.meta == IndexMeta("hashing-32", 32, "float16", 1)
    assert index.stats() == {"passages": GOLDEN["stats"]["passages"], "vectors": 0}  # no file is listed here to embed
    assert state.unembedded() == []
