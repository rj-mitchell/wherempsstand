"""Search index: LanceDB tables for BM25 keyword search and vector search.

Two tables share the same filter columns:

* ``passages``  every searchable passage, with a BM25 full-text index on
  ``search_text``. Built straight after ingest, so keyword search is available
  immediately.
* ``vectors``   one embedding per passage. Filled by the (slower, resumable)
  embedding job; hybrid search uses whatever is embedded so far. Stored as 16-bit
  floats (half the disk of 32-bit, with no measurable change to what is found:
  docs/DECISIONS.md); a table made before that keeps its 32-bit vectors until
  ``hansard vectors-f16`` converts it (hansard/index/float16.py), and both work.

Rows are keyed by ``source_key`` (one scrapedxml file), so a corrected
Hansard revision is applied by deleting that key's rows and adding the new
ones. Speaker columns (name, party, region, offices) come from the reference
data on the record's date and are refreshed per person when member data changes.

The tables themselves (opening, replacing a key's rows, adding vectors, building and merging the indexes, searching
by words and by meaning) are the Where They Stand sites' shared index (wts-core, ``wts_core.adapters.lancedb``),
opened here with this site's columns by ``search_index``. It also records beside the tables which embedding model
their vectors were made with (``wts_index.json``), and refuses to be opened for another, so vectors of two models
are never mixed. What is this site's own is here: the columns, ``similarities`` and ``ensure_portfolio_indices``
(functions over the index), and the number types of vectors, which the committee evidence's own table
(hansard/index/evidence.py) and the 16-bit conversion (hansard/index/float16.py) use as well.
"""

from __future__ import annotations

import os
from pathlib import Path

# LanceDB's own warnings (one per cluster while a vector index is trained on few rows, as the evidence index's first
# ones are) would fill the update logs: only its errors are shown. Read once, when LanceDB is first imported, so
# hansard/index/evidence.py sets it too. LANCEDB_LOG=warn (or info) in the environment brings them back.
os.environ.setdefault("LANCEDB_LOG", "error")

import numpy as np  # noqa: E402
import pyarrow as pa  # noqa: E402
from wts_core.adapters.lancedb import (  # noqa: E402
    KEEP_OLD_VERSIONS,
    IndexSchema,
    LanceIndex,
    ScalarColumns,
    date_lit,
    retry_once,
    sanitise_query,
    sql_list,
    sql_str,
)
from wts_core.ports import Embedder, IndexMeta, IndexOutOfDate  # noqa: E402

__all__ = [
    "FILTER_FIELDS", "KEEP_OLD_VERSIONS", "PASSAGES", "PASSAGE_SCHEMA", "PORTFOLIO_COLUMNS", "VECTORS",
    "VECTOR_TYPE", "IndexOutOfDate", "LanceIndex", "date_lit", "ensure_portfolio_indices", "query_vector",
    "retry_once", "sanitise_query", "search_index", "similarities", "sql_list", "sql_str", "vector_array",
    "vector_schema", "vector_value_type",
]

PASSAGES = "passages"
VECTORS = "vectors"

FILTER_FIELDS = [
    ("passage_id", pa.string()),
    ("record_id", pa.string()),
    ("source", pa.string()),
    ("source_key", pa.string()),
    ("record_type", pa.string()),
    ("house", pa.string()),
    ("date", pa.date32()),
    ("year", pa.int32()),
    ("person_id", pa.string()),
    ("party_id", pa.string()),
    ("region", pa.string()),
    ("is_gov", pa.bool_()),
    ("is_shadow", pa.bool_()),
    ("is_chair", pa.bool_()),
    ("gov_portfolios", pa.list_(pa.string())),
    ("shadow_portfolios", pa.list_(pa.string())),
    ("chair_portfolios", pa.list_(pa.string())),
]

PORTFOLIO_COLUMNS = ("gov_portfolios", "shadow_portfolios", "chair_portfolios")

PASSAGE_FIELDS = [
    ("chunk", pa.int32()),
    ("chamber", pa.string()),
    ("speaker_name", pa.string()),
    ("party", pa.string()),
    ("constituency", pa.string()),
    ("member_house", pa.string()),
    ("heading", pa.string()),
    ("text", pa.string()),
    ("search_text", pa.string()),
]

PASSAGE_SCHEMA = pa.schema([*FILTER_FIELDS, *PASSAGE_FIELDS])

# This site's columns, as the shared index takes them. ``version`` changes when the columns do.
SCHEMA = IndexSchema(filter_fields=FILTER_FIELDS, passage_fields=PASSAGE_FIELDS, version=1)
# Indices that let filtered (prefilter) searches skip non-matching rows.
SCALAR_COLUMNS = ScalarColumns(
    btree=("passage_id", "source_key", "record_id", "person_id", "date"),
    bitmap=("source", "house", "record_type", "region", "party_id", "is_gov", "is_shadow", "is_chair", "year"),
    label_list=PORTFOLIO_COLUMNS)

# New vector tables store 16-bit floats. Embeddings are computed in 32 bits and rounded when written; a query is
# given in the table's own type.
VECTOR_TYPE = pa.float16()


def search_index(path: Path, embedder: Embedder | IndexMeta | None = None, vectors_name: str = VECTORS) -> LanceIndex:
    """The site's search index in ``path``.

    ``embedder`` is the embedding model whose vectors are to be written or searched; the index must have been built
    with the same, or ``IndexOutOfDate`` is raised (``hansard embed --start-again`` then makes the vectors again
    with the new one). Without one the index is opened as it stands, whatever it was built with: for adding
    passages, search by words, building and merging the indexes, counts and searches of the vectors there are. Such
    an index adds no vectors. ``vectors_name`` is another name only while hansard/index/float16.py builds a new
    table."""
    return LanceIndex(Path(path), embedder, schema=SCHEMA, scalar_columns=SCALAR_COLUMNS, passages=PASSAGES,
                      vectors=vectors_name, number_type="float16")


def vector_schema(dim: int, value_type: pa.DataType = VECTOR_TYPE) -> pa.Schema:
    return pa.schema([*FILTER_FIELDS, ("vector", pa.list_(value_type, dim))])


def vector_value_type(schema: pa.Schema, column: str = "vector") -> pa.DataType:
    """The number type of a table's vectors: float32 (tables made before November 2026) or float16."""
    return schema.field(column).type.value_type


def vector_array(vectors: np.ndarray, value_type: pa.DataType) -> pa.FixedSizeListArray:
    """Embeddings (rows of 32-bit floats) as a vector column of the given number type."""
    vectors = np.asarray(vectors)
    flat = pa.array(vectors.astype(value_type.to_pandas_dtype()).reshape(-1), type=value_type)
    return pa.FixedSizeListArray.from_arrays(flat, vectors.shape[1])


def query_vector(vec: np.ndarray, value_type: pa.DataType) -> np.ndarray:
    """A query embedding in the table's own number type."""
    return np.asarray(vec, dtype=np.float32).astype(value_type.to_pandas_dtype())


def ensure_portfolio_indices(index: LanceIndex) -> bool:
    """Indices on the passages' portfolios, for the search of a subject's frontbench (added after the first
    build, so an existing index gets them once). Returns True if they had to be built."""
    return index.ensure_scalar_indexes(PORTFOLIO_COLUMNS)


def similarities(index: LanceIndex, vec: np.ndarray, passage_ids: list[str]) -> dict[str, float]:
    """Closeness in meaning (cosine similarity, as the search by meaning gives it) of these passages to the query,
    from their stored vectors."""
    if not passage_ids or index.vectors_name not in index.names():
        return {}
    rows = retry_once(lambda: index.vectors().search()
                      .where(f"passage_id IN ({', '.join(sql_str(p) for p in passage_ids)})")
                      .select(["passage_id", "vector"]).limit(None).to_list())
    q = np.asarray(vec, dtype=np.float32)
    q = q / (np.linalg.norm(q) or 1.0)
    out = {}
    for row in rows:
        v = np.asarray(row["vector"], dtype=np.float32)
        n = float(np.linalg.norm(v))
        if n:
            out[row["passage_id"]] = float(v @ q) / n
    return out
