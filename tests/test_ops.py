"""Backups, restoring one, off-site copies and the alerts for a backup that has stopped: the shared package's
(wts-core, ``wts_core.ops`` and ``wts_core.cli.ops_commands``), with this site's names and lines
(src/hansard/cli.py, src/hansard/store/wts.py). The scenarios recorded on the site's own code before the move
(tests/golden/backups.json, offsite.json) are played again here, and must give the same, word for word.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
import shared
from typer.testing import CliRunner
from wts_core.adapters import b2
from wts_core.ops import (
    OffsiteError,
    backup_alerts,
    check_offsite,
    make_backup,
    restore_backup,
    send_offsite,
)
from wts_core.testing import FixedClock

from hansard import cli
from hansard.config import SITE
from hansard.store.state import StateDB
from hansard.store.wts import BACKUP_ALERTS, BACKUPS

B2_NAMES = ("HANSARD_B2_BUCKET", "HANSARD_B2_KEY_ID", "HANSARD_B2_APPLICATION_KEY")


def run(args: list[str]) -> tuple[int, str]:
    result = CliRunner().invoke(cli.app, args)
    return result.exit_code, result.output


def marker(path: Path) -> str:
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT value FROM kv WHERE key = 'marker'").fetchone()[0]
    finally:
        con.close()


def test_the_backup_commands_and_the_alerts_are_what_they_were(tmp_path, monkeypatch):
    """`hansard backup`, `offsite` and `restore`, every line and every file: a new site, four nights keeping two,
    the local settings in the backup, a restore refused at the question and one made (the database it replaces
    kept with what was still in its write-ahead log), a second restore that never writes over the kept copy,
    backups that are missing, damaged, not a database, another site's, and one repacked by hand; then the
    alerts for each state of the backups' folder. One difference, in a line only the owner sees."""
    def environment(data_dir: Path, **more: str) -> None:
        shared.clear_environment()
        monkeypatch.setenv("HANSARD_DATA_DIR", str(data_dir))
        for name, value in more.items():
            monkeypatch.setenv(name, value)

    def alerts(folder: Path, env: dict[str, str], now: str) -> list[dict[str, str]]:
        if not folder.is_dir():  # as the admin page: nothing is said where there is no backups folder at all
            return []
        found = backup_alerts(folder, BACKUPS, offsite_set_up=b2.configured(SITE, env) is not None,
                              wording=BACKUP_ALERTS, clock=FixedClock(now))
        return [{"level": alert.level, "text": alert.text} for alert in found]

    for name in list(__import__("os").environ):
        if name.startswith("HANSARD_"):
            monkeypatch.delenv(name)
    now = shared.backups_scenario(
        tmp_path / "backups", run=run, environment=environment, alerts=alerts,
        set_day=lambda day: monkeypatch.setattr(cli, "CLOCK", FixedClock(f"{day}T04:20:00+00:00")))
    was = shared.read("backups.json")
    assert [c["case"] for c in now["commands"]] == [c["case"] for c in was["commands"]]

    def words(command: dict) -> dict:
        """A command's result; for one the command line itself refused (exit 2: a file that is not there), the
        words only, since the box they are drawn in is the terminal library's and wraps by the path's length."""
        if command["exit"] != 2:
            return command
        return {**command, "output": "".join(re.findall(r"[^\s│╭╰─╮╯]+", command["output"]))}

    differ = {c["case"]: (w["output"], c["output"]) for c, w in zip(now["commands"], was["commands"], strict=True)
              if words(c) != words(w)}
    assert "File'<tmp>/backups/missing.tar.gz'doesnotexist." in words(now["commands"][10])["output"]
    kept = "<tmp>/data/state.sqlite.before-restore is still there from an earlier restore: move or delete it first"
    assert differ == {
        # O: the sentence ends without its full stop (wts-core docs/AS-BUILT.md, ops: the refusal's words)
        "a second restore never writes over the kept copy": (f"Not restored: {kept}.\n", f"Not restored: {kept}\n"),
    }
    assert now["alerts"] == was["alerts"]
    assert now["restored"] == was["restored"] and now["after"] == was["after"]


def test_off_site_copies_are_what_they_were(tmp_path):
    """A copy stored and the newest kept, a key that cannot delete, an upload that keeps failing, a refused key, a
    key for another bucket, sites sharing a bucket: what is returned, what is in the bucket, and offsite.json."""
    clock = FixedClock("2026-11-03T04:20:05+00:00")
    now = shared.offsite_scenario(
        tmp_path, error=OffsiteError,
        send=lambda archive, keep, env, client: send_offsite(archive, b2.configured(SITE, env, client=client),
                                                             BACKUPS, keep=keep, clock=clock),
        check=lambda env, client: check_offsite(b2.configured(SITE, env, client=client), BACKUPS,
                                                how_to_set_up=b2.how_to_set_up(SITE)))
    was = shared.read("offsite.json")
    assert [step["case"] for step in now] == [step["case"] for step in was]
    for step, before in zip(now, was, strict=True):
        assert step == before, step["case"]


def test_a_backup_holds_both_sets_of_tables_and_restores(settings, tmp_path):
    """The backup is one file with the site's own tables and the shared package's, and the local settings."""
    import tarfile

    from hansard.store.wts import open_core

    state = StateDB(settings.state_db)
    state.set("marker", "before")
    core = open_core(settings)
    core.set("marker", "the package's, before")
    (settings.data_dir / "settings.local.toml").write_text('[archive]\nstart_date = "2015-01-01"\n')
    dest = tmp_path / "backups"
    for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
        made = make_backup(settings.state_db, dest, BACKUPS, keep=2, clock=FixedClock(f"{day}T03:00:00+00:00"))
    with tarfile.open(made) as tar:
        assert sorted(tar.getnames()) == ["settings.local.toml", "state.sqlite"]
    assert sorted(p.name for p in dest.iterdir()) == ["where-they-stand-2026-10-02.tar.gz",
                                                       "where-they-stand-2026-10-03.tar.gz"]
    assert made.stat().st_mode & 0o777 == 0o600  # password hashes and readers' contact details: its owner only
    state.set("marker", "after")
    core.set("marker", "the package's, after")
    restore_backup(made, settings.state_db, BACKUPS)
    assert StateDB(settings.state_db).get("marker") == "before"
    assert open_core(settings).get("marker") == "the package's, before"


def test_a_backup_made_before_the_shared_package_is_restored_and_its_rows_brought_across(settings, tmp_path):
    """The backups kept from before the move hold the old tables only. `hansard restore` takes one, and the
    next start brings its rows across: spending and saved briefings are all there."""
    from hansard.store.wts import open_core

    from .test_import import old_database, rows

    old_database(tmp_path / "old" / "state.sqlite", without=("statements",))  # as before 12 November 2026
    made = make_backup(tmp_path / "old" / "state.sqlite", tmp_path / "backups", BACKUPS,
                       clock=FixedClock("2026-11-10T03:00:00+00:00"))
    open_core(settings, FixedClock("2026-11-20T09:00:00+00:00"))  # the site as it is now, with nothing in it
    code, said = run_in(settings, ["restore", str(made), "--yes"])
    assert code == 0 and said.startswith("Restored. Start the site again. The database it replaced is kept as ")
    open_core(settings, FixedClock("2026-11-20T09:05:00+00:00"))
    assert rows(settings.state_db, "SELECT count(*) FROM wts_spending") == [(13,)]
    assert rows(settings.state_db, "SELECT count(*) FROM wts_briefings") == [(4,)]
    assert rows(settings.state_db, "SELECT count(*) FROM wts_reports") == [(6,)]
    assert rows(settings.state_db, "SELECT count(*) FROM wts_statements") == [(0,)]


def run_in(settings, args: list[str]) -> tuple[int, str]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cli, "_settings", lambda: settings)
        return run(args)


def test_a_swap_that_fails_says_which_files_to_move_back(settings, tmp_path, monkeypatch):
    state = StateDB(settings.state_db)
    state.set("marker", "before")
    out = make_backup(settings.state_db, tmp_path / "backups", BACKUPS, clock=FixedClock("2026-10-01T03:00:00+00:00"))
    state.set("marker", "after")  # in the write-ahead log only, which the restore removes just before the swap
    live, kept = settings.state_db, settings.data_dir / "state.sqlite.before-restore"
    rename = Path.replace

    def fails_for_the_database(self, target):
        if Path(target) == live:
            raise OSError(18, "Invalid cross-device link")
        return rename(self, target)

    monkeypatch.setattr(Path, "replace", fails_for_the_database)
    with pytest.raises(RuntimeError) as failed:
        restore_backup(out, live, BACKUPS)
    said = str(failed.value)
    assert "could not be put in place" in said and "Invalid cross-device link" in said
    assert f"move {kept} to {live} and {kept}-wal to {live}-wal" in said
    code, text = run_in(settings, ["restore", str(out), "--yes"])  # and until that is done, nothing more is touched
    assert code == 1 and "is still there from an earlier restore" in text
    monkeypatch.setattr(Path, "replace", rename)
    # doing what it says brings back everything, the newest writes included
    assert not Path(f"{live}-wal").exists() and kept.exists() and Path(f"{kept}-wal").exists()
    kept.replace(live)
    Path(f"{kept}-wal").replace(f"{live}-wal")
    assert marker(live) == "after"


def test_the_admin_page_lists_backup_alerts_and_an_empty_folder_setting_means_the_default(indexed_settings, tmp_path,
                                                                                          monkeypatch):
    from fastapi.testclient import TestClient

    from .test_api import make_app

    monkeypatch.setenv("HANSARD_ADMIN_TOKEN", "s3cret-token")
    for name in B2_NAMES:
        monkeypatch.delenv(name, raising=False)
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)

    def alerts() -> list[str]:
        usage = client.get("/api/admin/usage", headers={"Authorization": "Bearer s3cret-token"})
        return [a["text"] for a in usage.json()["alerts"]]

    folder = tmp_path / "backups"
    folder.mkdir()
    monkeypatch.setenv("HANSARD_BACKUP_DIR", str(folder))
    assert "No backups have been made yet." in alerts()
    assert "Backups are kept on this server only: no off-site copy is set up." in alerts()
    monkeypatch.delenv("HANSARD_BACKUP_DIR")
    default = alerts()  # /backups: not there on a developer's machine, so nothing to say
    monkeypatch.setenv("HANSARD_BACKUP_DIR", "")  # as "HANSARD_BACKUP_DIR=" in deploy/.env: not the working folder
    assert alerts() == default


def test_the_backup_check_command_says_what_the_admin_page_says_and_fails_when_backups_have_stopped(
        settings, tmp_path, monkeypatch):
    """`hansard backup-check`, for a timer that emails the owner: status 1 when the nightly backup or its off-site
    copy has stopped, 0 for a warning or when all is well."""
    import os

    for name in B2_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli, "CLOCK", FixedClock("2026-11-06T12:00:00+00:00"))
    folder = tmp_path / "backups"
    assert run_in(settings, ["backup-check", "--in", str(folder)]) == (
        1, f"There is no backups folder at {folder}.\n")
    folder.mkdir()
    assert run_in(settings, ["backup-check", "--in", str(folder)]) == (
        0, "warning: No backups have been made yet.\n"
           "warning: Backups are kept on this server only: no off-site copy is set up.\n")
    newest = folder / "where-they-stand-2026-11-01.tar.gz"
    newest.write_bytes(b"x")
    seconds = FixedClock("2026-11-06T12:00:00+00:00").now().timestamp()
    os.utime(newest, (seconds - 3 * 86400, seconds - 3 * 86400))
    assert run_in(settings, ["backup-check", "--in", str(folder)]) == (
        1, "error: The last backup was made over two days ago (where-they-stand-2026-11-01.tar.gz).\n"
           "warning: Backups are kept on this server only: no off-site copy is set up.\n")
    os.utime(newest, (seconds - 3600, seconds - 3600))
    for name in B2_NAMES:
        monkeypatch.setenv(name, "set")
    (folder / "offsite.json").write_text('{"ok": true, "when": "2026-11-06T03:00:00+00:00"}')
    assert run_in(settings, ["backup-check", "--in", str(folder)]) == (0, "Backups are up to date.\n")


# ---- the nightly housekeeping: `hansard tidy` ----------------------------------------------------------------------


def file_bytes(folder: Path, name: str = "state.sqlite") -> bytes:
    """Every byte of the database as it is on disk, its write-ahead log included."""
    return b"".join(p.read_bytes() for p in sorted(folder.glob(f"{name}*")) if "pre-wts-core" not in p.name
                    and not p.name.endswith("-shm"))


def tidy(settings, when: str) -> tuple[int, str]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cli, "CLOCK", FixedClock(when))
        patch.setattr(cli, "_settings", lambda: settings)
        return run(["tidy"])


def test_what_the_nightly_tidy_deletes_is_gone_from_the_files_bytes_and_from_the_next_backup(settings, tmp_path):
    """SQLite does not wipe what it deletes: the bytes stay in the file's unused space, and a backup copies them.
    So the night's housekeeping ends by writing the file out afresh, and what the privacy notice says is deleted
    is then nowhere in the live file, its write-ahead log, or a backup made after: looked for byte by byte."""
    import tarfile

    from hansard.store.wts import open_core

    from .test_import import old_database, rows

    old_database(settings.state_db)
    lapsing, old_report, kept = b"waiting@example.org", b"old@example.org", b"office@example.org"
    # a visitor's code on a spending row of an earlier day, and the adoption day's secret. (The site's own tables
    # of the day's visits and of feedback also hold codes, as they always have: they are not the package's.)
    code, secret = b"1e" * 12, b"5a" * 16
    open_core(settings, FixedClock("2026-11-13T12:00:00+00:00"))  # the move: the old rows are brought across
    before = file_bytes(settings.data_dir)
    assert all(found in before for found in (lapsing, old_report, kept, code, secret))

    status, said = tidy(settings, "2026-11-14T03:35:00+00:00")  # the night after
    assert status == 0, said
    # Every line, in the order the shared command works in (wts-core CHANGELOG 0.3.0, "The nightly tidy does
    # more, in a fixed order"): its own deletions; then the right of reply's, the one step the site gives; then
    # the old tables, after it, so that what the right of reply deleted tonight is gone from the old rows tonight;
    # and last the file written out afresh. (On 0.2.1 the site added five steps of its own for what the command
    # now does itself, and went over the old tables a second time.)
    assert said.splitlines() == [
        "Deleted 0 records of briefings older than 13 months.",
        "Removed the question from 0 records of failed briefings older than 90 days.",
        "Deleted 0 weekly report files older than 13 months.",
        "Forgot the visitor codes on 8 spending rows from before today.",
        "Forgot the signed-in account on 3 spending rows from before today.",
        "Deleted 1 note of failed briefings older than 90 days.",
        "Deleted 1 visitor secret of earlier days.",
        "Deleted 1 cached answer older than the 168 hours the site keeps one.",
        "Deleted 0 copies of the database made before an import, older than 14 days.",
        "Right of reply: 1 statement(s) nobody decided in three months declined, 1 old open report(s) lost their "
        "contact details, 0 confirming address(es) deleted three months after their statements ended.",
        "Kept the site's old tables within what it promises: 20 rows changed or deleted. They are dropped on "
        "2027-02-01.",
        "Wrote the database out afresh: nothing deleted is left in the file.",
    ]
    after = file_bytes(settings.data_dir)
    for gone in (lapsing, old_report, code, secret):
        assert gone not in after, gone
    assert kept in after  # a statement still waiting or shown keeps its sender's address, as before
    assert rows(settings.state_db, "PRAGMA journal_mode") == [("wal",)]  # and the file keeps its write-ahead log
    assert rows(settings.state_db, "PRAGMA integrity_check") == [("ok",)]
    # the copy made before the move still holds everything, as a backup of that day does: it is deleted once it
    # is older than a backup is kept on the server
    copy = settings.data_dir / "state.pre-wts-core.2026-11-13.sqlite"
    assert lapsing in copy.read_bytes()
    made = make_backup(settings.state_db, tmp_path / "backups", BACKUPS, clock=FixedClock("2026-11-15T03:30:00+00:00"))
    with tarfile.open(made) as tar:
        backed_up = tar.extractfile("state.sqlite").read()
    for gone in (lapsing, old_report, code, secret):
        assert gone not in backed_up, gone
    assert kept in backed_up
    status, said = tidy(settings, "2026-11-28T03:35:00+00:00")  # a fortnight on: the copy has gone too
    assert status == 0 and "Deleted 1 copy of the database made before an import, older than 14 days." in said
    assert not copy.exists()


def test_the_night_the_old_tables_are_dropped_nothing_of_them_is_left_in_the_file(settings):
    from hansard.store.wts import OLD_TABLES, open_core

    from .test_import import old_database, rows

    old_database(settings.state_db)
    with sqlite3.connect(settings.state_db) as con:  # words that are in an old table and nowhere else
        con.execute("UPDATE failures SET error = 'only-in-the-old-failures-table' WHERE id = 5")
    open_core(settings, FixedClock("2026-11-13T12:00:00+00:00"))
    with sqlite3.connect(settings.state_db) as con:  # (the package's copy of that row: put right since)
        con.execute("UPDATE wts_failures SET error = 'put right' WHERE id = 5")
    assert b"only-in-the-old-failures-table" in file_bytes(settings.data_dir)
    status, said = tidy(settings, "2027-02-01T03:35:00+00:00")
    assert status == 0, said
    assert ("Dropped the site's old tables, kept since it moved to the shared package: usage, answer_cache, "
            "briefings, failures, reports, statements.") in said
    # the command's own line says which tables went, last but one, and the file is written out afresh after it
    assert said.splitlines()[-2].startswith("Dropped the site's old tables, kept since it moved to the shared ")
    assert said.splitlines()[-1] == "Wrote the database out afresh: nothing deleted is left in the file."
    assert b"only-in-the-old-failures-table" not in file_bytes(settings.data_dir)
    con = sqlite3.connect(settings.state_db)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    con.close()
    assert not set(OLD_TABLES.tables) & tables and {"kv", "manifest", "wts_spending"} <= tables
    assert rows(settings.state_db, "SELECT count(*) FROM wts_briefings") == [(4,)]
    status, said = tidy(settings, "2027-02-02T03:35:00+00:00")  # and the night after, nothing is said of them
    assert status == 0 and "old tables" not in said


def test_tidy_on_a_new_site_does_nothing_and_says_so(settings):
    status, said = tidy(settings, "2026-11-20T03:35:00+00:00")
    assert status == 0, said
    assert "Right of reply: 0 statement(s) nobody decided in three months declined" in said
    assert said.splitlines()[-1] == "Wrote the database out afresh: nothing deleted is left in the file."
    assert "old tables" not in said  # a site that never had them is told nothing of them


def test_the_tidy_says_plainly_when_the_file_could_not_be_written_out_afresh_and_fails_the_second_night(settings):
    """The independent review's reproduction: with another connection part-way through a query, SQLite cannot
    empty the write-ahead log, and says so in the answer to the checkpoint, not with an error. The site's own
    last step once printed "Wrote the database out afresh" all the same, with the deleted bytes still in the log.
    The shared command's last step reads the answer (wts-core 0.3.0; the site's own step is gone): one such night
    is said plainly and tried again; two running end with status 1, so that `hansard-backup.service` fails and
    the alert goes. (It waits 30 seconds for the file each time, so this test takes a minute.)"""
    from hansard.store.wts import open_core

    from .test_import import old_database

    old_database(settings.state_db)
    open_core(settings, FixedClock("2026-11-13T12:00:00+00:00"))
    not_written = ("The database was NOT written out afresh tonight: the site was using it at that moment. What was "
                   "deleted tonight may still be in the file's unused space or in its write-ahead log. It will be "
                   "tried again at the next tidy.")
    lapsing = b"waiting@example.org"  # the address of a statement that lapses on the first night
    reader = sqlite3.connect(settings.state_db, check_same_thread=False)
    held = reader.execute("SELECT * FROM usage")  # part-way through a query, as a reader of the site might be
    held.fetchone()
    status, said = tidy(settings, "2026-11-14T03:35:00+00:00")
    assert status == 0 and said.splitlines()[-1] == not_written
    assert "Wrote the database out afresh" not in said
    assert lapsing in file_bytes(settings.data_dir)  # and so it is: the tidy said the truth
    # a second night the same: status 1, and the unit's alert goes
    status, said = tidy(settings, "2026-11-15T03:35:00+00:00")
    assert status == 1 and said.splitlines()[-1] == not_written + " That is 2 nights running."
    # the reader finishes; the next night the file is written out afresh, and the count starts again
    held.close()
    reader.close()
    status, said = tidy(settings, "2026-11-16T03:35:00+00:00")
    assert status == 0 and said.splitlines()[-1] == "Wrote the database out afresh: nothing deleted is left in the file."
    assert lapsing not in file_bytes(settings.data_dir)
    con = sqlite3.connect(settings.state_db)
    assert con.execute("SELECT count(*) FROM wts_kv WHERE key = 'tidy:nights_not_written_afresh'").fetchone() == (0,)
    assert con.execute("SELECT count(*) FROM kv WHERE key LIKE 'tidy:%'").fetchone() == (0,)  # nor the site's own
    con.close()
    # in the way again the night after: one night again, not the third running
    reader = sqlite3.connect(settings.state_db, check_same_thread=False)
    held = reader.execute("SELECT * FROM wts_spending")
    held.fetchone()
    status, said = tidy(settings, "2026-11-17T03:35:00+00:00")
    assert status == 0 and said.splitlines()[-1] == not_written
    held.close()
    reader.close()
    unit = (Path(__file__).parents[1] / "deploy" / "hansard-backup.service").read_text()
    assert "ExecStopPost=/usr/bin/docker compose run --rm --no-deps -T app hansard tidy" in unit
    assert "OnFailure=alert@%n.service" in unit


def test_the_tidy_removes_an_unfinished_copy_of_the_database_left_by_a_start_that_was_cut_short(settings):
    """A first start killed while the copy before the move is being made leaves it behind, with whatever of the
    old database it had reached (the review's reproduction). The shared command removes any that is more than a
    day old, with its other deletions (wts-core 0.3.0; the site's own step is gone); one being made at that
    moment is left alone."""
    import os

    from hansard.store.wts import open_core

    from .test_import import old_database

    old_database(settings.state_db)
    open_core(settings, FixedClock("2026-11-13T12:00:00+00:00"))
    folder = settings.data_dir
    stale = folder / "state.pre-wts-core.2026-11-13.sqlite.30754.139673523500864.partial"
    journal = folder / (stale.name + "-journal")
    fresh = folder / "state.pre-wts-core.2026-11-20.sqlite.41.7.partial"
    for left in (stale, journal, fresh):
        left.write_bytes(b"part of the old database: office@example.org")
    night = FixedClock("2026-11-20T03:35:00+00:00").now().timestamp()
    for left in (stale, journal):
        os.utime(left, (night - 7 * 86400, night - 7 * 86400))
    os.utime(fresh, (night - 60, night - 60))
    status, said = tidy(settings, "2026-11-20T03:35:00+00:00")
    # the count of what went is in the package's line for copies (wts-core 0.3.0, UPGRADING, "Where MPs Stand"):
    # the site's own line for them, "Deleted 2 files of an unfinished copy…", is gone with its step
    assert status == 0 and "Deleted 2 copies of the database made before an import, older than 14 days." in said
    assert not stale.exists() and not journal.exists() and fresh.exists()
    assert (folder / "state.pre-wts-core.2026-11-13.sqlite").exists()  # the finished copy has its own 14 days
    os.utime(fresh, (night - 86400 * 2, night - 86400 * 2))
    status, said = tidy(settings, "2026-11-21T03:35:00+00:00")
    assert status == 0 and "Deleted 1 copy of the database made before an import, older than 14 days." in said
    assert not fresh.exists()
    status, said = tidy(settings, "2026-11-22T03:35:00+00:00")
    assert "Deleted 0 copies of the database made before an import, older than 14 days." in said
