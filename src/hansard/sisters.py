"""Links to the charity's sister sites where their records meet this one's (docs/DECISIONS.md, 41).

Where Councils Stand keeps every council's minutes and named votes, at /councils/<ONS code>. A sitting MP's page lists
the councils whose areas overlap their seat, from the ONS lookup of wards to constituencies and councils (July 2024),
kept in data_static/geo/constituency_councils.csv by scripts/build_council_overlaps.py, and links each one Where
Councils Stand has a page for.

Which councils it has pages for comes from its own public list (/api/councils). The first copy is kept with the code
(data_static/sisters/councils.json); the twice-daily update fetches it again (``refresh_councils``) and keeps the
last good copy in the data folder if the site cannot be reached or answers with something that is not a list of
councils. Nothing is asked of another site while a page is served.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import re
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from hansard.config import REPO_ROOT, STATIC_DIR

log = logging.getLogger(__name__)

COUNCILS_SITE = "https://wherecouncilsstand.com"
COUNCILS_NAME = "Where Councils Stand"
COUNCILS_API = f"{COUNCILS_SITE}/api/councils"
OVERLAPS = STATIC_DIR / "geo" / "constituency_councils.csv"
COUNCILS_SEED = STATIC_DIR / "sisters" / "councils.json"
MIN_COUNCILS = 100  # fewer than this is not Where Councils Stand's list of the UK's councils
# Where Councils Stand knows Barnsley and Sheffield by the codes they were given in 2019 (its
# scripts/build_councils.py, SUPERSEDED); the ONS lookup of July 2024 still has the earlier ones
COUNCILS_CODES = {"E08000016": "E08000038", "E08000019": "E08000039"}
SHELF = (REPO_ROOT / "web" / "dist" / "family" / "shelf.html", REPO_ROOT / "web" / "public" / "family" / "shelf.html")


def council_url(gss: str) -> str:
    return f"{COUNCILS_SITE}/councils/{gss}"


# Where Councils Stand's list of councils

def councils_path(settings: Any) -> Path:
    """The copy the update keeps fresh, in the data folder."""
    return Path(settings.data_dir) / "sisters" / "councils.json"


def councils_listing(raw: Any, fetched: str) -> dict[str, Any]:
    """The parts of Where Councils Stand's /api/councils this site uses: each council's code, name and kind. Raises
    ValueError if ``raw`` is not that list."""
    rows = raw.get("councils") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise ValueError("the answer has no list of councils")
    councils = []
    for c in rows:
        if isinstance(c, dict) and isinstance(c.get("gss"), str) and isinstance(c.get("name"), str) \
                and re.fullmatch(r"[A-Z]\d{8}", c["gss"]) and c["name"].strip():
            councils.append({"gss": c["gss"], "name": c["name"].strip(), "type_name": str(c.get("type_name") or "")})
    if len(councils) < MIN_COUNCILS:
        raise ValueError(f"the list has {len(councils)} councils, fewer than {MIN_COUNCILS}")
    return {"source": COUNCILS_API, "fetched": fetched, "councils": sorted(councils, key=lambda c: c["gss"])}


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("councils"), list) else None


def _get(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "wherempsstand-update", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - a fixed https address
        return json.loads(resp.read())


def refresh_councils(settings: Any, fetch: Callable[[str], Any] = _get) -> dict[str, Any]:
    """Fetch Where Councils Stand's list again for the update. If the site cannot be reached, or its answer is not
    the list, or is less than half as long as the last good copy, the last good copy is kept."""
    path = councils_path(settings)
    last = _read(path) or _read(COUNCILS_SEED)
    try:
        listing = councils_listing(fetch(COUNCILS_API), fetched=datetime.now(UTC).date().isoformat())
        if last and len(listing["councils"]) < len(last["councils"]) / 2:
            raise ValueError(f"the list has {len(listing['councils'])} councils, against {len(last['councils'])} "
                             "in the last copy")
    except (OSError, ValueError) as exc:  # includes timeouts, HTTP errors and JSON that does not parse
        log.warning("Where Councils Stand's list of councils not updated: %s", exc)
        return {"error": str(exc)[:200], "kept": (last or {}).get("fetched")}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(listing, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return {"councils": len(listing["councils"])}


_listed: dict[Path, tuple[float, dict[str, dict[str, str]]]] = {}


def listed_councils(settings: Any) -> dict[str, dict[str, str]]:
    """The councils Where Councils Stand has pages for, by code: the update's copy, or the one kept with the code.
    Read again when the update writes a new copy."""
    path = councils_path(settings)
    if not path.is_file():
        path = COUNCILS_SEED
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    kept = _listed.get(path)
    if kept is None or kept[0] != mtime:
        data = _read(path) or {"councils": []}
        kept = (mtime, {c["gss"]: c for c in data["councils"] if isinstance(c, dict) and c.get("gss")})
        _listed[path] = kept
    return kept[1]


# Councils overlapping a seat

@lru_cache(maxsize=1)
def _overlaps() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    if not OVERLAPS.is_file():
        return out
    with open(OVERLAPS, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out.setdefault(r["pcon_code"], []).append(r)
    return out


def councils_for_seat(settings: Any, gss_code: str | None) -> list[dict[str, Any]]:
    """The councils whose areas overlap a constituency (2024 boundaries), the county council first where there is
    one, then by how much of the seat each covers. Each has its page on Where Councils Stand (``url``), or None
    where that site has no page for it."""
    if not gss_code:
        return []
    listed = listed_councils(settings)
    out = []
    for r in _overlaps().get(gss_code, []):
        code = COUNCILS_CODES.get(r["council_code"], r["council_code"])
        there = listed.get(code)
        out.append({"gss": code, "name": there["name"] if there else r["council_name"],
                    "tier": r["tier"], "kind": (there or {}).get("type_name") or None,
                    "url": council_url(code) if there else None})
    return out


# The sister sites' spines, from the family kit's shelf

@lru_cache(maxsize=8)
def spine(site: str) -> str:
    """The sister site's spine, as the family kit's footer shelf draws it (an SVG in its cloth), or "" without the
    kit's files."""
    for path in SHELF:
        try:
            shelf = path.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(rf'<svg class="wts-spine wts-cloth--{re.escape(site)}"[\s\S]*?</svg>', shelf)
        if m:
            return m.group(0)
    return ""
