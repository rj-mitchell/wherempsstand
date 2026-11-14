"""Rebuild the meaning-search index for the number of vectors there are now, checking it finds as much as before."""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from hansard.index.lance import LanceIndex

PROBES = (20, 40, 80)
REFINES = (None, 5, 10)
RECORD = "vector-index.json"  # beside the index (index_dir): the last rebuild, written by write_record
REINDEX_GROWTH = 0.2          # the monthly report says to rebuild once there are this share more vectors

# A spread of subjects, with and without filters, as briefings search them.
SAMPLE_QUERIES: list[tuple[str, str | None]] = [
    ("cost of school uniforms", None),
    ("future of BBC funding and the licence fee", None),
    ("rail services in the north of England", None),
    ("NHS waiting lists for elective surgery", None),
    ("artificial intelligence regulation and copyright", None),
    ("small boats crossing the Channel", None),
    ("farmers and inheritance tax on agricultural land", None),
    ("potholes and local road maintenance", None),
    ("defence spending as a share of GDP", None),
    ("special educational needs funding for councils", None),
    ("onshore wind planning rules", "house = 'commons'"),
    ("assisted dying", "house = 'lords'"),
    ("water companies and sewage discharges", "year >= 2020"),
    ("housing targets and the green belt", "is_gov = true"),
]


def top_ids(idx: LanceIndex, vectors: list[Any], limit: int, nprobes: int | None = None,
            refine_factor: int | None = None, exact: bool = False) -> tuple[list[list[str]], float]:
    """The passage IDs each sample query finds, and the median search time in milliseconds."""
    found, times = [], []
    for vec, (_, where) in zip(vectors, SAMPLE_QUERIES, strict=True):
        start = time.perf_counter()
        hits = idx.search_meaning(vec, where, limit, nprobes=nprobes, refine_factor=refine_factor, columns=["passage_id"],
                          exact=exact)
        times.append((time.perf_counter() - start) * 1000)
        found.append([h["passage_id"] for h in hits])
    times.sort()
    return found, round(times[len(times) // 2], 1)


def recall(truth: list[list[str]], found: list[list[str]]) -> float:
    """Share of the true nearest passages that were found (averaged over the queries)."""
    shares = [len(set(f) & set(t)) / len(t) for t, f in zip(truth, found, strict=True) if t]
    return round(sum(shares) / len(shares), 3) if shares else 1.0


@dataclass
class Baseline:
    """What the index in use finds now: the true nearest passages to each sample query (every vector compared), and
    the share of them found with the site's current settings."""
    vectors: list[Any]
    truth: list[list[str]]
    exact_median_ms: float
    before: dict[str, Any]


def measure_before(idx: LanceIndex, embedder, current: dict[str, Any] | None = None, limit: int = 300) -> Baseline:
    current = current or {}
    vectors = [embedder.embed_query(q) for q, _ in SAMPLE_QUERIES]
    truth, exact_ms = top_ids(idx, vectors, limit, exact=True)
    settings = {"nprobes": current.get("nprobes"), "refine_factor": current.get("refine_factor")}
    top_ids(idx, vectors, limit, **settings)  # warm the cache, so the timing is not of a cold read
    before, before_ms = top_ids(idx, vectors, limit, **settings)
    return Baseline(vectors, truth, exact_ms, {**settings, "median_ms": before_ms, "recall": recall(truth, before)})


def measure_after(idx: LanceIndex, base: Baseline, limit: int = 300, probes: tuple[int, ...] = PROBES,
                  refines: tuple[int | None, ...] = REFINES, tolerance: float = 0.02) -> dict[str, Any]:
    """Try each ``nprobes`` and ``refine_factor`` on a new index. If some setting finds at least as many of the true
    nearest passages as the old index did (within ``tolerance``), the fastest setting within ``tolerance`` of the
    best is ``recommended``."""
    trials = []
    for n in probes:
        for rf in refines:
            top_ids(idx, base.vectors, limit, n, rf)
            after, ms = top_ids(idx, base.vectors, limit, n, rf)
            trials.append({"nprobes": n, "refine_factor": rf, "median_ms": ms, "recall": recall(base.truth, after)})
    out: dict[str, Any] = {"trials": trials}
    best = max(t["recall"] for t in trials)
    if best >= base.before["recall"] - tolerance:
        pick = min((t for t in trials if t["recall"] >= best - tolerance), key=lambda t: t["median_ms"])
        out["recommended"] = {"nprobes": pick["nprobes"], "refine_factor": pick["refine_factor"],
                              "recall": pick["recall"]}
    return out


def rebuild(idx: LanceIndex, embedder, rows_per_partition: int = 8192, probes: tuple[int, ...] = PROBES,
            refines: tuple[int | None, ...] = REFINES, current: dict[str, Any] | None = None,
            limit: int = 300, tolerance: float = 0.02, force: bool = False) -> dict[str, Any]:
    """Rebuild the vector index, then try each ``nprobes`` and ``refine_factor``, measuring how many of the true
    nearest passages (from a search that compares every vector) each finds.

    The new index is kept if some setting finds at least as many as the old index with its ``current`` settings did
    (within ``tolerance``); the fastest setting within ``tolerance`` of the best is recommended. Otherwise the old
    index is put back (unless ``force``). A kept index is recorded beside it (``write_record``). The caller holds the
    update lock, so nothing else writes meanwhile."""
    vt = idx.vectors()
    rows = vt.count_rows()
    base = measure_before(idx, embedder, current, limit)
    version = vt.version
    start = time.time()
    idx.rebuild_vector_index(rows_per_partition)
    built = round(time.time() - start)
    after = measure_after(idx, base, limit, probes, refines, tolerance)
    out: dict[str, Any] = {"vectors": rows, "rows_per_partition": rows_per_partition,
                           "partitions": partitions(rows, rows_per_partition), "build_seconds": built,
                           "exact_median_ms": base.exact_median_ms, "before": base.before, "trials": after["trials"]}
    if "recommended" in after:
        out["recommended"] = after["recommended"]
        out["kept"] = True
    elif force:
        out["kept"] = True
    else:
        idx.vectors().restore(version)
        out["kept"] = False
        out["note"] = "the new index found fewer of the nearest passages at every setting; the old one was put back"
    if out["kept"]:
        out["record"] = str(write_record(idx.path, "reindex-vectors", out))
    return out


def measure(old: LanceIndex, new: LanceIndex, embedder, rows_per_partition: int, current: dict[str, Any] | None = None,
            limit: int = 300, probes: tuple[int, ...] = PROBES, refines: tuple[int | None, ...] = REFINES,
            tolerance: float = 0.02) -> dict[str, Any]:
    """``rebuild``'s measurement for an index already built on a new table beside the old one (``hansard
    vectors-f16``): the old table's true nearest passages and its index's share of them with the ``current``
    settings, then each setting on the new table's index. ``recommended`` only if one finds as many (as ``rebuild``
    keeps a new index)."""
    base = measure_before(old, embedder, current, limit)
    rows = new.vectors().count_rows()
    after = measure_after(new, base, limit, probes, refines, tolerance)
    return {"vectors": rows, "rows_per_partition": rows_per_partition,
            "partitions": partitions(rows, rows_per_partition), "exact_median_ms": base.exact_median_ms,
            "before": base.before, **after}


# ---- the record of the last rebuild ---------------------------------------------------------------------------------
# The index is partitioned for the number of vectors when it is built. New vectors join its existing partitions, so
# as the record grows each search reads more: the monthly disk report (``hansard index-status``) compares the number
# now with the number in this record, and says when to rebuild.


def partitions(rows: int, rows_per_partition: int) -> int:
    return max(1, rows // rows_per_partition)


def record_path(index_dir: Path) -> Path:
    return Path(index_dir) / RECORD


def write_record(index_dir: Path, by: str, result: dict[str, Any]) -> Path:
    """Note a rebuilt index beside it: when, by which command, its size and the settings chosen, with their recall."""
    rec = result.get("recommended") or {}
    record = {"built": datetime.now(UTC).isoformat(timespec="seconds"), "by": by, "vectors": result["vectors"],
              "partitions": result["partitions"], "rows_per_partition": result["rows_per_partition"],
              "nprobes": rec.get("nprobes"), "refine_factor": rec.get("refine_factor"), "recall": rec.get("recall"),
              "recall_before": result.get("before", {}).get("recall")}
    path = record_path(index_dir)
    path.write_text(json.dumps(record, indent=2) + "\n")
    return path


def read_record(index_dir: Path) -> dict[str, Any] | None:
    """The record of the last rebuild, or None if there is none (an index built before November 2026)."""
    try:
        return json.loads(record_path(index_dir).read_text())
    except (OSError, ValueError):
        return None


def status(index_dir: Path, vectors_now: int, growth_limit: float = REINDEX_GROWTH) -> dict[str, Any]:
    """The number of vectors now against the number at the last rebuild, and whether it is time for another."""
    record = read_record(index_dir)
    out: dict[str, Any] = {"vectors": vectors_now, "last_rebuild": record}
    if not vectors_now:
        out["reminder"] = "Meaning-search index: no passages are searched by meaning yet."
        return out
    if not record or not record.get("vectors"):
        out["rebuild_due"] = None
        out["reminder"] = (f"Meaning-search index: {vectors_now:,} passages, and no record of when its index was last "
                           "rebuilt. Run hansard reindex-vectors once meaning-based search has caught up (hansard "
                           "status shows files_unembedded 0); docs/HOSTING.md, 'Day to day', has the command.")
        return out
    then = int(record["vectors"])
    growth = vectors_now / then - 1
    out["growth"] = round(growth, 3)
    out["rebuild_due"] = growth >= growth_limit
    when = record.get("built", "")[:10]
    with contextlib.suppress(ValueError):
        d = date.fromisoformat(when)
        when = f"{d.day} {d:%B %Y}"
    change = f"{growth:.0%} more" if growth >= 0 else f"{-growth:.0%} fewer"
    line = f"Meaning-search index: {vectors_now:,} passages now, {then:,} when it was last rebuilt ({when}), {change}."
    if out["rebuild_due"]:
        line += (f" That is {growth_limit:.0%} or more: rebuild it with hansard reindex-vectors (docs/HOSTING.md, "
                 "'Day to day', has the command).")
    else:
        line += f" Rebuild it at {growth_limit:.0%} more ({int(then * (1 + growth_limit)):,} passages)."
    out["reminder"] = line
    return out
