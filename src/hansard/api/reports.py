"""Report a problem, and the public corrections log.

Anyone can report something wrong (in a briefing, or in a label, grouping, connection or summary the site shows), an
unfair characterisation, a private person's name that should not be shown, or anything else: the routes of the
charity's impartiality policy, section 7.1. A third route, a statement in someone's own words, is in statements.py.
Reports are private. When one is closed, the operator can add a short public note; notes, and every withdrawn briefing,
appear on the corrections page, so readers can see what has been put right.

Contact details are optional, used only to reply, and deleted when the report is closed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from hansard.store.state import StateDB, utcnow

# The values are kept as they were (old links and saved reports use them); the labels are the policy's routes.
KINDS = {"correction": "Something is wrong", "complaint": "You've been characterised unfairly",
         "takedown": "A private person's name or details to remove", "general": "Something else"}
CLOSED = ("resolved", "dismissed")
DAILY_LIMIT = 10  # reports per visitor per day


def recent_count(state: StateDB, visitor: str | None) -> int:
    if not visitor:
        return 0
    since = (datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds")
    with state.connect() as con:
        return con.execute("SELECT count(*) FROM reports WHERE visitor = ? AND at >= ?", (visitor, since)).fetchone()[0]


def add_report(state: StateDB, kind: str, message: str, briefing_id: str | None, contact: str | None,
               visitor: str | None, page: str | None = None) -> int:
    with state.connect() as con:
        cur = con.execute("INSERT INTO reports (at, kind, briefing_id, page, message, contact, visitor) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?)", (utcnow(), kind, briefing_id, page, message, contact or None,
                                                           visitor))
        return int(cur.lastrowid)


def list_reports(state: StateDB, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Reports, newest first, but open requests to remove a private person's name or details before everything
    else (the impartiality policy, 7.1: they are the ones that cannot wait)."""
    sql = ("SELECT id, at, kind, briefing_id, page, message, contact, status, closed_at, resolution, public_note "
           "FROM reports")
    args: tuple = ()
    if status:
        sql += " WHERE status = ?"
        args = (status,)
    with state.connect() as con:
        return [dict(r) for r in con.execute(
            sql + " ORDER BY (status = 'open' AND kind = 'takedown') DESC, at DESC LIMIT ?", (*args, limit)).fetchall()]


def close_report(state: StateDB, report_id: int, status: str, resolution: str, public_note: str | None) -> bool:
    """Close a report. Its contact details are deleted: they were only ever for replying."""
    with state.connect() as con:
        cur = con.execute("UPDATE reports SET status = ?, closed_at = ?, resolution = ?, public_note = ?, contact = NULL, "
                          "visitor = NULL WHERE id = ?",
                          (status, utcnow(), resolution, (public_note or "").strip() or None, report_id))
    return cur.rowcount > 0


def corrections(state: StateDB, limit: int = 200) -> list[dict[str, Any]]:
    """The public log: withdrawn briefings and public notes on closed reports, newest first."""
    with state.connect() as con:
        withdrawn = con.execute("SELECT id, withdrawn_at, question, withdrawn_reason FROM briefings "
                                "WHERE withdrawn_at IS NOT NULL").fetchall()
        notes = con.execute("SELECT closed_at, kind, briefing_id, public_note FROM reports "
                            "WHERE public_note IS NOT NULL AND status IN ('resolved', 'dismissed')").fetchall()
    out = [{"date": r["withdrawn_at"][:10], "kind": "withdrawn", "briefing_id": r["id"], "question": r["question"],
            "note": r["withdrawn_reason"]} for r in withdrawn]
    out += [{"date": r["closed_at"][:10], "kind": r["kind"], "briefing_id": r["briefing_id"], "question": None,
             "note": r["public_note"]} for r in notes]
    out.sort(key=lambda c: c["date"], reverse=True)
    return out[:limit]
