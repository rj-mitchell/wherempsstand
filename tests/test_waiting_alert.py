"""The alert for new reports and statements (deploy/waiting-alert.sh), run against stand-ins for curl, logger and the
mail script: which sites it asks, when it emails, and that it emails counts only."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[1] / "deploy"

# curl: prints $STUB/check-<domain> (the site's /api/check answer) when the token presented is $STUB/token-<domain>,
# and notes every address asked in $STUB/asked
CURL = r"""#!/usr/bin/env bash
url="${@: -1}"; domain="${url#https://}"; domain="${domain%%/*}"
for a in "$@"; do [[ "$a" == "Authorization: Bearer "* ]] && token="${a#Authorization: Bearer }"; done
echo "$url" >> "$STUB/asked"
[[ -f "$STUB/token-$domain" && "$(cat "$STUB/token-$domain")" == "${token:-}" ]] || exit 0
cat "$STUB/check-$domain" 2>/dev/null
"""
LOGGER = '#!/usr/bin/env bash\nshift 2; echo "$*" >> "$STUB/log"\n'
MAIL = '#!/usr/bin/env bash\n{ echo "SUBJECT: $2"; cat; echo "----"; } >> "$STUB/mail"\n'
SITES = {"wherempsstand.com": ("hansardgpt", "HANSARD"), "wherecouncilsstand.com": ("wherecouncilsstand", "COUNCILS"),
         "wherelandlordsstand.com": ("wherelandlordsstand", "LANDLORDS")}


@pytest.fixture
def server(tmp_path):
    stub, bin_, opt, state = (tmp_path / n for n in ("stub", "bin", "opt", "state"))
    for d in (stub, bin_, state):
        d.mkdir()
    for name, body in (("curl", CURL), ("logger", LOGGER)):
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    for domain, (folder, prefix) in SITES.items():
        deploy = opt / folder / "deploy"
        deploy.mkdir(parents=True)
        env = f"{prefix}_CHECK_TOKEN=token-for-{folder}\n"
        if folder == "hansardgpt":
            env += ('DOMAIN=wherempsstand.com\nSITES="wherecountriesstand.com=wherecountriesstand '
                    'wherecouncilsstand.com=wherecouncilsstand wherelandlordsstand.com=wherelandlordsstand"\n')
            shutil.copy(DEPLOY / "waiting-alert.sh", deploy)
            (deploy / "mail.sh").write_text(MAIL)
        (deploy / ".env").write_text(env)
        (stub / f"token-{domain}").write_text(f"token-for-{folder}")
    # Where Countries Stand is listed in SITES but not installed here: it is skipped
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "STUB": str(stub), "DIR": str(opt / "hansardgpt"),
           "WAITING_OPT": str(opt), "WAITING_STATE": str(state / "waiting")}
    return Server(stub, opt / "hansardgpt" / "deploy", env)


class Server:
    def __init__(self, stub: Path, mps: Path, env: dict):
        self.stub, self.mps, self.env = stub, mps, env

    def waiting(self, domain: str, reports: int, statements: int, oldest: int = 0) -> None:
        body = {"site": "x", "waiting": {"reports": reports, "statements": statements, "hidden": 0,
                                         "oldest_working_days": oldest}}
        (self.stub / f"check-{domain}").write_text(json.dumps(body))

    def run(self) -> None:
        out = subprocess.run(["bash", str(self.mps / "waiting-alert.sh")], env=self.env, capture_output=True,
                             text=True, timeout=60)
        assert out.returncode == 0, out.stderr

    def mail(self) -> str:
        p = self.stub / "mail"
        text = p.read_text() if p.exists() else ""
        p.unlink(missing_ok=True)
        return text


def test_a_new_report_or_statement_is_emailed_once_with_counts_and_the_command(server):
    for domain in SITES:
        server.waiting(domain, 0, 0)
    server.run()
    assert server.mail() == ""                                      # nothing waiting, nothing sent
    server.waiting("wherecouncilsstand.com", 1, 0, oldest=0)
    server.run()
    mail = server.mail()
    assert "SUBJECT: Where Councils Stand: 1 new report" in mail
    assert "docker compose run --rm -T app councils waiting" in mail and "wherecouncilsstand/deploy" in mail
    server.run()
    assert server.mail() == ""                                      # the same report isn't emailed again
    server.waiting("wherempsstand.com", 2, 1, oldest=3)
    server.run()
    mail = server.mail()
    assert "SUBJECT: Where MPs Stand: 2 new reports and 1 new statement" in mail
    assert "The oldest has waited 3 working days" in mail and "app hansard waiting" in mail


def test_a_report_closed_then_a_new_one_is_still_news(server):
    server.waiting("wherelandlordsstand.com", 1, 0)
    server.run()
    assert "Where Landlords Stand: 1 new report" in server.mail()  # waiting at the first look: emailed once
    server.waiting("wherelandlordsstand.com", 0, 0)
    server.run()
    assert server.mail() == ""
    server.waiting("wherelandlordsstand.com", 1, 0)
    server.run()
    assert "Where Landlords Stand: 1 new report" in server.mail()


def test_sites_without_a_token_or_an_answer_are_skipped_and_nothing_fails(server):
    (server.stub / "token-wherecouncilsstand.com").write_text("another token")  # its token is refused: no answer
    server.waiting("wherecouncilsstand.com", 5, 5)
    (server.mps / ".env").write_text((server.mps / ".env").read_text().replace("HANSARD_CHECK_TOKEN=token-for-hansardgpt\n", ""))
    server.waiting("wherempsstand.com", 5, 5)
    server.run()
    assert server.mail() == ""
    log = (server.stub / "log").read_text()
    assert "Where MPs Stand: no check token" in log and "Where Councils Stand: no check answer" in log
    asked = (server.stub / "asked").read_text()
    assert "wherecountriesstand.com" not in asked                   # listed, but not installed on this server


def test_the_email_carries_counts_only(server):
    server.waiting("wherelandlordsstand.com", 1, 0)
    check = json.loads((server.stub / "check-wherelandlordsstand.com").read_text())
    check["report_text"] = "Jane Example says the figure is wrong"   # anything else in the answer is never sent
    (server.stub / "check-wherelandlordsstand.com").write_text(json.dumps(check))
    server.run()
    mail = server.mail()
    assert "1 new report" in mail and "Jane Example" not in mail


def test_the_timer_is_installed_by_update_sh():
    update = (DEPLOY / "update.sh").read_text()
    assert "wheretheystand-waiting.service wheretheystand-waiting.timer" in update
    assert "wheretheystand-waiting.timer >/dev/null" in update
    assert "waiting-alert.sh" in (DEPLOY / "wheretheystand-waiting.service").read_text()
