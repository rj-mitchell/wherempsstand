"""Convert a table of meaning-search vectors from 32-bit to 16-bit floats: half the disk, the same passages found.

Tables made before November 2026 store each vector as 32-bit floats; new ones store 16-bit floats (decision 26 in
docs/DECISIONS.md). ``hansard vectors-f16`` converts an existing table, for the passages (``index/vectors``) and the
committee evidence (``evidence_index/turns``), while the site keeps searching the old one:

1. **copy**   the old table is read in batches and written to a new table beside it (``<name>_f16``), the vectors
              rounded to 16 bits and every other column as it was, committed about a million rows at a time. A
              stopped copy carries on where it stopped, unless the old table has changed since, when it starts again.
2. **index**  the new table gets the indices the old one has: the meaning-search index built as
              ``hansard reindex-vectors`` builds it, and the same scalar indices.
3. **check**  the same number of rows; a sample of vectors, looked up by key in both, differing by no more than
              16-bit rounding; and the same searches (the sample searches and the test set's questions) run on both
              tables, finding the same passages. Nothing is swapped if any check fails.
4. **swap**   the two tables' folders are exchanged in one step (a single rename), so the next search opens the new
              table; a search that had opened the old one just before is run again (``retry_once`` in lance.py).
5. **clean**  the old table is deleted, or kept as ``<name>_f32`` with ``keep_old``; the next run deletes it.

The caller holds the update lock, so nothing writes to either table meanwhile.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import json
import logging
import os
import random
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa

from hansard.index.lance import KEEP_OLD_VERSIONS, query_vector, sql_str, vector_value_type

log = logging.getLogger(__name__)

GB = 1024 ** 3
COMMIT_ROWS = 1024 * 1024  # Lance's own file size: each commit is one full file, so nothing needs compacting after
BATCH_ROWS = 32 * 1024     # rows read and converted at a time (about 50 MB of 32-bit vectors at 384 dimensions)
MIN_FREE_AFTER = 5 * GB    # free space left on the disk once the new table is written, for everything else
MAX_ROUNDING = 1e-3        # 16-bit floats keep about 3 significant figures: rounding moves a number by under
                           # 0.0005 of its size (under 0.0005 outright below 1, where an embedding's numbers are)
SAMPLE_ROWS = 2000
MIN_OVERLAP_10 = 0.9       # share of the 10 nearest the new table must find, on average, of the old table's 10


@dataclass
class Target:
    """One table of vectors, and how to index and search it."""
    label: str
    path: Path                                  # the LanceDB folder
    name: str                                   # the table
    key: tuple[str, ...]                        # columns that identify a row
    build_index: Callable[[Any], None]          # builds the new table's meaning-search index
    search_options: dict[str, Any] = field(default_factory=dict)  # nprobes and refine_factor, as the site searches

    @property
    def new_name(self) -> str:
        return f"{self.name}_f16"

    @property
    def kept_name(self) -> str:
        return f"{self.name}_f32"

    def folder(self, name: str) -> Path:
        return self.path / f"{name}.lance"

    @property
    def progress_file(self) -> Path:
        return self.path / f"{self.new_name}.progress.json"


# ---- sizes ---------------------------------------------------------------------------------------------------------


def folder_bytes(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            with contextlib.suppress(OSError):  # a file removed while counting
                total += os.lstat(os.path.join(root, f)).st_size
    return total


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def gb(n: float) -> float:
    return round(n / GB, 2)


def estimate_bytes(tbl: Any, rows: int) -> int:
    """The new table's likely size: 2 bytes per number, the other columns as a sample of rows takes them, and the
    meaning-search index (its compressed codes and row ids), with a tenth more for good measure."""
    dim = tbl.schema.field("vector").type.list_size
    others = [f.name for f in tbl.schema if f.name != "vector"]
    sample = tbl.search().select(others).limit(min(rows, 2000)).to_arrow() if others and rows else None
    per_row = (sample.nbytes / sample.num_rows) if sample is not None and sample.num_rows else 0
    return int(rows * (dim * 2 + per_row + max(1, dim // 8) + 16) * 1.1)


# ---- the tables ----------------------------------------------------------------------------------------------------


def _db(target: Target):
    import lancedb

    return lancedb.connect(str(target.path))


def _names(db) -> set[str]:
    res = db.list_tables()
    return set(getattr(res, "tables", res))


def f16_schema(schema: pa.Schema) -> pa.Schema:
    i = schema.get_field_index("vector")
    dim = schema.field(i).type.list_size
    return schema.set(i, pa.field("vector", pa.list_(pa.float16(), dim)))


def to_f16(batches, schema: pa.Schema):
    """Each batch with its vectors rounded to 16 bits, everything else as it was."""
    i = schema.get_field_index("vector")
    dim = schema.field(i).type.list_size
    for b in batches:
        flat = b.column(i).flatten().to_numpy(zero_copy_only=False).astype(np.float16)
        cols = list(b.columns)
        cols[i] = pa.FixedSizeListArray.from_arrays(pa.array(flat, type=pa.float16()), dim)
        yield pa.RecordBatch.from_arrays(cols, schema=schema)


def _read_progress(target: Target) -> dict[str, Any]:
    try:
        return json.loads(target.progress_file.read_text())
    except (OSError, ValueError):
        return {}


def copy(target: Target, src, db, batch_rows: int = BATCH_ROWS, commit_rows: int = COMMIT_ROWS,
         progress: Callable[[str], None] = log.info) -> dict[str, Any]:
    """Write the new table from the old one, carrying on from an earlier run if the old table is unchanged since."""
    rows, version = src.count_rows(), src.version
    schema = f16_schema(src.schema)
    state = _read_progress(target)
    restarted = False
    if target.new_name in _names(db):
        same = state.get("source_version") == version and state.get("source_rows") == rows
        dst = db.open_table(target.new_name)
        if not same or dst.count_rows() > rows or dst.schema.names != schema.names \
                or vector_value_type(dst.schema) != pa.float16():
            progress(f"{target.label}: the table has changed since the last run; copying it again from the start")
            db.drop_table(target.new_name)
            restarted = True
    if target.new_name not in _names(db):
        db.create_table(target.new_name, schema=schema)
        target.progress_file.write_text(json.dumps({"source_version": version, "source_rows": rows}))
    dst = db.open_table(target.new_name)
    start = done = dst.count_rows()
    t0 = time.time()
    columns = [f.name for f in src.schema]
    while done < rows:
        n = min(commit_rows, rows - done)
        reader = src.search().select(columns).offset(done).limit(n).to_batches(batch_rows)
        dst.add(pa.RecordBatchReader.from_batches(schema, to_f16(reader, schema)))
        now = dst.count_rows()
        if now != done + n:
            raise RuntimeError(f"{target.label}: expected {done + n:,} rows in the new table after this batch, "
                               f"found {now:,}")
        done = now
        progress(f"{target.label}: copied {done:,} of {rows:,} rows ({round(time.time() - t0)} s)")
    return {"rows": rows, "copied_now": done - start, "resumed_from": start, "restarted": restarted,
            "seconds": round(time.time() - t0)}


def index(target: Target, src, dst, progress: Callable[[str], None] = log.info) -> dict[str, Any]:
    """Give the new table the indices the old one has (skipping any it already has, from an earlier run)."""
    from lancedb.index import Bitmap, BTree, LabelList

    configs = {"BTree": BTree, "Bitmap": Bitmap, "LabelList": LabelList}
    have = {tuple(ix.columns or []) for ix in dst.list_indices()}
    built, t0 = [], time.time()
    for ix in src.list_indices():
        cols = tuple(ix.columns or [])
        if cols in have or not cols:
            continue
        if cols == ("vector",):
            progress(f"{target.label}: building the meaning-search index (the longest step)")
            target.build_index(dst)
        elif str(ix.index_type) in configs:
            dst.create_index(cols[0], config=configs[str(ix.index_type)](), replace=True)
        else:
            raise RuntimeError(f"{target.label}: no way to copy the {ix.index_type} index on {cols[0]}")
        built.append(f"{cols[0]} ({ix.index_type})")
    return {"built": built, "seconds": round(time.time() - t0)}


def _keyed(rows: pa.Table, key: tuple[str, ...]) -> dict[tuple, np.ndarray]:
    keys = list(zip(*(rows.column(k).to_pylist() for k in key), strict=True))
    vecs = rows.column("vector").to_pylist()
    return {k: np.asarray(v, dtype=np.float32) for k, v in zip(keys, vecs, strict=True)}


def check_sample(target: Target, src, dst, sample: int = SAMPLE_ROWS) -> dict[str, Any]:
    """Look up a random sample of the old table's rows in the new one by key, and compare their vectors."""
    rows = src.count_rows()
    offsets = sorted(random.Random(16).sample(range(rows), min(sample, rows)))
    old = _keyed(src.take_offsets(offsets).select([*target.key, "vector"]).to_arrow(), target.key)
    first = target.key[0]
    new: dict[tuple, np.ndarray] = {}
    values = sorted({k[0] for k in old})
    for i in range(0, len(values), 500):
        part = values[i:i + 500]
        lits = [sql_str(v) if isinstance(v, str) else str(int(v)) for v in part]
        where = f"{first} IN ({', '.join(lits)})"
        found = dst.search().where(where).select([*target.key, "vector"]).limit(None).to_arrow()
        new.update({k: v for k, v in _keyed(found, target.key).items() if k in old})
    missing = [k for k in old if k not in new]
    diffs = [float(np.max(np.abs(old[k] - new[k]))) for k in old if k in new]
    # rounding relative to the size of each number (values below 1 count as 1)
    worst = [float(np.max(np.abs(old[k] - new[k]) / np.maximum(1.0, np.abs(old[k])))) for k in old if k in new]
    cosines = [float(old[k] @ new[k] / ((np.linalg.norm(old[k]) * np.linalg.norm(new[k])) or 1.0))
               for k in old if k in new]
    max_diff = max(diffs) if diffs else 0.0
    return {"sampled": len(old), "missing": len(missing), "max_abs_difference": max_diff,
            "lowest_cosine": round(min(cosines), 7) if cosines else 1.0,
            "ok": not missing and max(worst, default=0.0) <= MAX_ROUNDING}


def nearest(tbl, vec: np.ndarray, key: tuple[str, ...], limit: int, nprobes: int | None = None,
            refine_factor: int | None = None) -> list[tuple[tuple, float]]:
    """[(key, similarity)] of the nearest rows, as the site searches them."""
    search = tbl.search(query_vector(vec, vector_value_type(tbl.schema)), vector_column_name="vector") \
        .distance_type("cosine")
    if any("vector" in (ix.columns or []) for ix in tbl.list_indices()):
        if nprobes:
            search = search.nprobes(int(nprobes))
        if refine_factor:
            search = search.refine_factor(int(refine_factor))
    rows = search.select([*key, "_distance"]).limit(limit).to_list()
    return [(tuple(r[k] for k in key), 1.0 - float(r["_distance"])) for r in rows]


def compare_hits(old: list[list[tuple[tuple, float]]], new: list[list[tuple[tuple, float]]]) -> dict[str, Any]:
    """How far two sets of search results differ: the share of each query's nearest 10 (and of all its results)
    found by both, how far the nearest 10 moved in rank, and how far the similarity of a row found by both changed."""
    o10, oall, moves, dsim, same_order = [], [], [], [], 0
    for a, b in zip(old, new, strict=True):
        if not a:
            continue
        ka, kb = [k for k, _ in a], [k for k, _ in b]
        o10.append(len(set(ka[:10]) & set(kb[:10])) / min(10, len(ka)))
        oall.append(len(set(ka) & set(kb)) / len(ka))
        rank_b = {k: i for i, k in enumerate(kb)}
        moves += [abs(i - rank_b[k]) for i, k in enumerate(ka[:10]) if k in rank_b]
        sims_b = dict(b)
        dsim += [abs(s - sims_b[k]) for k, s in a if k in sims_b]
        same_order += ka[:10] == kb[:10]
    n = len(o10)
    return {"queries": n,
            "overlap_at_10": round(sum(o10) / n, 4) if n else 1.0,
            "lowest_overlap_at_10": round(min(o10), 2) if n else 1.0,
            "overlap_all": round(sum(oall) / n, 4) if n else 1.0,
            "same_top_10_in_same_order": same_order,
            "mean_rank_change_top_10": round(sum(moves) / len(moves), 3) if moves else 0.0,
            "mean_similarity_difference": round(sum(dsim) / len(dsim), 6) if dsim else 0.0,
            "max_similarity_difference": round(max(dsim), 6) if dsim else 0.0}


def check_searches(target: Target, src, dst, queries: list[np.ndarray], limit: int = 300) -> dict[str, Any]:
    """The same searches on the old table and the new, as the site makes them."""
    old = [nearest(src, q, target.key, limit, **target.search_options) for q in queries]
    new = [nearest(dst, q, target.key, limit, **target.search_options) for q in queries]
    out = compare_hits(old, new)
    out["ok"] = out["overlap_at_10"] >= MIN_OVERLAP_10
    return out


# ---- the swap ------------------------------------------------------------------------------------------------------

_RENAME_EXCHANGE = 2
_AT_FDCWD = -100


def exchange(a: Path, b: Path) -> bool:
    """Swap two folders in one step (Linux's renameat2 with RENAME_EXCHANGE). False if the system can't."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        fn = libc.renameat2
    except (OSError, AttributeError):
        return False
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    if fn(_AT_FDCWD, os.fsencode(a), _AT_FDCWD, os.fsencode(b), _RENAME_EXCHANGE) == 0:
        return True
    err = ctypes.get_errno()
    if err in (errno.EINVAL, errno.ENOSYS, errno.ENOTSUP, errno.EOPNOTSUPP):
        return False
    raise OSError(err, os.strerror(err), str(a))


def swap(target: Target) -> str:
    """Put the new table in the old one's place; the old one ends up as ``<name>_f16``. Returns how."""
    live, new = target.folder(target.name), target.folder(target.new_name)
    if exchange(new, live):
        return "exchanged in one step"
    # Without an atomic exchange: two renames, between which (for well under a millisecond) there is no table and
    # a search by meaning finds nothing, so the site answers from keyword search alone.
    parked = target.folder(f"{target.name}_swap")
    os.rename(live, parked)
    os.rename(new, live)
    os.rename(parked, new)
    return "renamed in two steps"


def remove_leftovers(target: Target, db, keep_old: bool) -> list[str]:
    """After a swap (or a run stopped after one): delete the old table, or keep it as ``<name>_f32``."""
    removed = []
    names = _names(db)
    if target.new_name in names and target.folder(target.new_name).exists():
        old = db.open_table(target.new_name)
        if vector_value_type(old.schema) == pa.float32():
            if keep_old:
                if target.folder(target.kept_name).exists():
                    shutil.rmtree(target.folder(target.kept_name))
                os.rename(target.folder(target.new_name), target.folder(target.kept_name))
            else:
                time.sleep(2)  # let any search that opened the old folder just before the swap finish
                shutil.rmtree(target.folder(target.new_name))
                removed.append(target.new_name)
    if not keep_old and target.folder(target.kept_name).exists():
        shutil.rmtree(target.folder(target.kept_name))
        removed.append(target.kept_name)
    target.progress_file.unlink(missing_ok=True)
    return removed


# ---- the whole conversion ------------------------------------------------------------------------------------------


def convert(target: Target, *, dry_run: bool = False, keep_old: bool = False, force: bool = False,
            queries: list[np.ndarray] | None = None, min_free_after: int = MIN_FREE_AFTER,
            commit_rows: int = COMMIT_ROWS, progress: Callable[[str], None] = log.info) -> dict[str, Any]:
    """Convert one table (see the module's description). Safe to run again: each step carries on from the last."""
    out: dict[str, Any] = {"table": str(target.folder(target.name))}
    db = _db(target)
    if target.name not in _names(db):
        out["status"] = "no table yet: it will be made with 16-bit vectors"
        return out
    src = db.open_table(target.name)
    rows = src.count_rows()
    out["rows"] = rows
    out["gb_before"] = gb(folder_bytes(target.path))
    out["free_gb_before"] = gb(free_bytes(target.path))
    if vector_value_type(src.schema) == pa.float16():
        out["status"] = "already 16-bit"
        if not dry_run:
            out["removed"] = remove_leftovers(target, db, keep_old)
            out["gb_after"] = gb(folder_bytes(target.path))
        return out

    estimate = estimate_bytes(src, rows)
    written = folder_bytes(target.folder(target.new_name)) if target.folder(target.new_name).exists() else 0
    needed = max(0, estimate - written) + min_free_after
    out["new_table_gb_estimate"] = gb(estimate)
    out["needs_free_gb"] = gb(needed)
    if out["free_gb_before"] < gb(needed):
        out["status"] = (f"refused: {out['free_gb_before']} GB free, and this needs about {gb(needed)} GB "
                         f"({gb(estimate)} GB for the new table while the old one is still there, and "
                         f"{gb(min_free_after)} GB left over). Free some space first (docs/HOSTING.md, 'Disk space').")
        out["refused"] = True
        return out
    if dry_run:
        out["status"] = "dry run: nothing changed"
        out["likely_gb_saved"] = gb(max(0, folder_bytes(target.folder(target.name)) - estimate))
        return out

    out["copy"] = copy(target, src, db, commit_rows=commit_rows, progress=progress)
    dst = db.open_table(target.new_name)
    out["index"] = index(target, src, dst, progress=progress)
    dst = db.open_table(target.new_name)
    progress(f"{target.label}: checking the new table")
    checks: dict[str, Any] = {"rows_old": rows, "rows_new": dst.count_rows()}
    checks["rows_ok"] = checks["rows_old"] == checks["rows_new"]
    checks["sample"] = check_sample(target, src, dst)
    if queries:
        checks["searches"] = check_searches(target, src, dst, queries)
    out["checks"] = checks
    failed = [name for name, ok in (("rows", checks["rows_ok"]), ("sample", checks["sample"]["ok"]),
                                    ("searches", checks.get("searches", {}).get("ok", True))) if not ok]
    if failed and not (force and failed == ["searches"]):
        out["status"] = (f"not swapped: the {' and '.join(failed)} check failed. The site still uses the old table; "
                         f"the new one is kept, so a second run does not copy it again.")
        out["refused"] = True
        return out
    # nothing may have been written to the old table since the copy began (the update lock sees to that)
    state = _read_progress(target)
    src = db.open_table(target.name)
    if state.get("source_version") != src.version:
        out["status"] = "not swapped: the old table changed during the copy (was the update lock held?). Run again."
        out["refused"] = True
        return out

    out["swap"] = swap(target)
    progress(f"{target.label}: the site now searches the 16-bit table ({out['swap']})")
    out["removed"] = remove_leftovers(target, db, keep_old)
    live = db.open_table(target.name)
    live.optimize(cleanup_older_than=KEEP_OLD_VERSIONS)
    out["status"] = "converted" + (f"; the old table is kept as {target.kept_name}" if keep_old else "")
    out["gb_after"] = gb(folder_bytes(target.path))
    out["free_gb_after"] = gb(free_bytes(target.path))
    out["gb_saved"] = round(out["gb_before"] - out["gb_after"], 2)
    return out


def passages_target(index_dir: Path, dim: int, rows_per_partition: int = 8192,
                    search_options: dict[str, Any] | None = None) -> Target:
    from hansard.index.lance import VECTORS, SearchIndex

    def build(dst) -> None:  # as hansard reindex-vectors builds it
        SearchIndex(index_dir, dim, vectors_name=dst.name).rebuild_vector_index(rows_per_partition)

    return Target("passages", Path(index_dir), VECTORS, ("passage_id",), build, search_options or {})


def evidence_target(path: Path) -> Target:
    from hansard.index.evidence import TABLE, build_index

    return Target("committee evidence", Path(path), TABLE, ("session_id", "ord"), build_index,
                  {"nprobes": 20, "refine_factor": 5})

