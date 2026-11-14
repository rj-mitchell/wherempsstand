"""The site gives what it gave before it moved onto wts-core (the Where They Stand sites' shared package).

tests/golden/ holds what the site's own code gave on 14 November 2026, recorded by tests/golden/record.py before
anything changed. Each test here plays the same scenario (tests/golden/shared.py) on the code as it is now and
compares. Where the two differ on purpose, the difference is asserted, with the entry of wts-core's
docs/AS-BUILT.md or docs/MIGRATION.md that it comes from; nothing is skipped.
"""

from __future__ import annotations

from typing import Any

import shared

from hansard.config import REPO_ROOT, load_settings, save_local_settings

# Keys this move added to config/settings.toml, each with the value the code had as its fallback before or a
# setting of something new: the recorded settings are the same but for these.
ADDED_SETTINGS: dict[str, dict[str, Any]] = {}


def without_added(raw: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for section, values in raw.items():
        added = ADDED_SETTINGS.get(section, {})
        kept = {k: v for k, v in values.items() if k not in added} if isinstance(values, dict) else values
        if kept or section not in ADDED_SETTINGS:
            out[section] = kept
    return out


# ---- settings --------------------------------------------------------------------------------------------------


def test_settings_resolve_as_before(tmp_path):
    golden = shared.read("settings.json")
    now = shared.settings_cases(tmp_path / "settings", REPO_ROOT, load_settings)
    assert [c["name"] for c in now] == [c["name"] for c in golden["cases"]]
    for was, case in zip(golden["cases"], now, strict=True):
        gives = dict(case["gives"])
        for name in ("raw", *ADDED_SETTINGS):
            if name == "raw":
                gives["raw"] = without_added(gives["raw"])
            elif name in gives:
                gives[name] = {k: v for k, v in gives[name].items() if k not in ADDED_SETTINGS[name]}
        assert gives == was["gives"], case["name"]


def test_the_local_settings_file_is_written_byte_for_byte_as_before(tmp_path):
    assert shared.local_settings_written(tmp_path, save_local_settings) == shared.read("settings.json")["local_file"]
