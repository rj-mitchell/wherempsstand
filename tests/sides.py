"""The site as it is now, set up to play the scenarios of tests/golden/shared.py: its real web app on a fixed clock,
with the day's visitor secret and the saved briefings' addresses fixed, so that what it sends and writes can be
compared with what the old code's recordings hold (tests/test_parity.py and its neighbours)."""

from __future__ import annotations

import dataclasses
import shutil
import sqlite3
import types
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import shared
from fastapi.testclient import TestClient
from wts_core.adapters.hashing import HashingEmbedder
from wts_core.testing import FixedClock

from hansard import topics
from hansard.api import app as web
from hansard.api import auth
from hansard.api import usage as use
from hansard.config import Settings, load_settings
from hansard.engine import Engine
from hansard.rag.pipeline import Pipeline
from hansard.store import state as store

from .conftest import model_of

TESTS = {"embedding.backend": "hashing", "embedding.dim": 64}
# fewer sources than the site's own settings give, as when the recordings of whole answers were made
FEWER = {"summary_sources": 5, "comprehensive_sources": 8, "side_sources": 3, "comprehensive_side_sources": 4}


def fix_the_sites_own_clock(clock: FixedClock, monkeypatch: pytest.MonkeyPatch) -> None:
    """The site's own tables (counts, accounts, topic versions) are written by the site's own code, which reads the
    time itself: it is given the scenario's clock."""

    class Frozen(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
            return clock.now()

    for module in (use, store, topics, auth):
        monkeypatch.setattr(module, "datetime", Frozen)


def settings_in(folder: Path, monkeypatch: pytest.MonkeyPatch, auth_required: bool = False, **usage: Any) -> Settings:
    """The site's real settings with its data in ``folder``/data, as the recorder set them for each scenario."""
    shared.clear_environment()
    monkeypatch.setenv("HANSARD_DATA_DIR", str(folder / "data"))
    monkeypatch.setenv("HANSARD_ADMIN_TOKEN", shared.ADMIN_TOKEN)
    for name in ("HANSARD_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "HANSARD_BACKUP_DIR"):
        monkeypatch.delenv(name, raising=False)
    settings = load_settings(**TESTS)
    raw = {**settings.raw, "usage": {**settings.raw["usage"], **usage},
           "auth": {**settings.raw.get("auth", {}), "required": auth_required},
           "site": {**settings.raw.get("site", {}), "donate_url": shared.DONATE}}
    return dataclasses.replace(settings, raw=raw)


def new_side(pipeline: Any, settings: Settings, clock: FixedClock, monkeypatch: pytest.MonkeyPatch) -> shared.Side:
    """The site's own ``create_app`` over ``pipeline``, as a side a scenario can be played on."""
    fix_the_sites_own_clock(clock, monkeypatch)
    ids = shared.briefing_ids()
    monkeypatch.setattr(use, "new_briefing_id", lambda chance=None: next(ids))
    state = store.StateDB(settings.state_db)
    app = web.create_app(settings, pipeline=pipeline, state=state, clock=clock)
    app.state.core.set(f"visitor_salt:{shared.DAY}", shared.SALT)
    client = TestClient(app)

    def stream(body: dict[str, Any]):
        route = next(r.endpoint for r in app.routes if getattr(r, "path", "") == "/api/ask")
        with monkeypatch.context() as patch:
            patch.setattr(web, "StreamingResponse", lambda content, **kwargs: content)
            request = types.SimpleNamespace(client=types.SimpleNamespace(host="testclient"), headers={}, cookies={})
            return route(web.AskBody(**body), request, user=None)

    def another_client():
        other = TestClient(app)
        other.cookies.update(client.cookies)
        return other

    def sign_in() -> None:
        auth.create_user(state, "researcher@example.org", "correct horse battery")
        r = client.post("/api/auth/login", json={"email": "researcher@example.org",
                                                 "password": "correct horse battery"})
        assert r.status_code == 200, r.text

    side = shared.Side(client=client, pipeline=pipeline, connect=lambda: sqlite3.connect(state.path), new=True,
                       stream=stream, another_client=another_client, sign_in=sign_in)
    side.state, side.settings, side.app, side.clock = state, settings, app, clock
    return side


def scripted_side(folder: Path, monkeypatch: pytest.MonkeyPatch, *, has_model: bool = True, auth: bool = False,
                  **usage: Any) -> shared.Side:
    clock = FixedClock(shared.START)
    settings = settings_in(folder, monkeypatch, auth, **usage)
    pipeline = shared.ScriptedPipeline(clock.advance, settings.answer["model"], has_model=has_model)
    return new_side(pipeline, settings, clock, monkeypatch)


def real_side(template: Settings, folder: Path, sdk: Any, monkeypatch: pytest.MonkeyPatch, *,
              embedder: Any = None, **usage: Any) -> shared.Side:
    """The site's real pipeline over a copy of the tests' own index (``template``: tests/conftest.py's
    ``indexed_settings``), with a faked Anthropic SDK (None: no model at all). ``embedder`` stands in for the
    tests' own (one that keeps what it is given, say)."""
    clock = FixedClock(shared.START)
    shutil.copytree(template.data_dir, folder / "data", ignore=shutil.ignore_patterns("state.sqlite*"))
    src, dst = sqlite3.connect(template.state_db), sqlite3.connect(folder / "data" / "state.sqlite")
    try:  # the files of the record read in (the site's own table), with what is still in the write-ahead log
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    settings = settings_in(folder, monkeypatch, **usage)
    settings = dataclasses.replace(settings, raw={**settings.raw, "answer": {**settings.raw["answer"], **FEWER}})
    engine = Engine(settings, embedder=embedder or HashingEmbedder(64), today=shared.TODAY)
    return new_side(Pipeline(settings, engine, client=model_of(sdk), today=shared.TODAY), settings, clock,
                    monkeypatch)
