"""Hiding a saved briefing while a concern about it is decided (the charity's policy on replies, 7.3; wts-core's
``Replies.hide``): `hansard hide /b/<id> --note "…"`, `hansard unhide`, `hansard hidden`, `hansard waiting`.

Hiding is not withdrawing: it is undone with ``unhide``, and nothing is listed on the corrections page. While a
briefing is hidden its address shows the owner's note in its place; it is in no public list (topics, featured
briefings, the site map, the feeds); a statement beside it is hidden with it; and it is not played from the answer
cache to the next reader with the same question. What can be hidden on this site is a saved briefing, by any
spelling of its address; anything else is refused in words for the owner, never "hidden" for nothing.
"""

from __future__ import annotations

import sqlite3

import pytest
import shared
from typer.testing import CliRunner

from hansard import cli

from .sides import scripted_side

NOTE = "This briefing is not shown while a concern about it is looked into."
ADMIN = {"Authorization": f"Bearer {shared.ADMIN_TOKEN}"}
HINT = " (see `hansard statements list` and `hansard reports list --all`).\n"


@pytest.fixture
def site(tmp_path, monkeypatch):
    side = scripted_side(tmp_path, monkeypatch, requests_per_minute=1000)
    monkeypatch.setattr(cli, "CLOCK", side.clock)
    monkeypatch.setattr(cli, "_settings", lambda: side.settings)
    return side


def run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(cli.app, list(args))
    return result.exit_code, result.output


def ask(site, question=shared.Q, **body):
    status, text = site.ask(question, **body)
    assert status == 200
    return [e for e in shared.events_of(text) if isinstance(e, dict)]


def test_a_hidden_briefing_shows_the_note_in_its_place_everywhere_and_is_shown_again_when_unhidden(site):
    bid = ask(site)[-1]["briefing_id"]
    assert site.client.post("/api/admin/featured", headers=ADMIN, json={"briefing": bid}).status_code == 200
    statement = site.client.post("/api/statements", json={
        "item": f"/b/{bid}", "name": "Hilary Benn", "role": "MP for Leeds South", "link": "",
        "text": "My view is set out in the debate of 4 March 2021.", "email": "office@example.org"})
    assert statement.status_code == 200
    assert run("statements", "publish", "1", "--checked", "Telephoned the office.")[0] == 0
    shown = site.get(f"/api/briefings/{bid}")
    assert shown[0] == 200 and len(site.get(f"/api/statements?path=/b/{bid}")[1]["statements"]) == 1
    assert [f["id"] for f in site.get("/api/topics")[1]["featured"]] == [bid]
    assert f"/b/{bid}" in site.client.get("/sitemap-briefings.xml").text

    # hidden, by any spelling of its address: filed under the one the pages ask about
    assert run("hide", f"https://www.wherempsstand.com/b/{bid}/", "--note", NOTE) == (
        0, f"hidden: /b/{bid} (its address now shows the note in its place; `hansard unhide /b/{bid}` shows it "
           "again)\n")
    assert site.get(f"/api/briefings/{bid}") == [423, {"detail": NOTE}]
    page = site.client.get(f"/b/{bid}")
    assert page.status_code == 423 and "Briefing not shown for now" in page.text and NOTE in page.text
    assert "Hilary Benn said" not in page.text  # nothing of the briefing is in its page
    assert site.client.get(f"/b/{bid}/card.png").status_code == 423
    assert site.get(f"/api/statements?path=/b/{bid}")[1]["statements"] == []  # the statement is hidden with it
    assert site.get("/api/topics")[1]["featured"] == []
    assert f"/b/{bid}" not in site.client.get("/sitemap-briefings.xml").text
    # no new statement is taken beside it while it is hidden
    refused = site.client.post("/api/statements", json={
        "item": f"/b/{bid}", "name": "Another Member", "role": "MP for Somewhere", "link": "",
        "text": "A second statement, sent while it is hidden.", "email": "office@example.org"})
    # R: said as what it is (wts-core 0.3.0, ``ReplyMessages.item_hidden``); it was "That page isn't being shown
    # at the moment, so a statement can't sit beside it.", which is still said of a page that is not there
    assert refused.status_code == 400 and refused.json()["detail"] == (
        "That briefing is not being shown while a concern about it is looked into, so a statement can't sit beside "
        "it for now.")
    # it is not a withdrawal: nothing is on the corrections page, and the owner still sees it in the admin list
    assert site.get("/api/corrections") == [200, {"corrections": []}]
    assert [b["id"] for b in site.client.get("/api/admin/briefings", headers=ADMIN).json()["briefings"]] == [bid]
    assert run("hidden") == (0, f"/b/{bid}  hidden since 2026-11-13\n    shown in its place: {NOTE}\n")
    assert run("waiting") == (0, "Nothing is waiting for a decision. Hidden for now: 1 briefing(s).\n")
    assert run("would-lapse") == (0, "At the next tidy, 0 statement(s) nobody has decided would be declined as "
                                     "lapsed, and 0 open report(s) would lose their contact details (those sent "
                                     "before 2026-08-13).\n")

    # shown again: everything is as it was
    assert run("unhide", f"/b/{bid}") == (0, f"shown again: /b/{bid}\n")
    assert site.get(f"/api/briefings/{bid}") == shown
    assert len(site.get(f"/api/statements?path=/b/{bid}")[1]["statements"]) == 1
    assert [f["id"] for f in site.get("/api/topics")[1]["featured"]] == [bid]
    assert run("hidden") == (0, "") and run("unhide", f"/b/{bid}") == (1, f"Not done: /b/{bid} is not hidden" + HINT)


def test_a_hidden_briefing_is_not_played_from_the_cache_to_the_next_reader(site):
    first = ask(site)[-1]
    assert ask(site)[-1]["briefing_id"] == first["briefing_id"] and site.calls() == ["written"]
    assert run("hide", f"/b/{first['briefing_id']}", "--note", NOTE)[0] == 0
    fresh = ask(site)[-1]  # the same question: written afresh, never the hidden briefing
    assert site.calls() == ["written", "written"] and fresh["briefing_id"] != first["briefing_id"]
    assert site.get(f"/api/briefings/{fresh['briefing_id']}")[0] == 200
    # a cached copy met all the same (hidden by other means than the command): never played; the reader is sent
    # the sources and the note, and the copy is dropped
    site.app.state.replies.hide(f"/b/{fresh['briefing_id']}", NOTE)
    sent = ask(site)
    assert [e["type"] for e in sent][-2:] == ["sources", "final"] and site.calls() == ["written", "written"]
    assert sent[-1]["answer"] is None and sent[-1]["note"] == NOTE
    assert ask(site)[-1]["briefing_id"] not in (first["briefing_id"], fresh["briefing_id"])
    assert site.calls() == ["written", "written", "written"]


def test_only_a_saved_briefing_can_be_hidden_and_anything_else_is_refused_in_words_for_the_owner(site):
    """An independent review of the sister sites found that `hide` stored whatever was typed: a full address, a
    trailing slash, another case or a thing that does not exist printed "hidden" and hid nothing."""
    bid = ask(site)[-1]["briefing_id"]
    only = ("Not done: only a saved briefing can be hidden: give its address, /b/ and its ten characters (a page of "
            "the official record is not hidden here: docs/OPERATIONS.md, \"Hiding a briefing\")" + HINT)
    for not_one in ("/people/10669", "/debates/2021-03-04/commons/trial-by-jury", "/topics/trial-by-jury", "/about",
                    "https://example.org/b/" + bid, "record:uk.org.publicwhip/debate/2021-03-04a.1.1", bid,
                    f"/B/{bid.upper()}"):
        assert run("hide", not_one, "--note", NOTE) == (1, only), not_one
    assert run("hide", "/b/briefingzz", "--note", NOTE) == (1, "Not done: there is no briefing at /b/briefingzz" + HINT)
    assert run("hide", f"/b/{bid}", "--note", "  ") == (
        1, "Not done: give the note that is shown in the item's place while it is hidden" + HINT)
    con = sqlite3.connect(site.state.path)
    assert con.execute("SELECT count(*) FROM wts_hidden").fetchone()[0] == 0  # nothing was filed for any of them
    con.close()
    assert site.get(f"/api/briefings/{bid}")[0] == 200
    # every spelling of one briefing's address is one item: hidden once, and found hidden by each
    assert run("hide", f"wherempsstand.com/b/{bid}", "--note", NOTE)[0] == 0
    assert run("hide", f"/b/{bid}/", "--note", NOTE) == (
        1, f"Not done: /b/{bid} is already hidden: unhide it first to change the note" + HINT)
    assert run("unhide", f"https://wherempsstand.com/b/{bid}/") == (0, f"shown again: /b/{bid}\n")


def test_a_topics_briefing_hidden_is_not_the_topics_briefing_while_it_is_hidden(tmp_path, monkeypatch):
    """A topic's page shows its newest briefing still standing: a hidden one is passed over, as a withdrawn one
    is, and comes back when it is shown again. The update does not write another in its place for that alone."""
    from hansard import topics
    from hansard.store.wts import open_core

    from .test_import import old_database

    folder = tmp_path / "old-state"
    old_database(folder / "data" / "state.sqlite")
    from tests import sides

    settings = sides.settings_in(folder, monkeypatch)
    (folder / "topics.toml").write_text(shared.TOPICS)
    settings.raw["topics"] = {"file": str(folder / "topics.toml")}
    from wts_core.testing import FixedClock

    clock = FixedClock(shared.START)
    side = sides.new_side(shared.ScriptedPipeline(clock.advance, settings.answer["model"]), settings, clock,
                          monkeypatch)
    monkeypatch.setattr(cli, "CLOCK", clock)
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    before = side.get("/api/topics/trial-by-jury")
    assert before[0] == 200 and before[1]["briefing"]["id"] == "briefingad"
    assert run("hide", "/b/briefingad", "--note", NOTE)[0] == 0
    hidden = side.get("/api/topics/trial-by-jury")
    assert hidden[0] == 200 and hidden[1]["briefing"] is None and hidden[1]["versions"] == []
    assert "Hilary Benn said" not in side.client.get("/topics/trial-by-jury").text
    listed = {t["slug"]: t["current"] for t in side.get("/api/topics")[1]["topics"]}
    assert listed["trial-by-jury"] is None
    # for the update, the topic still has its briefing: hiding is for the time being, not a reason to rewrite
    core = open_core(settings, clock)
    from hansard.api.usage import desk

    assert topics.current(side.state, desk(settings, core, clock).archive, "trial-by-jury")["briefing_id"] == "briefingad"
    assert run("unhide", "/b/briefingad")[0] == 0
    assert side.get("/api/topics/trial-by-jury") == before
