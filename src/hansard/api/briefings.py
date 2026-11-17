"""Shareable briefings.

Every completed briefing is kept at an unguessable address (/b/<id>), with the sources and checks exactly as
they were shown, the date it was prepared and the most recent record it could draw on. A kept briefing is
also the record to consult if someone later questions what the site published. A briefing can be withdrawn
(after a complaint, for example); its address then says so instead of showing it.
"""

from __future__ import annotations

import dataclasses
import html
import json
import re
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from wts_core.ports import SYSTEM_RANDOM, Random
from wts_core.usage import BriefingArchive, SavedBriefing, SavedBriefings, alphabet_id

from hansard.rag.checks import shown_again
from hansard.rag.interests import without_public_office
from hansard.store.state import StateDB, utcnow

# no 0/o or 1/l, so an address read aloud or retyped is not misread; 10 characters is about 50 bits
_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
ID_PATTERN = re.compile(r"^[a-km-z2-9]{10}$")


def new_briefing_id(chance: Random = SYSTEM_RANDOM) -> str:
    """A saved briefing's address: ten characters of the site's own alphabet."""
    return alphabet_id(_ALPHABET, 10, random=chance)


def as_row(b: SavedBriefing) -> dict[str, Any]:
    """A saved briefing under the names the site's pages and tools have always read: what was asked with it is its
    ``settings``, and the newest record's date and the model sit beside them."""
    return {"id": b.id, "created_at": b.created_at, "question": b.question, "settings": b.options,
            "events": b.events, "records_up_to": b.extra.get("records_up_to"), "model": b.extra.get("model"),
            "withdrawn_at": b.withdrawn_at, "withdrawn_reason": b.withdrawn_reason}


def keep(archive: SavedBriefings, state: StateDB, question: str, settings: dict[str, Any],
         kept: list[dict[str, Any]], final: dict[str, Any]) -> dict[str, Any]:
    """Keep a just-written briefing from its stream events; returns its id, preparation time and record date.
    What is kept is the plan, the sources and the final event (no text event, no token counts, and nothing that
    was for one reader only)."""
    records_up_to = state.latest_record_date()
    events = [e for e in kept if e["type"] in ("plan", "sources")] + [
        {k: v for k, v in final.items() if k not in ("usage", "briefing_key", "support_reminder")}]
    saved = archive.save(question, settings, events,
                         extra={"records_up_to": records_up_to, "model": final.get("model")})
    return {"briefing_id": saved.id, "prepared_at": saved.created_at, "records_up_to": records_up_to}


# featured briefings


def feature(archive: SavedBriefings, state: StateDB, bid: str, title: str | None = None,
            blurb: str | None = None) -> bool:
    b = get_briefing(archive, bid)
    if b is None or b["withdrawn_at"]:
        return False
    with state.connect() as con:
        con.execute("INSERT OR REPLACE INTO featured (briefing_id, title, blurb, featured_at) VALUES (?, ?, ?, ?)",
                    (bid, (title or "").strip() or None, (blurb or "").strip() or None, utcnow()))
    return True


def unfeature(state: StateDB, bid: str) -> bool:
    with state.connect() as con:
        return con.execute("DELETE FROM featured WHERE briefing_id = ?", (bid,)).rowcount > 0


def is_featured(state: StateDB, bid: str) -> bool:
    with state.connect() as con:
        return con.execute("SELECT 1 FROM featured WHERE briefing_id = ?", (bid,)).fetchone() is not None


def featured_list(archive: SavedBriefings, state: StateDB) -> list[dict[str, Any]]:
    """Featured briefings, newest first, with a short summary; withdrawn ones are left out."""
    with state.connect() as con:
        rows = con.execute("SELECT briefing_id, title, blurb, featured_at FROM featured "
                           "ORDER BY featured_at DESC").fetchall()
    out = []
    for r in rows:
        b = archive.get(r["briefing_id"])
        if b is None or b.withdrawn_at:
            continue
        final = next((e for e in b.events if e.get("type") == "final"), {})
        out.append({"id": b.id, "title": r["title"] or b.question, "question": b.question,
                    "blurb": r["blurb"] or summary_text(final.get("answer")), "prepared_at": b.created_at,
                    "records_up_to": b.extra.get("records_up_to"), "featured_at": r["featured_at"]})
    return out


class ShownOnly:
    """The saved briefings without those hidden while a concern about each is decided: for the lists the public
    reads (topics, featured briefings, the site map, the feeds). A hidden briefing is not there to be listed, as
    a withdrawn one is not; its own address says why (the route answers for it)."""

    def __init__(self, archive: SavedBriefings, hidden: set[str]) -> None:
        self._archive, self._hidden = archive, hidden

    def get(self, id: str) -> Any:
        return None if id in self._hidden else self._archive.get(id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._archive, name)


def get_briefing(archive: SavedBriefings, bid: str) -> dict[str, Any] | None:
    if not ID_PATTERN.match(bid):
        return None
    b = archive.get(bid)
    return as_row(b) if b else None


def recent_briefings(archive: BriefingArchive, limit: int = 50) -> list[dict[str, Any]]:
    """The newest saved briefings, for the owner's list: newest first, and of two saved in the same second the one
    saved later first, as the list has always been (the shared package's own listing, which keeps that order from
    wts-core 0.3.0)."""
    return [dataclasses.asdict(b) for b in archive.recent(limit)]


def public_view(b: dict[str, Any]) -> dict[str, Any]:
    """What a reader of /b/<id> receives: the question, the evidence and the checked answer, nothing internal."""
    ev = {e["type"]: e for e in b["events"]}
    plan, sources, final = ev.get("plan", {}), ev.get("sources", {}), ev.get("final", {})
    return {
        "id": b["id"],
        "question": b["question"],
        "created_at": b["created_at"],
        "records_up_to": b["records_up_to"],
        "settings": b["settings"],
        "plan": plan.get("plan"),
        "sources": sources.get("sources", []),
        "candidates": sources.get("candidates"),
        "semantic": sources.get("semantic"),
        "evidence": sources.get("evidence"),
        "answer": shown_again(final.get("answer")),
        "interests": without_public_office(final.get("interests")),
    }


_MD = [(re.compile(r"\s*\[Source \d+\]"), ""),
       (re.compile(r"\*\*\s*In brief\s*:?\s*\*\*\s*:?", re.I), ""),                 # the executive summary's label
       (re.compile(r"^#+ .*$", re.M), ""),                                  # the briefing's own title line
       (re.compile(r"\*\*\s*(Headline|Summary|Briefing)\s*:?\s*\*\*\s*:?", re.I), ""),  # "**Headline:**" labels
       (re.compile(r"^\s*(Headline|Summary)\s*:?\s*$", re.M | re.I), ""),      # a label on its own line
       (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),
       (re.compile(r"\s+"), " ")]


_PREAMBLE = re.compile(r"^(?:in|across|from|based on) the (?:records?|evidence|search results)(?: (?:retrieved|supplied|"
                       r"provided|shown))?,?\s*", re.I)


def summary_text(answer: dict[str, Any] | None, limit: int = 220) -> str:
    """The opening of a briefing as plain text, for link previews and topic lists: without its title line or an
    opening remark about the records, starting with a capital."""
    lines = [ln for ln in ((answer or {}).get("markdown") or "").splitlines() if ln.strip()]
    first = re.sub(r"[*#_]", "", lines[0]).strip() if lines else ""
    if len(lines) > 1 and first and len(first) < 150 and not re.search(r"[.!?]$", first):
        lines = lines[1:]  # a title ("Cost of living: positions in Parliament, ...") rather than a sentence
    text = "\n".join(re.sub(r"^\s*[-*•]\s+", "", ln) for ln in lines)
    for pattern, repl in _MD:
        text = pattern.sub(repl, text)
    text = re.sub(r"^(Headline:?|Briefing:?)\s*", "", text.strip(), flags=re.I)
    text = _PREAMBLE.sub("", text)
    text = text[:1].upper() + text[1:]
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "…"


def fmt_date(iso: str | None) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso[:10])
    return f"{d.day} {d.strftime('%B %Y')}"


# Every page's link preview is the family kit's card for this site (web/public/family/card.png, copied in by the
# kit's sync.mjs), unless a page has its own. The alt text is the kit's, from web/public/family/head.html; a test
# keeps the two the same.
SITE_CARD = "/family/card.png"
SITE_CARD_ALT = ("Where MPs Stand. Parliament: what MPs and peers have said and done, from Hansard and the official "
                 "registers. A Where They Stand project.")

_HEAD_TAGS = re.compile(
    r'\s*<(?:meta (?:property="og:[^"]+"|name="(?:twitter:[^"]+|description|robots)")[^>]*|link rel="canonical"[^>]*|'
    r'title>[^<]*</title)>', re.I)


def page_with_meta(index_html: str, *, title: str, description: str, url: str, image: str | None = None,
                   image_alt: str = "", noindex: bool = True, data: dict[str, Any] | None = None) -> str:
    """The app's index.html with the page title and link-preview tags for one briefing, and ``data`` (schema.org
    structured data, for search engines) if given. Without an ``image``, the preview is the site's family card."""
    e = html.escape
    if not image:
        site = urlsplit(url)
        image, image_alt = f"{site.scheme}://{site.netloc}{SITE_CARD}", SITE_CARD_ALT
    tags = [f"<title>{e(title)}</title>",
            f'<meta name="description" content="{e(description)}" />',
            f'<link rel="canonical" href="{e(url)}" />',
            '<meta property="og:site_name" content="Where MPs Stand" />',
            '<meta property="og:type" content="article" />',
            '<meta property="og:locale" content="en_GB" />',
            f'<meta property="og:title" content="{e(title)}" />',
            f'<meta property="og:description" content="{e(description)}" />',
            f'<meta property="og:url" content="{e(url)}" />',
            '<meta name="twitter:card" content="summary_large_image" />']
    tags += [f'<meta property="og:image" content="{e(image)}" />',
             '<meta property="og:image:width" content="1200" />',
             '<meta property="og:image:height" content="630" />',
             f'<meta property="og:image:alt" content="{e(image_alt)}" />']
    if noindex:
        tags.append('<meta name="robots" content="noindex" />')
    if data:  # "</" is escaped so the data cannot end the script element
        tags.append('<script type="application/ld+json">' + json.dumps(data).replace("</", "<\\/") + "</script>")
    stripped = _HEAD_TAGS.sub("", index_html)
    return stripped.replace("</head>", "    " + "\n    ".join(tags) + "\n  </head>", 1)
