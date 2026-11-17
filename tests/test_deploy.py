"""The server's files (deploy/): the order `update.sh` works in, the nightly unit's time limit, the read-only look
at the old database before the move onto the shared package, and the script for going back.

Independent reviews of the sister sites' adoptions found three faults in how a deploy would have gone. `update.sh`
installed the new timers before it built the new image, so after a failed build they would have run commands the
old image lacks. The one-off import of the old rows runs on whichever command first opens the database on new
code, so a timer's command in the gap before the site restarted would have imported while the old site was still
writing, and what it wrote afterwards would have been left behind. And the housekeeping after the nightly backup
(``ExecStopPost``) had systemd's 90-second stop timeout, not the unit's 30 minutes.

Nothing here can run a real deploy: `update.sh` is run in a folder of its own with stand-ins for git, docker,
systemctl and the server's other scripts that note each call, so its order is what it does, not what its text says.
"""

from __future__ import annotations

import json
import os
import runpy
import shutil
import sqlite3
import subprocess
import sys

import pytest
import shared
from wts_core.testing import FixedClock

from hansard.config import REPO_ROOT
from hansard.owner import replies_for
from hansard.store.wts import OLD_TABLES, open_core

from .test_import import dump, old_database, rows

DEPLOY = REPO_ROOT / "deploy"
JOBS = ("hansard-sync", "hansard-backup", "hansard-backup-check", "hansard-weekly-report")
FAMILY = ("wheretheystand-watchdog", "wheretheystand-disk", "wheretheystand-disk-report")


STAND_IN = r"""#!/usr/bin/env bash
# A stand-in for git, docker, systemctl, flock and the server's other scripts: notes what it was asked, and answers
# as the test said it should.
name=$(basename "$0")
echo "$name $*" >> "$FAKE/calls"
case "$name $*" in
  "git -C "*" rev-parse "*) echo abc1234 ;;
  "systemctl is-active --quiet "*) [[ -e "$FAKE/timers/$3" ]] ;;
  "systemctl is-active "*) if [[ -e "$FAKE/running/$2" ]]; then echo activating; exit 3; else echo inactive; exit 3; fi ;;
  "systemctl stop "*) shift 1; for t in "$@"; do [[ -e "$FAKE/stop-fails/$t" ]] && exit 1; rm -f "$FAKE/timers/$t"; done ;;
  "systemctl start "*) shift 1; for t in "$@"; do touch "$FAKE/timers/$t"; done ;;
  "systemctl enable --now "*) shift 2; for t in "$@"; do touch "$FAKE/timers/$t"; done ;;
  "docker compose build") [[ ! -e "$FAKE/build-fails" ]] ;;
  "docker compose run "*" hansard check-settings") [[ ! -e "$FAKE/settings-fail" ]] ;;
  "docker compose run "*) exit 1 ;;   # the look for files not owned by the site's user: none
  "docker compose exec "*) [[ ! -e "$FAKE/not-answering" ]] ;;
  "flock -n "*) [[ ! -e "$FAKE/lock-held" ]] ;;
esac
"""


@pytest.fixture
def server(tmp_path, monkeypatch):
    """A stand-in for the server: a copy of deploy/ in a folder of its own, a folder for systemd's units, and git,
    docker, systemctl and sleep replaced by a script that notes each call; the server's other scripts (swap, the
    web server's settings, the health check) likewise. The site's timers are running, as on the server before a
    deploy (the two that are new with this version are not there yet)."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash here")
    fake, site, bin_ = tmp_path / "fake", tmp_path / "site", tmp_path / "bin"
    for folder in (fake / "timers", fake / "running", fake / "stop-fails", tmp_path / "units", bin_):
        folder.mkdir(parents=True)
    shutil.copytree(DEPLOY, site / "deploy")
    (site / "deploy" / ".env").write_text("", encoding="utf-8")
    (site / "deploy" / "local" / "sites").mkdir(parents=True)  # the web server's own settings, as on the server
    (site / "deploy" / "local" / "site.caddy").write_text("# this server's settings\n")
    (site / "deploy" / "local" / "sites" / "example").write_text("# another site\n")
    for name in ("git", "docker", "systemctl", "sleep", "flock"):
        (bin_ / name).write_text(STAND_IN, encoding="utf-8")
        (bin_ / name).chmod(0o755)
    for script in ("swap.sh", "housekeeping.sh", "caddy-local.sh", "check.sh"):
        (site / "deploy" / script).write_text(f'#!/usr/bin/env bash\necho "{script}" >> "$FAKE/calls"\n')
    for timer in ("hansard-sync", "hansard-backup", *FAMILY):
        (fake / "timers" / f"{timer}.timer").touch()
    monkeypatch.setenv("PATH", f"{bin_}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE", str(fake))
    monkeypatch.setenv("DIR", str(site))
    monkeypatch.setenv("UNITS_DIR", str(tmp_path / "units"))
    monkeypatch.setenv("UPDATE_LOCK", str(tmp_path / "update.lock"))

    def deploy():
        done = subprocess.run([bash, str(site / "deploy" / "update.sh")], capture_output=True, text=True)
        calls = (fake / "calls").read_text(encoding="utf-8").splitlines() if (fake / "calls").exists() else []
        return done, calls

    deploy.fake, deploy.units = fake, tmp_path / "units"
    deploy.timers = lambda: sorted(p.name for p in (fake / "timers").iterdir())
    return deploy


def position(calls: list[str], wanted: str) -> int:
    found = [n for n, call in enumerate(calls) if call.startswith(wanted)]
    assert found, f"update.sh never ran {wanted!r}: {calls}"
    return found[0]


def no_site_command(calls: list[str]) -> bool:
    """No call is a `hansard` command in the site's image but the read-only look at the settings: the only other
    `docker compose run` is a shell's."""
    look = "docker compose run --rm --no-deps -T app hansard check-settings"
    others = [call for call in calls if call != look]
    return all("--entrypoint sh" in call for call in others if call.startswith("docker compose run")) \
        and not any(" hansard " in call for call in others)


def units_of_the_site(folder) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if p.name.startswith("hansard-"))


def test_update_sh_holds_the_timers_builds_and_brings_the_new_site_up_and_only_then_installs_the_units(server):
    done, calls = server()
    assert done.returncode == 0, done.stdout + done.stderr
    held = position(calls, "systemctl stop hansard-sync.timer")
    build, up = position(calls, "docker compose build"), position(calls, "docker compose up -d")
    preflight = position(calls, "docker compose run --rm --no-deps -T app hansard check-settings")
    lock, unlock = position(calls, "flock -n 8"), position(calls, "flock -u 8")
    answers = position(calls, "docker compose exec")
    prune = position(calls, "docker image prune")
    enable = position(calls, "systemctl enable --now hansard-sync.timer")
    # the new image is tried on the server's settings before anything of the old site is stopped; the update lock
    # is held from before the restart until the site answers (the watchdog restarts nothing meanwhile); and old
    # images are cleared out, and the units installed, only once it has answered
    assert position(calls, "git -C") < held < build < preflight < lock < up < answers < unlock < prune < enable \
        < position(calls, "check.sh")
    # each timer that was running was held before the build, and all four run on the new units at the end
    for job in ("hansard-sync", "hansard-backup"):
        assert position(calls, f"systemctl stop {job}.timer") < build
    assert server.timers() == sorted(f"{name}.timer" for name in (*JOBS, *FAMILY))
    # the site's units installed are this version's, every one of them, and only once the site had answered
    assert units_of_the_site(server.units) == units_of_the_site(DEPLOY) == sorted(
        f"{job}.{kind}" for job in JOBS for kind in ("service", "timer"))
    # no `hansard` command that could open the state database for writing was run before the site restarted (nor
    # after): whatever opens it first on new code makes the one-off move, and must be the site. The one command
    # run is the look at the settings, which changes nothing (tested above: not a byte)
    assert no_site_command(calls)


def test_update_sh_stops_with_the_old_site_still_running_when_the_new_version_would_refuse_its_settings(server):
    """The independent review's first finding: a model in the server's own settings with no prices stopped the new
    site at its start, after the database had been moved. The new image is now tried on the server's settings
    first, read-only; if it would refuse, nothing has been stopped, moved or installed."""
    (server.fake / "settings-fail").touch()
    done, calls = server()
    assert done.returncode == 1 and "The new version would not start on this server" in done.stdout
    assert "Nothing has been changed" in done.stdout
    assert not any(call.startswith(("docker compose up", "docker compose stop", "docker image prune", "flock",
                                    "systemctl enable --now hansard")) for call in calls)
    assert units_of_the_site(server.units) == []
    assert server.timers() == sorted(f"{name}.timer" for name in ("hansard-sync", "hansard-backup", *FAMILY))


def test_update_sh_starts_the_timers_again_when_stopping_one_of_them_fails(server):
    """The review's reproduction: the trap that releases the timers was set after the loop that stops them, so a
    `systemctl stop` that failed left the timer stopped before it stopped for good."""
    (server.fake / "stop-fails" / "hansard-backup.timer").touch()
    done, calls = server()
    assert done.returncode != 0
    assert position(calls, "systemctl stop hansard-sync.timer") < position(calls, "systemctl stop hansard-backup.timer") \
        < position(calls, "systemctl start")
    assert server.timers() == sorted(f"{name}.timer" for name in ("hansard-sync", "hansard-backup", *FAMILY))
    assert not any(call.startswith("docker") for call in calls)  # and nothing was built


def test_update_sh_does_not_restart_the_site_while_another_sites_update_holds_the_lock(server):
    (server.fake / "lock-held").touch()
    done, calls = server()
    assert done.returncode == 1 and "Another site's update is running" in done.stdout
    assert not any(call.startswith(("docker compose up", "docker compose stop", "docker image prune")) for call in calls)
    assert server.timers() == sorted(f"{name}.timer" for name in ("hansard-sync", "hansard-backup", *FAMILY))


def test_update_sh_leaves_the_sites_units_alone_when_the_build_fails_and_starts_the_timers_again(server):
    (server.fake / "build-fails").touch()
    done, calls = server()
    assert done.returncode != 0
    assert units_of_the_site(server.units) == []  # not one new unit: the old image has no such commands
    assert not any(call.startswith(("docker compose up", "systemctl enable --now hansard")) for call in calls)
    # the two that were running are running again, on the units already installed; the new ones are not started
    assert server.timers() == sorted(f"{name}.timer" for name in ("hansard-sync", "hansard-backup", *FAMILY))
    assert position(calls, "systemctl start") > position(calls, "docker compose build") and no_site_command(calls)
    assert position(calls, "systemctl stop hansard-backup.timer") < position(calls, "docker compose build")


def test_update_sh_leaves_the_sites_units_and_the_old_image_alone_when_the_new_site_does_not_answer(server, monkeypatch):
    (server.fake / "not-answering").touch()
    done, calls = server()
    assert done.returncode == 1 and "did not answer within ten minutes" in done.stdout
    assert "Its timers are as they were" in done.stdout
    # it waited as long as the health check's start period (ten minutes: 200 tries, 3 seconds apart), holding the
    # update lock throughout; and nothing was cleared out, so the image the site ran on is there to go back to
    assert sum(1 for call in calls if call.startswith("docker compose exec")) == 200
    assert calls.count("sleep 3") == 200 and "flock -u 8" not in calls
    assert not any(call.startswith(("docker image prune", "docker builder prune")) for call in calls)
    assert "the image it ran on before is still here" in done.stdout and "(abc1234)" in done.stdout
    assert "undo-wts-core.sql" in done.stdout
    compose = (DEPLOY / "docker-compose.yml").read_text()
    assert "start_period: 10m" in compose and 'WAIT_TRIES:-200}"' in (DEPLOY / "update.sh").read_text()
    assert units_of_the_site(server.units) == []
    assert not any(call.startswith("systemctl enable --now hansard") for call in calls)
    assert server.timers() == sorted(f"{name}.timer" for name in ("hansard-sync", "hansard-backup", *FAMILY))
    assert "check.sh" not in calls


@pytest.mark.parametrize("job", JOBS)
def test_update_sh_does_not_start_while_one_of_the_sites_jobs_is_running(server, job):
    (server.fake / "running" / f"{job}.service").touch()
    done, calls = server()
    assert done.returncode == 1 and f"{job}.service is running" in done.stdout
    assert not any(call.startswith(("docker", "systemctl stop")) for call in calls)  # nothing built, nothing held
    assert server.timers() == sorted(f"{name}.timer" for name in ("hansard-sync", "hansard-backup", *FAMILY))


def test_the_servers_scripts_are_valid_bash():
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash here")
    for script in ("update.sh", "check.sh", "provision.sh"):
        assert subprocess.run([bash, "-n", str(DEPLOY / script)], capture_output=True).returncode == 0, script
    # the health check asks the site's present code whether the key works (the modules it named before are gone)
    check = (DEPLOY / "check.sh").read_text()
    assert "from hansard.adapters import has_model, model_client" in check and "hansard.rag.answer" not in check
    from hansard import adapters

    assert callable(adapters.has_model) and callable(adapters.model_client)


def test_the_housekeeping_after_the_backup_has_its_own_time_limit_and_every_timer_has_its_unit():
    unit = (DEPLOY / "hansard-backup.service").read_text(encoding="utf-8")
    service = dict(line.split("=", 1) for line in unit.split("[Service]")[1].splitlines()
                   if "=" in line and not line.startswith("#"))
    assert service["ExecStopPost"].endswith("hansard tidy")
    # ExecStopPost is timed by TimeoutStopSec, whose default is 90 seconds
    assert service["TimeoutStopSec"] == "30min" == service["TimeoutStartSec"]
    assert sorted(p.stem for p in DEPLOY.glob("hansard-*.timer")) == sorted(
        p.stem for p in DEPLOY.glob("hansard-*.service")) == sorted(JOBS)
    # each unit's command is one the site has
    import typer

    from hansard import cli

    commands = set(typer.main.get_command(cli.app).commands)
    for job in JOBS:
        text = (DEPLOY / f"{job}.service").read_text()
        ran = [line.split(" app hansard ", 1)[1].split()[0] for line in text.splitlines() if " app hansard " in line]
        assert ran and set(ran) <= commands, job
        assert "OnFailure=alert@%n.service" in text, job


# a look at the state database before the move (deploy/before-wts-core.py)

LOOK = DEPLOY / "before-wts-core.py"


@pytest.fixture
def old(settings):
    """A data folder holding a state database the old code made (tests/golden/old-state.sql)."""
    old_database(settings.state_db)
    return settings


def test_what_would_lapse_is_shown_before_the_deploy_and_is_what_then_lapses(old):
    """The first write or listing after the move lapses whatever has already waited three months: a statement is
    declined, and an open report loses its contact details. The owner is shown which beforehand, by a script that
    runs on the old site as it stands (no `hansard` command: one on new code would make the move), reads only, and
    names nobody. It says what the package's own ``would_lapse`` says after the move, and that is what happens."""
    script = runpy.run_path(str(LOOK))
    clock = FixedClock(shared.START)
    before = dump(old.state_db)
    cutoff, statements, reports = script["would_lapse"](str(old.state_db), clock.now())
    said, stops = script["lines"](str(old.state_db), clock.now())
    assert dump(old.state_db) == before  # read, and nothing changed: the old database is as it was
    assert [sid for sid, _ in statements] == [7] and [rid for rid, _ in reports] == [1]
    assert said[0] == "Sent before 2026-08-13 and still undecided: 1 statements, 1 reports with contact details."
    assert said[1].startswith("  statement 7: sent 2026-07-01, 135 days ago: it would be declined as lapsed")
    assert said[2].startswith("  report 1: sent 2026-07-01, 135 days ago: it would stay open, and its contact")
    assert said[3:] == ["Nothing would stop the move."] and not stops
    for kept_back in ("example.org", "Waiting Too Long", "Nobody decided", "An old report"):
        assert kept_back not in "\n".join(said)
    assert "The database is " in script["room"](str(old.state_db))

    # after the move, and before anything is listed or written: the package says the same of its own tables, and
    # so does the script, which reads them now
    open_core(old, clock)
    desk = replies_for(old, clock)
    would = desk.would_lapse()
    assert would.before == cutoff
    assert [(s.id, s.at) for s in would.statements] == statements
    assert [(r.id, r.at) for r in would.reports] == reports
    assert script["would_lapse"](str(old.state_db), clock.now()) == (cutoff, statements, reports)
    # the first listing does it: exactly those, and no others
    waiting = {s["id"] for s in desk.statements(status="pending")}
    assert waiting == {2} and desk.statement(7)["status"] == "declined" and not desk.statement(7)["email"]
    open_reports = {r["id"]: r for r in desk.reports()}
    assert open_reports[1]["contact"] is None and open_reports[2]["contact"] == "reader@example.org"
    assert script["lines"](str(old.state_db), clock.now()) == ([
        "Sent before 2026-08-13 and still undecided: 0 statements, 0 reports with contact details.",
        "This database has already been moved onto the shared package."], False)


def test_a_row_that_would_stop_the_move_is_named_beforehand_with_how_to_mend_it(old):
    """The move copies the old rows in one transaction when the new code first opens the database. The old tables
    have required every column the package's need since the site's first release, so nothing the old code wrote
    stops it; a row put in by hand without one would. The look beforehand names it and the mend; and it would
    indeed have stopped the move, with nothing half copied."""
    script = runpy.run_path(str(LOOK))
    clock = FixedClock(shared.START)
    with sqlite3.connect(old.state_db) as con:  # a failure's note with no time, as a hand-made table would allow
        con.execute("ALTER TABLE failures RENAME TO failures_as_made")
        con.execute("CREATE TABLE failures (id INTEGER PRIMARY KEY, at TEXT, kind TEXT, question TEXT, error TEXT)")
        con.execute("INSERT INTO failures (kind, error) VALUES ('briefing', 'no time was kept')")
    before = dump(old.state_db)
    said, stops = script["lines"](str(old.state_db), clock.now())
    assert stops and dump(old.state_db) == before
    (line,) = [line for line in said if "would stop the move" in line]
    assert line.startswith("In failures, 1 row(s) have no at and would stop the move (rowid 1).")
    assert "DELETE FROM failures WHERE at IS NULL;" in line and "no time was kept" not in line
    done = subprocess.run([sys.executable, str(LOOK), str(old.state_db)], capture_output=True, text=True)
    assert done.returncode == 1 and "would stop the move" in done.stdout
    with pytest.raises(sqlite3.IntegrityError):  # and it would
        open_core(old, clock)
    assert rows(old.state_db, "SELECT count(*) FROM wts_spending") == [(0,)]  # with nothing half copied
    with sqlite3.connect(old.state_db) as con:  # mended as the script says, the move goes through
        con.execute("DELETE FROM failures WHERE at IS NULL")
    assert script["would_stop_the_move"](str(old.state_db)) == []
    open_core(old, clock)
    assert rows(old.state_db, "SELECT count(*) FROM wts_spending") == [(13,)]
    assert subprocess.run([sys.executable, str(LOOK), str(old.state_db)], capture_output=True).returncode == 0


def test_the_look_says_when_the_database_is_not_the_old_codes_and_reads_a_new_site(tmp_path, settings):
    script = runpy.run_path(str(LOOK))
    now = FixedClock(shared.START).now()
    # a database with one of the two tables the move waits for, and not the other: the move would be passed over
    partial = tmp_path / "state.sqlite"
    con = sqlite3.connect(partial)
    con.execute("CREATE TABLE usage (id INTEGER PRIMARY KEY, at TEXT, kind TEXT)")
    con.commit()
    con.close()
    said, stops = script["lines"](str(partial), now)
    assert stops and "There is no briefings table" in said[-1]
    # a new site on the new code (none of the old tables): nothing to bring, nothing wrong
    open_core(settings, FixedClock(shared.START))
    assert script["lines"](str(settings.state_db), now) == ([
        "Sent before 2026-08-13 and still undecided: 0 statements, 0 reports with contact details.",
        "Nothing would stop the move."], False)
    # and no database at all (a first install): nothing to look at
    done = subprocess.run([sys.executable, str(LOOK), str(tmp_path / "none.sqlite")], capture_output=True, text=True)
    assert done.returncode == 0 and "nothing to look at" in done.stdout


# going back to the code before the move (deploy/undo-wts-core.sql)

UNDO = DEPLOY / "undo-wts-core.sql"
OLD_COLUMNS = {
    "usage": "id, user_id, at, kind, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, "
             "cost_usd, visitor",
    "answer_cache": "key, created_at, events",
    "failures": "id, at, kind, question, error",
    "briefings": "id, created_at, question, settings, events, records_up_to, model, withdrawn_at, withdrawn_reason",
    "reports": "id, at, kind, briefing_id, page, message, contact, status, closed_at, resolution, public_note",
    "statements": "id, at, name, role, item_path, person, text, link, email, status, published_at, updated_at, "
                  "closed_at, reason, checked",
}


def old_rows(path) -> dict[str, list[tuple]]:
    return {table: rows(path, f"SELECT {columns} FROM {table} ORDER BY 1") for table, columns in OLD_COLUMNS.items()}


def undo(path) -> None:
    con = sqlite3.connect(path)
    try:
        con.executescript(UNDO.read_text(encoding="utf-8"))
    finally:
        con.close()


def run_sql(path, name: str) -> None:
    con = sqlite3.connect(path)
    try:
        con.executescript((DEPLOY / name).read_text(encoding="utf-8"))
    finally:
        con.close()


def moved(path) -> bool:
    return rows(path, "SELECT count(*) FROM wts_schema WHERE name = 'import:hansard-state'") == [(1,)]


def test_going_back_straight_after_the_move_finds_the_old_tables_as_they_were_and_the_move_can_be_made_again(old):
    """The move, then the undo, with nothing done between: every old table reads as it did (but for the visitor's
    code on reports and statements, which the package never took and so cannot give back), and the move is
    forgotten, so the new code makes it afresh when it is next started."""
    before = old_rows(old.state_db)
    own = [line for line in dump(old.state_db) if not any(f'"{t}"' in line or f" {t} " in line or f" {t}(" in line
                                                          for t in (*OLD_TABLES.tables, "kv"))]
    open_core(old, FixedClock(shared.START))
    assert moved(old.state_db)
    undo(old.state_db)
    assert old_rows(old.state_db) == before
    assert rows(old.state_db, "SELECT count(*) FROM reports WHERE visitor IS NOT NULL") == [(0,)]
    assert rows(old.state_db, "SELECT key FROM kv ORDER BY key") == [
        ("some_other_key",), (f"supporter:{shared.DAY}:{'b2' * 12}",), (f"visitor_salt:{shared.DAY}",)]
    # the site's own tables were never moved, and are untouched
    assert [line for line in dump(old.state_db) if line in set(own)] == own
    # the move is forgotten: its mark is gone and the package's copies are empty
    assert not moved(old.state_db)
    for table in ("wts_spending", "wts_briefings", "wts_answer_cache", "wts_failures", "wts_reports",
                  "wts_statements", "wts_statement_log"):
        assert rows(old.state_db, f"SELECT count(*) FROM {table}") == [(0,)], table
    # the old code runs for a while, and takes in a briefing; then the new code is started again: the move is
    # made afresh, with that briefing in it
    with sqlite3.connect(old.state_db) as con:
        con.execute("INSERT INTO briefings (id, created_at, question, settings, events) VALUES "
                    "('meanwhikea', '2026-11-14T15:00:00+00:00', 'Asked on the old code, after going back?', '{}', "
                    "'[]')")
    open_core(old, FixedClock("2026-11-16T09:00:00+00:00"))
    assert moved(old.state_db)
    assert rows(old.state_db, "SELECT count(*) FROM wts_spending") == [(13,)]
    assert rows(old.state_db, "SELECT question FROM wts_briefings WHERE id = 'meanwhikea'") == [
        ("Asked on the old code, after going back?",)]
    assert rows(old.state_db, "SELECT count(*) FROM wts_statements") == [(8,)]
    assert rows(old.state_db, "SELECT status FROM wts_statements WHERE id = 5") == [("removed",)]


def test_going_back_after_some_days_of_use_gives_the_old_code_everything_the_site_still_keeps(old, monkeypatch):
    """After the move, some days of use and the nightly housekeeping, the undo writes the old tables again from the
    package's: what came in since is there, what was deleted on schedule stays deleted, and what the old tables
    cannot hold is folded into what they can. Coming forward again brings it all back across."""
    from typer.testing import CliRunner
    from wts_core.usage import BriefingArchive

    from hansard import cli
    from hansard.api.briefings import new_briefing_id
    from hansard.api.usage import desk as desk_parts

    clock = FixedClock(shared.START)
    core = open_core(old, clock)
    desk = replies_for(old, clock)
    # what came in since: a report, a statement published and then taken down, one updated, a briefing, spending
    clock.advance(hours=1)
    report = desk.report(kind="correction", page=None, message="Sent after the move.", contact="after@example.org")
    sent = desk.submit(name="Sent After", role="MP for Leeds South", item="/people/10669", link=None,
                       text="A statement sent after the move, of enough words.", email="after@example.org")
    desk.publish(sent, checked="Telephoned the office, after the move.", about="10669")
    clock.advance(hours=1)
    desk.remove(sent, "It named a private person.")
    desk.update(1, link="https://hansard.parliament.uk/commons/2021-03-05")  # published on the 10th: a new date
    parts = desk_parts(old, core, clock)
    parts.spending.add("answer", "claude-sonnet-5-5", shared.USAGES[4], visitor="c3" * 12)
    saved = BriefingArchive(core, new_id=new_briefing_id, clock=clock).save(
        "Asked after the move?", {"mode": "summary"}, shared.a_saved_briefing("Asked after the move?"),
        extra={"records_up_to": "2026-11-12", "model": "claude-sonnet-5-5"})
    # two nights' housekeeping: what has waited three months lapses, and yesterday's codes are forgotten
    monkeypatch.setattr(cli, "_settings", lambda: old)
    for night in ("2026-11-14T03:35:00+00:00", "2026-11-15T03:35:00+00:00"):
        monkeypatch.setattr(cli, "CLOCK", FixedClock(night))
        assert CliRunner().invoke(cli.app, ["tidy"]).exit_code == 0
    kept = {table: rows(old.state_db, f"SELECT count(*) FROM {table}")[0][0]
            for table in ("wts_spending", "wts_briefings", "wts_reports", "wts_statements", "wts_failures")}
    assert kept == {"wts_spending": 14, "wts_briefings": 5, "wts_reports": 7, "wts_statements": 9, "wts_failures": 4}

    undo(old.state_db)
    path = old.state_db
    # every row the package kept is in the old table, under the old names
    assert {t: rows(path, f"SELECT count(*) FROM {t}")[0][0] for t in ("usage", "briefings", "reports", "statements",
                                                                      "failures")} == {
        "usage": 14, "briefings": 5, "reports": 7, "statements": 9, "failures": 4}
    assert rows(path, "SELECT count(*) FROM usage WHERE visitor IS NOT NULL") == [(0,)]  # forgotten stays forgotten
    assert rows(path, "SELECT id, question, records_up_to, model FROM briefings WHERE id = ?", saved.id) == [
        (saved.id, "Asked after the move?", "2026-11-12", "claude-sonnet-5-5")]
    assert rows(path, "SELECT kind, message, contact, status FROM reports WHERE id = ?", report) == [
        ("correction", "Sent after the move.", "after@example.org", "open")]
    assert rows(path, "SELECT contact FROM reports WHERE id = 1") == [(None,)]  # lapsed: its contact stays deleted
    # the statement taken down after it was published is "declined" with the time it was published, as the old
    # code recorded one; the note of how its sender was confirmed is back in its column
    assert rows(path, "SELECT status, published_at IS NOT NULL, reason, checked, person FROM statements WHERE id = ?",
                sent) == [("declined", 1, "It named a private person.", "Telephoned the office, after the move.",
                           10669)]
    assert rows(path, "SELECT status, email, reason FROM statements WHERE id = 7") == [
        ("declined", None, "No decision was made in time; please send it again.")]
    # one updated since it was published is shown with the date of the update, as the old code showed it
    assert rows(path, "SELECT published_at = updated_at, substr(published_at, 1, 13), link FROM statements "
                      "WHERE id = 1") == [(1, "2026-11-13T14", "https://hansard.parliament.uk/commons/2021-03-05")]
    assert rows(path, "SELECT DISTINCT status FROM statements ORDER BY 1") == [
        ("declined",), ("pending",), ("published",), ("withdrawn",)]  # only the words the old code knows
    assert rows(path, "PRAGMA integrity_check") == [("ok",)]
    assert not moved(path) and rows(path, "SELECT count(*) FROM wts_statements") == [(0,)]
    # coming forward again, days later: everything is brought across afresh, the taken-down statement "removed"
    # once more, the lapsed one still declined
    back = open_core(old, FixedClock("2026-11-18T09:00:00+00:00"))
    assert moved(path)
    assert {table: rows(path, f"SELECT count(*) FROM {table}")[0][0] for table in kept} == kept
    after = replies_for(old, FixedClock("2026-11-18T09:00:00+00:00"))
    assert after.statement(sent)["status"] == "removed" and after.statement(7)["status"] == "declined"
    assert BriefingArchive(back, new_id=new_briefing_id).get(saved.id).question == "Asked after the move?"


def test_going_back_after_the_forgetting_gives_back_what_is_said_and_a_second_move_counts_no_account_twice(
        old, monkeypatch):
    """wts-core 0.3.0 (CHANGELOG, "A spending row says who it was for, and what time of day it was made, for its own
    day only"; UPGRADING, "What the undo script must clear"). Once a day is over its spending rows keep no account
    and no time of day, and each account's month is kept in ``wts_account_months``. So the undo gives back, for a
    row of a day gone by, no user_id, no visitor and the day's midnight, with its cost and day exact; today's rows
    whole. The script itself empties the month totals with the spending rows, so that coming forward again does not
    bring the same spending back beside them and count it twice in the owner's report of who it was for."""
    from typer.testing import CliRunner

    from hansard import cli
    from hansard.api.usage import desk as desk_parts
    from hansard.api.usage import summary
    from hansard.store.state import StateDB

    def report(clock) -> dict:
        core = open_core(old, clock)
        return summary(desk_parts(old, core, clock).spending, StateDB(old.state_db), clock)

    spent_before = rows(old.state_db, "SELECT round(sum(cost_usd), 6), count(*) FROM usage")[0]
    clock = FixedClock(shared.START)
    open_core(old, clock)  # the move: two accounts (1 and 99) asked on the 13th, and the public
    monkeypatch.setattr(cli, "_settings", lambda: old)
    monkeypatch.setattr(cli, "CLOCK", FixedClock("2026-11-14T03:35:00+00:00"))
    tidied = CliRunner().invoke(cli.app, ["tidy"])
    assert tidied.exit_code == 0 and "Forgot the signed-in account on 3 spending rows from before today." in tidied.output
    today = FixedClock("2026-11-14T09:00:00+00:00")
    with sqlite3.connect(old.state_db) as con:  # a call of today's, for account 1
        con.execute("INSERT INTO wts_spending (at, kind, model, visitor, account, input_tokens, output_tokens, "
                    "cost_usd) VALUES ('2026-11-14T08:59:58+00:00', 'answer', 'claude-sonnet-5-5', NULL, 1, 10, 2, "
                    "0.25)")
    before = report(today)
    lines = {line["email"]: line["cost_usd"] for line in before["by_user"]}
    total = rows(old.state_db, "SELECT sum(cost_usd) FROM wts_spending WHERE substr(at, 1, 7) = '2026-11'")[0][0]
    assert sum(lines.values()) == pytest.approx(total, abs=0.006)  # the lines add up to what was spent
    assert rows(old.state_db, "SELECT count(*) FROM wts_account_months") == [(2,)]

    undo(old.state_db)
    path = old.state_db
    # the month totals went with the spending rows: the script says so, and does it itself
    assert rows(path, "SELECT count(*) FROM wts_account_months") == [(0,)]
    assert rows(path, "SELECT count(*) FROM wts_visitor_days WHERE who != '*'") == [(0,)]
    # what comes back: a day gone by has no account, no visitor and its midnight; costs and days exact
    assert rows(path, "SELECT DISTINCT at FROM usage WHERE substr(at, 1, 10) = '2026-11-13'") == [
        ("2026-11-13T00:00:00+00:00",)]
    assert rows(path, "SELECT count(*) FROM usage WHERE substr(at, 1, 10) < '2026-11-14' AND "
                      "(user_id IS NOT NULL OR visitor IS NOT NULL)") == [(0,)]
    assert rows(path, "SELECT round(sum(cost_usd), 6), count(*) FROM usage WHERE at < '2026-11-14'")[0] == spent_before
    assert rows(path, "SELECT user_id, at, cost_usd FROM usage WHERE at >= '2026-11-14'") == [
        (1, "2026-11-14T08:59:58+00:00", 0.25)]  # today's row, whole

    # coming forward again: each line of the report once, and the lines add up to what was spent
    after = report(FixedClock("2026-11-14T10:00:00+00:00"))
    lines = {line["email"]: line["cost_usd"] for line in after["by_user"]}
    assert sum(lines.values()) == pytest.approx(total, abs=0.006)
    assert after["total_usd"] <= before["total_usd"] + 0.006


def test_going_back_that_way_is_no_longer_possible_once_the_old_tables_are_dropped(old):
    core = open_core(old, FixedClock("2027-02-01T03:35:00+00:00"))
    assert set(core.retire_old().dropped) == set(OLD_TABLES.tables)
    before = dump(old.state_db)
    for script in ("undo-wts-core.sql", "redo-wts-core.sql"):
        text = (DEPLOY / script).read_text()
        assert "VALID ONLY UNTIL 1 FEBRUARY 2027" in text
    # each script fails before it deletes anything, on a moved database whose old tables the nightly tidy has
    # dropped: the redo would otherwise empty the package's tables with nothing to bring their rows back from
    for script in ("undo-wts-core.sql", "redo-wts-core.sql"):
        with pytest.raises(sqlite3.OperationalError, match="no such table: usage"):
            run_sql(old.state_db, script)
        assert dump(old.state_db) == before, script  # nothing changed
        assert moved(old.state_db) and rows(old.state_db, "SELECT count(*) FROM wts_spending") == [(13,)], script
    assert OLD_TABLES.drop_on.isoformat() == "2027-02-01"
    open_core(old, FixedClock("2027-02-02T09:00:00+00:00"))  # and with no old tables there is nothing to look for


# the settings are checked before the database is moved, and the old code after the move is noticed
#
# The independent review's finding: the one-off import ran before the settings were checked. With a model in the
# server's own settings that had no prices, the site refused to start AFTER the import was made and marked; the
# owner, told that "the earlier version runs on the file as it is", went back; and when the new version was
# deployed again the import was skipped, so what the old code had written meanwhile was in no table the site
# reads: a saved briefing answering 404, its cost unseen by the caps.

NO_PRICES = '[answer]\nmodel = "claude-sonnet-6"\n'
PRICES = ('[usage.models."claude-sonnet-6"]\nprice_input_per_mtok = 2.0\nprice_output_per_mtok = 10.0\n'
          'price_cache_read_per_mtok = 0.2\n')


def with_local_settings(folder, text: str, monkeypatch):
    """The settings as the server would read them with ``text`` in its own settings.local.toml."""
    from hansard.config import load_settings

    (folder / "settings.local.toml").write_text(text)
    monkeypatch.setenv("HANSARD_DATA_DIR", str(folder))
    return load_settings(**{"embedding.backend": "hashing", "embedding.dim": 64})


def everything_in(folder) -> dict[str, bytes]:
    """The state database and everything beside it that is its own (its log, any copy), byte for byte."""
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir()) if p.is_file() and p.name.startswith("state.")}


def test_a_site_that_would_refuse_to_start_on_its_settings_has_not_touched_the_database(old, monkeypatch):
    from typer.testing import CliRunner
    from wts_core.settings import SettingsError

    from hansard import cli
    from hansard.api.app import create_app

    bad = with_local_settings(old.data_dir, NO_PRICES, monkeypatch)
    before = everything_in(old.data_dir)
    assert b"wts_" not in before["state.sqlite"]
    # the web app, the package's side of the database by itself, and every command that could be the first to
    # open it: each refuses on the settings, naming the model and both files, and the database is as it was
    with pytest.raises(SettingsError, match="claude-sonnet-6") as refused:
        create_app(bad)
    assert "settings.local.toml" in str(refused.value)
    with pytest.raises(SettingsError, match="claude-sonnet-6"):
        open_core(bad, FixedClock(shared.START))
    monkeypatch.setattr(cli, "_settings", lambda: bad)
    for command in (["tidy"], ["stats"], ["weekly-report"], ["statements", "list"], ["reports", "list"],
                    ["waiting"], ["hidden"], ["topics", "refresh"], ["topics", "list"], ["sync", "--no-fetch"]):
        result = CliRunner().invoke(cli.app, command)
        assert result.exit_code != 0 and isinstance(result.exception, SettingsError | SystemExit), command
    assert everything_in(old.data_dir) == before  # no copy, no new table, not a byte of the database changed
    # the look `update.sh` takes on the new image before it restarts the site: status 1, in words for the owner
    result = CliRunner().invoke(cli.app, ["check-settings"])
    assert result.exit_code == 1 and "The site would not start with these settings" in result.output
    assert "claude-sonnet-6" in result.output and everything_in(old.data_dir) == before

    # with the prices added the same look passes, still changing nothing; and then the site starts and moves
    good = with_local_settings(old.data_dir, NO_PRICES + PRICES, monkeypatch)
    before = everything_in(old.data_dir)
    monkeypatch.setattr(cli, "_settings", lambda: good)
    result = CliRunner().invoke(cli.app, ["check-settings"])
    assert result.exit_code == 0, result.output
    assert "The settings are complete" in result.output and "claude-sonnet-6 and claude-haiku-5-5" in result.output
    assert "Nothing was changed." in result.output and everything_in(old.data_dir) == before
    # and as `update.sh` runs it, a process of its own on the new image: the database is not opened for writing.
    # Not a byte of it changes, no log or copy appears beside it, and it is read even when it cannot be written
    old.state_db.chmod(0o400)
    try:
        done = subprocess.run([sys.executable, "-c", "from hansard.cli import app; app()", "check-settings"],
                              capture_output=True, text=True, env={**os.environ, "HANSARD_DATA_DIR": str(old.data_dir)})
    finally:
        old.state_db.chmod(0o600)
    assert done.returncode == 0 and "Nothing was changed." in done.stdout, done.stdout + done.stderr
    assert everything_in(old.data_dir) == before
    create_app(good, clock=FixedClock(shared.START))
    assert moved(old.state_db) and rows(old.state_db, "SELECT count(*) FROM wts_spending") == [(13,)]


def old_code_writes(path, when: str = "2026-11-13T15:00:00+00:00") -> None:
    """What the code from before the move writes when it answers a reader and takes a report: to the old tables."""
    con = sqlite3.connect(path)
    con.execute("INSERT INTO usage (user_id, visitor, at, kind, model, input_tokens, output_tokens, cost_usd) "
                "VALUES (NULL, 'v', ?, 'answer', 'claude-sonnet-5-5', 9000, 700, 9.99)", (when,))
    con.execute("INSERT INTO briefings (id, created_at, question, settings, events) VALUES "
                "('meanwhikea', ?, 'Asked on the old code after the move?', '{}', ?)",
                (when, json.dumps(shared.a_saved_briefing("Asked on the old code after the move?"))))
    con.execute("INSERT INTO reports (at, kind, message, status) VALUES (?, 'complaint', "
                "'A complaint sent meanwhile', 'open')", (when,))
    con.commit()
    con.close()


def test_the_old_code_having_written_since_the_move_is_noticed_and_the_move_made_again(old, monkeypatch):
    """The new code refuses to start on a database the old code has written to since the move, saying what each
    side holds and naming the mend; with the move made again from the old tables, nothing the old code took in is
    left out."""
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from hansard import cli
    from hansard.api.app import create_app
    from hansard.store.wts import WrittenSinceTheMove, written_since_the_move

    clock = FixedClock(shared.START)
    open_core(old, clock)
    assert written_since_the_move(old.state_db) is None
    old_code_writes(old.state_db)
    was = dump(old.state_db)
    with pytest.raises(WrittenSinceTheMove) as refused:
        open_core(old, FixedClock("2026-11-13T16:00:00+00:00"))
    said = str(refused.value)
    assert refused.value.old == {"usage": 1, "briefings": 1, "reports": 1} and refused.value.new == {}
    assert "moved on 2026-11-13 12:00 UTC (1 in usage, 1 in briefings, 1 in reports)" in said
    assert "the new tables have taken in nothing" in said
    assert "deploy/redo-wts-core.sql" in said and "deploy/undo-wts-core.sql" in said and "OPERATIONS.md" in said
    with pytest.raises(WrittenSinceTheMove):
        create_app(old, clock=FixedClock("2026-11-13T16:00:00+00:00"))
    monkeypatch.setattr(cli, "_settings", lambda: old)
    look = CliRunner().invoke(cli.app, ["check-settings"])
    assert look.exit_code == 1 and "deploy/redo-wts-core.sql" in look.output
    assert dump(old.state_db) == was  # refusing changed nothing

    run_sql(old.state_db, "redo-wts-core.sql")
    assert not moved(old.state_db) and rows(old.state_db, "SELECT count(*) FROM usage") == [(14,)]
    monkeypatch.setenv("HANSARD_ADMIN_TOKEN", shared.ADMIN_TOKEN)
    later = FixedClock("2026-11-13T16:00:00+00:00")
    client = TestClient(create_app(old, clock=later))
    assert moved(old.state_db) and written_since_the_move(old.state_db) is None
    # the briefing saved meanwhile is at its address, its cost counts towards the caps, and the report is listed
    shown = client.get("/api/briefings/meanwhikea")
    assert shown.status_code == 200 and "Asked on the old code after the move?" in shown.text
    admin = {"Authorization": f"Bearer {shared.ADMIN_TOKEN}"}
    assert client.get("/api/admin/usage", headers=admin).json()["today_usd"] == pytest.approx(0.0694 + 9.99)
    assert "A complaint sent meanwhile" in client.get("/api/admin/reports", headers=admin).text


def test_when_both_sides_have_written_the_owner_chooses_and_is_told_what_each_choice_discards(old):
    from wts_core.usage import BriefingArchive

    from hansard.api.briefings import new_briefing_id
    from hansard.store.wts import WrittenSinceTheMove

    clock = FixedClock(shared.START)
    core = open_core(old, clock)
    clock.advance(hours=1)  # the new code answers a reader; then the old code is started without the undo
    kept = BriefingArchive(core, new_id=new_briefing_id, clock=clock).save(
        "Asked on the new code?", {}, shared.a_saved_briefing("Asked on the new code?"))
    old_code_writes(old.state_db, "2026-11-13T15:00:00+00:00")
    with pytest.raises(WrittenSinceTheMove) as refused:
        open_core(old, FixedClock("2026-11-13T16:00:00+00:00"))
    assert refused.value.new == {"wts_briefings": 1} and "1 in wts_briefings" in str(refused.value)
    for script in ("undo-wts-core.sql", "redo-wts-core.sql"):
        assert "WHAT THIS DISCARDS" in (DEPLOY / script).read_text()
    # keeping the new code's: the undo carries its rows to the old tables and discards the old code's since
    undo(old.state_db)
    back = open_core(old, FixedClock("2026-11-13T16:00:00+00:00"))
    archive = BriefingArchive(back, new_id=new_briefing_id)
    assert archive.get(kept.id) is not None and archive.get("meanwhikea") is None


def test_the_nightly_keeping_of_the_old_tables_never_looks_like_the_old_code_at_work(old, monkeypatch):
    """The tidy changes old rows every night (addresses blanked, codes forgotten, notes deleted) and adds none: the
    site starts every morning after it, and after a statement lapses, a report is closed and a briefing withdrawn
    on the new code."""
    from typer.testing import CliRunner
    from wts_core.usage import BriefingArchive

    from hansard import cli
    from hansard.api.briefings import new_briefing_id
    from hansard.store.wts import written_since_the_move

    open_core(old, FixedClock(shared.START))
    monkeypatch.setattr(cli, "_settings", lambda: old)
    for day in range(14, 20):
        clock = FixedClock(f"2026-11-{day}T03:35:00+00:00")
        monkeypatch.setattr(cli, "CLOCK", clock)
        desk = replies_for(old, clock)
        if day == 15:
            desk.close(2, outcome="resolved", note="Put right.", public_note="A date was corrected.")
            desk.decline(2, "Not from the member.")
            BriefingArchive(open_core(old, clock), new_id=new_briefing_id, clock=clock).withdraw(
                shared.BRIEFING_1, "It misread a speech.")
        assert CliRunner().invoke(cli.app, ["tidy"]).exit_code == 0
        assert written_since_the_move(old.state_db) is None
        open_core(old, FixedClock(f"2026-11-{day}T07:00:00+00:00"))
    assert rows(old.state_db, "SELECT count(*) FROM statements WHERE email IS NULL")[0][0] >= 3


def test_the_two_scripts_refuse_a_database_that_has_not_been_moved_and_a_second_run(old):
    """`undo-wts-core.sql` writes the old tables afresh from the package's. Run on a database that was never
    moved, or run twice, it would write them from empty tables: it refuses instead, and changes nothing."""
    before = dump(old.state_db)
    for script in ("undo-wts-core.sql", "redo-wts-core.sql"):
        with pytest.raises(sqlite3.OperationalError, match="no such table: wts_schema"):
            run_sql(old.state_db, script)  # the new code has never opened it
    assert dump(old.state_db) == before
    open_core(old, FixedClock(shared.START))
    undo(old.state_db)  # the move is undone, and forgotten
    undone = dump(old.state_db)
    assert rows(old.state_db, "SELECT count(*) FROM usage") == [(13,)]
    for script in ("undo-wts-core.sql", "redo-wts-core.sql"):
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            run_sql(old.state_db, script)  # a second run: there is no move to undo
    assert dump(old.state_db) == undone and rows(old.state_db, "SELECT count(*) FROM usage") == [(13,)]


def test_a_row_the_old_code_added_is_decided_by_the_shared_packages_guard_alone_and_one_it_changed_by_the_sites(
        old):
    """From wts-core 0.3.0 the package itself refuses a database whose old tables hold a row written after the
    import ran (``Import(written_at=…)``; its CHANGELOG, "state.StateError, and Import(written_at=…)"), with the
    exact line its UPGRADING gives for this site. That guard alone decides a row added: the site's look asks it,
    over a scratch copy, so that it can be asked read-only before the old site is stopped. The site keeps of its
    own what the package does not do: a row the old code changed (a report closed), and words that count both
    sides and name both mends. On the site's golden old database, each kind of row the old code adds (a spending
    row, a saved briefing, a report, a note of a failure, a cached answer, a statement) refuses the start, by both
    ways in, however its time is written; nothing at or before the second of the move does."""
    from wts_core.state import StateDB as CoreDB
    from wts_core.state import StateError

    from hansard.config import SITE
    from hansard.store.wts import IMPORTS, WrittenSinceTheMove, migrations, written_since_the_move

    def package_opens() -> None:
        CoreDB(old.state_db, migrations=migrations(), imports=IMPORTS, clock=FixedClock("2026-11-13T16:00:00+00:00"),
               site=SITE.package, old_tables=OLD_TABLES)

    def write(sql: str, *args) -> None:
        con = sqlite3.connect(old.state_db)
        con.execute(sql, args)
        con.commit()
        con.close()

    open_core(old, FixedClock(shared.START))  # the move, at 12:00:00 UTC
    package_opens()
    assert written_since_the_move(old.state_db) is None and issubclass(WrittenSinceTheMove, StateError)
    assert IMPORTS[0].written_at == {"usage": "at", "answer_cache": "created_at", "briefings": "created_at",
                                     "failures": "at", "reports": "at", "statements": "at"}
    kinds = {  # what the old code writes, table by table, with the time it is given
        "usage": "INSERT INTO usage (at, kind, cost_usd) VALUES (?, 'answer', 0.01)",
        "briefings": "INSERT INTO briefings (id, created_at, question, settings, events) VALUES ('meanwhilex', ?, "
                     "'Asked meanwhile?', '{}', '[]')",
        "reports": "INSERT INTO reports (at, kind, message, status) VALUES (?, 'complaint', 'Sent meanwhile', 'open')",
        "failures": "INSERT INTO failures (at, kind, question, error) VALUES (?, 'answer', 'q', 'e')",
        "answer_cache": "INSERT INTO answer_cache (key, created_at, events) VALUES ('meanwhile', ?, '[]')",
        "statements": "INSERT INTO statements (at, name, role, item_path, text, status) VALUES (?, 'A', 'B', '/b/x', "
                      "'Sent meanwhile', 'waiting')",
    }
    undone = {"usage": "DELETE FROM usage WHERE cost_usd = 0.01", "briefings": "DELETE FROM briefings WHERE id = 'meanwhilex'",
              "reports": "DELETE FROM reports WHERE message = 'Sent meanwhile'",
              "failures": "DELETE FROM failures WHERE question = 'q'",
              "answer_cache": "DELETE FROM answer_cache WHERE key = 'meanwhile'",
              "statements": "DELETE FROM statements WHERE text = 'Sent meanwhile'"}
    afters = ("2026-11-13T15:00:00+00:00", "2026-11-13T15:00:00Z", "2026-11-13T15:00:00.123456+00:00",
              "2026-11-13 15:00:00", "2026-11-13T16:00:00+01:00", "2026-11-13T12:00:01+00:00")
    for table, sql in kinds.items():
        for after in afters:
            write(sql, after)
            found = written_since_the_move(old.state_db)
            assert found is not None and found.old == {table: 1}, (table, after)
            with pytest.raises(StateError, match=f"{table} has 1 row written after the import") as refused:
                package_opens()
            assert "script for undoing the move" in str(refused.value), (table, after)
            # the site's words are the ones the owner meets: both sides counted, both mends named
            with pytest.raises(WrittenSinceTheMove, match="deploy/redo-wts-core.sql") as said:
                open_core(old, FixedClock("2026-11-13T16:00:00+00:00"))
            assert f"(1 in {table})" in str(said.value) and "deploy/undo-wts-core.sql" in str(said.value)
            write(undone[table])
    # at or before the second of the move, or a value that is no time at all: neither stops the site
    for not_after in ("2026-11-13T12:00:00+00:00", "2026-11-13T13:30:00+02:00", "2026-11-13 11:59:59", "x", ""):
        write(kinds["usage"], not_after)
        assert written_since_the_move(old.state_db) is None, not_after
        package_opens()
        write(undone["usage"])
    # what the package's guard cannot see, and the site's look does: a row the old code changed (a report closed)
    write("UPDATE reports SET status = 'resolved', closed_at = '2026-11-13T15:00:00+00:00' WHERE id = 2")
    package_opens()
    found = written_since_the_move(old.state_db)
    assert found is not None and found.old == {"reports": 1}
    with pytest.raises(WrittenSinceTheMove, match=r"\(1 in reports\)"):
        open_core(old, FixedClock("2026-11-13T16:00:00+00:00"))
