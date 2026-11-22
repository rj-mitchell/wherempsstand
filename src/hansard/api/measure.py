"""Measurement for Google Ad Grants: built, and switched off (docs/DECISIONS.md, 50; the charity's
family/MEASUREMENT.md).

The switch is ``[ads] measurement`` and ``tag`` in config/settings.toml, false and empty as the site ships; on the
server ``HANSARD_ADS_MEASUREMENT`` and ``HANSARD_ADS_TAG`` in deploy/.env set them. Off, nothing here runs: the
pages are the built files as they are, and the site sends no content security policy of its own (the front door
sends it). On, with a valid tag, and only then:

- every page of the built site loads the family kit's ``measure.js`` in its head, which shows the family's consent
  banner and loads the Google tag only if the reader accepts;
- the footer of the static pages gains "Measurement choices" on a line of its own after the notices (the React
  pages draw the same line themselves when the page loads ``measure.js``: web/src/components/SiteFooter.tsx);
- ``/privacy`` is the measurement version of the notice, ``web/privacy-measurement.html``, which is never served at an
  address of its own, and which the site refuses to start with while it still says ``NNNNNNN`` or "(draft";
- every answer carries a content security policy: the front door's own, with the hosts the Google tag needs added
  from the kit's ``measure-csp.json``. The front door passes a policy the site sends, and sends its own only when
  the site sends none (deploy/Caddyfile), so the one switch changes both.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from starlette.types import ASGIApp, Message, Receive, Scope, Send
from wts_core.settings import SettingsError

from hansard.config import Settings

# The conversions this site has, for measure.js (its banner names only these): the end of a finished briefing, a
# source chip or a link to the record, the Word download and "Print or save as PDF", the Donate links, and a report
# or a statement sent
MEASURES = "briefing_read source_opened download donate_click report_sent"
_TAG = re.compile(r"(?:AW|G|GT)-[A-Za-z0-9]+")
# The content security policy the front door sends for this site (deploy/Caddyfile), which the site's own policy
# extends while measuring. tests/test_measure.py holds the two to the same words.
FRONT_DOOR_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                  "frame-ancestors 'none'")
# The measurement version of the privacy notice, in the built site, and the words that mark it unfinished
NOTICE = "privacy-measurement.html"
UNFINISHED = re.compile(r"NNNNNNN|\(draft\b")
# "Measurement choices", a control that opens the kit's banner again: after the footer's notices, on a line of its
# own, since it is not one of the family's six footer links. A link to the notice's section, so it still leads
# somewhere without the script.
CHOICES = ('<p class="measure-choices"><a href="/privacy#measurement" data-wts-measure-open>'
           "Measurement choices</a></p>")
_FOOTER = re.compile(r'(<footer class="footer full-width">.*?)(\s*</footer>)', re.S)


def measure_tag(settings: Settings) -> str:
    """The Google tag's ID while ``[ads] measurement`` is on with a valid tag, else "" (off)."""
    ads = settings.section("ads")
    tag = str(ads.get("tag") or "").strip()
    return tag if ads.get("measurement") and _TAG.fullmatch(tag) else ""


def policy(kit: Path, base: str = FRONT_DOOR_CSP) -> str:
    """The front door's policy with the hosts in the kit's measure-csp.json (``kit``) added, each list to the directive
    of its name (a directive the policy lacks starts from 'self', as default-src gave it). The kit's list is read,
    never edited here."""
    directives = [d.strip() for d in base.split(";") if d.strip()]
    for name, hosts in json.loads(kit.read_text()).items():
        if not isinstance(hosts, list):
            continue
        at = next((i for i, d in enumerate(directives) if d.split()[0] == name), None)
        if at is None:
            directives.append(" ".join([name, "'self'", *hosts]))
        else:
            directives[at] = " ".join([directives[at], *hosts])
    return "; ".join(directives)


def script(tag: str) -> str:
    return (f'<script src="/family/measure.js" data-tag="{tag}" data-privacy="/privacy" data-measures="{MEASURES}" '
            "defer></script>")


def measured(page: str, tag: str) -> str:
    """A page of the site while measuring: ``measure.js`` in its head, and "Measurement choices" in its footer."""
    if "/family/measure.js" not in page:
        page = page.replace("</head>", f"  {script(tag)}\n  </head>", 1)
    return _FOOTER.sub(lambda m: f"{m[1]}\n  {CHOICES}{m[2]}", page)


def notice(dist: Path) -> Path:
    """The measurement version of the privacy notice, refused while it is unfinished."""
    path = dist / NOTICE
    if not path.is_file():
        raise SettingsError(f"[ads] measurement is on, but the built site has no {NOTICE}: build the web app again")
    if UNFINISHED.search(path.read_text()):
        raise SettingsError("[ads] measurement is on, but the measurement privacy notice (web/privacy-measurement.html)"
                            " isn't finished: put in the charity number for NNNNNNN and the date it was last updated,"
                            " and build again")
    return path


def check(settings: Settings, dist: Path) -> None:
    """While measuring, what would stop the site from starting: no measurement notice in the build, or an unfinished
    one, or no list of the kit's hosts. Off, nothing."""
    if measure_tag(settings):
        notice(dist)
        if not (dist / "family" / "measure-csp.json").is_file():
            raise SettingsError("[ads] measurement is on, but the built site has no family/measure-csp.json")


# Paths whose answers are never pages of the site: the API (its own docs page included) and the built files
_NOT_PAGES = ("/api/", "/assets/", "/family/")
# what a browser may send that would ask for part of a page, or for nothing if unchanged: a page is changed as it is
# sent, so it is always sent whole
_PARTIAL = {b"range", b"if-range", b"if-none-match", b"if-modified-since"}


class Measure:
    """While measuring: the site's policy on every answer, and every page of the site changed as it is sent
    (``measured``). Plain ASGI, so the server-sent events of a briefing pass through as they are written."""

    def __init__(self, app: ASGIApp, tag: str, csp: str) -> None:
        self.app, self.tag, self.csp = app, tag, csp.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        page = not scope["path"].startswith(_NOT_PAGES)
        if page:
            scope = {**scope, "headers": [(k, v) for k, v in scope["headers"] if k not in _PARTIAL]}
        start: Message | None = None
        body: list[bytes] = []

        def with_policy(headers: list) -> list:
            return [*[(k, v) for k, v in headers if k.lower() != b"content-security-policy"],
                    (b"content-security-policy", self.csp)]

        async def sending(message: Message) -> None:
            nonlocal start
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                html = any(k.lower() == b"content-type" and v.startswith(b"text/html") for k, v in headers)
                if not (page and html):
                    await send({**message, "headers": with_policy(headers)})
                    return
                start = {**message, "headers": with_policy(headers)}
                return
            if start is None or message["type"] != "http.response.body":
                await send(message)
                return
            body.append(message.get("body", b""))
            if message.get("more_body"):
                return
            text = measured(b"".join(body).decode(), self.tag).encode()
            headers = [(k, v) for k, v in start["headers"]
                       if k.lower() not in (b"content-length", b"etag", b"last-modified")]
            await send({**start, "headers": [*headers, (b"content-length", str(len(text)).encode())]})
            await send({"type": "http.response.body", "body": text})

        await self.app(scope, receive, sending)
