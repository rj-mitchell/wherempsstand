"""Turn the research file on party groups and caucuses into the data file the site reads:
data_static/groups/groups.json.

    uv run python scripts/groups_from_research.py path/to/research/groups.json [--include-pending]

The research file lists, for each group, its names (with notes on which are ambiguous in Hansard) and every published
list of names found for it, each name matched to a UK Parliament ID. This script keeps what the owner decided the
site may use, and records for each membership its evidence: the group, the member's UK Parliament ID, the kind of
evidence, its date, the source's address and publisher, any archived copy and the role as published. It can be run
again whenever the research file gains lists (later lists are added the same way).

The owner's decisions (12 November 2026), applied here:

1. IPSA records of MPs paying for a group's pooled research service are kept, as their own kind ("ipsa"), shown as
   "paid for the group's research service (IPSA records, <years>)", never as membership. Whether they are shown at
   all is a setting ([groups] show_ipsa), off until the charity has legal advice.
2. The Socialist Parliamentary Research Group stays a group of its own, not merged with the Socialist Campaign Group.
3. The contributors to the Socialist Campaign Group's 2026 pamphlet are left out (authors, not signatories).
4. Mainstream is included, as signatories of its founding statement.
5. The Conservative Environment Network's own page: sitting UK MPs and peers only (its Fellows, former MPs, and MSPs
   are left out); the page is undated, so its "accessed" date is kept, with a note that it should be checked again.
6. The party a member had on the date of the list, never their latest party.
7. (12 November 2026, later) The Common Sense Group's letter of 9 November 2020, reproduced in full by a signatory,
   is used as signatories. The Net Zero Scrutiny Group's letter of 2 January 2022 is held back until it is checked
   against the archived original: its names come from The Week's prose account, not a copy of the letter
   (``--include-pending`` uses it once the owner has checked it). The owner checked it on 12 November 2026 against
   the archived copy of the Telegraph letter (archive.ph/LaeNS): all 20 names match, so it is used, citing the
   original. The letter of 28 June 2023 that the Net Zero
   Scrutiny Group's chair coordinated is not used: signing a letter a group's chair organised is not evidence of
   belonging to the group, and the reproduction's count of signatories disagrees with its own list.

8. (12 November 2026) IPSA records are used (settings: [groups] show_ipsa), labelled as payments for a group's
   research service, never as membership, on the basis in the charity's policies/special-category-data.md. A claim
   whose IPSA service name and the claim's own description name different groups is left out, as unclear (one in
   the research: a 2021-22 claim under the Common Sense Group's service described as "European Research Group").

Not used: press estimates, Wikipedia, and names the research could not match to a UK Parliament ID.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parents[1] / "data_static" / "groups" / "groups.json"

# groups named without "the" in a sentence ("Signed Mainstream's founding statement")
NO_ARTICLE = {"mainstream", "blue-labour", "labour-first", "progressive-britain", "labour-to-win", "labour-together",
              "liberal-reform", "popular-conservatives", "one-nation-conservatives"}
# lists not used, with the reason (decisions 3 and 7)
EXCLUDED_LISTS = {
    ("socialist-campaign-group", "2026-09-29"): "decision 3: contributors to a pamphlet, not signatories",
    ("net-zero-scrutiny-group", "2023-06-28"): "decision 7: a letter the group's chair coordinated, not the group's; "
                                               "its reproduction's count disagrees with its list",
}
# lists held back until checked against the original (decision 7); --include-pending uses them. None at present:
# the Net Zero Scrutiny Group's letter of 2 January 2022 was checked by the owner on 12 November 2026.
PENDING_LISTS: dict[tuple[str, str], str] = {}
# words that name a group in an IPSA claim's own description, to catch claims filed under one group's service but
# described as another's (decision 8)
IPSA_NAMES = {
    "european-research-group": ("european research group", "erg"),
    "common-sense-group": ("common sense group",),
    "northern-research-group": ("northern research group",),
    "socialist-parliamentary-research-group": ("socialist parliamentary research group",),
    "labour-movement-for-europe": ("labour movement for europe",),
    "conservative-union-research-group": ("conservative union research group",),
}
GROUP_FIELDS = ("id", "party", "description", "description_source", "aliases", "notes")


def short_name(name: str) -> str:
    """'Mainstream (Labour)' -> 'Mainstream': the name as used in a sentence."""
    return re.sub(r"\s*\([^)]*\)$", "", name).strip()


def document(title: str) -> str:
    """What was signed, from the list's title."""
    t = title.lower()
    for word in ("founding statement", "letter", "statement"):
        if word in t:
            return word
    return "list"


def publisher_short(lst: dict[str, Any]) -> str:
    """Who published the copy used, for the label: 'LabourList (reproducing ...)' -> 'LabourList'; the group's own
    site -> 'its website'; a member's site -> 'Sir Edward Leigh MP's website'. Decided on the publisher alone: the
    notes on who made a list public can mention other websites."""
    publisher = lst.get("publisher") or ""
    if re.search(r"(?i)\b(?:its|the group's) own website\b", publisher):
        return "its website"
    member_site = re.match(r"(.+?\bMP)'s website\b", publisher)
    if member_site:
        return f"{member_site.group(1)}'s website"
    return short_name(publisher)


def unclear_claim(group: str, claim: dict[str, Any]) -> bool:
    """A claim filed under this group's service whose own description names a different group (decision 8)."""
    details = (claim.get("details") or "").lower()
    names_other = any(re.search(rf"\b{re.escape(n)}\b", details) for other, ns in IPSA_NAMES.items() if other != group
                      for n in ns)
    names_own = any(re.search(rf"\b{re.escape(n)}\b", details) for n in IPSA_NAMES.get(group, ()))
    return names_other and not names_own


def claim_years(claims: list[dict[str, Any]]) -> tuple[str, str] | None:
    """The first and last years of a member's payments (refunds alone are not payments)."""
    days = [datetime.strptime(c["date"], "%d/%m/%Y").date() for c in claims or []
            if float(c.get("amount_claimed") or 0) > 0]
    if not days:
        return None
    return str(min(days).year), str(max(days).year)


def convert(research: list[dict[str, Any]], include_pending: bool = False) -> dict[str, Any]:
    groups, memberships, dropped = [], [], {}
    for g in research:
        name = short_name(g["name"])
        groups.append({**{k: g[k] for k in GROUP_FIELDS if k in g}, "name": name, "full_name": g["name"],
                       "the": g["id"] not in NO_ARTICLE})
        for lst in g.get("lists") or []:
            key = (g["id"], lst["date"])
            if key in EXCLUDED_LISTS:
                dropped[f"{g['id']} {lst['date']}"] = EXCLUDED_LISTS[key]
                continue
            if key in PENDING_LISTS and not include_pending:
                dropped[f"{g['id']} {lst['date']}"] = PENDING_LISTS[key]
                continue
            kind = {"subscribers": "ipsa", "signatories": "signatories", "members": "members"}[lst["kind"]]
            base = {"group": g["id"], "kind": kind, "date": lst["date"], "date_kind": lst["date_kind"],
                    "parliament": lst["parliament"], "source_url": lst["url"], "publisher": lst["publisher"],
                    "publisher_short": publisher_short(lst), "archive_url": lst.get("archive_url"),
                    "title": lst["title"], "document": document(lst["title"]) if kind == "signatories" else None}
            if kind == "members":
                base["note"] = ("The page is undated: what it said on the day it was accessed. To be checked again "
                                "from time to time.")
            for m in lst["members"]:
                role = m.get("role_as_published")
                if not m.get("member_id"):
                    dropped.setdefault("no UK Parliament ID (MSPs and unmatched names)", 0)
                    dropped["no UK Parliament ID (MSPs and unmatched names)"] += 1
                    continue
                if kind == "members" and role and re.search(r"Fellow|MSP", role):  # decision 5
                    dropped.setdefault("not a sitting UK parliamentarian (CEN Fellows)", 0)
                    dropped["not a sitting UK parliamentarian (CEN Fellows)"] += 1
                    continue
                entry = {**base, "member_id": str(m["member_id"]), "name_as_published": m["name_as_published"],
                         "role_as_published": role, "party_at_list_date": m.get("party_at_list_date"),
                         "match": m.get("match")}
                if kind == "ipsa":
                    claims = [c for c in m.get("ipsa_claims") or [] if not unclear_claim(g["id"], c)]
                    if len(claims) < len(m.get("ipsa_claims") or []):
                        dropped.setdefault("IPSA claims described as another group's (unclear)", 0)
                        dropped["IPSA claims described as another group's (unclear)"] += 1
                    years = claim_years(claims)
                    if years is None:
                        continue
                    entry["ipsa_years"] = years[0] if years[0] == years[1] else f"{years[0]}–{years[1]}"
                memberships.append(entry)
    memberships.sort(key=lambda m: (m["group"], m["date"], m["kind"], m["member_id"]))
    return {"about": ("Party groups and caucuses: published evidence of who belongs, one entry per member per list. "
                      "Made by scripts/groups_from_research.py; see that script for the owner's decisions."),
            "converted": date.today().isoformat(), "groups": groups, "memberships": memberships, "left_out": dropped}


def main(path: Path, include_pending: bool = False) -> None:
    data = convert(json.loads(path.read_text(encoding="utf-8")), include_pending)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    by: dict[tuple[str, str], int] = {}
    for m in data["memberships"]:
        by[(m["group"], m["kind"])] = by.get((m["group"], m["kind"]), 0) + 1
    for (g, k), n in sorted(by.items()):
        print(f"{g:40} {k:12} {n}")
    print(f"{len(data['memberships'])} memberships of {len(data['groups'])} groups; left out: {data['left_out']}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(Path(args[0]), include_pending="--include-pending" in sys.argv)
