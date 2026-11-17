"""The record of each briefing, the structured log, the statistics, the weekly report, the family's check route and
the marks of what happened next (wts-core's ``wts_core.observe``; its docs/adr/0012), as this site turns them on."""

from __future__ import annotations

import json
import logging
import sqlite3

import pytest
import shared
from fastapi.testclient import TestClient
from typer.testing import CliRunner
from wts_core.adapters.hashing import HashingEmbedder
from wts_core.observe import FIELDS, LOGGER

from hansard import cli
from hansard.api.app import create_app, start_logging

from .sides import scripted_side

PACKAGE = __import__("importlib.metadata").metadata.version("wts-core")
CANARY = "What did ministers say about the xylophonecanary bill of 2020?"
ADMIN = {"Authorization": f"Bearer {shared.ADMIN_TOKEN}"}


@pytest.fixture
def site(tmp_path, monkeypatch):
    """The site over a scripted pipeline, on a fixed clock, with the structured log turned on."""
    side = scripted_side(tmp_path, monkeypatch, requests_per_minute=1000)
    monkeypatch.setattr(cli, "CLOCK", side.clock)
    monkeypatch.setattr(cli, "_settings", lambda: side.settings)
    monkeypatch.setattr(cli, "_embedder", lambda: HashingEmbedder(64))  # no model is fetched in a test
    handler = start_logging(side.settings, side.clock)
    side.log_lines = lambda: (side.settings.log_dir / "briefings.jsonl").read_text().splitlines()
    yield side
    logging.getLogger(LOGGER).removeHandler(handler)
    handler.close()


def ask(site, question=shared.Q, play="written", **body):
    status, text = site.ask(question, play, **body)
    assert status == 200, text
    return [e for e in shared.events_of(text) if isinstance(e, dict)]


def records(site):
    con = sqlite3.connect(site.state.path)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM wts_briefing_log ORDER BY rowid")]
    finally:
        con.close()


def run(*args, exit_code=0):
    result = CliRunner().invoke(cli.app, list(args))
    assert result.exit_code == exit_code, result.output
    return result.output


def test_every_question_leaves_one_record_with_what_it_cost_what_the_checks_found_and_how_it_ended(site):
    bid = ask(site)[-1]["briefing_id"]
    ask(site)                                   # the same again: from the cache
    ask(site, shared.Q2, "no_records")
    ask(site, shared.Q3, "raises")
    ask(site, shared.Q4, sources_only=True)
    written = records(site)
    assert [r["outcome"] for r in written] == ["completed", "cached", "no_records", "error", "sources_only"]
    first = written[0]
    assert first["kind"] == "reader" and first["briefing_id"] == bid
    # the question is kept only where the site already kept it (wts-core 0.2.1): with a briefing written, replayed,
    # answered "no records" or failed; never for a request that got the sources only
    assert [r["question"] for r in written] == [shared.Q, shared.Q, shared.Q2, shared.Q3, ""]
    assert first["model"] == site.settings.answer["model"] and first["parse_model"] == "claude-haiku-5-5"
    assert first["config_id"] and first["package_version"] == PACKAGE
    assert first["sources"] == len(shared.CARDS) == 4  # and how they are spread, by the group each card names
    assert json.loads(first["spread"])["opposition"] == 1 and sum(json.loads(first["spread"]).values()) <= 4
    assert first["citations_total"] == 2 and first["cost_usd"] > 0 and first["input_tokens"] > 0
    assert first["t_parse"] == 400 and first["t_sources_sent"] == 700 and first["t_total"] > 2000
    assert written[3]["error_type"] == "RuntimeError" and "stream broke" not in json.dumps(written[3])
    # never the visitor's code, in any record
    con = sqlite3.connect(site.state.path)
    codes = {r[0] for r in con.execute("SELECT visitor FROM wts_spending") if r[0]}
    kept = json.loads(con.execute("SELECT settings FROM wts_configs WHERE config_id = ?",
                                  (first["config_id"],)).fetchone()[0])
    con.close()
    assert codes and not any(code in json.dumps(written) for code in codes)
    # the configuration behind the id is kept once, so a figure can be compared before and after a change: never
    # a key, and nothing about a reader
    assert kept["embedding"]["backend"] == "hashing" and "ranking" in kept and kept["answer"]["summary_sources"]
    assert "api_key" not in json.dumps(kept).lower() and "sk-" not in json.dumps(kept)


def test_what_happened_next_is_noted_on_the_briefings_record(site):
    """The signals that already arrive: "Was this useful?" (by the key the page was given with the answer), a
    report that names the briefing, its withdrawal, and a report of it closed with a public note."""
    final = ask(site)[-1]
    bid, key = final["briefing_id"], final["briefing_key"]

    def marks():
        r = records(site)[0]
        return r["useful"], r["reported"], r["corrected"], r["followed_up"]

    assert marks() == (None, 0, 0, 0)
    assert site.post("/api/feedback", {"question": shared.Q, "useful": False, "briefing_key": key})[0] == 200
    assert marks() == (0, 0, 0, 0)
    assert site.post("/api/feedback", {"question": shared.Q, "useful": True, "briefing_key": key})[0] == 200
    assert marks() == (1, 0, 0, 0)
    # a key that is not one, or no key at all, marks nothing and is thanked as before
    for sent in ({"briefing_key": "not-a-key"}, {"briefing_key": "f" * 64}, {}):
        assert site.post("/api/feedback", {"question": shared.Q, "useful": False, **sent}) == [200, {"ok": True}]
    assert marks() == (1, 0, 0, 0)
    assert site.post("/api/report", {"kind": "correction", "message": "A date is wrong.", "briefing_id": bid})[0] == 200
    assert marks() == (1, 1, 0, 0)
    # a report from a robot (the hidden field filled) marks nothing
    second = ask(site, shared.Q2)[-1]["briefing_id"]
    site.post("/api/report", {"kind": "correction", "message": "A date is wrong.", "briefing_id": second,
                              "website": "https://spam.example"})
    assert records(site)[1]["reported"] == 0
    # the report closed with a public note: the briefing was put right
    closed = site.client.post("/api/admin/reports/1/close", headers=ADMIN, json={
        "status": "resolved", "resolution": "The date was put right.", "public_note": "A date was corrected."})
    assert closed.status_code == 200 and marks() == (1, 1, 1, 0)
    # and a withdrawal
    assert site.client.post(f"/api/admin/briefings/{second}/withdraw", headers=ADMIN,
                            json={"reason": "It misread a speech."}).status_code == 200
    assert records(site)[1]["corrected"] == 1
    # a follow-up is recorded as one on its own record. The earlier briefing's is not marked: the page sends the
    # question it follows, not the briefing's address, and the API gains no field for it.
    ask(site, shared.Q4, history=shared.EARLIER, follows=shared.Q)
    assert records(site)[-1]["follow_up"] == 1 and marks()[3] == 0


def test_the_structured_log_holds_stages_and_times_and_never_the_question(site):
    ask(site, CANARY)
    lines = [json.loads(line) for line in site.log_lines()]
    assert [line["stage"] for line in lines] == ["parsed", "sources_sent", "first_token", "checked", None]
    assert [line["event"] for line in lines] == ["briefing.stage"] * 4 + ["briefing.completed"]
    assert all(set(line) == set(FIELDS) and line["site"] == "hansard" for line in lines)
    (record,) = records(site)
    assert {line["briefing"] for line in lines} == {record["id"]} and len(record["id"]) == 11
    assert "xylophonecanary" not in "\n".join(site.log_lines())
    logs = site.settings.section("logs")  # 14 days or 50 MB, whichever is less
    assert logs["days"] == 14 and logs["max_bytes"] * (logs["keep"] + 1) <= 50_000_000


def test_the_statistics_command_and_the_one_the_site_already_had(site):
    ask(site)
    ask(site)
    ask(site, shared.Q2, "raises")
    figures = json.loads(run("stats", "--since", "7d", "--json"))["stats"]
    assert figures["period"]["questions"] == 3
    assert {k: v for k, v in figures["period"]["by_outcome"].items() if v} == {"completed": 1, "cached": 1,
                                                                               "error": 1}
    assert "questions" in run("stats", "--since", "7d")
    commands = __import__("typer").main.get_command(cli.app).commands
    # every command the owner had is still there, with the new ones beside them
    assert {"status", "index-status", "backup", "restore", "offsite", "sync", "serve", "eval", "topics", "user",
            "statements"} <= set(commands)
    assert {"stats", "weekly-report", "tidy", "backup-check", "reports"} <= set(commands)


def test_the_weekly_report_is_written_and_proposes_thresholds_while_none_are_set(site):
    for n in range(3):
        ask(site, f"What has been said about question {n}?")
    site.clock.set("2026-11-16T07:07:00+00:00")  # the Monday after
    out = run("weekly-report")
    folder = site.settings.reports_dir
    assert sorted(p.name for p in folder.iterdir()) == ["2026-W46.json", "2026-W46.md"]
    assert all(oct(p.stat().st_mode & 0o777) == "0o600" for p in folder.iterdir())  # they quote readers' questions
    page = (folder / "2026-W46.md").read_text()
    assert page.startswith("# Where MPs Stand: how the briefings went, 9 November 2026 to 16 November 2026")
    # no threshold is set, so nothing can trip: the command proposes some from the week's figures, for the owner
    assert site.settings.section("alerts") == {}
    assert "No alert thresholds are set yet." in out and "daily_cost_usd = " in out
    site.settings.raw["alerts"] = {"error_rate": 0.1}
    ask(site, shared.Q2, "raises")
    assert "error" in run("stats", "--since", "1d", "--check", exit_code=1)  # one in four failed: over one in ten


def test_the_nightly_tidy_deletes_what_the_privacy_notice_says_is_deleted(site):
    ask(site, CANARY, "raises")                       # a briefing that could not be written
    ask(site)
    con = sqlite3.connect(site.state.path)
    visitor = con.execute("SELECT visitor FROM wts_spending").fetchone()[0]
    con.close()
    assert visitor
    site.clock.advance(days=1)
    assert "Right of reply: 0 statement(s) nobody decided" in run("tidy")
    con = sqlite3.connect(site.state.path)  # a visitor's code is kept for its own day only, and its secret with it
    assert {r[0] for r in con.execute("SELECT visitor FROM wts_spending")} == {""}
    assert con.execute("SELECT count(*) FROM wts_kv WHERE key LIKE 'visitor_salt:%'").fetchone()[0] == 0
    con.close()
    site.clock.advance(days=91)                       # 90 days on: the failed briefing's question is removed,
    run("tidy")                                       # from its record and from the note of the failure
    kept = records(site)
    assert [r["question"] for r in kept] == ["", shared.Q] and kept[0]["outcome"] == "error"
    con = sqlite3.connect(site.state.path)  # in none of the shared package's tables, the note of the failure included
    for (table,) in con.execute("SELECT name FROM sqlite_master WHERE name LIKE 'wts_%' AND type = 'table'").fetchall():
        assert "xylophonecanary" not in json.dumps([tuple(r) for r in con.execute(f"SELECT * FROM {table}")],
                                                   default=str), table
    con.close()
    site.clock.advance(days=310)                      # 13 months on: the records themselves go
    run("tidy")
    assert records(site) == []


def test_the_family_check_answers_only_with_the_token_and_gives_counts_and_ages(tmp_path, monkeypatch):
    """`GET /api/check`: not there at all on a site with no check token set (answered like any other address of
    the API that does not exist); 401 for a wrong token; counts and ages for the right one, and never a question,
    a name or a word of a report."""
    side = scripted_side(tmp_path / "off", monkeypatch)
    assert side.client.get("/api/check").status_code == side.client.get("/api/no-such-address").status_code == 404
    assert side.client.get("/api/check").json() == side.client.get("/api/no-such-address").json()

    # the token is read when the site starts: the site is started again with it set
    side = scripted_side(tmp_path / "on", monkeypatch, requests_per_minute=1000)
    monkeypatch.setenv("HANSARD_CHECK_TOKEN", "the-family-check-token")
    side.client = TestClient(create_app(side.settings, pipeline=side.pipeline, state=side.state, clock=side.clock))
    ask(side, CANARY)
    side.post("/api/report", {"kind": "correction", "message": "The canary-report date is wrong."})
    assert side.client.get("/api/check").status_code == 401
    assert side.client.get("/api/check", headers={"Authorization": "Bearer wrong"}).status_code == 401
    answer = side.client.get("/api/check", headers={"Authorization": "Bearer the-family-check-token"})
    assert answer.status_code == 200
    body = answer.json()
    assert body["site"] == "hansard" and body["versions"]["package"] == PACKAGE
    assert body["waiting"]["reports"] == 1 and body["waiting"]["statements"] == 0
    assert "xylophonecanary" not in answer.text and "canary-report" not in answer.text
