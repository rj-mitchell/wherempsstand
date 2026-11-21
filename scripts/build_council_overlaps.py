"""Build data_static/geo/constituency_councils.csv: for each Westminster constituency (2024 boundaries), the councils
whose areas overlap it, lower and upper tier, with ONS codes and names. Also writes the first copy of Where Councils
Stand's list of councils (data_static/sisters/councils.json), which the twice-daily update then keeps fresh
(``hansard.sisters.refresh_councils``).

    uv run python scripts/build_council_overlaps.py [--lookup lookup.csv] [--councils councils.json]

Source: ONS, "Ward to Westminster Parliamentary Constituency to Local Authority District to Upper Tier Local
Authority (July 2024) Lookup in the UK" (item 62eb9df29a2f4521b5076a419ff9a47e on the Open Geography Portal,
https://geoportal.statistics.gov.uk/), one row for each ward or part of a ward in a constituency. Source: Office for
National Statistics licensed under the Open Government Licence v.3.0 (the ONS geography licences page: lookups carry
this statement only, with no Ordnance Survey notice).

A council overlaps a seat when at least one of its wards (or part of one) is in the seat. Each row gives:

- ``tier``: ``unitary`` where the district is its own upper tier (unitary councils, metropolitan and London boroughs,
  Scotland, Wales and Northern Ireland), otherwise ``district`` for the lower tier and ``county`` for the county
  council above it;
- ``wards``: how many of the seat's wards (or parts of wards) are in the council's area, to list the council that
  covers most of the seat first.

Where Councils Stand's list (https://wherecouncilsstand.com/api/councils) says which councils have pages there; the
site links only to those. Run this again when the ONS publishes a new lookup (after a boundary or local government
change), and commit both files.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data_static" / "geo" / "constituency_councils.csv"
COUNCILS_OUT = ROOT / "data_static" / "sisters" / "councils.json"
LOOKUP_ITEM = "62eb9df29a2f4521b5076a419ff9a47e"
LOOKUP_URL = f"https://geoportal.statistics.gov.uk/api/download/v1/items/{LOOKUP_ITEM}/csv?layers=0"
COUNCILS_URL = "https://wherecouncilsstand.com/api/councils"
FIELDS = ["pcon_code", "pcon_name", "council_code", "council_name", "tier", "wards"]

sys.path.insert(0, str(ROOT / "src"))


def download(url: str) -> bytes:
    for attempt in range(6):
        if attempt:
            time.sleep(5 * attempt)
        req = urllib.request.Request(url, headers={"User-Agent": "wherempsstand-build"})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310 - fixed https addresses
                if resp.status == 202:  # the portal builds the file on first request
                    continue
                return resp.read()
        except OSError as exc:
            print(f"  {url}: {exc}; retrying", file=sys.stderr)
    raise SystemExit(f"{url} could not be downloaded after several attempts; try again later")


def overlaps(text: str) -> list[dict[str, str | int]]:
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    need = {"WD24CD", "PCON24CD", "PCON24NM", "LAD24CD", "LAD24NM", "UTLA24CD", "UTLA24NM"}
    if not rows or not need <= set(rows[0]):
        raise SystemExit(f"the lookup does not have the expected columns ({', '.join(sorted(need))})")
    seats: dict[str, str] = {}
    wards: dict[tuple[str, str], set[str]] = defaultdict(set)
    names: dict[str, str] = {}
    tiers: dict[str, str] = {}
    for r in rows:
        pcon, lad, utla = r["PCON24CD"].strip(), r["LAD24CD"].strip(), r["UTLA24CD"].strip()
        if not (pcon and lad and utla):
            continue
        seats[pcon] = r["PCON24NM"].strip()
        names[lad], names[utla] = r["LAD24NM"].strip(), r["UTLA24NM"].strip()
        if lad == utla:
            tiers[lad] = "unitary"
            wards[(pcon, lad)].add(r["WD24CD"])
        else:
            tiers[lad], tiers[utla] = "district", "county"
            wards[(pcon, lad)].add(r["WD24CD"])
            wards[(pcon, utla)].add(r["WD24CD"])
    out = [{"pcon_code": pcon, "pcon_name": seats[pcon], "council_code": code, "council_name": names[code],
            "tier": tiers[code], "wards": len(ws)} for (pcon, code), ws in wards.items()]
    order = {"county": 0, "unitary": 1, "district": 1}
    out.sort(key=lambda r: (r["pcon_code"], order[str(r["tier"])], -int(r["wards"]), r["council_name"]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--lookup", type=Path, help="the ONS lookup as CSV, already downloaded (otherwise fetched)")
    ap.add_argument("--councils", type=Path, help="Where Councils Stand's /api/councils, saved (otherwise fetched)")
    args = ap.parse_args()

    text = args.lookup.read_text(encoding="utf-8-sig") if args.lookup else download(LOOKUP_URL).decode("utf-8-sig")
    rows = overlaps(text)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    seats = {r["pcon_code"] for r in rows}
    print(f"{OUT.relative_to(ROOT)}: {len(seats)} constituencies, {len(rows)} council overlaps")

    from hansard.sisters import COUNCILS_CODES, councils_listing

    raw = args.councils.read_bytes() if args.councils else download(COUNCILS_URL)
    listing = councils_listing(json.loads(raw), fetched=datetime.now(UTC).date().isoformat())
    COUNCILS_OUT.parent.mkdir(parents=True, exist_ok=True)
    COUNCILS_OUT.write_text(json.dumps(listing, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    covered = {c["gss"] for c in listing["councils"]}
    codes = {COUNCILS_CODES.get(str(r["council_code"]), str(r["council_code"])) for r in rows}
    missing = sorted(codes - covered)
    print(f"{COUNCILS_OUT.relative_to(ROOT)}: {len(covered)} councils; {len(codes) - len(missing)} of the "
          f"{len(codes)} councils in the lookup have a page on Where Councils Stand"
          + (f" (not: {', '.join(missing)})" if missing else ""))


if __name__ == "__main__":
    main()
