"""What is kept about a reader, and where a statement under the right of reply may go: the two checks every Where
They Stand site runs over its real pipeline (wts-core, ``wts_core.testing``; its docs/adr/0008 and 0012), and what
this site's privacy notice says of the record of each briefing, of statements and of backups."""

from __future__ import annotations

import json
import logging
import re
import sqlite3

import pytest
import shared
from fastapi.testclient import TestClient
from wts_core.adapters.hashing import HashingEmbedder
from wts_core.observe import LOGGER
from wts_core.testing import assert_no_reader_data, assert_statements_stay_out

from hansard.api.app import create_app, start_logging
from hansard.api.usage import visitor_key
from hansard.config import REPO_ROOT
from hansard.index.build import Indexer, embed_pending
from hansard.store.wts import BACKUPS

from .sides import real_side

CANARY = "What has been said about trial by jury and the xylophonecanary since 2020?"
ADDRESS = "203.0.113.77"


class Recording(HashingEmbedder):
    """The tests' own embedder, keeping every text it is given: what was embedded, of the record and of questions."""

    def __init__(self) -> None:
        super().__init__(64)
        self.texts: list[str] = []

    def embed_documents(self, texts):
        self.texts += list(texts)
        return super().embed_documents(texts)

    def embed_query(self, text):
        self.texts.append(text)
        return super().embed_query(text)


class Asked:
    """The site's real model client (over the faked SDK), keeping each request it is given: what the shared
    package's check reads of a model (``wts_core.testing``'s ``ModelSeen``). Everything else is the client's own."""

    def __init__(self, client) -> None:
        self._client, self.requests, self.structured_requests = client, [], []

    def __getattr__(self, name):
        return getattr(self._client, name)

    def stream(self, request):
        self.requests.append(request)
        return self._client.stream(request)

    def structured(self, request, schema):
        self.structured_requests.append((request, schema))
        return self._client.structured(request, schema)


@pytest.fixture
def site(indexed_settings, tmp_path, monkeypatch, caplog):
    """The site's real pipeline over the tests' real index, with a faked model that keeps what it is sent, the
    recording embedder and the structured log turned on; every line any logger writes is kept too. Requests come
    from one address, as the web server passes it on."""
    caplog.set_level(logging.DEBUG)
    sdk, embedder = shared.FakeSDK(shared.READING), Recording()
    side = real_side(indexed_settings, tmp_path, sdk, monkeypatch, embedder=embedder, requests_per_minute=1000)
    handler = start_logging(side.settings, side.clock)
    side.client = TestClient(create_app(side.settings, pipeline=side.pipeline, state=side.state, clock=side.clock),
                             client=(ADDRESS, 50000))
    side.app = side.client.app
    side.app.state.core.set(f"visitor_salt:{shared.DAY}", shared.SALT)
    side.sdk, side.embedder = sdk, embedder
    side.pipeline._client = side.asked = Asked(side.pipeline.client)  # the client the pipeline was made with

    def log_lines() -> list[str]:
        written = side.settings.log_dir / "briefings.jsonl"
        return [*(written.read_text().splitlines() if written.exists() else []), *caplog.text.splitlines()]

    side.log_lines = log_lines
    yield side
    logging.getLogger(LOGGER).removeHandler(handler)
    handler.close()


def everything_kept(side) -> str:
    """Every row of every table of the state database, the site's own and the shared package's."""
    con = sqlite3.connect(side.state.path)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        return json.dumps({t: [list(r) for r in con.execute(f'SELECT * FROM "{t}"')] for t in tables}, default=str)
    finally:
        con.close()


def test_nothing_about_the_reader_is_kept_but_the_days_code_and_the_question(site):
    headers = {"User-Agent": "a-browser-like-no-other", "Referer": "https://referrer-like-no-other.example/page"}
    status, text = site.client.post("/api/ask", json={"question": CANARY}, headers=headers), None
    sent = [e for e in shared.events_of(status.text) if isinstance(e, dict)]
    assert status.status_code == 200 and sent[-1]["type"] == "final" and sent[-1]["answer"]["segments"]
    del text
    core = site.app.state.core
    code = visitor_key(core, ADDRESS, site.clock)
    con = sqlite3.connect(site.state.path)
    assert {r[0] for r in con.execute("SELECT visitor FROM wts_spending")} == {code}
    con.close()
    # The package's own check: the address is in no table of its own and no log line; the day's code only beside
    # the spending; the question only where the privacy notice says a question is kept (wts-core docs/adr/0012).
    assert_no_reader_data(core, site.log_lines(), question=CANARY, address=ADDRESS, visitor=code)
    # the forms: "Was this useful?", a report and a statement. A report and a statement keep no code of who sent
    # them: the day's count is kept apart (wts_reply_counts), as the daily limit always needed, and emptied daily
    bid = sent[-1]["briefing_id"]
    assert site.client.post("/api/feedback", json={"question": CANARY, "useful": True,
                                                   "briefing_key": sent[-1]["briefing_key"]},
                            headers=headers).status_code == 200
    assert site.client.post("/api/report", json={"kind": "correction", "briefing_id": bid,
                                                 "message": "A date is wrong."}, headers=headers).status_code == 200
    assert site.client.post("/api/statements", json={
        "item": f"/b/{bid}", "name": "Hilary Benn", "role": "MP for Leeds South", "link": "",
        "text": "My view is set out in the debate of 4 March 2021.", "email": "office@example.org"},
        headers=headers).status_code == 200
    assert_no_reader_data(core, site.log_lines(), question=CANARY, address=ADDRESS, visitor=code)
    # and the site's own tables with the package's: the address, the browser and the page they came from are in
    # no table at all, and in no log line
    kept = everything_kept(site)
    for never in (ADDRESS, "a-browser-like-no-other", "referrer-like-no-other"):
        assert never not in kept and never not in "\n".join(site.log_lines()), never
    con = sqlite3.connect(site.state.path)
    assert "visitor" not in {r[1] for t in ("wts_reports", "wts_statements") for r in con.execute(
        f"PRAGMA table_info({t})")}
    con.close()


def test_a_statement_never_reaches_the_model_the_embedder_the_index_the_record_or_a_log(site):
    first = [e for e in shared.events_of(site.client.post("/api/ask", json={"question": shared.RQ}).text)
             if isinstance(e, dict)]
    beside = f"/b/{first[-1]['briefing_id']}"
    asked_before = len(site.sdk.calls)

    def ask(question):
        # the record is indexed and embedded again first, so that anything able to reach the index has its chance
        indexer = Indexer(site.settings, site.pipeline.engine.index, site.state)
        indexer.index_pending()
        embed_pending(site.settings, site.pipeline.engine.index, site.state, site.embedder)
        return [e for e in shared.events_of(site.client.post("/api/ask", json={"question": question}).text)
                if isinstance(e, dict)]

    # The package's own check (wts-core docs/adr/0008): it publishes a statement carrying a marker beside the
    # briefing, asks a question on the same subject through the site's real pipeline, and fails if the marker is
    # in any request to the model, any text given to the embedder, the index, an event sent, any table but the
    # right of reply's own, or a log line.
    assert_statements_stay_out(
        replies=site.app.state.replies, ask=ask, model=site.asked, embedder=site.embedder,
        index=site.pipeline.engine.index,  # the site's real index, read whole (``LanceIndex.everything``)
        state=site.app.state.core, log_lines=site.log_lines, item=beside,
        question="What has been said about trial by jury in the Commons?")
    assert len(site.sdk.calls) == asked_before + 1  # the question was written afresh, by the model
    assert any("trial by jury" in text.lower() for text in site.embedder.texts)  # and searched for by meaning
    # the site's own tables hold nothing of it either
    con = sqlite3.connect(site.state.path)
    own = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'wts_%'")]
    assert "wtsmarker" not in json.dumps([[list(r) for r in con.execute(f'SELECT * FROM "{t}"')] for t in own],
                                         default=str)
    con.close()
    # and yet the statement is shown, beside the briefing; never its sender's address
    shown = site.client.get("/api/statements", params={"path": beside}).json()["statements"]
    assert len(shown) == 1 and "wtsmarker" in shown[0]["text"] and "@example.org" not in str(shown)
    # the briefing's own page, as a reader without scripts or a search engine reads it, has no statement in it
    assert "wtsmarker" not in site.client.get(beside).text


def test_once_its_day_is_over_no_briefing_is_tied_to_the_account_or_the_visitor_that_asked(site):
    """The one site with signed-in accounts: wts-core 0.3.0's check of what it promises of them (its
    ``testing.assert_briefings_tied_to_no_one``; docs/adr/0012, "What is kept about spending, and what can be
    promised"). A member of the public and a signed-in account each have a briefing written; once the day is over
    (the next day's first model call, or the nightly tidy) the spending rows keep the day and no time, no
    visitor's code and no account, the account's calls and cost are one line for the month, to the cent, and the
    attack the check makes on what is kept gives no briefing to anyone. (The check needs another asker beside the account: alone in its month, an account's
    total is made up of its own briefings and nothing else, which the privacy notice does not promise against.)"""
    from wts_core.testing import assert_briefings_tied_to_no_one
    from wts_core.usage import forget_earlier_days

    from hansard.api.auth import create_user

    public = site.client.post("/api/ask", json={"question": CANARY})
    assert [e for e in shared.events_of(public.text) if isinstance(e, dict)][-1]["type"] == "final"
    create_user(site.state, "researcher@example.org", "correct horse battery")
    member = TestClient(site.app, client=("198.51.100.9", 50000))
    assert member.post("/api/auth/login", json={"email": "researcher@example.org",
                                                "password": "correct horse battery"}).status_code == 200
    signed = member.post("/api/ask", json={"question": shared.RQ})
    assert [e for e in shared.events_of(signed.text) if isinstance(e, dict)][-1]["type"] == "final"
    core = site.app.state.core
    con = sqlite3.connect(site.state.path)
    assert con.execute("SELECT count(*) FROM wts_spending WHERE account IS NOT NULL").fetchone()[0] >= 1
    site.clock.advance(days=1)
    forget_earlier_days(core, clock=site.clock)
    assert con.execute("SELECT count(*) FROM wts_spending WHERE account IS NOT NULL OR visitor != '' OR "
                       "substr(at, 11) != 'T00:00:00+00:00'").fetchone()[0] == 0
    (calls, cost), = con.execute("SELECT calls, cost_usd FROM wts_account_months").fetchall()
    assert calls >= 1 and round(cost, 2) == cost
    con.close()
    assert_briefings_tied_to_no_one(core, clock=site.clock)


def test_the_privacy_notice_says_what_the_site_now_keeps():
    """The sentences the owner accepted with the shared package's design, word for word (wts-core docs/adr/0012 and
    docs/adr/0008, questions 3 and 4), and the figures in them are the ones the code keeps to."""
    from wts_core.observe import OWNER_ACCEPTED

    from hansard.replies import REPLY_RULES

    def words(name: str) -> str:
        return " ".join(re.sub(r"<[^>]+>", " ", (REPO_ROOT / "web" / name).read_text()).split())

    page = words("privacy.html")
    assert ("For each briefing the site also notes how long each step took, what it cost and what the automatic "
            "checks found, so that slow or poorly checked briefings can be found and fixed. These notes hold "
            "nothing that identifies you; they are kept for 13 months, and the question of a briefing that could "
            "not be written is removed from them after 90 days.") in page
    assert (OWNER_ACCEPTED.months, OWNER_ACCEPTED.failed_question_days) == (13, 90)
    kept = "waiting or shown, and deleted three months after it is withdrawn, declined or removed"
    assert kept in page and kept in words("named.html")
    assert REPLY_RULES.email_kept_months == 3
    assert ("Backups of the site's database are kept for up to two months, so a deleted address can remain in a "
            "backup until then.") in page
    assert BACKUPS.keep_offsite == 60  # one a night: about two months
    assert "requests are not logged" in page  # and the structured log holds no request: stages and times only
    # what wts-core 0.3.0 keeps of a day gone by, and of an account (its docs/adr/0012, "What is kept about
    # spending, and what can be promised"): the test above makes the attack on it
    assert ("The code is kept with what each of your briefings cost for that day only; after the day, that record "
            "keeps the day and the cost, and no code and no time of day.") in page
    assert ("After the day, the site keeps, for each account, one line a calendar month: how many questions it "
            "asked, what they cost to the cent, and how many visitor codes it was seen with.") in page
    assert "If the site were ever run invite-only, so that everyone who asks has signed in" in page
