"""Election results for every MP's seat, from the UK Parliament Members API (Open Parliament Licence).

For each MP since 1997 (matched by UK Parliament ID, never by name) the API lists the constituencies they have
represented, with dates. For each of those constituencies it gives every general election and by-election result:
the majority, votes cast, electorate and outcome. From these the site works out an MP's majority at the time they
spoke (the result of the most recent election in their seat before that day) and now.

The list of a seat's results gives no candidates, so the party that came second in each result (the runner-up, and its
votes) is read from that result's own page, one request per result, once: it never changes. A result whose runner-up
cannot be known (a single candidate, a tie for second place, votes missing) says so; nothing is guessed. The Brexit
Party and Reform UK are one registered party, renamed in January 2021: both count as Reform UK (``reform``) for
filtering and grouping, and each result keeps the name the party stood under at that election.

Cached in the data folder and refreshed weekly (by-elections happen); if the API cannot be reached, the previous
copy is kept.

The API has results from 2010 only. For the general elections of 1997, 2001 and 2005 the House of Commons Library's
results by constituency (CBP-8647, Open Parliament Licence) are used, kept in data_static/elections (see
scripts/hocl_election_results.py); for the by-elections of 2001 to 2010 its research papers 05/34 and 10/50 (see
scripts/library_by_elections.py), and for those of 1997 to 2001 the House of Commons Information Office's Factsheet
M16 (see scripts/factsheet_by_elections.py; it gives no electorate). They are attached to a seat by its name, for the
elections in which an MP the API lists sat for it. Those files give the runner-up too, except that the Library's
general election file adds smaller parties and independents together as "Other": where "Other" won or came second, the
runner-up is not known.
"""

from __future__ import annotations

import contextlib
import csv
import json
import logging
import re
import time
import unicodedata
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from hansard.config import STATIC_DIR
from hansard.sources.demographics import USER_AGENT

log = logging.getLogger(__name__)

API = "https://members-api.parliament.uk/api"
REFRESH = timedelta(days=7)
SOURCE = "UK Parliament Members API (constituency election results)"
LIBRARY_CSV = STATIC_DIR / "elections" / "general_elections_1997_2005.csv"
BY_ELECTIONS_CSV = STATIC_DIR / "elections" / "by_elections_2001_2010.csv"
BY_ELECTIONS_1997_CSV = STATIC_DIR / "elections" / "by_elections_1997_2001.csv"
# every MP's seats and every seat's results from the Members API, saved with the code
# (scripts/snapshot_election_results.py): fills any member or seat the server's own copy lacks, so results show even
# if Parliament's service cannot be reached from the server
SNAPSHOT = STATIC_DIR / "elections" / "results_snapshot.json"
LIBRARY_SOURCE = "House of Commons Library, general election results by constituency (CBP-8647)"
BY_ELECTIONS_SOURCE = "House of Commons Library, research papers 05/34 and 10/50 (by-elections 2001-2010)"
BY_ELECTIONS_1997_SOURCE = "House of Commons Information Office, Factsheet M16 (by-elections 1997-2001)"

# Majority as a share of votes cast: (key, label, upper bound, exclusive)
MAJORITY_BANDS = [
    ("marginal", "marginal (majority under 10%)", 10.0),
    ("middle", "majority 10% to 20%", 20.0),
    ("safe", "safe (majority over 20%)", 1000.0),
]
MAJORITY_BAND_LABEL = {k: label for k, label, _ in MAJORITY_BANDS}
TENURE_LABEL = {"first-term": "first-term MPs", "returning": "MPs re-elected at least once"}

# Party names in election results -> the site's party ids (as in parlparse), so a runner-up can be filtered on with the
# same ids as a member's party. Matched against the lower-cased name; the first match wins. Any other party gets an id
# made from its name. The Brexit Party is Reform UK under its earlier name (the same registered party, renamed in 2021).
RESULT_PARTIES = [
    (re.compile(r"^conservative"), "conservative"),
    (re.compile(r"^labour"), "labour"),  # Labour, and Labour and Co-operative candidates (one whip)
    (re.compile(r"^liberal democrat"), "liberal-democrat"),
    (re.compile(r"^scottish national|^snp$"), "scottish-national-party"),
    (re.compile(r"^plaid"), "plaid-cymru"),
    # the Green parties of England and Wales, Scotland and Northern Ireland: one id, as on the site (they never stand
    # against each other)
    (re.compile(r"^(the )?green|^scottish green"), "green"),
    (re.compile(r"^reform uk|^(the )?brexit party"), "reform"),
    (re.compile(r"^uk independence|^ukip$"), "ukip"),
    (re.compile(r"^democratic unionist|^dup$"), "dup"),
    (re.compile(r"^sinn f"), "sinn-fein"),
    (re.compile(r"^social democratic (and|&) labour|^sdlp$"), "social-democratic-and-labour-party"),
    (re.compile(r"^ulster unionist|^uup$"), "uup"),
    (re.compile(r"^alliance"), "alliance"),
    (re.compile(r"^traditional unionist"), "traditional-unionist-voice"),
    (re.compile(r"^uk unionist"), "ukup"),
    (re.compile(r"^respect"), "respect"),
    (re.compile(r"^workers party"), "workers-party"),
    (re.compile(r"^alba"), "alba"),
    (re.compile(r"^independent$"), "independent"),
    (re.compile(r"^speaker$"), "speaker"),
]
# How a runner-up is named in headings and labels ("Where the Conservatives came second"); other parties by their name
RUNNER_UP_NAME = {
    "conservative": "the Conservatives", "labour": "Labour", "liberal-democrat": "the Liberal Democrats",
    "scottish-national-party": "the SNP", "plaid-cymru": "Plaid Cymru", "green": "the Greens",
    "reform": "Reform UK (formerly the Brexit Party)", "ukip": "UKIP", "dup": "the DUP", "sinn-fein": "Sinn Féin",
    "social-democratic-and-labour-party": "the SDLP", "uup": "the UUP", "alliance": "the Alliance Party",
    "traditional-unionist-voice": "the TUV", "ukup": "the UK Unionist Party", "respect": "Respect",
    "workers-party": "the Workers Party", "alba": "Alba", "independent": "an independent candidate",
}

# UK general elections since 1945. Later ones are also read from the results, so this never goes stale.
GENERAL_ELECTIONS = [date.fromisoformat(d) for d in (
    "1945-07-05", "1950-02-23", "1951-10-25", "1955-05-26", "1959-10-08", "1964-10-15", "1966-03-31",
    "1970-06-18", "1974-02-28", "1974-10-10", "1979-05-03", "1983-06-09", "1987-06-11", "1992-04-09",
    "1997-05-01", "2001-06-07", "2005-05-05", "2010-05-06", "2015-05-07", "2017-06-08", "2019-12-12",
    "2024-07-04")]


PAUSE = 0.2           # seconds between requests: one at a time, as Parliament's services ask
BUDGET_SECONDS = 300  # per update; whatever is left is fetched at the next one


class Throttled(Exception):
    """Parliament asked us to slow down (429): stop now, keep what was fetched, carry on next update."""


def _get(client: httpx.Client, path: str, tries: int = 3, sleep=time.sleep) -> Any:
    for attempt in range(tries):
        try:
            resp = client.get(f"{API}{path}")
            if resp.status_code == 429:
                raise Throttled(path)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            body = resp.json()
            sleep(PAUSE)
            return body
        except httpx.HTTPError:
            if attempt == tries - 1:
                raise
            sleep(1 + attempt * 2)
    return None


def fetch_representations(client: httpx.Client, member_id: str, sleep=time.sleep) -> list[dict[str, Any]]:
    """The Commons seats a member has held: [{"id", "name", "start", "end"}]."""
    body = _get(client, f"/Members/{member_id}/Biography", sleep=sleep)
    reps = ((body or {}).get("value") or {}).get("representations") or []
    return [{"id": r["id"], "name": r["name"], "start": r["startDate"][:10],
             "end": (r.get("endDate") or "")[:10] or None} for r in reps if r.get("house") == 1 and r.get("id")]


def fetch_results(client: httpx.Client, constituency_id: int, sleep=time.sleep) -> list[dict[str, Any]]:
    """Every real (not notional) election result for a constituency, oldest first (without candidates: the list
    gives none; see ``fetch_runner_up``)."""
    body = _get(client, f"/Location/Constituency/{constituency_id}/ElectionResults", sleep=sleep)
    out = []
    for r in (body or {}).get("value") or []:
        if r.get("isNotional"):
            continue
        out.append({"date": r["electionDate"][:10], "title": r.get("electionTitle"),
                    "general": bool(r.get("isGeneralElection")), "majority": r.get("majority"),
                    "votes": r.get("turnout"), "electorate": r.get("electorate"), "result": r.get("result"),
                    "party": ((r.get("winningParty") or {}).get("name")), "election_id": r.get("electionId")})
    return sorted(out, key=lambda x: x["date"])


def parse_candidates(body: Any) -> list[dict[str, Any]]:
    """The candidates on one election result's page: [{"party", "votes", "rank"}] (party by name, as it stood)."""
    value = (body or {}).get("value") if isinstance(body, dict) else None
    if not isinstance(value, dict):
        return []
    return [{"party": ((c.get("party") or {}).get("name") or None), "votes": c.get("votes"), "rank": c.get("rankOrder")}
            for c in value.get("candidates") or [] if isinstance(c, dict)]


def runner_up(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The party that came second, and its votes: {"party", "votes"}, or None when that cannot be known from the
    candidates: fewer than two, votes missing (unless the result itself ranks one candidate second), a tie for first
    place the result does not settle, a tie for second place between parties, or a runner-up without a party."""
    if len(candidates) < 2:
        return None
    if any(c.get("votes") is None for c in candidates):
        second = [c for c in candidates if c.get("rank") == 2]
        if len(second) == 1 and second[0].get("party"):
            return {"party": second[0]["party"], "votes": second[0].get("votes")}
        return None
    order = sorted(candidates, key=lambda c: (-c["votes"], c.get("rank") or 999))
    if order[0]["votes"] == order[1]["votes"] and order[0].get("rank") != 1:
        return None  # level at the top, and the result does not say who won
    top = order[1]["votes"]
    second = [c for c in order[1:] if c["votes"] == top]
    parties = {c.get("party") for c in second}
    if len(parties) != 1 or None in parties:
        return None
    return {"party": second[0]["party"], "votes": top}


def fetch_runner_up(client: httpx.Client, constituency_id: int, election_id: int, sleep=time.sleep,
                    checked: str | None = None) -> dict[str, Any]:
    """The runner-up fields for one result, from its own page. ``candidates`` counts the candidates found; with none
    (Parliament's service answered empty), ``runner_up_checked`` says when, so it is asked again a week later."""
    body = _get(client, f"/Location/Constituency/{constituency_id}/ElectionResult/{election_id}", sleep=sleep)
    cands = parse_candidates(body)
    second = runner_up(cands)
    out = {"runner_up": second["party"] if second else None, "runner_up_votes": second["votes"] if second else None,
           "candidates": len(cands)}
    if not cands:
        out["runner_up_checked"] = checked or datetime.now(UTC).isoformat(timespec="seconds")
    return out


RUNNER_UP_FIELDS = ("runner_up", "runner_up_votes", "candidates", "runner_up_checked")


def needs_runner_up(result: dict[str, Any], week_ago: str) -> bool:
    """Whether a result's runner-up is still to be looked up: never asked, or asked and answered with no candidates
    more than a week ago."""
    if "runner_up" not in result:
        return True
    return result["runner_up"] is None and not result.get("candidates") and \
        result.get("runner_up_checked", "") < week_ago


def keep_runner_ups(new: list[dict[str, Any]], old: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A seat's results fetched again, keeping the runner-ups already looked up (matched by date, and by election
    where both copies know it)."""
    looked = {r["date"]: r for r in old or [] if "runner_up" in r}
    for r in new:
        was = looked.get(r["date"])
        if was is None or None not in (r.get("election_id"), was.get("election_id")) and \
                r["election_id"] != was["election_id"]:
            continue
        r.update({k: was[k] for k in RUNNER_UP_FIELDS if k in was})
    return new


def seed_runner_ups(results: dict[str, list[dict[str, Any]]], saved: dict[str, list[dict[str, Any]]]) -> int:
    """Fill runner-ups (and election ids) from the copy saved with the code, for results not yet looked up, so the
    server asks Parliament only for what that copy lacks. Returns the number filled."""
    filled = 0
    for cid, rs in results.items():
        theirs = {r["date"]: r for r in saved.get(cid) or [] if r.get("candidates")}
        for r in rs:
            s = theirs.get(r["date"])
            if s is None or "runner_up" in r:
                continue
            if r.get("election_id") is not None and s.get("election_id") not in (None, r["election_id"]):
                continue
            r.update({k: s[k] for k in ("election_id", *RUNNER_UP_FIELDS) if k in s and k != "runner_up_checked"})
            filled += 1
    return filled


def refresh_elections(cache: Path, member_ids: list[str], *, force: bool = False, client: httpx.Client | None = None,
                      budget_seconds: float = BUDGET_SECONDS, sleep=time.sleep, now=None,
                      saved: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Bring the cached seats and results up to date, a few minutes' worth per update.

    First anyone the copy has never looked up (the record grew), then, once a week, the sitting MPs and their seats
    (by-elections and general elections change those; history does not), then the runner-up of each result not yet
    looked up (taken from the copy saved with the code, ``saved``, where it has it). Requests go one at a time; on the
    first request to slow down, or when the time is up, what was fetched is saved and the next update carries on."""
    now = now or (lambda: datetime.now(UTC))
    old: dict[str, Any] = {}
    if cache.exists():
        try:
            old = json.loads(cache.read_text())
        except (OSError, ValueError):
            old = {}
    reps: dict[str, list] = dict(old.get("representations") or {})
    results: dict[str, list] = dict(old.get("results") or {})
    requested = set(old.get("requested") or [])
    # seats whose results came back empty (Parliament's service busy, say): asked again once a week
    empty_checked: dict[str, str] = dict(old.get("empty_checked") or {})
    try:
        fetched = datetime.fromisoformat(old["fetched_at"])
    except (KeyError, TypeError, ValueError):
        fetched = None
    cycle = old.get("cycle")  # the weekly refresh in progress: {"members": [...], "seats": [...]} still to do
    if cycle is None and (force or fetched is None or now() - fetched >= REFRESH):
        sitting = sorted(m for m, rs in reps.items() if m in set(member_ids) and any(r.get("end") is None for r in rs))
        cycle = {"members": sitting, "seats": sorted({str(r["id"]) for m in sitting for r in reps[m]
                                                       if r.get("end") is None})}
    new_members = [m for m in member_ids if m not in requested]
    stale_empty = any(not rs and empty_checked.get(c, "") < (now() - REFRESH).isoformat() for c, rs in results.items())
    seeded = seed_runner_ups(results, snapshot_results() if saved is None else saved)
    week_ago = (now() - REFRESH).isoformat()

    def lacking() -> list[str]:
        """Seats with a result whose runner-up is still to be looked up."""
        return sorted((c for c, rs in results.items() if any(needs_runner_up(r, week_ago) for r in rs)), key=int)

    if not new_members and not cycle and not stale_empty and not seeded and not lacking():
        return {"status": "fresh"}

    own = client is None
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True)
    started, done, stopped = time.monotonic(), 0, None

    def time_up() -> bool:
        return time.monotonic() - started > budget_seconds

    try:
        for m in new_members:
            if time_up():
                break
            reps[m] = fetch_representations(client, m, sleep)
            requested.add(m)
            done += 1
        for c in sorted({str(r["id"]) for rs in reps.values() for r in rs} - set(results)):  # seats never fetched
            if time_up():
                break
            results[c] = fetch_results(client, int(c), sleep)
            done += 1
        week_ago = (now() - REFRESH).isoformat()
        for c in sorted(c for c, rs in results.items() if not rs and empty_checked.get(c, "") < week_ago):
            if time_up():
                break
            results[c] = fetch_results(client, int(c), sleep)
            if not results[c]:
                empty_checked[c] = now().isoformat(timespec="seconds")
            done += 1
        while cycle and cycle["members"] and not time_up():
            m = cycle["members"][0]
            reps[m] = fetch_representations(client, m, sleep)
            cycle["members"].pop(0)
            done += 1
        while cycle and cycle["seats"] and not time_up():
            c = cycle["seats"][0]
            results[c] = keep_runner_ups(fetch_results(client, int(c), sleep), results.get(c, []))
            cycle["seats"].pop(0)
            done += 1
        stamp = now().isoformat(timespec="seconds")
        for c in lacking():
            if time_up():
                break
            if any(r.get("election_id") is None for r in results[c] if needs_runner_up(r, week_ago)):
                # saved before election ids were kept: the seat's list gives them
                results[c] = keep_runner_ups(fetch_results(client, int(c), sleep), results[c])
                done += 1
            for r in results[c]:
                if not needs_runner_up(r, week_ago) or time_up():
                    continue
                if r.get("election_id") is None:  # not known even now: asked again in a week
                    r.update({"runner_up": None, "runner_up_votes": None, "candidates": 0,
                              "runner_up_checked": stamp})
                    continue
                r.update(fetch_runner_up(client, int(c), int(r["election_id"]), sleep, checked=stamp))
                done += 1
    except Throttled as exc:
        stopped = f"Parliament asked us to slow down ({exc})"
    except Exception as exc:  # network trouble: keep what we have
        stopped = repr(exc)
    finally:
        if own:
            client.close()
    if done == 0 and stopped and not seeded:
        log.warning("could not refresh election results (%s); keeping the previous copy", stopped)
        return {"status": "failed", "error": stopped}
    finished_cycle = cycle is not None and not cycle["members"] and not cycle["seats"]
    remaining = (len([m for m in member_ids if m not in requested])
                 + len({str(r["id"]) for rs in reps.values() for r in rs} - set(results))
                 + (len(cycle["members"]) + len(cycle["seats"]) if cycle else 0)
                 + sum(needs_runner_up(r, week_ago) for rs in results.values() for r in rs))
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    tmp.write_text(json.dumps({
        "fetched_at": now().isoformat(timespec="seconds") if finished_cycle or fetched is None
        else old.get("fetched_at"),
        "source": SOURCE, "requested": sorted(requested), "empty_checked": empty_checked,
        "representations": {k: v for k, v in reps.items() if v}, "results": results,
        **({"cycle": cycle} if cycle and not finished_cycle else {})}, sort_keys=True))
    tmp.replace(cache)
    out = {"status": "updated" if not remaining else "partial", "requests": done, "remaining": remaining,
           "seats": len(results), "results": sum(len(v) for v in results.values()),
           "runner_ups": sum(1 for v in results.values() for r in v if r.get("runner_up"))}
    if seeded:
        out["runner_ups_from_saved_copy"] = seeded
    if stopped:
        log.info("election results: %d fetched, %d left for the next update (%s)", done, remaining, stopped)
        out["stopped"] = stopped
    return out


def seat_key(name: str) -> str:
    """'LIVERPOOL, WEST DERBY', 'Liverpool West Derby' and 'Bedfordshire Mid' / 'Mid Bedfordshire' alike."""
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower().replace("&", " and ")
    text = re.sub(r"\([^)]*\)", " ", text)  # "Richmond (Yorks)" is "RICHMOND" in the Library's file
    words = re.findall(r"[a-z]+", text)
    return " ".join(sorted(w for w in words if w != "and"))


def library_results(path: Path | None = None, by_elections: Path | None = None, by_elections_1997: Path | None = None
                    ) -> dict[tuple[str, str], dict[str, Any]]:
    """{(seat key, election date): result} from the Commons Library and Information Office files (general elections
    1997 to 2005, by-elections 1997 to 2010)."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for file, general, source in ((path or LIBRARY_CSV, True, LIBRARY_SOURCE),
                                  (by_elections or BY_ELECTIONS_CSV, False, BY_ELECTIONS_SOURCE),
                                  (by_elections_1997 or BY_ELECTIONS_1997_CSV, False, BY_ELECTIONS_1997_SOURCE)):
        try:
            with open(file, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    out[(seat_key(r["constituency"]), r["date"])] = {
                        "date": r["date"], "title": f"{r['year']} {'General Election' if general else 'By-election'}",
                        "general": general, "majority": int(r["majority"]) if r["majority"] else None,
                        "votes": int(r["votes"]) if r["votes"] else None,
                        "electorate": int(r["electorate"]) if r["electorate"] else None,
                        "result": r.get("result") or None, "party": r["winner"] if r["winner"] != "Other" else None,
                        **library_runner_up(r), "source": source}
        except OSError:
            pass
    return out


def library_runner_up(row: dict[str, str]) -> dict[str, Any]:
    """The runner-up fields from a Library or Information Office row. The Library's general election file adds smaller
    parties and independents together as "Other": when "Other" won or came second, who came second is not known."""
    party = (row.get("runner_up") or "").strip()
    known = party and "Other" not in (party, row.get("winner"))
    votes = row.get("runner_up_votes")
    return {"runner_up": party if known else None, "runner_up_votes": int(votes) if known and votes else None}


def add_library_results(representations: dict[str, list[dict[str, Any]]], results: dict[str, list[dict[str, Any]]],
                        library: dict[tuple[str, str], dict[str, Any]]) -> int:
    """Fill in the 1997, 2001 and 2005 general elections and the 1997-2010 by-elections for seats the API has no
    result for then: each election in which one of the MPs listed sat for the seat, matched by the seat's name.
    Returns the number added."""
    spans: dict[str, list[tuple[str, str, str]]] = {}
    for seats in representations.values():
        for r in seats:
            spans.setdefault(str(r["id"]), []).append((r["start"], r["end"] or "9999-12-31", r["name"]))  # end: excl.
    dates = sorted({d for _, d in library})
    added = 0
    for cid, ss in spans.items():
        have = {r["date"] for r in results.get(cid, [])}
        for d in dates:
            if d in have:
                continue
            hit = next((library.get((seat_key(name), d)) for start, end, name in ss if start <= d < end
                        and (seat_key(name), d) in library), None)
            if hit:
                results.setdefault(cid, []).append(dict(hit))
                added += 1
        if cid in results:
            results[cid].sort(key=lambda x: x["date"])
    return added


def saved_snapshot(path: Path | None = None) -> dict[str, Any]:
    """The seats and results saved with the code (see SNAPSHOT); empty if there is none."""
    try:
        return json.loads(Path(path or SNAPSHOT).read_text())
    except (OSError, ValueError):
        return {}


def snapshot_results(path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    return saved_snapshot(path).get("results") or {}


def load_elections(cache: Path, library: Path | None = None, snapshot: bool = True) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    # no copy of its own yet (Parliament's service unreachable from the server, say): the saved one serves
    with contextlib.suppress(OSError, ValueError):
        raw = json.loads(cache.read_text())
    reps, results = raw.get("representations", {}), raw.get("results", {})
    saved = saved_snapshot() if snapshot else {}
    for mid, rs in (saved.get("representations") or {}).items():  # members the server's own copy lacks
        if not reps.get(mid):
            reps[mid] = rs
    for cid, rs in (saved.get("results") or {}).items():  # seats the server's own copy lacks
        if not results.get(cid):
            results[cid] = rs
    seed_runner_ups(results, saved.get("results") or {})  # runner-ups the server has not looked up yet
    add_library_results(reps, results, library_results(library))
    return {"representations": reps, "results": results}


def commons_member_ids(people_json: Path, since: str = "1997-05-01") -> list[str]:
    """UK Parliament IDs of everyone who has sat in the Commons since ``since`` (from parlparse people.json, and the
    IDs saved for members it has none for)."""
    from hansard.reference.build import saved_parliament_ids

    data = json.loads(Path(people_json).read_text(encoding="utf-8"))
    ids = saved_parliament_ids()
    ids.update({p["id"]: str(i["identifier"]) for p in data["persons"] for i in p.get("identifiers", [])
                if i.get("scheme") == "datadotparl_id"})
    out = {ids[m["person_id"]] for m in data.get("memberships", [])
           if m.get("post_id") and m.get("person_id") in ids and (m.get("end_date") or "9999") >= since}
    return sorted(out)


# --------------------------------------------------------------------------- calculations


def majority_band(pct: float | None) -> str | None:
    if pct is None:
        return None
    return next(k for k, _, upper in MAJORITY_BANDS if pct < upper)


def general_elections(results: dict[str, list[dict[str, Any]]] | None = None) -> list[date]:
    seen = set(GENERAL_ELECTIONS)
    for rs in (results or {}).values():
        seen.update(date.fromisoformat(r["date"]) for r in rs if r.get("general"))
    return sorted(seen)


def first_term(first_elected: date | None, on: date, elections: list[date]) -> bool | None:
    """True while an MP is still in the Parliament they were first elected to (including by-election entrants)."""
    if first_elected is None:
        return None
    return not any(first_elected < g <= on for g in elections)


def election_label(result: dict[str, Any]) -> str:
    year = result["date"][:4]
    return f"{year} general election" if result.get("general") else f"{year} by-election"


def party_key(name: str | None) -> str | None:
    """The site's party id for a party named in an election result ("The Brexit Party" -> "reform"); None for
    "Other" or no name."""
    text = " ".join((name or "").split()).lower()
    if not text or text == "other":
        return None
    for pattern, pid in RESULT_PARTIES:
        if pattern.search(text):
            return pid
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).replace("'", "").replace("’", "")
    return "-".join(re.findall(r"[a-z0-9]+", re.sub(r"^the ", "", text))) or None


def runner_up_name(party_id: str | None, name: str | None = None) -> str:
    """How a runner-up is named in headings: "the Conservatives", "Reform UK (formerly the Brexit Party)"; other
    parties by their name in the result."""
    if party_id in RUNNER_UP_NAME:
        return RUNNER_UP_NAME[party_id]
    return name or party_id or "not known"


def runner_up_facts(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """The party that came second in a result, as shown with an MP: its name at that election, the site's id for it,
    its votes and the winner's majority over it. None if not known."""
    if not result or not result.get("runner_up"):
        return None
    return {"party": result["runner_up"], "party_id": party_key(result["runner_up"]),
            "votes": result.get("runner_up_votes"), "majority": result.get("majority"),
            "election": election_label(result), "date": result["date"]}
