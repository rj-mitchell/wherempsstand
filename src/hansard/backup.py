"""Daily backup of the one thing that cannot be rebuilt from public sources: the state database.

It holds the saved briefings, reports from readers, statements under the right of reply, feedback, usage and
spending, and visit counts. Everything else (Hansard, member data, the search index) is downloaded or rebuilt by
``hansard bootstrap``.

A backup is a consistent copy made with SQLite's online backup (safe while the site is running), checked, then
packed with the local settings into ``where-they-stand-YYYY-MM-DD.tar.gz``. The newest ``keep`` are kept.
``restore_backup`` puts one back (``hansard restore``): it checks the backup before it replaces anything, and keeps
the database it replaces.
"""

from __future__ import annotations

import shutil
import sqlite3
import tarfile
import tempfile
import zlib
from datetime import UTC, datetime
from pathlib import Path

from hansard.config import Settings

PREFIX = "where-they-stand-"
# A backup of this site's state database has at least these tables (store/state.py; all four since the first release).
REQUIRED_TABLES = frozenset({"manifest", "usage", "briefings", "reports"})


def make_backup(settings: Settings, dest: Path | None = None, keep: int = 14,
                day: str | None = None) -> Path | None:
    """The backup written, or None when there is no state database yet (a new site): opening it to copy it would
    create an empty one, and an empty database is not a backup."""
    if not settings.state_db.exists():
        return None
    dest = Path(dest or settings.data_dir / "backups")
    dest.mkdir(parents=True, exist_ok=True)
    day = day or datetime.now(UTC).strftime("%Y-%m-%d")
    out = dest / f"{PREFIX}{day}.tar.gz"
    with tempfile.TemporaryDirectory(dir=dest) as tmp:
        copy = Path(tmp) / "state.sqlite"
        src = sqlite3.connect(settings.state_db)
        dst = sqlite3.connect(copy)
        try:
            src.backup(dst)
            ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            dst.close()
            src.close()
        if ok != "ok":
            raise RuntimeError(f"the backup copy failed its integrity check: {ok}")
        partial = Path(tmp) / out.name
        with tarfile.open(partial, "w:gz") as tar:
            tar.add(copy, arcname="state.sqlite")
            local = settings.data_dir / "settings.local.toml"
            if local.exists():
                tar.add(local, arcname="settings.local.toml")
        partial.chmod(0o600)  # it holds password hashes and readers' contact details: its owner only
        partial.replace(out)
    for old in sorted(dest.glob(f"{PREFIX}*.tar.gz"))[:-keep] if keep > 0 else []:
        old.unlink()
    return out


def restore_backup(settings: Settings, archive: Path) -> Path | None:
    """Put a backup's state database (and local settings) back. Stop the site first. The backup is unpacked beside
    the database and checked before anything is replaced; the database it replaces is kept beside it as
    ``state.sqlite.before-restore``, which is returned (None when there was none). A kept copy from an earlier
    restore is never written over: the restore is refused until it has been moved or deleted."""
    archive, state_db = Path(archive), settings.state_db
    kept = state_db.with_name(state_db.name + ".before-restore") if state_db.exists() else None
    for earlier in (kept, Path(f"{kept}-wal")) if kept else ():
        if earlier.exists():  # the only copy of what an earlier restore replaced: never written over
            raise RuntimeError(f"{earlier} is still there from an earlier restore: move or delete it first.")
    state_db.parent.mkdir(parents=True, exist_ok=True)
    # beside the database, not in the system's temporary folder: the swap is then a rename on one file system
    with tempfile.TemporaryDirectory(dir=state_db.parent) as tmp:
        db, local = Path(tmp) / "state.sqlite", Path(tmp) / "settings.local.toml"
        try:
            with tarfile.open(archive) as tar:
                # "./state.sqlite" as well as "state.sqlite": a backup repacked by hand is named that way
                members = {m.name.removeprefix("./"): m for m in tar.getmembers() if m.isfile()}
                if "state.sqlite" not in members:
                    raise RuntimeError(f"{archive.name} is not a backup of this site: it has no state.sqlite")
                tar.extractall(tmp, members=[members[n] for n in (db.name, local.name) if n in members],
                               filter="data")
        except (tarfile.TarError, OSError, EOFError, zlib.error) as exc:  # not a tar, or cut short, or damaged
            raise RuntimeError(f"{archive.name} could not be read: {exc}") from exc
        con = sqlite3.connect(db)
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        except sqlite3.DatabaseError as exc:
            raise RuntimeError(f"{archive.name} is not a database: {exc}") from exc
        finally:
            con.close()
        if ok != "ok":
            raise RuntimeError(f"the backup's database failed its integrity check: {ok}")
        if REQUIRED_TABLES - tables:
            raise RuntimeError(f"{archive.name} is not a backup of this site's state database")
        if kept:
            # Copied under a temporary name and renamed into place, so a failure part-way never leaves a damaged
            # kept copy. This database keeps its newest writes in a write-ahead log until they are folded in
            # (store/state.py), so the log is kept with the copy: SQLite reads the two together. The log is put in
            # place first: the copy is never there without it.
            copy = Path(tmp) / kept.name
            shutil.copy2(state_db, copy)
            Path(f"{kept}-shm").unlink(missing_ok=True)  # an index of a log that is no longer there; SQLite remakes it
            if Path(f"{state_db}-wal").exists():
                shutil.copy2(f"{state_db}-wal", f"{copy}-wal")
                Path(f"{copy}-wal").replace(f"{kept}-wal")
            copy.replace(kept)
        try:
            for suffix in ("-wal", "-shm", "-journal"):  # left from the database being replaced, they would corrupt it
                Path(f"{state_db}{suffix}").unlink(missing_ok=True)
            db.chmod(0o600)  # as the backup: it holds password hashes and readers' contact details
            db.replace(state_db)
        except OSError as exc:
            if not kept:
                raise
            # Nothing is rolled back automatically: the owner is told exactly which files hold what was there.
            back = f"move {kept} to {state_db}"
            if Path(f"{kept}-wal").exists():
                back += f" and {kept}-wal to {state_db}-wal"
            raise RuntimeError(f"the backup could not be put in place ({exc}). {state_db} is no longer complete: its "
                               f"write-ahead log has been removed. To go back to what was there, {back}.") from exc
        if local.exists():
            local.replace(settings.data_dir / "settings.local.toml")
    return kept
