"""Web API: public access with caps and cache, optional sign-in, streaming answers, search."""

from __future__ import annotations

import asyncio
import dataclasses
import html
import json

import pytest
from fastapi.testclient import TestClient
from wts_core.adapters.hashing import HashingEmbedder

from hansard.api.app import create_app
from hansard.api.auth import create_user
from hansard.engine import Engine
from hansard.rag.pipeline import Pipeline
from hansard.store.state import StateDB

from .conftest import model_of
from .test_rag import TODAY, FakeClient


def make_app(indexed_settings, tmp_path, *, auth: bool, **usage):
    # each test gets its own state DB (users, usage, cache) on top of the shared index
    s = dataclasses.replace(indexed_settings, raw={
        **indexed_settings.raw,
        "auth": {"required": auth},
        "usage": {**indexed_settings.raw["usage"], "per_user_daily_questions": 2, "per_visitor_daily_questions": 2,
                  **usage}})
    client = FakeClient()
    pipeline = Pipeline(s, Engine(s, embedder=HashingEmbedder(64), today=TODAY), client=model_of(client),
                        today=TODAY)
    state = StateDB(tmp_path / "state.sqlite")
    return create_app(s, pipeline, state), state, client


@pytest.fixture()
def api(indexed_settings, tmp_path):
    """Invite-only mode."""
    app, state, _ = make_app(indexed_settings, tmp_path, auth=True, answer_cache_hours=0)
    create_user(state, "researcher@example.org", "correct horse battery")
    create_user(state, "admin@example.org", "admin password 123", admin=True)
    return TestClient(app), state


@pytest.fixture()
def public(indexed_settings, tmp_path):
    """The default: open to everyone."""
    app, state, client = make_app(indexed_settings, tmp_path, auth=False)
    return TestClient(app), state, client


def login(client, email="researcher@example.org", password="correct horse battery"):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r


def spend(app, kind, model, usage, visitor=None):
    """One model call in the spending record, as the flow of a briefing records it."""
    return app.state.parts.spending.add(kind, model, usage, visitor=visitor)


def answers_recorded(state, column="count(*)"):
    with state.connect() as con:
        return con.execute(f"SELECT {column} FROM wts_spending WHERE kind = 'answer'").fetchone()[0]


def events(resp):
    out = []
    for chunk in resp.text.split("\n\n"):
        for line in chunk.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
    return out


def test_requires_sign_in(api):
    client, _ = api
    assert client.get("/api/meta").status_code == 401
    assert client.post("/api/ask", json={"question": "trial by jury"}).status_code == 401
    assert client.get("/api/health").status_code == 200


def test_login_logout_and_bad_password_throttle(api):
    client, _ = api
    for _ in range(5):
        assert client.post("/api/auth/login", json={"email": "researcher@example.org", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "researcher@example.org", "password": "nope"}).status_code == 429
    client2 = TestClient(client.app)
    client2.headers["X-Forwarded-For"] = "x"
    # the throttle is keyed on client + email; a different email still works
    login(client2, "admin@example.org", "admin password 123")
    assert client2.get("/api/me").json()["email"] == "admin@example.org"
    client2.post("/api/auth/logout")
    assert client2.get("/api/me").status_code == 401


def test_meta_lists_filters_and_attribution(api):
    client, _ = api
    login(client)
    m = client.get("/api/meta").json()
    assert "North East" in m["regions"] and "transport" in m["portfolios"]
    assert any("Open Parliament Licence" in a["text"] for a in m["attribution"])
    assert any("Open Government Licence" in a["text"] for a in m["attribution"])
    assert any("mySociety" in a["text"] for a in m["attribution"])


def test_ask_streams_plan_sources_text_and_verified_answer(api):
    client, state = api
    login(client)
    r = client.post("/api/ask", json={"question": "What has been said about trial by jury?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    evs = events(r)
    types = [e["type"] for e in evs]
    assert evs[0]["type"] == "progress" and evs[0]["step"] == "reading"  # sent before the question is read
    assert types[1] == "plan" and types[2] == "sources" and "text" in types and types[-1] == "final"
    assert types[-2] == "progress"  # checking the citations, after the writing
    final = evs[-1]["answer"]
    assert "[Source 1]" in final["markdown"] and final["citations_invalid"] == 0
    assert answers_recorded(state) == 1


def sources_but_no_briefing(evs, word):
    """Over a limit, readers still get the (free) sources, with a note saying why there is no briefing."""
    final = evs[-1]
    return (final["type"] == "final" and final["answer"] is None and word in final["note"]
            and any(e["type"] == "sources" and e["sources"] for e in evs))


def test_sources_only_needs_no_model(public):
    client, state, fake = public
    before = len(fake.calls)
    evs = events(client.post("/api/ask", json={"question": "trial by jury", "sources_only": True}))
    assert sources_but_no_briefing(evs, "Sources only") and len(fake.calls) == before


def test_daily_question_cap(api):
    client, _ = api
    login(client)
    for _ in range(2):
        assert events(client.post("/api/ask", json={"question": "trial by jury"}))[-1]["type"] == "final"
    last = events(client.post("/api/ask", json={"question": "trial by jury"}))
    assert sources_but_no_briefing(last, "limit")


def test_search_endpoint_needs_no_model(api):
    client, _ = api
    login(client)
    r = client.post("/api/search", json={"question": "Northern Ireland security", "scope": {"written": False}})
    body = r.json()
    assert body["sources"] and all(s["kind"] == "record" for s in body["sources"])
    assert all("written" not in s["chamber"].lower() for s in body["sources"])
    assert "keyword;dur=" in r.headers["server-timing"] and "timings" not in body


def test_admin_usage_is_admin_only(api):
    client, state = api
    login(client)
    assert client.get("/api/admin/usage").status_code == 403
    admin = TestClient(client.app)
    login(admin, "admin@example.org", "admin password 123")
    spend(client.app, "answer", "claude-opus-5-5", {"input_tokens": 10_000, "output_tokens": 1_000})
    assert admin.get("/api/admin/usage").json()["total_usd"] > 0


def prices(**usage):
    from wts_core.usage import PriceTable

    return PriceTable.from_settings({"price_input_per_mtok": 4.0, "price_output_per_mtok": 20.0,
                                     "price_cache_read_per_mtok": 0.2, "cache_write_multiplier": 1.25, **usage})


def cost_usd(usage, table=None, model=None):
    """What a call costs by the site's prices, from token counts in either of the forms the site has stored."""
    from wts_core.ports import Usage

    return (table or prices()).cost(Usage.from_mapping(usage), model)


def test_cost_calculation():
    assert cost_usd({"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_read_tokens": 0}) == \
        pytest.approx(6.0)


# ---- public mode (the default) -------------------------------------------------------------------


def test_public_site_needs_no_sign_in(public):
    client, state, _ = public
    assert client.get("/api/me").json() == {"email": None, "is_admin": False, "auth_required": False}
    assert client.get("/api/meta").status_code == 200
    evs = events(client.post("/api/ask", json={"question": "What has been said about trial by jury?"}))
    assert evs[-1]["type"] == "final" and evs[-1]["answer"]["citations_total"] == 1
    visitor = answers_recorded(state, "visitor")
    assert visitor and "testclient" not in visitor  # a salted hash, never the address


def test_repeat_questions_come_from_the_cache_and_cost_nothing(public):
    client, state, model = public
    first = events(client.post("/api/ask", json={"question": "What has been said about trial by jury?"}))
    again = events(client.post("/api/ask", json={"question": "what has been said about  trial by jury?"}))
    assert len(model.calls) == 1
    assert again[-1]["cached"] is True and again[-1]["answer"] == first[-1]["answer"]
    assert [e["type"] for e in again] == ["plan", "sources", "text", "final"]
    other = events(client.post("/api/ask", json={"question": "What has been said about trial by jury?",
                                                 "mode": "comprehensive"}))
    assert len(model.calls) == 2 and "cached" not in other[-1]
    assert answers_recorded(state) == 2


def test_each_visitor_has_a_daily_limit_but_cached_answers_still_load(public):
    client, _, _ = public
    for q in ("trial by jury", "jury trials and courts"):
        assert events(client.post("/api/ask", json={"question": q}))[-1]["type"] == "final"
    blocked = events(client.post("/api/ask", json={"question": "security in Northern Ireland"}))
    assert sources_but_no_briefing(blocked, "limit")
    assert events(client.post("/api/ask", json={"question": "trial by jury"}))[-1]["type"] == "final"
    other = TestClient(client.app, client=("203.0.113.9", 50000))
    assert events(other.post("/api/ask", json={"question": "security in Northern Ireland"}))[-1]["type"] == "final"


def test_site_wide_daily_budget(public):
    client, state, _ = public
    spend(client.app, "answer", "claude-opus-5-5", {"input_tokens": 3_000_000})
    evs = events(client.post("/api/ask", json={"question": "trial by jury"}))
    assert sources_but_no_briefing(evs, "budget")


def test_requests_per_minute_limit(indexed_settings, tmp_path):
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False, requests_per_minute=3)
    client = TestClient(app)
    codes = [client.post("/api/search", json={"question": "trial by jury"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_admin_endpoints_need_the_admin_token(indexed_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("HANSARD_ADMIN_TOKEN", "s3cret-token")
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    assert client.get("/api/admin/usage").status_code == 403
    assert client.get("/api/admin/usage", headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert client.get("/api/admin/sync", headers={"Authorization": "Bearer s3cret-token"}).status_code == 200


def test_feedback_refers_to_the_briefing_and_shows_in_admin_usage(indexed_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("HANSARD_ADMIN_TOKEN", "s3cret-token")
    app, state, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    final = events(client.post("/api/ask", json={"question": "trial by jury"}))[-1]
    key = final["briefing_key"]
    assert key and client.post("/api/ask", json={"question": "trial by jury"}).text.count(key) == 1  # replay too
    assert client.post("/api/feedback", json={"question": "trial by jury", "useful": False,
                                              "briefing_key": key}).json() == {"ok": True}
    usage = client.get("/api/admin/usage", headers={"Authorization": "Bearer s3cret-token"}).json()
    assert usage["visits"]["feedback"]["total"] == 1
    assert usage["visits"]["feedback"]["recent_not_useful"][0]["question"] == "trial by jury"


def test_visit_counts_keep_daily_totals_only(public):
    client, state, _ = public
    for path in ("/", "/", "/methodology"):
        assert client.post("/api/hit", json={"path": path, "referrer": "https://news.example.org/story"}).status_code == 200
    with state.connect() as con:
        assert con.execute("SELECT sum(views) FROM page_views").fetchone()[0] == 3
        assert tuple(con.execute("SELECT host, views FROM referrers").fetchone()) == ("news.example.org", 3)
        assert con.execute("SELECT count(*) FROM day_visitors").fetchone()[0] == 1  # one visitor, hashed
        con.execute("UPDATE day_visitors SET day = '2026-01-01'")  # yesterday's hashes...
    client.post("/api/hit", json={"path": "/"})
    with state.connect() as con:  # ...are rolled up into a count and deleted
        assert con.execute("SELECT visitors FROM day_totals WHERE day = '2026-01-01'").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM day_visitors WHERE day = '2026-01-01'").fetchone()[0] == 0


def test_what_visitors_did_is_counted_once_a_day_each(public):
    from hansard.api.usage import task_summary

    client, state, _ = public
    client.post("/api/hit", json={"path": "/"})
    for _ in range(2):  # opening two sources counts the visitor once
        assert client.post("/api/hit", json={"path": "/b/x", "event": "source"}).status_code == 200
    assert client.post("/api/hit", json={"path": "/", "event": "something-else"}).status_code == 422
    with state.connect() as con:
        assert con.execute("SELECT count(*) FROM day_events WHERE event = 'source'").fetchone()[0] == 1
        assert con.execute("SELECT sum(views) FROM page_views").fetchone()[0] == 1  # an event is not a page view
        con.execute("UPDATE day_events SET day = '2026-01-01'")
        con.execute("UPDATE day_visitors SET day = '2026-01-01'")
    client.post("/api/hit", json={"path": "/b/x", "event": "source"})  # rolls up yesterday's
    with state.connect() as con:
        assert con.execute("SELECT visitors FROM event_totals WHERE day = '2026-01-01'").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM day_events WHERE day = '2026-01-01'").fetchone()[0] == 0
    t = task_summary(state)
    assert t["source"]["visitors"] == 1 and t["ask"]["visitors"] == 0 and t["useful"]["rated"] == 0


def test_public_status(public):
    client, state, _ = public
    st = client.get("/api/status").json()
    assert st["briefings"] == "available" and st["archive_start"]
    spend(client.app, "answer", "claude-opus-5-5", {"input_tokens": 3_000_000})
    st = client.get("/api/status").json()
    assert st["briefings"] == "paused" and "budget" in st["briefings_note"]


def test_static_pages_are_served_at_clean_addresses(indexed_settings, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("app")
    (dist / "methodology.html").write_text("method")
    (dist / "robots.txt").write_text("robots")
    s = dataclasses.replace(indexed_settings, raw={**indexed_settings.raw,
                                                   "paths": {**indexed_settings.raw.get("paths", {}), "web_dist": str(dist)}})
    app, _, _ = make_app(s, tmp_path, auth=False)
    client = TestClient(app)
    assert client.get("/methodology").text == "method"
    assert client.get("/robots.txt").text == "robots"
    assert client.get("/").text == "app"
    missing = client.get("/no/such/page")  # not a page: a real 404, so search engines do not index it
    assert missing.status_code == 404 and missing.text == "app"  # (no 404.html in this build: the app)
    (dist / "404.html").write_text("not found")
    assert client.get("/no-such-page").text == "not found" and client.get("/404").status_code == 404
    assert client.get("/api/nope").status_code == 404
    head = client.head("/methodology")  # link checkers and search engines ask with HEAD
    assert head.status_code == 200 and head.text == ""


def test_a_head_request_is_still_one_when_its_answer_is_sent(indexed_settings, tmp_path):
    """The web server (uvicorn) reads the request's own scope as the answer starts, to know whether a body follows.
    The site answers HEAD by asking for the page as GET; if the scope still said GET then, the server expected the
    page's body, got none, and logged "Response content shorter than Content-Length" for every HEAD (found on the
    server, 16 November 2026). The test client does not look at the scope, so this asks the app as the server does."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("app")
    (dist / "methodology.html").write_text("method")
    s = dataclasses.replace(indexed_settings, raw={**indexed_settings.raw,
                                                   "paths": {**indexed_settings.raw.get("paths", {}), "web_dist": str(dist)}})
    app, _, _ = make_app(s, tmp_path, auth=False)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "HEAD", "scheme": "http",
             "path": "/methodology", "raw_path": b"/methodology", "root_path": "", "query_string": b"",
             "headers": [(b"host", b"testserver")], "client": ("192.0.2.1", 50000), "server": ("testserver", 80)}
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append((message["type"], scope["method"], message))

    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m[0] == "http.response.start")
    assert start[1] == "HEAD"  # what the server sees as the answer starts
    assert start[2]["status"] == 200
    assert dict(start[2]["headers"])[b"content-length"] == b"6"  # the GET's length, as HEAD should say
    assert b"".join(m[2].get("body", b"") for m in sent if m[0] == "http.response.body") == b""


def test_public_endpoints_show_only_when_and_whether_updates_ran(public):
    client, state, _ = public
    run = state.start_run("sync")
    state.finish_run(run, {"reference": {"parlparse_sha": "abc123"}})
    for body in (client.get("/api/status").json()["last_update"], client.get("/api/meta").json()["last_sync"]):
        assert set(body) == {"finished_at", "status"} and body["status"] == "ok"


def _app_with_dist(indexed_settings, tmp_path, monkeypatch=None):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><html><head><title>Where MPs Stand</title>'
        '<meta property="og:title" content="Where MPs Stand" />'
        '<meta property="og:image" content="https://wherempsstand.com/family/card.png">'
        '<link rel="canonical" href="https://wherempsstand.com/" /></head><body>app</body></html>')
    s = dataclasses.replace(indexed_settings, raw={
        **indexed_settings.raw, "paths": {**indexed_settings.raw.get("paths", {}), "web_dist": str(dist)}})
    app, state, _ = make_app(s, tmp_path, auth=False)
    return TestClient(app), state


def test_every_briefing_gets_a_permanent_shareable_address(indexed_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("HANSARD_ADMIN_TOKEN", "s3cret-token")
    client, _ = _app_with_dist(indexed_settings, tmp_path)
    final = events(client.post("/api/ask", json={"question": "What has been said about trial by jury?"}))[-1]
    bid = final["briefing_id"]
    assert len(bid) == 10 and final["prepared_at"]
    # a repeat question replays the same briefing, at the same address
    again = events(client.post("/api/ask", json={"question": "What has been said about trial by jury?"}))[-1]
    assert again["cached"] and again["briefing_id"] == bid

    view = client.get(f"/api/briefings/{bid}").json()
    assert view["question"] == "What has been said about trial by jury?"
    assert view["answer"]["citations_total"] == 1 and view["sources"] and view["plan"]
    assert "usage" not in json.dumps(view) and "briefing_key" not in view

    page = client.get(f"/b/{bid}")
    assert page.status_code == 200 and "<body>app</body>" in page.text
    assert '<meta property="og:title" content="What has been said about trial by jury? | Where MPs Stand" />' in page.text
    # shared links show the site's family card; the briefing's own card is still there for embedding
    assert '/family/card.png" />' in page.text and 'content="noindex"' in page.text
    assert page.text.count("<title>") == 1 and page.text.count('property="og:image"') == 1  # site-wide tags replaced

    card = client.get(f"/b/{bid}/card.png")
    assert card.status_code == 200 and card.headers["content-type"] == "image/png"
    from io import BytesIO

    from PIL import Image
    assert Image.open(BytesIO(card.content)).size == (1200, 630)

    # withdrawing a briefing takes down the page, the data and the card
    admin = {"Authorization": "Bearer s3cret-token"}
    assert client.post(f"/api/admin/briefings/{bid}/withdraw", json={"reason": "complaint"}).status_code == 403
    assert client.post(f"/api/admin/briefings/{bid}/withdraw", json={"reason": "complaint"}, headers=admin).json()
    assert client.get(f"/api/briefings/{bid}").status_code == 410
    assert client.get(f"/b/{bid}").status_code == 410
    assert client.get(f"/b/{bid}/card.png").status_code == 410
    listed = client.get("/api/admin/briefings", headers=admin).json()["briefings"]
    assert listed[0]["id"] == bid and listed[0]["withdrawn_reason"] == "complaint"


def test_unknown_or_malformed_briefing_addresses(indexed_settings, tmp_path):
    client, _ = _app_with_dist(indexed_settings, tmp_path)
    assert client.get("/api/briefings/abcdefghij").status_code == 404
    assert client.get("/b/abcdefghij").status_code == 404
    assert client.get("/b/ZZZ'%3Cscript%3E").status_code == 404


def test_preview_text_starts_with_the_summary():
    from hansard.api.briefings import summary_text

    md = "# Onshore wind: city and rural MPs\n\n**Headline:** In the records retrieved, rural MPs objected [Source 3].\n\n**Ministers - supportive**"
    assert summary_text({"markdown": md}).startswith("Rural MPs objected.")  # no title, no remark about the records
    titled = "**Cost of living: positions in Parliament, 2025 to 2026**\n\n**In brief**\n- In the records retrieved, ministers spoke."
    assert summary_text({"markdown": titled}) == "Ministers spoke."
    assert summary_text({"markdown": "Headline\n\n" + "word " * 100}, limit=40).endswith("…")


def test_reports_are_private_until_closed_with_a_public_note(indexed_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("HANSARD_ADMIN_TOKEN", "s3cret-token")
    app, state, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    admin = {"Authorization": "Bearer s3cret-token"}
    final = events(client.post("/api/ask", json={"question": "What has been said about trial by jury?"}))[-1]
    bid = final["briefing_id"]

    ok = client.post("/api/report", json={"kind": "correction", "message": "The second point misquotes the minister.",
                                           "briefing_id": bid, "contact": "reader@example.org"})
    assert ok.status_code == 200
    assert client.post("/api/report", json={"kind": "general", "message": "too short"}).status_code == 422
    # a spam bot fills in the hidden field: thanked, but nothing is kept
    assert client.post("/api/report", json={"kind": "general", "message": "Buy cheap watches now!!",
                                            "website": "http://spam.example"}).status_code == 200

    assert client.get("/api/admin/reports").status_code == 403
    reports = client.get("/api/admin/reports?status=open", headers=admin).json()["reports"]
    assert len(reports) == 1 and reports[0]["briefing_id"] == bid and reports[0]["contact"] == "reader@example.org"
    assert client.get("/api/corrections").json()["corrections"] == []

    rid = reports[0]["id"]
    closed = client.post(f"/api/admin/reports/{rid}/close", headers=admin,
                         json={"status": "resolved", "resolution": "Checked; quote was wrong.",
                               "public_note": "A quotation in a briefing on jury trials was corrected."})
    assert closed.status_code == 200
    after = client.get("/api/admin/reports", headers=admin).json()["reports"][0]
    assert after["status"] == "resolved" and after["contact"] is None  # contact details deleted on closing

    client.post(f"/api/admin/briefings/{bid}/withdraw", headers=admin, json={"reason": "Misquoted a minister."})
    log = client.get("/api/corrections").json()["corrections"]
    assert {c["kind"] for c in log} == {"withdrawn", "correction"}
    assert "Checked" not in json.dumps(log)  # the private resolution never appears publicly
    withdrawn = next(c for c in log if c["kind"] == "withdrawn")
    assert withdrawn["note"] == "Misquoted a minister." and withdrawn["briefing_id"] == bid


def test_open_takedown_requests_are_listed_first(settings):
    from wts_core.testing import FixedClock

    from hansard.owner import replies_for

    clock = FixedClock("2026-11-01T09:00:00+00:00")
    replies = replies_for(settings, clock)
    for kind in ("takedown", "correction", "takedown", "complaint", "takedown"):
        replies.report(kind=kind, page=None, message="Please look at this.")
        clock.advance(days=1)
    replies.close(5, outcome="resolved")
    listed = [(r["kind"], r["at"][8:10]) for r in replies.reports()]
    assert listed == [("takedown", "03"), ("takedown", "01"), ("complaint", "04"), ("correction", "02")]
    # with closed reports too: the open requests first, then everything else newest first; and never cut off
    assert [r["at"][8:10] for r in replies.reports(open_only=False)] == ["03", "01", "05", "04", "02"]
    replies.close(replies.reports()[0]["id"], outcome="resolved", note="Name removed.")
    assert [r["at"][8:10] for r in replies.reports(open_only=False)] == ["01", "05", "04", "03", "02"]


def test_reports_are_limited_per_visitor(indexed_settings, tmp_path):
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False, requests_per_minute=100)
    client = TestClient(app)
    body = {"kind": "general", "message": "A perfectly reasonable message."}
    codes = [client.post("/api/report", json=body).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429


def test_donate_link_is_hidden_until_set(indexed_settings, tmp_path, monkeypatch):
    monkeypatch.delenv("HANSARD_DONATE_URL", raising=False)
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    assert TestClient(app).get("/api/site").json() == {"donate_url": "https://wheretheystand.uk/donate"}
    unset = dataclasses.replace(indexed_settings, raw={**indexed_settings.raw, "site": {"donate_url": ""}})
    app, _, _ = make_app(unset, tmp_path, auth=False)
    client = TestClient(app)
    assert client.get("/api/site").json() == {"donate_url": None}
    monkeypatch.setenv("HANSARD_DONATE_URL", "https://ko-fi.com/example")
    assert client.get("/api/site").json() == {"donate_url": "https://ko-fi.com/example"}
    assert client.get("/api/meta").json()["donate_url"] == "https://ko-fi.com/example"
    for bad in ("http://ko-fi.com/x", "javascript:alert(1)", 'https://x.example/"><script>'):
        monkeypatch.setenv("HANSARD_DONATE_URL", bad)
        assert client.get("/api/site").json() == {"donate_url": None}


def test_visitor_codes_change_daily_and_old_secrets_are_deleted(settings):
    from wts_core.testing import FixedClock

    from hansard.api.usage import visitor_key
    from hansard.store.wts import open_core

    clock = FixedClock("2026-10-12T10:00:00+00:00")
    core = open_core(settings, clock)
    core.set("visitor_salt", "an-old-permanent-salt")
    monday = visitor_key(core, "203.0.113.5", clock)
    assert visitor_key(core, "203.0.113.5", clock) == monday
    assert visitor_key(core, "198.51.100.7", clock) != monday
    clock.advance(days=1)
    tuesday = visitor_key(core, "203.0.113.5", clock)
    assert tuesday != monday
    with core.connect() as con:
        keys = [r[0] for r in con.execute("SELECT key FROM wts_kv WHERE key LIKE 'visitor_salt%'")]
    assert keys == ["visitor_salt:2026-10-13"]


def test_failures_and_spending_show_as_alerts(settings):
    import dataclasses

    from hansard.api.usage import alerts, desk, recent_failures
    from hansard.store.wts import open_core

    capped = dataclasses.replace(settings, raw={**settings.raw, "usage": {
        **settings.usage, "daily_spend_cap_usd": 10, "monthly_spend_cap_usd": 150}})
    parts = desk(capped, open_core(capped))
    state = StateDB(capped.state_db)
    assert alerts(parts, state) == []
    parts.failures.note("question reading", "Q?", "AuthenticationError: invalid x-api-key")
    a = alerts(parts, state)
    assert len(a) == 1 and "1 briefing failed" in a[0]["text"] and "invalid x-api-key" in a[0]["text"]
    assert recent_failures(parts.failures)[0]["kind"] == "question reading"
    parts.spending.add("answer", "claude-opus-5-5", {"input_tokens": 2_000_000}, visitor="v")  # $8 of a $10 day
    texts = " ".join(x["text"] for x in alerts(parts, state))
    assert "80% of the $10 cap" in texts


def test_failed_briefing_is_logged_and_readers_see_plain_words(indexed_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("HANSARD_ADMIN_TOKEN", "s3cret-token")
    app, state, client = make_app(indexed_settings, tmp_path, auth=False)

    def broken(*a, **k):
        raise RuntimeError("upstream is down")

    monkeypatch.setattr(client.messages, "stream", broken)
    monkeypatch.setattr(client.beta.messages, "stream", broken)
    tc = TestClient(app)
    evs = events(tc.post("/api/ask", json={"question": "What has been said about trial by jury?"}))
    err = evs[-1]
    assert err["type"] == "error" and "could not be written" in err["message"] and "RuntimeError" not in err["message"]
    usage = tc.get("/api/admin/usage", headers={"Authorization": "Bearer s3cret-token"}).json()
    assert usage["failures"][0]["error"].startswith("RuntimeError") and usage["alerts"]
    assert usage["popular_questions"][0] == {"question": "what has been said about trial by jury?", "times": 1,
                                              "last": usage["popular_questions"][0]["last"]}


def test_cache_writes_and_reads_are_priced():
    assert cost_usd({"input_tokens": 1_000_000, "cache_write_tokens": 1_000_000}) == 5.0
    assert cost_usd({"input_tokens": 0, "cache_read_tokens": 1_000_000}) == 0.2


def test_raw_sdk_token_names_are_priced_and_cache_writes_recorded(settings):
    from wts_core.ports import Usage

    from hansard.api.usage import desk
    from hansard.store.wts import open_core

    # the SDK's own names: its input_tokens leaves out the cache writes and reads
    raw = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_creation_input_tokens": 1_000_000,
           "cache_read_input_tokens": 1_000_000}
    assert cost_usd(raw) == pytest.approx(4.0 + 2.0 + 5.0 + 0.2)
    # the same call in the site's own form costs the same
    assert cost_usd(Usage.from_mapping(raw).as_stored()) == cost_usd(raw)
    # counts that are missing, None or not whole numbers are coped with; this site's name for cache reads comes
    # first when both are given, even at 0 (as Where Countries Stand's copy)

    def token_counts(usage):
        return Usage.from_mapping(usage).as_stored()

    assert cost_usd({"input_tokens": 1000, "cache_read_input_tokens": None}) == pytest.approx(0.004)
    assert token_counts({"input_tokens": 1000.0, "output_tokens": "20", "cache_read_tokens": 0,
                         "cache_read_input_tokens": 50}) == {"input_tokens": 1000, "output_tokens": 20,
                                                             "cache_read_tokens": 0, "cache_write_tokens": 0}
    assert all(type(v) is int for v in token_counts({"input_tokens": 7.0, "cache_write_tokens": 2.0}).values())

    core = open_core(settings)
    spending = desk(settings, core).spending
    spending.add("answer", "claude-opus-5-5", raw)
    spending.add("answer", "claude-opus-5-5", Usage.from_mapping(raw).as_stored())
    with core.connect() as con:
        rows = [tuple(r) for r in con.execute(
            "SELECT input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd FROM wts_spending")]
    assert rows == [(2_000_000, 100_000, 1_000_000, 1_000_000, 11.2)] * 2


def test_support_reminder_every_n_new_briefings(indexed_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("HANSARD_DONATE_URL", "https://ko-fi.com/example")
    app, state, _ = make_app(indexed_settings, tmp_path, auth=False, support_reminder_every=2,
                             per_visitor_daily_questions=10)
    client = TestClient(app)

    def final(q):
        return [e for e in events(client.post("/api/ask", json={"question": q})) if e["type"] == "final"][-1]

    assert "support_reminder" not in final("trial by jury")
    second = final("onshore wind farms")
    assert second["support_reminder"] == 2  # every second new briefing, with the count so far
    assert "support_reminder" not in final("trial by jury")  # an earlier briefing again: no new cost, no reminder
    # the shared copy of a briefing never carries the reminder
    assert "support_reminder" not in client.get(f"/api/briefings/{second['briefing_id']}").text
    assert client.post("/api/support-clicked").status_code == 200
    final("rail fares")
    assert "support_reminder" not in final("prison places")  # went to donate: no more reminders today


def test_pages_carry_readable_text_for_readers_without_scripts():
    from hansard.api.plaintext import briefing_text, markdown_text, with_text

    page = '<body><div id="person-root"><p class="muted">Fetching the member page.</p></div></body>'
    assert with_text(page, "<h1>Hilary Benn</h1>") == '<body><div id="person-root"><h1>Hilary Benn</h1></div></body>'
    assert 'class="thread prose"><p>x</p>' in with_text('<div id="root"></div>', "<p>x</p>")
    assert with_text(page, None) == page
    md = markdown_text("**In brief**\n- Ministers said <b>so</b> [Source 1]\n\nAbout the evidence: two records.")
    assert "<strong>In brief</strong>" in md and "<li>Ministers said &lt;b&gt;so&lt;/b&gt;" in md
    text = briefing_text({"question": "Q?", "created_at": "2026-10-01T10:00:00", "records_up_to": "2026-09-30",
                          "answer": {"markdown": "Body."},
                          "sources": [{"n": 1, "title": "A <speech>", "official_url": "https://x.example/1"}]})
    assert "<h1>Q?</h1>" in text and "A &lt;speech&gt;" in text and 'href="https://x.example/1"' in text


def test_oversized_follow_up_history_and_plans_are_bounded(public):
    from hansard.api.app import AskBody

    body = AskBody.model_validate({"question": "trial by jury", "history": [
        {"question": "x" * 600_000, "answer": "y" * 600_000, "topic": "z" * 9_000, "scope": "w" * 9_000}]})
    h = body.history_dicts()[0]
    assert (len(h["question"]), len(h["answer"]), len(h["topic"]), len(h["scope"])) == (2000, 1000, 500, 1000)
    client, _, _ = public
    huge = {"search_text": "jury", "filters": {"person_ids": [str(i) for i in range(100_000)]}}
    assert client.post("/api/search", json={"question": "trial by jury", "plan": huge}).status_code == 422
    from hansard.rag.query_parse import QueryPlan

    plan = QueryPlan.from_dict({"search_text": "a" * 50_000, "filters": {"parties": ["x"] * 500}})
    assert len(plan.search_text) == 1000 and len(plan.filters.parties) == 50


def test_briefings_in_progress_count_against_the_caps():
    from wts_core.usage import BriefingsInProgress, busy_refusal

    from hansard.messages import MESSAGES

    f = BriefingsInProgress(per_key=2, total=3, estimate_usd=0.5)

    def start(key):
        busy = f.start(key)
        return busy_refusal(busy, MESSAGES).message if busy else None

    assert start("a") is None and start("a") is None
    assert "already being written" in start("a")
    assert start("b") is None and "Many briefings" in start("c")
    assert f.pending_usd() == 1.5 and f.pending_usd(besides=1) == 1.0
    assert set(f.running) == {"a", "b"}  # the refused visitor left nothing behind
    f.finish("a")
    assert start("c") is None
    for key in ("a", "b", "c"):
        f.finish(key)
    assert f.running == {} and f.pending_usd() == 0


def test_the_settings_file_has_every_figure_the_flow_of_a_briefing_needs(indexed_settings):
    """The limits on briefings in progress, their estimated cost and the cache's life are written in
    config/settings.toml: the code has no fallback for them, and the site will not start without them."""
    import dataclasses

    from wts_core.briefing import desk_settings
    from wts_core.settings import SettingsError

    from hansard.messages import MESSAGES

    u = indexed_settings.raw["usage"]
    assert (u["briefings_at_once_per_reader"], u["briefings_at_once"], u["briefing_estimate_usd"]) == (2, 8, 0.15)
    assert u["answer_cache_hours"] == 168
    read = desk_settings(indexed_settings, MESSAGES, at_once_per_visitor="briefings_at_once_per_reader",
                         forget_over=50_000)
    assert (read.at_once_per_visitor, read.at_once, read.estimate_usd, read.cache_hours) == (2, 8, 0.15, 168.0)
    without = dataclasses.replace(indexed_settings, raw={**indexed_settings.raw, "usage": {
        k: v for k, v in u.items() if k not in ("briefings_at_once_per_reader", "answer_cache_hours")}})
    with pytest.raises(SettingsError, match="briefings_at_once_per_reader") as stopped:
        desk_settings(without, MESSAGES, at_once_per_visitor="briefings_at_once_per_reader", forget_over=50_000)
    assert "answer_cache_hours" in str(stopped.value)  # everything missing is named together


def test_an_ipv6_connection_counts_as_one_visitor():
    from wts_core.usage import network_of

    assert network_of("2001:db8:1:2:aaaa::1") == network_of("2001:db8:1:2:bbbb::9") == "2001:db8:1:2::/64"
    assert network_of("2001:db8:1:3::1") != network_of("2001:db8:1:2::1")
    assert network_of("203.0.113.9") == "203.0.113.9" and network_of("::ffff:203.0.113.9") == "203.0.113.9"
    assert network_of(None) == "-"


def test_sign_in_attempts_are_throttled_per_address_whatever_email_is_tried(api):
    client, _ = api
    codes = [client.post("/api/auth/login", json={"email": f"guess{i}@example.org", "password": "wrong"}).status_code
             for i in range(21)]
    assert codes[:20] == [401] * 20 and codes[20] == 429


def test_pages_without_their_own_image_share_the_family_card():
    from pathlib import Path

    from hansard.api.briefings import SITE_CARD, SITE_CARD_ALT, page_with_meta

    head = (Path(__file__).resolve().parents[1] / "web" / "public" / "family" / "head.html").read_text()
    # the alt text is the family kit's own, word for word (escaped as the kit writes it)
    assert f'content="{html.escape(SITE_CARD_ALT)}"' in head and f'{SITE_CARD}"' in head
    page = page_with_meta('<html><head><meta property="og:image" content="x"></head></html>', title="T",
                          description="D", url="https://wherempsstand.com/bills/x")
    assert page.count('property="og:image"') == 1
    assert '<meta property="og:image" content="https://wherempsstand.com/family/card.png" />' in page


# ---- a withdrawn briefing is not played again from the answer cache ----------------------------------------------


def test_a_withdrawn_briefing_is_not_replayed_to_the_next_reader_with_the_same_question(tmp_path, monkeypatch):
    """Found by wts-core's independent review of the sites' adoptions, and true of this site's own code before
    it: a briefing the owner withdrew went on being played from the answer cache to anyone who asked the same
    question, for up to a week. Withdrawing one now drops its cached copies, and the question is written afresh."""
    import shared

    from .sides import scripted_side

    side = scripted_side(tmp_path, monkeypatch, requests_per_minute=1000)
    admin = {"Authorization": f"Bearer {shared.ADMIN_TOKEN}"}

    def ask():
        status, text = side.ask(shared.Q)
        assert status == 200
        return [e for e in shared.events_of(text) if isinstance(e, dict)][-1]

    first = ask()
    again = ask()
    assert again["briefing_id"] == first["briefing_id"] and again.get("cached") and side.calls() == ["written"]
    withdrawn = side.client.post(f"/api/admin/briefings/{first['briefing_id']}/withdraw", headers=admin,
                                 json={"reason": "It misread a speech."})
    assert withdrawn.status_code == 200
    assert side.get(f"/api/briefings/{first['briefing_id']}") == [410, {"detail": "This briefing has been withdrawn."}]
    fresh = ask()
    assert side.calls() == ["written", "written"]  # written afresh, by the model
    assert fresh["briefing_id"] != first["briefing_id"] and not fresh.get("cached") and fresh["answer"]
    assert ask()["briefing_id"] == fresh["briefing_id"]  # and that one is cached in its turn

    # a cached copy met all the same (its briefing withdrawn by other means): never played; the reader is sent
    # the sources and the words the briefing's address shows, and the copy is dropped
    side.app.state.parts.archive.withdraw(fresh["briefing_id"], "Withdrawn without the cache being told.")
    status, text = side.ask(shared.Q)
    sent = [e for e in shared.events_of(text) if isinstance(e, dict)]
    assert [e["type"] for e in sent][-2:] == ["sources", "final"] and side.calls() == ["written", "written"]
    assert sent[-1]["answer"] is None and sent[-1]["note"] == "This briefing has been withdrawn."
    assert "Hilary Benn said" not in text
    assert ask()["briefing_id"] not in (first["briefing_id"], fresh["briefing_id"])  # and the next is written afresh
    assert side.calls() == ["written", "written", "written"]


def test_the_owners_list_of_briefings_and_the_days_spending_read_as_they_always_have(tmp_path, monkeypatch):
    """Two things the independent review found changed and that are put back: of two briefings saved in the same
    second, the owner's list shows the one saved later first (it had them in order of their addresses); and a
    day with no call is a whole 0 in the admin page's figures, not 0.0."""
    import json

    import shared

    from .sides import scripted_side

    side = scripted_side(tmp_path, monkeypatch, requests_per_minute=1000)
    admin = {"Authorization": f"Bearer {shared.ADMIN_TOKEN}"}
    usage = side.client.get("/api/admin/usage", headers=admin)
    assert '"today_usd":0,' in usage.text.replace(" ", "") and usage.json()["today_calls"] == 0
    archive = side.app.state.parts.archive
    for bid in ("zzzzzzzzzz", "aaaaaaaaaa", "mmmmmmmmmm"):  # saved in this order, within one second
        archive.save(f"Asked as {bid}?", {}, shared.a_saved_briefing("q"), id=bid)
    listed = side.client.get("/api/admin/briefings", headers=admin).json()["briefings"]
    assert [b["id"] for b in listed] == ["mmmmmmmmmm", "aaaaaaaaaa", "zzzzzzzzzz"]
    assert len({b["created_at"] for b in listed}) == 1 and json.dumps(listed[0]).count("withdrawn") == 2


def test_a_reading_and_an_interests_check_that_answer_nothing_are_paid_for_and_count_towards_the_caps(
        indexed_settings, tmp_path):
    """wts-core CHANGELOG, 0.3.0: "ModelClient.structured raises ports.NoStructuredAnswer whenever an answered
    call cannot be read into the schema", and the error carries what the call cost. The small model answers both
    of its calls with nothing in them: the question is read by the built-in rules and no interests are flagged,
    as always, and each call is in the spending record once and counted by the caps."""
    from types import SimpleNamespace as NS

    from wts_core.usage import Caps

    s = dataclasses.replace(indexed_settings, raw={**indexed_settings.raw, "auth": {"required": False}})
    sdk, asked = FakeClient(), []

    def nothing(**kw):
        asked.append(kw["output_format"].__name__)
        return NS(stop_reason="end_turn", parsed_output=None,
                  usage=NS(input_tokens=400, output_tokens=30, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0))

    sdk.messages = NS(stream=sdk._stream, parse=nothing)

    class EveryoneHasLand:
        def for_person(self, pid):
            return {"categories": [{"type": "6", "name": "Land and property (within or outside the UK)", "items": [
                {"summary": "Farm land", "details": [], "registered": "10 January 2000", "children": []}]}]}

    class NoLords:
        def for_person(self, pid):
            return []

    pipeline = Pipeline(s, Engine(s, embedder=HashingEmbedder(64), today=TODAY), client=model_of(sdk), today=TODAY)
    pipeline._registers = (EveryoneHasLand(), NoLords(), None)
    app = create_app(s, pipeline, StateDB(tmp_path / "state.sqlite"))
    final = events(TestClient(app).post("/api/ask", json={"question": "What has been said about trial by jury?"}))[-1]
    assert final["type"] == "final" and final["interests"] == [] and "interests_usage" not in final
    assert asked == ["ParsedQuery", "Matches"] and len(sdk.calls) == 1
    with app.state.core.connect() as con:
        rows = {kind: (tokens_in, tokens_out, cost) for kind, tokens_in, tokens_out, cost in con.execute(
            "SELECT kind, input_tokens, output_tokens, cost_usd FROM wts_spending ORDER BY id")}
        assert con.execute("SELECT count(*) FROM wts_spending").fetchone()[0] == 3  # each call once, no more
    assert sorted(rows) == ["answer", "interests", "parse"]
    assert rows["parse"][:2] == rows["interests"][:2] == (400, 30) and rows["interests"][2] > 0
    parts = app.state.parts
    day = parts.clock.now().strftime("%Y-%m-%d")
    cost = {kind: row[2] for kind, row in rows.items()}
    assert parts.spending.spent_since(day) == pytest.approx(sum(cost.values()))

    def paused_at(cap):
        return Caps(parts.spending, dataclasses.replace(parts.limits, daily_cap_usd=cap), parts.messages,
                    parts.clock).site_paused()

    # a day's cap that the briefing and one of the two small calls would not reach is reached with the other
    assert paused_at(cost["answer"] + cost["parse"] + cost["interests"] / 2).kind == "daily_cap"
    assert paused_at(cost["answer"] + cost["interests"] + cost["parse"] / 2).kind == "daily_cap"
    assert paused_at(sum(cost.values()) * 1.01) is None
