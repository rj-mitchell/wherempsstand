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
    # ``unmerged`` now counts vectors waiting for the meaning-search index too (wts-core's
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


# a refused index is loud: the update fails, the checks say so (the independent review's fourth finding)


def test_search_by_meaning_switched_off_is_said_by_the_engine_the_command_and_the_familys_check(old, monkeypatch,
                                                                                                tmp_path):
    """When the index's vectors were made with another embedding model, search falls back to words alone for
    every reader. One line in the log was all that said so. Now `hansard meaning-check` (which the server's
    health check runs) ends with status 1, and the family's nightly check is told."""
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from hansard import cli
    from hansard.api.app import create_app
    from hansard.rag.pipeline import Pipeline

    search_index(old.index_dir, HashingEmbedder(64))  # the index notes the model that made its vectors
    monkeypatch.setattr(cli, "_settings", lambda: old)
    assert Engine(old, today=shared.TODAY).meaning_search() == ("on", "Search by meaning is on.")
    on = CliRunner().invoke(cli.app, ["meaning-check"])
    assert (on.exit_code, on.output) == (0, "Search by meaning is on.\n")

    other = dataclasses.replace(old, raw={**old.raw, "embedding": {"backend": "hashing", "dim": 32}})
    engine = Engine(other, today=shared.TODAY)
    state_of_it, said = engine.meaning_search()
    assert state_of_it == "refused" and said.startswith("Search by meaning is OFF: ")
    assert "hansard embed --start-again" in said and "If search by meaning is off" in said
    monkeypatch.setattr(cli, "_settings", lambda: other)
    off = CliRunner().invoke(cli.app, ["meaning-check"])
    assert off.exit_code == 1 and "Search by meaning is OFF" in off.output
    # the family's check: a number, 1, for whoever holds the token
    monkeypatch.setenv("HANSARD_CHECK_TOKEN", "the-family-check-token")
    token = {"Authorization": "Bearer the-family-check-token"}
    client = TestClient(create_app(other, pipeline=Pipeline(other, engine, client=None, today=shared.TODAY)))
    assert client.get("/api/check", headers=token).json()["figures"]["meaning_search_refused"] == 1
    fine = TestClient(create_app(old, pipeline=Pipeline(old, Engine(old, today=shared.TODAY), client=None,
                                                        today=shared.TODAY)))
    assert fine.get("/api/check", headers=token).json()["figures"]["meaning_search_refused"] == 0
    # a site that has never embedded anything is not at fault: nothing is refused, and the command ends well
    fresh = dataclasses.replace(old, data_dir=tmp_path / "fresh")
    monkeypatch.setattr(cli, "_settings", lambda: fresh)
    never = CliRunner().invoke(cli.app, ["meaning-check"])
    assert (never.exit_code, never.output) == (0, "Nothing has been embedded yet: search is by words alone.\n")
    # and the server's health check runs the command, failing on status 1 only
    check = (shared.GOLDEN.parents[1] / "deploy" / "check.sh").read_text()
    assert "hansard meaning-check" in check and '1) bad "${meaning##' in check


# the models folder (wts-core docs/UPGRADING.md, "The embedder's id")


def test_meaning_check_says_which_case_the_models_folder_is_in_and_the_sites_note_of_the_weights_follows_the_index(
        settings, monkeypatch):
    """On a server set up before the shared package, data/models holds the re-ranker's files beside the embedding
    model's. From wts-core 0.3.0 the embedding model's id covers its own files only, where they can be told apart,
    and both the index and the site's own note of the weights take it on their first opening on this version; the
    old note (the whole folder's fingerprint) is accepted once. `hansard meaning-check --look` (read only, for before
    a deploy) and `hansard meaning-check` say whether anything in the folder may be moved yet."""
    import hashlib

    from typer.testing import CliRunner
    from wts_core.adapters.fastembed import model_fingerprint

    from hansard import cli
    from hansard.adapters import MODELS_FOLDER, models_dir, models_folder
    from hansard.store.state import StateDB

    s = dataclasses.replace(settings, raw={**settings.raw, "embedding": {**settings.raw["embedding"],
                                                                         "backend": "fastembed"}})
    model = s.embedding["model"]
    folder = models_dir(s)
    own = folder / f"models--{model.replace('/', '--')}" / "onnx"
    reranker = folder / "models--Xenova--ms-marco-MiniLM-L-6-v2" / "onnx"
    for where, weights in ((own, b"the embedding model"), (reranker, b"the re-ranker")):
        where.mkdir(parents=True)
        (where / "model.onnx").write_bytes(weights)
    whole = model_fingerprint(folder)  # what the site's code before the package noted
    StateDB(s.state_db).set("embedding_model_sha256", whole)
    monkeypatch.setattr(cli, "_settings", lambda: s)

    # before the deploy: nothing is stamped, so nothing in the folder may be moved yet
    looked = CliRunner().invoke(cli.app, ["meaning-check", "--look"])
    assert looked.exit_code == 0, looked.output
    lines = looked.output.splitlines()
    assert lines[0] == "The index is stamped: nothing (it is stamped when first opened)"
    mine = model_fingerprint(folder, model)
    assert mine != whole and lines[1] == f"This version opens it as: {model}@{mine[:16]}"
    assert lines[2] == f"and accepts, once: {model}@{whole[:16]}"
    assert lines[3] == "The model's own files were found."
    assert lines[4] == MODELS_FOLDER["not yet"].format(folder=folder)
    # the index opened on this version takes the model's own id; the site's note is still the whole folder's
    s.index_dir.mkdir(parents=True, exist_ok=True)
    (s.index_dir / META_FILE).write_text(json.dumps({"embedder_id": f"{model}@{mine[:16]}", "dimension": 384}))
    assert models_folder(s)[0] == "not yet"
    # the next `hansard embed` accepts the old note once and notes the model's own (the step in embed_pending_cmd)
    noted = StateDB(s.state_db).get("embedding_model_sha256")
    assert noted in (mine, model_fingerprint(folder))
    StateDB(s.state_db).set("embedding_model_sha256", mine)
    assert models_folder(s) == ("ready", MODELS_FOLDER["ready"].format(folder=folder))
    # and now the re-ranker's old files may go: nothing that matters changes
    (reranker / "model.onnx").unlink()
    assert model_fingerprint(folder, model) == mine and models_folder(s)[0] == "ready"

    # weights that cannot be told apart: the whole folder's id, and nothing in it is to be moved
    (folder / "loose.onnx").write_bytes(hashlib.sha256(b"x").digest())
    (own.parent).rename(folder / "models--somewhere-else")
    assert models_folder(s) == ("unclear", MODELS_FOLDER["unclear"].format(folder=folder))
    assert "hansard embed --start-again" in MODELS_FOLDER["unclear"]


def test_the_sites_note_of_the_weights_accepts_the_whole_folders_once_and_then_the_models_own(settings, monkeypatch):
    """`hansard embed` refuses to add vectors when the weights have changed since its note of them. The note
    covered the whole models folder before wts-core 0.3.0; it now follows the embedder's own id, accepting the old
    note once, so that moving a re-ranker's files out of the folder afterwards does not stop the nightly update."""
    from typer import Exit
    from wts_core.adapters.fastembed import model_fingerprint

    from hansard import adapters, cli
    from hansard.adapters import models_dir
    from hansard.store.state import StateDB

    s = dataclasses.replace(settings, raw={**settings.raw, "embedding": {**settings.raw["embedding"],
                                                                         "backend": "fastembed", "dim": 64}})
    folder = models_dir(s)
    for name, weights in (("models--the--embedder", b"embedder"), ("models--a--re-ranker", b"re-ranker")):
        (folder / name).mkdir(parents=True)
        (folder / name / "model.onnx").write_bytes(weights)
    mine = model_fingerprint(folder / "models--the--embedder")

    class Embedder(HashingEmbedder):
        fingerprint = mine

    monkeypatch.setattr(adapters, "get_embedder", lambda settings: Embedder(64))
    state = StateDB(s.state_db)
    state.set("embedding_model_sha256", model_fingerprint(folder))  # the whole folder's, as noted before
    cli.embed_pending_cmd(s, max_files=0)
    assert state.get("embedding_model_sha256") == mine  # accepted once, and noted afresh
    (folder / "models--a--re-ranker" / "model.onnx").unlink()  # the re-ranker's old files moved out
    cli.embed_pending_cmd(s, max_files=0)
    state.set("embedding_model_sha256", "another model's weights")
    with pytest.raises(Exit):
        cli.embed_pending_cmd(s, max_files=0)
