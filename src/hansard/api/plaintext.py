"""Readable text in each page's HTML, for anything that reads pages without running their scripts: AI assistants,
search engines, readers with scripts off. It goes where the app later draws the page, so people see no difference.
Plain HTML only, every value escaped."""

from __future__ import annotations

import re
from html import escape as e
from typing import Any

from hansard.api.briefings import ASKED_BY_READER, briefing_title, fmt_date

# the page's placeholder ("Fetching the debate."), with any attributes it carries (role="status" in web/*.html)
_ROOT = re.compile(r'(<div id="[\w-]+-root">)\s*<p class="muted"[^>]*>[^<]*</p>\s*(</div>)')
_EMPTY_ROOT = '<div id="root"></div>'
# an index page's list, as it is before the app fills it: its live region or app root, with the "Fetching …" line
_LIST = re.compile(r'(<div id="[\w-]+-(?:live|root)"[^>]*>)\s*<p class="muted"[^>]*>[^<]*</p>\s*(</div>)')
# the home page's text for readers with scripts off (web/index.html); other pages built on it bring their own
_HOME_FALLBACK = re.compile(r'(?:<!--[^>]*-->\s*)?<noscript class="home-fallback">.*?</noscript>\s*', re.S)
MAX_ITEMS = 60
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def with_text(page: str, fragment: str | None) -> str:
    """The page with ``fragment`` placed in its app root (replaced by the app when it starts)."""
    if not fragment:
        return page
    if _EMPTY_ROOT in page:
        page = _HOME_FALLBACK.sub("", page, count=1)
        return page.replace(_EMPTY_ROOT, f'<div id="root"><main class="thread prose">{fragment}</main></div>', 1)
    return _ROOT.sub(lambda m: m.group(1) + fragment + m.group(2), page, count=1)


def with_list(page: str, fragment: str | None) -> str:
    """An index page (/bills, /people …) with ``fragment``, its list of links, in place of the "Fetching …" line
    (replaced by the app when it starts, which keeps it if its own data cannot be fetched: serverText.tsx)."""
    if not fragment:
        return page
    return _LIST.sub(lambda m: m.group(1) + f'<div class="server-list">{fragment}</div>' + m.group(2), page, count=1)


def with_heading(page: str, heading: str, intro: str | None = None) -> str:
    """The page with its first heading, and the paragraph introducing it, in other words (an archive page made from
    an index page's template)."""
    page = re.sub(r"<h1>[^<]*</h1>", lambda _: f"<h1>{e(heading)}</h1>", page, count=1)
    if intro is not None:
        page = re.sub(r'<p class="intro-text">.*?</p>', lambda _: f'<p class="intro-text">{e(intro)}</p>', page,
                      count=1, flags=re.S)
    return page


def _link(href: str | None, text: str) -> str:
    return f'<a href="{e(href)}">{e(text)}</a>' if href else e(text)


def person_path(m: dict[str, Any] | None) -> str | None:
    """A member's page, from anything that carries their number and name's slug."""
    return f"/people/{m['number']}/{m['slug']}" if m and m.get("number") and m.get("slug") else None


def _inline(md: str) -> str:
    """Escaped text with **bold** kept."""
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", e(md))


def markdown_text(md: str) -> str:
    """Enough of a briefing's markdown for reading: headings, bold labels, bullets and paragraphs."""
    out, items = [], []
    for block in re.split(r"\n\s*\n", md.strip()):
        for line in block.splitlines():
            line = line.rstrip()
            if re.match(r"^\s*[-*•]\s+", line):
                items.append(f"<li>{_inline(re.sub(r'^\s*[-*•]\s+', '', line))}</li>")
                continue
            if items:
                out.append("<ul>" + "".join(items) + "</ul>")
                items = []
            if m := re.match(r"^(#{1,6})\s+(.*)", line):
                out.append(f"<h3>{_inline(m.group(2))}</h3>")
            elif line.strip():
                out.append(f"<p>{_inline(line.strip())}</p>")
    if items:
        out.append("<ul>" + "".join(items) + "</ul>")
    return "".join(out)


def briefing_text(view: dict[str, Any], heading: str | None = None) -> str:
    answer = view.get("answer") or {}
    """A briefing as plain HTML. A topic briefing is headed by the topic's title and its standing question; any other
    is headed by its neutral title, with the question labelled as a reader's (never as the page's own heading)."""
    if heading:
        parts = [f"<h1>{e(heading)}</h1>", f"<p><em>{e(view['question'])}</em></p>"]
    else:
        parts = [f"<h1>{e(briefing_title(view.get('created_at')))}</h1>",
                 f"<p>{ASKED_BY_READER}: <em>{e(view['question'])}</em></p>"]
    parts.append(f"<p>A briefing written by AI (Claude) from the Hansard records cited below, prepared "
                 f"{e(fmt_date(view['created_at'][:10]))}"
                 + (f" from records up to {e(fmt_date(view['records_up_to']))}" if view.get("records_up_to") else "")
                 + ". Check the sources before relying on it.</p>")
    if answer.get("markdown"):
        parts.append(markdown_text(answer["markdown"]))
    sources = view.get("sources") or []
    if sources:
        parts.append("<h2>Sources</h2><ol>")
        for s in sources[:MAX_ITEMS]:
            href = s.get("official_url") or s.get("twfy_url") or s.get("url")
            parts.append(f'<li value="{int(s.get("n") or 0)}">{_link(href, s.get("title") or "Source")}</li>')
        parts.append("</ol>")
    return "".join(parts)


def person_text(core: dict[str, Any], activity: dict[str, Any] | None,
                recent: list[dict[str, Any]] | None = None) -> str:
    """A member's page. Their latest contributions when they are at hand; otherwise the debates they last spoke in
    (``recent``), which are quick to find."""
    parts = [f"<h1>{e(core['name'])}</h1>", f"<p>{e(core.get('status') or '')}.</p>"]
    if core.get("memberships"):
        parts.append("<h2>Membership</h2><ul>")
        for m in reversed(core["memberships"]):
            what = f"MP for {m.get('constituency') or '—'}" if m["house"] == "commons" else "House of Lords"
            parts.append(f"<li>{e(what)}{', ' + e(m['party']) if m.get('party') else ''} "
                         f"({e(fmt_date(m['start']))} – {e(fmt_date(m['end'])) if m.get('end') else 'now'})</li>")
        parts.append("</ul>")
    if core.get("offices"):
        parts.append("<h2>Roles</h2><ul>")
        for o in core["offices"][:30]:
            parts.append(f"<li>{e(o['title'])} ({e(fmt_date(o['start']))} – "
                         f"{e(fmt_date(o['end'])) if o.get('end') else 'now'})</li>")
        parts.append("</ul>")
    for s in core.get("seats") or []:
        results = "; ".join(f"{r['election']}: majority {r['majority']:,}" + (f" ({r['pct']}%)" if r.get("pct") else "")
                            + (f", second: {r['runner_up']}" if r.get("runner_up") else "")
                            for r in s.get("results") or [] if r.get("majority") is not None)
        if results:
            parts.append(f"<p>{e(s['constituency'])}: {e(results)}.</p>")
    if core.get("party_groups"):
        parts.append("<h2>Party groups and caucuses</h2><ul>")
        for g in core["party_groups"]:
            items = "; ".join(f"{ev['label']} ({ev['parliament']} Parliament)" for ev in g["evidence"])
            parts.append(f"<li>{e(g['name'])}: {e(items)}</li>")
        parts.append("</ul><p>On published evidence only: most groups publish no lists.</p>")
    contributions = (activity or {}).get("contributions") or []
    if contributions:
        parts.append("<h2>Recent contributions</h2><ol>")
        for c in contributions:
            parts.append(f"<li><p>{e(fmt_date(c['date']))}, {e(c['chamber'])}"
                         + (f", {e(c['heading'])}" if c.get("heading") else "") + "</p>"
                         f"<p>{e(c['excerpt'])}</p><p>{_link(c.get('debate_path') or c.get('twfy_url'), 'Read in full')}</p></li>")
        parts.append("</ol>")
    if not contributions and recent:
        parts.append("<h2>Recent debates</h2><ul>")
        for d in recent:
            parts.append(f"<li>{e(fmt_date(d['date']))}, {e(d.get('chamber') or '')}: "
                         f"{_link(d['path'], d.get('title') or 'the debate')}</li>")
        parts.append("</ul>")
    voting = (activity or {}).get("voting")
    if voting and voting.get("votes"):
        parts.append(f"<h2>Voting record</h2><p>{voting['votes']:,} votes recorded, of which {voting['rebellions']:,} "
                     "were against the majority of their own party.</p>")
    return "".join(parts)


def bill_text(b: dict[str, Any]) -> str:
    parts = [f"<h1>{e(b['name'])}</h1>",
             f"<p>{b.get('contributions', 0):,} contributions in Parliament, {e(fmt_date(b['first']))} to "
             f"{e(fmt_date(b['last']))}.</p><h2>Sittings</h2><ul>"]
    for s in (b.get("sittings") or [])[:MAX_ITEMS]:
        stages = ", ".join(s.get("stages") or [])
        parts.append(f"<li>{e(fmt_date(s['date']))}, {'House of Lords' if s.get('house') == 'lords' else 'House of Commons'}"
                     f"{': ' + e(stages) if stages else ''}, {_link(s.get('path') or s.get('url'), 'read the debate')}</li>")
    parts.append("</ul>")
    return "".join(parts)


def debate_text(d: dict[str, Any]) -> str:
    parts = [f"<h1>{e(d['title'])}</h1>", f"<p>{e(d['chamber_name'])}, {e(fmt_date(d['date']))}"
             + (f", {e(d['department'])}" if d.get("department") else "") + ".</p>"]
    shown = 0
    for it in d.get("items") or []:
        if it.get("kind") == "heading":
            parts.append(f"<h2>{e(' '.join(it.get('paragraphs') or []) or it.get('text') or '')}</h2>")
            continue
        if it.get("kind") != "said":
            continue
        sp = it.get("speaker") or {}
        who = sp.get("name") or it.get("speaker_name_raw") or "A member"
        detail = ", ".join(x for x in (sp.get("party"), sp.get("constituency")) if x)
        body = "".join(f"<p>{e(p)}</p>" for p in it.get("paragraphs") or [])
        parts.append(f"<h3>{_link(person_path(sp), who)}{' (' + e(detail) + ')' if detail else ''}</h3>{body}")
        shown += 1
        if shown >= 200:
            parts.append(f"<p>{_link(d.get('official_url'), 'The rest of the debate is in Hansard')}.</p>")
            break
    return "".join(parts)


def week_text(w: dict[str, Any]) -> str:
    parts = [f"<h1>In Parliament, week of {e(fmt_date(w['start']))}</h1>"]
    for day in w.get("days") or []:
        parts.append(f"<h2>{e(fmt_date(day['date']))}</h2>")
        for ch in day.get("chambers") or []:
            parts.append(f"<h3>{e(ch['name'])}</h3><ul>")
            for deb in (ch.get("debates") or [])[:MAX_ITEMS]:
                kind = f" ({deb['kind']})" if deb.get("kind") else ""
                parts.append(f"<li>{_link(deb.get('path'), deb['title'])}{e(kind)}</li>")
            parts.append("</ul>")
    nav = [_link(f"/weeks/{w['previous']}", "The week before") if w.get("previous") else "",
           _link(f"/weeks/{w['next']}", "The week after") if w.get("next") else "", _link("/weeks", "Every week")]
    parts.append(f"<p>{' · '.join(x for x in nav if x)}</p>")
    return "".join(parts)


def edm_text(m: dict[str, Any]) -> str:
    sponsor = m.get("sponsor") or {}
    parts = [f"<h1>{e(m['title'])}</h1>",
             f"<p>Early day motion {e(m.get('uin') or '')}, tabled {e(fmt_date(m['date_tabled']))}"
             + (f" by {_link(person_path(sponsor), sponsor['name'])}"
                + (f" ({e(sponsor['party'])})" if sponsor.get("party") else "") if sponsor.get("name") else "")
             + (f". Signed by {m['sponsors_count']} member{'' if m['sponsors_count'] == 1 else 's'}" if m.get("sponsors_count") else "") + ".</p>"]
    if m.get("text"):
        parts.append(f"<blockquote><p>{e(m['text'])}</p></blockquote>")
    if m.get("party_counts"):
        parts.append("<p>Signatures by party: " + e(", ".join(f"{p} {n}" for p, n in m["party_counts"])) + ".</p>")
    signed = [s for s in m.get("sponsors") or [] if not s.get("withdrawn") and s.get("name")]
    if signed:
        parts.append("<h2>Signed by</h2><ul>" + "".join(
            f"<li>{_link(person_path(s), s['name'])}{' (' + e(s['party']) + ')' if s.get('party') else ''}</li>"
            for s in signed) + "</ul>")
    return "".join(parts)


def committee_text(d: dict[str, Any]) -> str:
    span = f"since {fmt_date(d['start'])}" if d.get("start") and not d.get("end") else \
        (f"{fmt_date(d['start'])} to {fmt_date(d['end'])}" if d.get("start") else "")
    parts = [f"<h1>{e(d['name'])}</h1>",
             f"<p>{e(' '.join(x for x in (d.get('house'), (d.get('category') or '').lower() + ' committee') if x))}"
             f"{', ' + e(span) if span else ''}. {_link(d.get('url'), 'Its page on the Parliament website')}.</p>"]
    if d.get("purpose"):
        parts.append(f"<p>{e(d['purpose'])}</p>")
    if d.get("inquiries"):
        parts.append("<h2>Inquiries</h2><ul>")
        for q in d["inquiries"][:500]:  # every inquiry, so each can be reached from here
            when = " to ".join(fmt_date(x) for x in (q.get("open"), q.get("close")) if x)
            parts.append(f"<li>{_link(f'/inquiries/{q['id']}', q['title'])}{' (' + e(when) + ')' if when else ''}</li>")
        parts.append("</ul>")
    if d.get("top_organisations"):
        parts.append("<h2>Organisations it has heard from most</h2><ul>" + "".join(
            f"<li>{e(o['organisation'])} ({o.get('n') or o.get('sessions') or 0})</li>"
            for o in d["top_organisations"][:20]) + "</ul>")
    return "".join(parts)


def inquiry_text(d: dict[str, Any]) -> str:
    by = " and ".join(c["name"] for c in d.get("committees") or [])
    when = " to ".join(fmt_date(x) for x in (d.get("open"), d.get("close")) if x)
    parts = [f"<h1>{e(d['title'])}</h1>",
             f"<p>{e(d.get('type') or 'Inquiry')}{' by the ' + e(by) if by else ''}{', ' + e(when) if when else ''}. "
             f"{_link(d.get('url'), 'Its page on the Parliament website')}.</p>"]
    if d.get("publications"):
        parts.append("<h2>Reports and responses</h2><ul>" + "".join(
            f"<li>{_link(p.get('url'), p.get('title') or 'Publication')}"
            f"{' (' + e(fmt_date(p['date'])) + ')' if p.get('date') else ''}</li>"
            for p in d["publications"][:MAX_ITEMS]) + "</ul>")
    if d.get("sessions"):
        parts.append("<h2>Evidence sessions</h2><ul>")
        for s in d["sessions"][:MAX_ITEMS]:
            who = "; ".join(", ".join(x for x in (w.get("name"), w.get("organisation")) if x)
                            for w in s.get("witnesses") or [])
            transcript = f" ({_link(f'/evidence/{s['id']}', 'transcript')})" if s.get("transcript") and s.get("id") else ""
            parts.append(f"<li>{e(fmt_date(s['date'])) if s.get('date') else ''}: {e(who) or 'witnesses not listed'}"
                         f"{transcript}</li>")
        parts.append("</ul>")
    return "".join(parts)


def group_text(g: dict[str, Any]) -> str:
    parts = [f"<h1>{e(g['title'])}</h1>"]
    if g.get("purpose"):
        parts.append(f"<p>{e(g['purpose'])}</p>")
    facts = [x for x in (g.get("category"), g.get("categories")) if x]
    if facts:
        parts.append(f"<p>{e('; '.join(facts))}.</p>")
    if g.get("secretariat"):
        parts.append(f"<p>Secretariat: {e(g['secretariat'])}</p>")
    if g.get("members"):
        parts.append("<h2>Members</h2><ul>" + "".join(
            f"<li>{_link(person_path(m.get('member')), m.get('name') or '')}"
            f"{' (' + e(m['role']) + ')' if m.get('role') else ''}</li>"
            for m in g["members"][:100]) + "</ul>")
    if g.get("benefits"):
        parts.append("<h2>Funding and benefits registered</h2><ul>" + "".join(
            f"<li>{e(b.get('funder') or '')}: {e(b.get('type') or '')}"
            f"{', £' + e(b['value']) if b.get('value') else ''}"
            f"{' (received ' + e(fmt_date(b['received'])) + ')' if b.get('received') else ''}</li>"
            for b in g["benefits"][:MAX_ITEMS]) + "</ul>")
    if g.get("source_url"):
        parts.append(f"<p>{_link(g['source_url'], 'The official register entry')}.</p>")
    return "".join(parts)


def devolved_day_text(d: dict[str, Any]) -> str:
    parts = [f"<h1>{e(d['body'])}, {e(fmt_date(d['date']))}</h1>",
             f"<p>{d.get('contributions', 0):,} contributions from {d.get('speakers', 0):,} members. "
             f"{_link(d.get('official_url'), 'The official report')}.</p>"]
    shown = 0
    for it in d.get("items") or []:
        if it.get("kind") == "heading":
            parts.append(f"<h2>{e(it.get('text') or '')}</h2>")
        elif it.get("kind") == "said":
            sp = it.get("speaker") or {}
            detail = ", ".join(x for x in (sp.get("party"), sp.get("seat")) if x)
            parts.append(f"<h3>{_link(person_path(sp), sp.get('name') or 'A member')}"
                         f"{' (' + e(detail) + ')' if detail else ''}</h3>"
                         + "".join(f"<p>{e(p)}</p>" for p in it.get("paragraphs") or []))
            shown += 1
            if shown >= 200:
                break
    return "".join(parts)


def evidence_text(t: dict[str, Any]) -> str:
    parts = [f"<h1>{e(t.get('title') or 'Oral evidence')}</h1>"]
    if t.get("date"):
        parts.append(f"<p>Oral evidence, {e(fmt_date(t['date']))}.</p>")
    for turn in (t.get("turns") or [])[:300]:
        parts.append(f"<h3>{e(turn.get('speaker') or '')}</h3><p>{e(turn.get('text') or '')}</p>")
    return "".join(parts)


# Index and archive pages: plain lists of links, so that every page of the record can be reached from the index
# pages in a few steps by anything that does not run the pages' scripts. The apps draw their own lists over them.

def _years(first: str, last: str) -> str:
    return first[:4] if first[:4] == last[:4] else f"{first[:4]}–{last[:4]}"


def year_links(prefix: str, years: list[str], label: str) -> str:
    """'Bills by year: 2026 · 2025 …', each year a link to its archive page (``prefix`` + year)."""
    return f'<nav aria-label="{e(label)}"><p>{e(label)}: ' + " · ".join(
        _link(f"{prefix}{y}", y) for y in years) + "</p></nav>"


def bills_list_text(bills: list[dict[str, Any]], years: list[str], total: int | None = None) -> str:
    """A list of bills, newest first, with the years they were debated, and the archive of bills by year."""
    if not bills:
        return ""
    parts = [f"<p>{total:,} bill{'' if total == 1 else 's'}. The {len(bills):,} most recently debated "
             f"{'is' if len(bills) == 1 else 'are'} listed.</p>" if total else "",
             '<ul class="topic-list">']
    for b in bills:
        parts.append(f"<li>{_link(f'/bills/{b['slug']}', b.get('label') or b['name'])} "
                     f"({e(_years(b['first'], b['last']))})</li>")
    parts.append("</ul>")
    return "".join(parts) + year_links("/bills/year/", years, "Bills by the year they were debated")


def people_list_text(people: list[dict[str, Any]], *, former: bool = False) -> str:
    """Members by House, each linked to their page: the sitting members, or (``former``) those who have left since
    1997, with a link between the two lists."""
    parts = []
    for house, heading in (("commons", "MPs"), ("lords", "Members of the House of Lords")):
        members = [m for m in people if m.get("house") == house]
        if not members:
            continue
        if former:
            heading = "Former MPs" if house == "commons" else "Former members of the House of Lords"
        parts.append(f"<h2>{e(heading)} ({len(members):,})</h2><ul class=\"people-list\">")
        for m in members:
            detail = ", ".join(x for x in (m.get("constituency"), m.get("party"),
                                           f"until {m['until'][:4]}" if former and m.get("until") else None) if x)
            note = f' <span class="muted small">{e(detail)}</span>' if detail else ""
            parts.append(f"<li>{_link(person_path(m), m['name'])}{note}</li>")
        parts.append("</ul>")
    other = _link("/people", "Sitting MPs and peers") if former else \
        _link("/people/former", "Former MPs and peers since 1997")
    parts.append(f"<p>{other}</p>")
    return "".join(parts)


def edms_list_text(rows: list[dict[str, Any]], months: list[tuple[str, int]], *, heading: str | None = None) -> str:
    """Motions, each linked, and the archive of motions by the month they were tabled."""
    if not rows and not months:
        return ""
    parts = [f"<h2>{e(heading)}</h2>" if heading else "", '<ul class="edm-list">']
    for r in rows:
        sponsor = r.get("sponsor") or {}
        who = f", {_link(person_path(sponsor), sponsor['name'])}" if sponsor.get("name") else ""
        parts.append(f"<li>{_link(f'/edms/{r['id']}', r['title'])} ({e(fmt_date(r.get('date_tabled')))}{who})</li>")
    parts.append("</ul>")
    return "".join(parts) + edm_months_text(months)


def edm_months_text(months: list[tuple[str, int]]) -> str:
    """Every month motions were tabled in, by year: 'Motions by month. 2026: January, February …'."""
    by_year: dict[str, list[str]] = {}
    for month, _ in months:
        by_year.setdefault(month[:4], []).append(month)
    if not by_year:
        return ""
    lines = [f"<li>{e(y)}: " + ", ".join(_link(f"/edms/month/{m}", MONTHS[int(m[5:7]) - 1])
                                        for m in sorted(ms)) + "</li>" for y, ms in by_year.items()]
    return '<nav aria-label="Motions by month"><h2>Motions by month</h2><ul>' + "".join(lines) + "</ul></nav>"


def committees_list_text(rows: list[dict[str, Any]], house_names: dict[str, str]) -> str:
    parts = []
    for house in ("Commons", "Lords", "Joint"):
        mine = [r for r in rows if r.get("house") == house]
        if mine:
            parts.append(f"<h2>{e(house_names.get(house, house))}</h2><ul class=\"edm-list committee-list\">"
                         + "".join(f"<li>{_link(f'/committees/{r['id']}', r['name'])}</li>" for r in mine) + "</ul>")
    return "".join(parts)


def groups_list_text(groups: list[dict[str, Any]], parliament: str, parliaments: dict[str, str]) -> str:
    """One parliament's groups, each linked, and links to the other parliaments' lists."""
    if not groups:
        return ""
    parts = [f"<h2>{e(parliaments.get(parliament, parliament))}</h2><ul class=\"edm-list\">"]
    parts += [f"<li>{_link(f'/groups/{parliament}/{g['slug']}', g['title'])}</li>" for g in groups]
    parts.append("</ul>")
    others = [_link("/groups" if k == "uk" else f"/groups?parliament={k}", name)
              for k, name in parliaments.items() if k != parliament]
    parts.append(f"<p>Groups in the {' · '.join(others)}</p>" if others else "")
    return "".join(parts)


def devolved_list_text(legislatures: list[dict[str, Any]]) -> str:
    """Each devolved parliament's latest sitting days and the archive of its days by year."""
    parts = []
    for leg in legislatures:
        if not leg.get("days"):
            continue
        parts.append(f"<h2>{e(leg['name'])}</h2><ul>" + "".join(
            f"<li>{_link(f'/devolved/{leg['id']}/{d['key']}', fmt_date(d['date']))}</li>" for d in leg["days"])
                     + "</ul>")
        if leg.get("years"):
            parts.append(year_links(f"/devolved/{leg['id']}/year/", leg["years"], f"{leg['name']} by year"))
    return "".join(parts)


def devolved_year_text(name: str, leg: str, days: list[tuple[str, str]]) -> str:
    return "<ul>" + "".join(f"<li>{_link(f'/devolved/{leg}/{key}', fmt_date(day))}</li>" for key, day in days) + \
        f"</ul><p>{_link('/devolved', 'All the devolved parliaments')}</p>"


def topics_list_text(topics: list[dict[str, Any]], featured: list[dict[str, Any]]) -> str:
    parts = ['<ul class="topic-list">'] + [
        f"<li>{_link(f'/topics/{t['slug']}', t['title'])}</li>" for t in topics] + ["</ul>"]
    if featured:
        parts.append("<h2>Featured briefings</h2><ul class=\"topic-list\">" + "".join(
            f"<li>{_link(f'/b/{f['id']}', f.get('title') or 'Briefing')}</li>" for f in featured) + "</ul>")
    return "".join(parts)


def weeks_list_text(weeks: list[dict[str, str]]) -> str:
    """Every week Parliament sat, by year, each linked to its page."""
    by_year: dict[str, list[dict[str, str]]] = {}
    for w in weeks:
        by_year.setdefault(w["start"][:4], []).append(w)
    parts = ["<h1>Every week in Parliament</h1>",
             f"<p>The {len(weeks):,} weeks either House sat since the record begins, newest first. "
             f"{_link('/this-week', 'The latest week')}.</p>"]
    for y, ws in by_year.items():
        parts.append(f"<h2>{e(y)}</h2><ul>" + "".join(
            f"<li>{_link(f'/weeks/{w['start']}', 'Week of ' + fmt_date(w['start']))}</li>" for w in ws) + "</ul>")
    return "".join(parts)
