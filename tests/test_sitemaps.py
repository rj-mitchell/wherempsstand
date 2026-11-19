"""The sitemap index: /sitemap.xml lists every sitemap the site has, so that one submission in Search Console
covers them all (19 November 2026, after Search Console showed none of the member or bill pages known to Google)."""

import re
from pathlib import Path

WEB_PUBLIC = Path(__file__).resolve().parents[1] / "web" / "public"
CHILDREN = ("pages", "people", "briefings", "bills", "committees", "edms", "groups", "devolved")


def test_the_index_lists_every_sitemap_and_each_one_made_by_the_site_answers(indexed_settings, tmp_path):
    from fastapi.testclient import TestClient

    from .test_api import make_app

    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    r = client.get("/sitemap.xml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    assert "<sitemapindex " in r.text and "<urlset" not in r.text
    listed = re.findall(r"<loc>([^<]+)</loc>", r.text)
    assert [loc.rsplit("/", 1)[1] for loc in listed] == [f"sitemap-{name}.xml" for name in CHILDREN]
    for name in CHILDREN[1:]:  # the fixed pages are a static file, served with the built site
        assert client.get(f"/sitemap-{name}.xml").status_code == 200, name


def test_the_fixed_pages_are_a_sitemap_of_their_own_and_robots_names_only_the_index():
    pages = (WEB_PUBLIC / "sitemap-pages.xml").read_text(encoding="utf-8")
    assert "<urlset " in pages and "<loc>https://wherempsstand.com/</loc>" in pages
    assert not (WEB_PUBLIC / "sitemap.xml").exists()  # /sitemap.xml is the index, which the app makes
    robots = (WEB_PUBLIC / "robots.txt").read_text(encoding="utf-8")
    assert re.findall(r"^Sitemap: (.+)$", robots, flags=re.M) == ["https://wherempsstand.com/sitemap.xml"]
