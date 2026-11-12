"""Statements beside items: the right of reply (the charity's impartiality policy, section 7.2).

A member of Parliament, a peer, a member of a devolved parliament, an organisation such as a party group, or someone
acting for them, can ask to add a short statement in their own words beside what the site says about them. It is
shown beside the item it is about and on the member's own page, headed "Statement from [name], [date]" and followed
by "In their own words. Not checked or endorsed by Where MPs Stand."

A statement is never part of the record: it lives only here, in the site's own state database, and nothing that
searches the record or writes briefings reads this table (tests/test_statements.py checks both). It is published as
written or declined with reasons; it is never edited, except to remove something the rules don't allow, with the
author's agreement (the owner then replaces the text with ``update_statement``).

The email address given is used only to confirm who sent it and to let them update or withdraw it. It is kept while
the statement is pending or shown, and deleted three months after the statement is withdrawn or declined (the
privacy notice), by ``forget_old_emails``, which runs whenever statements are written or listed, and at each update.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from hansard.store.state import StateDB, utcnow

MAX_WORDS = 150
EMAIL_KEPT_MONTHS = 3  # after a statement is withdrawn or declined (the privacy notice)
DAILY_LIMIT = 5  # statements per visitor per day
STATUSES = ("pending", "published", "declined", "withdrawn")
SITE_HOSTS = ("wherempsstand.com", "www.wherempsstand.com")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# addresses on this site that a statement can sit beside: a member's page, a briefing, a topic briefing, a debate,
# a bill, a party group, an early day motion, a committee, an inquiry, evidence, a devolved parliament's day
ITEM_PATH = re.compile(r"^/(people/\d+|b/[a-km-z2-9]{10}|topics/[\w-]+|debates/\d{4}-\d{2}-\d{2}/[\w-]+/[\w-]+|"
                       r"bills/[\w-]+|groups/[\w-]+/[\w-]+|edms/\d+|committees/\d+|inquiries/\d+|evidence/\d+|"
                       r"devolved/[\w-]+/[\w.-]+)$")
PERSON_PATH = re.compile(r"^/people/(\d+)$")


class StatementError(ValueError):
    """A statement that can't be accepted as sent; the message says why, in words for the person sending it."""


def word_count(text: str) -> int:
    return len(text.split())


def item_path(address: str) -> str:
    """The path of an item on this site, from what someone pasted: a full address or a path. A member's page is kept
    as /people/<number>, without the name after it, so it matches however the page was reached."""
    address = (address or "").strip()
    if "://" not in address:  # "wherempsstand.com/people/…", or a path
        address = ("https://" if address.split("/")[0] in SITE_HOSTS else "https://wherempsstand.com/") \
            + address.lstrip("/")
    parsed = urlparse(address)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in SITE_HOSTS:
        raise StatementError("Please give the address of the page on wherempsstand.com that the statement is about.")
    path = re.sub(r"/+$", "", parsed.path) or "/"
    person = re.match(r"^/people/(\d+)(?:/[\w-]*)?$", path)
    if person:
        path = f"/people/{person.group(1)}"
    if not ITEM_PATH.match(path):
        raise StatementError("That address isn't a page a statement can sit beside: please give the address of a "
                             "member's page, a briefing or another page that names them.")
    return path


def person_of(path: str) -> int | None:
    m = PERSON_PATH.match(path)
    return int(m.group(1)) if m else None


def clean_link(link: str | None) -> str | None:
    link = (link or "").strip()
    if not link:
        return None
    parsed = urlparse(link)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or any(c in link for c in "\"'<> "):
        raise StatementError("The link to the record should be a full web address, starting https://.")
    return link


def clean_text(text: str) -> str:
    """Line endings made plain and runs of blank lines closed up; the words themselves are never changed."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def check(name: str, role: str, text: str, email: str | None, *, need_email: bool = True) -> None:
    if len(name.strip()) < 2:
        raise StatementError("Please give the name the statement is from.")
    if len(role.strip()) < 2:
        raise StatementError("Please give their role, for example \"MP for Leeds Central\" or \"Chief of staff to …\".")
    words = word_count(text)
    if words < 5:
        raise StatementError("Please write the statement: a sentence or more.")
    if words > MAX_WORDS:
        raise StatementError(f"A statement can be up to {MAX_WORDS} words. This one is {words}.")
    if need_email and not EMAIL.match((email or "").strip()):
        raise StatementError("Please give an email address, so we can confirm who the statement is from.")


def recent_count(state: StateDB, visitor: str | None) -> int:
    if not visitor:
        return 0
    since = (datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds")
    with state.connect() as con:
        return con.execute("SELECT count(*) FROM statements WHERE visitor = ? AND at >= ?",
                           (visitor, since)).fetchone()[0]


def add_statement(state: StateDB, *, name: str, role: str, item: str, text: str, link: str | None, email: str,
                  visitor: str | None = None) -> int:
    """Store a statement as pending. Raises StatementError if it can't be accepted."""
    text = clean_text(text)
    check(name, role, text, email)
    path = item_path(item)
    with state.connect() as con:
        cur = con.execute("INSERT INTO statements (at, name, role, item_path, person, text, link, email, visitor) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (utcnow(), name.strip()[:120], role.strip()[:200], path, person_of(path), text,
                           clean_link(link), email.strip()[:200], visitor))
        sid = int(cur.lastrowid)
    forget_old_emails(state)
    return sid


def months_ago(now: datetime, months: int) -> datetime:
    """The same moment ``months`` calendar months earlier (the last day of the month when that day is missing)."""
    y, m = divmod(now.year * 12 + now.month - 1 - months, 12)
    m += 1
    for day in range(now.day, 27, -1):
        try:
            return now.replace(year=y, month=m, day=day)
        except ValueError:
            continue
    return now.replace(year=y, month=m, day=min(now.day, 28))


def forget_old_emails(state: StateDB, now: datetime | None = None) -> int:
    """Delete the email address of every statement withdrawn or declined more than three months ago."""
    cutoff = months_ago(now or datetime.now(UTC), EMAIL_KEPT_MONTHS).isoformat(timespec="seconds")
    with state.connect() as con:
        cur = con.execute("UPDATE statements SET email = NULL, visitor = NULL WHERE email IS NOT NULL "
                          "AND status IN ('withdrawn', 'declined') AND closed_at < ?", (cutoff,))
    return cur.rowcount


def list_statements(state: StateDB, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    """Every statement, with its email and private notes: for the owner only."""
    forget_old_emails(state)
    sql = ("SELECT id, at, name, role, item_path, person, text, link, email, status, published_at, updated_at, "
           "closed_at, reason, checked FROM statements")
    args: tuple = ()
    if status:
        sql += " WHERE status = ?"
        args = (status,)
    with state.connect() as con:
        return [dict(r) for r in con.execute(sql + " ORDER BY at DESC LIMIT ?", (*args, limit)).fetchall()]


def get_statement(state: StateDB, sid: int) -> dict[str, Any] | None:
    with state.connect() as con:
        row = con.execute("SELECT * FROM statements WHERE id = ?", (sid,)).fetchone()
    return dict(row) if row else None


def published(state: StateDB, *, paths: list[str] | tuple[str, ...] = (), person: int | None = None
              ) -> list[dict[str, Any]]:
    """Published statements for some items (a briefing at /b/<id> and its topic address, say) or one member, oldest
    first, with only what is shown on the site: never the email address or the owner's private notes."""
    where: list[str] = []
    args: list[Any] = []
    for path in paths[:5]:
        try:
            args.append(item_path(path))
        except StatementError:
            continue
        where.append("item_path = ?")
    if person is not None:
        where.append("person = ?")
        args.append(person)
    if not where:
        return []
    with state.connect() as con:
        rows = con.execute("SELECT id, name, role, item_path, person, text, link, published_at FROM statements "
                           f"WHERE status = 'published' AND ({' OR '.join(where)}) ORDER BY published_at, id",
                           args).fetchall()
    return [{"id": r["id"], "name": r["name"], "role": r["role"], "item": r["item_path"], "person": r["person"],
             "text": r["text"], "link": r["link"], "date": r["published_at"][:10]} for r in rows]


def publish_statement(state: StateDB, sid: int, *, person: int | None = None, checked: str | None = None) -> bool:
    """Publish a pending statement as written, once the owner has confirmed who sent it (``checked`` notes how,
    privately). ``person`` is the member it is about, when its address isn't their own page."""
    s = get_statement(state, sid)
    if not s or s["status"] != "pending":
        return False
    with state.connect() as con:
        con.execute("UPDATE statements SET status = 'published', published_at = ?, person = ?, checked = ? "
                    "WHERE id = ?", (utcnow(), person if person is not None else s["person"],
                                     (checked or "").strip() or None, sid))
    return True


def decline_statement(state: StateDB, sid: int, reason: str) -> bool:
    """Decline a pending statement, or take down a published one that breaks the rules, with the reason (sent to the
    author, never shown on the site)."""
    if len((reason or "").strip()) < 3:
        raise StatementError("Please give the reason: it is sent to the author.")
    with state.connect() as con:
        cur = con.execute("UPDATE statements SET status = 'declined', closed_at = ?, reason = ? "
                          "WHERE id = ? AND status IN ('pending', 'published')", (utcnow(), reason.strip(), sid))
    return cur.rowcount > 0


def update_statement(state: StateDB, sid: int, *, text: str | None = None, link: str | None = None,
                     name: str | None = None, role: str | None = None, clear_link: bool = False) -> bool:
    """Replace a statement's words (or link, name or role) at the author's request. A published statement is shown
    with the date of the new version."""
    s = get_statement(state, sid)
    if not s or s["status"] not in ("pending", "published"):
        return False
    new = {"text": clean_text(text) if text is not None else s["text"], "name": (name or s["name"]).strip(),
           "role": (role or s["role"]).strip(),
           "link": None if clear_link else (clean_link(link) if link is not None else s["link"])}
    check(new["name"], new["role"], new["text"], None, need_email=False)
    now = utcnow()
    with state.connect() as con:
        con.execute("UPDATE statements SET text = ?, name = ?, role = ?, link = ?, updated_at = ?, "
                    "published_at = CASE WHEN status = 'published' THEN ? ELSE published_at END WHERE id = ?",
                    (new["text"], new["name"], new["role"], new["link"], now, now, sid))
    return True


def withdraw_statement(state: StateDB, sid: int, note: str | None = None) -> bool:
    """Take a statement down at its author's request. Its email address is deleted three months later."""
    with state.connect() as con:
        cur = con.execute("UPDATE statements SET status = 'withdrawn', closed_at = ?, reason = ? "
                          "WHERE id = ? AND status IN ('pending', 'published')",
                          (utcnow(), (note or "").strip() or "Withdrawn by the author.", sid))
    return cur.rowcount > 0
