from datetime import date

import numpy as np
import pytest

from hansard.index.lance import SearchIndex
from hansard.index.retune import SAMPLE_QUERIES, rebuild, recall

DIM = 16


class FakeEmbedder:
    def embed_query(self, text: str) -> np.ndarray:
        rng = np.random.default_rng(abs(hash(text)) % 2**32)
        v = rng.normal(size=DIM)
        return v / np.linalg.norm(v)


@pytest.fixture
def idx(tmp_path):
    idx = SearchIndex(tmp_path / "index", DIM)
    rng = np.random.default_rng(1)
    n = 12_000
    rows = [{"passage_id": f"p{i}", "record_id": f"r{i}", "source": "debates", "source_key": "k", "record_type": "speech",
             "house": "commons" if i % 2 else "lords", "date": date(2021, 1, 1), "year": 2021, "person_id": None,
             "party_id": None, "region": None, "is_gov": bool(i % 3 == 0), "is_shadow": False, "is_chair": False,
             "gov_portfolios": [], "shadow_portfolios": [], "chair_portfolios": []} for i in range(n)]
    vecs = rng.normal(size=(n, DIM))
    idx.add_vectors([], rows, vecs / np.linalg.norm(vecs, axis=1, keepdims=True))
    idx.rebuild_vector_index(rows_per_partition=4000)  # the "old" index: few large partitions
    return idx


def test_recall():
    assert recall([["a", "b"], ["c", "d"]], [["a", "x"], ["c", "d"]]) == 0.75
    assert recall([[]], [[]]) == 1.0


def test_rebuild_keeps_an_index_that_finds_the_same_passages(idx):
    out = rebuild(idx, FakeEmbedder(), rows_per_partition=1000, probes=(20, 40), refines=(None, 5), limit=50,
                  tolerance=1.0)
    assert out["kept"] and out["recommended"]["nprobes"] in (20, 40)
    assert 0 <= out["before"]["recall"] <= 1
    assert [(t["nprobes"], t["refine_factor"]) for t in out["trials"]] == [(20, None), (20, 5), (40, None), (40, 5)]
    assert len(idx.vector(FakeEmbedder().embed_query(SAMPLE_QUERIES[0][0]), None, 10)) == 10


def test_rebuild_puts_the_old_index_back_when_results_differ(idx):
    version = idx.vectors().version
    out = rebuild(idx, FakeEmbedder(), rows_per_partition=1000, probes=(1,), refines=(None,), limit=50,
                  tolerance=-1.0)
    assert not out["kept"] and "put back" in out["note"]
    assert idx.vectors().version > version  # restoring writes a new version equal to the old one
    assert len(idx.vector(FakeEmbedder().embed_query("x"), None, 10)) == 10


def test_a_kept_rebuild_is_recorded_and_one_put_back_is_not(idx):
    from hansard.index.retune import read_record

    assert read_record(idx.path) is None
    out = rebuild(idx, FakeEmbedder(), rows_per_partition=1000, probes=(20,), refines=(5,), limit=50, tolerance=1.0)
    record = read_record(idx.path)
    assert out["record"] == str(idx.path / "vector-index.json")
    assert record["by"] == "reindex-vectors" and record["vectors"] == 12_000
    assert record["partitions"] == 12 and record["rows_per_partition"] == 1000
    assert (record["nprobes"], record["refine_factor"]) == (20, 5)
    assert record["recall"] == out["trials"][0]["recall"] and record["recall_before"] == out["before"]["recall"]
    assert record["built"][:4].isdigit()

    rebuild(idx, FakeEmbedder(), rows_per_partition=2000, probes=(1,), refines=(None,), limit=50, tolerance=-1.0)
    assert read_record(idx.path) == record  # the old index was put back: still the one recorded


def test_index_status_says_when_to_rebuild(tmp_path):
    from hansard.index.retune import record_path, status

    none = status(tmp_path, 1000)
    assert none["rebuild_due"] is None and "no record" in none["reminder"]
    assert "hansard reindex-vectors once meaning-based search has caught up" in none["reminder"]
    record_path(tmp_path).write_text('{"built": "2026-11-07T12:00:00+00:00", "vectors": 7887156}')
    small = status(tmp_path, 8_100_000)
    assert small["rebuild_due"] is False and small["growth"] == 0.027
    assert small["reminder"] == ("Meaning-search index: 8,100,000 passages now, 7,887,156 when it was last rebuilt "
                                 "(7 November 2026), 3% more. Rebuild it at 20% more (9,464,587 passages).")
    due = status(tmp_path, 9_500_000)
    assert due["rebuild_due"] is True and "rebuild it with hansard reindex-vectors" in due["reminder"]
    assert "no passages" in status(tmp_path, 0)["reminder"]


def test_the_index_status_command(settings, monkeypatch):
    import json

    from typer.testing import CliRunner

    from hansard import cli
    from hansard.index.retune import record_path

    monkeypatch.setattr(cli, "_settings", lambda: settings)
    empty = CliRunner().invoke(cli.app, ["index-status", "--text"])
    assert empty.exit_code == 0 and "no passages" in empty.output
    idx = SearchIndex(settings.index_dir, 64)
    rows = [{"passage_id": f"p{i}", "record_id": f"r{i}", "source": "debates", "source_key": "k",
             "record_type": "speech", "house": "commons", "date": date(2021, 1, 1), "year": 2021, "person_id": None,
             "party_id": None, "region": None, "is_gov": False, "is_shadow": False, "is_chair": False,
             "gov_portfolios": [], "shadow_portfolios": [], "chair_portfolios": []} for i in range(30)]
    idx.add_vectors([], rows, np.ones((30, 64)))
    record_path(settings.index_dir).write_text('{"built": "2026-11-07T12:00:00+00:00", "vectors": 20}')
    res = CliRunner().invoke(cli.app, ["index-status"])
    out = json.loads(res.output)
    assert out["vectors"] == 30 and out["growth"] == 0.5 and out["rebuild_due"] is True
    assert out["last_rebuild"]["vectors"] == 20
