"""Measurement for Google Ad Grants, [ads] in config/settings.toml (docs/DECISIONS.md, 50; hansard/api/measure.py).

Off, as it ships: every page is the built file as it is, byte for byte, the site sends no content security policy
of its own (the front door sends it), /privacy is today's notice, and the measurement notice is at no address. On,
with a tag: every page loads the family kit's measure.js once, in its head, with the tag and this site's five
conversions; the static pages' footer has "Measurement choices" on a line of its own after the notices; /privacy is
the measurement notice; and every answer carries the front door's policy with exactly the kit's hosts added. An
unfinished notice stops the site, and `hansard check-settings`, before anything starts. The pages' marks and the
React footer are tested in web/src/__tests__/measure.test.tsx; the front door in tests/test_deploy.py.

The pages are the site's own sources with their partials put in, as the build does (web/vite.config.ts), so this
runs without a build of the app, as CI's Python job does."""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner
from wts_core.settings import SettingsError

from hansard import cli
from hansard.api import measure
from hansard.api.app import create_app
from hansard.config import REPO_ROOT, load_settings

from .test_api import make_app

WEB = REPO_ROOT / "web"
TAG = "AW-123456789"
KIT_CSP = json.loads((WEB / "public" / "family" / "measure-csp.json").read_text())
GOOGLE = re.compile(r"google|doubleclick", re.I)
PAGES = ["/", "/about", "/methodology", "/corrections", "/named", "/privacy", "/accessibility", "/terms",
         "/no-such-page"]


def built(html: str) -> str:
    """A page source with its partials put in, as web/vite.config.ts does at build time."""
    for _ in range(5):
        html = re.sub(r"<!--#include (family/)?([\w-]+)-->", lambda m: (
            WEB / "public" / "family" / f"{m[2]}.html" if m[1] else WEB / "partials" / f"{m[2]}.html").read_text(),
            html)
    return html


def finished(html: str) -> str:
    """The measurement notice as the owner finishes it before turning measurement on."""
    return html.replace("NNNNNNN", "1234567").replace(
        "Last updated 22 November 2026 (draft, for when measurement is turned on).", "Last updated 1 March 2027.")


@pytest.fixture
def dist(tmp_path) -> Path:
    """The built site, as far as these tests need it: every page, the kit's files, the notice finished."""
    out = tmp_path / "dist"
    (out / "assets").mkdir(parents=True)
    (out / "family").mkdir()
    for page in WEB.glob("*.html"):
        html = built(page.read_text(encoding="utf-8"))
        (out / page.name).write_text(finished(html) if page.name == measure.NOTICE else html, encoding="utf-8")
    for name in ("measure.js", "measure-csp.json", "family.css"):
        (out / "family" / name).write_bytes((WEB / "public" / "family" / name).read_bytes())
    return out


def switched(settings, dist: Path, measurement: bool, tag: str = TAG):
    return dataclasses.replace(settings, raw={**settings.raw, "ads": {"measurement": measurement, "tag": tag},
                                              "paths": {**settings.raw.get("paths", {}), "web_dist": str(dist)}})


@pytest.fixture
def site(indexed_settings, tmp_path, dist):
    """The site with the switch as given, on the built pages above."""

    def start(measurement: bool, tag: str = TAG, folder: str = "s") -> TestClient:
        here = tmp_path / folder
        here.mkdir()
        app, _, _ = make_app(switched(indexed_settings, dist, measurement, tag), here, auth=False)
        return TestClient(app)

    return start


def test_the_switch_ships_off_with_no_tag(settings, dist):
    s = load_settings(env={})
    assert s.section("ads") == {"measurement": False, "tag": ""}
    assert measure.measure_tag(s) == ""
    assert measure.measure_tag(switched(s, dist, True, "")) == "", "on without a tag is still off"
    assert measure.measure_tag(switched(s, dist, True, 'AW-1"><script>')) == ""
    assert measure.measure_tag(switched(s, dist, False)) == ""
    assert measure.measure_tag(switched(s, dist, True)) == TAG
    # the server sets it in deploy/.env
    on = load_settings(env={"HANSARD_ADS_MEASUREMENT": "true", "HANSARD_ADS_TAG": TAG})
    assert measure.measure_tag(on) == TAG
    assert measure.measure_tag(load_settings(env={"HANSARD_ADS_TAG": TAG})) == ""


def test_off_every_page_is_the_built_file_byte_for_byte_and_no_policy_is_sent(site, dist):
    client = site(False)
    for path in PAGES:
        r = client.get(path)
        if path != "/":  # the home page is its file with what search engines are told about the site (app.py: home)
            name = {"/no-such-page": "404"}.get(path, path.strip("/"))
            assert r.content == (dist / f"{name}.html").read_bytes(), path
        assert "content-security-policy" not in r.headers, path
        assert not re.search(r"measure\.js|data-wts-measure-open|Measurement choices", r.text), path
    privacy = client.get("/privacy").text
    assert "Where MPs Stand uses no cookies, no advertising and no tracking" in privacy
    assert 'id="measurement"' not in privacy and not GOOGLE.search(privacy)
    # the measurement notice is never at an address of its own
    for path in ("/privacy-measurement", "/privacy-measurement.html"):
        r = client.get(path)
        assert r.status_code == 404 and r.content == (dist / "404.html").read_bytes(), path
    assert "content-security-policy" not in client.get("/api/health").headers


def test_on_every_page_loads_measure_js_once_and_the_footer_has_measurement_choices(site):
    client = site(True)
    for path in PAGES:
        r = client.get(path)
        head, _, body = r.text.partition("</head>")
        assert head.count(measure.script(TAG)) == 1 and r.text.count("measure.js") == 1, path
        assert measure.script(TAG) == (
            '<script src="/family/measure.js" data-tag="AW-123456789" data-privacy="/privacy" '
            'data-measures="briefing_read source_opened download donate_click report_sent" defer></script>')
        assert "googletagmanager" not in r.text, path  # only measure.js loads it, and only after "Accept"
        assert int(r.headers["content-length"]) == len(r.content), path
        assert "etag" not in r.headers, path
        for footer in re.findall(r'<footer class="footer full-width">.*?</footer>', body, flags=re.S):
            # still the family's six links; "Measurement choices" a line of its own after the notices
            assert len(re.findall(r"<a ", footer.split('<nav class="footer-nav"')[1].split("</nav>")[0])) == 6
            assert re.search(r'<p class="licence">.*?</p>\s*' + re.escape(measure.CHOICES) + r"\s*</footer>",
                             footer, flags=re.S), path
    # a HEAD answer says what the GET would send
    head = client.head("/about")
    assert head.status_code == 200 and int(head.headers["content-length"]) == len(client.get("/about").content)
    # the API and the kit's files are left as they are
    assert client.get("/api/health").json()
    assert client.get("/family/measure.js").content == (WEB / "public" / "family" / "measure.js").read_bytes()


def test_on_the_privacy_notice_is_the_measurement_version(site, dist):
    client = site(True)
    for path in ("/privacy", "/privacy.html"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.text == measure.measured((dist / measure.NOTICE).read_text(), TAG), path
    html = client.get("/privacy").text
    for words in ("Google Ad Grants", '<h2 id="measurement">',
                  "finish reading a briefing, open a source,\n          download a briefing",
                  "independent controller", "The legal basis is your consent", "90 days",
                  'href="https://policies.google.com/technologies/retention"',
                  'href="https://business.safety.google/privacy/"',
                  'href="https://business.safety.google/adscontrollerterms/"', "“Measurement choices”",
                  "Nothing is loaded from Google until you choose",
                  "only the path is sent, never anything after a “?” or a “#”, so a question in a page's address", "No ads are ever shown",
                  "nothing on them is personalised", "registered in England and Wales (charity number 1234567)",
                  "Last updated 1 March 2027.", '<link rel="canonical" href="https://wherempsstand.com/privacy" />'):
        assert words in html, words
    # and still never at an address of its own
    assert client.get("/privacy-measurement").status_code == 404


def test_on_every_answer_carries_the_front_doors_policy_with_exactly_the_kits_hosts(site):
    client = site(True)
    for path in ("/", "/privacy", "/api/health", "/family/family.css"):
        policy = client.get(path).headers["content-security-policy"]
        directives = {d.split()[0]: d.split()[1:] for d in policy.split("; ")}
        base = {d.split()[0]: d.split()[1:] for d in measure.FRONT_DOOR_CSP.split("; ")}
        assert directives["script-src"] == ["'self'", *KIT_CSP["script-src"]]
        assert directives["img-src"] == ["'self'", "data:", *KIT_CSP["img-src"]]
        assert directives["connect-src"] == ["'self'", *KIT_CSP["connect-src"]]
        assert directives["frame-src"] == ["'self'", *KIT_CSP["frame-src"]]
        for name in ("default-src", "style-src", "frame-ancestors"):
            assert directives[name] == base[name]
        assert set(directives) == {*base, "script-src", "frame-src"}
        assert "*" not in policy and "'unsafe-inline'" not in policy.replace("style-src 'self' 'unsafe-inline'", "")


def test_the_front_doors_policy_is_the_one_the_site_extends():
    caddyfile = (REPO_ROOT / "deploy" / "Caddyfile").read_text()
    own = caddyfile.split("{$DOMAIN} {", 1)[1].split("\n}\n", 1)[0]
    assert f'?Content-Security-Policy "{measure.FRONT_DOOR_CSP}"' in own


def test_on_then_off_the_pages_are_as_they_were(site):
    off, on, off_again = site(False, folder="1"), site(True, folder="2"), site(False, folder="3")
    before = {p: off.get(p).content for p in PAGES}
    assert all(b"measure.js" in on.get(p).content for p in PAGES)
    assert {p: off_again.get(p).content for p in PAGES} == before


def test_an_unfinished_notice_stops_the_site_and_check_settings(settings, dist, monkeypatch):
    draft = built((WEB / measure.NOTICE).read_text())
    assert "NNNNNNN" in draft and "(draft, for when measurement is turned on)" in draft
    (dist / measure.NOTICE).write_text(draft)
    on = switched(settings, dist, True)
    with pytest.raises(SettingsError, match="NNNNNNN"):
        create_app(on)
    monkeypatch.setattr(cli, "_settings", lambda: on)
    result = CliRunner().invoke(cli.app, ["check-settings"])
    assert result.exit_code == 1 and "measurement privacy notice" in result.output
    # finished, the same settings pass; and off, a draft is no matter
    (dist / measure.NOTICE).write_text(finished(draft))
    measure.check(on, dist)
    (dist / measure.NOTICE).write_text(draft)
    measure.check(switched(settings, dist, False), dist)


def test_the_notice_differs_from_todays_only_where_measurement_needs_it():
    """The measurement notice is today's with the measurement section and the lines that change with it, so a
    change to today's notice that is not made to it too shows here."""
    today = (WEB / "privacy.html").read_text()
    notice = (WEB / measure.NOTICE).read_text()
    kept = [p for p in re.findall(r"<(?:h2|dt)[^>]*>.*?</(?:h2|dt)>", today, flags=re.S)]
    for heading in kept:
        assert heading in notice, heading
    assert notice.count("<h2") == today.count("<h2") + 1
