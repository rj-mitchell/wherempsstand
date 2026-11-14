"""Does storing the meaning-search vectors as 16-bit floats change what is found? A retrieval-only comparison: no
model is called, nothing is paid for, and the index given is only read.

From the vectors of an existing index (32-bit, as made before November 2026), three tables are built in a temporary
folder from the same embeddings: two 32-bit tables and one 16-bit table, each indexed as ``hansard reindex-vectors``
indexes the site's. The same searches run on each, with the site's settings: the test set's questions
(eval/questions.toml), the sample searches of hansard/index/retune.py, and a few hundred headings taken from the
index itself (a fixed sample, so a second run gives the same numbers). It reports, for 16-bit against 32-bit:

* exact searches (every vector compared, no index): the share of the nearest 10 and 300 found by both, how far the
  nearest 10 moved in rank, and how far each passage's similarity changed. This is the effect of 16 bits alone.
* indexed searches, as the site makes them: the same measures, beside the same comparison between the two 32-bit
  tables (whose indices are trained separately), which is how far results move with no change at all; and the share
  of the exact nearest 300 that each finds.

Usage (from the repository, with the local embedding model the index was made with):

    HANSARD_DATA_DIR=/path/to/data uv run python scripts/compare_float16.py [--headings 300] [--out results.json]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pyarrow as pa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hansard.adapters import get_embedder  # noqa: E402
from hansard.config import load_settings  # noqa: E402
from hansard.evaluate import load_questions  # noqa: E402
from hansard.index.float16 import compare_hits  # noqa: E402
from hansard.index.lance import (  # noqa: E402
    FILTER_FIELDS,
    LanceIndex,
    search_index,
    vector_array,
    vector_schema,
)
from hansard.index.retune import SAMPLE_QUERIES  # noqa: E402


def read_vectors(idx: LanceIndex) -> pa.Table:
    tbl = idx.vectors()
    if tbl.schema.field("vector").type.value_type != pa.float32():
        raise SystemExit("this index's vectors are already 16-bit: give it one made with 32-bit vectors")
    return tbl.search().select([name for name, _ in FILTER_FIELDS] + ["vector"]).limit(None).to_arrow()


def build(path: Path, name: str, rows: pa.Table, value_type: pa.DataType, dim: int,
          rows_per_partition: int) -> LanceIndex:
    vecs = rows.column("vector").combine_chunks().flatten().to_numpy().reshape(-1, dim)
    cols = {n: rows.column(n) for n, _ in FILTER_FIELDS}
    cols["vector"] = vector_array(vecs, value_type)
    search_index(path).db.create_table(name, data=pa.table(cols, schema=vector_schema(dim, value_type)))
    idx = search_index(path, vectors_name=name)  # opened once its table is there, so it knows the vectors' size
    if rows.num_rows >= 10_000:
        idx.rebuild_vector_index(rows_per_partition)
    return idx


def headings(idx: LanceIndex, n: int) -> list[str]:
    rows = idx.passages().search().select(["heading"]).limit(None).to_arrow().column("heading").to_pylist()
    distinct = sorted({h.strip() for h in rows if h and len(h.split()) >= 3})
    return random.Random(0).sample(distinct, min(n, len(distinct)))


def search_all(idx: LanceIndex, queries: list[np.ndarray], limit: int, exact: bool, nprobes, refine):
    out = []
    for q in queries:
        hits = idx.search_meaning(q, None, limit, nprobes=nprobes, refine_factor=refine, columns=["passage_id"],
                          exact=exact)
        out.append([((h["passage_id"],), 1.0 - float(h["_distance"])) for h in hits])
    return out


def recall(truth, found) -> float:
    shares = [len({k for k, _ in f} & {k for k, _ in t}) / len(t) for t, f in zip(truth, found, strict=True) if t]
    return round(sum(shares) / len(shares), 4) if shares else 1.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--headings", type=int, default=300, help="headings from the index to search for as well")
    ap.add_argument("--limit", type=int, default=300, help="results per search (the site takes 300)")
    ap.add_argument("--rows-per-partition", type=int, default=8192)
    ap.add_argument("--out", type=Path, help="also write the results here as JSON")
    args = ap.parse_args()

    settings = load_settings()
    dim = int(settings.embedding.get("dim", 384))
    nprobes = settings.ranking.get("vector_nprobes")
    refine = settings.ranking.get("vector_refine_factor")
    live = search_index(settings.index_dir)
    rows = read_vectors(live)
    vecs = rows.column("vector").combine_chunks().flatten().to_numpy().reshape(-1, dim)
    rounding = float(np.max(np.abs(vecs - vecs.astype(np.float16).astype(np.float32))))

    kinds = {"sample searches": [q for q, _ in SAMPLE_QUERIES],
             "test set questions": [q.question for q in load_questions(ROOT / "eval" / "questions.toml")[1]],
             "headings": headings(live, args.headings)}
    texts = [t for group in kinds.values() for t in group]
    embedder = get_embedder(settings)
    queries = [embedder.embed_query(t) for t in texts]

    with tempfile.TemporaryDirectory() as tmp:
        t0 = time.time()
        a = build(Path(tmp), "f32", rows, pa.float32(), dim, args.rows_per_partition)
        a2 = build(Path(tmp), "f32_again", rows, pa.float32(), dim, args.rows_per_partition)
        b = build(Path(tmp), "f16", rows, pa.float16(), dim, args.rows_per_partition)
        built = round(time.time() - t0)
        sizes = {n: sum(f.stat().st_size for f in (Path(tmp) / f"{n}.lance").rglob("*") if f.is_file())
                 for n in ("f32", "f16")}
        exact_a = search_all(a, queries, args.limit, True, None, None)
        exact_b = search_all(b, queries, args.limit, True, None, None)
        site_a = search_all(a, queries, args.limit, False, nprobes, refine)
        site_a2 = search_all(a2, queries, args.limit, False, nprobes, refine)
        site_b = search_all(b, queries, args.limit, False, nprobes, refine)

    result = {
        "vectors": rows.num_rows, "dimensions": dim, "queries": len(queries),
        "query_kinds": {k: len(v) for k, v in kinds.items()},
        "settings": {"nprobes": nprobes, "refine_factor": refine, "limit": args.limit},
        "largest_rounding_of_a_stored_number": rounding,
        "table_mb": {k: round(v / 1024 ** 2, 1) for k, v in sizes.items()},
        "exact_16_vs_32": compare_hits(exact_a, exact_b),
        "indexed_16_vs_32": compare_hits(site_a, site_b),
        "indexed_32_vs_32_rebuilt": compare_hits(site_a, site_a2),
        "share_of_exact_nearest_found": {"32-bit": recall(exact_a, site_a), "32-bit rebuilt": recall(exact_a, site_a2),
                                         "16-bit": recall(exact_a, site_b)},
        "build_seconds": built,
    }
    print(json.dumps(result, indent=1))
    if args.out:
        args.out.write_text(json.dumps(result, indent=1) + "\n")


if __name__ == "__main__":
    main()
