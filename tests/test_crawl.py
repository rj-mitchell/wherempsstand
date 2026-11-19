"""Sitemaps and plain links: every page of the record can be found by a search engine, from the sitemaps and by
following links in the pages' HTML without running their scripts (an audit of 19 November 2026 found debates and
weeks in no sitemap, former members left out, no dates, and index pages whose HTML linked to nothing)."""

import dataclasses
import re
from datetime import date

from fastapi.testclient import TestClient

from .test_api import make_app

TODAY = date.today().isoformat()


def _client(indexed_settings, tmp_path) -> TestClient:
    """The app with a stand-in build: each page's own file, with its list or root as the real ones have them."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    head = "<!doctype html><html><head><title>T</title></head><body>"
    for name, inner in {"index": '<div id="root"></div>',
                        "bills": '<h1>Bills</h1><p class="intro-text">Every bill.</p>'
                                 '<div id="bills-live" aria-live="polite"><p class="muted">Fetching.</p></div>',
                        "people": '<h1>MPs and peers</h1><p class="intro-text">Each member.</p>'
                                  '<div id="people-live" aria-live="polite"><p class="muted">Fetching.</p></div>',
                        "topics": '<h1>Topic briefings</h1>'
                                  '<div id="topics-live" aria-live="polite"><p class="muted">Fetching.</p></div>',
                        **{n: f'<h1>{n}</h1><div id="{n}-root"><p class="muted" role="status">Fetching.</p></div>'
                           for n in ("committees", "edms", "groups", "devolved")},
                        **{n: f'<div id="{r}-root"><p class="muted" role="status">Fetching.</p></div>'
                           for n, r in (("week", "week"), ("debate", "debate"), ("person", "person"))}}.items():
        (dist / f"{name}.html").write_text(f"{head}{inner}</body></html>")
    s = dataclasses.replace(indexed_settings, raw={
        **indexed_settings.raw, "paths": {**indexed_settings.raw.get("paths", {}), "web_dist": str(dist)}})
    return TestClient(make_app(s, tmp_path, auth=False)[0])


def _urls(body: str) -> list[tuple[str, str | None]]:
    return [(loc, mod) for loc, mod in re.findall(r"<url><loc>([^<]+)</loc>(?:<lastmod>([^<]+)</lastmod>)?</url>",
                                                  body)]


def _links(page: str) -> set[str]:
    return set(re.findall(r'<a href="(/[^"#?]*)', page))


def test_debates_and_weeks_have_sitemaps_dated_by_the_day_they_were_held(indexed_settings, tmp_path):
    client = _client(indexed_settings, tmp_path)
    debates = _urls(client.get("/sitemap-debates-2026.xml").text)
    assert debates and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d or "") for _, d in debates)
    for url, day in debates:
        assert f"/debates/{day}/" in url
    assert any("/commons-written-statements/" in u for u, _ in debates)  # each written statement has a page too
    first = debates[0][0].removeprefix("http://testserver")
    assert client.get(first).status_code == 200  # each address is a page
    weeks = _urls(client.get("/sitemap-weeks.xml").text)
    assert ("http://testserver/weeks/2026-09-14", "2026-09-17") in weeks  # Monday's address, its last sitting day
    assert client.get("/weeks/2026-09-14").status_code == 200


def test_the_people_sitemap_has_former_members_and_only_true_dates(indexed_settings, tmp_path):
    client = _client(indexed_settings, tmp_path)
    people = _urls(client.get("/sitemap-people.xml").text)
    listed = {u.rsplit("/people/", 1)[1].split("/")[0] for u, _ in people if "/people/" in u}
    current = {str(p["number"]) for p in client.get("/api/people").json()["people"]}
    everyone = {str(p["number"]) for p in client.get("/api/people?everyone=true").json()["people"]}
    assert current < everyone and everyone <= listed  # former members as well as sitting ones
    dates = [d for _, d in people if d]
    assert dates and max(dates) == "2026-10-08"  # the last day they spoke (a written answer), never today
    for name in ("bills", "committees", "groups", "devolved", "weeks"):
        assert TODAY not in client.get(f"/sitemap-{name}.xml").text, name


def test_index_pages_link_to_every_page_in_their_html(indexed_settings, tmp_path):
    client = _client(indexed_settings, tmp_path)
    bills = client.get("/bills").text
    assert "/bills/immigration-and-asylum-bill-2026" in _links(bills) and "/bills/year/2026" in _links(bills)
    year = client.get("/bills/year/2026")
    assert year.status_code == 200 and "<h1>Bills debated in 2026</h1>" in year.text
    assert "<title>Bills debated in 2026 | Where MPs Stand</title>" in year.text
    assert "/bills/immigration-and-asylum-bill-2026" in _links(year.text)
    assert client.get("/bills/year/1990").status_code == 404 and client.get("/bills/year/x").status_code == 404

    people = client.get("/people").text
    current = client.get("/api/people").json()["people"]
    assert {f"/people/{m['number']}/{m['slug']}" for m in current} <= _links(people)
    assert "/people/former" in _links(people)
    former = client.get("/people/former")
    gone = [m for m in client.get("/api/people?everyone=true").json()["people"] if not m["current"]]
    assert former.status_code == 200 and "<h1>Former MPs and peers</h1>" in former.text
    assert gone and {f"/people/{m['number']}/{m['slug']}" for m in gone} <= _links(former.text)
    assert client.get("/people/10001").status_code in (200, 404)  # a member's own address is still theirs

    weeks = client.get("/weeks")
    assert weeks.status_code == 200 and {"/weeks/2026-09-14", "/weeks/2012-03-05"} <= _links(weeks.text)
    for path in ("/committees", "/edms", "/groups", "/devolved", "/topics"):  # their lists: in each one's own tests
        assert client.get(path).status_code == 200, path
    assert client.get("/groups?parliament=nowhere").status_code == 404
    assert client.get("/edms/month/2024-13").status_code == 404


def test_week_pages_link_to_the_weeks_either_side_and_debates_to_their_speakers(indexed_settings, tmp_path):
    client = _client(indexed_settings, tmp_path)
    links = _links(client.get("/weeks/2026-09-14").text)
    assert "/weeks/2012-03-05" in links and "/weeks" in links  # the week before, as the record has it
    debates = _urls(client.get("/sitemap-debates-2026.xml").text)
    spoken = [u for u, _ in debates if "/commons/" in u or "/lords/" in u]
    page = client.get(spoken[0].removeprefix("http://testserver")).text
    assert any(re.fullmatch(r"/people/\d+/[a-z0-9-]+", h) for h in _links(page))


def test_a_members_page_links_to_the_debates_they_last_spoke_in_without_waiting_for_their_activity(
        indexed_settings, tmp_path):
    client = _client(indexed_settings, tmp_path)
    people = client.get("/api/people").json()["people"]
    for m in people:  # the first member with something said in the fixtures
        page = client.get(f"/people/{m['number']}/{m['slug']}").text
        if "<h2>Recent debates</h2>" in page:
            assert any(h.startswith("/debates/") for h in _links(page))
            break
    else:
        raise AssertionError("no member's page lists their recent debates")


def test_the_motions_store_gives_its_archive_by_month_and_sitemaps_by_year(tmp_path):
    from hansard.sources.edms import EdmStore

    store = EdmStore(tmp_path / "edms.sqlite")
    for i, (title, tabled, status) in enumerate([("A", "2024-01-10", "2024-02-01"), ("B", "2024-03-05", None),
                                                 ("C", "2025-07-01", None), ("D", None, None)], start=1):
        store.upsert({"id": i, "uin": str(i), "title": title, "text": "", "date_tabled": tabled, "status": "Open",
                      "status_date": status, "sponsors_count": 1, "primary_mnis": 1, "primary_name": "Ann",
                      "primary_party": "Labour", "amendment_to": None, "prayer": 0})
    store.set_sponsors(2, [{"mnis": 2, "name": "Bob", "party": "Labour", "sponsoring_order": 1,
                            "signed": "2024-05-20", "withdrawn": None}])
    assert store.months() == [("2025-07", 1), ("2024-03", 1), ("2024-01", 1)]
    assert [r["title"] for r in store.in_month("2024-03")] == ["B"]
    assert store.years() == ["2025", "2024", "undated"]
    # dated by the newest of tabling, change of status and signature
    assert store.sitemap("2024") == [(1, "2024-02-01"), (2, "2024-05-20")]
    assert store.sitemap("undated") == [(4, None)]
