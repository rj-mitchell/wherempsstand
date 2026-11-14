"""16-bit vectors: new tables, reading tables of either kind, and converting an old 32-bit table in place."""

from __future__ import annotations

from datetime import date

import numpy as np
import pyarrow as pa
import pytest
from wts_core.ports import IndexMeta

from hansard.index import float16
from hansard.index.evidence import EvidenceVectors
from hansard.index.lance import (
    LanceIndex,
    search_index,
    similarities,
    vector_schema,
    vector_value_type,
)

DIM = 16
EMBEDDER = IndexMeta("an-embedding-model", DIM, number_type="float16")  # whichever model made the vectors: the tools here do not mind
ROWS = 12_000  # enough for a meaning-search index


def _rows(n: int, start: int = 0) -> list[dict]:
    return [{"passage_id": f"p{i}", "record_id": f"r{i}", "source": "debates", "source_key": f"k{i // 100}",
             "record_type": "speech", "house": "commons" if i % 2 else "lords", "date": date(2026, 1, 1),
             "year": 2026, "person_id": f"m{i % 50}", "party_id": "lab", "region": "london", "is_gov": False,
             "is_shadow": False, "is_chair": False, "gov_portfolios": [], "shadow_portfolios": [],
             "chair_portfolios": []} for i in range(start, start + n)]


def _vectors(n: int, seed: int = 0) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal((n, DIM)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def _old_index(path, rows: int = ROWS) -> tuple[LanceIndex, np.ndarray]:
    """A vectors table as made before November 2026: 32-bit, with its index and passage_id index."""
    idx = search_index(path, EMBEDDER)
    idx.db.create_table("vectors", schema=vector_schema(DIM, pa.float32()))
    vecs = _vectors(rows)
    half = rows // 2
    idx.add_vectors([], _rows(half), vecs[:half])
    idx.add_vectors([], _rows(rows - half, half), vecs[half:])
    idx.rebuild_vector_index(1024)
    idx.ensure_vector_ids()
    return idx, vecs


def _target(path) -> float16.Target:
    return float16.passages_target(path, DIM, 1024, {"refine_factor": 5})


def test_new_tables_store_16_bit_vectors_and_find_the_same(tmp_path):
    idx = search_index(tmp_path, EMBEDDER)
    vecs = _vectors(50)
    idx.add_vectors([], _rows(50), vecs)
    assert vector_value_type(idx.vectors().schema) == pa.float16()
    hits = idx.search_meaning(vecs[7], None, 3)
    assert hits[0]["passage_id"] == "p7" and hits[0]["_distance"] < 1e-3
    sims = similarities(idx, vecs[7], ["p7", "p8"])
    assert sims["p7"] == pytest.approx(1.0, abs=1e-3)
    assert sims["p8"] == pytest.approx(float(vecs[7] @ vecs[8]), abs=2e-3)


def test_an_old_32_bit_table_is_still_read_and_written(tmp_path):
    idx = search_index(tmp_path, EMBEDDER)
    idx.db.create_table("vectors", schema=vector_schema(DIM, pa.float32()))
    vecs = _vectors(40)
    idx.add_vectors([], _rows(40), vecs)
    assert vector_value_type(idx.vectors().schema) == pa.float32()  # written in the table's own type
    assert idx.search_meaning(vecs[3], "house = 'commons'", 1)[0]["passage_id"] == "p3"
    assert similarities(idx, vecs[3], ["p3"])["p3"] == pytest.approx(1.0, abs=1e-6)


def test_evidence_vectors_of_either_kind(tmp_path):
    turns = [{"session_id": 1, "ord": i} for i in range(30)]
    vecs = _vectors(30)
    new = EvidenceVectors(tmp_path / "new", DIM)
    new.add([1], turns, vecs)
    assert vector_value_type(new._table().schema) == pa.float16()
    old = EvidenceVectors(tmp_path / "old", DIM)
    old.db.create_table("turns", schema=pa.schema([("session_id", pa.int64()), ("ord", pa.int32()),
                                                    ("vector", pa.list_(pa.float32(), DIM))]))
    old.add([1], turns, vecs)
    assert vector_value_type(old._table().schema) == pa.float32()
    for ev in (new, old):
        sid, ord_, sim = ev.search(vecs[4], 2)[0]
        assert (sid, ord_) == (1, 4) and sim == pytest.approx(1.0, abs=1e-3)


def test_conversion_copies_checks_and_swaps(tmp_path):
    idx, vecs = _old_index(tmp_path)
    before = idx.search_meaning(vecs[11], None, 10, refine_factor=5)
    out = float16.convert(_target(tmp_path), queries=list(_vectors(20, seed=1)), commit_rows=5000)
    assert out["status"] == "converted", out
    assert out["copy"]["rows"] == ROWS and out["checks"]["rows_ok"]
    assert out["checks"]["sample"]["ok"] and out["checks"]["sample"]["max_abs_difference"] < 1e-3
    assert out["checks"]["searches"]["overlap_at_10"] >= 0.9
    assert out["swap"] == "exchanged in one step"
    vt = idx.vectors()
    assert vector_value_type(vt.schema) == pa.float16() and vt.count_rows() == ROWS
    assert {tuple(ix.columns) for ix in vt.list_indices()} == {("vector",), ("passage_id",)}
    assert not (tmp_path / "vectors_f16.lance").exists() and not (tmp_path / "vectors_f16.progress.json").exists()
    after = idx.search_meaning(vecs[11], None, 10, refine_factor=5)
    assert after[0]["passage_id"] == before[0]["passage_id"] == "p11"
    # the site carries on writing, now in 16 bits
    idx.add_vectors(["k0"], _rows(3), _vectors(3, seed=2))
    assert idx.vectors().count_rows() == ROWS - 100 + 3
    # and a second run has nothing to do
    assert float16.convert(_target(tmp_path))["status"] == "already 16-bit"


def test_a_stopped_copy_carries_on_and_a_changed_table_starts_again(tmp_path):
    idx, _ = _old_index(tmp_path)
    target = _target(tmp_path)
    db = float16._db(target)
    messages = []

    def stop_after_first(msg: str) -> None:
        messages.append(msg)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        float16.copy(target, db.open_table("vectors"), db, commit_rows=5000, progress=stop_after_first)
    assert db.open_table("vectors_f16").count_rows() == 5000
    out = float16.copy(target, db.open_table("vectors"), db, commit_rows=5000, progress=lambda m: None)
    assert out["resumed_from"] == 5000 and out["copied_now"] == ROWS - 5000 and not out["restarted"]

    # an update changed the old table between runs: the copy starts again
    idx.add_vectors([], _rows(10, ROWS), _vectors(10, seed=3))
    out = float16.copy(target, db.open_table("vectors"), db, commit_rows=5000, progress=lambda m: None)
    assert out["restarted"] and out["resumed_from"] == 0 and out["rows"] == ROWS + 10
    assert db.open_table("vectors_f16").count_rows() == ROWS + 10


def test_dry_run_and_too_little_space_change_nothing(tmp_path):
    _old_index(tmp_path, rows=500)
    dry = float16.convert(_target(tmp_path), dry_run=True)
    assert dry["status"].startswith("dry run") and dry["new_table_gb_estimate"] >= 0
    refused = float16.convert(_target(tmp_path), min_free_after=10 ** 15)
    assert refused["refused"] and "Free some space" in refused["status"]
    assert not (tmp_path / "vectors_f16.lance").exists()
    assert vector_value_type(search_index(tmp_path, EMBEDDER).vectors().schema) == pa.float32()


def test_without_an_atomic_exchange_two_renames_do(tmp_path, monkeypatch):
    _old_index(tmp_path, rows=500)
    monkeypatch.setattr(float16, "exchange", lambda a, b: False)
    out = float16.convert(_target(tmp_path), keep_old=True)
    assert out["swap"] == "renamed in two steps"
    assert vector_value_type(search_index(tmp_path, EMBEDDER).vectors().schema) == pa.float16()
    assert (tmp_path / "vectors_f32.lance").exists()  # kept, as asked
    again = float16.convert(_target(tmp_path))
    assert again["removed"] == ["vectors_f32"] and not (tmp_path / "vectors_f32.lance").exists()


def test_evidence_table_conversion(tmp_path):
    ev = EvidenceVectors(tmp_path, DIM)
    ev.db.create_table("turns", schema=pa.schema([("session_id", pa.int64()), ("ord", pa.int32()),
                                                   ("vector", pa.list_(pa.float32(), DIM))]))
    vecs = _vectors(600)
    ev.add([], [{"session_id": i // 10, "ord": i % 10} for i in range(600)], vecs)
    out = float16.convert(float16.evidence_target(tmp_path), queries=list(_vectors(5, seed=4)))
    assert out["status"] == "converted" and out["checks"]["sample"]["ok"], out
    assert out["checks"]["searches"]["overlap_at_10"] == 1.0  # no index below 10,000 answers: exact both times
    assert vector_value_type(ev._table().schema) == pa.float16()
    assert ev.search(vecs[123], 1)[0][:2] == (12, 3)


def test_a_search_that_fails_once_is_tried_again():
    from hansard.index.lance import retry_once

    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("Not found: vectors.lance/data/x.lance")
        return ["ok"]

    assert retry_once(flaky) == ["ok"] and len(calls) == 2


def test_compare_hits():
    a = [[(("p1",), 0.9), (("p2",), 0.8), (("p3",), 0.7)]]
    b = [[(("p2",), 0.8001), (("p1",), 0.9), (("p4",), 0.6)]]
    out = float16.compare_hits(a, b)
    assert out["overlap_at_10"] == pytest.approx(2 / 3, abs=1e-4)
    assert out["mean_rank_change_top_10"] == 1.0
    assert out["max_similarity_difference"] == pytest.approx(1e-4, abs=1e-6)


def test_the_command_converts_both_tables_under_the_update_lock(settings, monkeypatch):
    import json

    from typer.testing import CliRunner

    from hansard import cli
    from hansard.sync import evidence_index_path, update_lock

    monkeypatch.setattr(cli, "_settings", lambda: settings)
    dim = int(settings.embedding["dim"])
    idx = search_index(settings.index_dir, IndexMeta("an-embedding-model", dim, number_type="float16"))
    idx.db.create_table("vectors", schema=vector_schema(dim, pa.float32()))
    rng = np.random.default_rng(5)
    idx.add_vectors([], _rows(300), rng.standard_normal((300, dim)).astype(np.float32))
    ev = EvidenceVectors(evidence_index_path(settings), dim)
    ev.db.create_table("turns", schema=pa.schema([("session_id", pa.int64()), ("ord", pa.int32()),
                                                   ("vector", pa.list_(pa.float32(), dim))]))
    ev.add([], [{"session_id": 1, "ord": i} for i in range(20)], rng.standard_normal((20, dim)).astype(np.float32))

    with update_lock(settings):
        busy = CliRunner().invoke(cli.app, ["vectors-f16"])
    assert busy.exit_code == 1 and "An update is running" in busy.output

    dry = CliRunner().invoke(cli.app, ["vectors-f16", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert vector_value_type(idx.vectors().schema) == pa.float32()

    res = CliRunner().invoke(cli.app, ["vectors-f16"])
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout[res.stdout.index("{"):])
    assert out["passages"]["status"] == "converted" and out["committee evidence"]["status"] == "converted"
    assert out["passages"]["checks"]["searches"]["overlap_at_10"] == 1.0  # (the hashing embedder, no index)
    assert vector_value_type(idx.vectors().schema) == pa.float16()
    assert vector_value_type(ev._table().schema) == pa.float16()


class _Embedder:
    def embed_query(self, text: str) -> np.ndarray:
        v = np.random.default_rng(abs(hash(text)) % 2**32).standard_normal(DIM)
        return v / np.linalg.norm(v)


def test_the_conversion_measures_its_new_index_and_records_it(tmp_path):
    from hansard.index.retune import read_record

    _old_index(tmp_path)
    target = float16.passages_target(tmp_path, DIM, 1024, {"refine_factor": 5}, embedder=_Embedder())
    out = float16.convert(target, queries=list(_vectors(10, seed=1)), commit_rows=5000)
    assert out["status"] == "converted", out
    tuning = out["tuning"]
    assert tuning["vectors"] == ROWS and tuning["partitions"] == 11
    assert tuning["before"] == {**tuning["before"], "nprobes": None, "refine_factor": 5}
    assert [(t["nprobes"], t["refine_factor"]) for t in tuning["trials"]][:3] == [(20, None), (20, 5), (20, 10)]
    rec = tuning["recommended"]
    assert rec["recall"] >= tuning["before"]["recall"] - 0.02
    assert out["checks"]["searches"]["overlap_at_10"] >= 0.9  # the new table searched with its recommended settings
    record = read_record(tmp_path)
    assert out["record"] == str(tmp_path / "vector-index.json")
    assert record["by"] == "vectors-f16" and record["vectors"] == ROWS and record["rows_per_partition"] == 1024
    assert (record["nprobes"], record["refine_factor"], record["recall"]) == (rec["nprobes"], rec["refine_factor"],
                                                                              rec["recall"])


def test_a_new_index_that_finds_fewer_is_not_swapped(tmp_path):
    from hansard.index.retune import read_record

    _old_index(tmp_path)
    target = float16.passages_target(tmp_path, DIM, 1024, {"refine_factor": 5}, embedder=_Embedder(),
                                     tolerance=-1.0)  # nothing can beat the old index by more than everything
    out = float16.convert(target, commit_rows=5000)
    assert out["refused"] and "the index check failed" in out["status"] and "recommended" not in out["tuning"]
    assert vector_value_type(search_index(tmp_path, EMBEDDER).vectors().schema) == pa.float32()
    assert read_record(tmp_path) is None


def test_the_command_saves_the_measured_settings(settings, monkeypatch):
    import json

    from typer.testing import CliRunner

    from hansard import cli
    from hansard.config import load_settings

    monkeypatch.setattr(cli, "_settings", lambda: settings)
    dim = int(settings.embedding["dim"])
    idx = search_index(settings.index_dir, IndexMeta("an-embedding-model", dim, number_type="float16"))
    idx.db.create_table("vectors", schema=vector_schema(dim, pa.float32()))
    vecs = np.random.default_rng(6).standard_normal((3000, dim)).astype(np.float32)
    idx.add_vectors([], _rows(3000), vecs)
    idx.rebuild_vector_index(256)
    res = CliRunner().invoke(cli.app, ["vectors-f16", "--only", "passages", "--rows-per-partition", "256"])
    assert res.exit_code == 0, res.output
    out, end = json.JSONDecoder().raw_decode(res.stdout[res.stdout.index("{"):])
    passages = out["passages"]
    assert passages["status"] == "converted" and passages["record"]
    rec = passages["tuning"]["recommended"]
    assert "Restart the app" in res.stdout[res.stdout.index("{") + end:]
    ranking = load_settings(**{"embedding.backend": "hashing", "embedding.dim": 64}).ranking
    assert ranking["vector_nprobes"] == rec["nprobes"]
    assert ranking["vector_refine_factor"] == (rec["refine_factor"] or 0)
