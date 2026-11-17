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
    counts = {t: rows(old.state_db, f"SELECT count(*) FROM {t}")[0][0] for t in OLD_TABLES.tables}
    assert counts == {"usage": 13, "answer_cache": 3, "failures": 5, "briefings": 4, "reports": 6, "statements": 8}

    open_core(old, FixedClock(NOW))

    # The old tables, and every table of the site's own, byte for byte: but for the one row the import deletes,
    # a permanent visitor secret from the site's first weeks (the package
    # deletes old secrets from wts_kv only)
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
    # was "declined" and is "removed" (wts-core's statuses)
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
    open_core(old, FixedClock("2026-11-14T09:00:00+00:00"))
    assert rows(old.state_db, "SELECT count(*) FROM wts_failures") == [(5,)]  # nothing is brought a second time
    assert [line for line in dump(old.state_db) if "sqlite_sequence" not in line] == [
        line for line in after if "sqlite_sequence" not in line]
    # A row written by the old code after the move (the site taken back to it, say) is in no table the new code
    # reads, and the import will not run again by itself: the new code refuses to start rather than leave it out
    # (tests/test_deploy.py has the mend).
    from hansard.store.wts import WrittenSinceTheMove

    with sqlite3.connect(old.state_db) as con:
        con.execute("INSERT INTO failures (at, kind, question, error) VALUES ('2026-11-14T10:00:00+00:00', "
                    "'briefing', 'q', 'late')")
    with pytest.raises(WrittenSinceTheMove, match="1 in failures"):
        open_core(old, FixedClock("2026-11-14T11:00:00+00:00"))
    with sqlite3.connect(old.state_db) as con:
        con.execute("DELETE FROM failures WHERE error = 'late'")
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


def test_a_new_site_has_nothing_to_bring_and_no_copy_is_made(settings):
    db = open_core(settings, FixedClock(NOW))
    assert {"wts_spending", "wts_briefings", "wts_reports", "wts_statements", "manifest", "users"} <= db.tables()
    assert not set(OLD_TABLES.tables) & db.tables()  # the site's own code makes none of the old tables now
    assert list(settings.data_dir.glob("state.pre-wts-core.*")) == []
    assert rows(settings.state_db, "SELECT name FROM wts_schema WHERE name LIKE 'import:%'") == []
    assert rows(settings.state_db, "PRAGMA journal_mode") == [("wal",)]


# what the site says of the old rows, read through the shared package: as the old code said it

VISITOR_A, VISITOR_B = "a1" * 12, "b2" * 12
KEY_FRESH, KEY_STALE, KEY_NO_ADDRESS = "a" * 64, "b" * 64, "c" * 64
ADMIN = {"Authorization": f"Bearer {shared.ADMIN_TOKEN}"}


@pytest.fixture
def after(tmp_path, monkeypatch):
    """The site started on the old database (so the import has run), at the moment the old code's figures were
    read, with the topics the old code had."""
    from tests import sides

    folder = tmp_path / "old-state"
    old_database(folder / "data" / "state.sqlite")
    settings = sides.settings_in(folder, monkeypatch)
    monkeypatch.setenv("HANSARD_BACKUP_DIR", str(folder / "no-backups"))
    (folder / "topics.toml").write_text(shared.TOPICS)
    settings.raw["topics"] = {"file": str(folder / "topics.toml")}
    clock = FixedClock(NOW)
    pipeline = shared.ScriptedPipeline(clock.advance, settings.answer["model"])
    side = sides.new_side(pipeline, settings, clock, monkeypatch)
    side.folder = folder
    return side


def test_spending_limits_and_alerts_read_the_same_after_the_import(after, monkeypatch):
    from hansard.api import usage as use
    from tests import sides

    parts, state, clock = after.app.state.parts, after.state, after.clock
    assert use.summary(parts.spending, state, clock) == GOLDEN["summary"]
    assert parts.spending.totals(shared.DAY).spent_usd == pytest.approx(GOLDEN["spent"][shared.DAY])
    assert parts.spending.totals(shared.DAY[:7]).spent_usd == pytest.approx(GOLDEN["spent"][shared.DAY[:7]])
    assert {v: parts.spending.briefings_today(visitor=v) for v in (VISITOR_A, VISITOR_B, "nobody")} == GOLDEN[
        "briefings_today"]
    assert {str(v): use.support_reminder_due(parts.spending, state, v, 2) for v in (VISITOR_A, VISITOR_B, None)} == {
        k.replace("null", "None"): v for k, v in GOLDEN["reminder_due"].items()}
    assert use.recent_failures(parts.failures) == GOLDEN["failures"]
    assert after.app.state.core.get(f"visitor_salt:{shared.DAY}") == GOLDEN["salt"]
    # the limits, with the day's limit set at two as when the old code was asked
    tight = use.desk(sides.settings_in(after.folder, monkeypatch, per_visitor_daily_questions=2,
                                       per_user_daily_questions=2), after.app.state.core, clock)
    researcher = rows(state.path, "SELECT user_id FROM users WHERE email = 'researcher@example.org'")[0][0]
    refused = {}
    for visitor, account in ((VISITOR_A, None), (VISITOR_B, None), ("nobody", None), (None, researcher), (None, 99)):
        refusal = tight.caps.refusal(visitor=visitor, account=account)
        refused[str((visitor, account))] = refusal.message if refusal else None
    assert refused == GOLDEN["refused"]
    assert sum(1 for said in refused.values() if said) == 2
    # the alerts, with the day's cap set at five cents as when the old code was asked
    low = use.desk(sides.settings_in(after.folder, monkeypatch, daily_spend_cap_usd=0.05), after.app.state.core, clock)
    assert use.alerts(low, state) == GOLDEN["alerts"]


def test_cached_answers_and_saved_briefings_read_the_same_after_the_import(after):
    from hansard.api import briefings as saved

    parts, state = after.app.state.parts, after.state
    assert {key: parts.cache.get(key, 168) for key in (KEY_FRESH, KEY_STALE, KEY_NO_ADDRESS)} == GOLDEN["cached"]
    ids = (shared.BRIEFING_1, shared.BRIEFING_2, shared.WITHDRAWN, "briefingad", "briefingzz")
    assert {bid: saved.get_briefing(parts.archive, bid) for bid in ids} == GOLDEN["briefings"]
    # each saved briefing at its own address, as it was shown (a withdrawn one gone, as before)
    assert {bid: list(after.get(f"/api/briefings/{bid}")) for bid in ids} == GOLDEN["shown"]
    assert saved.recent_briefings(parts.archive) == GOLDEN["recent"]
    assert saved.featured_list(parts.archive, state) == GOLDEN["featured"]
    assert list(after.get("/api/topics")) == GOLDEN["topics"]
    assert list(after.get("/api/topics/trial-by-jury")) == GOLDEN["topic"]
    assert list(after.get("/api/status")) == GOLDEN["status"]
    assert after.client.get("/api/admin/briefings", headers=ADMIN).json() == GOLDEN["admin_briefings"]
    assert after.client.get("/api/admin/usage", headers=ADMIN).json() == GOLDEN["admin_usage"]


def test_the_topics_command_lists_each_topic_with_its_briefing_after_the_import(after, monkeypatch):
    """`hansard topics list`, an owner's command from before the move: it reads the topics' briefings through the
    shared package's saved briefings now."""
    from typer.testing import CliRunner

    from hansard import cli

    monkeypatch.setattr(cli, "_settings", lambda: after.settings)
    monkeypatch.setattr(cli, "CLOCK", after.clock)
    result = CliRunner().invoke(cli.app, ["topics", "list"])
    assert result.exit_code == 0, result.output
    listed = {line.split()[0]: line for line in result.output.splitlines()}
    assert "written 2026-11-03" in listed["trial-by-jury"] and "1 version(s)" in listed["trial-by-jury"]
    assert all("not written yet" in line for slug, line in listed.items() if slug != "trial-by-jury")


def test_reports_and_statements_read_the_same_after_the_import_but_for_what_three_months_end(after, monkeypatch):
    """What the owner and the public are shown of the old reports and statements. Four differences, each from the
    shared package's rules (wts-core 0.2.1):

    * R: a statement nobody decided in three months lapses, the first time the owner's list is read: it is
      declined with a reason that says so, and its sender's address deleted. An open report three months old
      loses its contact details the same way. (The old code kept both until the owner acted.)
    * O: a statement taken down after it was published reads "removed", not "declined".
    * R: the public log gives a closed report's date, kind and public note, and no longer the briefing it named.
    * N: the owner's lists are in the order the statements were received by number, not by time; the two agree
      on a real site, where a number is given as each arrives.
    """
    from typer.testing import CliRunner

    from hansard import cli
    from hansard import replies as rp
    from hansard.replies import REPLY_MESSAGES

    desk, archive = after.app.state.replies, after.app.state.parts.archive
    # what the owner can be shown before anything lapses: numbers and ages only (`hansard would-lapse`, read and
    # not done; before the very first deploy the same is read from the old tables by deploy/before-wts-core.py)
    from typer.testing import CliRunner as Runner

    from hansard import cli as command_line

    monkeypatch.setattr(command_line, "_settings", lambda: after.settings)
    monkeypatch.setattr(command_line, "CLOCK", after.clock)
    said = Runner().invoke(command_line.app, ["would-lapse"])
    assert (said.exit_code, said.output) == (0, (
        "At the next tidy, 1 statement(s) nobody has decided would be declined as lapsed, and 1 open report(s) "
        "would lose their contact details (those sent before 2026-08-13). Statements: 7. Reports: 1.\n"))
    lapsing = desk.would_lapse()
    assert [(s.id, s.at[:10]) for s in lapsing.statements] == [(7, "2026-07-01")]
    assert [(r.id, r.at[:10]) for r in lapsing.reports] == [(1, "2026-07-01")]
    paths = (f"/b/{shared.BRIEFING_1}", f"/b/{shared.BRIEFING_2}", shared.MEMBER)
    # what the public reads: published statements beside each item and for each member, exactly as before
    assert {path: rp.published(desk, paths=[path]) for path in paths} == GOLDEN["published"]
    assert {str(n): rp.published(desk, person=n) for n in (10669, 10115)} == GOLDEN["published_for_members"]
    assert rp.corrections(desk, archive) == [
        {**entry, "briefing_id": None} if entry["kind"] != "withdrawn" else entry for entry in GOLDEN["corrections"]]
    assert [entry["briefing_id"] for entry in GOLDEN["corrections"]] == ["briefingab", "briefingac"]

    was = {s["id"]: s for s in GOLDEN["admin_statements"]["statements"]}
    listed = after.client.get("/api/admin/statements", headers=ADMIN).json()["statements"]
    assert [s["id"] for s in listed] == [8, 7, 6, 5, 4, 3, 2, 1]
    assert [s["id"] for s in GOLDEN["admin_statements"]["statements"]] == [2, 8, 1, 3, 5, 4, 7, 6]
    now = {s["id"]: s for s in listed}
    assert was[7]["status"] == "pending" and was[7]["email"] == "waiting@example.org"
    assert now == {**was,
                   5: {**was[5], "status": "removed"},
                   7: {**was[7], "status": "declined", "email": None, "closed_at": NOW,
                       "reason": REPLY_MESSAGES.lapsed}}
    reports = after.client.get("/api/admin/reports", headers=ADMIN).json()["reports"]
    assert reports == [{**r, "contact": None} if r["id"] == 1 else r for r in GOLDEN["admin_reports"]["reports"]]
    assert next(r for r in GOLDEN["admin_reports"]["reports"] if r["id"] == 1)["contact"] == "old@example.org"

    # the command prints each statement as it always has, in the new order and with those two changes
    monkeypatch.setattr(cli, "_settings", lambda: after.settings)
    monkeypatch.setattr(cli, "CLOCK", after.clock)
    result = CliRunner().invoke(cli.app, ["statements", "list", "--status", "all"])
    code, printed = GOLDEN["statements_command"]
    blocks = {int(block[1:block.index(" ")]): block for block in printed.split("\n\n") if block}
    blocks[5] = blocks[5].replace("#5  declined", "#5  removed")
    blocks[7] = ("#7  declined  received 2026-07-01  closed 2026-11-13\n"
                 "  From: Waiting Too Long, MP for Leeds South  <email deleted>\n"
                 "  About: /b/briefingac  (no member set)\n"
                 f"  Reason: {REPLY_MESSAGES.lapsed}\n"
                 "  Nobody decided this one in four months.")
    assert (result.exit_code, result.output) == (code, "".join(blocks[n] + "\n\n" for n in (8, 7, 6, 5, 4, 3, 2, 1)))


# the old tables after the import: kept to the privacy notice's promises each night, then dropped


def old_personal_data(path: Path) -> dict[str, int]:
    return {
        "addresses": rows(path, "SELECT count(*) FROM statements WHERE email IS NOT NULL")[0][0],
        "contacts": rows(path, "SELECT count(*) FROM reports WHERE contact IS NOT NULL")[0][0],
        "failures": rows(path, "SELECT count(*) FROM failures")[0][0],
        "secrets": rows(path, "SELECT count(*) FROM kv WHERE key LIKE 'visitor_salt%'")[0][0],
        "codes on spending": rows(path, "SELECT count(*) FROM usage WHERE visitor IS NOT NULL")[0][0],
        "codes on reports": rows(path, "SELECT count(*) FROM reports WHERE visitor IS NOT NULL")[0][0],
        "codes on statements": rows(path, "SELECT count(*) FROM statements WHERE visitor IS NOT NULL")[0][0],
    }


def test_the_old_tables_keep_to_the_privacy_notice_each_night(old):
    from hansard.owner import replies_for, tidy_replies

    path = old.state_db
    core = open_core(old, FixedClock(NOW))
    at_the_import = old_personal_data(path)
    assert at_the_import == {"addresses": 6, "contacts": 3, "failures": 5, "secrets": 1, "codes on spending": 8,
                             "codes on reports": 3, "codes on statements": 5}
    # the night of the adoption: the package lapses what has waited three months, and the old tables follow.
    # Today's codes and today's secret stay (a way back that day finds each visitor's count), earlier days' go
    tidy_replies(old, FixedClock(NOW))
    done = core.retire_old()
    assert done.dropped == () and set(done.left) == set(OLD_TABLES.tables) and done.waiting_for == ()
    assert old_personal_data(path) == {
        "addresses": 5,           # the statement nobody decided in three months: its sender's address
        "contacts": 2,            # the report left open three months: its contact details
        "failures": 4,            # the note of a failed briefing from August: past its 90 days
        "secrets": 1, "codes on spending": 4,  # today's only
        "codes on reports": 0, "codes on statements": 0}
    assert rows(path, "SELECT DISTINCT substr(at, 1, 10) FROM usage WHERE visitor IS NOT NULL") == [(shared.DAY,)]
    assert rows(path, "SELECT id FROM statements WHERE email IS NULL ORDER BY id") == [(4,), (6,), (7,)]
    assert rows(path, "SELECT id FROM reports WHERE contact IS NULL ORDER BY id") == [(1,), (4,), (5,), (6,)]
    # the rows themselves are all still there, for the way back
    assert {t: rows(path, f"SELECT count(*) FROM {t}")[0][0] for t in ("usage", "statements", "reports")} == {
        "usage": 13, "statements": 8, "reports": 6}
    # the next night: the adoption day's secret and its codes go from the old tables, as from the package's
    tomorrow = FixedClock("2026-11-14T03:10:00+00:00")
    core = open_core(old, tomorrow)
    assert core.retire_old().changed == 5
    after_a_day = old_personal_data(path)
    assert after_a_day["secrets"] == 0 and after_a_day["codes on spending"] == 0
    assert rows(path, "SELECT key FROM kv ORDER BY key") == [
        ("some_other_key",), (f"supporter:{shared.DAY}:{'b2' * 12}",)]  # the site's own keys are not the package's
    # a week on the owner closes a report: its contact details are deleted from the package's row at once, and
    # from the old row that night
    later = FixedClock("2026-11-20T03:10:00+00:00")
    assert rows(path, "SELECT contact FROM reports WHERE id = 2") == [("reader@example.org",)]
    replies_for(old, later).close(2, outcome="resolved", note="The date was put right.")
    open_core(old, later).retire_old()
    assert rows(path, "SELECT contact FROM reports WHERE id = 2") == [(None,)]
    assert rows(path, "SELECT id FROM reports WHERE contact IS NOT NULL") == rows(
        path, "SELECT id FROM wts_reports WHERE contact IS NOT NULL") == [(3,)]


def test_the_old_tables_are_dropped_on_the_day_stated_and_the_site_runs_without_them(old, tmp_path, monkeypatch):
    from datetime import date

    assert OLD_TABLES.drop_on == date(2027, 2, 1)
    core = open_core(old, FixedClock("2027-01-31T03:10:00+00:00"))
    assert set(core.retire_old().left) == set(OLD_TABLES.tables)
    core = open_core(old, FixedClock("2027-02-01T03:10:00+00:00"))
    done = core.retire_old()
    assert set(done.dropped) == set(OLD_TABLES.tables) and done.left == ()
    assert not set(OLD_TABLES.tables) & core.tables()
    assert {"kv", "manifest", "users", "feedback", "wts_spending", "wts_statements"} <= core.tables()
    assert core.retire_old() == type(done)()  # and a night later there is nothing left to do
    # the rows brought across are all still read, and the site's own tables are untouched
    assert rows(old.state_db, "SELECT count(*) FROM wts_spending") == [(13,)]
    assert rows(old.state_db, "SELECT count(*) FROM wts_briefings") == [(4,)]
    assert rows(old.state_db, "SELECT count(*) FROM users") == [(2,)]


def test_the_old_tables_are_not_dropped_while_the_import_has_still_to_run(settings):
    """A database restored from a backup made before the move, opened for the first time after the day stated:
    the import runs on the way in, so the drop finds it done and nothing is lost."""
    old_database(settings.state_db)
    core = open_core(settings, FixedClock("2027-03-01T03:10:00+00:00"))
    assert rows(settings.state_db, "SELECT count(*) FROM wts_spending") == [(13,)]
    assert set(core.retire_old().dropped) == set(OLD_TABLES.tables)
    assert rows(settings.state_db, "SELECT count(*) FROM wts_spending") == [(13,)]
