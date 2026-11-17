"""The shared package's tables in the state database, ``data/state.sqlite``, and the one-off move of the site's old
rows into them.

The state database is the one thing on the server that can't be rebuilt from public sources. It is one SQLite file
with a write-ahead log, opened by the web app and by the update (two processes). Two sets of tables live in it:

* the site's own (``hansard/store/state.py``): the files of the record read in, the update history, accounts and
  sessions, visit and question counts, feedback, topic versions, featured briefings and supporter keys;
* the Where They Stand sites' shared package's (wts-core, ``wts_core.state``; its docs/adr/0005), each named
  ``wts_…``: spending against the caps, the answer cache, saved briefings, notes of failed briefings, the record of
  each briefing, readers' reports, statements under the right of reply and their log, items hidden while a concern
  is decided, and the day's visitor secret. The package makes them, and the site reads and writes them only
  through it.

Before wts-core the site kept the second set in tables of its own (``usage``, ``answer_cache``, ``briefings``,
``failures``, ``reports``, ``statements``, and the day's secret in ``kv``). The first time the site starts on a
database that has them, their rows are copied into the ``wts_`` tables, once (``IMPORTS``), in one transaction.
The import leaves the old tables as they were, so the version before this one still runs on the file; and a copy
of the whole file as it was is kept beside it first, as ``state.pre-wts-core.<date>.sqlite``. ``hansard tidy``
deletes that copy once it is older than the backups are kept, keeps the old tables to the privacy notice's
promises each night, and drops them on a stated day (``OLD_TABLES``). docs/OPERATIONS.md, "Moving onto wts-core",
says how to go back, and until when.
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from wts_core import observe, replies, usage
from wts_core.ops import AlertWording, BackupSpec
from wts_core.ports import SYSTEM_CLOCK, Clock
from wts_core.state import Import, Migration, OldTables, StateError, columns
from wts_core.state import StateDB as CoreDB

from hansard.config import SITE, Settings
from hansard.store.state import StateDB

__all__ = ["BACKUPS", "BACKUP_ALERTS", "IMPORTS", "IMPORT_MARK", "OLD_TABLES", "CoreDB", "WrittenSinceTheMove",
           "check_before_opening", "migrations", "open_core", "written_since_the_move"]

# The tables the site made for itself before wts-core, whose rows are now in the package's, and what becomes of
# them. Nothing reads them now. They stay in the file for a while so that a deploy can be undone
# (deploy/undo-wts-core.sql), and while they do they keep to what the privacy notice says, each night
# (`hansard tidy`, in the nightly update):
#
# * an old statement's confirming address and an old report's contact details go once the package's row has none
#   (three months after a statement ends or a report is left open; at once when a report is closed);
# * the visitor codes the old tables held (on spending, reports and statements) go once their day is over, and
#   every old day's visitor secret but today's with them: without its day's secret a code means nothing;
# * a note of a failed briefing goes after 90 days.
#
# From 1 February 2027 the old tables are dropped, and from then the way back to the code before wts-core is a
# backup made before the adoption, while one is kept, and not the undo script. (``kv`` is not dropped: it stays
# the site's own, for the supporter keys; only the visitor secret left it.)
OLD_TABLES = OldTables(
    tables=("usage", "answer_cache", "briefings", "failures", "reports", "statements"),
    drop_on=date(2027, 2, 1),
    nightly=(
        "UPDATE statements SET email = NULL WHERE email IS NOT NULL AND id IN "
        "(SELECT id FROM wts_statements WHERE email IS NULL)",
        "UPDATE reports SET contact = NULL WHERE contact IS NOT NULL AND id IN "
        "(SELECT id FROM wts_reports WHERE contact IS NULL)",
        "DELETE FROM failures WHERE substr(at, 1, 10) < date(:today, '-90 days')",
        "DELETE FROM kv WHERE key = 'visitor_salt' OR (key LIKE 'visitor_salt:%' AND key != 'visitor_salt:' || :today)",
        lambda con: _forget_codes(con, "usage"),
        lambda con: _forget_codes(con, "reports"),
        lambda con: _forget_codes(con, "statements"),
    ))


def _forget_codes(con: sqlite3.Connection, table: str) -> list[str]:
    """Blank an old table's visitor codes of days gone by (a table from before it had the column has none)."""
    if "visitor" not in columns(con, table):
        return []
    return [f"UPDATE {table} SET visitor = NULL WHERE visitor IS NOT NULL AND substr(at, 1, 10) < :today"]


# What the site's backups are called and hold (`hansard backup`, `hansard restore`): a tar of the database and the
# local settings, as where-they-stand-YYYY-MM-DD.tar.gz; the newest 14 kept on the server and 60 off site. The
# database keeps its newest writes in a write-ahead log, so the copy a restore keeps carries the log. A restore
# takes only a database with these two tables: the site's own, in every backup made before the shared package or
# after, and in no sister site's. The database is also marked as this site's when it is opened (``open_core``),
# and a restore refuses one marked as another's.
BACKUPS = BackupSpec(prefix="where-they-stand-", format="tar", member="state.sqlite",
                     extra_files=("settings.local.toml",), keep=14, keep_offsite=60,
                     required_tables=("manifest", "sync_log"), wal=True, site=SITE.package)

# What the admin page and `hansard backup-check` say when the nightly backup or its off-site copy has stopped:
# this site's own sentences, as they were.
BACKUP_ALERTS = AlertWording(
    no_backups="No backups have been made yet.",
    backup_stale="The last backup was made over two days ago ({name}).",
    server_only="Backups are kept on this server only: no off-site copy is set up.",
    no_offsite_yet="No off-site copy has been made yet; one is made with the next backup.",
    offsite_failed="The last off-site copy failed ({when}): {error}",
    offsite_stale="The last off-site copy was over two days ago ({day}).",
)


def migrations() -> tuple[Migration, ...]:
    """The tables of every part of the package this site uses. Each module names its own; the site joins them."""
    return (*usage.migrations(), *replies.migrations(), *observe.migrations())


# the one-off copy of the site's old rows into the shared package's tables (wts-core docs/adr/0005)
#
# What changes its name: a spending row's ``user_id`` is its ``account``; a briefing's ``settings`` are its
# ``options``, and its ``records_up_to`` and ``model`` go into ``extra``; a statement's ``item_path`` is its
# ``item`` and its ``person`` whom it is ``about``. A statement the site took down after publishing it was
# "declined" and is now "removed" (the package keeps "declined" for one never shown). The old table had no log:
# each statement gets one entry, "imported", holding the note of how its sender was confirmed, which the package
# keeps in the log and nowhere else. The visitor codes the old tables kept on reports and statements are not
# brought across: the package keeps none on either. The day's visitor secret comes across under its own name, so
# a visitor's count for today carries on; a permanent, un-dated secret from the site's first weeks, if one is
# still there, is deleted (it could turn old codes back into addresses, and nothing else would ever delete it).
#
# Each statement is built when the import runs, from the tables and columns the file has: a backup from before a
# table or column existed (``statements`` came on 12 November 2026; ``usage.visitor``, ``usage.cache_write_tokens``
# and ``reports.page`` were added after the first release) is brought across with what it holds.


def _there(con: sqlite3.Connection, table: str) -> bool:
    return bool(columns(con, table))


def _bring_secret(con: sqlite3.Connection) -> list[str]:
    if not _there(con, "kv"):
        return []
    return ["INSERT OR IGNORE INTO wts_kv (key, value) SELECT key, value FROM kv WHERE key LIKE 'visitor_salt:%'",
            "DELETE FROM kv WHERE key = 'visitor_salt'"]


def _bring_spending(con: sqlite3.Connection) -> list[str]:
    have = columns(con, "usage")
    visitor = "visitor" if "visitor" in have else "NULL"
    writes = "cache_write_tokens" if "cache_write_tokens" in have else "0"
    return ["INSERT INTO wts_spending (id, at, kind, model, visitor, account, input_tokens, output_tokens, "
            f"cache_read_tokens, cache_write_tokens, cost_usd) SELECT id, at, kind, model, {visitor}, user_id, "
            f"input_tokens, output_tokens, cache_read_tokens, {writes}, cost_usd FROM usage"]


def _bring_cache(con: sqlite3.Connection) -> list[str]:
    if not _there(con, "answer_cache"):
        return []
    return ["INSERT INTO wts_answer_cache (key, created_at, events) SELECT key, created_at, events FROM answer_cache"]


def _bring_failures(con: sqlite3.Connection) -> list[str]:
    if not _there(con, "failures"):
        return []
    return ["INSERT INTO wts_failures (id, at, kind, question, error) SELECT id, at, kind, question, error "
            "FROM failures"]


def _bring_reports(con: sqlite3.Connection) -> list[str]:
    have = columns(con, "reports")
    if not have:
        return []
    page = "nullif(page, '')" if "page" in have else "NULL"
    return ["INSERT INTO wts_reports (id, at, kind, page, message, contact, briefing_id, status, closed_at, "
            f"resolution, public_note) SELECT id, at, kind, {page}, message, nullif(contact, ''), briefing_id, "
            "status, closed_at, resolution, public_note FROM reports"]


def _bring_statements(con: sqlite3.Connection) -> list[str]:
    if not _there(con, "statements"):
        return []
    return ["INSERT INTO wts_statements (id, at, item, about, name, role, text, link, email, status, published_at, "
            "updated_at, closed_at, reason) SELECT id, at, item_path, CAST(person AS TEXT), name, role, text, "
            "nullif(link, ''), nullif(email, ''), CASE WHEN status = 'declined' AND published_at IS NOT NULL "
            "THEN 'removed' ELSE status END, published_at, updated_at, closed_at, reason FROM statements",
            "INSERT INTO wts_statement_log (statement, at, action, note) SELECT id, at, 'imported', checked "
            "FROM statements ORDER BY id"]


IMPORTS = (
    Import(
        name="hansard-state",  # its mark in wts_schema is IMPORT_MARK, below
        statements=(
            _bring_secret,
            _bring_spending,
            _bring_cache,
            _bring_failures,
            "INSERT INTO wts_briefings (id, created_at, question, options, events, extra, withdrawn_at, "
            "withdrawn_reason) SELECT id, created_at, question, settings, events, "
            "json_object('records_up_to', records_up_to, 'model', model), withdrawn_at, withdrawn_reason "
            "FROM briefings",
            _bring_reports,
            _bring_statements,
        ),
        # in every state database since the site's first release; a new site has neither, and nothing to bring
        needs=("usage", "briefings"),
        # Which column of each old table holds the time a row was written (wts-core docs/UPGRADING.md, "Going
        # back to the old code", the line for this site): with these the shared package refuses a
        # database the old code has added rows to since the move. It alone decides that, here and in the look
        # below; the old tables' other times (a report's closing, a statement's deciding) are the site's to read.
        written_at={"usage": "at", "answer_cache": "created_at", "briefings": "created_at", "failures": "at",
                    "reports": "at", "statements": "at"},
    ),
)


# when the old code has run again after the move
#
# The import runs once, and marks that it has (``import:hansard-state`` in ``wts_schema``, with the time). If the
# site is then taken back to the code before the shared package, that code reads and writes the old tables only;
# and if the new code is started again after that, the import would not run a second time, and whatever the old
# code wrote meanwhile (a saved briefing, a spending row, a report) would be in no table the site reads: a briefing
# answering 404, spending the caps do not see.
#
# One guard for each thing, never two that could disagree:
#
# * **A row the old code added** to an old table after the second the import ran: the shared package's guard
#   (``Import(written_at=…)``, above) decides, each time the database is opened. The site's look before that
#   (below) asks the same guard, over a scratch copy of what it reads, so that it can be asked read-only: before
#   the old site is stopped (`hansard check-settings`, in deploy/update.sh), and before anything is opened for
#   writing.
# * **A row the old code changed** that was already there (a briefing withdrawn, a report closed, a statement
#   published, updated or decided): the package cannot see this (its docs/UPGRADING.md, "Going back to the old code"), and the
#   site's look decides it by those rows' own times, which the package's line does not name.
#
# What the site adds besides is its words: they count what each side has taken in since the move and name both
# ways out, deploy/redo-wts-core.sql and deploy/undo-wts-core.sql (docs/OPERATIONS.md, "Going back, and coming
# forward again"). The package's own words name one script, "for undoing the move", which "clears the import's
# mark and the package's rows": that is what this site calls its redo, and its undo does the opposite, so the
# site's words are the ones the owner must meet. The look runs before the database is opened, so they are.
#
# The nightly keeping of the old tables to the site's promises (``OLD_TABLES``) blanks and deletes, and sets no
# time, so it can never look like the old code at work.

IMPORT_MARK = "import:hansard-state"
# the old tables' other times, which say a row already there was changed: the site's to read
_CHANGED_TIMES = {"briefings": ("withdrawn_at",), "reports": ("closed_at",),
                  "statements": ("published_at", "updated_at", "closed_at")}
# for the owner's message only: every time of each side's tables, rows added and rows changed
_OLD_TIMES = {table: (column, *_CHANGED_TIMES.get(table, ()))
              for table, column in IMPORTS[0].written_at.items() if isinstance(column, str)}
_NEW_TIMES = {"wts_answer_cache": ("created_at",), "wts_briefings": ("created_at", "withdrawn_at"),
              "wts_failures": ("at",), "wts_reports": ("at", "closed_at"),
              "wts_statements": ("at", "published_at", "updated_at", "closed_at")}


class WrittenSinceTheMove(StateError):
    """The old code has written to the state database since its rows were moved to the shared package's tables.
    ``old`` and ``new`` count, table by table, what each side has taken in or changed since ``moved_at``."""

    def __init__(self, moved_at: str, old: dict[str, int], new: dict[str, int]) -> None:
        self.moved_at, self.old, self.new = moved_at, old, new

        def counted(found: dict[str, int]) -> str:
            return ", ".join(f"{n:,} in {table}" for table, n in found.items()) or "nothing"

        super().__init__(
            f"The site's code from before the shared package has written to this database since its rows were "
            f"moved on {moved_at[:16].replace('T', ' ')} UTC ({counted(old)}), and those rows are in no table the "
            "site now reads. The site will not start until that is settled, so that nothing is silently left out. "
            f"Since the move the new tables have taken in {counted(new)}. To keep what the old code wrote and "
            "make the move again from the old tables: run deploy/redo-wts-core.sql (it discards what the new "
            "tables alone hold). To keep what the new code wrote instead: run deploy/undo-wts-core.sql (it "
            "discards what the old code wrote since the move). Then start the site again. "
            "docs/OPERATIONS.md, \"Going back, and coming forward again\", has the commands.")


def _moment(value: Any) -> datetime | None:
    """A time as a time, to the second, however it was written (``+00:00`` or ``Z``, with or without microseconds,
    another offset, or SQLite's own ``datetime('now')``, taken as UTC); None for what cannot be read as one."""
    if not isinstance(value, str):
        return None
    try:
        when = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return (when if when.tzinfo else when.replace(tzinfo=UTC)).astimezone(UTC).replace(microsecond=0)


def _added_since(con: sqlite3.Connection, name: str, moved_at: str) -> bool:
    """Whether the shared package's guard refuses the database for rows added to the old tables since the move:
    the guard itself, asked over a scratch copy of what it reads (``Import.written_at``'s columns), so that the
    database is not opened for writing. Only rows from two days before the move on are copied: a time earlier
    than that, in any zone, is not after the move, and the package itself reads from the day before on."""
    moved = _moment(moved_at)
    since = (moved - timedelta(days=2)).strftime("%Y-%m-%d") if moved else "9999"
    with tempfile.TemporaryDirectory(prefix="hansard-look-") as scratch:
        look = Path(scratch) / name  # so that a warning of the package's names the file it is about
        out = sqlite3.connect(look)
        try:
            out.execute("CREATE TABLE wts_schema (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
            out.execute("INSERT INTO wts_schema (name, applied_at) VALUES (?, ?)", (IMPORT_MARK, moved_at))
            for table, column in IMPORTS[0].written_at.items():
                have = columns(con, table)
                if not have:
                    continue  # dropped on its day, or never there: the package passes over it too
                out.execute(f'CREATE TABLE "{table}" (' + ", ".join(f'"{c}"' for c in sorted(have)) + ")")
                if isinstance(column, str) and column in have:
                    out.executemany(f'INSERT INTO "{table}" ("{column}") VALUES (?)', con.execute(
                        f'SELECT "{column}" FROM "{table}" WHERE typeof("{column}") = \'text\' AND "{column}" >= ?',
                        (since,)))
            out.commit()
        finally:
            out.close()
        try:
            CoreDB(look, imports=IMPORTS, old_tables=OLD_TABLES)
        except StateError:
            return True
    return False


def _changed_since(con: sqlite3.Connection, tables: dict[str, tuple[str, ...]], moved_at: str) -> dict[str, int]:
    """How many rows of each table have a time after the move: only rows from the day before it on are read."""
    moved = _moment(moved_at)
    if moved is None:
        return {}
    earliest = (moved - timedelta(days=1)).strftime("%Y-%m-%d")
    found = {}
    for table, times in tables.items():
        have = columns(con, table)
        wanted = [t for t in times if t in have]
        if not wanted:
            continue
        rows = con.execute(f"SELECT {', '.join(wanted)} FROM {table} WHERE "
                           + " OR ".join(f"{t} >= ?" for t in wanted), [earliest] * len(wanted))
        n = sum(1 for row in rows if any((when := _moment(v)) is not None and when > moved for v in row))
        if n:
            found[table] = n
    return found


def _spent_since(con: sqlite3.Connection, moved_at: str) -> int:
    """How many spending rows the new code has written since the move. Not told by their times, which keep only
    the day once that day is over (wts-core 0.3.0): by their numbers, which carry on from the last the old table
    had at the move."""
    if not columns(con, "wts_spending") or not columns(con, "usage"):
        return 0
    moved = _moment(moved_at)
    last = 0
    for number, at in con.execute("SELECT id, at FROM usage ORDER BY id DESC"):
        when = _moment(at)
        if moved is not None and when is not None and when <= moved:
            last = number
            break
    return int(con.execute("SELECT count(*) FROM wts_spending WHERE id > ?", (last,)).fetchone()[0])


def written_since_the_move(path: Path) -> WrittenSinceTheMove | None:
    """What the old code has written since the move, as the error to raise, or None when all is well: the database
    is not there, has never been moved, has no old tables left, or the old tables hold no row added since the
    move (the shared package's guard says) and none changed since (the site's look says). Reads only: the
    database is opened read-only, and nothing is changed."""
    path = Path(path)
    if not path.exists():
        return None
    # Read-only, and without making a file. While something has the database open its write-ahead log is beside
    # it, and a read-only connection reads the two together. With no log there, nothing is in one (the last to
    # close the database folded it in), and the file is read as it stands: opened any other way, SQLite would make
    # a log and its index beside a database that nothing has open.
    log_there = Path(f"{path}-wal").exists()
    con = sqlite3.connect(f"file:{path}?mode=ro" + ("" if log_there else "&immutable=1"), uri=True, timeout=30)
    try:
        if not columns(con, "wts_schema"):
            return None
        mark = con.execute("SELECT applied_at FROM wts_schema WHERE name = ?", (IMPORT_MARK,)).fetchone()
        if mark is None:
            return None
        if not _added_since(con, path.name, mark[0]) and not _changed_since(con, _CHANGED_TIMES, mark[0]):
            return None
        old = _changed_since(con, _OLD_TIMES, mark[0])
        spent = _spent_since(con, mark[0])
        new = {**({"wts_spending": spent} if spent else {}), **_changed_since(con, _NEW_TIMES, mark[0])}
        return WrittenSinceTheMove(mark[0], old, new)
    finally:
        con.close()


def check_before_opening(settings: Settings, path: Path | None = None) -> None:
    """What is checked before the state database is opened for writing, by the web app and by every command: that
    the settings are complete, and that the old code has not written to a database already moved. Raises for
    either, with the database as it was."""
    from hansard.startup import check_settings

    check_settings(settings)
    conflict = written_since_the_move(Path(path or settings.state_db))
    if conflict is not None:
        raise conflict


def open_core(settings: Settings, clock: Clock = SYSTEM_CLOCK, path: Path | None = None) -> CoreDB:
    """The shared package's side of the state database, with its tables made (and the old rows brought across, the
    first time) on the way in. The site's own side is opened first: it makes the file with its write-ahead log,
    which the package leaves as it finds it. The file is marked as this site's the first time it is opened, and a
    file marked as another site's is refused. ``path`` is the file, where it is not the settings' own (tests).

    Two things are checked before the file is opened for writing, because the first opening on new code may change
    it for good (``check_before_opening``): that the settings are complete (a site that would then refuse to start
    must not have moved the database first), and that the old code has not written to a database already moved.
    Either stops here, with the database as it was."""
    path = Path(path or settings.state_db)
    check_before_opening(settings, path)
    StateDB(path)
    return CoreDB(path, migrations=migrations(), imports=IMPORTS, clock=clock, site=SITE.package,
                  old_tables=OLD_TABLES)
