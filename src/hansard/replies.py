"""Readers' reports and the right of reply (the charity's impartiality policy, section 7), with this site's own
addresses, limits and words.

A member of Parliament, a peer, a member of a devolved parliament, an organisation such as a party group, or
someone acting for them, can ask to add a short statement in their own words beside what the site says about them.
It is shown beside the item it is about and on the member's own page, headed "Statement from [name], [date]" and
followed by "In their own words. Not checked or endorsed by Where MPs Stand."

The rules and the data are the Where They Stand sites' shared ones (wts-core, ``wts_core.replies``; its
docs/adr/0008): what a statement may be, its life (received, then published or declined; a published one withdrawn
at its author's request or removed by the site for breaking the rules), the log of every step, the confirming
address deleted three months after a statement ends, reports and their closing, the corrections log, and hiding an
item while a concern about it is decided. They are kept in the state database's ``wts_reports``,
``wts_statements`` and the tables beside them. What is here is what is this site's own: where a statement may sit,
its limits, and every word a sender is shown.

A statement is never part of the record: nothing that searches the record or writes briefings reads it
(tests/test_statements.py and tests/test_privacy.py check both). It is published as written or declined with
reasons; it is never edited, except to remove something the rules don't allow, with the author's agreement.

The email address given is used only to confirm who sent it and to let them update or withdraw it. It is kept while
the statement is waiting or shown, and deleted three months after the statement is withdrawn, declined or removed
(the privacy notice).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from wts_core import replies
from wts_core.ports import SYSTEM_CLOCK, Clock
from wts_core.replies import Item, NotDone, Refused, Replies, ReplyMessages, ReplyRules
from wts_core.state import StateDB as CoreDB
from wts_core.usage import SavedBriefings

from hansard.config import SITE

MAX_WORDS = 150
DAILY_STATEMENTS = 5   # statements per visitor per day
DAILY_REPORTS = 10     # reports per visitor per day
STATUSES = replies.STATUSES  # pending, published, declined, withdrawn, removed
SITE_HOSTS = SITE.hosts
# addresses on this site that a statement can sit beside: a member's page, a briefing, a topic briefing, a debate,
# a bill, a party group, an early day motion, a committee, an inquiry, evidence, a devolved parliament's day
ITEM_PATH = re.compile(r"^/(people/\d+|b/[a-km-z2-9]{10}|topics/[\w-]+|debates/\d{4}-\d{2}-\d{2}/[\w-]+/[\w-]+|"
                       r"bills/[\w-]+|groups/[\w-]+/[\w-]+|edms/\d+|committees/\d+|inquiries/\d+|evidence/\d+|"
                       r"devolved/[\w-]+/[\w.-]+)$")
PERSON_PATH = re.compile(r"^/people/(\d+)$")
# The values are kept as they were (old links and saved reports use them); the labels are the policy's routes.
KINDS = {"correction": "Something is wrong", "complaint": "You've been characterised unfairly",
         "takedown": "A private person's name or details to remove", "general": "Something else"}

REPLY_RULES = ReplyRules(
    min_words=5,
    max_words=MAX_WORDS,
    max_chars=2000,  # the form's own limit, which it applies first
    name_max=120,
    role_max=200,
    link_max=500,
    email_max=200,
    daily_statements=DAILY_STATEMENTS,
    daily_reports=DAILY_REPORTS,
    about_required=False,  # a statement beside a briefing or a debate may be published before a member is set
    report_kinds={"correction": "correction", "complaint": "complaint", "takedown": replies.TAKEDOWN,
                  "general": "general"},
    report_kind_required=True,
    report_min=10,
    report_max=4000,
    page_max=200,
    contact_max=200,
    # What the site's forms let through, field by field (the request bodies in hansard/api/app.py,
    # StatementBody and ReportBody, refuse anything longer before the right of reply sees it): no more than each
    # limit above, so the six "too long" refusals can never be reached and have no words here (wts-core 0.3.0;
    # the package checks the two against each other when the site starts, and tests/test_statements.py holds
    # these figures to the request bodies')
    form_max={"text": 2000, "name": 120, "role": 200, "link": 500, "report": 4000, "contact": 200},
)

NO_BRIEFING = ("There is no briefing at that address: please check the address of the briefing the statement is "
               "about.")
WITHDRAWN = "That briefing has been withdrawn, so a statement can't sit beside it."
NO_MEMBER = ("There is no member's page at that address: please check the address of the page the statement is "
             "about.")
NOT_THIS_SITE = "Please give the address of the page on wherempsstand.com that the statement is about."
NOT_AN_ITEM = ("That address isn't a page a statement can sit beside: please give the address of a member's page, "
               "a briefing or another page that names them.")

REPLY_MESSAGES = ReplyMessages(
    too_few_words="Please write the statement: a sentence or more.",
    too_many_words="A statement can be up to {max} words. This one is {n}.",
    name_missing="Please give the name the statement is from.",
    role_missing="Please give their role, for example \"MP for Leeds Central\" or \"Chief of staff to …\".",
    bad_link="The link to the record should be a full web address, starting https://.",
    bad_email="Please give an email address, so we can confirm who the statement is from.",
    item_not_shown="That page isn't being shown at the moment, so a statement can't sit beside it.",
    # a briefing hidden while a concern about it is decided (the one thing this site hides), said as such
    item_hidden=("That briefing is not being shown while a concern about it is looked into, so a statement can't "
                 "sit beside it for now."),
    daily_statements="Thank you, we have your statements. Please email us if there is more to add.",
    daily_reports="Thank you, we have your reports. Please email us if there is more to add.",
    report_kind="Please choose what the report is about.",
    report_too_short="Please say what is wrong: a sentence or more.",
    lapsed="No decision was made in time; please send it again.",
    # the family glossary's heading and disclaimer
    heading="Statement from {name}, {date}",
    disclaimer="In their own words. Not checked or endorsed by {site}.",
    record_link="The record it points to",
    site=SITE.name,
)


def item_path(address: str) -> str:
    """The path of an item on this site, from what someone pasted: a full address or a path. A member's page is kept
    as /people/<number>, without the name after it, so it matches however the page was reached."""
    address = (address or "").strip()
    if "://" not in address:  # "wherempsstand.com/people/…", or a path
        address = ("https://" if address.split("/")[0] in SITE_HOSTS else "https://wherempsstand.com/") \
            + address.lstrip("/")
    parsed = urlparse(address)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in SITE_HOSTS:
        raise Refused(NOT_THIS_SITE, "bad_item")
    path = re.sub(r"/+$", "", parsed.path) or "/"
    person = re.match(r"^/people/(\d+)(?:/[\w-]*)?$", path)
    if person:
        path = f"/people/{person.group(1)}"
    if not ITEM_PATH.match(path):
        raise Refused(NOT_AN_ITEM, "bad_item")
    return path


def person_of(path: str) -> int | None:
    m = PERSON_PATH.match(path)
    return int(m.group(1)) if m else None


def member_number(typed: str) -> str:
    """Whom a statement speaks for, as the owner types it (``--person 10669``): a member's number, as in
    /people/<number>."""
    number = (typed or "").strip()
    if not number.isdigit():
        raise ValueError("A member is given by their number, as in /people/<number>.")
    return str(int(number))


class SiteItems:
    """Where a statement may sit on this site (``replies.Items``).

    Checked that it is there: a briefing (it must exist and not be withdrawn) and a member's page. Not checked:
    topics, debates, bills, party groups, early day motions, committees, inquiries, evidence and devolved sittings,
    each of which is looked up in its own store; the owner sees the address before a statement is published.
    ``member_known(number)`` says whether there is a member's page of that number (None: it can't be told just
    now, and the statement is kept for the owner to check)."""

    def __init__(self, briefings: SavedBriefings, member_known: Callable[[int], bool | None] | None = None) -> None:
        self._briefings = briefings
        self._member_known = member_known

    def resolve(self, pasted: str) -> Item:
        path = item_path(pasted)
        number = person_of(path)
        return Item(address=path, about=str(number) if number is not None else None)

    def why_not(self, item: Item) -> str | None:
        """Why a statement can't sit beside the item, in words for its sender, when it is known not to be there."""
        path = item.address
        if path.startswith("/b/"):
            b = self._briefings.get(path.removeprefix("/b/"))
            if b is None:
                return NO_BRIEFING
            if b.withdrawn_at:
                return WITHDRAWN
        number = person_of(path)
        if number is not None and self._member_known is not None and self._member_known(number) is False:
            return NO_MEMBER
        return None

    def shown(self, item: Item) -> bool:
        """Whether the item is there to sit beside. For a sender the reason it is not is said in the site's more
        exact words (``Refused``); a hidden item is the package's own to refuse."""
        why = self.why_not(item)
        if why is not None:
            raise Refused(why, "item_not_shown")
        return True


BRIEFING_PATH = re.compile(r"^/b/([a-km-z2-9]{10})$")
ONLY_BRIEFINGS = ("only a saved briefing can be hidden: give its address, /b/ and its ten characters (a page of the "
                  "official record is not hidden here: docs/OPERATIONS.md, \"Hiding a briefing\")")


def hideable(briefings: SavedBriefings) -> Callable[[str], str]:
    """This site's one spelling for what can be hidden while a concern about it is decided, whatever the owner
    typed (``wts_core.replies.Replies(hideable=…)``): a saved briefing, filed as ``/b/<id>`` (its full address,
    with or without a trailing slash, will do). A topic's briefing is a saved briefing, and is hidden by its own
    address. Anything else, and a briefing that is not there, is refused in words for the owner: a command that
    takes an address must not say "hidden" for nothing."""

    def one_spelling(typed: str) -> str:
        try:
            path = item_path(typed)
        except Refused:
            raise NotDone(ONLY_BRIEFINGS) from None
        found = BRIEFING_PATH.match(path)
        if found is None:
            raise NotDone(ONLY_BRIEFINGS)
        if briefings.get(found.group(1)) is None:
            raise NotDone(f"there is no briefing at {path}")
        return path

    return one_spelling


def hidden_note(replies_: Replies, briefing_id: str | None) -> str | None:
    """The note shown in a saved briefing's place while it is hidden, or None when it is shown."""
    if not briefing_id or not BRIEFING_PATH.match(f"/b/{briefing_id}"):
        return None
    row = replies_.hidden(f"/b/{briefing_id}")
    return row["note"] if row else None


def open_replies(core: CoreDB, briefings: SavedBriefings, member_known: Callable[[int], bool | None] | None = None,
                 clock: Clock = SYSTEM_CLOCK) -> Replies:
    """The site's reports and statements, over its state database and its saved briefings."""
    return Replies(core, items=SiteItems(briefings, member_known), rules=REPLY_RULES, messages=REPLY_MESSAGES,
                   clock=clock, hideable=hideable(briefings))


def checked_note(log: list[dict[str, Any]]) -> str | None:
    """How a statement's sender was confirmed: the note of its publishing, which the package keeps in the log and
    nowhere else (for a statement from before the log, the note the import brought across)."""
    return next((e["note"] for e in reversed(log) if e["action"] in ("published", "imported") and e["note"]), None)


def as_listed(replies_: Replies, statement: dict[str, Any]) -> dict[str, Any]:
    """A statement for the owner, under the names the admin page and the commands have always read."""
    about = statement.get("about")
    return {"id": statement["id"], "at": statement["at"], "name": statement["name"], "role": statement["role"],
            "item_path": statement["item"], "person": int(about) if about and str(about).isdigit() else None,
            "text": statement["text"], "link": statement["link"], "email": statement["email"],
            "status": statement["status"], "published_at": statement["published_at"],
            "updated_at": statement["updated_at"], "closed_at": statement["closed_at"],
            "reason": statement["reason"], "checked": checked_note(replies_.log(statement["id"]))}


def as_shown(statement: dict[str, Any]) -> dict[str, Any]:
    """A published statement under the names this site's pages have always read: what the public sees of it, and
    nothing else (never its author's email address, the reason it ended or the owner's log)."""
    about = statement.get("about")
    return {"id": statement["id"], "name": statement["name"], "role": statement["role"], "item": statement["item"],
            "person": int(about) if about and str(about).isdigit() else None, "text": statement["text"],
            "link": statement["link"], "date": statement["date"]}


def published(replies_: Replies, *, paths: list[str] | tuple[str, ...] = (), person: int | None = None
              ) -> list[dict[str, Any]]:
    """Published statements for some items (a briefing at /b/<id> and its topic address, say) or one member, oldest
    first, with only what is shown on the site."""
    items = []
    for path in paths[:5]:
        try:
            items.append(Item(address=item_path(path)))
        except Refused:
            continue
    if not items and person is None:
        return []
    return [as_shown(s) for s in replies_.published(items=items, about=str(person) if person is not None else None)]


def corrections(replies_: Replies, briefings: SavedBriefings) -> list[dict[str, Any]]:
    """The public log: withdrawn briefings and public notes on closed reports, newest first. Of a closed report
    only the date, the kind and what the owner chose to say publicly: never what its sender typed, the page they
    named or a briefing they reported. Every entry there is: the log is not cut short."""
    withdrawn = [{"date": b.withdrawn_at, "briefing_id": b.id, "question": b.question, "note": b.withdrawn_reason}
                 for b in briefings.withdrawn()]
    entries = replies_.corrections(limit=1_000_000, withdrawn=withdrawn)
    return [{"date": e["date"], "kind": e["kind"], "briefing_id": e.get("briefing_id"),
             "question": e.get("question"), "note": e["note"]} for e in entries]
