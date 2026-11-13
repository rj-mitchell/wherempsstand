"""Backing up a new site, and restoring a backup: checked before anything is replaced, and the database it
replaces is kept. (The backup itself is tested in test_sync.py.)"""

from __future__ import annotations

import io
import sqlite3
import tarfile

import pytest
from typer.testing import CliRunner

from hansard.backup import make_backup, restore_backup
from hansard.cli import app
from hansard.store.state import StateDB


def marker(path) -> str | None:
    con = sqlite3.connect(path)
    try:
        row = con.execute("SELECT value FROM kv WHERE key = 'marker'").fetchone()
    finally:
        con.close()
    return row[0] if row else None


def archive_of(path, **members: bytes):
    """A .tar.gz holding the given files, for the cases make_backup would never write."""
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name.replace("__", "."))
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def test_a_new_site_has_nothing_to_back_up(settings, tmp_path, monkeypatch):
    for name in ("HANSARD_B2_BUCKET", "HANSARD_B2_KEY_ID", "HANSARD_B2_APPLICATION_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert make_backup(settings, tmp_path / "b") is None
    r = CliRunner().invoke(app, ["backup", "--to", str(tmp_path / "b")])
    assert r.exit_code == 0 and "Nothing to back up yet" in r.output and "Backed up" not in r.output
    # no empty database is created to be archived as if it were a good backup, and no folder for it
    assert not settings.state_db.exists() and not (tmp_path / "b").exists()
    StateDB(settings.state_db).set("marker", "saved")
    r = CliRunner().invoke(app, ["backup", "--to", str(tmp_path / "b")])
    assert r.exit_code == 0 and "Backed up to" in r.output and "Off-site copy: not set up" in r.output
    assert len(list((tmp_path / "b").glob("where-they-stand-*.tar.gz"))) == 1


def test_restore_puts_a_backup_back_and_keeps_the_database_it_replaces(settings, tmp_path):
    state = StateDB(settings.state_db)
    state.set("marker", "before")
    local = settings.data_dir / "settings.local.toml"
    local.write_text('[archive]\nstart_date = "2015-01-01"\n')
    out = make_backup(settings, tmp_path / "backups", day="2026-10-01")
    state.set("marker", "after")
    state._conn().close()  # the site is stopped first
    local.write_text('[archive]\nstart_date = "2020-01-01"\n')
    (settings.data_dir / "state.sqlite-journal").write_bytes(b"left over")

    kept = restore_backup(settings, out)
    assert marker(settings.state_db) == "before"
    assert kept == settings.data_dir / "state.sqlite.before-restore" and marker(kept) == "after"
    assert settings.state_db.stat().st_mode & 0o777 == 0o600
    assert "2015-01-01" in local.read_text()  # the local settings come back with it
    assert not (settings.data_dir / "state.sqlite-journal").exists()
    # unpacked beside the database, and nothing of that is left behind
    assert sorted(p.name for p in settings.data_dir.iterdir() if not p.name.startswith("state.sqlite.before-restore")) \
        == ["settings.local.toml", "state.sqlite"]


def test_the_kept_database_has_writes_still_in_the_write_ahead_log(settings, tmp_path):
    state = StateDB(settings.state_db)
    state.set("marker", "before")
    out = make_backup(settings, tmp_path / "backups", day="2026-10-01")
    state.set("marker", "after")  # the connection is left open, as when the site is killed: this is in the log only
    assert (settings.data_dir / "state.sqlite-wal").stat().st_size > 0
    kept = restore_backup(settings, out)
    assert marker(kept) == "after" and marker(settings.state_db) == "before"
    # a second restore replaces the kept copy and its log together
    assert marker(restore_backup(settings, out)) == "before"


def test_restore_refuses_a_damaged_or_foreign_file_and_changes_nothing(settings, tmp_path):
    state = StateDB(settings.state_db)
    state.set("marker", "live")
    state._conn().close()
    local = settings.data_dir / "settings.local.toml"
    local.write_text("# live\n")
    good = make_backup(settings, tmp_path / "b", day="2026-10-02")
    (settings.data_dir / "state.sqlite-wal").write_bytes(b"")  # must still be there after every refusal

    bad = archive_of(tmp_path / "where-they-stand-2026-10-01.tar.gz", state__sqlite=b"not a database at all" * 100,
                     settings__local__toml=b"# from the archive\n")
    with pytest.raises(RuntimeError, match="not a database"):
        restore_backup(settings, bad)
    not_a_tar = tmp_path / "notes.tar.gz"
    not_a_tar.write_bytes(b"x" * 1000)
    with pytest.raises(RuntimeError, match="could not be read"):
        restore_backup(settings, not_a_tar)
    truncated = tmp_path / "cut.tar.gz"
    truncated.write_bytes(good.read_bytes()[: good.stat().st_size // 2])
    with pytest.raises(RuntimeError, match="could not be read"):
        restore_backup(settings, truncated)
    empty = archive_of(tmp_path / "empty.tar.gz", settings__local__toml=b"# from the archive\n")
    with pytest.raises(RuntimeError, match="no state.sqlite"):  # was: an empty database replaced the live one
        restore_backup(settings, empty)
    other = tmp_path / "other.sqlite"
    con = sqlite3.connect(other)
    con.execute("CREATE TABLE usage (x)")
    con.execute("CREATE TABLE briefings (x)")  # another site's state database has these two, but not all four
    con.commit()
    con.close()
    foreign = archive_of(tmp_path / "foreign.tar.gz", state__sqlite=other.read_bytes())
    with pytest.raises(RuntimeError, match="not a backup of this site's state database"):
        restore_backup(settings, foreign)

    assert sorted(p.name for p in settings.data_dir.iterdir()) == ["settings.local.toml", "state.sqlite",
                                                                   "state.sqlite-wal"]
    assert marker(settings.state_db) == "live" and local.read_text() == "# live\n"


def test_the_restore_command(settings, tmp_path):
    state = StateDB(settings.state_db)
    state.set("marker", "before")
    out = make_backup(settings, tmp_path / "b", day="2026-10-01")
    state.set("marker", "after")
    state._conn().close()
    r = CliRunner().invoke(app, ["restore", str(out)], input="n\n")  # the question is still asked
    assert r.exit_code == 1 and "This replaces the current saved briefings" in r.output
    assert marker(settings.state_db) == "after"
    r = CliRunner().invoke(app, ["restore", str(out), "--yes"])
    assert r.exit_code == 0, r.output
    assert "Restored. Start the site again." in r.output and "state.sqlite.before-restore" in r.output
    assert marker(settings.state_db) == "before"
    not_a_backup = tmp_path / "notes.tar.gz"
    not_a_backup.write_bytes(b"x" * 1000)
    r = CliRunner().invoke(app, ["restore", str(not_a_backup), "--yes"])
    assert r.exit_code == 1 and "Not restored: notes.tar.gz could not be read" in r.output
    assert "Traceback" not in r.output and marker(settings.state_db) == "before"
