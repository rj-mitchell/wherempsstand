"""Reports, statements under the right of reply and the owner's commands: the scenario recorded on the site's own
code before wts-core (tests/golden/replies.json, played by tests/golden/shared.py) is played again on the code as
it is now, step for step, and every difference is named here and asserted. Whatever is not named is the same.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest
import shared
from typer.testing import CliRunner

WAS = shared.read("replies.json")
FILLER = "(the old code kept a report of nothing but spaces here)"
SPACES = "a report of spaces"
TAKE_DOWN = "take down a published statement that breaks the rules, as it was typed before wts-core"


class Forms:
    """The site's web client, with one step made up for: the old code kept a report of nothing but spaces, and the
    new code refuses it. So that the reports after it keep their numbers, and the day's count its tenth, a report
    is filed in its place once the refusal has been seen."""

    def __init__(self, client: Any, replies: Any, visitor: Any) -> None:
        self.client, self.replies, self.visitor = client, replies, visitor

    def get(self, *args: Any, **kwargs: Any) -> Any:
        return self.client.get(*args, **kwargs)

    def post(self, url: str, json: Any = None, **kwargs: Any) -> Any:
        r = self.client.post(url, json=json, **kwargs)
        if url == "/api/report" and not (json or {}).get("message", "x").strip():
            assert r.status_code == 400
            self.replies.report(kind="correction", page=None, message=FILLER, visitor=self.visitor())
        return r


def as_before(kept: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """The package's rows under the old tables' names, so that the two can be compared."""
    notes: dict[int, str] = {}
    for entry in kept["log"]:
        if entry["action"] == "published":
            notes[entry["statement"]] = entry["note"]
    statements = [{"id": s["id"], "at": s["at"], "name": s["name"], "role": s["role"], "item_path": s["item"],
                   "person": int(s["about"]) if s["about"] else None, "text": s["text"], "link": s["link"],
                   "email": s["email"], "status": s["status"], "published_at": s["published_at"],
                   "updated_at": s["updated_at"], "closed_at": s["closed_at"], "reason": s["reason"],
                   "checked": notes.get(s["id"])} for s in kept["statements"]]
    return {"reports": kept["reports"], "statements": statements, "briefings": kept["briefings"]}


@pytest.fixture(scope="module")
def played(indexed_settings, tmp_path_factory):
    """The scenario, played once on the code as it is now: what each step gave, and the rows left."""
    from hansard import cli
    from tests.sides import real_side

    tmp = tmp_path_factory.mktemp("replies")
    with pytest.MonkeyPatch.context() as patch:
        side = real_side(indexed_settings, tmp / "replies", shared.FakeSDK(shared.READING), patch)
        patch.setattr(cli, "_settings", lambda: side.settings)
        patch.setattr(cli, "CLOCK", side.clock)
        archive, replies = side.app.state.parts.archive, side.app.state.replies
        log: dict[str, Any] = {}

        def run(args: list[str]) -> tuple[int, str]:
            result = CliRunner().invoke(cli.app, args)
            if args[:3] == ["statements", "decline", "4"] and "--reason" in args and result.exit_code == 1 \
                    and "published" in result.output:
                # the old way of taking a published statement down is refused now: `remove` is the word for it
                log["refused"] = (result.exit_code, result.output)
                result = CliRunner().invoke(cli.app, ["statements", "remove", *args[2:]])
            return result.exit_code, result.output

        def kept() -> dict[str, Any]:
            con = sqlite3.connect(side.state.path)
            con.row_factory = sqlite3.Row
            try:
                return as_before({
                    "reports": [dict(r) for r in con.execute("SELECT * FROM wts_reports ORDER BY id")],
                    "statements": [dict(r) for r in con.execute("SELECT * FROM wts_statements ORDER BY id")],
                    "log": [dict(r) for r in con.execute("SELECT * FROM wts_statement_log ORDER BY id")],
                    "briefings": [dict(r) for r in con.execute(
                        "SELECT id, created_at, question, withdrawn_at, withdrawn_reason FROM wts_briefings "
                        "ORDER BY id")]})
            finally:
                con.close()

        from hansard.api import usage as use
        visitor = lambda: use.visitor_key(side.app.state.core, "testclient", side.clock)  # noqa: E731
        now = shared.plain(shared.replies_scenario(
            client=Forms(side.client, replies, visitor), run=run, tick=side.clock.advance, scratch=tmp / "words",
            save=lambda bid, question, options, events: archive.save(
                question, options, events, id=bid, extra={"records_up_to": "2026-11-12", "model": "claude-sonnet-5-5"}),
            kept=kept), tmp=tmp)
        now["refused"] = log.get("refused")
        now["removed"] = [row["id"] for row in side.client.get(
            "/api/admin/statements?status=removed", headers=shared.ADMIN).json()["statements"]]
        con = sqlite3.connect(side.state.path)
        now["log"] = [list(r) for r in con.execute(
            "SELECT statement, at, action, note, previous FROM wts_statement_log ORDER BY id")]
        con.close()
        yield now



def tidied(value: Any) -> Any:
    """What the old code gave, with the two differences that run through every listing made to it: a name or a
    role typed with runs of spaces is kept with single ones, and the report of spaces is the one filed in its
    place (``Forms``)."""
    text = json.dumps(value).replace("MP for Leeds  South", "MP for Leeds South").replace("Hilary   Benn",
                                                                                           "Hilary Benn")
    return json.loads(text)


def deep(was: Any, now: Any, path: str = "") -> dict[str, tuple[Any, Any]]:
    """Every place two values differ, by its path."""
    if isinstance(was, dict) and isinstance(now, dict):
        out: dict[str, tuple[Any, Any]] = {}
        for key in sorted(set(was) | set(now)):
            out.update(deep(was.get(key, "(absent)"), now.get(key, "(absent)"), f"{path}.{key}" if path else key))
        return out
    if isinstance(was, list) and isinstance(now, list) and len(was) == len(now):
        out = {}
        for i, (a, b) in enumerate(zip(was, now, strict=True)):
            out.update(deep(a, b, f"{path}[{i}]"))
        return out
    return {} if was == now else {path: (was, now)}


def differences(played: dict[str, Any], part: str) -> dict[str, dict[str, tuple[Any, Any]]]:
    assert [s["case"] for s in played[part]] == [s["case"] for s in WAS[part]]
    out = {}
    for was, now in zip(WAS[part], played[part], strict=True):
        # (a form with a field missing is answered with what was typed, as it was typed: nothing to tidy there)
        found = deep(was if was["case"] == "a statement with a field missing" else tidied(was), now)
        if found:
            out[was["case"]] = found
    return out


HINT = " (see `hansard statements list` and `hansard reports list --all`).\n"
NO_SUCH = "No such statement, or it can't be changed in that way now (see `hansard statements list`).\n"


def test_what_the_forms_and_the_public_pages_answer(played):
    """Reports and statements sent, what the public may read, and the owner's lists: 62 steps, three differences."""
    assert len(WAS["http"]) == 62
    assert differences(played, "http") == {
        # R: a report of nothing but spaces was thanked and kept, empty; it is refused, in the words for one too
        # short. Those words are the family's, the same on every Where They Stand
        # site: the owner asked for the family's wording to be the same (16 November 2026); they were "Please say
        # what is wrong: a sentence or more."
        SPACES: {"status": (200, 400), "json.ok": (True, "(absent)"),
                 "json.detail": ("(absent)", "Please say what is wrong, in a few words.")},
        # R: with two things wrong, the words are checked before the name (wts-core's
        # order of the checks); each fault alone is answered as before
        "a statement with two things wrong: the name and the words": {"json.detail": (
            "Please give the name the statement is from.", "A statement can be up to 150 words. This one is 151.")},
        # the report filed in the place of the one of spaces, so that the rest keep their numbers
        "the owner's list of reports": {"json.reports[4].message": ("", FILLER)},
        "the owner's list of open reports": {"json.reports[4].message": ("", FILLER)},
        # page titles are "Page | Site name", as on every Where They Stand site (the charity's family/GLOSSARY.md,
        # "Page titles"); and a saved briefing's title and preview are the site's own neutral words, never the
        # reader's question nor the model's words, which can repeat an allegation (docs/DECISIONS.md, 36)
        "a saved briefing's page": {
            "title": ("<title>What has been said about trial by jury since 2020? — Where MPs Stand</title>",
                      "<title>Briefing from Hansard, 13 November 2026 | Where MPs Stand</title>"),
            "description[0]": (
                '<meta name="description" content="Hilary Benn said that trial by jury was &quot;a cornerstone&quot;. '
                "In the debate that followed: Kenneth Clarke said jury trial would be kept for serious offences. This "
                'was a turning point for every member in the Chamber." />',
                '<meta name="description" content="A briefing written by AI from the Hansard record, with every source '
                'it draws on. Prepared 13 November 2026 from records up to 12 November 2026." />')},
        "a withdrawn briefing's page": {"title": (
            "<title>Briefing withdrawn — Where MPs Stand</title>", "<title>Briefing withdrawn | Where MPs Stand</title>")},
        "the page of a briefing there is not": {"title": (
            "<title>Briefing not found — Where MPs Stand</title>", "<title>Briefing not found | Where MPs Stand</title>")},
    }
    # R: a name or a role typed with runs of spaces is kept with single ones (as it was always shown on a page)
    listed = {s["id"]: s for s in played["http"][-5]["json"]["statements"]}
    assert played["http"][-5]["case"] == "the owner's list of statements"
    assert listed[1]["name"] == "Hilary Benn" and listed[1]["role"] == "MP for Leeds South"
    assert shared.STATEMENT["name"] == "  Hilary   Benn " and shared.STATEMENT["role"] == "MP for Leeds  South"


def test_what_the_owners_commands_print(played):
    """The commands as they were typed before wts-core: the same lines for whatever was done, and a plainer
    reason for whatever was not (O: wts-core's commands)."""
    assert differences(played, "cli") == {
        "publish with an empty note": {"output": (
            "Please say how you confirmed who sent it: it is kept privately.\n",
            "Not done: say how you confirmed who sent it (checked), for the log" + HINT)},
        "publish it again": {"output": (NO_SUCH, "Not done: statement 1 is published, not pending" + HINT)},
        "publish one there is not": {"output": (NO_SUCH, "Not done: no statement 99" + HINT)},
        "decline with a reason of spaces": {"output": (
            "Please give the reason: it is sent to the author.\n",
            "Not done: give the reason: the sender is told why" + HINT)},
        "decline one already declined": {"output": (NO_SUCH, "Not done: statement 3 is declined, not pending" + HINT)},
        # O: an update that changes nothing said "updated" and gave the statement a new date; it is refused
        "update with nothing to change": {"exit": (0, 1), "output": (
            "updated #1\n", "Not done: nothing to change: give new words, a name, a role, a link or whom it speaks "
                            "for" + HINT)},
        "update a declined statement": {"output": (
            NO_SUCH, "Not done: statement 3 is declined, not pending or published" + HINT)},
        "withdraw it again": {"output": (
            NO_SUCH, "Not done: statement 2 is withdrawn, not pending or published" + HINT)},
        # O: taking a published statement down is `statements remove` now (below), and it is "removed"
        TAKE_DOWN: {"output": (
            "declined #4: tell the author why; their email address is deleted in three months\n",
            "removed #4: tell the author which rule it broke; their email address is deleted in three months\n")},
        "every statement": {"output": (
            tidied(next(s["output"] for s in WAS["cli"] if s["case"] == "every statement")),
            tidied(next(s["output"] for s in WAS["cli"] if s["case"] == "every statement")).replace(
                "#4  declined  received", "#4  removed  received"))},
    }
    # the old way of taking a published statement down, `statements decline`, is refused and says why; the test
    # then typed `statements remove` with the same reason, and that is the line compared above
    assert played["refused"] == (1, "Not done: statement 4 is published, not pending" + HINT)


def test_what_the_owners_pages_do_and_what_is_shown_afterwards(played):
    FEED = next(i for i, c in enumerate(WAS["after"]) if c["case"] == "the corrections feed")
    assert differences(played, "admin") == {}  # all 18 steps as before, taking a published statement down included
    assert len(WAS["admin"]) == 18
    assert differences(played, "after") == {
        # R: the public log no longer names the briefing a closed report was about: only the date, the kind and
        # the owner's public note (wts-core CHANGELOG 0.2.1; the note says what was put right)
        "the corrections log, with a note and two withdrawn": {"json.corrections[2].briefing_id": ("briefingaa", None)},
        "the owner's list of reports, now": {"json.reports[4].message": ("", FILLER)},
        # the feed's title is "Corrections | Where MPs Stand", as page titles are (family/GLOSSARY.md, "Page titles")
        "the corrections feed": {"text": (
            WAS["after"][FEED]["text"],
            WAS["after"][FEED]["text"].replace("<title>Corrections — Where MPs Stand</title>",
                                               "<title>Corrections | Where MPs Stand</title>"))},
        # O: a statement taken down after it was published is "removed", not "declined"; and a statement keeps
        # the time it was first published when it is updated (the date shown beside it is the update's, as before)
        "the owner's list of statements, now": {
            "json.statements[0].published_at": ("2026-11-13T13:45:00+00:00", "2026-11-13T13:39:00+00:00"),
            "json.statements[1].status": ("declined", "removed"),
            "json.statements[4].published_at": ("2026-11-13T13:21:00+00:00", "2026-11-13T13:09:00+00:00"),
            "json.statements[4].status": ("declined", "removed"),
            # the update that changed nothing no longer counts as one
            "json.statements[4].updated_at": ("2026-11-13T13:21:00+00:00", "2026-11-13T13:20:00+00:00")},
        "the owner's list of declined statements": {"json.statements": (
            tidied(WAS["after"][12]["json"]["statements"]),
            [s for s in tidied(WAS["after"][12]["json"]["statements"]) if s["id"] == 3])},
    }
    assert [s["id"] for s in WAS["after"][12]["json"]["statements"]] == [4, 3, 1]
    assert played["removed"] == [4, 1]  # the two taken down: listed under their own word now


def test_the_rows_kept(played):
    """What is in the tables after the forms, and at the end: the old columns, but for the visitor's code, which
    the package keeps on no report and no statement (the day's count is kept apart, without the report)."""
    def without_codes(kept: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
        assert all("visitor" in row for table in ("reports", "statements") for row in kept[table])
        return {table: [{k: v for k, v in row.items() if k != "visitor"} for row in rows]
                for table, rows in tidied(kept).items()}

    assert deep(without_codes(WAS["kept_after_the_forms"]), played["kept_after_the_forms"]) == {
        "reports[6].message": ("", FILLER)}
    assert deep(without_codes(WAS["kept"]), played["kept"]) == {
        "reports[6].message": ("", FILLER),
        "statements[0].status": ("declined", "removed"),
        "statements[0].published_at": ("2026-11-13T13:21:00+00:00", "2026-11-13T13:09:00+00:00"),
        "statements[0].updated_at": ("2026-11-13T13:21:00+00:00", "2026-11-13T13:20:00+00:00"),
        "statements[3].status": ("declined", "removed"),
        "statements[4].published_at": ("2026-11-13T13:45:00+00:00", "2026-11-13T13:39:00+00:00"),
    }
    # and what the old table had no place for: everything done to a statement, in order
    assert [(entry[0], entry[2]) for entry in played["log"]] == [
        (1, "received"), (2, "received"), (3, "received"), (4, "received"), (5, "received"),
        (1, "published"), (3, "declined"), (1, "updated"), (1, "updated"), (1, "updated"),
        (2, "published"), (2, "withdrawn"), (4, "published"), (4, "removed"),
        (5, "published"), (1, "removed"), (5, "updated"), (5, "updated"), (5, "withdrawn")]
