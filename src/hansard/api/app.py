"""FastAPI application: streaming answers, source search, record drawer, metadata.

Public by default: anyone may ask. Model-written briefings cost money, so they are
limited per visitor and overall, and identical requests are served from a cache.
Invite-only sign-in remains available (``[auth] required = true``)."""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationInfo, field_validator
from wts_core import ops
from wts_core.adapters import b2
from wts_core.briefing import Ask, Hooks, TooManyRequests, current_stages, sse
from wts_core.cli import backup_ages, check_route, last_day_figures, latest_week_figures
from wts_core.events import Event
from wts_core.observe import LOGGER, SqliteBriefingLog, briefing_config, json_logging
from wts_core.ports import SYSTEM_CLOCK, SYSTEM_RANDOM, BriefingConfig, Clock, Random
from wts_core.replies import NotDone, Refused, Replies
from wts_core.usage import RateLimiter, cache_key, network_of

from hansard.api import usage as use
from hansard.api.auth import (
    COOKIE,
    LoginThrottle,
    authenticate,
    create_session,
    end_session,
    user_for_token,
)
from hansard.api.briefings import (
    ID_PATTERN,
    ShownOnly,
    briefing_description,
    briefing_title,
    feature,
    featured_list,
    featured_title,
    fmt_date,
    get_briefing,
    is_featured,
    keep,
    page_with_meta,
    public_view,
    recent_briefings,
    summary_text,
    trusted_history,
    unfeature,
)
from hansard.api.cards import card_paths, card_png
from hansard.api.feeds import FEED_TYPE, rss
from hansard.api.plaintext import (
    bill_text,
    bills_list_text,
    briefing_text,
    committee_text,
    committees_list_text,
    debate_text,
    devolved_day_text,
    devolved_list_text,
    devolved_year_text,
    edm_text,
    edms_list_text,
    evidence_text,
    group_text,
    groups_list_text,
    inquiry_text,
    people_list_text,
    person_text,
    topics_list_text,
    week_text,
    weeks_list_text,
    with_heading,
    with_list,
    with_text,
)
from hansard.api.usage import (
    alerts,
    count_question,
    mark_supporter,
    popular_questions,
    recent_failures,
    record_event,
    record_feedback,
    record_visit,
    summary,
    support_reminder_due,
    task_summary,
    visit_summary,
    visitor_key,
)
from hansard.config import REPO_ROOT, SITE, Settings, load_settings
from hansard.devolution import notes_for
from hansard.engine import Engine, Scope
from hansard.rag import prompts
from hansard.rag.interests import without_public_office
from hansard.rag.pipeline import AskRequest, Pipeline
from hansard.rag.query_parse import ITL1_REGIONS, QueryPlan, parse_model
from hansard.rag.query_parse import system_prompt as query_parse_instructions
from hansard.reference.portfolios import PORTFOLIOS
from hansard.replies import (
    SITE_HOSTS,
    STATUSES,
    as_listed,
    corrections,
    hidden_note,
    open_replies,
    published,
)
from hansard.sisters import councils_for_seat
from hansard.store.state import StateDB
from hansard.store.wts import BACKUP_ALERTS, BACKUPS, check_before_opening, open_core
from hansard.wording import shorten, title_case

log = logging.getLogger(__name__)

# a cached briefing is only reused while the instructions that wrote it, and those that read the question, are unchanged
INSTRUCTIONS_VERSION = __import__("hashlib").sha256(
    (prompts.ANSWER_SYSTEM + json.dumps([prompts.MODE_INSTRUCTIONS, prompts.GROUP_BY_INSTRUCTIONS],
                                        sort_keys=True)
     + query_parse_instructions({}, [], date(2000, 1, 1))).encode()).hexdigest()[:12]

ATTRIBUTION = [
    {"text": "Contains Parliamentary information licensed under the Open Parliament Licence v3.0.",
     "url": "https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/"},
    {"text": "Contains public sector information licensed under the Open Government Licence v3.0 "
             "(ONS, NRS and NISRA boundaries, council areas, urban areas and census populations).",
     "url": "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"},
    {"text": "Data service provided by TheyWorkForYou: Hansard data and member records via TheyWorkForYou / "
             "ParlParse, with thanks to mySociety.",
     "url": "https://www.theyworkforyou.com/"},
]


class LoginBody(BaseModel):
    email: str
    password: str


class ScopeBody(BaseModel):
    written: bool = True
    pbc: bool = True
    votes: bool = True
    edms: bool = True
    devolved: bool = True
    evidence: bool = True


class FeedbackBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    useful: bool
    briefing_key: str | None = Field(default=None, max_length=128)


class ReportBody(BaseModel):
    # correction: "Something is wrong"; complaint: "You've been characterised unfairly" (the policy's routes; the
    # values are kept so old links and saved reports still work); takedown: a private person's name to remove
    kind: Literal["correction", "complaint", "takedown", "general"]
    message: str = Field(min_length=10, max_length=4000)
    briefing_id: str | None = Field(default=None, max_length=20)
    page: str | None = Field(default=None, max_length=300)  # the page on this site it is about, if not a briefing
    contact: str | None = Field(default=None, max_length=200)
    website: str | None = Field(default=None, max_length=200)  # left empty by people; filled in by spam bots


class CloseReportBody(BaseModel):
    status: Literal["resolved", "dismissed"]
    resolution: str = Field(min_length=3, max_length=2000)       # private
    public_note: str | None = Field(default=None, max_length=1000)  # shown on the corrections page


class StatementBody(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    role: str = Field(min_length=2, max_length=200)
    item: str = Field(min_length=1, max_length=300)  # the address on this site it sits beside
    text: str = Field(min_length=1, max_length=2000)  # up to 150 words, checked in statements.py
    link: str | None = Field(default=None, max_length=500)
    email: str = Field(min_length=3, max_length=200)
    website: str | None = Field(default=None, max_length=200)  # left empty by people; filled in by spam bots


class PublishStatementBody(BaseModel):
    person: int | None = None  # the member it is about, when its address isn't their page
    checked: str = Field(min_length=1, max_length=500)  # how the sender was confirmed (private); required


class DeclineStatementBody(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)  # sent to the author; never shown on the site


class UpdateStatementBody(BaseModel):
    text: str | None = Field(default=None, max_length=2000)
    name: str | None = Field(default=None, max_length=120)
    role: str | None = Field(default=None, max_length=200)
    link: str | None = Field(default=None, max_length=500)  # an empty string removes the link


class WithdrawStatementBody(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class WithdrawBody(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class FeatureBody(BaseModel):
    briefing: str = Field(min_length=10, max_length=300, description="the briefing's id or full address")
    title: str | None = Field(default=None, max_length=200)
    blurb: str | None = Field(default=None, max_length=600)


class HitBody(BaseModel):
    path: str = Field(min_length=1, max_length=200)
    referrer: str | None = Field(default=None, max_length=500)
    # something done on the page rather than a page view: "source" (opened a source)
    event: Literal["source"] | None = None


class HistoryItem(BaseModel):
    """An earlier question in the conversation, as the browser sends it back: the saved briefing's id, if it has one,
    or else the question and what it searched for. Each part is cut to a fixed length (longer than the browser ever
    sends), so a request cannot inflate the prompt. No answer is taken from the browser: the start of an earlier
    briefing is read from the saved briefing itself (``briefings.trusted_history``), so a request cannot invent
    one. An ``answer`` sent by an older page is ignored."""

    question: str = ""
    topic: str = ""
    scope: str = ""
    briefing_id: str = ""

    @field_validator("question", "topic", "scope", "briefing_id", mode="before")
    @classmethod
    def _trimmed(cls, v: Any, info: ValidationInfo) -> str:
        v = str(v or "")[:HISTORY_LIMITS[info.field_name]]
        return v if info.field_name != "briefing_id" or ID_PATTERN.match(v) else ""


HISTORY_LIMITS = {"question": 2000, "topic": 500, "scope": 1000, "briefing_id": 10}
PLAN_MAX_CHARS = 20_000  # an edited plan is a few hundred characters; this is far more than any reader needs


class AskBody(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    mode: Literal["summary", "comprehensive"] = "summary"
    recency: float | None = Field(default=None, ge=0, le=1)
    role_weight: float | None = Field(default=None, ge=1, le=2)
    scope: ScopeBody = ScopeBody()
    plan: dict[str, Any] | None = None
    history: list[HistoryItem] = Field(default_factory=list, max_length=10)
    sources_only: bool = False  # find and show the sources, but write no briefing (free)
    follows: str | None = Field(default=None, max_length=2000)  # a follow-up: the question it follows (kept with it)
    compare: list[dict[str, str]] = Field(default_factory=list, max_length=3)  # sides, from the Compare page

    @field_validator("plan")
    @classmethod
    def _plan_size(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None and len(json.dumps(v, default=str)) > PLAN_MAX_CHARS:
            raise ValueError("the search plan is too large")
        return v

    @field_validator("compare")
    @classmethod
    def _compare_trimmed(cls, v: list[dict[str, str]]) -> list[dict[str, str]]:
        return [{k[:20]: str(x)[:200] for k, x in list(side.items())[:5]} for side in v]

    def history_dicts(self) -> list[dict[str, str]]:
        """The earlier turns as the browser named them (the saved briefings are read by ``trusted_history``)."""
        return [h.model_dump() for h in self.history]


def donate_url(settings: Settings) -> str | None:
    """The 'Support this site' link, if one is set and is a plain https address."""
    url = (os.environ.get("HANSARD_DONATE_URL") or settings.section("site").get("donate_url") or "").strip()
    return url if url.startswith("https://") and len(url) < 300 and not any(c in url for c in "\"'<> ") else None


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    """What visitors may see of an update run: when it finished and whether it worked (no internal details)."""
    return {"finished_at": run.get("finished_at"), "status": run.get("status")}


def breadcrumbs(base: str, trail: list[tuple[str, str]]) -> dict[str, Any]:
    """schema.org's trail of pages above this one, from the home page: (name, path) each, this page last."""
    return {"@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": n, "name": name, "item": base + path}
        for n, (name, path) in enumerate([("Where MPs Stand", "/"), *trail], start=1)]}


def structured(*items: dict[str, Any]) -> dict[str, Any]:
    """Structured data for a page's head: schema.org items, together."""
    return {"@context": "https://schema.org", "@graph": list(items)}


# the charity, as the family's footer names it (web/public/family/shelf.html)
PUBLISHER = {"@type": "Organization", "@id": "https://wheretheystand.uk/#org", "name": "Where They Stand",
             "url": "https://wheretheystand.uk/"}


def server_timing(timings: dict[str, float]) -> str:
    """'keyword;dur=812.4, meaning;dur=40.1': how long each stage took, readable in the browser's network panel."""
    return ", ".join(f"{k};dur={v * 1000:.1f}" for k, v in timings.items())


STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
SITEMAP_LIMIT = 50_000  # the most addresses one sitemap may hold (sitemaps.org)
COMMITTEE_HOUSES = {"Commons": "House of Commons", "Lords": "House of Lords", "Joint": "Joint committees"}
EMPTY_PAGE = "<!doctype html><html><head></head><body></body></html>"
# the built pages that are only frames for one item, filled in from their address (/people/<number>/<name> uses
# person.html): at their own address (/person, /bill.html) they are empty, so they answer 404, not an empty page
TEMPLATES = {"person", "bill", "debate", "committee", "inquiry", "evidence", "edm", "group", "devolvedday", "week"}
CLOSED = ("resolved", "dismissed")  # how a report may be closed
WITHDRAWN_BY_AUTHOR = "Withdrawn by the author."  # the note of a withdrawal, when the owner gives none
WITHDRAWN = "This briefing has been withdrawn."  # what a withdrawn briefing's address says
NOT_SHOWN = "Briefing not shown for now"  # the title of a briefing hidden while a concern about it is decided


def answer_key(settings: Settings, body: AskBody, data: Any, *, model: bool, cache_hours: float) -> str | None:
    """The answer cache's key for a question: everything an answer depends on. The question (its case and spacing
    apart), what the reader chose, the model, the update the record was last brought up to date by (``data``) and
    the version of the instructions: change any of them and an answer written before is not served again. None
    when nothing is to be cached or looked for: there is no model, or no cache."""
    if not model or cache_hours <= 0:
        return None
    return cache_key({"q": " ".join(body.question.lower().split()), **body.model_dump(exclude={"question"}),
                      "model": settings.answer.get("model"), "data": data,
                      "instructions": INSTRUCTIONS_VERSION}, length=64)


def filters_set(body: AskBody) -> tuple[str, ...]:
    """Which choices the reader made beyond the question, for the record of the briefing: their names, never their
    values."""
    chosen = [k for k, v in ((body.plan or {}).get("filters") or {}).items() if v not in (None, [], "", False)]
    chosen += [name for name in ("recency", "role_weight") if getattr(body, name) is not None]
    chosen += [f"scope.{k}" for k, v in body.scope.model_dump().items() if v is not getattr(ScopeBody(), k)]
    chosen += ["compare"] if body.compare else []
    return tuple(sorted(chosen))


def configuration(settings: Settings, log: SqliteBriefingLog | None = None) -> BriefingConfig:
    """Everything that could change a briefing, and the short id of it all: what goes into each briefing's record,
    so a figure can be compared before and after a change. Never a key, and nothing about a reader. Given the record
    of briefings, the same call keeps the settings behind the id there."""
    answer, embedding = settings.answer, settings.embedding
    return briefing_config(
        log=log, model=str(answer.get("model")), parse_model=parse_model(settings),
        instructions=INSTRUCTIONS_VERSION, site_version=SITE.version,
        also={"ranking": dict(settings.ranking),
              "embedding": {k: embedding.get(k) for k in ("backend", "model", "dim", "passage_words")},
              "answer": {k: answer.get(k) for k in (
                  "summary_sources", "comprehensive_sources", "side_sources", "comprehensive_side_sources",
                  "summary_effort", "comprehensive_effort", "query_parse_effort", "interests_check", "max_tokens",
                  "use_fallbacks", "cache_instructions")}})


def start_logging(settings: Settings, clock: Clock = SYSTEM_CLOCK) -> logging.Handler:
    """Turn the structured log on for this process: one line of JSON for each stage a briefing reaches and one for
    how it ended, in data/logs/briefings.jsonl. A line holds the record's id, a stage's name, milliseconds and an
    error's class name, and nothing else: never a question, an address or a visitor's code (wts-core
    docs/adr/0012). Kept for 14 days or 50 MB, whichever is less (``[logs]``)."""
    logs = settings.section("logs")
    handler = json_logging(settings.log_dir / "briefings.jsonl", site=SITE, max_bytes=int(logs["max_bytes"]),
                           keep=int(logs["keep"]), days=int(logs["days"]), clock=clock)
    lines = logging.getLogger(LOGGER)
    lines.addHandler(handler)
    lines.setLevel(logging.INFO)
    lines.propagate = False
    return handler


class SiteHooks(Hooks):
    """This site's own steps in the flow of a briefing (``wts_core.briefing.Hooks``)."""

    def __init__(self, settings: Settings, state: StateDB, parts: Any, replies: Replies | None = None) -> None:
        self.settings, self.state, self.parts, self.replies = settings, state, parts, replies

    def on_cached(self, ask: Ask, hit: list[Event]) -> list[Event]:
        """A cached answer, about to be played again, is sent as it was stored: its flags are not judged again
        (a saved briefing's are, when its own address is opened). Two things only are put right on the way out.

        A briefing that has been withdrawn, or is hidden while a concern about it is decided, is never played
        again. Withdrawing or hiding one drops its cached copies, so the next reader with the question has a
        briefing written afresh; should a copy be met all the same (one withdrawn by an older version of the
        site, say), it is dropped here, and this reader is sent the reading of the question, the sources, and
        the words the briefing's own address shows."""
        taken_down = self.taken_down(hit)
        if taken_down is not None:
            self.parts.cache.delete_briefing(taken_down[0])
            return [*(e for e in hit if e.get("type") in ("plan", "sources")),
                    {"type": "final", "answer": None, "usage": None, "note": taken_down[1]}]
        if hit and hit[-1].get("interests"):  # saved before posts in government were left out
            hit[-1] = {**hit[-1], "interests": without_public_office(hit[-1]["interests"])}
        final = hit[-1]
        if final.get("answer") and not final["answer"].get("no_records") and not final.get("briefing_id"):
            # cached before shareable links existed: give it an address now, and remember it
            assert ask.cache_key is not None
            hit[-1] = {**final, **keep(self.parts.archive, self.state, ask.question, dict(ask.options), hit, final)}
            self.parts.cache.put(ask.cache_key, hit, self.parts.cache_hours)
        return hit

    def taken_down(self, events: list[Event]) -> tuple[str, str] | None:
        """(the briefing's id, the words shown in its place) when a cached answer's briefing is not to be shown."""
        bid = next((e.get("briefing_id") for e in reversed(events) if e.get("type") == "final"), None)
        saved = self.parts.archive.get(bid) if isinstance(bid, str) and ID_PATTERN.match(bid) else None
        if saved is not None and saved.withdrawn_at:
            return saved.id, WITHDRAWN
        note = hidden_note(self.replies, saved.id) if saved is not None and self.replies is not None else None
        return (saved.id, note) if saved is not None and note is not None else None

    def usages(self, ask: Ask, event: Event) -> list[tuple[str, str | None, Any]]:
        """The third model call of a briefing: the check of the quoted members' registered interests, which the
        pipeline reports on the final event. It is recorded before the briefing's own."""
        if event["type"] == "final" and event.get("interests_usage"):
            return [("interests", parse_model(self.settings), event["interests_usage"])]
        return []

    def on_event(self, ask: Ask, event: Event) -> Event | None:
        if event["type"] == "final":  # the interests check's token counts were for the spending record only
            return {k: v for k, v in event.items() if k != "interests_usage"}
        return event

    def finish(self, ask: Ask, final: Event, kept: list[Event]) -> Event:
        # lets the reader's feedback refer to this briefing
        return {**final, "briefing_key": ask.cache_key} if ask.cache_key else final

    def for_this_reader(self, ask: Ask, final: Event) -> Event:
        """The note on what briefings cost, after every so many new briefings a visitor has had written today: it
        goes to this reader only, never into saved, shared or cached copies."""
        if not final.get("usage") or ask.account is not None or not donate_url(self.settings):
            return final
        remind = support_reminder_due(self.parts.spending, self.state, ask.visitor,
                                      int(self.settings.usage.get("support_reminder_every", 10)))
        return {**final, "support_reminder": remind} if remind else final


def create_app(settings: Settings | None = None, pipeline: Pipeline | None = None,
               state: StateDB | None = None, *, clock: Clock = SYSTEM_CLOCK,
               random: Random = SYSTEM_RANDOM) -> FastAPI:
    settings = settings or load_settings()
    # Whatever could stop the site from starting is found first, before the state database is opened: the first
    # opening on new code may change it for good (hansard/startup.py; hansard/store/wts.py).
    check_before_opening(settings, state.path if state is not None else None)
    state = state or StateDB(settings.state_db)  # the site's own tables
    # The shared package's side of the same file: spending, the answer cache, saved briefings, failures, reports
    # and statements (hansard/store/wts.py). The first start on a database from before it brings the old rows across.
    core = open_core(settings, clock, path=state.path)
    # Everything the flow of a briefing is made of, from [usage] (hansard/api/usage.py: desk). This is also the
    # start-up check: every limit, price and message the flow needs, with whatever is missing named in one error.
    parts = use.desk(settings, core, clock, random)
    app = FastAPI(title="Where MPs Stand", docs_url="/api/docs", openapi_url="/api/openapi.json")
    throttle = LoginThrottle()
    address_throttle = LoginThrottle(limit=20)  # failed sign-ins from one address, whatever email is tried
    auth_required = bool(settings.section("auth").get("required", False))
    # page counts: their own allowance, so browsing never uses up the one for questions
    hit_limiter = RateLimiter(120, forget_over=use.FORGET_IDLE_VISITORS_OVER, clock=clock)
    admin_token = os.environ.get("HANSARD_ADMIN_TOKEN") or None
    lock = threading.Lock()
    dist = Path(settings.section("paths").get("web_dist", REPO_ROOT / "web" / "dist"))
    holder: dict[str, Pipeline] = {}
    if pipeline is not None:
        holder["p"] = pipeline

    # Answers that change only when the record or member data is updated (twice a day, by a separate process) are
    # kept for ten minutes, so a page opened again, or by the next reader, comes straight from memory.
    remembered_answers: OrderedDict[str, tuple[float, Any]] = OrderedDict()
    remembered_lock = threading.Lock()

    def remembered(key: str, compute: Callable[[], Any], ttl: float = 600.0) -> Any:
        now = time.time()
        with remembered_lock:
            hit = remembered_answers.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
        value = compute()
        with remembered_lock:
            remembered_answers[key] = (now, value)
            remembered_answers.move_to_end(key)
            while len(remembered_answers) > 2000:
                remembered_answers.popitem(last=False)
        return value

    def readable(build: Callable[[], str]) -> str | None:
        """The page's readable text, or none if it cannot be built (the page itself still works)."""
        try:
            return build()
        except Exception:  # noqa: BLE001 - the readable copy is a courtesy; never fail the page for it
            log.exception("readable page text not built")
            return None

    def template(name: str) -> str:
        """A page of the built app (web/dist/<name>.html), for the server to fill in; a bare page without a build."""
        f = dist / f"{name}.html"
        return f.read_text() if f.is_file() else EMPTY_PAGE

    def remembered_if_ready(key: str, ttl: float = 600.0) -> Any:
        with remembered_lock:
            hit = remembered_answers.get(key)
        return hit[1] if hit is not None and time.time() - hit[0] < ttl else None

    def get_pipeline() -> Pipeline:
        with lock:
            if "p" not in holder:
                holder["p"] = Pipeline(settings, Engine(settings))
            return holder["p"]

    def get_people():
        from hansard.people import People

        p = get_pipeline()
        with lock:
            if "people" not in holder:
                holder["people"] = People(settings, p.engine.resolver, p.today)
            return holder["people"]

    def member_known(number: int) -> bool | None:
        """Whether there is a member's page of this number; None when the members' data can't be read just now
        (a statement about them is then kept, and the owner checks it)."""
        try:
            return bool(get_people().resolve(number))
        except Exception:
            log.warning("statement about /people/%s accepted without checking the member exists", number,
                        exc_info=True)
            return None

    replies = open_replies(core, parts.archive, member_known, clock)
    # One record for each question asked (wts-core docs/adr/0012): how long each stage took, what it cost, what
    # the checks found and how it ended; with the question only where the site already kept it (a briefing saved
    # or cached, or a note of a failure). `hansard briefing-stats` and the weekly report are made from it.
    record = SqliteBriefingLog(core, clock=clock)
    desk = parts.desk(configuration(settings, record), log=record, hooks=SiteHooks(settings, state, parts, replies),
                      group_of=lambda card: card.get("group"))  # the record counts each briefing's sources by group
    app.state.parts, app.state.replies, app.state.core, app.state.desk = parts, replies, core, desk
    app.state.record = record

    def current_user(request: Request) -> dict[str, Any] | None:
        user = user_for_token(state, request.cookies.get(COOKIE))
        if user is None and auth_required:
            raise HTTPException(401, "Please sign in.")
        return user

    def admin_user(request: Request, user=Depends(current_user)):
        if user and user["is_admin"]:
            return user
        given = request.headers.get("authorization", "")
        if admin_token and secrets.compare_digest(given.encode(), f"Bearer {admin_token}".encode()):
            return None
        raise HTTPException(403, "Admins only.")

    def backup_alerts(folder: Path) -> list[dict[str, str]]:
        """For the admin page: a nightly backup that has not run, or an off-site copy that failed. Nothing is said
        when there is no backups folder at all (a computer that is not the server)."""
        if not folder.is_dir():
            return []
        found = ops.backup_alerts(folder, BACKUPS, offsite_set_up=b2.configured(SITE) is not None,
                                  wording=BACKUP_ALERTS, clock=clock)
        return [{"level": a.level, "text": a.text} for a in found]

    def briefing_of(key: str | None) -> str | None:
        """The saved briefing a cached answer was kept as, from the key the page was given with it (None when the
        key is not one, or its answer is no longer cached)."""
        if not key or not re.fullmatch(r"[0-9a-f]{16,128}", key):
            return None
        final = next((e for e in reversed(parts.cache.get(key, parts.cache_hours) or []) if e.get("type") == "final"),
                     None)
        found = (final or {}).get("briefing_id")
        return found if isinstance(found, str) and ID_PATTERN.match(found) else None

    def requester(request: Request, user: dict[str, Any] | None) -> tuple[str | None, str]:
        """(visitor hash for anonymous people, rate-limit key); 429 when over the per-minute limit."""
        visitor = None if user else visitor_key(core, request.client.host if request.client else None, clock, random)
        key = f"user:{user['user_id']}" if user else f"visitor:{visitor}"
        try:
            desk.allow(key)
        except TooManyRequests as exc:
            raise HTTPException(429, exc.message) from None
        return visitor, key

    # auth
    @app.post("/api/auth/login")
    def login(body: LoginBody, request: Request, response: Response):
        address = network_of(request.client.host if request.client else None)
        key = f"{address}|{body.email.lower()}"
        # per address and email, and per address whatever email is tried
        if throttle.blocked(key) or address_throttle.blocked(address):
            raise HTTPException(429, "Too many failed sign-in attempts. Try again in 15 minutes.")
        user = authenticate(state, body.email, body.password)
        if user is None:
            throttle.fail(key)
            address_throttle.fail(address)
            raise HTTPException(401, "Incorrect email or password.")
        throttle.reset(key)
        days = int(settings.section("auth").get("session_days", 14))
        token = create_session(state, user["user_id"], days)
        response.set_cookie(COOKIE, token, max_age=days * 86400, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https", path="/")
        return {"email": user["email"], "is_admin": bool(user["is_admin"])}

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        end_session(state, request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/me")
    def me(user=Depends(current_user)):
        return {"email": user["email"] if user else None, "is_admin": bool(user and user["is_admin"]),
                "auth_required": auth_required}

    # metadata
    @app.get("/api/meta")
    def meta(user=Depends(current_user)):
        p = get_pipeline()
        idx = p.engine.index.stats()
        runs = state.runs(1)
        return {
            "regions": ITL1_REGIONS,
            "region_groups": settings.regions,
            "portfolios": PORTFOLIOS,
            "parties": [{"id": i, "name": n} for i, n in p.party_choices()],
            # party groups there is published evidence for (a group with none is not offered)
            "party_groups": p.engine.groups().offered(),
            "answers_enabled": p.has_model(),
            "model": settings.answer.get("model"),
            "archive_start": settings.archive_start.isoformat(),
            "index": idx,
            "last_sync": _public_run(runs[0]) if runs else None,
            "defaults": {"recency": settings.ranking.get("default_recency", 0.25),
                         "role_weight": settings.ranking.get("default_role_weight", 1.5),
                         "min_half_life_years": settings.ranking.get("min_half_life_years", 0.5)},
            "attribution": ATTRIBUTION,
            "donate_url": donate_url(settings),
        }

    @app.get("/api/site")
    def site():
        """Small, public settings for the static pages."""
        return {"donate_url": donate_url(settings)}

    @app.post("/api/support-clicked")
    def support_clicked(request: Request, user=Depends(current_user)):
        """The reader went through to the donation page from the reminder: no more reminders for them today."""
        visitor, _ = requester(request, user)
        if visitor:
            mark_supporter(state, visitor)
        return {"ok": True}

    @app.get("/api/health")
    def health():
        return {"ok": True}

    # questions
    @app.post("/api/ask")
    def ask(body: AskBody, request: Request, user=Depends(current_user)):
        """A question's whole journey is the shared briefing desk's (wts-core, ``wts_core.briefing``): the cache,
        the caps, the places in progress, the pipeline's events, the spending record, the saved briefing. The
        route describes the request to it and streams what it gives back."""
        visitor, who = requester(request, user)
        p = get_pipeline()
        uid = user["user_id"] if user else None
        req = AskRequest(question=body.question, mode=body.mode, recency=body.recency,
                         role_weight=body.role_weight, scope=Scope(**body.scope.model_dump()), plan=body.plan,
                         history=trusted_history(parts.archive, body.history_dicts()), compare=body.compare)
        model_on = p.has_model()
        last = state.runs(1) if model_on and parts.cache_hours > 0 else []
        key = answer_key(settings, body, last[0]["run_id"] if last else None, model=model_on,
                         cache_hours=parts.cache_hours)
        # the site's own counts, as they always were: how often each question is asked, and that today's visitor
        # asked one
        count_question(state, body.question)
        record_event(state, "ask", visitor)
        if body.history:
            record_event(state, "follow_up", visitor)
        asked = Ask(
            question=body.question, visitor=visitor, account=uid, limiter_key=who,
            write=lambda: p.run(req),
            # the model reads the question unless the site's own budget is used up, whatever stopped this reader
            sources_only=lambda note, free: sources_only_events(body, note, uid, visitor),
            cache_key=key, only_sources=body.sources_only, model_available=model_on,
            follow_up=bool(body.history), mode=body.mode, filters_set=filters_set(body),
            options=body.model_dump(exclude={"question", "history"}))  # saved with the briefing
        # a saved briefing here is the plan, the sources and the final event: a permanent, shareable address
        asked.save = lambda final, kept, text: keep_briefing(body, kept, final)
        return StreamingResponse(sse(desk.handle(asked)), media_type="text/event-stream", headers=STREAM_HEADERS)

    def retrieve(body: AskBody, read_with_model: bool = False, uid: int | None = None,
                 visitor: str | None = None) -> dict[str, Any]:
        """The plan and ranked sources for the question or edited plan, without writing a briefing.

        The question is read by the model when ``read_with_model`` (a fraction of a penny with a small model, and far
        more accurate), otherwise by the free built-in rules.
        """
        p = get_pipeline()
        if body.plan:
            plan = QueryPlan.from_dict({"question": body.question, **body.plan})
        elif read_with_model and p.has_model():
            plan, usage = p.make_plan(AskRequest(question=body.question,
                                                 history=trusted_history(parts.archive, body.history_dicts()),
                                                 compare=body.compare))
            if plan.parse_error and parts.failures is not None:
                parts.failures.note("question reading", body.question, plan.parse_error)
            if usage:
                cost = parts.spending.add("parse", parse_model(settings), usage, visitor=visitor, account=uid)
                current_stages().spent(usage, cost)  # for the record of this question: what its reading cost
        else:
            from hansard.rag.query_parse import parse_offline

            plan = parse_offline(body.question, settings.regions, p.today, p.group_aliases())
            p._set_comparison(plan, AskRequest(question=body.question, compare=body.compare))
        req = AskRequest(question=body.question, mode=body.mode, recency=body.recency, role_weight=body.role_weight,
                         scope=Scope(**body.scope.model_dump()))
        res, _, sources = p.find(plan, req)
        return {"timings": res.timings, "plan": plan.to_dict(), "sources": [s.card() for s in sources if s.kind in ("record", "edm", "devolved", "evidence")],
                "candidates": res.candidates, "semantic": res.semantic_available,
                "evidence": {**res.evidence, "devolution": notes_for(plan.question),
                             "edms": sum(1 for s in sources if s.kind == "edm"),
                             "devolved": sum(1 for s in sources if s.kind == "devolved"),
                             "committee_evidence": sum(1 for s in sources if s.kind == "evidence")}
                if res.evidence else res.evidence}

    def sources_only_events(body: AskBody, note: str, uid: int | None = None,
                            visitor: str | None = None) -> Iterator[dict[str, Any]]:
        # read the question with the model unless the site's own budget is used up
        r = retrieve(body, parts.caps.site_paused() is None, uid, visitor)
        yield {"type": "plan", "plan": r["plan"], "usage": None}
        yield {"type": "sources", **{k: r[k] for k in ("sources", "candidates", "semantic", "evidence")}}
        yield {"type": "final", "answer": None, "usage": None, "note": note}

    @app.post("/api/search")
    def search(body: AskBody, request: Request, user=Depends(current_user)):
        """Retrieval only (no model call): ranked sources for the question or edited plan."""
        requester(request, user)
        body = retrieve(body)
        timings = body.pop("timings", {})
        return JSONResponse(jsonable_encoder(body), headers={"Server-Timing": server_timing(timings)})

    def keep_briefing(body: AskBody, kept: list[dict[str, Any]], final: dict[str, Any]) -> dict[str, Any]:
        return keep(parts.archive, state, body.question, body.model_dump(exclude={"question", "history"}), kept,
                    final)

    # shared briefings
    def _briefing_or_error(bid: str) -> dict[str, Any]:
        b = get_briefing(parts.archive, bid)
        if b is None:
            raise HTTPException(404, "There is no briefing at this address.")
        if b["withdrawn_at"]:
            raise HTTPException(410, WITHDRAWN)
        note = hidden_note(replies, bid)
        if note is not None:  # hidden while a concern about it is decided: the note stands in its place
            raise HTTPException(423, note)
        return b

    def shown() -> ShownOnly:
        """The saved briefings as the public lists read them now: without those hidden for the time being."""
        return ShownOnly(parts.archive, {h["item"].removeprefix("/b/") for h in replies.all_hidden()})

    @app.get("/api/briefings/{bid}")
    def briefing(bid: str):
        return public_view(_briefing_or_error(bid))

    @app.get("/b/{bid}/card.png", include_in_schema=False)
    def briefing_card(bid: str):
        b = _briefing_or_error(bid)
        view = public_view(b)
        path = card_png(settings.data_dir / "cards", bid, briefing_title(b["created_at"]), fmt_date(b["created_at"]),
                        fmt_date(b["records_up_to"]), view["evidence"])
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/b/{bid}", include_in_schema=False)
    def briefing_page(bid: str, request: Request):
        """The app, with this briefing's title and link-preview card in the page head."""
        index = dist / "index.html"
        page = index.read_text() if index.is_file() else "<!doctype html><html><head></head><body></body></html>"
        b = get_briefing(parts.archive, bid) if ID_PATTERN.match(bid) else None
        base = str(request.base_url).rstrip("/")
        if b is None or b["withdrawn_at"]:
            gone = b is not None
            html_page = page_with_meta(
                page, title="Briefing withdrawn | Where MPs Stand" if gone else "Briefing not found | Where MPs Stand",
                description="This briefing is no longer available." if gone else "There is no briefing at this address.",
                url=f"{base}/b/{bid}", image=None)
            return Response(html_page, status_code=410 if gone else 404, media_type="text/html")
        note = hidden_note(replies, bid)
        if note is not None:  # hidden for now: the page says so (its script asks), and nothing of it is in the page
            html_page = page_with_meta(page, title=f"{NOT_SHOWN} | Where MPs Stand", description=note,
                                       url=f"{base}/b/{bid}", image=None)
            return Response(html_page, status_code=423, media_type="text/html")
        view = public_view(b)
        # never the reader's question, nor the model's words: either can repeat an allegation under the site's name.
        # A featured briefing, which the owner has read, may have the owner's own title and its opening as preview.
        featured = is_featured(state, bid)
        title = (featured_title(state, bid) if featured else None) or briefing_title(b["created_at"])
        description = ((summary_text(view["answer"]) if featured else "")
                       or briefing_description(b["created_at"], b["records_up_to"]))
        html_page = page_with_meta(
            page, title=f"{title} | Where MPs Stand", description=description, url=f"{base}/b/{bid}",
            noindex=not featured)  # featured briefings are for search engines to find
        return Response(with_text(html_page, readable(lambda: briefing_text(view))), media_type="text/html")

    @app.post("/api/admin/briefings/{bid}/withdraw")
    def withdraw(bid: str, body: WithdrawBody, user=Depends(admin_user)):
        if not ID_PATTERN.match(bid) or not parts.archive.withdraw(bid, body.reason):
            raise HTTPException(404, "There is no briefing at this address.")
        for card in card_paths(settings.data_dir / "cards", bid):
            card.unlink(missing_ok=True)
        parts.cache.delete_briefing(bid)  # and it is not played again to the next reader with the same question
        record.mark(bid, corrected=True)  # what happened next, on that briefing's record
        return {"ok": True}

    @app.get("/api/admin/briefings")
    def briefings_list(limit: int = 50, user=Depends(admin_user)):
        return {"briefings": recent_briefings(parts.archive, min(max(limit, 1), 500))}

    # topic briefings and featured briefings
    def _topic(slug: str):
        from hansard.topics import load_topics, topics_file

        t = next((t for t in load_topics(topics_file(settings)) if t.slug == slug), None)
        if t is None:
            raise HTTPException(404, "There is no topic at this address.")
        return t

    @app.get("/api/topics")
    def topics_index():
        from hansard.topics import topic_listing

        topics = [{k: v for k, v in t.items() if k != "last_check"}
                  for t in topic_listing(settings, state, shown())]
        return {"topics": topics, "featured": featured_list(shown(), state)}

    @app.get("/api/topics/{slug}")
    def topic_detail(slug: str):
        from hansard.topics import versions

        t = _topic(slug)
        vs = versions(state, shown(), slug)
        b = get_briefing(parts.archive, vs[0]["briefing_id"]) if vs else None
        return {"topic": {"slug": t.slug, "title": t.title, "area": t.area, "question": t.question,
                          "description": t.description, "refresh_days": t.refresh_days},
                "briefing": public_view(b) if b else None, "versions": vs}

    @app.get("/topics/{slug}", include_in_schema=False)
    def topic_page(slug: str, request: Request):
        """The app, showing the topic's current briefing, with its title and preview card in the page head."""
        from hansard.topics import current

        t = _topic(slug)
        index = dist / "index.html"
        page = index.read_text() if index.is_file() else "<!doctype html><html><head></head><body></body></html>"
        base = str(request.base_url).rstrip("/")
        cur = current(state, shown(), slug)
        b = get_briefing(parts.archive, cur["briefing_id"]) if cur else None
        description = (summary_text(public_view(b)["answer"]) if b else "") or t.description or t.question
        html_page = page_with_meta(
            page, title=f"{t.title}: where MPs and peers stand | Where MPs Stand", description=description,
            url=f"{base}/topics/{slug}", noindex=False)
        text = readable(lambda: briefing_text(public_view(b), heading=t.title)) if b else None
        return Response(with_text(html_page, text), media_type="text/html")

    @app.get("/sitemap-briefings.xml", include_in_schema=False)
    def sitemap_briefings(request: Request):
        from hansard.topics import topic_listing

        base = str(request.base_url).rstrip("/")
        urls = [f"<url><loc>{base}/topics</loc></url>"]
        for t in topic_listing(settings, state, shown()):
            if t["current"]:
                urls.append(f"<url><loc>{base}/topics/{t['slug']}</loc>"
                            f"<lastmod>{t['current']['prepared_at'][:10]}</lastmod></url>")
        urls += [f"<url><loc>{base}/b/{f['id']}</loc><lastmod>{f['prepared_at'][:10]}</lastmod></url>"
                 for f in featured_list(shown(), state)]
        return Response('<?xml version="1.0" encoding="UTF-8"?><urlset '
                        f'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{"".join(urls)}</urlset>',
                        media_type="application/xml")

    @app.get("/api/admin/topics")
    def admin_topics(user=Depends(admin_user)):
        from hansard.topics import topic_listing

        return {"topics": topic_listing(settings, state, parts.archive),
                "featured": featured_list(parts.archive, state)}

    @app.post("/api/admin/featured")
    def admin_feature(body: FeatureBody, user=Depends(admin_user)):
        m = re.search(r"([a-km-z2-9]{10})/?$", body.briefing.strip())
        if not m or not feature(parts.archive, state, m.group(1), body.title, body.blurb):
            raise HTTPException(404, "There is no briefing at that address (or it has been withdrawn).")
        return {"ok": True, "id": m.group(1)}

    @app.delete("/api/admin/featured/{bid}")
    def admin_unfeature(bid: str, user=Depends(admin_user)):
        if not unfeature(state, bid):
            raise HTTPException(404, "That briefing is not featured.")
        return {"ok": True}

    # bills
    def get_bills():
        from hansard.bills import Bills

        with lock:
            if "bills" not in holder:
                holder["bills"] = Bills(settings)
        return holder["bills"]

    @app.get("/api/bills")
    def bills_index():
        """Every bill debated since 2010, newest first (summary only)."""
        out = []
        for b in get_bills().all(state.latest_record_date()):
            last = b["sittings"][-1]
            out.append({k: b[k] for k in ("slug", "name", "years", "first", "last", "houses", "contributions")}
                       | {"sittings": len(b["sittings"]), "divisions": len(b["divisions"]),
                          "latest": f"{', '.join(last['stages'])} ({'Lords' if last['house'] == 'lords' else 'Commons'})"})
        return {"bills": out}

    @app.get("/api/bills/{slug}")
    def bill_detail(slug: str):
        b = get_bills().get(slug, state.latest_record_date())
        if b is None:
            raise HTTPException(404, "There is no bill at this address.")
        return b

    @app.get("/bills/{slug}", include_in_schema=False)
    def bill_page(slug: str, request: Request):
        b = get_bills().get(slug, state.latest_record_date())
        index = dist / "bill.html"
        page = index.read_text() if index.is_file() else "<!doctype html><html><head></head><body></body></html>"
        base = str(request.base_url).rstrip("/")
        if b is None:
            return Response(page_with_meta(page, title="Bill not found | Where MPs Stand",
                                           description="There is no bill at this address.", url=f"{base}/bills/{slug}",
                                           image=None), status_code=404, media_type="text/html")
        houses = " and ".join("the Lords" if h == "lords" else "the Commons" for h in b["houses"])
        # bills of the same name (a Finance Bill every year, a bill brought back in a new session) are told apart by
        # the years they were debated
        description = (f"The {b['name']} ({b['years']}) in Parliament: every debate and vote in {houses}, "
                       f"{fmt_date(b['first'])} to {fmt_date(b['last'])}, from the official record.")
        trail = breadcrumbs(base, [("Bills", "/bills"), (f"{b['name']} ({b['years']})", f"/bills/{slug}")])
        return Response(with_text(page_with_meta(page, title=f"{b['name']} ({b['years']}) | Where MPs Stand",
                                                 description=description, data=structured(trail),
                                                 url=f"{base}/bills/{slug}", image=None, noindex=False),
                                  readable(lambda: bill_text(b))), media_type="text/html")

    # debates
    def get_debates():
        from hansard.debates import Debates

        with lock:
            if "debates" not in holder:
                holder["debates"] = Debates(settings)
        return holder["debates"]

    _DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    _MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
    _YEAR = re.compile(r"^(19|20)\d{2}$")
    _SLUG = re.compile(r"^[a-z0-9-]{1,200}$")

    def find_debate(day: str, chamber: str, slug: str):
        if not (_DAY.match(day) and _SLUG.match(chamber) and _SLUG.match(slug)):
            return None
        return get_debates().get(day, chamber, slug, state.latest_record_date())

    @app.get("/api/debates/{day}/{chamber}/{slug}")
    def debate_detail(day: str, chamber: str, slug: str):
        """A whole debate as recorded, in order, with each speaker as they were on the day. Free: no AI."""
        d = find_debate(day, chamber, slug)
        if d is None:
            raise HTTPException(404, "There is no debate at this address.")
        return d

    @app.get("/debates/{day}/{chamber}/{slug}", include_in_schema=False)
    def debate_page(day: str, chamber: str, slug: str, request: Request):
        d = find_debate(day, chamber, slug)
        index = dist / "debate.html"
        page = index.read_text() if index.is_file() else "<!doctype html><html><head></head><body></body></html>"
        url = f"{str(request.base_url).rstrip('/')}/debates/{day}/{chamber}/{slug}"
        if d is None:
            return Response(page_with_meta(page, title="Debate not found | Where MPs Stand",
                                           description="There is no debate at this address.", url=url, image=None),
                            status_code=404, media_type="text/html")
        if d.get("kind") == "statement":
            description = (f"{d['title']}: a written statement to the {d['chamber_name']} on {fmt_date(d['date'])}"
                           + (f" ({d['department']})" if d.get("department") else "") + ", as published by Parliament.")
        else:
            description = (f"{d['title']}: the debate in the {d['chamber_name']} on {fmt_date(d['date'])}, as "
                           f"recorded in Hansard, with {d['contributions']} contributions from {d['speakers']} members.")
        from hansard.week import monday

        week = monday(date.fromisoformat(d["date"])).isoformat()
        trail = breadcrumbs(str(request.base_url).rstrip("/"), [
            (f"Week of {fmt_date(week)}", f"/weeks/{week}"),
            (f"{d['title']}, {fmt_date(d['date'])}", f"/debates/{day}/{chamber}/{slug}")])
        return Response(with_text(page_with_meta(page, title=f"{d['title']}, {fmt_date(d['date'])} | Where MPs Stand",
                                                 description=description, url=url, image=None, noindex=False,
                                                 data=structured(trail)),
                                  readable(lambda: debate_text(d))), media_type="text/html")

    # the week in Parliament
    def get_weeks():
        from hansard.week import Weeks

        with lock:
            if "weeks" not in holder:
                holder["weeks"] = Weeks(settings)
        return holder["weeks"]

    def find_week(start: str | None):
        from hansard.week import monday

        w = get_weeks()
        if start is None:
            day = w.latest()
        else:
            try:
                day = date.fromisoformat(start) if _DAY.match(start) else None
            except ValueError:
                day = None
        return w.get(monday(day), state.latest_record_date()) if day else None

    @app.get("/api/week")
    def week_detail(start: str | None = None):
        """One sitting week (Monday to Sunday; the latest if no start is given). Free: no AI."""
        return remembered(f"week:{start}", lambda: week_data(start))

    def week_data(start: str | None):
        w = find_week(start)
        if w is None:
            raise HTTPException(404, "Parliament did not sit that week, or the week is outside the record.")
        if start is None:  # "this week": say so when Parliament has not sat lately (a recess)
            return {**w, "pause": get_weeks().pause(date.today())}
        return w

    def week_page(start: str | None, request: Request) -> Response:
        w = find_week(start)
        index = dist / "week.html"
        page = index.read_text() if index.is_file() else "<!doctype html><html><head></head><body></body></html>"
        base = str(request.base_url).rstrip("/")
        if w is None:
            return Response(page_with_meta(page, title="Week not found | Where MPs Stand",
                                           description="Parliament did not sit that week.",
                                           url=f"{base}/weeks/{start}", image=None),
                            status_code=404, media_type="text/html")
        url = f"{base}/this-week" if start is None else f"{base}/weeks/{w['start']}"
        span = f"{fmt_date(w['start'])} to {fmt_date(w['end'])}"
        return Response(with_text(page_with_meta(
            page, title=f"In Parliament, week of {fmt_date(w['start'])} | Where MPs Stand",
            description=f"The debates, votes, bills and statements in Parliament, {span}, from the official record.",
            url=url, image=None, noindex=False), readable(lambda: week_text(w))), media_type="text/html")

    @app.get("/this-week", include_in_schema=False)
    def this_week_page(request: Request):
        return week_page(None, request)

    @app.get("/weeks/{start}", include_in_schema=False)
    def a_week_page(start: str, request: Request):
        return week_page(start, request)

    # early day motions
    # the public search API (no AI)
    @app.get("/api/v1/search")
    def public_search(request: Request, q: str = "", date_from: str | None = None, date_to: str | None = None,
                      house: str | None = None, party: str | None = None, written: bool = True,
                      limit: int = 20, user=Depends(current_user)):
        """Search the Westminster record since 1997, ranked by relevance. Free; no AI. See /developers."""
        from hansard.engine import Filters

        requester(request, user)
        q = " ".join(q.split())[:300]
        if len(q) < 2:
            raise HTTPException(400, "Give some words to search for (q).")
        try:
            filters = Filters(date_from=date.fromisoformat(date_from) if date_from else None,
                              date_to=date.fromisoformat(date_to) if date_to else None,
                              houses=[house] if house in ("commons", "lords") else [],
                              parties=[party] if party else [])
        except ValueError as exc:
            raise HTTPException(400, "Dates must be written as YYYY-MM-DD.") from exc
        res = get_pipeline().engine.search(q, filters, Scope(written=written), [], 0.0, 1.0,
                                           max(1, min(int(limit), 50)), diversify=False)
        out = []
        for r in res.records:
            sp = r.get("speaker") or {}
            out.append({"record_id": r["record_id"], "date": r["date"], "house": r["house"], "chamber": r["chamber"],
                        "type": r["record_type"], "heading": r.get("heading"),
                        "speaker": {"name": sp.get("name") or r.get("speaker_name_raw"), "party": sp.get("party"),
                                    "constituency": sp.get("constituency"), "person_id": sp.get("person_id")},
                        "excerpt": " ".join((r.get("excerpt") or "").split())[:1000],
                        "column": r.get("colnum"), "uin": r.get("uin"),
                        "url": r.get("official_url") or r.get("twfy_url"), "twfy_url": r.get("twfy_url"),
                        "relevance": round(r["ranking"]["relevance"], 4) if r.get("ranking") else None})
        body = {"query": q, "results": out, "count": len(out),
                "licence": "Contains Parliamentary information licensed under the Open Parliament Licence v3.0."}
        return JSONResponse(body, headers={"Server-Timing": server_timing(res.timings)})

    # the devolved parliaments
    def get_devolved():
        from hansard.devolved import DevolvedStore, Members
        from hansard.sync import devolved_path

        with lock:
            if "devolved" not in holder:
                holder["devolved"] = (DevolvedStore(devolved_path(settings)),
                                      Members(settings.parlparse_dir / "members" / "people.json"))
        return holder["devolved"]

    def devolved_speaker(members, pp, leg: str, row: dict) -> dict:
        from hansard.devolved import LEGISLATURES

        on = members.on(row.get("person_id"), LEGISLATURES[leg]["org"], row["date"])
        raw = re.sub(r"^\d+\.\s*", "", row.get("speaker") or "")
        number = None
        if row.get("person_id"):
            from hansard.people import person_number

            number = person_number(row["person_id"])
        name = on.get("name") or raw or None
        role = raw.split("(")[0].strip() if raw.lower().startswith(("the ", "deputy ")) else None
        return {"name": name, "party": on.get("party"), "seat": on.get("seat"), "role": role, "number": number,
                "slug": re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-") if number else None}

    @app.get("/api/devolved")
    def devolved_index():
        """The devolved parliaments' recent sitting days. Free: no AI."""
        from hansard.devolved import LEGISLATURES

        store, _ = get_devolved()
        counts = store.counts()
        return {"legislatures": [{"id": k, "name": v["name"], "member": v["member"], "nation": v["nation"],
                                  **(counts.get(k) or {"speeches": 0}), "days": store.days(k, 12),
                                  "years": devolved_years(k)}
                                 for k, v in LEGISLATURES.items()]}

    @app.get("/api/devolved/search")
    def devolved_search(q: str = "", leg: str | None = None, page: int = 1):
        from hansard.devolved import LEGISLATURES

        if leg and leg not in LEGISLATURES:
            raise HTTPException(404, "Unknown parliament.")
        store, members = get_devolved()
        page = max(1, min(page, 200))
        total, rows = store.search(q[:200], leg, limit=25, offset=(page - 1) * 25)
        pp = get_people()
        return {"total": total, "page": page, "per_page": 25,
                "items": [{**r, "speaker": devolved_speaker(members, pp, r["legislature"], r),
                           "body": LEGISLATURES[r["legislature"]]["name"]} for r in rows]}

    @app.get("/api/devolved/{leg}")
    def devolved_days(leg: str, before: str | None = None):
        from hansard.devolved import LEGISLATURES

        if leg not in LEGISLATURES or (before and not _DAY.match(before)):
            raise HTTPException(404, "Unknown parliament.")
        store, _ = get_devolved()
        return {"id": leg, **LEGISLATURES[leg], "days": store.days(leg, 40, before)}

    @app.get("/api/devolved/{leg}/{key}")
    def devolved_day(leg: str, key: str):
        from hansard.devolved import LEGISLATURES

        if leg not in LEGISLATURES or not _SLUG.match(key.replace("_", "-")):
            raise HTTPException(404, "Unknown parliament.")
        store, members = get_devolved()
        rows = store.day(leg, key)
        if not rows:
            raise HTTPException(404, "There is no sitting at this address.")
        pp = get_people()
        items, section = [], None
        for r in rows:
            head = r["major"] or ""
            if head and head != section:
                section = head
                items.append({"kind": "heading", "text": head, "id": f"h-{r['ord']}"})
            items.append({"kind": "said" if r["person_id"] else "procedural", "id": f"s{r['ord']}",
                          "minor": r["minor"], "time": r["time"], "url": r["url"],
                          "speaker": devolved_speaker(members, pp, leg, r) if r["person_id"] else None,
                          "paragraphs": [p for p in r["text"].split("\n\n") if p]})
        return {"id": leg, "body": LEGISLATURES[leg]["name"], "member": LEGISLATURES[leg]["member"],
                "key": key, "date": rows[0]["date"], "items": items,
                "speakers": len({r["person_id"] for r in rows if r["person_id"]}),
                "contributions": sum(1 for r in rows if r["person_id"]),
                "official_url": next((r["url"] for r in rows if r["url"]), None)}

    @app.get("/devolved/{leg}/{key}", include_in_schema=False)
    def devolved_day_page(leg: str, key: str, request: Request):
        from hansard.devolved import LEGISLATURES

        index = dist / "devolvedday.html"
        page = index.read_text() if index.is_file() else "<!doctype html><html><head></head><body></body></html>"
        base = str(request.base_url).rstrip("/")
        rows = get_devolved()[0].day(leg, key) if leg in LEGISLATURES else []
        if not rows:
            return Response(page_with_meta(page, title="Not found | Where MPs Stand", description="Nothing here.",
                                           url=f"{base}/devolved/{leg}/{key}", image=None),
                            status_code=404, media_type="text/html")
        name = LEGISLATURES[leg]["name"]
        return Response(with_text(page_with_meta(page, title=f"{name}, {fmt_date(rows[0]['date'])} | Where MPs Stand",
                                                 description=f"What was said in the {name} on {fmt_date(rows[0]['date'])}, "
                                                             "as recorded in its official report, with each member's "
                                                             "party and seat.",
                                                 url=f"{base}/devolved/{leg}/{key}", image=None, noindex=False),
                                  readable(lambda: devolved_day_text(devolved_day(leg, key)))),
                        media_type="text/html")

    def get_committees():
        from hansard.sources.committees import Committees
        from hansard.sync import committees_path

        with lock:
            if "committees" not in holder:
                holder["committees"] = Committees(committees_path(settings))
        c = holder["committees"]
        if not c.available():
            raise HTTPException(503, "Committee information is being collected and will appear after the next update.")
        return c

    @app.get("/api/committees")
    def committees_index():
        """Current select and other committees of both Houses. Free: no AI."""
        c = get_committees()
        return {"committees": c.index(), "totals": c.stats()}

    @app.get("/api/committees/{cid}")
    def committee_detail(cid: int):
        d = get_committees().committee(cid)
        if d is None:
            raise HTTPException(404, "There is no committee with that number.")
        return d

    @app.get("/api/inquiries/{iid}")
    def inquiry_detail(iid: int):
        d = get_committees().inquiry(iid)
        if d is None:
            raise HTTPException(404, "There is no inquiry with that number.")
        have = get_evidence().available()
        for s in d["sessions"]:
            s["transcript"] = s["id"] in have
        return d

    def get_evidence():
        from hansard.sources.evidence import EvidenceStore
        from hansard.sync import evidence_path

        with lock:
            if "evidence" not in holder:
                holder["evidence"] = EvidenceStore(evidence_path(settings))
        return holder["evidence"]

    @app.get("/api/evidence")
    def evidence_search(q: str = "", page: int = 1):
        """What witnesses and committee members said in oral evidence (transcripts collected so far)."""
        page = max(1, min(page, 200))
        total, rows = get_evidence().search(q[:200], limit=25, offset=(page - 1) * 25)
        sessions = {r["session_id"] for r in rows}
        meta = get_committees().sessions_meta(sessions) if sessions else {}
        return {"total": total, "page": page, "per_page": 25,
                "items": [{**r, **meta.get(r["session_id"], {})} for r in rows]}

    @app.get("/api/evidence/{sid}")
    def evidence_detail(sid: int):
        t = get_evidence().transcript(sid)
        if t is None:
            raise HTTPException(404, "There is no transcript for that session here yet.")
        return {**t, **get_committees().sessions_meta({sid}).get(sid, {})}

    @app.get("/evidence/{sid}", include_in_schema=False)
    def evidence_page(sid: int, request: Request):
        index = dist / "evidence.html"
        page = index.read_text() if index.is_file() else "<!doctype html><html><head></head><body></body></html>"
        url = f"{str(request.base_url).rstrip('/')}/evidence/{sid}"
        t = get_evidence().transcript(sid)
        if t is None:
            return Response(page_with_meta(page, title="Transcript not found | Where MPs Stand",
                                           description="There is no transcript for that session here yet.", url=url,
                                           image=None), status_code=404, media_type="text/html")
        title = t["title"] or "Oral evidence"
        try:
            session = get_committees().sessions_meta({sid}).get(sid, {})
        except HTTPException:
            session = {}
        trail = [("Committees", "/committees")]
        if session.get("committee_id"):
            trail.append(((session.get("committees") or "Committee").split("; ")[0],
                          f"/committees/{session['committee_id']}"))
        if session.get("inquiry_id"):
            trail.append((session.get("inquiry") or "Inquiry", f"/inquiries/{session['inquiry_id']}"))
        trail.append((title, f"/evidence/{sid}"))
        return Response(with_text(page_with_meta(page, title=f"{title} | Where MPs Stand",
                                                 description=f"{title}: the transcript of a committee evidence session, "
                                                             "with who said what.", url=url, image=None, noindex=False,
                                                 data=structured(breadcrumbs(str(request.base_url).rstrip("/"),
                                                                             trail))),
                                  readable(lambda: evidence_text({**t, **session}))),
                        media_type="text/html")

    @app.get("/api/witnesses")
    def witness_search(q: str = ""):
        """Organisations and people who gave oral evidence to committees, and how often."""
        return get_committees().witnesses(q[:200])

    def committee_meta_page(kind: str, ident: int, request: Request) -> Response:
        index = dist / f"{kind}.html"
        page = index.read_text() if index.is_file() else "<!doctype html><html><head></head><body></body></html>"
        base = str(request.base_url).rstrip("/")
        try:
            c = get_committees()
            d = c.committee(ident) if kind == "committee" else c.inquiry(ident)
        except HTTPException:
            d = None
        path = f"/committees/{ident}" if kind == "committee" else f"/inquiries/{ident}"
        if d is None:
            return Response(page_with_meta(page, title="Not found | Where MPs Stand", description="Nothing here.",
                                           url=base + path, image=None), status_code=404, media_type="text/html")
        if kind == "committee":
            title, desc = d["name"], (d.get("purpose") or f"The {d['name']}: its inquiries, reports and the "
                                      "organisations it has heard from.")
        else:
            title = d["title"]
            desc = (f"{d['title']}: an inquiry by the " + " and ".join(x["name"] for x in d["committees"]) +
                    ", with its reports, the Government's responses and who gave evidence.")
        if kind == "inquiry":  # which sessions have a transcript here, to link to
            have = get_evidence().available()
            d = {**d, "sessions": [{**x, "transcript": x["id"] in have} for x in d["sessions"]]}
        text = committee_text if kind == "committee" else inquiry_text
        above = [] if kind == "committee" else [(c["name"], f"/committees/{c['id']}") for c in d["committees"][:1]
                                                if c.get("id")]
        trail = breadcrumbs(base, [("Committees", "/committees"), *above, (title, path)])
        return Response(with_text(page_with_meta(page, title=f"{title} | Where MPs Stand", description=desc[:300],
                                                 url=base + path, image=None, noindex=False, data=structured(trail)),
                                  readable(lambda: text(d))),
                        media_type="text/html")

    @app.get("/committees/{cid}", include_in_schema=False)
    def committee_page(cid: int, request: Request):
        return committee_meta_page("committee", cid, request)

    @app.get("/inquiries/{iid}", include_in_schema=False)
    def inquiry_page(iid: int, request: Request):
        return committee_meta_page("inquiry", iid, request)

    def get_register():
        from hansard.sources.regmem import Register
        from hansard.sync import regmem_path

        with lock:
            if "register" not in holder:
                holder["register"] = Register(regmem_path(settings))
        return holder["register"]

    def get_donations():
        from hansard.sources.donations import Donations
        from hansard.sync import donations_path

        with lock:
            if "donations" not in holder:
                holder["donations"] = Donations(donations_path(settings))
        return holder["donations"]

    def get_directorships():
        from hansard.sources.companies import Directorships
        from hansard.sync import directorships_path

        with lock:
            if "directorships" not in holder:
                holder["directorships"] = Directorships(directorships_path(settings))
        return holder["directorships"]

    @app.get("/api/directorships")
    def directorships_index(q: str = ""):
        """Members' current company roles at Companies House, by company; ``q`` searches companies and members."""
        return get_directorships().index(q[:200])

    def get_groups():
        from hansard.sources.appgs import Groups
        from hansard.sync import appgs_path

        with lock:
            if "groups" not in holder:
                holder["groups"] = Groups(appgs_path(settings))
        return holder["groups"]

    lighter_lock = threading.Lock()

    @app.get("/api/lighter-side")
    def lighter_side():
        """What made the House laugh, the same week in earlier years, and early day motions you might not expect:
        from the record by plain rules, no AI. Rebuilt every few hours."""
        def build() -> dict[str, Any]:
            import duckdb

            from hansard.lighter import Lighter, lighter_edms
            from hansard.reference.profiles import attach_reference
            from hansard.store.parquet import attach_store
            from hansard.sync import edms_path

            with lighter_lock:
                con = duckdb.connect()
                try:
                    stores = set(attach_store(con, settings.parquet_dir))
                    attach_reference(con, settings.reference_dir)
                    lighter = Lighter(con)
                    found = ({"laughter": lighter.laughter(), "on_this_day": lighter.on_this_day()}
                             if "records" in stores else {"laughter": [], "on_this_day": []})
                finally:
                    con.close()
            people = get_people()
            for section in found.values():  # each speaker's page and party on the day
                for m in section:
                    who = people.brief(m.pop("person_id"), m["date"]) if m.get("person_id") else None
                    m["member"] = who
            edms = lighter_edms(edms_path(settings))
            names = people.by_parliament_id([(e["mnis"], e["date"]) for e in edms if e.get("mnis")])
            for e in edms:  # the member's name as on their page ("John McDonnell"), not as the motion lists it
                e["tabled_by"] = (names.get((e.pop("mnis"), e["date"])) or {}).get("name") or e["tabled_by"]
            return {**found, "edms": edms, "built": date.today().isoformat()}

        return remembered("lighter-side", build, ttl=6 * 3600)

    @app.get("/api/groups")
    def groups_index(q: str = "", parliament: str = "uk"):
        """All-party parliamentary groups (or a devolved parliament's cross-party groups), and their biggest
        registered funders. Free: no AI."""
        return remembered(f"groups:{parliament}:{q[:200]}", lambda: groups_index_data(q, parliament))

    def groups_index_data(q: str, parliament: str):
        from hansard.sources.appgs import PARLIAMENTS

        if parliament not in PARLIAMENTS:
            raise HTTPException(404, "Unknown parliament.")
        return get_groups().index(q[:200], parliament)

    @app.get("/api/groups/funder")
    def groups_funder(name: str):
        return {"funder": name, "benefits": get_groups().funder(name[:300])}

    @app.get("/api/groups/{parliament}/{slug}")
    def group_detail(parliament: str, slug: str):
        g = get_groups().group(slug, parliament)
        if g is None:
            raise HTTPException(404, "There is no group at this address.")
        people = get_people()
        for m in g["members"]:
            m["member"] = people.brief(m["person_id"], m["updated"]) if m["person_id"] else None
        return g

    @app.get("/groups/{parliament}/{slug}", include_in_schema=False)
    def group_page(parliament: str, slug: str, request: Request):
        index = dist / "group.html"
        page = index.read_text() if index.is_file() else "<!doctype html><html><head></head><body></body></html>"
        g = get_groups().group(slug, parliament)
        base = str(request.base_url).rstrip("/")
        if g is None:
            return Response(page_with_meta(page, title="Group not found | Where MPs Stand",
                                           description="There is no group at this address.",
                                           url=f"{base}/groups/{parliament}/{slug}", image=None),
                            status_code=404, media_type="text/html")
        # a group's title runs to 107 characters and its purpose to 280: cut at a word for the page title and the
        # search result's description (the page itself has them in full)
        desc = shorten(g["purpose"] or g["title"], 155)
        return Response(with_text(page_with_meta(page, title=f"{shorten(g['title'], 60)} | Where MPs Stand",
                                                 description=desc,
                                                 url=f"{base}/groups/{parliament}/{slug}", image=None, noindex=False),
                                  readable(lambda: group_text(group_detail(parliament, slug)))),
                        media_type="text/html")

    @app.get("/api/donations")
    def donations_index():
        """The biggest donors to MPs in the last five years and the latest donations, as registered with the
        Electoral Commission and matched to MPs. Free: no AI."""
        return remembered("donations", donations_index_data)

    def donations_index_data():
        out = get_donations().index()
        people = get_people()
        for r in out.get("recent", []):
            r["member"] = people.brief(r["person_id"], r["accepted"]) if r.get("person_id") else None
        return out

    @app.get("/api/donations/donors")
    def donations_donors(q: str = ""):
        return {"query": q, "donors": get_donations().donors(q[:200])}

    @app.get("/api/donations/donor")
    def donations_by_donor(name: str):
        people = get_people()
        rows = get_donations().donor(name[:300])
        for r in rows:
            r["member"] = people.brief(r["person_id"], r["accepted"]) if r.get("person_id") else None
        return {"donor": name, "donations": rows}

    def get_registers():
        from hansard.sources.registers import Registers

        with lock:
            if "registers" not in holder:
                holder["registers"] = Registers(settings.data_dir)
        return holder["registers"]

    def get_edms():
        from hansard.sources.edms import EdmStore
        from hansard.sync import edms_path

        with lock:
            if "edms" not in holder:
                holder["edms"] = EdmStore(edms_path(settings))
        return holder["edms"]

    def edm_member(found: dict, mnis: int | None, on: str | None, name: str | None, party: str | None) -> dict:
        m = found.get((mnis, on)) or {}
        return {"name": m.get("name") or name, "party": m.get("party") or party, "number": m.get("number"),
                "slug": m.get("slug")}

    def edm_rows(rows: list[dict]) -> list[dict]:
        found = get_people().by_parliament_id([(r["primary_mnis"], r["date_tabled"]) for r in rows
                                               if r["primary_mnis"]])
        # older motions' titles are in capitals ("WINNIE EWING"): shown as titles, acronyms kept
        return [{**{k: r[k] for k in ("id", "uin", "date_tabled", "status", "sponsors_count", "prayer")},
                 "title": title_case(r["title"]),
                 "sponsor": edm_member(found, r["primary_mnis"], r["date_tabled"], r["primary_name"],
                                       r["primary_party"])} for r in rows]

    @app.get("/api/edms")
    def edms_index(q: str = "", page: int = 1, person: int | None = None):
        """Early day motions, newest first, optionally matching words or signed by one member. Free: no AI."""
        store = get_edms()
        page = max(1, min(page, 500))
        mnis = None
        if person is not None:
            pp = get_people()
            pid = pp.resolve(person)
            info = pp.page(pid) if pid else None
            if not info or not info.get("parliament_id"):
                raise HTTPException(404, "There is no member with that number.")
            mnis = [int(info["parliament_id"])]
        total, rows = store.search(q[:200], mnis=mnis, limit=25, offset=(page - 1) * 25)
        return {"total": total, "page": page, "per_page": 25, "items": edm_rows(rows), "available": store.count() > 0}

    @app.get("/api/edms/months")
    def edms_by_month():
        """Each month motions were tabled in, newest first, with how many: the archive of motions by month."""
        return {"months": [{"month": m, "count": n} for m, n in edm_months()]}

    @app.get("/api/edms/{edm_id}")
    def edm_detail(edm_id: int):
        e = get_edms().get(edm_id)
        if e is None:
            raise HTTPException(404, "There is no early day motion with that number.")
        pairs = [(s["mnis"], s["signed"]) for s in e["sponsors"]]
        if e["primary_mnis"]:
            pairs.append((e["primary_mnis"], e["date_tabled"]))
        found = get_people().by_parliament_id(pairs)
        sponsors = [{**edm_member(found, s["mnis"], s["signed"], s["name"], s["party"]),
                     **{k: s[k] for k in ("sponsoring_order", "signed", "withdrawn")}} for s in e["sponsors"]]
        parties: dict[str, int] = {}
        for s in sponsors:
            if not s["withdrawn"]:
                parties[s["party"] or "Unknown"] = parties.get(s["party"] or "Unknown", 0) + 1
        return {**{k: e[k] for k in ("id", "uin", "text", "date_tabled", "status", "status_date",
                                     "sponsors_count", "amendment_to", "prayer")},
                "title": title_case(e["title"]),
                "sponsor": edm_member(found, e["primary_mnis"], e["date_tabled"], e["primary_name"],
                                      e["primary_party"]),
                "sponsors": sponsors, "signatures_known": bool(e["sponsors_fetched"]),
                "party_counts": sorted(parties.items(), key=lambda x: (-x[1], x[0])),
                "amendments": e["amendments"],
                "official_url": f"https://edm.parliament.uk/early-day-motion/{e['id']}"}

    @app.get("/edms/{edm_id}", include_in_schema=False)
    def edm_page(edm_id: int, request: Request):
        e = get_edms().get(edm_id)
        index = dist / "edm.html"
        page = index.read_text() if index.is_file() else "<!doctype html><html><head></head><body></body></html>"
        url = f"{str(request.base_url).rstrip('/')}/edms/{edm_id}"
        if e is None:
            return Response(page_with_meta(page, title="Early day motion not found | Where MPs Stand",
                                           description="There is no early day motion with that number.", url=url,
                                           image=None), status_code=404, media_type="text/html")
        when = fmt_date(e["date_tabled"]) if e["date_tabled"] else "an unknown date"
        return Response(with_text(page_with_meta(
            page, title=f"{shorten(title_case(e['title']), 60)} (early day motion) | Where MPs Stand",
            description=f"Early day motion tabled on {when} by {e['primary_name']}, with "
                        f"{e['sponsors_count'] or 0} signatures: the text and who signed.",
            url=url, image=None, noindex=False), readable(lambda: edm_text(edm_detail(edm_id)))), media_type="text/html")

    # charts
    chart_slots = threading.Semaphore(2)  # each chart scans the record for a few seconds; keep the server responsive

    def get_trends():
        from hansard.trends import Trends

        today = get_pipeline().today  # before taking the lock: get_pipeline takes it too
        with lock:
            if "trends" not in holder:
                holder["trends"] = Trends(settings, today)
        return holder["trends"]

    @app.get("/api/trends")
    def trends(request: Request, q: str = "", by: str = "all", user=Depends(current_user)):
        """How often words were used in Parliament each year, with the context to read the numbers (free)."""
        requester(request, user)
        phrases = [p for p in q.split(",") if p.strip()]
        if by == "parliaments":
            return parliaments_chart(phrases[:1])
        if not chart_slots.acquire(timeout=30):
            raise HTTPException(503, "The site is busy drawing other charts. Please try again in a moment.")
        try:
            return get_trends().chart(phrases, by, str(state.latest_record_date()))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        finally:
            chart_slots.release()

    def parliaments_chart(phrases: list[str]) -> dict[str, Any]:
        """Westminster's two Houses and the three devolved parliaments, side by side, as rates (comparable)."""
        from hansard.devolved import LEGISLATURES
        from hansard.trends import clean_phrase

        try:
            phrase = clean_phrase(phrases[0]) if phrases else ""
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not phrase:
            raise HTTPException(400, "Give a word or phrase.")
        if not chart_slots.acquire(timeout=30):
            raise HTTPException(503, "The site is busy drawing other charts. Please try again in a moment.")
        try:
            base = get_trends().chart([phrase], "house", str(state.latest_record_date()))
        finally:
            chart_slots.release()
        dev = get_devolved()[0].yearly(phrase)
        first = min((min(v) for v in dev.values() if v), default=None)
        years = [y for y in base["years"] if first is None or y >= first]
        keep = [base["years"].index(y) for y in years]
        series = [{**s, **{k: [s[k][i] for i in keep] for k in ("counts", "totals", "rate")}} for s in base["series"]]
        for leg, cfg in LEGISLATURES.items():
            by_year = dev.get(leg) or {}
            if not by_year:
                continue
            counts = [by_year[y][0] if y in by_year else None for y in years]
            totals = [by_year[y][1] if y in by_year else None for y in years]
            series.append({"key": f"{phrase}|{leg}", "phrase": phrase, "group": leg, "label": cfg["name"],
                           "counts": counts, "totals": totals,
                           "rate": [round(10000 * c / t, 2) if c is not None and t else None
                                    for c, t in zip(counts, totals, strict=True)]})
        partial = sorted({*[y for y in base["partial_years"] if y in years], *([first] if first else [])})
        return {"phrases": [phrase], "by": "parliaments", "years": years, "series": series,
                "partial_years": partial, "total": sum(sum(c or 0 for c in s["counts"]) for s in series)}

    # timelines
    @app.get("/api/timeline")
    def timeline_api(request: Request, q: str = "", person: int | None = None, party: str | None = None,
                     user=Depends(current_user)):
        """What a member or a party said on a topic, and how they voted, in date order (free: no AI)."""
        from hansard.timeline import timeline

        requester(request, user)
        q = " ".join(q.split())
        if len(q) < 3:
            raise HTTPException(400, "Give a topic of at least three letters.")
        pid = get_people().resolve(person) if person is not None else None
        if person is not None and pid is None:
            raise HTTPException(404, "There is no member with that number.")
        if (pid is None) == (not party):
            raise HTTPException(400, "Choose a member or a party.")
        if party and party not in {p for p, _ in get_pipeline().parties()}:
            raise HTTPException(404, "There is no party with that name.")
        return timeline(get_pipeline().engine, get_people(), q, person_id=pid, party_id=party)

    # MPs and peers
    @app.get("/api/people")
    def people_list(q: str = "", everyone: bool = False):
        # everyone since 1997 is a few thousand: all of them, for the list of former members
        return {"people": get_people().search(q.strip()[:80], current_only=not everyone,
                                              limit=20_000 if everyone else 2000)}

    @app.get("/api/people/{number}")
    def person(number: int, part: Literal["all", "core", "activity", "interests"] = "all"):
        """A member's page. The page asks for it in three parts so the first shows at once: ``core`` (who they are,
        seats, roles, membership), ``activity`` (contributions, votes, motions, devolved and committee evidence)
        and ``interests`` (registers, donations, company roles, groups). Without ``part``, everything."""
        if part == "all":
            return remembered(f"person:{number}", lambda: {**person_core(number), **person_activity(number),
                                                            **person_interests(number)})
        fn = {"core": person_core, "activity": person_activity, "interests": person_interests}[part]
        return remembered(f"person:{number}:{part}", lambda: fn(number))

    @app.get("/api/people/{number}/contributions")
    def person_contributions(number: int, offset: int = Query(0, ge=0, le=2000), limit: int = Query(10, ge=1, le=50)):
        """More of a member's contributions, newest first (substantive ones: not "Will the member give way?")."""
        pp = get_people()
        pid = pp.resolve(number)
        if not pid:
            raise HTTPException(404, "There is no member with that number.")
        rows = remembered(f"person:{number}:contrib:{offset + limit + 1}",
                          lambda: pp.contributions(pid, limit=offset + limit + 1))
        return {"contributions": rows[offset:offset + limit], "more": len(rows) > offset + limit}

    def person_core(number: int) -> dict[str, Any]:
        pp = get_people()
        pid = pp.resolve(number)
        page = pp.page(pid) if pid else None
        if page is None:
            raise HTTPException(404, "There is no member with that number.")
        try:
            store, members = get_devolved()
            ms = members.memberships(pid)
            now = [m for m in ms if m["end"] is None]
            if now and not page.get("current"):  # a sitting MSP, MS or MLA (and not an MP or peer)
                m = now[-1]
                page = {**page, "status": f"{m['member']} for {m['seat']} ({m['body']})"
                                          + (f", {m['party']}" if m["party"] else "")}
        except (OSError, ValueError):
            pass
        # party groups and caucuses, with the evidence for each, by Parliament
        page = {**page, "party_groups": get_pipeline().engine.groups().all_for(pid)}
        # a sitting MP's councils, on Where Councils Stand, and the topic briefings that quote them (decisions 41, 42)
        seat = page.get("current") or {}
        page["councils"] = councils_for_seat(settings, seat.get("gss_code")) if seat.get("house") == "commons" else []
        page["quoted_in"] = [{k: q[k] for k in ("title", "path", "prepared_at")} for q in quoted_index().get(pid, [])]
        return page

    def quoted_index() -> dict[str, list[dict[str, Any]]]:
        """Who each topic briefing quotes, worked out again when the topics may have changed (ten minutes)."""
        from hansard.topics import quoted_index as index

        return remembered("topics:quoted", lambda: index(settings, state, shown()))

    def person_activity(number: int) -> dict[str, Any]:
        pp = get_people()
        pid = pp.resolve(number)
        if not pid:
            raise HTTPException(404, "There is no member with that number.")
        page = remembered(f"person:{number}:core", lambda: person_core(number))
        edms = None
        if page.get("parliament_id"):
            try:
                edms = get_edms().member_summary(int(page["parliament_id"]), recent=8)
                edms["recent"] = edm_rows(edms["recent"])
            except (ValueError, OSError):
                edms = None
        evidence = []
        if page.get("parliament_id"):
            try:
                evidence = get_committees().for_member(int(page["parliament_id"]))
            except (HTTPException, ValueError, OSError):
                evidence = []
        devolved = None
        try:
            store, members = get_devolved()
            ms = members.memberships(pid)
            if ms:
                devolved = {"memberships": ms, **store.for_person(pid)}
        except (OSError, ValueError):
            devolved = None
        contributions = pp.contributions(pid, limit=11)
        return {"contributions": contributions[:10], "contributions_more": len(contributions) > 10,
                "voting": pp.voting(pid), "edms": edms, "devolved": devolved, "committee_evidence": evidence,
                "topics": pp.topics(pid)}

    def person_interests(number: int) -> dict[str, Any]:
        from hansard.sources.companies import mark_declared as _mark_declared

        pp = get_people()
        pid = pp.resolve(number)
        if not pid:
            raise HTTPException(404, "There is no member with that number.")
        interests, registers = get_register().for_person(pid), get_registers().for_person(pid)
        return {"interests": interests, "registers": registers,
                "donations": get_donations().for_person(pid), "groups": get_groups().for_person(pid),
                "directorships": _mark_declared(get_directorships().for_person(pid),
                                               json.dumps([interests, registers], default=str))}

    @app.get("/api/postcode/{postcode}")
    def postcode(postcode: str, request: Request, user=Depends(current_user)):
        requester(request, user)
        key = "postcode:" + re.sub(r"\s+", "", postcode).upper()[:10]
        try:  # seats change only at boundary reviews; kept in memory for a day, never written down
            found = remembered(key, lambda: get_people().by_postcode(postcode), ttl=86400)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except Exception:  # Parliament's service could not be reached
            raise HTTPException(503, "The postcode service could not be reached. Please try again.") from None
        if not found:
            raise HTTPException(404, "No constituency was found for that postcode.")
        return found

    # Index and archive pages. The index pages' own files (web/*.html) are filled in with plain lists of links
    # (plaintext.py), and their archives (bills by year, motions by month, former members, devolved sittings by year,
    # every sitting week) are made from the same files, so that anything that does not run the pages' scripts can
    # still reach every page of the record in a few steps.
    def html(page: str, status_code: int = 200) -> Response:
        return Response(page, status_code=status_code, media_type="text/html")

    def not_found(name: str, url: str) -> Response:
        return html(page_with_meta(template(name), title="Not found | Where MPs Stand", description="Nothing here.",
                                   url=url, image=None), 404)

    def archive_page(name: str, request: Request, path: str, title: str, description: str, heading: str,
                     intro: str | None, fragment: Callable[[], str]) -> Response:
        page = page_with_meta(template(name), title=f"{title} | Where MPs Stand", description=description,
                              url=str(request.base_url).rstrip("/") + path, image=None, noindex=False)
        return html(with_list(with_heading(page, heading, intro), readable(fragment)))

    def bill_years(bills: list[dict[str, Any]]) -> list[str]:
        """The years bills were debated in, newest first: a bill counts in each year from its first sitting to its
        last (as the page's app counts them, web/src/billsIndex.ts)."""
        return sorted({str(y) for b in bills for y in range(int(b["first"][:4]), int(b["last"][:4]) + 1)},
                      reverse=True)

    @app.get("/bills", include_in_schema=False)
    def bills_page():
        bills = get_bills().all(state.latest_record_date())
        return html(with_list(template("bills"), readable(lambda: bills_list_text(bills[:100], bill_years(bills),
                                                                                     len(bills)))))

    @app.get("/bills/year/{year}", include_in_schema=False)
    def bills_year_page(year: str, request: Request):
        bills = get_bills().all(state.latest_record_date())
        these = [b for b in bills if b["first"][:4] <= year <= b["last"][:4]] if _YEAR.match(year) else []
        if not these:
            return not_found("bills", f"{str(request.base_url).rstrip('/')}/bills/year/{year}")
        return archive_page("bills", request, f"/bills/year/{year}", f"Bills debated in {year}",
                            f"The {len(these):,} bills debated in Parliament in {year}, each with its stages in the "
                            "Commons and the Lords and every vote, from the official record.",
                            f"Bills debated in {year}", f"The {len(these):,} bills debated in Parliament in {year}.",
                            lambda: bills_list_text(these, bill_years(bills)))

    def people_listing(former: bool) -> list[dict[str, Any]]:
        def build() -> list[dict[str, Any]]:
            people = get_people().search(current_only=not former, limit=20_000)
            return [m for m in people if not m["current"]] if former else people
        return remembered(f"people-listing:{former}", build)

    @app.get("/people", include_in_schema=False)
    def people_page():
        return html(with_list(template("people"), readable(lambda: people_list_text(people_listing(False)))))

    @app.get("/people/former", include_in_schema=False)
    def former_people_page(request: Request):
        former = people_listing(True)
        return archive_page("people", request, "/people/former", "Former MPs and peers since 1997",
                            f"The {len(former):,} MPs and peers who have left Parliament since 1997: what each said "
                            "there, with their roles, majorities and votes.", "Former MPs and peers",
                            "Members of either House since 1997 who no longer sit, each with what they said in "
                            "Parliament, their roles, majorities and votes.",
                            lambda: people_list_text(former, former=True))

    @app.get("/committees", include_in_schema=False)
    def committees_page():
        def text() -> str:
            try:
                return committees_list_text(get_committees().index(), COMMITTEE_HOUSES)
            except HTTPException:  # not collected yet on this server: the app says so
                return ""
        return html(with_list(template("committees"), readable(text)))

    def edm_months() -> list[tuple[str, int]]:
        return remembered("edm-months", lambda: get_edms().months())

    @app.get("/edms", include_in_schema=False)
    def edms_page():
        def text() -> str:
            _, rows = get_edms().search(limit=50)
            return edms_list_text(edm_rows(rows), edm_months(), heading="The latest motions")
        return html(with_list(template("edms"), readable(text)))

    @app.get("/edms/month/{month}", include_in_schema=False)
    def edms_month_page(month: str, request: Request):
        from hansard.api.plaintext import MONTHS

        rows = get_edms().in_month(month) if _MONTH.match(month) else []
        if not rows:
            return not_found("edms", f"{str(request.base_url).rstrip('/')}/edms/month/{month}")
        when = f"{MONTHS[int(month[5:]) - 1]} {month[:4]}"
        return archive_page("edms", request, f"/edms/month/{month}", f"Early day motions tabled in {when}",
                            f"The {len(rows):,} early day motions tabled in the House of Commons in {when}: each "
                            "motion's text and who signed it.", f"Early day motions tabled in {when}",
                            f"The {len(rows):,} motions tabled in {when}, in the order they were tabled.",
                            lambda: edms_list_text(edm_rows(rows), edm_months()))

    @app.get("/groups", include_in_schema=False)
    def groups_page(request: Request, parliament: str = "uk"):
        from hansard.sources.appgs import PARLIAMENTS

        if parliament not in PARLIAMENTS:
            return not_found("groups", f"{str(request.base_url).rstrip('/')}/groups")

        def text() -> str:
            found = groups_index_data("", parliament)
            return groups_list_text(found.get("groups") or [], parliament, PARLIAMENTS)
        if parliament == "uk":
            return html(with_list(template("groups"), readable(text)))
        name = PARLIAMENTS[parliament]
        return archive_page("groups", request, f"/groups?parliament={parliament}", f"Cross-party groups: {name}",
                            f"The groups of members in the {name}, with their officers, members and registered "
                            "benefits.", f"Groups in the {name}", None, text)

    def devolved_years(leg: str) -> list[str]:
        return sorted({d[:4] for _, _, d in devolved_calendar(leg)}, reverse=True)

    def devolved_calendar(leg: str) -> list[tuple[str, str, str]]:
        return remembered(f"devolved-calendar:{leg}", lambda: get_devolved()[0].calendar(leg))

    @app.get("/devolved", include_in_schema=False)
    def devolved_page():
        def text() -> str:
            return devolved_list_text(devolved_index()["legislatures"])
        return html(with_list(template("devolved"), readable(text)))

    @app.get("/devolved/{leg}/year/{year}", include_in_schema=False)
    def devolved_year_page(leg: str, year: str, request: Request):
        from hansard.devolved import LEGISLATURES

        days = [(k, d) for _, k, d in devolved_calendar(leg) if d[:4] == year] if leg in LEGISLATURES else []
        if not days:
            return not_found("devolved", f"{str(request.base_url).rstrip('/')}/devolved/{leg}/year/{year}")
        name = LEGISLATURES[leg]["name"]
        return archive_page("devolved", request, f"/devolved/{leg}/year/{year}", f"{name}, {year}",
                            f"Every sitting of the {name} in {year} with something said, each with its official "
                            "report.", f"{name}: sittings in {year}",
                            f"The {len(days):,} sitting days of the {name} in {year}, newest first.",
                            lambda: devolved_year_text(name, leg, days))

    @app.get("/topics", include_in_schema=False)
    def topics_page():
        from hansard.topics import topic_listing

        def text() -> str:
            # a featured briefing is named by the owner's title or the site's own words, never a reader's question
            featured = [{"id": f["id"], "title": featured_title(state, f["id"]) or briefing_title(f["prepared_at"])}
                        for f in featured_list(shown(), state)]
            return topics_list_text(topic_listing(settings, state, shown()), featured)
        return html(with_list(template("topics"), readable(text)))

    @app.get("/weeks", include_in_schema=False)
    def weeks_page(request: Request):
        weeks = get_weeks().all(state.latest_record_date())
        page = page_with_meta(template("week"), title="Every week in Parliament | Where MPs Stand",
                              description="Every week the Commons or the Lords sat since 1997, each with its debates, "
                                          "votes, bills and statements, from the official record.",
                              url=f"{str(request.base_url).rstrip('/')}/weeks", image=None, noindex=False)
        return html(with_text(page, readable(lambda: weeks_list_text(weeks))))

    @app.get("/people/{number}", include_in_schema=False)
    @app.get("/people/{number}/{name}", include_in_schema=False)
    def person_page(number: int, request: Request, name: str = ""):
        """The person page, with that member's name and description in the page head."""
        page_file = dist / "person.html"
        html_doc = page_file.read_text() if page_file.is_file() else "<!doctype html><html><head></head><body></body></html>"
        pp = get_people()
        pid = pp.resolve(number)
        info = pp.page(pid) if pid else None
        base = str(request.base_url).rstrip("/")
        if info is None:
            return Response(page_with_meta(html_doc, title="Member not found | Where MPs Stand",
                                           description="There is no member at this address.",
                                           url=f"{base}/people/{number}", image=None), status_code=404,
                            media_type="text/html")
        if name != info["slug"] or number != info["number"]:
            # one address for each member: a missing or old name, or a number since merged into theirs, is sent on
            query = f"?{request.url.query}" if request.url.query else ""
            return RedirectResponse(f"/people/{info['number']}/{info['slug']}{query}", status_code=301)
        desc = f"{info['status']}. What {info['name']} has said in Parliament, with roles, majorities and votes."
        # contributions only when already at hand, so the page is never held up by them; otherwise the debates they
        # last spoke in, which are quick to find (their headings only)
        activity = remembered_if_ready(f"person:{number}:activity")
        text = readable(lambda: person_text(
            remembered(f"person:{number}:core", lambda: person_core(number)), activity,
            None if activity else remembered(f"person:{number}:debates", lambda: pp.recent_debates(pid))))
        page_url = f"{base}/people/{info['number']}/{info['slug']}"
        current = info.get("current") or {}
        office = next((o["title"] for o in info.get("offices") or [] if not o.get("end")), None)
        job = office or ((f"Member of Parliament for {current['constituency']}" if current.get("constituency")
                          else "Member of Parliament") if current.get("house") == "commons"
                         else "Member of the House of Lords" if current else None)
        person = {"@type": "Person", "@id": f"{page_url}#person", "name": info["name"], "url": page_url,
                  "description": info["status"],
                  **({"jobTitle": job} if job else {}),
                  # Parliament's own page for the member, where their number there is known
                  **({"sameAs": [f"https://members.parliament.uk/member/{info['parliament_id']}"]}
                     if info.get("parliament_id") else {}),
                  **({"alternateName": info["other_names"]} if info.get("other_names") else {}),
                  **({"memberOf": {"@type": "Organization", "name": "House of Commons" if info["current"]["house"]
                                   == "commons" else "House of Lords"}} if info.get("current") else {}),
                  **({"affiliation": {"@type": "Organization", "name": info["current"]["party"]}}
                     if (info.get("current") or {}).get("party") else {})}
        listed = ("MPs and peers", "/people") if info.get("current") else ("Former MPs and peers", "/people/former")
        trail = breadcrumbs(base, [listed, (info["name"], f"/people/{info['number']}/{info['slug']}")])
        return Response(with_text(page_with_meta(html_doc, title=f"{info['name']} | Where MPs Stand", description=desc,
                                                 url=page_url, noindex=False, data=structured(person, trail)), text),
                        media_type="text/html")

    # The sitemap index: one address for search engines to read, which lists the site's fixed pages (a static file,
    # web/public/sitemap-pages.xml) and each sitemap the record makes. Submitting it once in Search Console covers
    # them all. Debates and early day motions have a sitemap a year, which keeps each well under the 50,000
    # addresses a sitemap may hold. A date is given only where the record has a true one (when a debate was held,
    # when a motion was last signed), never the time the sitemap was made.
    SITEMAPS = ("pages", "people", "briefings", "bills", "weeks", "committees", "groups", "devolved")

    def xml(body: str) -> Response:
        return Response('<?xml version="1.0" encoding="UTF-8"?>' + body, media_type="application/xml")

    def sitemap_names() -> list[str]:
        try:
            edm_years = remembered("edm-years", lambda: get_edms().years())
        except Exception:  # noqa: BLE001 - the motions not set up on this server: none listed
            log.warning("no early day motions for the sitemap", exc_info=True)
            edm_years = []
        return [*SITEMAPS, *(f"debates-{y}" for y in get_debates().years()), *(f"edms-{y}" for y in edm_years)]

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap_index(request: Request):
        base = str(request.base_url).rstrip("/")
        items = "".join(f"<sitemap><loc>{base}/sitemap-{name}.xml</loc></sitemap>" for name in sitemap_names())
        return xml(f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</sitemapindex>')

    def sitemap(urls: list[tuple[str, str | None]], name: str) -> Response:
        """A sitemap of (address, date last changed or None)."""
        if len(urls) > SITEMAP_LIMIT:  # never expected (each is a year or less): said, not hidden
            log.error("sitemap-%s.xml has %d addresses; only the first %d are listed", name, len(urls), SITEMAP_LIMIT)
        body = "".join(f"<url><loc>{u}</loc>" + (f"<lastmod>{d[:10]}</lastmod>" if d else "") + "</url>"
                       for u, d in urls[:SITEMAP_LIMIT])
        return xml(f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>')

    @app.get("/sitemap-people.xml", include_in_schema=False)
    def sitemap_people(request: Request):
        """Every member since 1997 (each has a page), dated by the last day they spoke in the record."""
        base = str(request.base_url).rstrip("/")
        spoke = remembered(f"last-spoke:{state.latest_record_date()}", lambda: get_people().last_spoke(),
                           ttl=12 * 3600)
        people = get_people().search(current_only=False, limit=20_000)
        return sitemap([(f"{base}/people", None), (f"{base}/people/former", None)]
                       + [(f"{base}/people/{p['number']}/{p['slug']}", spoke.get(p["number"])) for p in people],
                       "people")

    @app.get("/sitemap-bills.xml", include_in_schema=False)
    def sitemap_bills(request: Request):
        base = str(request.base_url).rstrip("/")
        bills = get_bills().all(state.latest_record_date())
        return sitemap([(f"{base}/bills/year/{y}", None) for y in bill_years(bills)]
                       + [(f"{base}/bills/{b['slug']}", b["last"]) for b in bills], "bills")

    @app.get("/sitemap-weeks.xml", include_in_schema=False)
    def sitemap_weeks(request: Request):
        """Every sitting week, dated by its last sitting day."""
        base = str(request.base_url).rstrip("/")
        return sitemap([(f"{base}/weeks", None)] + [(f"{base}/weeks/{w['start']}", w["last"])
                                                    for w in get_weeks().all(state.latest_record_date())], "weeks")

    @app.get("/sitemap-debates-{year}.xml", include_in_schema=False)
    def sitemap_debates(year: str, request: Request):
        """The year's debates and written statements, each dated by the day it was held."""
        base = str(request.base_url).rstrip("/")
        if not _YEAR.match(year) or int(year) not in get_debates().years():
            raise HTTPException(404, "There is no sitemap at this address.")
        found = get_debates().listing(int(year), state.latest_record_date())
        return sitemap([(base + path, day) for path, day in found], f"debates-{year}")

    @app.get("/sitemap-committees.xml", include_in_schema=False)
    def sitemap_committees(request: Request):
        base = str(request.base_url).rstrip("/")
        try:
            cs, iq = get_committees().sitemap_ids()
        except HTTPException:
            cs, iq = [], []
        try:  # transcripts collected so far, dated by their session
            ev = sorted(get_evidence().available(), reverse=True)
            days = get_committees().session_dates(set(ev)) if ev else {}
        except Exception:  # noqa: BLE001 - evidence not collected on this server: none listed
            ev, days = [], {}
        return sitemap([(f"{base}/committees/{c}", d) for c, d in cs] + [(f"{base}/inquiries/{i}", d) for i, d in iq]
                       + [(f"{base}/evidence/{e}", days.get(e)) for e in ev], "committees")

    @app.get("/sitemap-groups.xml", include_in_schema=False)
    def sitemap_groups(request: Request):
        from hansard.sources.appgs import PARLIAMENTS

        base = str(request.base_url).rstrip("/")
        lists = [(f"{base}/groups" + ("" if k == "uk" else f"?parliament={k}"), None) for k in PARLIAMENTS]
        return sitemap(lists + [(f"{base}/groups/{p}/{s}", d) for p, s, d in get_groups().sitemap()], "groups")

    @app.get("/sitemap-edms.xml", include_in_schema=False)
    def sitemap_edms_index(request: Request):
        """The motions' sitemaps, a year each (this address once listed every motion in one, which outgrew it)."""
        base = str(request.base_url).rstrip("/")
        items = "".join(f"<sitemap><loc>{base}/sitemap-{name}.xml</loc></sitemap>"
                        for name in sitemap_names() if name.startswith("edms-"))
        return xml(f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</sitemapindex>')

    @app.get("/sitemap-edms-{year}.xml", include_in_schema=False)
    def sitemap_edms(year: str, request: Request):
        """The motions tabled in the year, each dated by its newest signature or change of status, and the year's
        monthly lists."""
        base = str(request.base_url).rstrip("/")
        if not (_YEAR.match(year) or year == "undated"):
            raise HTTPException(404, "There is no sitemap at this address.")
        found = get_edms().sitemap(year)
        if not found:
            raise HTTPException(404, "There is no sitemap at this address.")
        months = [(f"{base}/edms/month/{m}", None) for m, _ in edm_months() if m[:4] == year]
        return sitemap(months + [(f"{base}/edms/{i}", d) for i, d in found], f"edms-{year}")

    @app.get("/sitemap-devolved.xml", include_in_schema=False)
    def sitemap_devolved(request: Request):
        from hansard.devolved import LEGISLATURES

        base = str(request.base_url).rstrip("/")
        days = [(leg, key, day) for leg, key, day in get_devolved()[0].calendar() if leg in LEGISLATURES]
        years = sorted({(leg, day[:4]) for leg, _, day in days}, reverse=True)
        return sitemap([(f"{base}/devolved/{leg}/year/{y}", None) for leg, y in years]
                       + [(f"{base}/devolved/{leg}/{key}", day) for leg, key, day in days], "devolved")

    # RSS feeds
    def feed(request: Request, path: str, title: str, link: str, description: str, items: list) -> Response:
        base = str(request.base_url).rstrip("/")
        return Response(rss(title=f"{title} | Where MPs Stand", link=base + link, description=description,
                            self_url=base + path, items=items), media_type=FEED_TYPE)

    @app.get("/feeds/people/{number}.xml", include_in_schema=False)
    def feed_person(number: int, request: Request):
        pp = get_people()
        pid = pp.resolve(number)
        page = pp.page(pid) if pid else None
        if page is None:
            raise HTTPException(404, "There is no member with that number.")
        base = str(request.base_url).rstrip("/")
        items = [{"title": c["heading"] or c["chamber"], "date": c["date"], "guid": c["record_id"],
                  "link": base + c["debate_path"] if c.get("debate_path") else c["twfy_url"],
                  "description": f"{c['chamber']}, {fmt_date(c['date'])}. {c['excerpt'].strip()}…"}
                 for c in pp.contributions(pid)]
        return feed(request, f"/feeds/people/{number}.xml", page["name"], f"/people/{number}/{page['slug']}",
                    f"What {page['name']} has said in Parliament, newest first, from the official record.", items)

    @app.get("/feeds/topics.xml", include_in_schema=False)
    def feed_topics(request: Request):
        from hansard.topics import topic_listing

        base = str(request.base_url).rstrip("/")
        items = [{"title": f"{t['title']}: updated briefing", "link": f"{base}/topics/{t['slug']}",
                  "guid": f"{base}/b/{t['current']['briefing_id']}", "date": t["current"]["prepared_at"],
                  "description": t["current"].get("summary") or t["question"]}
                 for t in topic_listing(settings, state, shown()) if t["current"]]
        items.sort(key=lambda i: i["date"], reverse=True)
        return feed(request, "/feeds/topics.xml", "Topic briefings", "/topics",
                    "Each topic briefing when it is updated with new contributions.", items)

    @app.get("/feeds/topics/{slug}.xml", include_in_schema=False)
    def feed_topic(slug: str, request: Request):
        from hansard.topics import versions

        t = _topic(slug)
        base = str(request.base_url).rstrip("/")
        items = []
        for v in versions(state, shown(), slug)[:20]:
            b = get_briefing(parts.archive, v["briefing_id"])
            summary = summary_text(public_view(b)["answer"]) if b else ""
            items.append({"title": f"{t.title}: briefing of {fmt_date(v['prepared_at'][:10])}",
                          "link": f"{base}/b/{v['briefing_id']}", "guid": f"{base}/b/{v['briefing_id']}",
                          "date": v["prepared_at"], "description": summary or t.question})
        return feed(request, f"/feeds/topics/{slug}.xml", t.title, f"/topics/{slug}",
                    f"Each new version of the topic briefing on {t.title.lower()}, as Parliament says more.", items)

    @app.get("/feeds/bills.xml", include_in_schema=False)
    def feed_bills(request: Request):
        base = str(request.base_url).rstrip("/")
        items = []
        for b in get_bills().all(state.latest_record_date())[:40]:
            last = b["sittings"][-1]
            house = "Lords" if last["house"] == "lords" else "Commons"
            items.append({"title": f"{b['name']}: {', '.join(last['stages'])} ({house})",
                          "link": f"{base}/bills/{b['slug']}", "guid": f"{base}/bills/{b['slug']}#{last['date']}",
                          "date": last["date"],
                          "description": f"Debated in the {house} on {fmt_date(last['date'])}."})
        return feed(request, "/feeds/bills.xml", "Bills", "/bills",
                    "Bills as they are debated in Parliament, latest sitting first.", items)

    @app.get("/feeds/corrections.xml", include_in_schema=False)
    def feed_corrections(request: Request):
        base = str(request.base_url).rstrip("/")
        items = [{"title": "Briefing withdrawn" if c["kind"] == "withdrawn" else "Correction",
                  "link": f"{base}/corrections", "guid": f"{base}/corrections#{c['date']}-{k}", "date": c["date"],
                  "description": " ".join(x for x in (c.get("question"), c.get("note")) if x)}
                 for k, c in enumerate(corrections(replies, parts.archive))]
        return feed(request, "/feeds/corrections.xml", "Corrections", "/corrections",
                    "Withdrawn briefings and published corrections.", items)

    @app.get("/feeds/committee-reports.xml", include_in_schema=False)
    def feed_committee_reports(request: Request):
        base = str(request.base_url).rstrip("/")
        try:
            pubs = get_committees().recent_publications()
        except HTTPException:
            pubs = []
        items = [{"title": f"{p['type']}: {p['title']}", "date": p["date"], "guid": f"committee-publication-{p['id']}",
                  "link": f"{base}/inquiries/{p['inquiry_id']}" if p["inquiry_id"] else p["url"],
                  "description": f"{p['type']} from the {p['committee'] or 'committee'}, published "
                                 f"{fmt_date(p['date'])}. The document: {p['url']}"} for p in pubs]
        return feed(request, "/feeds/committee-reports.xml", "Committee reports and government responses",
                    "/committees", "New select committee reports, special reports and government responses.", items)

    @app.get("/feeds/edms.xml", include_in_schema=False)
    def feed_edms(request: Request):
        base = str(request.base_url).rstrip("/")
        _, rows = get_edms().search(limit=40)
        items = [{"title": title_case(r["title"]), "date": r["date_tabled"] or "2000-01-01", "guid": f"edm-{r['id']}",
                  "link": f"{base}/edms/{r['id']}",
                  "description": f"Early day motion {r['uin'] or ''} tabled by {r['primary_name'] or 'an MP'}"
                                 f"{' (' + r['primary_party'] + ')' if r['primary_party'] else ''}."}
                 for r in rows if r["date_tabled"]]
        return feed(request, "/feeds/edms.xml", "Early day motions", "/edms",
                    "Early day motions as they are tabled.", items)

    @app.get("/changelog", include_in_schema=False)
    def changelog_gone():  # the changelog page was retired: old links go to the About page
        return RedirectResponse("/about", status_code=301)

    # feedback, visit counts and public status
    @app.post("/api/feedback")
    def feedback(body: FeedbackBody, request: Request, user=Depends(current_user)):
        visitor, _ = requester(request, user)
        record_feedback(state, body.briefing_key, body.question, body.useful, visitor)
        about = briefing_of(body.briefing_key)
        if about:
            record.mark(about, useful=body.useful)  # on that briefing's record: the answer, and nothing of who gave it
        return {"ok": True}

    @app.post("/api/report")
    def report(body: ReportBody, request: Request, user=Depends(current_user)):
        visitor, _ = requester(request, user)
        bid = body.briefing_id if body.briefing_id and ID_PATTERN.match(body.briefing_id) else None
        page = None
        if body.page and not bid:  # only a path on this site is kept
            parsed = urlparse(body.page.strip())
            if parsed.hostname in (None, *SITE_HOSTS) and parsed.path.startswith("/"):
                page = parsed.path[:200]
        try:  # a filled hidden field is a bot: thanked, and nothing kept
            kept = replies.report(kind=body.kind, page=page, message=body.message, contact=body.contact,
                                  briefing_id=bid, visitor=visitor, trap=body.website)
        except Refused as exc:
            raise HTTPException(429 if exc.why == "daily_reports" else 400, str(exc)) from None
        if kept is not None and bid:
            record.mark(bid, reported=True)  # what happened next, on that briefing's record
        return {"ok": True}

    # statements beside items (the right of reply). Kept apart from the record: the briefing pipeline, search
    # and the pages' readable text never read them; the site fetches them on their own, to show beside the item.
    @app.post("/api/statements")
    def statement_add(body: StatementBody, request: Request, user=Depends(current_user)):
        visitor, _ = requester(request, user)
        try:  # a filled hidden field is a bot: thanked, and nothing kept
            replies.submit(name=body.name, role=body.role, item=body.item, text=body.text, link=body.link,
                           email=body.email, visitor=visitor, trap=body.website)
        except Refused as exc:
            raise HTTPException(429 if exc.why == "daily_statements" else 400, str(exc)) from None
        return {"ok": True}

    @app.get("/api/statements")
    def statements_shown(path: Annotated[list[str] | None, Query()] = None, person: int | None = None):
        return {"statements": published(replies, paths=[p[:300] for p in path or []], person=person)}

    @app.get("/api/admin/statements")
    def statements_list(status: str | None = None, user=Depends(admin_user)):
        rows = replies.statements(status if status in STATUSES else None)  # type: ignore[arg-type]
        return {"statements": [as_listed(replies, row) for row in rows]}

    def statement_action(act: Callable[[], None]) -> dict[str, bool]:
        """One of the owner's actions on a statement, from the admin page. New words that break a rule are answered
        in the site's words for them; anything else that can't be done, as it always was."""
        try:
            act()
        except Refused as exc:
            raise HTTPException(400, str(exc)) from None
        except NotDone:
            raise HTTPException(404, "No such statement, or it can't be changed in that way now.") from None
        return {"ok": True}

    @app.post("/api/admin/statements/{sid}/publish")
    def statement_publish(sid: int, body: PublishStatementBody, user=Depends(admin_user)):
        if not body.checked.strip():  # how the sender was confirmed is all spaces
            raise HTTPException(400, "Please say how you confirmed who sent it: it is kept privately.")
        about = str(body.person) if body.person is not None else None
        return statement_action(lambda: replies.publish(sid, checked=body.checked, about=about))

    @app.post("/api/admin/statements/{sid}/decline")
    def statement_decline(sid: int, body: DeclineStatementBody, user=Depends(admin_user)):
        """Decline a waiting statement, or take down a published one that breaks the rules (which the record now
        calls removing it), with the reason its author is told."""
        if len(body.reason.strip()) < 3:  # a reason of spaces only
            raise HTTPException(400, "Please give the reason: it is sent to the author.")
        shown = (replies.statement(sid) or {}).get("status") == "published"
        return statement_action(lambda: (replies.remove if shown else replies.decline)(sid, body.reason.strip()))

    @app.post("/api/admin/statements/{sid}/update")
    def statement_update(sid: int, body: UpdateStatementBody, user=Depends(admin_user)):
        def act() -> None:
            if (body.text, body.name, body.role, body.link) == (None, None, None, None):
                if replies.statement(sid) is None:
                    raise NotDone(f"no statement {sid}")
                return  # nothing to change: answered as done, as it always was
            replies.update(sid, text=body.text, name=body.name or None, role=body.role or None,
                           link=body.link or None, clear_link=body.link == "")

        return statement_action(act)

    @app.post("/api/admin/statements/{sid}/withdraw")
    def statement_withdraw(sid: int, body: WithdrawStatementBody, user=Depends(admin_user)):
        return statement_action(lambda: replies.withdraw(sid, (body.note or "").strip() or WITHDRAWN_BY_AUTHOR))

    @app.get("/api/corrections")
    def corrections_log():
        return {"corrections": corrections(replies, parts.archive)}

    @app.get("/api/admin/reports")
    def reports_list(status: str | None = None, user=Depends(admin_user)):
        rows = replies.reports(open_only=status == "open")
        return {"reports": [r for r in rows if status not in CLOSED or r["status"] == status]}

    @app.post("/api/admin/reports/{report_id}/close")
    def report_close(report_id: int, body: CloseReportBody, user=Depends(admin_user)):
        about = next((r["briefing_id"] for r in replies.reports(open_only=True) if r["id"] == report_id), None)
        try:
            replies.close(report_id, outcome=body.status, note=body.resolution, public_note=body.public_note)
        except NotDone:
            raise HTTPException(404, "No such report.") from None
        if about and body.status == "resolved" and (body.public_note or "").strip():
            record.mark(about, corrected=True)  # a public note on a report of a briefing: it was put right
        return {"ok": True}

    @app.post("/api/hit")
    def hit(body: HitBody, request: Request, user=Depends(current_user)):
        """Cookie-free page counting: no identifiers are sent or stored beyond today's salted hash. Over the allowance
        the page is simply not counted (no error, so nothing shows in the reader's browser)."""
        visitor = None if user else visitor_key(core, request.client.host if request.client else None, clock, random)
        if not hit_limiter.allow(f"user:{user['user_id']}" if user else f"visitor:{visitor}"):
            return Response(status_code=204)
        if body.event:
            record_event(state, body.event, visitor)
            return {"ok": True}
        host = urlparse(body.referrer).hostname if body.referrer else None
        own = request.url.hostname
        record_visit(state, body.path.split("?")[0], host if host and host != own else None, visitor)
        return {"ok": True}

    # Counts and ages for whoever holds the family's check token, and for no one else: the address is not there
    # at all with no token set on this site, and answers 401 for a wrong one. Never a question, a name or a word
    # of a report.
    check_token = SITE.env("CHECK_TOKEN")

    def meaning_refused() -> int:
        """1 when the index has vectors that are not being searched because another embedding model made them."""
        look = getattr(get_pipeline().engine, "meaning_search", None)
        return int(look is not None and look()[0] == "refused")
    if (check_token or "").strip():
        check = check_route(
            SITE, token=check_token, replies=replies,
            backups=lambda: backup_ages(Path(os.environ.get("HANSARD_BACKUP_DIR") or "/backups"), BACKUPS,
                                        clock=clock),
            # the last day's figures, and whether search by meaning has been switched off by an index made with
            # another embedding model (1: the owner must act; docs/OPERATIONS.md, "If search by meaning is off")
            figures=lambda: {**last_day_figures(record, clock=clock), "meaning_search_refused": meaning_refused()},
            # the newest weekly report's totals, numbers only, for the family's weekly summary
            weekly=lambda: latest_week_figures(core), clock=clock)

        @app.get("/api/check")
        def api_check(authorization: str | None = Header(None)):
            status_code, answer = check(authorization)
            return JSONResponse(answer, status_code=status_code)

    @app.get("/api/status")
    def status():
        p = get_pipeline()
        runs = state.runs(1)
        idx = p.engine.index.stats()
        model_on = p.has_model()
        paused = parts.caps.site_paused() if model_on else None

        def latest_vote() -> str | None:
            if not p.votes.available:
                return None
            row = p.votes.con.execute("SELECT max(date) FROM divisions").fetchone()
            return row[0].isoformat() if row and row[0] else None

        return {
            "archive_start": settings.archive_start.isoformat(),
            "latest_record": state.latest_record_date(),
            "latest_vote": remembered("status:latest_vote", latest_vote, ttl=600),
            "last_update": _public_run(runs[0]) if runs else None,
            "briefings": "unavailable" if not model_on else ("paused" if paused else "available"),
            "briefings_note": paused.message if paused else None,
            "passages": idx.get("passages", 0),
            "meaning_search_coverage": round(idx["vectors"] / idx["passages"], 3) if idx.get("passages") else 0,
        }

    @app.get("/api/admin/usage")
    def usage(user=Depends(admin_user)):
        backups = Path(os.environ.get("HANSARD_BACKUP_DIR") or "/backups")  # set but empty: the default too
        return {**summary(parts.spending, state, clock), "visits": visit_summary(state), "tasks": task_summary(state),
                "alerts": alerts(parts, state) + backup_alerts(backups),
                "failures": recent_failures(parts.failures), "popular_questions": popular_questions(state)}

    @app.get("/api/admin/sync")
    def sync_log(user=Depends(admin_user)):
        return {"runs": state.runs(30)}

    # web app
    @app.middleware("http")
    async def head_requests(request: Request, call_next):
        """HEAD (used by link checkers and search engines) answers like GET, without the body."""
        if request.method != "HEAD":
            return await call_next(request)
        request.scope["method"] = "GET"
        resp = await call_next(request)
        # Back to HEAD before the answer goes out: the web server reads this same scope as it sends, and for a GET
        # it waits for as many bytes of body as Content-Length says, which a HEAD answer never has.
        request.scope["method"] = "HEAD"
        return Response(status_code=resp.status_code, headers=dict(resp.headers))

    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def home(request: Request):
            """The home page, with what search engines are told about the site as a whole: its name and who
            publishes it (schema.org WebSite, on this page only; the same file is the frame of topic and briefing
            pages, which carry their own)."""
            base = str(request.base_url).rstrip("/")
            site = {"@context": "https://schema.org", "@type": "WebSite", "@id": f"{base}/#website",
                    "name": "Where MPs Stand", "url": f"{base}/", "inLanguage": "en-GB", "publisher": PUBLISHER}
            tag = '<script type="application/ld+json">' + json.dumps(site).replace("</", "<\\/") + "</script>"
            return html((dist / "index.html").read_text().replace("</head>", f"  {tag}\n  </head>", 1))

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            if path.strip("/").removesuffix(".html") in TEMPLATES:  # a page's frame, empty without its address
                missing = dist / "404.html"
                return FileResponse(missing if missing.is_file() else dist / "index.html", status_code=404)
            f = (dist / path).resolve()
            if path and f.is_file() and f.is_relative_to(dist.resolve()):  # never serve outside the build
                return FileResponse(f)
            page = dist / f"{path.strip('/')}.html"  # static pages: /methodology -> methodology.html
            if path and "/" not in path.strip("/") and page.is_file() and page.name != "404.html":
                return FileResponse(page)
            if path.strip("/") in ("", "index.html"):
                return FileResponse(dist / "index.html")
            # anything else is not a page: say so, so search engines do not index it as one
            missing = dist / "404.html"
            return FileResponse(missing if missing.is_file() else dist / "index.html", status_code=404)

    if os.environ.get("HANSARD_WARM_UP") == "1":
        # Load the search models and the commonly used tables now, in the background, so the first reader after a
        # restart does not wait for them. Anything that fails here simply loads on first use as before.
        def warm_up() -> None:
            t = time.time()
            try:
                from hansard.engine import Filters

                engine = get_pipeline().engine
                engine.search("cost of living", Filters(), Scope(written=True), [], 0.0, 1.0, 5)
                get_people()
                get_weeks().latest()
                get_bills()
                # what an MP's page draws on, so the first page after a restart is not the slow one
                from hansard.vote_types import vote_types as _vote_types

                _vote_types(settings).all()
                for load in (get_register, get_registers, get_devolved, get_donations, get_groups, get_committees,
                             get_edms, get_directorships):
                    try:
                        load()
                    except Exception as exc:  # a source not set up on this server: load it on first use as before
                        log.info("warm-up skipped %s: %s", load.__name__, exc)
                log.info("warmed up in %.1fs", time.time() - t)
            except Exception as exc:  # never stop the site starting
                log.warning("warm-up incomplete: %s", exc)

        threading.Thread(target=warm_up, name="warm-up", daemon=True).start()
    return app
