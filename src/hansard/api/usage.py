"""The site's own counts and the owner's figures: feedback, cookie-free visit counts, how often each question is
asked, the note on what briefings cost, and what the admin page shows of spending and failures.

Limits, cost, the spending caps, the answer cache and the notes of failed briefings are the Where They Stand sites'
shared ones (wts-core, ``wts_core.usage``; built for this site by ``desk``, below). What is here is what only this
site has, kept in the site's own tables as it always was, and the admin page's reading of the shared spending
record in this site's own words.

The site is public, so limits apply per visitor as well as overall. A visitor is identified by a salted hash of
their IP address; the address itself is never stored.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from wts_core.briefing import DeskParts, desk_parts
from wts_core.ports import SYSTEM_CLOCK, SYSTEM_RANDOM, Clock, Random
from wts_core.state import StateDB as CoreDB
from wts_core.usage import FailureLog, Spending, visitor_code

from hansard.api.briefings import new_briefing_id
from hansard.config import Settings
from hansard.messages import MESSAGES
from hansard.startup import AT_ONCE_PER_VISITOR, FORGET_IDLE_VISITORS_OVER, models_named
from hansard.store.state import StateDB, utcnow

__all__ = ["FORGET_IDLE_VISITORS_OVER", "desk", "models_named"]  # and the functions below


def desk(settings: Settings, core: CoreDB, clock: Clock = SYSTEM_CLOCK, random: Random = SYSTEM_RANDOM) -> DeskParts:
    """Everything the flow of a briefing is made of, from ``[usage]``: prices, limits, the spending record, the
    caps, the per-minute limiter, the places in progress, the answer cache, saved briefings (at ten characters of
    the site's own alphabet) and the notes of failed briefings, on the one clock. This is also the start-up check:
    every limit, price and message the flow needs, with whatever is missing named together in one error."""
    return desk_parts(settings, core, MESSAGES, new_id=lambda chance: new_briefing_id(chance),
                      at_once_per_visitor=AT_ONCE_PER_VISITOR, forget_over=FORGET_IDLE_VISITORS_OVER,
                      keep_failures=True, require_prices_for=models_named(settings), clock=clock, random=random)


def visitor_key(core: CoreDB, address: str | None, clock: Clock = SYSTEM_CLOCK,
                random: Random = SYSTEM_RANDOM) -> str:
    """A code for a visitor's IP address, used for daily limits and visitor counts; the address itself is not kept.

    The secret mixed into the code changes every day (UTC) and old ones are deleted, so codes cannot be linked
    across days or turned back into an address.
    """
    return visitor_code(core, address, forget_undated_salt=True, clock=clock, random=random)


def _supporter_key(visitor: str) -> str:
    return f"supporter:{datetime.now(UTC).strftime('%Y-%m-%d')}:{visitor}"


def mark_supporter(state: StateDB, visitor: str) -> None:
    """The visitor went through to the donation page: no more reminders today (visitor codes change daily)."""
    with state.connect() as con:
        con.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, '1')", (_supporter_key(visitor),))
        con.execute("DELETE FROM kv WHERE key LIKE 'supporter:%' AND key NOT LIKE ?",
                    (f"supporter:{datetime.now(UTC).strftime('%Y-%m-%d')}:%",))


def support_reminder_due(spending: Spending, state: StateDB, visitor: str | None, every: int) -> int:
    """After every ``every``-th new briefing a visitor has had written today, how many that is (else 0); never
    once they have gone through to donate today."""
    if not visitor or every <= 0:
        return 0
    n = spending.briefings_today(visitor=visitor)
    return n if n > 0 and n % every == 0 and state.get(_supporter_key(visitor)) is None else 0


# feedback and cookie-free visit counts

def record_feedback(state: StateDB, briefing_key: str | None, question: str, useful: bool,
                    visitor: str | None) -> None:
    with state.connect() as con:
        con.execute("INSERT INTO feedback (at, briefing_key, question, useful, visitor) VALUES (?, ?, ?, ?, ?)",
                    (utcnow(), briefing_key, question[:500], int(useful), visitor))


def alerts(parts: DeskParts, state: StateDB) -> list[dict[str, str]]:
    """Things the operator should know about now: recent failures and spending close to the caps."""
    out: list[dict[str, str]] = []
    since = (parts.clock.now() - timedelta(hours=24)).isoformat(timespec="seconds")
    recent = recent_failures(parts.failures, None, since=since)
    n = len(recent)
    if n:
        out.append({"level": "error" if n >= 3 else "warning",
                    "text": f"{n} briefing{'s' if n != 1 else ''} failed in the last 24 hours "
                            f"(latest: {recent[0]['kind']}, {recent[0]['error']}). See Recent failures."})
    s = summary(parts.spending, state, parts.clock)
    for label, spent, cap in (("today", s["today_usd"], float(parts.limits.daily_cap_usd or 0)),
                              ("this month", s["total_usd"], float(parts.limits.monthly_cap_usd or 0))):
        if cap and spent >= 0.8 * cap:
            out.append({"level": "error", "text": f"Spending {label} is ${spent:.2f}, {spent / cap:.0%} of the "
                                                  f"${cap:.0f} cap. New briefings pause at the cap."})
        elif cap and spent >= 0.5 * cap:
            out.append({"level": "warning", "text": f"Spending {label} is ${spent:.2f}, {spent / cap:.0%} of the "
                                                    f"${cap:.0f} cap."})
    return out


def recent_failures(failures: FailureLog | None, limit: int | None = 20, *,
                    since: str | None = None) -> list[dict[str, Any]]:
    return failures.recent(limit, since=since) if failures is not None else []


def count_question(state: StateDB, question: str) -> None:
    q = " ".join(question.lower().split())[:300]
    with state.connect() as con:
        con.execute("INSERT INTO question_counts (day, question, n) VALUES (?, ?, 1) "
                    "ON CONFLICT(day, question) DO UPDATE SET n = n + 1", (datetime.now(UTC).strftime("%Y-%m-%d"), q))


def popular_questions(state: StateDB, days: int = 30, limit: int = 20) -> list[dict[str, Any]]:
    """The questions asked most often (counts only, nothing about who asked)."""
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
    with state.connect() as con:
        rows = con.execute("SELECT question, sum(n) total, max(day) last FROM question_counts WHERE day >= ? "
                           "GROUP BY question ORDER BY total DESC, last DESC LIMIT ?", (since, limit)).fetchall()
    return [{"question": q, "times": n, "last": last} for q, n, last in rows]


def record_visit(state: StateDB, path: str, referrer_host: str | None, visitor: str | None) -> None:
    """Count a page view. Only daily totals are kept; today's visitor hashes are rolled up and deleted."""
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    with state.connect() as con:
        con.execute("INSERT INTO page_views (day, path, views) VALUES (?, ?, 1) "
                    "ON CONFLICT(day, path) DO UPDATE SET views = views + 1", (day, path[:100]))
        if referrer_host:
            con.execute("INSERT INTO referrers (day, host, views) VALUES (?, ?, 1) "
                        "ON CONFLICT(day, host) DO UPDATE SET views = views + 1", (day, referrer_host[:100]))
        if visitor:
            con.execute("INSERT OR IGNORE INTO day_visitors (day, visitor) VALUES (?, ?)", (day, visitor))
        con.execute("INSERT OR REPLACE INTO day_totals (day, visitors) "
                    "SELECT day, count(*) FROM day_visitors WHERE day < ? GROUP BY day", (day,))
        con.execute("DELETE FROM day_visitors WHERE day < ?", (day,))


# what a visitor can do that shows the site doing its job, counted once per visitor per day
EVENTS = ("ask", "follow_up", "source")


def record_event(state: StateDB, event: str, visitor: str | None) -> None:
    """Note that today's visitor did this (once a day each); earlier days are rolled up into totals and the visitor
    hashes deleted, so only daily counts are kept."""
    if event not in EVENTS or not visitor:
        return
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    with state.connect() as con:
        con.execute("INSERT OR IGNORE INTO day_events (day, event, visitor) VALUES (?, ?, ?)", (day, event, visitor))
        con.execute("INSERT OR REPLACE INTO event_totals (day, event, visitors) "
                    "SELECT day, event, count(*) FROM day_events WHERE day < ? GROUP BY day, event", (day,))
        con.execute("DELETE FROM day_events WHERE day < ?", (day,))


def task_summary(state: StateDB, days: int = 30) -> dict[str, Any]:
    """Of the visitors in the last ``days`` days, how many asked a question, asked a follow-up and opened a source,
    and how many briefings readers rated useful: the signs that the site does what it is for."""
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    with state.connect() as con:
        visitors = (con.execute("SELECT coalesce(sum(visitors), 0) FROM day_totals WHERE day >= ? AND day < ?",
                                (since, today)).fetchone()[0]
                    + con.execute("SELECT count(*) FROM day_visitors WHERE day = ?", (today,)).fetchone()[0])
        did = dict(con.execute("SELECT event, sum(visitors) FROM event_totals WHERE day >= ? AND day < ? "
                               "GROUP BY event", (since, today)).fetchall())
        for event, n in con.execute("SELECT event, count(*) FROM day_events WHERE day = ? GROUP BY event", (today,)):
            did[event] = did.get(event, 0) + n
        fb = con.execute("SELECT coalesce(sum(useful), 0), count(*) FROM feedback WHERE at >= ?", (since,)).fetchone()
    def share(n: int) -> int | None:
        return round(100 * n / visitors) if visitors else None

    return {"days": days, "visitors": visitors,
            **{e: {"visitors": did.get(e, 0), "pct": share(did.get(e, 0))} for e in EVENTS},
            "useful": {"yes": fb[0], "rated": fb[1], "pct": round(100 * fb[0] / fb[1]) if fb[1] else None}}


def visit_summary(state: StateDB, days: int = 30) -> dict[str, Any]:
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    with state.connect() as con:
        views = con.execute("SELECT day, sum(views) FROM page_views WHERE day >= ? GROUP BY day ORDER BY day",
                            (since,)).fetchall()
        visitors = dict(con.execute("SELECT day, visitors FROM day_totals WHERE day >= ?", (since,)).fetchall())
        visitors[today] = con.execute("SELECT count(*) FROM day_visitors WHERE day = ?", (today,)).fetchone()[0]
        pages = con.execute("SELECT path, sum(views) v FROM page_views WHERE day >= ? GROUP BY path "
                            "ORDER BY v DESC LIMIT 20", (since,)).fetchall()
        refs = con.execute("SELECT host, sum(views) v FROM referrers WHERE day >= ? GROUP BY host "
                           "ORDER BY v DESC LIMIT 20", (since,)).fetchall()
        fb = con.execute("SELECT sum(useful), count(*) FROM feedback WHERE at >= ?", (since,)).fetchone()
        unhelpful = con.execute("SELECT at, question FROM feedback WHERE useful = 0 AND at >= ? "
                                "ORDER BY at DESC LIMIT 20", (since,)).fetchall()
    return {
        "days": days,
        "by_day": [{"day": d, "views": v, "visitors": visitors.get(d, 0)} for d, v in views],
        "top_pages": [{"path": p, "views": v} for p, v in pages],
        "top_referrers": [{"host": h, "views": v} for h, v in refs],
        "feedback": {"useful": fb[0] or 0, "total": fb[1] or 0,
                     "recent_not_useful": [{"at": a, "question": q} for a, q in unhelpful]},
    }


def summary(spending: Spending, state: StateDB, clock: Clock = SYSTEM_CLOCK) -> dict[str, Any]:
    """This month's spending by who it was for (each account by its email address, everyone not signed in together,
    and the site's own calls, such as topic briefings), and today's."""
    now = clock.now()
    month = now.strftime("%Y-%m")
    with state.connect() as con:
        emails = dict(con.execute("SELECT user_id, email FROM users").fetchall())
    groups: dict[str, dict[str, Any]] = {}
    for a in spending.by_account(month):
        label = emails.get(a.account) or ("(public)" if a.public else "(system)")
        g = groups.setdefault(label, {"email": label, "calls": 0, "cost_usd": 0.0, "visitors": 0})
        g["calls"] += a.calls
        g["cost_usd"] += a.spent_usd
        g["visitors"] += a.visitors
    by_user = sorted(({**g, "cost_usd": round(g["cost_usd"], 4)} for g in groups.values()),
                     key=lambda g: -g["cost_usd"])
    today = spending.totals(now.strftime("%Y-%m-%d"))
    return {"month": month,
            # (a whole 0 on a day with no call, as the sum of no rows always was)
            "today_usd": round(today.spent_usd, 4) if today.calls else 0, "today_calls": today.calls,
            "by_user": by_user,
            "total_usd": round(sum(g["cost_usd"] or 0 for g in by_user), 4)}
