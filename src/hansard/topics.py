"""Topic briefings: standing questions on the issues Parliament keeps returning to, kept up to date.

The topics are listed in ``config/topics.toml``. Each has a permanent page (/topics/<slug>) showing its latest
briefing, with earlier versions kept and linked. The daily sync refreshes a few topics each run when:

* the current version is older than the topic's ``refresh_days`` (a week by default), and
* records have arrived since it was written, and
* the evidence has actually changed: the sources are found first (free), and if they are mostly the same as last
  time (``unchanged_overlap``, 80% by default) the briefing is not rewritten. Only a rewrite costs money.

Refreshing stops when the site's daily or monthly budget is reached, and after ``max_per_run`` briefings.
"""

from __future__ import annotations

import logging
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from wts_core.briefing import DeskParts
from wts_core.usage import SavedBriefings

from hansard.api.briefings import keep
from hansard.config import Settings
from hansard.rag.pipeline import AskRequest, Pipeline
from hansard.rag.query_parse import QueryPlan, parse_model
from hansard.store.state import StateDB, utcnow

log = logging.getLogger(__name__)

RECHECK_HOURS = 20  # a topic whose evidence was unchanged is checked again after this long (twice-daily syncs)
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass
class Topic:
    slug: str
    title: str
    question: str
    area: str
    description: str = ""
    refresh_days: int = 7


def topics_file(settings: Settings) -> Path:
    name = settings.section("topics").get("file", "topics.toml")
    return settings.config_path.parent / name


def load_topics(path: Path) -> list[Topic]:
    if not Path(path).exists():
        return []
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    out = [Topic(t["slug"], t["title"], t["question"], t.get("area", "Other"), t.get("description", ""),
                 int(t.get("refresh_days", 7))) for t in raw.get("topic", [])]
    seen: set[str] = set()
    for t in out:
        if not SLUG.match(t.slug) or t.slug in seen:
            raise ValueError(f"topic slugs must be unique, lower case words joined by hyphens: {t.slug!r}")
        seen.add(t.slug)
    return out


# versions


def versions(state: StateDB, archive: SavedBriefings, slug: str) -> list[dict[str, Any]]:
    """Every version of a topic briefing still standing, newest first (a withdrawn briefing is not one)."""
    with state.connect() as con:
        rows = con.execute("SELECT briefing_id, prepared_at, records_up_to FROM topic_versions WHERE slug = ? "
                           "ORDER BY prepared_at DESC", (slug,)).fetchall()
    standing = []
    for r in rows:
        b = archive.get(r["briefing_id"])
        if b is not None and b.withdrawn_at is None:
            standing.append(dict(r))
    return standing


def current(state: StateDB, archive: SavedBriefings, slug: str) -> dict[str, Any] | None:
    v = versions(state, archive, slug)
    return v[0] if v else None


def topic_of_briefing(state: StateDB, bid: str) -> str | None:
    with state.connect() as con:
        row = con.execute("SELECT slug FROM topic_versions WHERE briefing_id = ?", (bid,)).fetchone()
    return row[0] if row else None


def last_check(state: StateDB, slug: str) -> dict[str, Any] | None:
    with state.connect() as con:
        row = con.execute("SELECT checked_at, outcome FROM topic_checks WHERE slug = ?", (slug,)).fetchone()
    return dict(row) if row else None


def _checked(state: StateDB, slug: str, outcome: str) -> None:
    with state.connect() as con:
        con.execute("INSERT OR REPLACE INTO topic_checks (slug, checked_at, outcome) VALUES (?, ?, ?)",
                    (slug, utcnow(), outcome))


def is_due(topic: Topic, state: StateDB, archive: SavedBriefings, latest_record: str | None,
           now: datetime | None = None) -> tuple[bool, str]:
    cur = current(state, archive, topic.slug)
    if cur is None:
        return True, "not written yet"
    now = now or datetime.now(UTC)
    age = now - datetime.fromisoformat(cur["prepared_at"])
    if age < timedelta(days=topic.refresh_days):
        return False, f"written {age.days} days ago"
    if latest_record and cur["records_up_to"] and cur["records_up_to"] >= latest_record:
        return False, "no new records since"
    # checking costs almost nothing (reading the question, then a free search), but once a day is enough
    chk = last_check(state, topic.slug)
    if chk and chk["outcome"].startswith("evidence unchanged") and \
            now - datetime.fromisoformat(chk["checked_at"]) < timedelta(hours=RECHECK_HOURS):
        return False, "checked recently: evidence unchanged"
    return True, "due"


def _card_id(card: dict[str, Any]) -> str:
    return card.get("record_id") or f"division:{card.get('division_id') or card.get('title')}"


def _source_ids(events: list[dict[str, Any]]) -> set[str]:
    for e in events:
        if e.get("type") == "sources":
            return {_card_id(s) for s in e.get("sources", [])}
    return set()


def overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


# refreshing


def refresh_topics(settings: Settings, state: StateDB, pipeline: Pipeline, parts: DeskParts, *,
                   only: list[str] | None = None, force: bool = False, limit: int | None = None,
                   now: datetime | None = None, progress: Callable[[str], None] = log.info) -> list[dict[str, Any]]:
    """Check the topics that are due and rewrite those whose evidence has changed. Returns what happened to each.
    ``parts`` is the shared spending record, caps, notes of failures and saved briefings (``hansard.api.usage.desk``):
    a topic's briefing is paid for, counted against the caps and kept like any other."""
    from hansard.api.briefings import get_briefing

    cfg = settings.section("topics")
    topics = load_topics(topics_file(settings))
    if only:
        topics = [t for t in topics if t.slug in set(only)]
    limit = int(cfg.get("max_per_run", 4)) if limit is None else limit
    same = float(cfg.get("unchanged_overlap", 0.8))
    latest = state.latest_record_date()
    results: list[dict[str, Any]] = []
    written = 0

    def failed(question: str, error: str) -> None:
        if parts.failures is not None:
            parts.failures.note("topic", question, error)

    def note(t: Topic, outcome: str, record: bool = True) -> None:
        results.append({"slug": t.slug, "outcome": outcome})
        if record:
            _checked(state, t.slug, outcome)
        progress(f"  {t.slug}: {outcome}")

    for t in topics:
        due, why = (True, "forced") if force else is_due(t, state, parts.archive, latest, now)
        if not due:
            note(t, why, record=False)
            continue
        if written >= limit:
            note(t, "waiting: enough topics refreshed this run", record=False)
            continue
        if not pipeline.has_model():
            note(t, "skipped: no API key", record=False)
            continue
        blocked = parts.caps.site_paused()
        if blocked:
            note(t, f"skipped: {blocked.message}", record=False)
            continue
        req = AskRequest(question=t.question)
        plan, usage = pipeline.make_plan(req)
        if usage:
            parts.spending.add("topic", parse_model(settings), usage)
        if plan.parse_error:
            failed(t.question, plan.parse_error)
            note(t, "failed: the question could not be read (see failures on the admin page)")
            break  # the same cause (credit, key) would stop every other topic too
        req.plan = {k: v for k, v in plan.to_dict().items() if k != "question"}
        cur = current(state, parts.archive, t.slug)
        if cur and not force:
            _, _, sources = pipeline.find(QueryPlan.from_dict({**req.plan, "question": t.question}), req)
            old = get_briefing(parts.archive, cur["briefing_id"])
            share = overlap(_source_ids(old["events"]) if old else set(),
                            {_card_id(s.card()) for s in sources})
            if share >= same:
                note(t, f"evidence unchanged ({round(share * 100)}% the same sources): not rewritten")
                continue
        kept: list[dict[str, Any]] = []
        final: dict[str, Any] | None = None
        error = None
        try:
            for ev in pipeline.run(req):
                if ev["type"] == "text":
                    continue
                kept.append(ev)
                if ev["type"] == "final":
                    final = ev
                elif ev["type"] == "error":
                    error = ev.get("message")
        except Exception as exc:  # an API failure: keep the current version
            error = f"{type(exc).__name__}: {exc}"
        if final and final.get("usage"):
            parts.spending.add("topic", final.get("model"), final["usage"])
        answer = (final or {}).get("answer")
        if error or not answer:
            failed(t.question, error or "no briefing was written")
            note(t, "failed: kept the previous version (see failures on the admin page)")
            break
        if answer.get("no_records"):
            note(t, "no records found: not written")
            continue
        meta = keep(parts.archive, state, t.question, {"mode": "summary", "topic": t.slug}, kept, final)
        with state.connect() as con:
            con.execute("INSERT INTO topic_versions (slug, briefing_id, prepared_at, records_up_to) "
                        "VALUES (?, ?, ?, ?)", (t.slug, meta["briefing_id"], meta["prepared_at"],
                                                meta["records_up_to"]))
        written += 1
        note(t, f"rewritten ({meta['briefing_id']})")
    return results


def topic_listing(settings: Settings, state: StateDB, archive: SavedBriefings) -> list[dict[str, Any]]:
    """Every topic with its current version (if any), for the /topics page and the admin page."""
    from hansard.api.briefings import get_briefing, summary_text

    out = []
    for t in load_topics(topics_file(settings)):
        cur = current(state, archive, t.slug)
        summary = ""
        if cur:
            b = get_briefing(archive, cur["briefing_id"])
            final = next((e for e in (b or {}).get("events", []) if e.get("type") == "final"), {})
            summary = summary_text(final.get("answer"))
        out.append({"slug": t.slug, "title": t.title, "area": t.area, "question": t.question,
                    "description": t.description, "refresh_days": t.refresh_days,
                    "current": {**cur, "summary": summary} if cur else None,
                    "versions": len(versions(state, archive, t.slug)), "last_check": last_check(state, t.slug)})
    return out
