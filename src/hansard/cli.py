"""``hansard`` command-line interface."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import date
from pathlib import Path
from typing import Any

import typer
from wts_core.cli import (
    BackupCheckWords,
    BackupWords,
    OpsWording,
    RestoreWords,
    eval_commands,
    ops_commands,
    replies_tidy,
    stats_commands,
)
from wts_core.observe import config_id
from wts_core.ports import SYSTEM_CLOCK, Clock

from hansard import owner
from hansard.config import SITE, STATIC_DIR, Settings, load_settings
from hansard.store.wts import BACKUP_ALERTS, BACKUPS

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Where MPs Stand: sourced briefings on the record of the UK Parliament.")
user_app = typer.Typer(no_args_is_help=True, help="Manage invite-only user accounts.")
app.add_typer(user_app, name="user")
topics_app = typer.Typer(no_args_is_help=True, help="Topic briefings (config/topics.toml).")
app.add_typer(topics_app, name="topics")
eval_app = typer.Typer(no_args_is_help=True, help="Score the site against the test set (eval/questions.toml).")
app.add_typer(eval_app, name="eval")

log = logging.getLogger("hansard")

EVAL_DIR = Path("eval")  # the test set (relative: run from the repository, or /app in the container)
CLOCK: Clock = SYSTEM_CLOCK  # the time the shared commands go by (a backup's name, a decision's date); tests set it


def _settings() -> Settings:
    return load_settings()


@contextmanager
def _one_at_a_time() -> Iterator[None]:
    """For the long one-off fetches: not while an update is running (they write the same files), and an update
    due meanwhile is skipped until the next one."""
    from hansard.sync import SyncRunning, update_lock

    with ExitStack() as stack:
        try:
            stack.enter_context(update_lock(_settings()))
        except SyncRunning:
            typer.echo("An update is running. Try again when it has finished "
                       "(`systemctl is-active hansard-sync` says inactive).", err=True)
            raise typer.Exit(1) from None
        yield


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore", "urllib3", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


# reference


def build_reference_data(settings: Settings, pull: bool = True, members_dir: Path | None = None) -> dict:
    from hansard.reference.build import build_reference
    from hansard.sources import parlparse
    from hansard.store.state import StateDB
    from hansard.sync import install_reference, refresh_demographics_data, refresh_elections_data

    state = StateDB(settings.state_db)
    sha = None
    live = members_dir is None  # the real member data (not the sample fixtures): fetch the extras too
    demographics, demo_status = refresh_demographics_data(settings, fetch=live)
    if members_dir is None:
        if pull:
            sha = parlparse.ensure_checkout(settings.sources["parlparse_repo"], settings.parlparse_dir,
                                            settings.sources.get("parlparse_branch", "master"))
        else:
            sha = parlparse.head_sha(settings.parlparse_dir)
        members_dir = parlparse.members_dir(settings.parlparse_dir)
    elections, elec_status = refresh_elections_data(settings, members_dir, fetch=live)
    ref = build_reference(members_dir, STATIC_DIR / "geo" / "constituencies.csv",
                          Path(settings.config_path).parent / "overrides" / "constituency_names.csv",
                          demographics, elections)
    people = install_reference(settings, state, ref, sha)
    # the index's speaker columns for these people are refreshed by the next `hansard sync`
    return {**ref.stats, "parlparse_sha": sha, "people_changed": len(people), "demographics": demo_status,
            "elections": elec_status}


@app.command()
def reference(pull: bool = typer.Option(True, help="git pull parlparse members/ first"),
              members: Path = typer.Option(None, help="use this members/ directory instead of the checkout")):
    """Build reference tables (people, seats, offices, regions) from parlparse."""
    _setup_logging()
    stats = build_reference_data(_settings(), pull=pull, members_dir=members)
    unmatched = stats.pop("unmatched_seats")
    typer.echo(json.dumps(stats, indent=2, default=str))
    if unmatched:
        typer.echo(f"WARNING: {len(unmatched)} seats since 1997 without a region: {unmatched[:10]}")


# data


@app.command()
def fetch(since: str = typer.Option(None, help="first date (default: archive start)"),
          transport: str = typer.Option("auto", help="auto | rsync | https. auto is HTTPS, storing each file gzipped, "
                                                     "unless [sources] compress_raw_xml = false, when it is rsync if "
                                                     "reachable. rsync always writes plain .xml (a full uncompressed "
                                                     "mirror).")):
    """Mirror new and corrected scrapedxml files from TheyWorkForYou."""
    _setup_logging()
    typer.echo(json.dumps(fetch_xml(_settings(), _date(since), transport), indent=2))


def fetch_xml(settings: Settings, since: date | None = None, transport: str = "auto") -> dict:
    from hansard.ingest import DIVISION_DIRS
    from hansard.sources.twfy import (
        HttpsMirror,
        drop_shadowed_gzip,
        rsync_available,
        rsync_directories,
    )

    since = since or settings.archive_start
    dirs = [*settings.record_dirs, *sorted(DIVISION_DIRS)]
    src = settings.sources
    # Stored gzipped (docs/DECISIONS.md, 25): rsync can only write plain files, so it is used only when asked for.
    compress = bool(src.get("compress_raw_xml", True))
    wants_rsync = transport == "rsync" or (transport == "auto" and not compress)
    if wants_rsync and rsync_available(src["twfy_rsync"]):
        rsync_directories(src["twfy_rsync"], settings.raw_xml_dir, dirs, since.year)
        return {"transport": "rsync", "gzipped_copies_replaced": drop_shadowed_gzip(settings.raw_xml_dir, dirs)}
    if transport == "rsync":
        raise typer.Exit("rsync to TheyWorkForYou is not reachable")
    mirror = HttpsMirror(src["twfy_https"], settings.raw_xml_dir, compress=compress)
    plan = mirror.plan(dirs, since)
    done = mirror.download(plan)
    return {"transport": "https", "gzip": compress, "planned": len(plan), "downloaded": len(done),
            "unchanged": mirror.unchanged, "markers_from_listing": mirror.adopted}


@app.command("compress-raw")
def compress_raw(dry_run: bool = typer.Option(False, "--dry-run", help="report what would be done, and the likely "
                                                                       "saving from a sample, without changing "
                                                                       "anything"),
                 limit: int = typer.Option(None, help="compress at most this many files (run again to continue)")):
    """One-off: gzip the raw Hansard XML in place, keeping each file's mtime, so nothing is parsed again."""
    _setup_logging()
    from hansard.sources.twfy import compress_mirror

    settings = _settings()
    if dry_run:
        out = compress_mirror(settings.raw_xml_dir, dry_run=True, limit=limit)
    else:
        with _one_at_a_time():
            out = compress_mirror(settings.raw_xml_dir, limit=limit)
    typer.echo(json.dumps(out, indent=2))
    if out.get("conflicts"):
        typer.echo("Some files are stored in both forms with different contents; the .gz copy is the one read.",
                   err=True)


@app.command()
def ingest(workers: int = typer.Option(3, help="parser processes (each streams one file)"),
           since: str = typer.Option(None), until: str = typer.Option(None)):
    """Parse new or corrected XML files into the Parquet store."""
    _setup_logging()
    from hansard.ingest import ingest as run

    stats = run(_settings(), workers=workers, since=_date(since), until=_date(until))
    typer.echo(json.dumps(stats.as_dict(), indent=2, default=str))


@app.command()
def index(rebuild_fts: bool = typer.Option(True, help="(re)build the BM25 index afterwards"),
          dedupe: bool = typer.Option(False, help="one-off: drop older copies of republished "
                                                  "records left by earlier versions of the indexer")):
    """Add newly ingested records to the keyword search index."""
    _setup_logging()
    settings = _settings()
    typer.echo(json.dumps(index_pending(settings, rebuild_fts, dedupe), indent=2))


def index_pending(settings: Settings, rebuild_fts: bool = True, dedupe: bool = False) -> dict:
    from hansard.index.build import Indexer
    from hansard.index.lance import search_index
    from hansard.store.state import StateDB

    idx = search_index(settings.index_dir)
    t = time.time()
    indexer = Indexer(settings, idx, StateDB(settings.state_db))
    out = indexer.dedupe() if dedupe else {}
    n = indexer.index_pending()
    if rebuild_fts and (n or dedupe):
        idx.build_indexes()
    elif n:
        idx.tidy()
    return {"passages_written": n, **out, "seconds": round(time.time() - t, 1), **idx.stats()}


@app.command()
def embed(max_files: int = typer.Option(None, help="stop after this many files"),
          max_minutes: float = typer.Option(None, help="stop after this many minutes (resumable)"),
          start_again: bool = typer.Option(False, "--start-again", help="after the embedding model has changed: "
                                           "throw the vectors away and make them all again with the model in the "
                                           "settings (search is by words alone until they are made)")):
    """Embed passages for semantic search, newest first (resumable)."""
    _setup_logging()
    settings = _settings()
    typer.echo(json.dumps(embed_pending_cmd(settings, max_files, max_minutes, start_again=start_again), indent=2))


MODEL_CHANGED = ("embedding model weights changed since the index was built; restore the pinned model, or make "
                 "the vectors again with `hansard embed --start-again`")


def embed_pending_cmd(settings: Settings, max_files=None, max_minutes=None, start_again: bool = False) -> dict:
    from wts_core.adapters.fastembed import model_fingerprint

    from hansard.adapters import get_embedder, models_dir
    from hansard.index.build import embed_pending
    from hansard.index.lance import IndexOutOfDate, search_index
    from hansard.store.state import StateDB

    state = StateDB(settings.state_db)
    embedder = get_embedder(settings)
    fastembed = settings.embedding.get("backend") == "fastembed"
    if start_again:
        # the vectors go, the index records the new model, and every file is marked as waiting to be embedded
        search_index(settings.index_dir).start_again(embedder, number_type="float16")
        with state.connect() as con:
            con.execute("UPDATE manifest SET embedded_at = NULL")
            con.execute("DELETE FROM kv WHERE key = 'embedding_model_sha256'")
    if fastembed:
        # The weights the vectors were made with, as the index's own note has them from wts-core 0.3.0: the
        # embedding model's own files (or, where they cannot be told from another model's, the whole folder's).
        # The note this site kept before covered every model file in the folder, the re-ranker's too on a server
        # set up before the package: that is accepted, once, and noted again below, as the index accepts its own
        # earlier id. Nothing in the folder is to be moved before then (hansard.adapters.models_folder).
        fp = embedder.fingerprint
        prev = state.get("embedding_model_sha256")
        if prev and prev not in (fp, model_fingerprint(models_dir(settings))):
            raise typer.Exit(MODEL_CHANGED)
    try:  # the index says which model its vectors were made with, and is not opened for another
        idx = search_index(settings.index_dir, embedder)
    except IndexOutOfDate as exc:
        log.error("%s", exc)
        raise typer.Exit(MODEL_CHANGED) from None
    if fastembed:
        state.set("embedding_model_sha256", fp)
        state.set("embedding_model", embedder.name)
    t = time.time()
    n = embed_pending(settings, idx, state, embedder, max_files=max_files,
                      max_seconds=max_minutes * 60 if max_minutes else None)
    if n and idx.stats()["vectors"] >= 10_000:
        idx.build_indexes()
    return {"passages_embedded": n, "seconds": round(time.time() - t, 1), **idx.stats()}


@app.command("reindex-vectors")
def reindex_vectors(rows_per_partition: int = typer.Option(8192, help="vectors per index partition"),
                    tolerance: float = typer.Option(0.02, help="keep the new index if some setting finds the "
                                                              "nearest passages within this share of the old one"),
                    force: bool = typer.Option(False, help="keep the new index whatever the comparison shows")):
    """Rebuild the meaning-search index for the vectors there are now (faster searches), checking the results.

    Takes a while on the full record (tens of minutes, most of it comparing every vector to measure how many of the
    nearest passages each index finds); the site keeps working on the old index meanwhile, and the twice-daily
    update waits for it to finish."""
    from hansard.adapters import get_embedder
    from hansard.index.lance import search_index
    from hansard.index.retune import rebuild
    from hansard.sync import update_lock

    _setup_logging()
    settings = _settings()
    idx = search_index(settings.index_dir)
    with update_lock(settings):
        current = {"nprobes": settings.ranking.get("vector_nprobes"),
                   "refine_factor": settings.ranking.get("vector_refine_factor")}
        out = rebuild(idx, get_embedder(settings), rows_per_partition, current=current, tolerance=tolerance,
                      force=force)
    if out.get("recommended"):
        out["settings_file"] = _save_vector_settings(settings, out["recommended"])
    typer.echo(json.dumps(out, indent=2))
    if out.get("kept"):
        typer.echo(RESTART_NOTE)


# the app reads its settings when it starts; update.sh restarts it only when there is new code to install
RESTART_NOTE = ("\nRestart the app to search with the new index settings: in /opt/hansardgpt/deploy, "
                "docker compose restart app")


def _save_vector_settings(settings: Settings, rec: dict) -> str:
    """Save the recommended nprobes and refine_factor to settings.local.toml, for the site and the updates."""
    from hansard.config import save_local_settings

    return str(save_local_settings(settings.data_dir, {"ranking": {
        "vector_nprobes": rec["nprobes"], "vector_refine_factor": rec["refine_factor"] or 0}}))


@app.command("groups-scan")
def groups_scan(everything: bool = typer.Option(False, "--all", help="read the whole record again (once, or after "
                                                                     "the rules change), not just since the last scan"),
                report: Path = typer.Option(None, help="write every declaration found to this Markdown file, for a "
                                                       "person to check")):
    """Find members' own statements in Parliament that they belong to a party group or caucus ("I am a member of the
    European Research Group"). Each update does this for new records; --all reads the whole archive."""
    from hansard.groups import declarations_report, scan_declarations

    settings = _settings()
    typer.echo(json.dumps(scan_declarations(settings, everything=everything), indent=1))
    if report:
        report.write_text(declarations_report(settings), encoding="utf-8")
        typer.echo(f"report written to {report}")


@app.command("index-status")
def index_status(text: bool = typer.Option(False, "--text", help="print only the sentence for the monthly report")):
    """How many passages are searched by meaning now, against the number when the index was last rebuilt
    (index/vector-index.json), and whether it is time to rebuild it (20% more). Reads no vectors: quick at any time."""
    from hansard.index.lance import search_index
    from hansard.index.retune import status as index_status_of

    settings = _settings()
    if not settings.index_dir.exists():
        out = index_status_of(settings.index_dir, 0)
    else:
        idx = search_index(settings.index_dir)
        out = index_status_of(settings.index_dir, idx.stats()["vectors"])
    typer.echo(out["reminder"] if text else json.dumps(out, indent=2))


def _check_queries(embedder, questions: Path) -> list:
    """The searches the conversion compares old and new tables on: the sample searches and the test set's questions,
    embedded by the local model (free)."""
    from hansard.index.retune import SAMPLE_QUERIES

    texts = [q for q, _ in SAMPLE_QUERIES]
    if questions.exists():
        from hansard.evaluate import load_questions

        texts += [q.question for q in load_questions(questions)[1]]
    return [embedder.embed_query(t) for t in texts]


@app.command("vectors-f16")
def vectors_f16(dry_run: bool = typer.Option(False, "--dry-run", help="report sizes, free space and the likely "
                                                                     "saving, and change nothing"),
                only: str = typer.Option(None, help="'passages' or 'evidence' (default: both)"),
                keep_old: bool = typer.Option(False, help="keep the old 32-bit table as <name>_f32 (the next run "
                                                          "deletes it)"),
                force: bool = typer.Option(False, help="swap even if the old and new tables' searches differ"),
                rows_per_partition: int = typer.Option(8192, help="vectors per index partition, as reindex-vectors"),
                questions: Path = typer.Option(EVAL_DIR / "questions.toml",
                                               help="questions to compare searches on, as well as the samples")):
    """Store the meaning-search vectors as 16-bit floats: about half the disk, the same passages found.

    Once, on an index made before November 2026. Copies each table of vectors (the passages', then the committee
    evidence's) to a new 16-bit one, builds its index as reindex-vectors does, measures the passages' new index and
    chooses its settings as reindex-vectors does, checks it (row counts, a sample of vectors, and the same searches
    on both), then swaps it in place of the old one in one step and deletes the old one. The site keeps searching
    throughout. Safe to stop and run again: it carries on where it stopped. Run under the server's update lock
    (docs/HOSTING.md, "Disk space"); restart the app afterwards if it says so."""
    from hansard.adapters import get_embedder
    from hansard.index.float16 import convert, evidence_target, passages_target
    from hansard.sync import SyncRunning, evidence_index_path, update_lock

    _setup_logging()
    settings = _settings()
    if only not in (None, "passages", "evidence"):
        raise typer.BadParameter("--only must be 'passages' or 'evidence'")
    embedder = None
    if not dry_run:
        try:
            embedder = get_embedder(settings)
        except Exception as exc:  # the other checks still run
            log.warning("no local embedding model (%s): the searches are not compared, nor the new index measured",
                        exc)
    r = settings.ranking
    targets = []
    if only in (None, "passages"):
        targets.append(passages_target(settings.index_dir, int(settings.embedding.get("dim", 384)),
                                       rows_per_partition, {"nprobes": r.get("vector_nprobes"),
                                                            "refine_factor": r.get("vector_refine_factor")},
                                       embedder=embedder))
    if only in (None, "evidence") and evidence_index_path(settings).exists():
        targets.append(evidence_target(evidence_index_path(settings)))
    results = {}
    with ExitStack() as stack:
        if not dry_run:
            try:
                stack.enter_context(update_lock(settings))
            except SyncRunning:
                typer.echo("An update is running. Try again when it has finished.", err=True)
                raise typer.Exit(1) from None
        queries = None
        for target in targets:
            if not dry_run and queries is None and target.folder(target.name).exists():
                queries = _check_queries(embedder, questions) if embedder is not None else []
            results[target.label] = convert(target, dry_run=dry_run, keep_old=keep_old, force=force,
                                            queries=queries, progress=lambda m: typer.echo(m, err=True))
            if results[target.label].get("refused"):
                break  # disk space, or a failed check: leave the rest for after it is dealt with
    passages = results.get("passages", {})
    rec = passages.get("tuning", {}).get("recommended")
    if rec and passages.get("record"):  # converted, with the settings measured for its new index
        passages["settings_file"] = _save_vector_settings(settings, rec)
    typer.echo(json.dumps(results, indent=2))
    if any(v.get("refused") for v in results.values()):
        raise typer.Exit(1)
    if passages.get("settings_file"):
        typer.echo(RESTART_NOTE)


@app.command()
def status():
    """Show data, index and sync status."""
    from hansard.index.lance import search_index
    from hansard.store.state import StateDB

    settings = _settings()
    state = StateDB(settings.state_db)
    manifest = state.manifest()
    idx = search_index(settings.index_dir)
    out = {
        "data_dir": str(settings.data_dir),
        "files_ingested": len(manifest),
        "files_unindexed": len(state.unindexed()),
        "files_unembedded": len(state.unembedded()),
        "index": idx.stats(),
        "parlparse_sha": state.get("parlparse_sha"),
        "embedding_model": state.get("embedding_model"),
        "last_runs": state.runs(5),
    }
    typer.echo(json.dumps(out, indent=2, default=str))


# search


@app.command()
def search(query: str, date_from: str = typer.Option(None, "--from"), date_to: str = typer.Option(None, "--to"),
           region: list[str] = typer.Option(None), house: list[str] = typer.Option(None),
           portfolio: list[str] = typer.Option(None), office: list[str] = typer.Option(None),
           recency: float = typer.Option(None), role_weight: float = typer.Option(None),
           limit: int = typer.Option(10), no_written: bool = typer.Option(False)):
    """Run the deterministic retrieval pipeline and print ranked sources."""
    from hansard.engine import Engine, Filters, Scope

    settings = _settings()
    t = time.time()
    eng = Engine(settings)
    res = eng.search(query, Filters(date_from=_date(date_from), date_to=_date(date_to), regions=region or [],
                                    houses=house or [], offices=office or []),
                     Scope(written=not no_written), portfolio or [], recency, role_weight, limit)
    typer.echo(f"{res.candidates} candidates, semantic={'on' if res.semantic_available else 'off'}, "
               f"{time.time() - t:.2f}s")
    for i, r in enumerate(res.records, 1):
        sp = r["speaker"] or {}
        offices = "; ".join(o["title"] for o in sp.get("offices", [])) or "backbench"
        typer.echo(f"\n[{i}] {r['date']} {r['chamber']} col {r['colnum'] or r['uin'] or '-'} | "
                   f"{sp.get('name') or r['speaker_name_raw']} ({sp.get('party')}, {sp.get('constituency') or sp.get('house')})"
                   f" | {offices}\n    {r['heading']}\n    {r['excerpt'][:300]}...\n    {r['ranking']}")


# sync and bootstrap


@app.command()
def sync(embed_minutes: float = typer.Option(60.0, help="time budget for embedding new passages"),
         fetch_data: bool = typer.Option(True, "--fetch/--no-fetch")):
    """Daily incremental update: member data, new Hansard, index, embeddings."""
    _setup_logging()
    from hansard.sync import SyncRunning, run_sync

    try:
        stats = run_sync(_settings(), fetch=fetch_data, embed_minutes=embed_minutes)
    except SyncRunning as exc:
        raise typer.Exit(f"Not started: {exc}.") from None
    typer.echo(json.dumps(stats, indent=2, default=str))
    if stats.get("error"):
        raise typer.Exit(1)


@app.command()
def devolved(files: int = typer.Option(200, help="files to fetch in this run"),
             since: str = typer.Option(None, help="first date (default: the first sittings, 1998-99)")):
    """Fetch the Scottish Parliament, Senedd and Northern Ireland Assembly records (also done by every sync)."""
    _setup_logging()
    with _one_at_a_time():
        from hansard.devolved import SINCE, DevolvedStore, Members, sync_devolved
        from hansard.sync import devolved_path

        settings = _settings()
        stats = sync_devolved(DevolvedStore(devolved_path(settings)), max_files=files,
                              since=date.fromisoformat(since) if since else SINCE,
                              members=Members(settings.parlparse_dir / "members" / "people.json"))
        typer.echo(json.dumps(stats, indent=2))


@app.command()
def committees(force: bool = typer.Option(True, help="rebuild even if the copy is less than a week old")):
    """Fetch select committees, their reports and evidence sessions (also done weekly by the sync)."""
    _setup_logging()
    from hansard.sources.committees import refresh
    from hansard.sync import committees_path

    typer.echo(json.dumps(refresh(committees_path(_settings()), force=force), indent=2))


@app.command()
def evidence(sessions: int = typer.Option(10000, help="transcripts to fetch in this run (about 8,000 since 2014)"),
             embed_minutes: float = typer.Option(180, help="then embed answers for meaning-based search, for up to "
                                                           "this long (0 to skip)")):
    """Fetch committee evidence transcripts and embed them now, rather than a few hundred per update."""
    _setup_logging()
    with _one_at_a_time():
        import sqlite3

        from hansard.adapters import get_embedder
        from hansard.index.evidence import EvidenceVectors, embed_evidence
        from hansard.sources.evidence import SINCE, EvidenceStore, sync_transcripts
        from hansard.sync import committees_path, evidence_index_path, evidence_path

        settings = _settings()
        with sqlite3.connect(committees_path(settings)) as cc:
            listed = cc.execute("SELECT id, date FROM sessions").fetchall()
        store = EvidenceStore(evidence_path(settings))
        out = {"transcripts": sync_transcripts(store, listed, since=SINCE, max_sessions=sessions)}
        if embed_minutes:
            emb = get_embedder(settings)
            out["embedded"] = embed_evidence(store, EvidenceVectors(evidence_index_path(settings), int(emb.dim)), emb,
                                             max_seconds=embed_minutes * 60)
        typer.echo(json.dumps(out, indent=2))


@app.command()
def interests(lords: bool = typer.Option(False, help="Read every peer's entries now, even if read this week"),
              archive: int = typer.Option(0, help="also read this many past Commons editions into the archive "
                                                  "(about 420 since 2000; sync reads 40 a run)")):
    """Fetch the newest registers of interests: Commons, Lords and the devolved parliaments (also done by sync)."""
    _setup_logging()
    with _one_at_a_time():
        if archive:
            from hansard.sources.regmem_archive import RegisterArchive, refresh_archive
            from hansard.sync import regmem_path

            typer.echo(json.dumps(refresh_archive(RegisterArchive(regmem_path(_settings()).with_name(
                "regmem_archive.sqlite")), max_editions=archive), indent=2))
        import duckdb

        from hansard.reference.profiles import attach_reference
        from hansard.sources.registers import refresh_devolved, refresh_lords, sitting_peers
        from hansard.sources.regmem import refresh
        from hansard.sync import regmem_path

        settings = _settings()
        out: dict = {"commons": refresh(regmem_path(settings))}
        con = duckdb.connect()
        attach_reference(con, settings.reference_dir)
        out["lords"] = refresh_lords(settings.data_dir / "regmem_lords.json", sitting_peers(con), force=lords)
        out["devolved"] = refresh_devolved(settings.data_dir)
        typer.echo(json.dumps(out, indent=2))


@app.command()
def whips(count: int = typer.Option(1500, help="votes to check in this run")):
    """Check votes for whip reports published by TheyWorkForYou Votes (also done by sync, a slice at a time)."""
    _setup_logging()
    from hansard.sources.whips import refresh

    settings = _settings()
    typer.echo(json.dumps(refresh(settings.data_dir / "whips.json", settings.data_dir / "raw" / "twfy_divisions.parquet",
                                  per_run=count, force=True), indent=2))


@app.command()
def directorships(count: int = typer.Option(400, help="members to look up in this run"),
                  show: str = typer.Option("", help="print what is held for members whose name contains this"),
                  check: bool = typer.Option(False, "--check", help="check the API key (never prints it)")):
    """Look up MPs' and peers' company directorships at Companies House (also done by sync, a slice at a time).
    Needs HANSARD_COMPANIES_HOUSE_KEY."""
    _setup_logging()
    from hansard.sources import parlparse
    from hansard.sources.companies import Directorships, refresh
    from hansard.sync import directorships_path

    if check:
        from hansard.sources.companies import check_key

        typer.echo(json.dumps(check_key(), indent=2))
        return
    settings = _settings()
    path = directorships_path(settings)
    if show:
        data = json.loads(path.read_text()) if path.exists() else {}
        for pid, rec in (data.get("people") or {}).items():
            if show.lower() in (rec.get("name") or "").lower():
                d = Directorships(path).for_person(pid) or {}
                typer.echo(f"{rec['name']} ({len(d.get('officers', []))} Companies House record(s))")
                for a in d.get("appointments", []):
                    typer.echo(f"  {a['role']}, {a['company']} ({a['status']}), {a['from'] or '?'} to "
                               f"{a['until'] or ('the company closed' if not a['current'] else 'now')}")
        return
    try:
        out = refresh(path, settings.reference_dir, parlparse.members_dir(settings.parlparse_dir) / "people.json",
                      per_run=count)
    except PermissionError as exc:
        raise typer.Exit(f"{exc}\nRun: docker compose exec app hansard directorships --check") from None
    typer.echo(json.dumps(out, indent=2))


@app.command()
def groups(force: bool = typer.Option(True, help="Download again even if the copy is less than a week old")):
    """Download the all-party parliamentary groups dataset and rebuild (also done weekly by sync)."""
    _setup_logging()
    import duckdb

    from hansard.reference.profiles import attach_reference
    from hansard.sources.appgs import refresh
    from hansard.sync import appgs_path

    settings = _settings()
    con = duckdb.connect()
    attach_reference(con, settings.reference_dir)
    typer.echo(json.dumps(refresh(appgs_path(settings), con, force=force), indent=2))


@app.command()
def donations(force: bool = typer.Option(True, help="Download again even if the copy is less than a week old")):
    """Download the Electoral Commission's register of donations and match it to MPs (also done weekly by sync)."""
    _setup_logging()
    import duckdb

    from hansard.reference.profiles import attach_reference
    from hansard.sources.donations import refresh
    from hansard.sync import donations_path

    settings = _settings()
    con = duckdb.connect()
    attach_reference(con, settings.reference_dir)
    typer.echo(json.dumps(refresh(donations_path(settings), con, force=force), indent=2))


@app.command()
def edms(check: bool = typer.Option(False, help="show what the API returns now, to confirm the fields read"),
         months: int = typer.Option(12, help="older months to fetch in this run"),
         signatures: int = typer.Option(1000, help="motions whose signatures to fetch in this run"),
         minutes: float = typer.Option(None, help="stop fetching signatures after this many minutes (default: when "
                                                   "done; the twice-daily update stops after 15)")):
    """Fetch early day motions (also done by every sync)."""
    _setup_logging()
    with _one_at_a_time():
        from hansard.sources.edms import EdmStore, sync_edms
        from hansard.sources.edms import check as edm_check
        from hansard.sync import edms_path

        if check:
            typer.echo(edm_check())
            return
        stats = sync_edms(EdmStore(edms_path(_settings())), months_per_run=months, sponsors_per_run=signatures,
                          sponsors_seconds=minutes * 60 if minutes else float("inf"))
        typer.echo(json.dumps(stats, indent=2, default=str))


@app.command()
def bootstrap(sample: bool = typer.Option(False, help="offline demo using bundled fixtures"),
              since: str = typer.Option(None, "--from", help="first date to load (default: archive start)"),
              embed_minutes: float = typer.Option(None, help="limit initial embedding time (resumable later)"),
              keyword_only: bool = typer.Option(False, help="skip meaning-based search (smaller server; remembered)"),
              workers: int = typer.Option(3)):
    """One-command setup: reference data, Hansard download, ingest, index, embeddings, smoke test."""
    _setup_logging()
    from hansard.bootstrap import run_bootstrap

    stats = run_bootstrap(_settings(), sample=sample, since=_date(since), embed_minutes=embed_minutes,
                          workers=workers, keyword_only=keyword_only)
    typer.echo(json.dumps(stats, indent=2, default=str))


# web


@app.command("meaning-check")
def meaning_check(look: bool = typer.Option(
        False, "--look", help="read only the index's note and the models folder, loading no model: for before a "
                              "deploy, on the new image, while the old site is still serving")):
    """Whether search by meaning is at work, and which case the models folder is in (whether anything in it may be
    moved yet: hansard.adapters.models_folder). Ends with status 1 only when the index has vectors that are being
    refused (they were made with another embedding model than the one in use): search has then fallen back to
    words alone for every reader. `deploy/check.sh` runs this; the last line is the state of search by meaning."""
    from hansard.adapters import models_dir, models_folder

    settings = _settings()
    fastembed = settings.embedding.get("backend", "fastembed") == "fastembed"
    if look:
        from wts_core.adapters.fastembed import model_fingerprints
        from wts_core.adapters.lancedb import stored_meta

        model = str(settings.embedding.get("model", ""))
        now, earlier, unclear = model_fingerprints(models_dir(settings), model) if fastembed else ("", (), "")
        stamped = stored_meta(settings.index_dir)
        typer.echo("The index is stamped: " + (stamped.embedder_id if stamped else
                                                "nothing (it is stamped when first opened)"))
        typer.echo("This version opens it as: " + (f"{model}@{now[:16]}" if now else model))
        typer.echo("and accepts, once: " + (", ".join(f"{model}@{was[:16]}" for was in earlier) or "no other id"))
        typer.echo(unclear or "The model's own files were found.")
        if fastembed:
            typer.echo(models_folder(settings)[1])
        return
    from hansard.engine import Engine

    engine = Engine(settings)
    state_of_it, said = engine.meaning_search()
    embedder = engine.embedder if fastembed and state_of_it != "none yet" else None
    if embedder is not None and hasattr(embedder, "fingerprint"):
        typer.echo(models_folder(settings, embedder.fingerprint, embedder.unclear, embedder.id)[1])
    typer.echo(said, err=state_of_it == "refused")
    if state_of_it == "refused":
        raise typer.Exit(1)



@app.command("check-settings")
def check_settings_cmd():
    """Check the settings and look at the state database, changing nothing: what `deploy/update.sh` runs on a
    newly built version before the site is restarted on it. Ends with status 1 if the site would refuse to start."""
    from wts_core.settings import SettingsError

    from hansard.api.measure import check as check_measurement
    from hansard.config import REPO_ROOT
    from hansard.startup import check_settings, models_named
    from hansard.store.wts import written_since_the_move

    try:
        settings = _settings()
        check_settings(settings)
        # with measurement on, the built site's measurement notice must be finished (hansard/api/measure.py)
        check_measurement(settings, Path(settings.section("paths").get("web_dist", REPO_ROOT / "web" / "dist")))
    except SettingsError as exc:
        typer.echo(f"The site would not start with these settings: {exc}", err=True)
        raise typer.Exit(1) from None
    conflict = written_since_the_move(settings.state_db)
    if conflict is not None:
        typer.echo(str(conflict), err=True)
        raise typer.Exit(1)
    typer.echo(f"The settings are complete ({settings.files}): every limit, cap and message, and prices for "
               f"{' and '.join(models_named(settings))}. Nothing was changed.")


@app.command("set-key")
def set_key(key: str = typer.Option(..., prompt="Paste your Anthropic API key (it will not be shown)",
                                    hide_input=True)):
    """Save the Anthropic API key in the data folder (handy when running on your own computer)."""
    import os

    from wts_core.adapters.anthropic import KEY_FILE

    from hansard.adapters import model_client

    key = key.strip()
    if not key.startswith("sk-ant-"):
        raise typer.BadParameter("that doesn't look like an Anthropic API key (they start with sk-ant-)")
    path = _settings().data_dir / KEY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key + "\n")
    os.chmod(path, 0o600)
    typer.echo(f"Saved ({len(key)} characters). Checking it with Anthropic...")
    import anthropic

    try:
        os.environ["HANSARD_ANTHROPIC_API_KEY"] = key
        model_client(_settings()).client.models.list(limit=1)  # the SDK's own client: one free call, to check the key
    except anthropic.AuthenticationError:
        path.unlink()
        typer.echo("Anthropic did not accept that key, so it was not kept. Check it and run this again.")
        raise typer.Exit(1) from None
    except Exception as e:  # noqa: BLE001 - network trouble etc.: keep the key, say what happened
        typer.echo(f"Kept the key, but could not check it just now ({type(e).__name__}).")
        return
    typer.echo("The key works. Written briefings are switched on.")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False,
          behind_proxy: bool = typer.Option(False, help="trust X-Forwarded-* headers (e.g. behind Caddy)"),
          access_log: bool = typer.Option(False, help="log every request, with visitors' IP addresses (off by "
                                                      "default: the site keeps its own cookie-free counts)")):
    """Run the web application."""
    import uvicorn

    from hansard.api.app import start_logging

    _setup_logging()
    start_logging(_settings())  # the structured log of briefings: stages and milliseconds, never a reader's words
    os.environ.setdefault("HANSARD_WARM_UP", "1")  # load the search models at start, not on the first question
    uvicorn.run("hansard.api.app:create_app", host=host, port=port, factory=True, reload=reload,
                proxy_headers=behind_proxy, forwarded_allow_ips="*" if behind_proxy else None,
                access_log=access_log)


# backups (wts-core, ``wts_core.cli.ops_commands``): `hansard backup`, `restore`, `offsite`, `backup-check`
#
# The commands are the Where They Stand sites' shared ones, under the names, with the options and printing the
# lines this site has always had. What a backup is called and holds is in hansard/store/wts.py (``BACKUPS``).
# `backup-check` is new with them: the alerts the admin page shows, for a timer to email.

OPS_WORDING = OpsWording(
    backup=BackupWords(
        folder="backups", keep=BACKUPS.keep,
        nothing_to_back_up="Nothing to back up yet: there is no state database.",
        backed_up="Backed up to {archive}", failed=None,
        offsite_stored="Off-site copy: stored in {bucket} ({stored} kept there)",
        offsite_not_set_up="Off-site copy: not set up", offsite_failed="The off-site copy failed: {error}",
        help="Back up what cannot be rebuilt: saved briefings, reports, feedback, usage and visit counts.",
        to_help="folder for backups (default: <data>/backups)", keep_help="how many daily backups to keep"),
    restore=RestoreWords(
        archive_must_exist=True, says_aborted=True,
        confirm="This replaces the current saved briefings, reports and usage records. Continue?",
        restored="Restored. Start the site again.", replaced=" The database it replaced is kept as {kept}.",
        not_restored="Not restored: {error}",
        help="Put a backup back (stop the site first). Replaces the current saved briefings, reports and usage. "
             "The backup is checked before anything is replaced, and the database it replaces is kept as "
             "state.sqlite.before-restore.",
        yes_help="do not ask for confirmation"),
    backup_check=BackupCheckWords(
        folder="/backups", no_folder="There is no backups folder at {folder}.", alert="{level}: {text}",
        up_to_date="Backups are up to date.", alerts=BACKUP_ALERTS,
        help="Check that the nightly backup and its off-site copy are being made: what the admin page says of "
             "them. Ends with status 1 if either has stopped, so that its timer emails the owner.",
        folder_help="the backups folder"),
    errors_to_stderr=True,
    offsite_help="Check the off-site backup bucket: whether the key works, and how many backups are stored there.")

app.add_typer(ops_commands(SITE, lambda: _settings(), BACKUPS, OPS_WORDING, clock=lambda: CLOCK,
                           before=lambda: _setup_logging()))


# how briefings went: statistics, the weekly report, the nightly tidy (wts-core docs/adr/0012)
#
# `hansard stats --since 7d` (and --json, --themes, --check), `hansard weekly-report`, `hansard tidy`: the Where
# They Stand sites' shared commands (``wts_core.cli.stats_commands``) over the record of each briefing.
# (`hansard status` is another thing: what is loaded and indexed.)
#
# `hansard tidy` is the nightly housekeeping, run by the backup's timer after the backup. It deletes what the
# privacy notice says is deleted in time, whether or not anyone visits: a briefing's record after 13 months, the
# question of a failed one after 90 days, a visitor's code and a signed-in account on a spending row after its
# day (with the row's time of day) and the secret the code was made with, cached answers past their week, weekly
# reports after 13 months, the copy of the database made before the move onto the shared package once it is older
# than the backups; then the right of reply's (a statement's confirming address three months after it ends, what
# nobody decided in three months). While the site's old tables are still in the file it keeps them to the same
# promises, and drops them on their day (hansard/store/wts.py, OLD_TABLES). Last, the file is written out afresh,
# and the command says plainly when it could not be. `hansard propose-questions` writes, for the owner to approve,
# test questions proposed from real use (docs/EVALUATION.md).

def _core() -> Any:
    from hansard.store.wts import open_core

    return open_core(_settings(), CLOCK)


def _prices() -> Any:
    from wts_core.usage import PriceTable

    s = _settings()
    return PriceTable.from_settings(s.usage, file=s.files)


def _embedder() -> Any:
    from hansard.adapters import get_embedder

    return get_embedder(_settings())


app.add_typer(stats_commands(
    SITE, lambda: _core(),
    thresholds=lambda: _settings().section("alerts"),  # empty until the owner sets them
    folder=lambda: _settings().reports_dir,
    # readers' questions are grouped into themes by meaning with the site's own embedding model, on the server
    embedder=lambda: _embedder(), similarity=lambda: float(_settings().embedding["theme_similarity"]),
    pricing=lambda: _prices(),
    backup_days=BACKUPS.keep,  # the copy made before the move is kept as long as a backup on the server: 14 days
    # The right of reply's deletions are the one step that is the site's to give. The command does the rest
    # itself, in this order: its own deletions (old records, visitor codes and accounts of days gone by, notes of
    # failed briefings, old visitor secrets, cached answers past their hours, old and unfinished copies); then
    # this step; then the old tables, after it; and last the file written out afresh.
    also_tidy=[replies_tidy(lambda: owner.replies_for(_settings(), CLOCK), owner.WORDING)],
    cache_hours=lambda: float(_settings().usage["answer_cache_hours"]),
    # no question of a briefing that is hidden while a concern about it is decided is proposed as a test question
    proposals_leave_out=lambda briefing: owner.replies_for(_settings(), CLOCK).hidden(f"/b/{briefing}") is not None,
    clock=lambda: CLOCK))


@user_app.command("add")
def user_add(email: str, admin: bool = typer.Option(False),
             password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True)):
    """Invite a user (create an account)."""
    from hansard.api.auth import create_user
    from hansard.store.state import StateDB

    create_user(StateDB(_settings().state_db), email, password, admin)
    typer.echo(f"created {email}")


@user_app.command("list")
def user_list():
    from hansard.store.state import StateDB

    with StateDB(_settings().state_db).connect() as con:
        for r in con.execute("SELECT email, is_admin, disabled, created_at FROM users ORDER BY email"):
            typer.echo(f"{r[0]}  admin={bool(r[1])}  disabled={bool(r[2])}  since {r[3]}")


@user_app.command("disable")
def user_disable(email: str):
    from hansard.store.state import StateDB

    with StateDB(_settings().state_db).connect() as con:
        con.execute("UPDATE users SET disabled = 1 WHERE email = ?", (email,))
        con.execute("DELETE FROM sessions WHERE user_id IN (SELECT user_id FROM users WHERE email = ?)", (email,))
    typer.echo(f"disabled {email}")


# reports and statements beside items (the right of reply)
#
# The commands are the Where They Stand sites' shared ones (wts-core, ``wts_core.cli``), under the names and with
# the lines this site has always had for statements; the commands for reports are new with them. hansard/owner.py
# holds every line they print.

app.add_typer(owner.reply_commands(lambda: _settings(), lambda: CLOCK))


# topic briefings


@topics_app.command("list")
def topics_list():
    """Each topic, its current version and when it was last checked."""
    from hansard.api.usage import desk
    from hansard.store.state import StateDB
    from hansard.store.wts import open_core
    from hansard.topics import topic_listing

    settings = _settings()
    archive = desk(settings, open_core(settings, CLOCK), CLOCK).archive
    for t in topic_listing(settings, StateDB(settings.state_db), archive):
        cur = t["current"]
        chk = t["last_check"]
        typer.echo(f"{t['slug']:32} {('written ' + cur['prepared_at'][:10]) if cur else 'not written yet':22} "
                   f"{t['versions']} version(s)  {('last check: ' + chk['outcome']) if chk else ''}")


@topics_app.command("refresh")
def topics_refresh(only: list[str] = typer.Option(None, help="just these topics (slugs)"),
                   force: bool = typer.Option(False, help="rewrite even if not due or the evidence is unchanged"),
                   limit: int = typer.Option(None, help="at most this many rewrites (default: max_per_run)")):
    """Rewrite the topic briefings that are due. Each rewrite costs about 11p."""
    from hansard.api.usage import desk
    from hansard.rag.pipeline import Pipeline
    from hansard.store.state import StateDB
    from hansard.store.wts import open_core
    from hansard.topics import refresh_topics

    settings = _settings()
    _setup_logging()
    parts = desk(settings, open_core(settings))  # first: it checks the settings before the database is opened
    res = refresh_topics(settings, StateDB(settings.state_db), Pipeline(settings), parts,
                         only=only or None, force=force, limit=limit, progress=typer.echo)
    typer.echo(f"{sum(1 for r in res if r['outcome'].startswith('rewritten'))} rewritten")


# the test set
#
# `hansard eval plans|sources|retrieval|briefings` are the site's own, as they always were: how questions are read,
# what evidence is chosen, and briefings by several models judged side by side (src/hansard/evaluate.py).
#
# `hansard eval run`, `eval compare` and `eval replay` are the Where They Stand sites' shared commands
# (``wts_core.cli.eval_commands``), added to the same group. `eval run` asks every question in eval/questions.toml
# of the real pipeline, each briefing written (it spends money: no more than [eval] max_cost_usd unless --max-cost
# says otherwise), saves the run in the data folder (<data>/eval/runs/: on the server /data/eval/runs/, the one
# place a command there can write and have kept) and holds it to [eval.thresholds]; `eval compare` sets two saved
# runs side by side; `eval replay` runs the checks again over briefings a run kept (--record-fixtures), free and
# with no model. HANSARD_EVAL_QUESTIONS names another file of questions (eval/sample-questions.toml, for the
# bundled sample data).
#
# What a run spends is its own: the questions go to the pipeline directly, not through the flow readers' questions
# take, so nothing is written to the spending record. It is not on the admin page and counts towards neither cap;
# the run has its own limit, says so as it starts, and reports what it spent. Its cost counts each of a
# briefing's three model calls, the check of registered interests among them (``HansardEval.usages``). (wts-core
# 0.3.0 could record a run in the site's spending, where the caps would count it: ``eval_commands(spending=…)``.
# On this site a full run on the archive costs about $6 (docs/EVALUATION.md), against a daily cap of $50, so on a
# busy day readers could be paused for the rest of it; it is not given, and that is for the owner to decide.)

NOT_IN_USAGE = ("What this run spends is not in the admin page's spending and counts towards neither the daily nor "
                "the monthly cap: it stops at its own limit ([eval] max_cost_usd, or --max-cost), and says below "
                "what it spent.")


def _eval_site() -> Any:
    from hansard.evaluate import HansardEval

    typer.echo(NOT_IN_USAGE, err=True)
    return HansardEval(_settings(), path=Path(os.environ.get("HANSARD_EVAL_QUESTIONS") or EVAL_DIR / "questions.toml"))


def _configuration() -> Any:
    """What an evaluation run is a run of: the configuration the settings give as they stand, with the same id the
    record of briefings gives it. Nothing is kept by asking (a run is not a reader's briefing)."""
    from hansard.api.app import configuration

    return configuration(_settings(), None).settings


def _checks(name: str) -> Any:
    from hansard.rag import checks

    return getattr(checks, name)


eval_app.add_typer(eval_commands(
    lambda: _eval_site(),  # looked up when a command runs, so a test can stand another in its place
    config=_configuration, config_id=config_id,  # the id the record of briefings gives the same parts
    pricing=lambda: _prices(),
    thresholds=lambda: _settings().section("eval")["thresholds"],
    runs=lambda: _settings().data_dir / "eval" / "runs",
    max_cost_usd=lambda: float(_settings().section("eval")["max_cost_usd"]),  # no run starts without a limit
    verifier=lambda: _checks("VERIFIER"), loaded_words=lambda: _checks("LOADED_WORDS"),
    group=None, clock=lambda: CLOCK))



def _eval_setup(questions: Path, only: str | None):
    from hansard.evaluate import load_questions

    today, qs = load_questions(questions)
    if only:
        keep = set(only.split(","))
        qs = [q for q in qs if q.id in keep]
    return today, qs


def _client():
    from hansard.adapters import model_client

    return model_client(_settings())


def _load_plans(path: Path) -> dict[str, dict]:
    run = json.loads(path.read_text(encoding="utf-8"))
    return {r["id"]: r["plan"] for r in run["rows"] if r.get("plan")}


@eval_app.command("plans")
def eval_plans(model: str = typer.Option(None, help="model that reads the questions (default: the configured one)"),
               effort: str = typer.Option(None, help="low, medium or high"),
               questions: Path = typer.Option(EVAL_DIR / "questions.toml"), only: str = typer.Option(None),
               out: Path = typer.Option(EVAL_DIR / "runs")):
    """Score how each question is read (the filters chosen). Costs a little: one model call per question."""
    from hansard.evaluate import run_plans, save
    from hansard.rag.pipeline import Pipeline

    settings = _settings()
    today, qs = _eval_setup(questions, only)
    pipeline = Pipeline(settings)
    res = run_plans(settings, qs, today, _client(), pipeline.parties(), model, effort,
                    party_groups=pipeline.group_choices(), group_aliases=pipeline.group_aliases())
    path = save(out, f"plans-{res['model']}", res)
    typer.echo(json.dumps(res["summary"], indent=1))
    typer.echo(f"saved {path}")


@eval_app.command("sources")
def eval_sources(plans: Path = typer.Option(..., help="a saved plans run: the filters each question was read as"),
                 variant: list[str] = typer.Option(["baseline"], help="baseline or rerank:<model>[:question|keywords|both[:weight]]"),
                 judge: str = typer.Option("claude-haiku-5-5", help="model that grades relevance"),
                 questions: Path = typer.Option(EVAL_DIR / "questions.toml"), only: str = typer.Option(None),
                 out: Path = typer.Option(EVAL_DIR / "runs"),
                 judgments: Path = typer.Option(EVAL_DIR / "judgments.jsonl")):
    """Compare ranking variants on the relevance and balance of the sources they choose."""
    from hansard.evaluate import (
        judge_sources,
        judgments_load,
        parse_variant,
        run_sources,
        save,
        summarise_sources,
    )

    settings = _settings()
    today, qs = _eval_setup(questions, only)
    plan_map = _load_plans(plans)
    runs = []
    for v in variant:
        typer.echo(f"{v}:")
        run = run_sources(settings, plan_map, qs, today, parse_variant(v))
        save(out, f"sources-{v.replace(':', '_').replace('/', '-')}", run)
        runs.append(run)
    graded = judge_sources(settings, _client(), qs, runs, judgments, judge)
    known = judgments_load(judgments)
    summaries = [summarise_sources(r, known) for r in runs]
    for sm in summaries:
        typer.echo(json.dumps({k: v for k, v in sm.items() if k != "per_question"}))
    typer.echo(f"grading cost ${graded['cost_usd']} ({graded['graded']} passages graded)")
    save(out, "sources-summary", {"summaries": summaries, "grading": graded})


@eval_app.command("retrieval")
def eval_retrieval(plans: Path = typer.Option(None, help="saved question readings (default: the newest made by a "
                                                         "model in eval/runs, so no model is called)"),
                   compare: Path = typer.Option(None, help="an earlier retrieval run: say how far this one differs"),
                   meaning: int = typer.Option(300, help="nearest passages by meaning to keep for each question"),
                   questions: Path = typer.Option(EVAL_DIR / "questions.toml"), only: str = typer.Option(None),
                   out: Path = typer.Option(EVAL_DIR / "runs")):
    """The sources each question finds, and its nearest passages by meaning: retrieval only, free (no model calls,
    no grading). Run before and after a change to the search index, the second time with --compare <first run>."""
    from hansard.evaluate import Variant, compare_retrieval, newest_plans, run_sources, save

    settings = _settings()
    today, qs = _eval_setup(questions, only)
    plans = plans or newest_plans(EVAL_DIR / "runs")
    if plans is None:
        raise typer.Exit("no saved question readings in eval/runs: give one with --plans")
    typer.echo(f"question readings from {plans}")
    run = run_sources(settings, _load_plans(plans), qs, today, Variant("baseline"), meaning=meaning)
    run["stage"] = "retrieval"
    path = save(out, "retrieval", run)
    typer.echo(f"saved {path}")
    if compare:
        diff = compare_retrieval(json.loads(compare.read_text(encoding="utf-8")), run)
        save(out, "retrieval-compare", {"before": str(compare), "after": str(path), **diff})
        typer.echo(json.dumps(diff, indent=1))


@eval_app.command("briefings")
def eval_briefings(plans: Path = typer.Option(...), model: list[str] = typer.Option(["claude-opus-5-5"]),
                   judge: str = typer.Option("claude-opus-5-5", help="model for the side-by-side judgement"),
                   questions: Path = typer.Option(EVAL_DIR / "questions.toml"), only: str = typer.Option(None),
                   out: Path = typer.Option(EVAL_DIR / "runs")):
    """Write briefings with each model (about $0.06-0.15 each) and compare them; the first model is the baseline."""
    from hansard.evaluate import compare_briefings, run_briefings, save

    settings = _settings()
    today, qs = _eval_setup(questions, only)
    plan_map, client = _load_plans(plans), _client()
    runs = []
    for m in model:
        typer.echo(f"{m}:")
        run = run_briefings(settings, plan_map, qs, today, client, m)
        save(out, f"briefings-{m}", run)
        typer.echo(json.dumps(run["summary"]))
        runs.append(run)
    for other in runs[1:]:
        cmp = compare_briefings(settings, client, runs[0], other, judge)
        save(out, f"compare-{runs[0]['model']}-{other['model']}", cmp)
        typer.echo(json.dumps({k: cmp[k] for k in ("a", "b", "tally", "cost_usd")}))


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())

__all__ = ["app", "shutil"]
