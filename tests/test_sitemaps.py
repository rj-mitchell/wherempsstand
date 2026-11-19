"""The sitemap index: /sitemap.xml lists every sitemap the site has, so that one submission in Search Console
covers them all (19 November 2026, after Search Console showed none of the member or bill pages known to Google)."""

import re
from pathlib import Path

WEB_PUBLIC = Path(__file__).resolve().parents[1] / "web" / "public"
CHILDREN = ("pages", "people", "briefings", "bills", "weeks", "committees", "groups", "devolved")


def test_the_index_lists_every_sitemap_and_each_one_made_by_the_site_answers(indexed_settings, tmp_path):
    from fastapi.testclient import TestClient

    from .test_api import make_app

    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    r = client.get("/sitemap.xml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    assert "<sitemapindex " in r.text and "<urlset" not in r.text
    listed = re.findall(r"<loc>([^<]+)</loc>", r.text)
    names = [loc.rsplit("/", 1)[1] for loc in listed]
    # the fixed ones, then the debates a year each (the fixtures have days in 2026 and 2012; no motions here)
    assert names == [f"sitemap-{name}.xml" for name in CHILDREN] + ["sitemap-debates-2026.xml",
                                                                    "sitemap-debates-2012.xml"]
    for name in names[1:]:  # the fixed pages are a static file, served with the built site
        assert client.get(f"/{name}").status_code == 200, name
    assert client.get("/sitemap-debates-1990.xml").status_code == 404
    assert client.get("/sitemap-edms-1990.xml").status_code == 404 and client.get("/sitemap-edms-x.xml").status_code == 404


def test_the_fixed_pages_are_a_sitemap_of_their_own_and_robots_names_only_the_index():
    pages = (WEB_PUBLIC / "sitemap-pages.xml").read_text(encoding="utf-8")
    assert "<urlset " in pages and "<loc>https://wherempsstand.com/</loc>" in pages
    assert not (WEB_PUBLIC / "sitemap.xml").exists()  # /sitemap.xml is the index, which the app makes
    robots = (WEB_PUBLIC / "robots.txt").read_text(encoding="utf-8")
    assert re.findall(r"^Sitemap: (.+)$", robots, flags=re.M) == ["https://wherempsstand.com/sitemap.xml"]


def _robots_allows(path: str) -> bool:
    """Whether robots.txt lets a crawler fetch ``path``, read as Google and RFC 9309 read it: the longest matching
    rule wins and, of two as long, Allow (Python's own robotparser takes the first match, which is not how search
    engines read the file)."""
    rules = []
    for line in (WEB_PUBLIC / "robots.txt").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(Allow|Disallow):\s*(\S*)", line)
        if m and m.group(2):
            pattern = re.escape(m.group(2)).replace(r"\*", ".*").replace(r"\$", "$")
            rules.append((len(m.group(2)), m.group(1) == "Allow", pattern))
    found = [(n, allow) for n, allow, pattern in rules if re.match(pattern, path)]
    return max(found, default=(0, True))[1]


# what each page of the record fetches as it is drawn (web/src): Google's renderer is refused any of these that
# robots.txt closes, and the page then showed "Failed to fetch" in place of its content (audit, 19 November 2026)
PAGE_FETCHES = ["/api/people/10001?part=core", "/api/people/10001?part=activity", "/api/people/10001?part=interests",
                "/api/people/10001/contributions?offset=10&limit=10", "/api/people", "/api/bills",
                "/api/bills/railways-bill-2025", "/api/debates/2026-11-18/commons/railways-bill", "/api/week",
                "/api/week?start=2026-11-16", "/api/committees", "/api/committees/24", "/api/inquiries/9",
                "/api/evidence/123", "/api/evidence?q=x", "/api/witnesses?q=", "/api/edms?page=2", "/api/edms/63000",
                "/api/groups?parliament=uk", "/api/groups/uk/cycling", "/api/devolved", "/api/devolved/senedd",
                "/api/devolved/senedd/2026-10-14", "/api/donations", "/api/directorships", "/api/lighter-side",
                "/api/topics", "/api/topics/cost-of-living", "/api/briefings/abcdefghjk",
                "/api/statements?path=/bills/x", "/api/corrections", "/api/status", "/api/site", "/api/meta"]
# what costs money, writes, is a reader's own or the owner's, and the searches (each visitor's allowance)
CLOSED = ["/api/ask", "/api/search", "/api/v1/search?q=jury", "/api/trends?q=x", "/api/timeline?q=x&person=1",
          "/api/postcode/SW1A1AA", "/api/feedback", "/api/report", "/api/support-clicked", "/api/hit", "/api/me",
          "/api/auth/login", "/api/admin/usage", "/api/admin/briefings", "/api/check", "/api/docs",
          "/api/openapi.json", "/api/an-address-added-later", "/admin"]


def test_robots_lets_search_engines_draw_the_pages_but_not_ask_write_or_reach_the_owners_pages():
    assert [p for p in PAGE_FETCHES if not _robots_allows(p)] == []
    assert [p for p in CLOSED if _robots_allows(p)] == []
    assert _robots_allows("/") and _robots_allows("/people/10001/diane-abbott") and _robots_allows("/b/abcdefghjk")


def test_robots_closes_every_address_that_writes_or_is_the_owners(indexed_settings, tmp_path):
    """An address that only takes POST or DELETE writes something or costs something, and the owner's are private:
    none may be open to crawlers, whatever is added later. (Crawlers only read: an address that also answers GET,
    such as /api/statements, is judged by what it gives to a GET.)"""
    from .test_api import make_app

    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    routes = [(r.path, r.methods) for r in app.routes if getattr(r, "methods", None) and r.path.startswith("/api/")]
    read = {path for path, methods in routes if methods & {"GET", "HEAD"}}
    writes = [path for path, _ in routes if path not in read]
    owners = [path for path, _ in routes if path.startswith(("/api/admin", "/api/auth", "/api/check"))]
    assert writes and owners
    sample = {path: re.sub(r"\{[^}]+\}", "x", path) for path in writes + owners}
    assert [path for path, url in sample.items() if _robots_allows(url)] == []
