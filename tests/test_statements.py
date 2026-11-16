"""Statements beside items (the right of reply): what is accepted, the owner's commands, what is shown, how long the
email address is kept, and that a statement never reaches the model, the record or the search."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from wts_core.replies import Refused, months_before
from wts_core.testing import FixedClock

from hansard.owner import replies_for
from hansard.replies import MAX_WORDS, as_listed, item_path, published

from .test_api import events, make_app

ADMIN = {"Authorization": "Bearer s3cret-token"}
MARKER = "Zebrafinch"  # a word that appears nowhere in the record
BENN = 10669


def statement(**over):
    body = {"name": "Hilary Benn", "role": "MP for Leeds South", "item": "https://wherempsstand.com/people/10669/hilary-benn",
            "text": "I have always supported the right to trial by jury, as my speech of 2008 makes clear.",
            "link": "https://hansard.parliament.uk/commons/2008-06-11", "email": "hilary.benn.mp@parliament.uk"}
    return {**body, **over}


@pytest.fixture()
def site(indexed_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("HANSARD_ADMIN_TOKEN", "s3cret-token")
    app, state, client = make_app(indexed_settings, tmp_path, auth=False, requests_per_minute=100)
    return TestClient(app), state, client


def list_statements(client):
    """Every statement as the owner sees it, under the names the admin page reads."""
    replies = client.app.state.replies
    return [as_listed(replies, row) for row in replies.statements()]


# ---- what is accepted ----------------------------------------------------------------------------------------------

def test_item_addresses_are_paths_on_this_site():
    assert item_path("https://wherempsstand.com/people/10669/hilary-benn") == "/people/10669"
    assert item_path("/people/10669") == "/people/10669"
    assert item_path("https://www.wherempsstand.com/b/abcdefghjk?x=1#s") == "/b/abcdefghjk"
    assert item_path("wherempsstand.com/groups/commons/socialist-campaign-group/") == "/groups/commons/socialist-campaign-group"
    assert item_path("/debates/2008-06-11/commons/counter-terrorism-bill") == "/debates/2008-06-11/commons/counter-terrorism-bill"
    for bad in ("https://example.org/people/10669", "/about", "/", "javascript:alert(1)", "/people/abc", ""):
        with pytest.raises(Refused):
            item_path(bad)


def test_a_statement_is_stored_as_pending_and_not_shown(site):
    client, state, _ = site
    assert client.post("/api/statements", json=statement()).json() == {"ok": True}
    [s] = list_statements(client)
    assert s["status"] == "pending" and s["item_path"] == "/people/10669" and s["person"] == BENN
    assert s["email"] == "hilary.benn.mp@parliament.uk"
    assert client.get(f"/api/statements?person={BENN}").json() == {"statements": []}  # not until published


@pytest.mark.parametrize("over, said", [
    ({"text": " ".join(["word"] * (MAX_WORDS + 1))}, "up to 150 words"),
    ({"text": "Too short."}, "a sentence or more"),
    ({"item": "https://example.org/b/abcdefghjk"}, "address of the page"),
    ({"item": "/about"}, "isn't a page a statement can sit beside"),
    ({"link": "javascript:alert(1)"}, "full web address"),
    ({"email": "not an email"}, "email address"),
    ({"role": " x "}, "their role"),
])
def test_statements_are_checked(site, over, said):
    client, state, _ = site
    r = client.post("/api/statements", json=statement(**over))
    assert r.status_code == 400 and said in r.json()["detail"]
    assert list_statements(client) == []


def test_a_statement_is_only_accepted_beside_an_item_that_exists(site):
    client, state, _ = site
    bid = events(client.post("/api/ask", json={"question": "What has been said about trial by jury?"}))[-1]["briefing_id"]
    gone = client.post("/api/statements", json=statement(item="/b/abcdefghjk"))  # a well-formed address, no briefing
    assert gone.status_code == 400 and "There is no briefing at that address" in gone.json()["detail"]
    nobody = client.post("/api/statements", json=statement(item="https://wherempsstand.com/people/99999999/no-one"))
    assert nobody.status_code == 400 and "There is no member's page at that address" in nobody.json()["detail"]
    assert list_statements(client) == []
    assert client.post("/api/statements", json=statement(item=f"/b/{bid}")).status_code == 200
    assert client.post("/api/statements", json=statement(item=f"/people/{BENN}")).status_code == 200
    # the other kinds of page are not looked up: the owner sees the address before publishing
    assert client.post("/api/statements", json=statement(item="/groups/commons/no-such-group")).status_code == 200
    assert {s["item_path"] for s in list_statements(client)} == {f"/b/{bid}", f"/people/{BENN}",
                                                                "/groups/commons/no-such-group"}
    # a withdrawn briefing takes no new statements
    assert client.post(f"/api/admin/briefings/{bid}/withdraw", headers=ADMIN,
                       json={"reason": "It misattributed a quotation."}).status_code == 200
    withdrawn = client.post("/api/statements", json=statement(item=f"/b/{bid}"))
    assert withdrawn.status_code == 400 and "has been withdrawn" in withdrawn.json()["detail"]
    assert len(list_statements(client)) == 3
    # the other checks still come first
    first = client.post("/api/statements", json=statement(item="/b/abcdefghjk", text="Too short."))
    assert "a sentence or more" in first.json()["detail"]


def test_exactly_150_words_is_accepted_and_required_fields_are_required(site):
    client, state, _ = site
    assert client.post("/api/statements", json=statement(text=" ".join(["word"] * MAX_WORDS))).status_code == 200
    for field in ("name", "role", "item", "text", "email"):
        body = statement()
        del body[field]
        assert client.post("/api/statements", json=body).status_code == 422, field
    assert client.post("/api/statements", json=statement(link=None)).status_code == 200  # the link is optional
    assert len(list_statements(client)) == 2


def test_the_trap_field_keeps_nothing_and_there_is_a_daily_limit(site):
    client, state, _ = site
    assert client.post("/api/statements", json=statement(website="http://spam.example")).json() == {"ok": True}
    assert list_statements(client) == []
    codes = [client.post("/api/statements", json=statement()).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]


# ---- the owner's commands ------------------------------------------------------------------------------------------

def test_owner_publishes_declines_updates_and_withdraws(site):
    client, state, _ = site
    for item in ("/people/10669", "/groups/commons/socialist-campaign-group", "/people/10777"):
        client.post("/api/statements", json=statement(item=item))
    assert client.get("/api/admin/statements").status_code == 403
    listed = client.get("/api/admin/statements?status=pending", headers=ADMIN).json()["statements"]
    assert len(listed) == 3 and all(s["email"] for s in listed)
    by_item = {s["item_path"]: s["id"] for s in listed}
    benn, group, cameron = by_item["/people/10669"], by_item["/groups/commons/socialist-campaign-group"], \
        by_item["/people/10777"]

    ok = client.post(f"/api/admin/statements/{benn}/publish", headers=ADMIN,
                     json={"checked": "Replied from his parliament.uk address"})
    assert ok.status_code == 200
    # a statement about a page that isn't the member's own is given the member when published
    # how the sender was confirmed is required, as on the command line: without it nothing is published
    for body in ({"person": BENN}, {"person": BENN, "checked": ""}):
        assert client.post(f"/api/admin/statements/{group}/publish", headers=ADMIN, json=body).status_code == 422
    spaces = client.post(f"/api/admin/statements/{group}/publish", headers=ADMIN, json={"person": BENN, "checked": "  "})
    assert spaces.status_code == 400 and "how you confirmed who sent it" in spaces.json()["detail"]
    assert [s["item"] for s in client.get(f"/api/statements?person={BENN}").json()["statements"]] == ["/people/10669"]
    client.post(f"/api/admin/statements/{group}/publish", headers=ADMIN,
                json={"person": BENN, "checked": "Phoned the group's office"})
    shown = client.get(f"/api/statements?person={BENN}").json()["statements"]
    assert [s["item"] for s in shown] == ["/people/10669", "/groups/commons/socialist-campaign-group"]
    assert set(shown[0]) == {"id", "name", "role", "item", "person", "text", "link", "date"}  # never the email
    assert "parliament.uk" not in json.dumps(shown).replace("hansard.parliament.uk", "")
    beside = client.get("/api/statements", params={"path": "https://wherempsstand.com/groups/commons/socialist-campaign-group"})
    assert [s["id"] for s in beside.json()["statements"]] == [group]
    assert client.get("/api/statements").json() == {"statements": []}

    # published twice: refused
    assert client.post(f"/api/admin/statements/{benn}/publish", headers=ADMIN,
                       json={"checked": "again"}).status_code == 404

    # the author's new words: shown as written, with the new date
    new = "Having read the debate again, I stand by every word of what I said about juries then."
    assert client.post(f"/api/admin/statements/{benn}/update", headers=ADMIN, json={"text": new}).status_code == 200
    assert client.post(f"/api/admin/statements/{benn}/update", headers=ADMIN,
                       json={"text": " ".join(["w"] * 151)}).status_code == 400
    assert client.post(f"/api/admin/statements/{benn}/update", headers=ADMIN, json={"link": ""}).status_code == 200
    s = client.get(f"/api/statements?path=/people/{BENN}").json()["statements"][0]
    assert s["text"] == new and s["link"] is None

    # declined with a reason, kept private; withdrawn at the author's request
    assert client.post(f"/api/admin/statements/{cameron}/decline", headers=ADMIN, json={"reason": "x"}).status_code == 422
    blank = client.post(f"/api/admin/statements/{cameron}/decline", headers=ADMIN, json={"reason": "     "})
    assert blank.status_code == 400 and "Please give the reason" in blank.json()["detail"]  # a message, not a 500
    assert client.post(f"/api/admin/statements/{cameron}/decline", headers=ADMIN,
                       json={"reason": "It is about another member's conduct."}).status_code == 200
    assert client.post(f"/api/admin/statements/{group}/withdraw", headers=ADMIN, json={}).status_code == 200
    assert [s["id"] for s in client.get(f"/api/statements?person={BENN}").json()["statements"]] == [benn]
    everything = {s["id"]: s for s in client.get("/api/admin/statements", headers=ADMIN).json()["statements"]}
    assert everything[cameron]["status"] == "declined" and "conduct" in everything[cameron]["reason"]
    assert everything[group]["status"] == "withdrawn"
    # a declined or withdrawn statement can't be published or changed again
    assert client.post(f"/api/admin/statements/{cameron}/publish", headers=ADMIN,
                       json={"checked": "again"}).status_code == 404
    assert client.post(f"/api/admin/statements/{group}/update", headers=ADMIN, json={"text": new}).status_code == 404
    assert client.post("/api/admin/statements/999/withdraw", headers=ADMIN, json={}).status_code == 404


def test_the_statements_commands(settings, monkeypatch, tmp_path):
    from typer.testing import CliRunner

    from hansard import cli

    monkeypatch.setattr(cli, "_settings", lambda: settings)
    replies = replies_for(settings)
    sid = replies.submit(**statement())
    run = CliRunner().invoke
    listed = run(cli.app, ["statements", "list"])
    assert listed.exit_code == 0 and f"#{sid}  pending" in listed.output and "hilary.benn.mp@parliament.uk" in listed.output
    assert run(cli.app, ["statements", "publish", str(sid)]).exit_code != 0  # --checked is required
    unsaid = run(cli.app, ["statements", "publish", str(sid), "--checked", " "])  # and must say something
    assert unsaid.exit_code == 1 and "how you confirmed who sent it" in unsaid.output
    assert published(replies, person=BENN) == []
    assert run(cli.app, ["statements", "publish", str(sid), "--checked", "Replied from parliament.uk"]).exit_code == 0
    assert published(replies, person=BENN)[0]["id"] == sid
    words = tmp_path / "new.txt"
    words.write_text("These are the author's new words, sent by email, exactly as written.\n", encoding="utf-8")
    assert run(cli.app, ["statements", "update", str(sid), "--text-file", str(words)]).exit_code == 0
    assert published(replies, person=BENN)[0]["text"] == "These are the author's new words, sent by email, exactly as written."
    # a published statement is not declined: one that breaks the rules is removed, and `decline` says so
    declined = run(cli.app, ["statements", "decline", str(sid), "--reason", "It breaks the rules."])
    assert declined.exit_code == 1 and "is published, not pending" in declined.output
    no_reason = run(cli.app, ["statements", "remove", str(sid), "--reason", " "])
    assert no_reason.exit_code == 1 and "give the reason" in no_reason.output and "Traceback" not in no_reason.output
    assert no_reason.exception is None or isinstance(no_reason.exception, SystemExit)
    assert published(replies, person=BENN)[0]["id"] == sid  # still shown
    shown = run(cli.app, ["statements", "show", str(sid)])
    assert "Checked: Replied from parliament.uk" in shown.output and "text was: I have always supported" in shown.output
    assert run(cli.app, ["statements", "withdraw", str(sid)]).exit_code == 0
    assert published(replies, person=BENN) == []
    assert run(cli.app, ["statements", "decline", str(sid), "--reason", "too late"]).exit_code == 1  # already withdrawn
    everything = run(cli.app, ["statements", "list", "--status", "all"]).output
    assert "withdrawn" in everything and "Reason: Withdrawn by the author." in everything
    # a published statement that breaks the rules is removed, with the reason
    again = replies.submit(**statement())
    replies.publish(again, checked="ok")
    removed = run(cli.app, ["statements", "remove", str(again), "--reason", "It names a private person."])
    assert removed.exit_code == 0 and f"removed #{again}" in removed.output
    assert f"#{again}  removed" in run(cli.app, ["statements", "list", "--status", "removed"]).output


def test_the_reports_commands(settings, monkeypatch):
    from typer.testing import CliRunner

    from hansard import cli

    monkeypatch.setattr(cli, "_settings", lambda: settings)
    replies = replies_for(settings)
    rid = replies.report(kind="takedown", page="/debates/2021-03-04/commons/trial-by-jury",
                         message="Please remove my name from this page.", contact="named@example.org")
    run = CliRunner().invoke
    listed = run(cli.app, ["reports", "list"])
    assert listed.exit_code == 0 and f"#{rid}  open" in listed.output and "named@example.org" in listed.output
    assert "A private person's name or details to remove" in listed.output
    closed = run(cli.app, ["reports", "close", str(rid), "--note", "Name removed from the page.",
                           "--public-note", "A private person's name was removed from a debate page."])
    assert closed.exit_code == 0 and f"closed report #{rid} as resolved" in closed.output
    assert run(cli.app, ["reports", "list"]).output == ""  # none open now
    after = run(cli.app, ["reports", "list", "--all"]).output
    assert "named@example.org" not in after and "Reply to: (no contact details)" in after
    assert "What was done: Name removed from the page." in after
    again = run(cli.app, ["reports", "close", str(rid)])
    assert again.exit_code == 1 and f"no open report {rid}" in again.output


# ---- how long the email address is kept ------------------------------------------------------------------------------

def test_the_email_is_deleted_three_months_after_a_statement_is_withdrawn_or_declined(settings):
    clock = FixedClock("2026-08-12T09:00:00+00:00")
    replies = replies_for(settings, clock)
    ids = [replies.submit(**statement()) for _ in range(3)]
    replies.publish(ids[0], checked="ok")  # shown: kept
    replies.withdraw(ids[1])
    clock.advance(days=2)
    replies.decline(ids[2], "It is about another member's conduct.")
    ids.append(replies.submit(**statement()))  # still pending: kept
    clock.set("2026-11-12T08:59:00+00:00")
    assert replies.forget_old_emails() == 0  # not quite three months
    clock.set("2026-11-12T09:01:00+00:00")
    assert replies.forget_old_emails() == 1
    emails = {s["id"]: s["email"] for s in replies.statements()}
    assert emails[ids[1]] is None and emails[ids[2]] and emails[ids[0]] and emails[ids[3]]
    assert months_before(datetime(2026, 5, 31, tzinfo=UTC), 3) == datetime(2026, 2, 28, tzinfo=UTC)
    # a statement nobody decides in three months lapses, and its address goes at once (wts-core docs/adr/0008)
    clock.set("2026-11-14T09:00:01+00:00")
    assert replies.lapse_undecided() == (1, 0)
    lapsed = replies.statement(ids[3])
    assert lapsed["status"] == "declined" and lapsed["email"] is None and lapsed["reason"] == "No decision was made in time; please send it again."


# ---- never the model's, the record's or the search's -----------------------------------------------------------------

def test_statements_never_reach_the_model_the_record_or_the_search(site):
    client, state, fake = site
    first = events(client.post("/api/ask", json={"question": "What has been said about trial by jury?"}))[-1]
    bid = first["briefing_id"]
    text = f"{MARKER}: I said this about juries and I would say it again today in the House."
    for item in (f"/b/{bid}", f"/people/{BENN}"):
        client.post("/api/statements", json=statement(item=item, text=text))
    for s in list_statements(client):
        client.app.state.replies.publish(s["id"], about=str(BENN), checked="ok")
    assert len(client.get(f"/api/statements?path=/b/{bid}").json()["statements"]) == 1

    fake.calls.clear()
    asked = events(client.post("/api/ask", json={"question": "What did Hilary Benn say about trial by jury?"}))
    again = events(client.post("/api/ask", json={"question": "Have MPs debated juries in fraud trials?"}))
    assert fake.calls, "the model was asked"
    for call in fake.calls:
        sent = json.dumps(call, default=str)
        assert MARKER not in sent and "I would say it again today" not in sent
    for out in (asked, again):
        assert MARKER not in json.dumps(out)
    assert "I would say it again today" not in client.get(f"/api/briefings/{bid}").text
    found = client.post("/api/search", json={"question": MARKER}).text
    assert "I would say it again today" not in found
    assert "I would say it again today" not in client.get(f"/api/v1/search?q={MARKER}").text
    assert "I would say it again today" not in client.get(f"/people/{BENN}").text  # not in the page's readable text
    assert "I would say it again today" not in client.get(f"/api/people/{BENN}?part=core").text


def test_nothing_that_searches_or_writes_briefings_reads_statements():
    """Statements are read only by the site's reports-and-statements module, the web app's statement endpoints, the
    owner's commands and the clean-up in the update; never by the briefing pipeline, the search, the index or the
    record's pages and feeds. (The table is the shared package's, ``wts_statements``; ``store/wts.py`` names the
    site's old one once, to bring its rows across.)"""
    src = Path(__file__).resolve().parents[1] / "src" / "hansard"
    allowed = {"replies.py", "owner.py", "api/app.py", "cli.py", "sync.py", "store/wts.py"}
    table = re.compile(r"\b(FROM|JOIN|INTO|UPDATE|TABLE IF NOT EXISTS)\s+(wts_)?statements?(_log)?\b", re.I)
    for path in src.rglob("*.py"):
        rel = path.relative_to(src).as_posix()
        code = path.read_text(encoding="utf-8")
        if rel in allowed:
            continue
        assert "hansard.replies" not in code and "hansard.owner" not in code and "wts_core.replies" not in code, rel
        assert not table.search(code), rel
    app = (src / "api" / "app.py").read_text(encoding="utf-8")
    # in the web app, statements are only used by the statement endpoints
    users = [line for line in app.splitlines()
             if re.search(r"\b(published|replies\.statements|replies\.submit)\(", line)]
    assert len(users) == 3


def test_the_forms_let_through_no_more_than_the_rules_allow_so_no_too_long_refusal_is_ever_reached(
        settings, tmp_path, monkeypatch):
    """wts-core 0.3.0 lets a site leave out the six "too long" messages its own forms make unreachable, saying in
    ``ReplyRules.form_max`` what the forms let through. The figures are the request bodies' own (the web pages'
    fields have the same), and each is within the rule. Sent through the form, a field one character longer is
    refused by the form, as it always was, before the right of reply sees it. The owner's own update, which comes
    through no form, is told the limit in the package's words for the owner (O)."""
    from typer.testing import CliRunner

    from hansard import cli
    from hansard.api.app import ReportBody, StatementBody
    from hansard.replies import REPLY_MESSAGES, REPLY_RULES

    def longest(model, field: str) -> int:
        return next(m.max_length for m in model.model_fields[field].metadata if hasattr(m, "max_length"))

    from_forms = {"text": longest(StatementBody, "text"), "name": longest(StatementBody, "name"),
                  "role": longest(StatementBody, "role"), "link": longest(StatementBody, "link"),
                  "report": longest(ReportBody, "message"), "contact": longest(ReportBody, "contact")}
    assert dict(REPLY_RULES.form_max) == from_forms
    limits = {"text": "max_chars", "name": "name_max", "role": "role_max", "link": "link_max",
              "report": "report_max", "contact": "contact_max"}
    for part, most in from_forms.items():
        assert most <= getattr(REPLY_RULES, limits[part]), part
    for left_out in ("too_many_chars", "name_too_long", "role_too_long", "link_too_long", "report_too_long",
                     "contact_too_long"):
        assert getattr(REPLY_MESSAGES, left_out) is None, left_out

    monkeypatch.setattr(cli, "_settings", lambda: settings)
    replies = replies_for(settings)
    sid = replies.submit(**statement())
    words = tmp_path / "long.txt"
    words.write_text("word " + "x" * 2100, encoding="utf-8")
    said = CliRunner().invoke(cli.app, ["statements", "update", str(sid), "--name", "N" * 121])
    assert said.exit_code == 1 and "the new name is too long: the most is 120 characters" in said.output
    said = CliRunner().invoke(cli.app, ["statements", "update", str(sid), "--text-file", str(words)])
    assert said.exit_code == 1 and "Traceback" not in said.output
