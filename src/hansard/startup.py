"""Every check on the settings that can refuse a start, made before anything opens the state database.

The first thing to open the state database on new code may change it for good (the shared package makes its tables,
and once brings the site's old rows across: ``hansard/store/wts.py``). So whatever could stop the site from starting
is found first, with the database not yet touched: a deploy that fails on its settings then leaves the old site's
database exactly as it was. ``open_core`` calls this, so it holds for the web app and for every command;
``hansard check-settings`` runs it alone, for ``deploy/update.sh`` to try the new version against the server's own
settings before the old site is stopped.
"""

from __future__ import annotations

from wts_core.briefing import DeskSettings, desk_settings

from hansard.config import Settings
from hansard.messages import MESSAGES

FORGET_IDLE_VISITORS_OVER = 50_000  # the per-minute limiter forgets idle visitors once it holds this many
AT_ONCE_PER_VISITOR = "briefings_at_once_per_reader"  # this site's name, in [usage], for the package's figure


def models_named(settings: Settings) -> tuple[str, ...]:
    """Every model the settings name: the one that writes briefings, and the one that reads questions and checks
    interests. Each must have prices of its own under ``[usage.models]``, so none is costed at another's."""
    from hansard.rag.query_parse import parse_model

    return tuple(dict.fromkeys(str(m) for m in (settings.answer.get("model"), parse_model(settings)) if m))


def check_settings(settings: Settings) -> DeskSettings:
    """Everything the flow of a briefing needs from the settings, or one error (``SettingsError``) that names the
    settings files and every key that is missing or wrong: each limit and cap, each figure for briefings in
    progress and the answer cache, prices for every model the settings name, and every message a reader can be
    shown. Reads the settings and nothing else."""
    return desk_settings(settings, MESSAGES, at_once_per_visitor=AT_ONCE_PER_VISITOR,
                         forget_over=FORGET_IDLE_VISITORS_OVER, require_prices_for=models_named(settings))
