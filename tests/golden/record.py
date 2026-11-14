"""Record what Where MPs Stand's own code gave before it moved onto wts-core (14 November 2026).

Run on the code as it was (the commit this file was added in, or any before the switch):

    uv run python tests/golden/record.py [folder to write into; default: this one]

It writes the JSON files beside it, which tests/test_parity.py and its neighbours compare with what the site gives
now; ``old-state.sql``, a state database made by the old code with rows of every kind, which the one-off import is
tested on; and ``old-index.tar.gz``, a small search index built by the old code, which the new code must open and
search the same. It needs the modules the switch deleted (``hansard.api.reports``, ``hansard.api.statements``,
``hansard.backup``, ``hansard.offsite``, ``hansard.rag.verify`` and the rest), so it does not run on the code as
it is now: check out the old commit to record again.
"""

# ruff: noqa: I001
# The imports below are not sorted by the linter: several are of modules the switch deleted, and the linter sorts
# an import differently when its module is not there (it takes it for an outside library). Sorted either way, the
# other checkout would complain: a fresh one in CI, or one where the old modules' cache folders are still about.

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import types
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
os.environ["_TYPER_FORCE_DISABLE_TERMINAL"] = "1"
os.environ["NO_COLOR"] = "1"
for _name in [k for k in os.environ if k.startswith(("HANSARD_", "ANTHROPIC_"))]:
    del os.environ[_name]

import shared  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from hansard import backup, cli, offsite, topics  # noqa: E402
from hansard.api import app as web  # noqa: E402
from hansard.api import auth  # noqa: E402
from hansard.api import briefings as saved  # noqa: E402
from hansard.api import reports as rp  # noqa: E402
from hansard.api import statements as st  # noqa: E402
from hansard.api import usage as use  # noqa: E402
from hansard.config import REPO_ROOT, STATIC_DIR, load_settings, save_local_settings  # noqa: E402
from hansard.embed.base import HashingEmbedder  # noqa: E402
from hansard.engine import Engine, Filters, Scope  # noqa: E402
from hansard.index import build as index_build  # noqa: E402
from hansard.index import lance  # noqa: E402
from hansard.ingest import ingest  # noqa: E402
from hansard.rag import interests  # noqa: E402
from hansard.rag.british import britishise_segments  # noqa: E402
from hansard.rag.neutral import flag_loaded  # noqa: E402
from hansard.rag.pipeline import Pipeline  # noqa: E402
from hansard.rag.sources import Source  # noqa: E402
from hansard.rag.verify import check_quotes, drop_preambles, verify  # noqa: E402
from hansard.reference.build import build_reference, write_reference  # noqa: E402
from hansard.sources.demographics import load_demographics  # noqa: E402
from hansard.sources.elections import load_elections  # noqa: E402
from hansard.store import state as store  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures"
TESTS = {"embedding.backend": "hashing", "embedding.dim": 64}
# fewer sources than the site's own settings give, so that the recordings of whole answers stay a readable size
FEWER = {"summary_sources": 5, "comprehensive_sources": 8, "side_sources": 3, "comprehensive_side_sources": 4}


class Time:
    """The old code reads the clock itself: this stands in for ``datetime`` in its modules, and for ``time``."""

    def __init__(self) -> None:
        self.at = datetime.fromisoformat(shared.START)
        self.seconds = 1000.0

    def set(self, when: str) -> None:
        self.at = datetime.fromisoformat(when)

    def advance(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)
        self.seconds += seconds

    def install(self) -> None:
        clock = self

        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.at

        for module in (use, st, rp, backup, offsite, store, topics, auth):
            module.datetime = Frozen
        use.time = types.SimpleNamespace(monotonic=lambda: clock.seconds)


TIME = Time()


def environment(data_dir: Path, **more: str) -> None:
    shared.clear_environment()
    os.environ.update({"HANSARD_DATA_DIR": str(data_dir), **more})


def run_cli(args: list[str]) -> tuple[int, str]:
    result = CliRunner().invoke(cli.app, args)
    return result.exit_code, result.output


# ---- the small tables ----------------------------------------------------------------------------------------------


def limiter() -> dict[str, Any]:
    rate = use.RateLimiter(shared.LIMITER_PER_MINUTE)
    allowed = []
    for t, who in shared.LIMITER_SCRIPT:
        TIME.seconds = 1000.0 + t
        allowed.append(rate.allow(who))
    TIME.seconds = 1000.0
    return {"per_minute": shared.LIMITER_PER_MINUTE, "script": shared.LIMITER_SCRIPT, "allowed": allowed}


def costs() -> list[dict[str, Any]]:
    prices = load_settings().usage
    return [{"usage": u, "model": m, "cost_usd": use.cost_usd(u, prices, m), "stored_as": use.token_counts(u)}
            for u in shared.USAGES for m in shared.MODELS]


def cache_keys(tmp: Path) -> list[dict[str, Any]]:
    """The key ``/api/ask`` works out for each body, as its route does."""
    environment(tmp / "keys")
    settings = load_settings()
    out = []
    for world in shared.KEY_WORLDS:
        for raw in shared.KEY_BODIES:
            body = web.AskBody(**raw)
            key = use.cache_key({"q": " ".join(body.question.lower().split()),
                                 **body.model_dump(exclude={"question"}),
                                 "model": world.get("model", settings.answer.get("model")),
                                 "data": world.get("data", 7),
                                 "instructions": world.get("instructions", web.INSTRUCTIONS_VERSION)})
            out.append({"body": raw, "world": world, "key": key})
    return out


def caps(tmp: Path) -> dict[str, Any]:
    limits = {**load_settings().usage, **shared.CAP_LIMITS}
    state = store.StateDB(tmp / "caps" / "state.sqlite")
    said = []
    for when, what, *rest in shared.CAPS_SCRIPT:
        TIME.set(when)
        if what == "spend":
            kind, model, usage, visitor, account = rest
            said.append(use.record_usage(state, account, kind, model, usage, limits, visitor))
        else:
            visitor, account, pending = rest
            said.append(use.check_caps(state, account, limits, visitor, pending))
    TIME.set(shared.START)
    flight = use.InFlight(**shared.IN_PROGRESS)
    places = []
    for what, arg in shared.IN_PROGRESS_SCRIPT:
        if what == "start":
            places.append(flight.start(arg))
        elif what == "finish":
            flight.finish(arg)
            places.append(dict(flight.running))
        else:
            places.append(flight.pending_usd(besides=arg))
    return {"limits": shared.CAP_LIMITS, "script": shared.CAPS_SCRIPT, "said": said,
            "in_progress": shared.IN_PROGRESS, "in_progress_script": shared.IN_PROGRESS_SCRIPT, "places": places}


def checks() -> dict[str, Any]:
    sources = [Source(**s) for s in shared.SOURCES]
    written = []
    for case in shared.CHECK_CASES:
        report = verify(case["blocks"], sources)
        drop_preambles(report.segments)
        britishise_segments(report.segments)
        flag_loaded(report.segments)
        check_quotes(report, sources)
        written.append({"name": case["name"], "answer": report.to_dict()})
    # a saved briefing as its address serves it: one object, its flags judged again
    shown = [{"name": case["name"], "view": saved.public_view({
        "id": "briefingaa", "question": shared.Q, "created_at": shared.START, "records_up_to": "2026-11-12",
        "settings": {"mode": "summary"}, "events": json.loads(json.dumps(case["events"]))})}
        for case in shared.SAVED_BRIEFINGS]
    ids = [saved.new_id() for _ in range(200)]
    return {"written": written, "shown_again": shown,
            "summaries": [saved.summary_text(case["events"][-1].get("answer")) for case in shared.SAVED_BRIEFINGS],
            "ids": {"all_ten_of_the_alphabet": all(len(i) == 10 and set(i) <= set(shared.ALPHABET) for i in ids),
                    "all_match_the_pattern": all(saved.ID_PATTERN.match(i) for i in ids),
                    "all_different": len(set(ids)) == len(ids)}}


def interests_check() -> dict[str, Any]:
    sources = [Source(**s) for s in shared.INTEREST_SOURCES]
    out = {}
    for name, matches in shared.INTEREST_CASES.items():
        sdk = shared.InterestsSDK(matches)
        flags, usage = interests.check(sdk, "claude-haiku-5-5", "What has been said about trial by jury?", sources,
                                       shared.Register(shared.REGISTER), None, effort="low")
        out[name] = {"flags": flags, "asked": sdk.asked,
                     "usage": None if usage is None else {k: getattr(usage, k) for k in vars(usage)}}
    sdk = shared.InterestsSDK([])
    flags, usage = interests.check(sdk, "claude-haiku-5-5", "q", sources[2:], shared.Register({}), None)
    out["nobody quoted has an entry: the model is not asked"] = {"flags": flags, "asked": sdk.asked, "usage": usage}
    return out


# ---- /api/ask ------------------------------------------------------------------------------------------------------


def old_side(pipeline: Any, settings: Any, sdk: Any = None) -> shared.Side:
    state = store.StateDB(settings.state_db)
    with state.connect() as con:
        con.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (f"visitor_salt:{shared.DAY}", shared.SALT))
    ids = shared.briefing_ids()
    saved.new_id = lambda: next(ids)
    os.environ["HANSARD_ADMIN_TOKEN"] = shared.ADMIN_TOKEN
    app = web.create_app(settings, pipeline=pipeline, state=state)
    client = TestClient(app)

    def stream(body: dict[str, Any]):
        route = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/ask")
        real, web.StreamingResponse = web.StreamingResponse, lambda content, **kwargs: content
        try:
            request = types.SimpleNamespace(client=types.SimpleNamespace(host="testclient"), headers={}, cookies={})
            return route(web.AskBody(**body), request, user=None)
        finally:
            web.StreamingResponse = real

    def another_client():
        other = TestClient(app)
        other.cookies.update(client.cookies)
        return other

    def sign_in() -> None:
        auth.create_user(state, "researcher@example.org", "correct horse battery")
        r = client.post("/api/auth/login", json={"email": "researcher@example.org",
                                                 "password": "correct horse battery"})
        assert r.status_code == 200, r.text

    side = shared.Side(client=client, pipeline=pipeline, connect=lambda: sqlite3.connect(state.path), new=False,
                       stream=stream, another_client=another_client, sign_in=sign_in)
    side.state, side.settings, side.app = state, settings, app
    return side


def settings_in(folder: Path, auth_required: bool = False, **usage: Any) -> Any:
    environment(folder / "data")
    settings = load_settings(**TESTS)
    settings.raw["usage"] = {**settings.raw["usage"], **usage}
    settings.raw["auth"] = {**settings.raw.get("auth", {}), "required": auth_required}
    settings.raw["site"] = {**settings.raw.get("site", {}), "donate_url": shared.DONATE}
    return settings


def scripted(tmp: Path) -> dict[str, Any]:
    n = iter(range(1000))

    def make(has_model: bool = True, auth: bool = False, **usage: Any) -> shared.Side:
        TIME.set(shared.START)
        settings = settings_in(tmp / f"scripted-{next(n)}", auth, **usage)
        pipeline = shared.ScriptedPipeline(TIME.advance, settings.answer["model"], has_model=has_model)
        return old_side(pipeline, settings)

    return shared.scripted_asks(make)


def build_data(folder: Path) -> Any:
    """A complete, offline data folder built from the tests' fixtures (as tests/conftest.py's ``indexed_settings``)."""
    settings = settings_in(folder)
    shutil.copytree(FIXTURES / "xml", settings.raw_xml_dir)
    write_reference(build_reference(FIXTURES / "members", STATIC_DIR / "geo" / "constituencies.csv",
                                    demographics=load_demographics(FIXTURES / "demographics.json"),
                                    elections=load_elections(FIXTURES / "elections.json", snapshot=False)),
                    settings.reference_dir)
    state = store.StateDB(settings.state_db)
    ingest(settings, workers=1, since=date(2010, 1, 1), state=state)
    idx = lance.SearchIndex(settings.index_dir, 64)
    index_build.Indexer(settings, idx, state).index_pending()
    idx.build_indices()
    index_build.embed_pending(settings, idx, state, HashingEmbedder(64))
    state._conn().close()
    return settings


def real_side(template: Path, folder: Path, sdk: Any, **usage: Any) -> shared.Side:
    TIME.set(shared.START)
    shutil.copytree(template, folder)
    settings = settings_in(folder, **usage)
    settings.raw["answer"] = {**settings.raw["answer"], **FEWER}
    engine = Engine(settings, embedder=HashingEmbedder(64), today=shared.TODAY)
    return old_side(Pipeline(settings, engine, client=sdk, today=shared.TODAY), settings)


def real(tmp: Path, template: Path) -> dict[str, Any]:
    n = iter(range(1000))
    return shared.real_asks(lambda sdk, **usage: real_side(template, tmp / f"real-{next(n)}", sdk, **usage))


# ---- topic briefings -----------------------------------------------------------------------------------------------

TOPICS = """
[[topic]]
slug = "trial-by-jury"
title = "Trial by jury"
area = "Justice"
question = "trial by jury"
description = "Jury trials and the courts."

[[topic]]
slug = "rural-buses"
title = "Rural bus services"
area = "Transport"
question = "bus services in rural areas"
"""


def topic_briefings(tmp: Path) -> dict[str, Any]:
    """Topic briefings refreshed by the update: what is said of each, and the rows written."""
    out = {}
    for name, plays, usage, has_model in (
            ("both written, then neither is due", [], {}, True),
            ("the first cannot be read: the run stops", ["unread_alone"], {}, True),
            ("the model declines the first: the run stops", ["declined"], {}, True),
            ("no records for the first, the second written", ["no_records"], {}, True),
            ("the budget is spent", [], {"daily_spend_cap_usd": 0.0}, True),
            ("no API key", [], {}, False)):
        TIME.set(shared.START)
        folder = tmp / f"topics-{len(out)}"
        settings = settings_in(folder, **usage)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "topics.toml").write_text(TOPICS)
        settings.raw["topics"] = {"file": str(folder / "topics.toml"), "max_per_run": 4}
        pipeline = shared.ScriptedPipeline(TIME.advance, settings.answer["model"], has_model=has_model)
        pipeline.plays.extend(plays)
        side = old_side(pipeline, settings)
        side.latest_record()
        said = [topics.refresh_topics(settings, side.state, pipeline, progress=lambda line: None)]
        TIME.advance(3600)
        said.append(topics.refresh_topics(settings, side.state, pipeline, progress=lambda line: None))
        with side.state.connect() as con:
            versions = [list(r) for r in con.execute("SELECT slug, briefing_id, prepared_at, records_up_to "
                                                     "FROM topic_versions ORDER BY slug, prepared_at")]
            checked = [list(r) for r in con.execute("SELECT slug, checked_at, outcome FROM topic_checks ORDER BY 1")]
        out[name] = {"said": said, "rows": side.rows(), "versions": versions, "checks": checked,
                     "calls": side.calls(), "listing": side.get("/api/topics"),
                     "page": side.get("/api/topics/trial-by-jury")}
    return shared.plain(out, tmp=tmp)


# ---- reports, statements and the owner's commands ------------------------------------------------------------------


def replies_kept(state: Any) -> dict[str, Any]:
    con = sqlite3.connect(state.path)
    con.row_factory = sqlite3.Row
    try:
        return {"reports": [dict(r) for r in con.execute("SELECT * FROM reports ORDER BY id")],
                "statements": [dict(r) for r in con.execute("SELECT * FROM statements ORDER BY id")],
                "briefings": [dict(r) for r in con.execute(
                    "SELECT id, created_at, question, withdrawn_at, withdrawn_reason FROM briefings ORDER BY id")]}
    finally:
        con.close()


def save_briefing(state: Any, bid: str, question: str, options: dict[str, Any], events: list[dict[str, Any]],
                  records_up_to: str | None = "2026-11-12", model: str | None = "claude-sonnet-5-5") -> None:
    with state.connect() as con:
        con.execute("INSERT INTO briefings (id, created_at, question, settings, events, records_up_to, model) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)", (bid, store.utcnow(), question, json.dumps(options, default=str),
                                                     json.dumps(events, default=str), records_up_to, model))


def replies(tmp: Path, template: Path) -> dict[str, Any]:
    side = real_side(template, tmp / "replies", shared.FakeSDK(shared.READING))
    return shared.replies_scenario(
        client=side.client, run=run_cli, tick=TIME.advance, scratch=tmp / "words",
        save=lambda bid, question, options, events: save_briefing(side.state, bid, question, options, events),
        kept=lambda: replies_kept(side.state))


# ---- backups -------------------------------------------------------------------------------------------------------


def backups(tmp: Path) -> dict[str, Any]:
    def alerts(folder: Path, env: dict[str, str], now: str) -> list[dict[str, str]]:
        return offsite.backup_alerts(folder, env, datetime.fromisoformat(now))

    return shared.backups_scenario(
        tmp / "backups", run=run_cli, set_day=lambda day: TIME.set(f"{day}T04:20:00+00:00"),
        environment=environment, alerts=alerts)


def offsite_copies(tmp: Path) -> list[dict[str, Any]]:
    TIME.set("2026-11-03T04:20:05+00:00")
    return shared.offsite_scenario(
        tmp / "offsite", error=offsite.OffsiteError,
        send=lambda archive, keep, env, client: offsite.send(archive, keep=keep, env=env, client=client),
        check=lambda env, client: offsite.check(env=env, client=client))


# ---- a state database made by the old code, with rows of every kind ---------------------------------------------

OLD = "2026-08-01T10:00:00+00:00"       # more than three months before the scenarios' day
KEY_FRESH, KEY_STALE, KEY_NO_ADDRESS = "a" * 64, "b" * 64, "c" * 64
VISITOR_A, VISITOR_B = "a1" * 12, "b2" * 12
OPTIONS = {"mode": "summary", "recency": None, "role_weight": None, "scope": {"written": True, "pbc": True,
           "votes": True, "edms": True, "devolved": True, "evidence": True}, "plan": None, "sources_only": False,
           "follows": None, "compare": []}


def old_state(tmp: Path) -> tuple[str, dict[str, Any]]:
    """The database as SQL, and what the old code says of it: the figures the import must leave unchanged."""
    TIME.set(shared.START)
    folder = tmp / "old-state"
    settings = settings_in(folder)
    os.environ["HANSARD_BACKUP_DIR"] = str(folder / "no-backups")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "topics.toml").write_text(TOPICS)
    settings.raw["topics"] = {"file": str(folder / "topics.toml")}
    pipeline = shared.ScriptedPipeline(TIME.advance, settings.answer["model"])
    side = old_side(pipeline, settings)
    state, limits = side.state, settings.usage
    with state.connect() as con:  # a permanent secret from the site's first weeks, which no later version deleted
        con.execute("INSERT INTO kv (key, value) VALUES ('visitor_salt', ?)", ("0d" * 16,))
        con.execute("INSERT INTO kv (key, value) VALUES ('some_other_key', 'kept')")
    side.latest_record()
    TIME.set("2026-10-01T09:00:00+00:00")
    researcher = auth.create_user(state, "researcher@example.org", "correct horse battery")
    auth.create_user(state, "admin@example.org", "admin password 123", admin=True)
    with state.connect() as con:  # a password's hash is salted afresh each time: a fixed one, so the file is the same
        con.execute("UPDATE users SET password_hash = '$argon2id$not-a-real-hash$' || user_id")
    # spending: last month, this month, today; two visitors and an account; every kind; and what the columns allow
    for when, kind, model, usage, visitor, account in (
            ("2026-10-20T08:00:00+00:00", "answer", "claude-sonnet-5-5", shared.USAGES[4], "0f" * 12, None),
            ("2026-11-02T08:00:00+00:00", "parse", "claude-haiku-5-5", shared.USAGES[5], "1e" * 12, None),
            ("2026-11-02T08:00:09+00:00", "answer", "claude-sonnet-5-5", shared.USAGES[6], "1e" * 12, None),
            ("2026-11-02T08:00:09+00:00", "interests", "claude-haiku-5-5", shared.INTERESTS_USAGE, "1e" * 12, None),
            ("2026-11-03T07:15:00+00:00", "topic", "claude-haiku-5-5", shared.USAGES[5], None, None),
            ("2026-11-03T07:15:20+00:00", "topic", "claude-sonnet-5-5", shared.USAGES[4], None, None),
            ("2026-11-13T00:00:00+00:00", "parse", "claude-haiku-5-5", shared.USAGES[5], VISITOR_A, None),
            ("2026-11-13T00:00:07+00:00", "answer", "claude-sonnet-5-5", shared.USAGES[4], VISITOR_A, None),
            ("2026-11-13T09:30:00+00:00", "answer", "claude-sonnet-5-5", shared.USAGES[3], VISITOR_A, None),
            ("2026-11-13T10:00:00+00:00", "answer", "claude-sonnet-5-5", shared.USAGES[4], VISITOR_B, None),
            ("2026-11-13T10:00:01+00:00", "answer", "claude-opus-5-5", shared.USAGES[3], None, researcher),
            ("2026-11-13T10:00:02+00:00", "answer", None, shared.USAGES[0], None, researcher),
            ("2026-11-13T10:30:00+00:00", "answer", "claude-sonnet-5-5", shared.USAGES[3], None, 99)):
        TIME.set(when)
        use.record_usage(state, account, kind, model, usage, limits, visitor)
    # the answer cache: one that is served, one too old, one cached before briefings had addresses
    TIME.set("2026-11-13T11:00:00+00:00")
    cached = shared.a_saved_briefing(shared.Q)
    use.store_events(state, KEY_FRESH, [*cached[:2], {"type": "text", "text": "Hilary Benn said…"}, {
        **cached[2], "usage": None, "cached": True, "briefing_key": KEY_FRESH, "briefing_id": shared.BRIEFING_1,
        "prepared_at": "2026-10-25T12:00:00+00:00", "records_up_to": "2026-10-24"}], 168)
    use.store_events(state, KEY_NO_ADDRESS, [*cached[:2], {"type": "text", "text": "Hilary Benn said…"}, {
        **cached[2], "usage": None, "cached": True}], 168)
    with state.connect() as con:
        con.execute("INSERT INTO answer_cache (key, created_at, events) VALUES (?, ?, ?)",
                    (KEY_STALE, "2026-11-01T00:00:00+00:00", json.dumps([{"type": "final", "answer": None}])))
    # failures
    for when, kind, question, error in (
            ("2026-08-01T00:00:00+00:00", "briefing", "An old question?", "RuntimeError: an old failure"),
            ("2026-11-12T23:00:00+00:00", "question reading", shared.Q, "AuthenticationError: bad key"),
            ("2026-11-13T07:15:00+00:00", "topic", "trial by jury", "no briefing was written"),
            ("2026-11-13T11:30:00+00:00", "briefing", shared.Q2, "The model declined to answer this question."),
            ("2026-11-13T11:31:00+00:00", "briefing", None, "x" * 600)):
        TIME.set(when)
        use.record_failure(state, kind, question, error)
    # saved briefings: one withdrawn, one a topic's, one featured, and what the columns allow
    for when, bid, question, options, up_to, model in (
            ("2026-10-25T12:00:00+00:00", shared.BRIEFING_1, shared.Q, OPTIONS, "2026-10-24", "claude-sonnet-5-5"),
            ("2026-11-05T12:00:00+00:00", shared.BRIEFING_2, shared.Q2, {**OPTIONS, "mode": "comprehensive"},
             "2026-11-04", "claude-opus-5-5"),
            ("2026-11-06T12:00:00+00:00", shared.WITHDRAWN, "An older question?", {}, None, None),
            ("2026-11-03T07:15:20+00:00", "briefingad", "trial by jury", {"mode": "summary", "topic": "trial-by-jury"},
             "2026-11-02", "claude-sonnet-5-5")):
        TIME.set(when)
        save_briefing(state, bid, question, options, shared.a_saved_briefing(question), up_to, model)
    with state.connect() as con:
        con.execute("INSERT INTO topic_versions (slug, briefing_id, prepared_at, records_up_to) VALUES "
                    "('trial-by-jury', 'briefingad', '2026-11-03T07:15:20+00:00', '2026-11-02')")
    TIME.set("2026-11-07T09:00:00+00:00")
    saved.withdraw_briefing(state, shared.WITHDRAWN, "It quoted the wrong debate.")
    saved.feature(state, shared.BRIEFING_1, "Trial by jury since 2020", None)
    # reports: open, closed with and without a public note, a takedown request, and the columns that may be empty
    for when, kind, message, briefing, contact, visitor, page in (
            ("2026-07-01T09:00:00+00:00", "general", "An old report, never closed.", None, "old@example.org",
             "0f" * 12, None),
            ("2026-11-10T09:00:00+00:00", "correction", "The date of the debate is wrong.", shared.BRIEFING_1,
             "reader@example.org", "1e" * 12, None),
            ("2026-11-11T09:00:00+00:00", "takedown", "Please remove my name.", None, "named@example.org",
             VISITOR_A, "/debates/2021-03-04/commons/trial-by-jury"),
            ("2026-11-12T09:00:00+00:00", "complaint", "A loaded word about a member.", None, None, None,
             "/people/10669"),
            ("2026-11-12T10:00:00+00:00", "correction", "Closed the same day.", shared.BRIEFING_2,
             "gone@example.org", VISITOR_B, None),
            ("2026-11-12T11:00:00+00:00", "general", "Dismissed the same day.", None, None, None, None)):
        TIME.set(when)
        rp.add_report(state, kind, message, briefing, contact, visitor, page)
    TIME.set("2026-11-12T15:00:00+00:00")
    rp.close_report(state, 5, "resolved", "The briefing was corrected.", "A briefing gave the wrong date; corrected.")
    rp.close_report(state, 6, "dismissed", "Nothing to change.", None)
    # statements in every state the old code has
    def sent(when: str, item: str, name: str, text: str, link: str | None = None,
             email: str = "office@example.org", visitor: str | None = VISITOR_A) -> int:
        TIME.set(when)
        return st.add_statement(state, name=name, role="MP for Leeds South", item=item, text=text, link=link,
                                email=email, visitor=visitor)

    one = sent("2026-11-08T09:00:00+00:00", f"/b/{shared.BRIEFING_1}", "Hilary Benn",
               shared.WORDS + "\n\nSecond paragraph.", "https://hansard.parliament.uk/commons/2021-03-04")
    TIME.set("2026-11-09T09:00:00+00:00")
    st.publish_statement(state, one, person=10669, checked="Replied from their parliament.uk address, 9 November.")
    TIME.set("2026-11-10T09:00:00+00:00")
    st.update_statement(state, one, text="New words, exactly as the author sent them.")
    sent("2026-11-12T09:00:00+00:00", shared.MEMBER, "Still Waiting", "A statement not yet decided at all.",
         visitor=None)
    two = sent("2026-11-01T09:00:00+00:00", f"/b/{shared.BRIEFING_2}", "Declined Lately",
               "This is about another member entirely.")
    TIME.set("2026-11-02T09:00:00+00:00")
    st.decline_statement(state, two, "It is about another member's conduct.")
    three = sent("2026-07-01T09:00:00+00:00", shared.MEMBER, "Withdrawn Long Ago", "Shown for a while, then not.")
    TIME.set("2026-07-02T09:00:00+00:00")
    st.publish_statement(state, three, checked="Telephoned the office.")
    TIME.set(OLD)
    st.withdraw_statement(state, three, None)
    four = sent("2026-07-05T09:00:00+00:00", f"/b/{shared.BRIEFING_2}", "Taken Down Lately",
                "Shown until last week, when it was taken down.")
    TIME.set("2026-07-06T09:00:00+00:00")
    st.publish_statement(state, four, person=10115, checked="Replied from their own address.")
    TIME.set("2026-11-06T09:00:00+00:00")
    st.decline_statement(state, four, "It broke the rules: it named a private person.")  # a published one, taken down
    five = sent("2026-06-01T09:00:00+00:00", "/debates/2021-03-04/commons/trial-by-jury", "Declined Long Ago",
                "Never shown, and declined in June.")
    TIME.set("2026-06-02T09:00:00+00:00")
    st.decline_statement(state, five, "Not from the member.")
    sent("2026-07-01T09:00:00+00:00", f"/b/{shared.WITHDRAWN}", "Waiting Too Long",
         "Nobody decided this one in four months.", email="waiting@example.org")
    six = sent("2026-11-11T09:00:00+00:00", shared.MEMBER, "Shown With No Link", "Published as written, no link.")
    TIME.set("2026-11-11T15:00:00+00:00")
    st.publish_statement(state, six, checked="Replied from their parliament.uk address.")
    # the site's own tables, which the import must leave alone: counts, feedback, a supporter, a topic check
    TIME.set("2026-11-13T11:40:00+00:00")
    use.count_question(state, shared.Q)
    use.record_event(state, "ask", VISITOR_A)
    use.record_visit(state, "/people/10669", "example.org", VISITOR_A)
    use.record_feedback(state, KEY_FRESH, shared.Q, True, VISITOR_A)
    use.record_feedback(state, None, shared.Q2, False, None)
    use.mark_supporter(state, VISITOR_B)
    run = state.start_run("sync")
    state.finish_run(run, {"files": 3})
    TIME.set(shared.START)

    # what the old code says of it
    with state.connect() as con:
        asked = {v: con.execute("SELECT count(*) FROM usage WHERE visitor = ? AND kind = 'answer' AND at >= ?",
                                (v, shared.DAY)).fetchone()[0] for v in (VISITOR_A, VISITOR_B, "nobody")}
        spent = {since: con.execute("SELECT coalesce(sum(cost_usd), 0) FROM usage WHERE at >= ?",
                                    (since,)).fetchone()[0] for since in (shared.DAY, shared.DAY[:7])}
    paths = (f"/b/{shared.BRIEFING_1}", f"/b/{shared.BRIEFING_2}", shared.MEMBER)
    admin = {"Authorization": f"Bearer {shared.ADMIN_TOKEN}"}
    figures = {
        "summary": use.summary(state), "alerts": use.alerts(state, {**limits, "daily_spend_cap_usd": 0.05}),
        "failures": use.recent_failures(state), "spent": spent,
        "briefings_today": asked,
        "reminder_due": {v: use.support_reminder_due(state, v, 2) for v in (VISITOR_A, VISITOR_B, None)},
        "refused": {str(who): use.check_caps(state, who[1], {**limits, "per_visitor_daily_questions": 2,
                                                              "per_user_daily_questions": 2}, who[0])
                    for who in ((VISITOR_A, None), (VISITOR_B, None), ("nobody", None), (None, researcher),
                                (None, 99))},
        "cached": {key: use.cached_events(state, key, 168) for key in (KEY_FRESH, KEY_STALE, KEY_NO_ADDRESS)},
        "briefings": {bid: saved.get_briefing(state, bid) for bid in (
            shared.BRIEFING_1, shared.BRIEFING_2, shared.WITHDRAWN, "briefingad", "briefingzz")},
        "shown": {bid: side.get(f"/api/briefings/{bid}") for bid in (
            shared.BRIEFING_1, shared.BRIEFING_2, shared.WITHDRAWN, "briefingad", "briefingzz")},
        "recent": saved.recent_briefings(state), "featured": saved.featured_list(state),
        "topics": side.get("/api/topics"), "topic": side.get("/api/topics/trial-by-jury"),
        "corrections": rp.corrections(state),
        "reports": rp.list_reports(state), "open_reports": rp.list_reports(state, "open"),
        "statements": st.list_statements(state),
        "published": {path: st.published(state, paths=[path]) for path in paths},
        "published_for_members": {str(n): st.published(state, person=n) for n in (10669, 10115)},
        "salt": state.get(f"visitor_salt:{shared.DAY}"),
        "statements_command": run_cli(["statements", "list", "--status", "all"]),
        "admin_usage": side.client.get("/api/admin/usage", headers=admin).json(),
        "admin_statements": side.client.get("/api/admin/statements", headers=admin).json(),
        "admin_reports": side.client.get("/api/admin/reports", headers=admin).json(),
        "admin_briefings": side.client.get("/api/admin/briefings", headers=admin).json(),
        "status": side.get("/api/status"),
    }
    state._conn().close()
    con = sqlite3.connect(state.path)
    sql = "\n".join(con.iterdump()) + "\n"
    con.close()
    return sql, shared.plain(figures, tmp=tmp)


# ---- a search index built by the old code ----------------------------------------------------------------------

QUERIES = ["trial by jury", "Northern Ireland security", "Finance Bill", "high speed rail"]


def searches(settings: Any) -> dict[str, Any]:
    """What a search of the index gives, by words, by meaning and through the engine; and what the site's own
    functions over the index give."""
    index = lance.SearchIndex(settings.index_dir, 64)
    embedder = HashingEmbedder(64)
    wheres = [None, "house = 'lords'", "year >= 2012 AND is_gov = true"]
    found: dict[str, Any] = {
        "stats": index.stats(), "vector_type": str(lance.vector_value_type(index.vectors().schema)),
        "unmerged": index.unmerged(), "words": [], "meaning": [], "engine": []}
    passages: list[str] = []
    for q in QUERIES:
        for where in wheres:
            rows = index.bm25(q, where, 16)
            found["words"].append({"query": q, "where": where, "hits": shared.close_hits(rows, "_score")})
            passages += [r["passage_id"] for r in rows[:3]]
            # every vector compared in full, so the answer does not depend on how the index was trained
            found["meaning"].append({"query": q, "where": where, "hits": shared.close_hits(
                index.vector(embedder.embed_query(q), where, 16, exact=True), "_distance")})
    engine = Engine(settings, embedder=embedder, today=shared.TODAY)
    for q in QUERIES:
        for filters, scope in ((Filters(), Scope()), (Filters(houses=["lords"]), Scope(written=False))):
            res = engine.search(q, filters, scope, [], None, None, 6, diversify=True)
            found["engine"].append({"query": q, "houses": filters.houses, "candidates": res.candidates,
                                    "semantic": res.semantic_available, "no_records": res.no_records,
                                    "records": [[r["record_id"], round(r["ranking"]["score"], 4)]
                                                for r in res.records]})
    passages = sorted(set(passages))
    sims = index.similarities(embedder.embed_query(QUERIES[0]), passages)
    found["similarities"] = {p: round(sims[p], 4) for p in sorted(sims)}
    keys = sorted({r["source_key"] for r in index.passages_for(passages).values()})[:2]
    found["rows_for_keys"] = {"keys": keys, "passages": sorted(r["passage_id"] for r in index.rows_for_keys(keys))}
    found["passages_for"] = {p: {k: str(v) for k, v in sorted(row.items()) if k in ("record_id", "source_key",
                                                                                   "house", "date", "heading")}
                             for p, row in sorted(index.passages_for(passages[:3]).items())}
    return found


def old_index(template: Path, out: Path) -> dict[str, Any]:
    environment(template / "data")
    settings = load_settings(**TESTS)
    found = searches(settings)
    with tarfile.open(out / "old-index.tar.gz", "w:gz") as tar:
        for name in ("index", "parquet", "reference"):
            tar.add(settings.data_dir / name, arcname=name)
    return found


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else shared.GOLDEN
    out.mkdir(parents=True, exist_ok=True)
    TIME.install()
    with tempfile.TemporaryDirectory() as folder:
        tmp = Path(folder)
        shared.write(out, "settings.json", {
            "cases": shared.settings_cases(tmp / "settings", REPO_ROOT, load_settings),
            "local_file": shared.local_settings_written(tmp, save_local_settings)})
        shared.write(out, "limiter.json", limiter())
        shared.write(out, "costs.json", costs())
        shared.write(out, "cache-keys.json", cache_keys(tmp))
        shared.write(out, "caps.json", caps(tmp))
        shared.write(out, "checks.json", checks())
        shared.write(out, "interests.json", interests_check())
        shared.write(out, "ask-scripted.json", shared.plain(scripted(tmp), tmp=tmp))
        template = tmp / "template"
        build_data(template)
        shared.write(out, "index.json", old_index(template, out))
        shared.write(out, "ask-real.json", shared.plain(real(tmp, template), tmp=tmp))
        shared.write(out, "topics.json", topic_briefings(tmp))
        shared.write(out, "replies.json", shared.plain(replies(tmp, template), tmp=tmp))
        shared.write(out, "backups.json", backups(tmp))
        shared.write(out, "offsite.json", offsite_copies(tmp))
        sql, figures = old_state(tmp)
        (out / "old-state.sql").write_text(sql, encoding="utf-8")
        shared.write(out, "old-state.json", figures)
    print(f"recorded into {out}")


if __name__ == "__main__":
    main()
