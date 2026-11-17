"""The server's watchdog (deploy/watchdog.sh), run against stand-ins for docker, logger, journalctl and the mail
script: which sites it restarts, when it waits, and what it emails."""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"

# docker: "compose ps -q app" prints the ids in $STUB/ps-<site>; "inspect" prints $STUB/inspect-<id> ("<one-off
# label> <health>"); "compose restart app" notes the site in $STUB/restarts, and fails if $STUB/fail-<site> exists.
# The site is the name of the folder holding the deploy folder it is run from.
DOCKER = r"""#!/usr/bin/env bash
site="$(basename "$(dirname "$PWD")")"
case "$1 $2" in
  "compose ps") cat "$STUB/ps-$site" 2>/dev/null; exit 0 ;;
  "compose restart") echo "$site" >> "$STUB/restarts"; [[ ! -e "$STUB/fail-$site" ]] ;;
  inspect*) cat "$STUB/inspect-${@: -1}" ;;
  *) exit 0 ;;
esac
"""
LOGGER = '#!/usr/bin/env bash\nshift 2; echo "$*" >> "$STUB/log"\n'
QUIET = "#!/usr/bin/env bash\nexit 0\n"
# mail.sh: the subject and the message, as alert.sh hands them over
MAIL = '#!/usr/bin/env bash\n{ echo "SUBJECT: $2"; cat; echo "----"; } >> "$STUB/mail"\n'


@pytest.fixture
def server(tmp_path):
    stub, bin_, opt, state = (tmp_path / n for n in ("stub", "bin", "opt", "state"))
    for d in (stub, bin_, state):
        d.mkdir()
    for name, body in (("docker", DOCKER), ("logger", LOGGER), ("journalctl", QUIET), ("systemctl", QUIET)):
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    mps = opt / "hansardgpt" / "deploy"
    mps.mkdir(parents=True)
    shutil.copy(DEPLOY / "watchdog.sh", mps)
    shutil.copy(DEPLOY / "alert.sh", mps)
    (mps / "mail.sh").write_text(MAIL)
    (mps / ".env").write_text('DOMAIN=wherempsstand.com\nSITES="wherecountriesstand.com=wherecountriesstand '
                              'wherecouncilsstand.com=wherecouncilsstand wherelandlordsstand.com=wherelandlordsstand"\n')
    for site in ("wherecountriesstand", "wherecouncilsstand"):  # Landlords is listed but not installed here
        (opt / site / "deploy").mkdir(parents=True)
        (opt / site / "deploy" / "docker-compose.yml").write_text("name: x\n")
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "STUB": str(stub), "DIR": str(opt / "hansardgpt"),
           "WATCHDOG_OPT": str(opt), "WATCHDOG_STATE": str(state / "watchdog"), "MAIL_STATE": str(state),
           "UPDATE_LOCK": str(tmp_path / "update.lock")}
    return Server(stub, mps, env, tmp_path / "update.lock", state / "watchdog")


class Server:
    def __init__(self, stub: Path, mps: Path, env: dict, lock: Path, history: Path):
        self.stub, self.mps, self.env, self.lock, self.history = stub, mps, env, lock, history

    def app(self, site: str, health: str, one_off: str | None = None) -> None:
        """The site's app, with its health ("healthy", "unhealthy", "starting" or "" for none)."""
        ids = [f"{site}-run"] if one_off else []
        ids.append(f"{site}-app")
        (self.stub / f"ps-{site}").write_text("\n".join(ids) + "\n")
        (self.stub / f"inspect-{site}-app").write_text(f"False {health}\n")
        if one_off:
            (self.stub / f"inspect-{site}-run").write_text(f"True {one_off}\n")

    def run(self) -> subprocess.CompletedProcess:
        out = subprocess.run(["bash", str(self.mps / "watchdog.sh")], env=self.env, capture_output=True, text=True,
                             timeout=60)
        assert out.returncode == 0, out.stderr
        return out

    def read(self, name: str) -> str:
        p = self.stub / name
        return p.read_text() if p.exists() else ""

    def restarts(self) -> list[str]:
        return self.read("restarts").split()


def test_nothing_happens_while_every_site_answers(server):
    for site in ("hansardgpt", "wherecountriesstand", "wherecouncilsstand"):
        server.app(site, "healthy")
    server.run()
    assert server.restarts() == [] and server.read("mail") == "" and server.read("log") == ""


def test_where_mps_stand_is_restarted_and_the_alert_emailed_as_before(server):
    server.app("hansardgpt", "unhealthy")
    server.app("wherecountriesstand", "healthy")
    server.run()
    assert server.restarts() == ["hansardgpt"]
    mail = server.read("mail")
    assert "SUBJECT: Server alert: wheretheystand-watchdog" in mail
    assert "Where MPs Stand stopped answering its health check, so the watchdog restarted it." in mail
    assert "restarting it" in server.read("log")


def test_every_site_in_sites_is_watched(server):
    server.app("hansardgpt", "healthy")
    server.app("wherecountriesstand", "unhealthy")
    server.app("wherecouncilsstand", "unhealthy")
    server.run()
    assert server.restarts() == ["wherecountriesstand", "wherecouncilsstand"]
    mail = server.read("mail")
    # each site's alert is its own, so one site's does not hold back another's for six hours
    assert "wherecountriesstand.com stopped answering" in mail and "wherecouncilsstand.com stopped answering" in mail


def test_apps_not_running_starting_or_without_a_health_check_are_left_alone(server):
    server.app("wherecountriesstand", "starting")
    server.app("wherecouncilsstand", "")
    server.run()  # Where MPs Stand has no container at all here (being rebuilt, say)
    assert server.restarts() == [] and server.read("mail") == ""


def test_an_update_container_is_not_mistaken_for_the_site(server):
    # an update's one-off container has the same health check, and nothing answers it there
    server.app("hansardgpt", "healthy", one_off="unhealthy")
    server.run()
    assert server.restarts() == []


def test_nothing_is_restarted_while_an_update_holds_the_lock(server):
    server.app("hansardgpt", "unhealthy")
    server.app("wherecountriesstand", "unhealthy")
    with open(server.lock, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        server.run()
        server.run()
    assert server.restarts() == []
    mail = server.read("mail")
    assert mail.count("while an update is running") == 2  # one email per site, not one per run
    assert "fuser -v" in mail
    server.run()  # the update has finished
    assert server.restarts() == ["hansardgpt", "wherecountriesstand"]


def test_the_lock_is_held_while_a_site_restarts(server):
    server.app("hansardgpt", "unhealthy")
    probe = server.stub.parent / "bin" / "docker"
    # the restart itself tries the lock, as an update starting at that moment would
    probe.write_text(DOCKER.replace('"compose restart")', '"compose restart") flock -n "$UPDATE_LOCK" true && '
                                    'echo free >> "$STUB/lock" || echo held >> "$STUB/lock";'))
    server.run()
    assert server.read("lock").split() == ["held"] and server.restarts() == ["hansardgpt"]


def test_restarts_back_off_and_then_stop(server):
    server.app("wherecouncilsstand", "unhealthy")
    server.run()
    server.run()  # two minutes later, still unhealthy: too soon to restart again
    assert server.restarts() == ["wherecouncilsstand"]
    assert "waiting until 10 minutes have passed" in server.read("log")

    history = server.history / "wherecouncilsstand"
    now = int(time.time())
    history.write_text(f"{now - 11 * 60}\n")  # restarted 11 minutes ago
    server.run()
    assert server.restarts() == ["wherecouncilsstand"] * 2

    history.write_text(f"{now - 30 * 60}\n{now - 15 * 60}\n")  # the second wait is 20 minutes
    server.run()
    assert server.restarts() == ["wherecouncilsstand"] * 2
    history.write_text(f"{now - 50 * 60}\n{now - 25 * 60}\n")
    server.run()
    assert server.restarts() == ["wherecouncilsstand"] * 3

    history.write_text(f"{now - 3 * 3600}\n{now - 2 * 3600}\n{now - 3600}\n")  # three in six hours: no more
    server.run()
    assert server.restarts() == ["wherecouncilsstand"] * 3
    assert "a person needs to look" in server.read("mail")

    history.write_text(f"{now - 7 * 3600}\n{now - 2 * 3600}\n{now - 3600}\n")  # the oldest is past six hours
    server.run()
    assert server.restarts() == ["wherecouncilsstand"] * 4
    assert str(now - 7 * 3600) not in history.read_text()  # restarts older than six hours are forgotten


def test_a_failed_restart_is_reported(server):
    server.app("wherecountriesstand", "unhealthy")
    (server.stub / "fail-wherecountriesstand").touch()
    server.run()
    assert "the watchdog could not restart it" in server.read("mail")


def test_without_sites_only_where_mps_stand_is_watched(server):
    (server.mps / ".env").write_text("DOMAIN=wherempsstand.com\n")
    server.app("hansardgpt", "unhealthy")
    server.app("wherecountriesstand", "unhealthy")
    server.run()
    assert server.restarts() == ["hansardgpt"]


@pytest.mark.parametrize("script", sorted(p.name for p in DEPLOY.glob("*.sh")))
def test_deploy_scripts_parse(script):
    subprocess.run(["bash", "-n", str(DEPLOY / script)], check=True)
