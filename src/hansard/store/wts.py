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
The old tables are left as they were, so the version before this one still runs on the file; and a copy of the
whole file as it was is kept beside it first, as ``state.pre-wts-core.<date>.sqlite``. ``hansard tidy`` deletes
that copy once it is older than the backups are kept. docs/OPERATIONS.md, "The state database", says how to go back.
"""

from __future__ import annotations

import sqlite3

from wts_core import observe, replies, usage
from wts_core.ops import AlertWording, BackupSpec
from wts_core.ports import SYSTEM_CLOCK, Clock
from wts_core.state import Import, Migration, columns
from wts_core.state import StateDB as CoreDB

from hansard.config import SITE, Settings
from hansard.store.state import StateDB

__all__ = ["BACKUPS", "BACKUP_ALERTS", "IMPORTS", "OLD_TABLES", "CoreDB", "migrations", "open_core"]

# The tables the site made for itself before wts-core, whose rows are now in the package's. Nothing reads them
# now; they are dropped in a later release. (``kv`` stays the site's own: only the visitor secret left it.)
OLD_TABLES = ("usage", "answer_cache", "briefings", "failures", "reports", "statements")

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


# ---- the one-off copy of the site's old rows into the shared package's tables (wts-core docs/adr/0005) ---------
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
        name="hansard-state",
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
    ),
)


def open_core(settings: Settings, clock: Clock = SYSTEM_CLOCK) -> CoreDB:
    """The shared package's side of the state database, with its tables made (and the old rows brought across, the
    first time) on the way in. The site's own side is opened first: it makes the file with its write-ahead log,
    which the package leaves as it finds it. The file is marked as this site's the first time it is opened, and a
    file marked as another site's is refused."""
    StateDB(settings.state_db)
    return CoreDB(settings.state_db, migrations=migrations(), imports=IMPORTS, clock=clock, site=SITE.package)
