"""The owner's commands for reports, statements under the right of reply and hidden briefings: what each is called
on this site, and every line it prints.

The commands themselves are the Where They Stand sites' shared ones (wts-core, ``wts_core.cli``). The statements
commands keep the names, options and lines they have always had here (``hansard statements list``, ``publish``,
``decline``, ``update``, ``withdraw``); ``statements remove``, ``show`` and ``log`` and the ``reports`` commands
came with the shared package.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from wts_core.cli import RepliesCommands, RepliesWording, replies_commands
from wts_core.ports import SYSTEM_CLOCK, Clock
from wts_core.replies import Replies
from wts_core.usage import BriefingArchive, SqliteAnswerCache

from hansard.api.briefings import new_briefing_id
from hansard.config import Settings
from hansard.replies import BRIEFING_PATH, KINDS, checked_note, member_number, open_replies
from hansard.store.wts import open_core


def replies_for(settings: Settings, clock: Clock = SYSTEM_CLOCK, path: Path | None = None) -> Replies:
    """The site's reports and statements, for a command: over the state database and its saved briefings. (Whether
    a member's page exists is not looked up here: the owner sees the address.)"""
    core = open_core(settings, clock, path)
    return open_replies(core, BriefingArchive(core, new_id=new_briefing_id, clock=clock), clock=clock)


def tidy_replies(settings: Settings, clock: Clock = SYSTEM_CLOCK, path: Path | None = None) -> dict[str, int]:
    """Delete what the right of reply no longer keeps: the confirming address of a statement that ended three
    months ago, the contact details of a report left open that long, what nobody decided in three months, and
    earlier days' counts. Each update does this, so the privacy notice's three months never wait for a visitor."""
    desk = replies_for(settings, clock, path)
    emails = desk.forget_old_emails()
    lapsed, contacts = desk.lapse_undecided()
    return {"emails": emails, "lapsed": lapsed, "contacts": contacts, "counts": desk.forget_old_counts()}


# the lines


def statement_lines(s: Mapping[str, Any]) -> list[str]:
    """A statement as ``hansard statements list`` has always shown it."""
    lines = [f"#{s['id']}  {s['status']}  received {s['at'][:10]}"
             + (f"  published {s['published_at'][:10]}" if s["published_at"] else "")
             + (f"  closed {s['closed_at'][:10]}" if s["closed_at"] else ""),
             f"  From: {s['name']}, {s['role']}  <{s['email'] or 'email deleted'}>",
             f"  About: {s['item']}" + (f"  (member {s['about']})" if s["about"] else "  (no member set)")]
    if s["link"]:
        lines.append(f"  Link: {s['link']}")
    checked = checked_note(list(s.get("log") or []))
    if checked:
        lines.append(f"  Checked: {checked}")
    if s["reason"]:
        lines.append(f"  Reason: {s['reason']}")
    lines.append("  " + s["text"].replace("\n", "\n  ") + "\n")
    return lines


def log_lines(e: Mapping[str, Any]) -> list[str]:
    """One thing done to a statement: when, what, the note, and for an update the words it replaced."""
    lines = [f"  {e['at'][:16].replace('T', ' ')}  {e['action']}" + (f": {e['note']}" if e["note"] else "")]
    for field, was in (e.get("previous") or {}).items():
        lines.append(f"      {field} was: " + str(was).replace("\n", "\n      "))
    return lines


def report_lines(r: Mapping[str, Any]) -> list[str]:
    """A report as ``hansard reports list`` shows it."""
    about = f"/b/{r['briefing_id']}" if r["briefing_id"] else (r["page"] or "(no page given)")
    lines = [f"#{r['id']}  {r['status']}  received {r['at'][:10]}"
             + (f"  closed {r['closed_at'][:10]}" if r["closed_at"] else ""),
             f"  Kind: {KINDS.get(r['kind'], r['kind'] or 'not said')}",
             f"  About: {about}",
             f"  Reply to: {r['contact'] or '(no contact details)'}"]
    if r["resolution"]:
        lines.append(f"  What was done: {r['resolution']}")
    if r["public_note"]:
        lines.append(f"  Public note: {r['public_note']}")
    lines.append("  " + r["message"].replace("\n", "\n  ") + "\n")
    return lines


WORDING = RepliesWording(
    not_done="Not done: {message} (see `hansard statements list` and `hansard reports list --all`).",
    refused="{message}",
    errors_to_stderr=True,
    statement=statement_lines,
    log_entry=log_lines,
    report=report_lines,
    published="published #{id}",
    declined="declined #{id}: tell the author why; their email address is deleted in three months",
    updated="updated #{id}",
    withdrawn="withdrawn #{id}; the email address is deleted in three months",
    removed="removed #{id}: tell the author which rule it broke; their email address is deleted in three months",
    closed="closed report #{id} as {status}; its contact details are deleted",
    hid="hidden: {item} (its address now shows the note in its place; `hansard unhide {item}` shows it again)",
    unhid="shown again: {item}",
    hidden_item=lambda h: [f"{h['item']}  hidden since {h['at'][:10]}", f"    shown in its place: {h['note']}"],
    waiting="Waiting for a decision: {reports} report(s), {statements} statement(s); the oldest has waited "
            "{oldest_working_days} working day(s). Hidden for now: {hidden} briefing(s).",
    nothing_waiting="Nothing is waiting for a decision. Hidden for now: {hidden} briefing(s).",
    lapsing=lambda w: (f"At the next tidy, {w['statements']} statement(s) nobody has decided would be declined "
                       f"as lapsed, and {w['reports']} open report(s) would lose their contact details (those sent "
                       f"before {w['before']})."
                       + (f" Statements: {w['statement_ids']}." if w["statements"] else "")
                       + (f" Reports: {w['report_ids']}." if w["reports"] else "")),
    tidied="Right of reply: {lapsed} statement(s) nobody decided in three months declined, {contacts} old open "
           "report(s) lost their contact details, {emails} confirming address(es) deleted three months after "
           "their statements ended.",
)

COMMANDS = RepliesCommands(
    listed=("pending",),  # `hansard statements list` shows what is waiting, as it always has
    about_option="--person",
    withdraw_note="Withdrawn by the author.",
    outcome="resolved",
    # `hansard hide`, `unhide`, `hidden`, `waiting` and `would-lapse` came with the shared package, under its own
    # names; the nightly housekeeping is `hansard tidy` (hansard/cli.py), so the package's own command for it is
    # left out
    tidy=None,
    help={
        "statements": "Statements beside items (the right of reply): list, publish, decline, update, withdraw and "
                      "remove them.",
        "reports": "Reports sent through \"Report a problem\": list them, and close each with what was done.",
        "statements_list": "Statements and their state, with the email address to confirm who sent each (pending "
                           "first by default).",
        "statements_list.status": "pending, published, declined, withdrawn, removed or all",
        "statements_publish": "Publish a pending statement as written, once you have confirmed who sent it.",
        "statements_publish.checked": "how you confirmed who sent it, e.g. \"replied from their parliament.uk "
                                      "address\" (kept privately; leave their email address out of it)",
        "statements_publish.about": "the member it is about (their number, as in /people/<number>), when its "
                                    "address isn't their page",
        "statements_decline": "Decline a pending statement, with the reason.",
        "statements_decline.reason": "why: sent to the author, never shown",
        "statements_update": "Replace a statement at its author's request (published ones are shown with the new "
                             "date).",
        "statements_update.text_file": "a file holding the author's new words, exactly as sent",
        "statements_update.link": "a new link to the record; \"\" removes it",
        "statements_withdraw": "Take a statement down at its author's request.",
        "statements_withdraw.note": "a private note, e.g. the date they asked",
        "statements_remove": "Take down a published statement that breaks the rules, with the reason.",
        "statements_remove.reason": "the rule it broke: sent to the author, never shown",
        "hide": "Hide a saved briefing while a concern about it is decided: its address then shows the note in its "
                "place. Not a withdrawal: `hansard unhide` shows it again, and nothing is listed on the corrections "
                "page.",
        "hide.note": "what the briefing's address shows in its place, e.g. \"This briefing is not shown while a "
                     "concern about it is looked into.\"",
        "unhide": "Show a hidden briefing again.",
        "hidden": "The briefings hidden now, longest hidden first.",
        "waiting": "What is waiting for a decision (reports, statements), how long the oldest has waited, and how "
                   "many briefings are hidden.",
        "would_lapse": "What the next tidy would do to what has waited three months, read and not done: the "
                       "statements it would decline as lapsed and the open reports whose contact details it would "
                       "delete, by number. Never an address.",
    },
)


def reply_commands(settings: Callable[[], Settings], clock: Callable[[], Clock] = lambda: SYSTEM_CLOCK) -> Any:
    """The commands, for ``hansard.cli`` to add to the site's own. ``settings`` and ``clock`` are read when a
    command runs."""
    def changed(row: Mapping[str, Any], kind: str) -> None:
        """After a change a page may show. When a briefing has just been hidden, its cached copies are dropped, so
        that the next reader with the same question is not played what was hidden (the site checks again as it
        plays one; this saves the reader the wait)."""
        found = BRIEFING_PATH.match(str(row.get("item") or ""))
        if kind == "hidden" and found:
            SqliteAnswerCache(open_core(settings(), clock()), clock=clock()).delete_briefing(found.group(1))

    return replies_commands(lambda: replies_for(settings(), clock()), COMMANDS, WORDING, about=member_number,
                            changed=changed)
