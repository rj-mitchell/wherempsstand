"""The one-off import of the site's old rows into the shared package's tables (src/hansard/store/wts.py,
``IMPORTS``; wts-core docs/adr/0005).

tests/golden/old-state.sql is a state database made by the site's own code before the move, with rows of every
kind: spending last month, this month and today, by two visitors, an account and the update itself; a cached
answer that is served, one too old and one cached before briefings had addresses; notes of failed briefings; saved
briefings, one withdrawn, one a topic's, one featured; reports open and closed, a takedown request, and the
columns that may be empty; statements waiting, published (one updated since), declined, withdrawn, one taken down
after it was published and one that has waited longer than three months; accounts, counts, feedback and a
supporter's key in the site's own tables; and, in ``kv``, the day's visitor secret and a permanent one from the
site's first weeks. tests/golden/old-state.json is what the old code read from it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import shared
from wts_core.testing import FixedClock

from hansard.store.wts import OLD_TABLES, open_core

GOLDEN = shared.read("old-state.json")
NOW = shared.START  # when the old code's figures were read: 13 November 2026
UNDATED_SECRET = "INSERT INTO \"kv\" VALUES('visitor_salt','" + "0d" * 16 + "');"


def dump(path: Path) -> list[str]:
    con = sqlite3.connect(path)
    try:
        return list(con.iterdump())
    finally:
        con.close()


def not_the_packages(lines: list[str]) -> list[str]:
    """A dump without the shared package's tables (and SQLite's own count of their rows)."""
    return [line for line in lines if "wts_" not in line and "sqlite_sequence" not in line]


def rows(path: Path, sql: str, *args) -> list[tuple]:
    con = sqlite3.connect(path)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def old_database(path: Path, without: tuple[str, ...] = ()) -> None:
    """The old database at ``path``; ``without`` leaves out the statements that make or fill these tables."""
    sql = (shared.GOLDEN / "old-state.sql").read_text(encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(sql)
    for table in without:
        con.execute(f"DROP TABLE {table}")
    con.execute("PRAGMA journal_mode=WAL")  # as the site's own code leaves the file
    con.commit()
    con.close()


@pytest.fixture
def old(settings):
    """A data folder holding the old database."""
    old_database(settings.state_db)
    return settings


def test_every_old_row_is_brought_across_and_the_old_tables_are_left_as_they_were(old):
    before = dump(old.state_db)
    assert UNDATED_SECRET in before
    counts = {t: rows(old.state_db, f"SELECT count(*) FROM {t}")[0][0] for t in OLD_TABLES}
    assert counts == {"usage": 13, "answer_cache": 3, "failures": 5, "briefings": 4, "reports": 6, "statements": 8}

    open_core(old, FixedClock(NOW))

    # The old tables, and every table of the site's own, byte for byte: but for the one row the import deletes,
    # a permanent visitor secret from the site's first weeks (wts-core docs/AS-BUILT.md, usage: "the package
    # deletes old secrets from wts_kv only")
    assert not_the_packages(dump(old.state_db)) == [line for line in not_the_packages(before)
                                                    if line != UNDATED_SECRET]
    path = old.state_db
    # spending, row for row, with the visitor's code and the account (the old ``user_id``)
    assert rows(path, "SELECT id, at, kind, model, visitor, account, input_tokens, output_tokens, "
                      "cache_read_tokens, cache_write_tokens, cost_usd FROM wts_spending ORDER BY id") == rows(
        path, "SELECT id, at, kind, model, visitor, user_id, input_tokens, output_tokens, cache_read_tokens, "
              "cache_write_tokens, cost_usd FROM usage ORDER BY id")
    assert {r[0] for r in rows(path, "SELECT DISTINCT kind FROM wts_spending")} == {
        "answer", "parse", "interests", "topic"}
    assert rows(path, "SELECT account, count(*) FROM wts_spending WHERE account IS NOT NULL GROUP BY 1") == [
        (1, 2), (99, 1)]
    # the day's visitor secret, under its own name: a visitor's code, and so their count for today, carries on.
    # The permanent one is in neither table now; the site's other keys stay where they were.
    assert rows(path, "SELECT key, value FROM wts_kv WHERE key LIKE 'visitor_salt%'") == [
        (f"visitor_salt:{shared.DAY}", shared.SALT)]
    assert rows(path, "SELECT key FROM kv ORDER BY key") == [
        ("some_other_key",), (f"supporter:{shared.DAY}:{'b2' * 12}",), (f"visitor_salt:{shared.DAY}",)]
    assert rows(path, "SELECT value FROM wts_kv WHERE key = 'site'") == [("hansard",)]
    assert rows(path, "SELECT key, created_at, events FROM wts_answer_cache ORDER BY key") == rows(
        path, "SELECT key, created_at, events FROM answer_cache ORDER BY key")
    assert rows(path, "SELECT id, at, kind, question, error FROM wts_failures ORDER BY id") == rows(
        path, "SELECT id, at, kind, question, error FROM failures ORDER BY id")
    # saved briefings: the same ids, and so the same addresses; their events byte for byte; what was asked with
    # them (the old ``settings``) as their options; the newest record's date and the model beside them
    assert rows(path, "SELECT id, created_at, question, options, events, withdrawn_at, withdrawn_reason, "
                      "json_extract(extra, '$.records_up_to'), json_extract(extra, '$.model') "
                      "FROM wts_briefings ORDER BY id") == rows(
        path, "SELECT id, created_at, question, settings, events, withdrawn_at, withdrawn_reason, records_up_to, "
              "model FROM briefings ORDER BY id")
    assert rows(path, "SELECT extra FROM wts_briefings WHERE id = ?", shared.WITHDRAWN) == [
        ('{"records_up_to":null,"model":null}',)]
    # reports, with everything but the visitor's code, which the package keeps on no report
    assert rows(path, "SELECT id, at, kind, page, message, contact, briefing_id, status, closed_at, resolution, "
                      "public_note FROM wts_reports ORDER BY id") == rows(
        path, "SELECT id, at, kind, page, message, contact, briefing_id, status, closed_at, resolution, "
              "public_note FROM reports ORDER BY id")
    assert rows(path, "SELECT count(*) FROM reports WHERE visitor IS NOT NULL") == [(3,)]
    # statements: the address is the item, the member whom it is about; one taken down after it was published
    # was "declined" and is "removed" (wts-core docs/MIGRATION.md 1.9)
    assert rows(path, "SELECT id, at, item, part, about, name, role, text, link, email, status, published_at, "
                      "updated_at, closed_at, reason FROM wts_statements ORDER BY id") == rows(
        path, "SELECT id, at, item_path, '', CAST(person AS TEXT), name, role, text, link, email, "
              "CASE WHEN id = 5 THEN 'removed' ELSE status END, published_at, updated_at, closed_at, reason "
              "FROM statements ORDER BY id")
    assert rows(path, "SELECT id, status, published_at IS NOT NULL FROM statements WHERE status = 'declined' "
                      "ORDER BY id") == [(3, "declined", 0), (5, "declined", 1), (6, "declined", 0)]
    assert rows(path, "SELECT about FROM wts_statements ORDER BY id") == [
        ("10669",), ("10669",), (None,), ("10669",), ("10115",), (None,), (None,), ("10669",)]
    # the log: one entry each, "imported", with the note of how the sender was confirmed where there was one
    assert rows(path, "SELECT statement, at, action, note, previous FROM wts_statement_log ORDER BY id") == rows(
        path, "SELECT id, at, 'imported', checked, NULL FROM statements ORDER BY id")
    assert rows(path, "SELECT note FROM wts_statement_log WHERE statement = 1") == [
        ("Replied from their parliament.uk address, 9 November.",)]
    # no visitor's code came across on a report or a statement: the package's tables have nowhere to keep one
    for table in ("wts_reports", "wts_statements", "wts_statement_log"):
        assert "visitor" not in {r[1] for r in rows(path, f"PRAGMA table_info({table})")}


def test_a_copy_of_the_file_as_it_was_is_kept_and_a_second_start_brings_nothing(old):
    before = dump(old.state_db)
    open_core(old, FixedClock(NOW))
    copy = old.data_dir / f"state.pre-wts-core.{shared.DAY}.sqlite"
    assert copy.exists() and copy.stat().st_mode & 0o777 == 0o600
    # the file as it was, with whatever was still in its write-ahead log (and the package's tables, made and empty)
    assert not_the_packages(dump(copy)) == not_the_packages(before)
    assert rows(copy, "SELECT count(*) FROM wts_spending") == [(0,)]
    after = dump(old.state_db)
    with sqlite3.connect(old.state_db) as con:  # a row written by the old code after the move (a rollback, say)
        con.execute("INSERT INTO failures (at, kind, question, error) VALUES ('x', 'briefing', 'q', 'late')")
    open_core(old, FixedClock("2026-11-14T09:00:00+00:00"))
    assert rows(old.state_db, "SELECT count(*) FROM wts_failures") == [(5,)]  # not brought again, nor the late row
    assert [line for line in dump(old.state_db) if "'late'" not in line and "sqlite_sequence" not in line] == [
        line for line in after if "sqlite_sequence" not in line]
    assert sorted(p.name for p in old.data_dir.glob("state.pre-wts-core.*")) == [copy.name]  # and no second copy
    assert rows(old.state_db, "PRAGMA journal_mode") == [("wal",)]  # the file keeps its write-ahead log


def test_a_backup_from_before_statements_were_kept_is_brought_across_with_what_it_holds(settings):
    """A backup from before 12 November 2026 has no ``statements`` table; one from the site's first weeks has no
    ``failures``, and its ``usage`` and ``reports`` lack the columns added since."""
    old_database(settings.state_db, without=("statements", "failures"))
    con = sqlite3.connect(settings.state_db)
    for table, column in (("usage", "visitor"), ("usage", "cache_write_tokens"), ("reports", "page")):
        con.execute("DROP INDEX IF EXISTS usage_visitor_at")
        con.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    con.commit()
    con.close()
    path = settings.state_db
    spent = rows(path, "SELECT id, at, kind, user_id, cost_usd FROM usage ORDER BY id")
    open_core(settings, FixedClock(NOW))
    assert rows(path, "SELECT id, at, kind, account, cost_usd FROM wts_spending ORDER BY id") == spent
    assert rows(path, "SELECT DISTINCT visitor, cache_write_tokens FROM wts_spending") == [(None, 0)]
    assert rows(path, "SELECT count(*), count(page) FROM wts_reports") == [(6, 0)]
    assert rows(path, "SELECT count(*) FROM wts_briefings") == [(4,)]
    assert rows(path, "SELECT count(*) FROM wts_statements") == [(0,)]
    assert rows(path, "SELECT count(*) FROM wts_failures") == [(0,)]
    assert rows(path, "SELECT name FROM wts_schema WHERE name LIKE 'import:%'") == [("import:hansard-state",)]


def test_an_import_that_fails_leaves_nothing_half_done(old):
    with sqlite3.connect(old.state_db) as con:  # a row the package's table cannot take: a failure with no time
        con.execute("ALTER TABLE failures RENAME TO failures_as_made")
        con.execute("CREATE TABLE failures (id INTEGER PRIMARY KEY, at TEXT, kind TEXT, question TEXT, error TEXT)")
        con.execute("INSERT INTO failures (kind, error) VALUES ('briefing', 'no time was kept')")
    with pytest.raises(sqlite3.IntegrityError):
        open_core(old, FixedClock(NOW))
    assert rows(old.state_db, "SELECT count(*) FROM wts_spending") == [(0,)]
    assert rows(old.state_db, "SELECT count(*) FROM wts_kv WHERE key LIKE 'visitor_salt%'") == [(0,)]
    assert rows(old.state_db, "SELECT value FROM kv WHERE key = 'visitor_salt'") == [("0d" * 16,)]
    assert rows(old.state_db, "SELECT name FROM wts_schema WHERE name LIKE 'import:%'") == []


def test_a_database_marked_as_another_sites_is_refused(settings):
    open_core(settings, FixedClock(NOW))
    with sqlite3.connect(settings.state_db) as con:
        con.execute("UPDATE wts_kv SET value = 'wcs' WHERE key = 'site'")
    with pytest.raises(RuntimeError, match="another site"):
        open_core(settings, FixedClock(NOW))
