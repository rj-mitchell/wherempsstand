"""Party groups and caucuses (the Socialist Campaign Group, the European Research Group, the Labour Growth Group and
the like): who belongs, on published evidence only, with the evidence shown every time.

A member counts as in a group when there is published evidence of it, or when they said so themselves in Parliament:

* ``members``: on a list the group published of its members (the Conservative Environment Network's own page);
* ``signatories``: they signed a letter or statement the group published with its signatories' names;
* ``ipsa``: IPSA's published business costs show their office paying for the group's pooled research service. That is
  a subscription, not membership (IPSA says so), and is shown only as what it is, only when ``[groups] show_ipsa`` is
  on (off until the charity has legal advice on the basis for showing it);
* ``declared``: they said in the Commons or the Lords that they are a member, the chair, a founder... (first person
  only, found by :func:`find_declarations`).

The lists, with the owner's decisions about which to use, are in data_static/groups/groups.json (made by
scripts/groups_from_research.py). Declarations are found in the record as it is processed (``hansard groups-scan``,
and each update) and kept in the data folder (groups.sqlite).

**The time rule.** Evidence counts for the Parliament it belongs to: a list or letter of the 2019-24 Parliament for
what members said in that Parliament, one from after the 2024 general election for what they have said since; a
declaration in Parliament for the whole Parliament in which it was made. Groups change between Parliaments, so
nothing is carried from one to the next. Most groups publish no lists; no evidence is not evidence of not belonging.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from hansard.config import STATIC_DIR
from hansard.sources.elections import GENERAL_ELECTIONS

log = logging.getLogger(__name__)

GROUPS_FILE = STATIC_DIR / "groups" / "groups.json"
END = date(9999, 12, 31)
KINDS = ("members", "signatories", "ipsa", "declared")
KIND_LABEL = {"members": "listed by the group", "signatories": "signed the group's letter or statement",
              "ipsa": "paid for the group's research service (IPSA records)", "declared": "said so in Parliament"}
# the parties whose members a group's self-declarations are accepted from (independents too: members who lost or gave
# up the whip often stay in their group)
PARTY_IDS = {"Labour": {"labour", "labourco-operative", "independent-labour"},
             "Conservative": {"conservative", "independent-conservative", "conservative-independent"},
             "Liberal Democrat": {"liberal-democrat", "independent-liberal-democrat"}}
NON_PARTY = {None, "", "independent", "non-affiliated", "none", "non-attached"}


def _fmt(d: date | str) -> str:
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return f"{d.day} {d.strftime('%B %Y')}"


# --------------------------------------------------------------------------- Parliaments


def parliament_span(on: date) -> tuple[date, date]:
    """The Parliament a date falls in, from one general election to the next (the end is the next election day)."""
    start, end = date(1900, 1, 1), END
    for g in GENERAL_ELECTIONS:
        if g <= on:
            start = g
        elif end == END:
            end = g
    return start, end


def named_parliament_span(name: str, fallback: date) -> tuple[date, date]:
    """'2019-2024' -> the Parliament elected in December 2019; '2024-' -> the one elected in July 2024."""
    m = re.match(r"(\d{4})", name or "")
    if not m:
        return parliament_span(fallback)
    year = int(m.group(1))
    starts = [g for g in GENERAL_ELECTIONS if g.year == year]
    return parliament_span(starts[-1]) if starts else parliament_span(fallback)


def parliament_label(start: date, end: date) -> str:
    """'2019–24', '2024–'."""
    return f"{start.year}–" + ("" if end == END else f"{end.year % 100:02d}")


# --------------------------------------------------------------------------- the published lists


def load_group_data(path: Path | None = None) -> dict[str, Any]:
    try:
        return json.loads(Path(path or GROUPS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"groups": [], "memberships": []}


def possessive(group: dict[str, Any]) -> str:
    """"the Labour Growth Group's", "Mainstream's"."""
    return ("the " if group.get("the", True) else "") + group["name"] + "'s"


def named(group: dict[str, Any]) -> str:
    return ("the " if group.get("the", True) else "") + group["name"]


def evidence_label(ev: dict[str, Any], group: dict[str, Any]) -> str:
    """One line saying what the evidence is, in the site's voice, e.g. "Signed the Labour Growth Group's letter of
    30 July 2024 (LabourList)". IPSA records are never called membership."""
    kind = ev["kind"]
    if kind == "signatories":
        return f"Signed {possessive(group)} {ev.get('document') or 'statement'} of {_fmt(ev['date'])} " \
               f"({ev.get('publisher_short') or ev.get('publisher')})"
    if kind == "members":
        return f"Listed by {named(group)} ({ev.get('publisher_short') or ev.get('publisher')}, accessed " \
               f"{_fmt(ev['date'])})"
    if kind == "ipsa":
        return f"Paid for {possessive(group)} research service (IPSA records, {ev.get('ipsa_years') or ''})"
    house = {"lords": "Lords", "commons": "Commons"}.get(ev.get("house") or "", "Commons")
    return f"Said in the {house} on {_fmt(ev['date'])}: “{ev.get('sentence')}”"


# --------------------------------------------------------------------------- finding self-declarations

_ROLE = (r"(?:(?:proud|founding|long-standing|active|paid-up|keen|enthusiastic|current|very proud)\s+)*"
         r"(?P<role>member|chair|chairman|chairwoman|co-chair|co-chairman|vice-chair|vice-chairman|deputy chair|"
         r"convenor|convener|co-convenor|founder|co-founder|secretary|treasurer|officer|leader|co-leader|"
         r"one of the founders|one of the founding members|founder member|founding member)s?")
_ARTICLE = r"(?:(?:a|an|the|one of the)\s+)?"
_CAUCUS = r"(?:(?:the\s+)?parliamentary\s+caucus\s+of\s+)?(?:the\s+)?"
# words that, between the speaker's "I" and the group's name, make the sentence about something other than their own
# membership: negation, hypotheses, other people
_NOT = re.compile(r"\b(not|never|no longer|nor|neither|if|were I|would|wish|accused|alleged|claimed|suggested|"
                  r"described|called|wife|husband|partner|son|daughter|brother|sister|colleague|friend|he|she|they|"
                  r"his|her|their|Minister|Gentleman|Lady|Member|Members|Friend)\b")
_OTHERS_BEFORE = re.compile(r"\b(Minister|Gentleman|Lady|Member|Friend|he|she|they|colleague|Government|Lord|"
                            r"Baroness|noble|Secretary of State)\b")
_ABBREV = re.compile(r"\b(hon|Hon|Mr|Mrs|Ms|Dr|St|No|Rt|rt|Prof|Sir|Gen|Col|Capt|Lt|vs|etc)\.")

# How each group is named in the record, for finding declarations. Names flagged as ambiguous are left out, apart from
# the forms that are not: case matters where the name is also a common phrase ("common sense group", "Labour
# together"); some names are used only while the group existed under that name.
_DETECT: dict[str, dict[str, Any]] = {
    "socialist-campaign-group": {"names": [r"Socialist Campaign Group(?: of (?:Labour )?MPs)?",
                                           r"Campaign Group of Labour MPs"]},
    "socialist-parliamentary-research-group": {"names": [r"Socialist Parliamentary Research Group"]},
    "tribune-group": {"names": [r"Tribune [Gg]roup(?: of (?:Labour )?MPs)?"], "case": True},
    "labour-growth-group": {"names": [r"Labour Growth Group"], "from": date(2024, 7, 4)},
    "blue-labour": {"names": [r"Blue Labour caucus"], "from": date(2025, 1, 1)},
    "red-wall-caucus": {"names": [r"(?:Labour )?Red Wall [Cc]aucus"], "case": True, "from": date(2024, 7, 4)},
    "mainstream": {"names": [r"Labour Mainstream", r"Mainstream Labour", r"(?<=of )Mainstream(?! [a-z])"],
                   "case": True, "from": date(2025, 9, 1)},
    "labour-first": {"names": [r"Labour First(?! [a-z])"], "case": True},
    "progressive-britain": {"names": [r"Progressive Britain"], "case": True},
    "labour-to-win": {"names": [r"Labour to Win"], "case": True},
    "labour-together": {"names": [r"Labour Together"], "case": True},
    "labour-movement-for-europe": {"names": [r"Labour Movement for Europe"]},
    "european-research-group": {"names": [r"European Research Group", r"(?-i:ERG)"]},
    "one-nation-conservatives": {"names": [r"One Nation (?:[Cc]aucus|[Gg]roup(?: of Conservative MPs)?)",
                                           r"One Nation Conservatives"], "case": True, "from": date(2019, 3, 1)},
    "common-sense-group": {"names": [r"Common Sense Group(?: of Conservative MPs)?"], "case": True},
    "northern-research-group": {"names": [r"Northern Research Group(?: of (?:Conservative )?MPs)?"]},
    "china-research-group": {"names": [r"China Research Group"]},
    "conservative-environment-network": {"names": [r"Conservative Environment Network(?: caucus)?",
                                                   r"(?-i:CEN) caucus"]},
    "new-conservatives": {"names": [r"New Conservatives"], "case": True, "from": date(2023, 5, 1),
                          "to": date(2024, 7, 4)},
    "covid-recovery-group": {"names": [r"Covid Recovery Group"], "from": date(2020, 1, 1)},
    "net-zero-scrutiny-group": {"names": [r"Net Zero Scrutiny Group"]},
    "blue-collar-conservatives": {"names": [r"Blue Collar Conservatives"], "case": True, "from": date(2019, 1, 1)},
    "conservative-growth-group": {"names": [r"Conservative Growth Group"]},
    "popular-conservatives": {"names": [r"Popular Conservatives", r"PopCons"], "case": True,
                              "from": date(2024, 2, 1)},
    "conservative-union-research-group": {"names": [r"Conservative Union Research Group"]},
    "liberal-reform": {"names": [r"Liberal Reform(?! [a-z])"], "case": True},
    "social-liberal-forum": {"names": [r"Social Liberal Forum"]},
}


def _group_pattern(gid: str) -> str:
    spec = _DETECT[gid]
    names = "|".join(spec["names"])
    return f"(?-i:{names})" if spec.get("case") else f"(?:{names})"


def _constructions(g: str) -> list[re.Pattern[str]]:
    """First-person ways of saying one belongs to the group named by ``g`` (a pattern)."""
    which = (rf"{g}(?:\s+of\s+(?:Labour\s+|Conservative\s+)?MPs)?\s*,?\s*(?:—\s*)?(?:of which|which)\s+I\s+"
             rf"(?:am|have been)\s+(?:(?:also|very|now|still|proud to be)\s+)*{_ARTICLE}{_ROLE}")
    which_i = (rf"{g}(?:\s+of\s+(?:Labour\s+|Conservative\s+)?MPs)?\s*,?\s*(?:—\s*)?which\s+I\s+(?:now\s+|currently\s+)?"
               rf"(?P<verb>chair|co-chair|convene|lead|founded|co-founded|set up|helped (?:to )?(?:found|set up))")
    return [re.compile(p, re.I) for p in (
        # I am a member of / the chair of / very proud to be a founder member of the X
        rf"\b(?:I am|I'm|I have been|I've been|I remain)\s+(?:(?:also|still|very|proud|privileged|honoured|"
        rf"delighted|pleased|happy|now|currently|indeed|myself)\s+)*(?:to be\s+)?{_ARTICLE}{_ROLE}\s+of\s+{_CAUCUS}{g}",
        # I am privileged to chair the X; I chair the X; I helped found the X
        rf"\bI am\s+(?:\w+\s+){{0,2}}to\s+(?P<verb>chair|co-chair|convene|lead)\s+(?:the\s+)?{g}",
        rf"\bI\s+(?:now\s+|currently\s+|also\s+|proudly\s+)?(?P<verb>chair|co-chair|convene|founded|co-founded|"
        rf"helped (?:to )?(?:found|set up))\s+(?:the\s+)?{g}",
        # the X, of which I am a member; the X, which I chair
        which, which_i,
        # in my role as a member of the X; as the chair of the X, I...
        rf"\b(?:in my (?:role|capacity) as|my (?:role|work|position) as)\s+{_ARTICLE}{_ROLE}\s+of\s+{_CAUCUS}{g}",
        rf"\bas\s+{_ARTICLE}{_ROLE}\s+of\s+{_CAUCUS}{g}(?P<then>\s*,?\s*(?:I|my|we)\b)?",
        # we in the X; I am in the X; I am part of the parliamentary caucus of the X
        rf"\b(?:we|those of us)\s+(?:who are\s+)?in\s+(?:the\s+)?{g}",
        rf"\bI(?:\s+know\s+I)?\s+am\s+(?:also\s+)?(?:in|part of)\s+{_CAUCUS}{g}",
        # Lords: I declare that I am... part of the caucus of the X; I declare an interest as the chair of the X
        rf"\bI\s+(?:must\s+|should\s+|also\s+)?declare\b[^.;]{{0,200}}?\b(?:member|chair|part|founder|officer|"
        rf"convenor)\b[^.;]{{0,80}}?{g}",
    )]


_COMPILED: dict[str, tuple[re.Pattern[str], list[re.Pattern[str]]]] = {
    gid: (re.compile(_group_pattern(gid), re.I), _constructions(_group_pattern(gid))) for gid in _DETECT}
# words in any group's name, to find the few records worth reading closely (a DuckDB regular expression)
PREFILTER = (r"(?i)(research group|campaign group|tribune|growth group|blue labour|red wall|mainstream|labour first|"
             r"progressive britain|labour to win|labour together|movement for europe|\bERG\b|one nation|common sense|"
             r"environment network|CEN caucus|new conservatives|recovery group|scrutiny group|blue collar|"
             r"popular conservatives|popcons|liberal reform|liberal forum)")


def sentences(text: str) -> list[str]:
    """The sentences of a passage (not split after "hon." or "Mr.")."""
    protected = _ABBREV.sub(lambda m: m.group(0).replace(".", "\x00"), text or "")
    parts = re.split(r"(?<=[.!?])[\"”’)]?\s+(?=[\"“‘(]?[A-Z0-9])|\n+", protected)
    return [" ".join(p.replace("\x00", ".").split()) for p in parts if p.strip()]


def _role(m: re.Match[str]) -> str:
    raw = (m.groupdict().get("role") or m.groupdict().get("verb") or "member").lower()
    if raw in ("founder member", "founding member"):
        return raw
    if raw.startswith("one of the found") or raw.startswith("found") or raw.startswith("co-found") or \
            raw.startswith("helped"):
        return "founder"
    if raw in ("convene", "convener", "co-convenor"):
        return "convenor"
    if raw in ("chairman", "chairwoman", "lead", "leader"):
        return "chair"
    return raw


def _quoted(sentence: str, at: int) -> bool:
    before = sentence[:at]
    return before.count("“") > before.count("”") or before.count('"') % 2 == 1


def find_declarations(text: str, on: date | None = None, party_id: str | None = None,
                      groups: dict[str, dict[str, Any]] | None = None) -> list[dict[str, str]]:
    """The groups a speaker says they belong to in ``text``: [{"group", "sentence", "role"}], one per group.

    First person only ("I am a member of", "of which I am the chair", "we in the ERG", "I declare that I am part
    of"). Not counted: what is said of others, negations ("I am not in the Tribune group"), questions, hypotheses,
    quotations, a minister answering a group, a group's name on its own, and names that are also everyday phrases
    unless written as the group's name. ``party_id`` (the speaker's party on the day) must fit the group's party, so
    a member of another party saying it ironically is not counted. Precision over recall: some real declarations are
    missed."""
    out: dict[str, dict[str, str]] = {}
    for sentence in sentences(text):
        if sentence.rstrip().endswith("?"):
            continue
        for gid, (name, rules) in _COMPILED.items():
            if gid in out or not name.search(sentence):
                continue
            spec = _DETECT[gid]
            if on is not None and (on < spec.get("from", date.min) or on >= spec.get("to", date.max)):
                continue
            party = (groups or {}).get(gid, {}).get("party")
            if party in PARTY_IDS and party_id not in NON_PARTY and party_id not in PARTY_IDS[party]:
                continue
            for rule in rules:
                m = rule.search(sentence)
                if not m or _quoted(sentence, m.start()):
                    continue
                span = sentence[m.start():m.end()]
                if _NOT.search(span.split(" of ")[0] if " of which" not in span and " which I" not in span
                               else span[span.find("which"):]):
                    continue
                # "as a member of the X" belongs to the speaker only when the sentence is theirs: it opens with "I",
                # and nobody else is named before the "as"
                if span.lower().startswith("as ") and not m.groupdict().get("then") and (
                        not sentence.startswith("I ") or _OTHERS_BEFORE.search(sentence[:m.start()])):
                    continue
                if re.search(r"\b(if I|were I|I would|I wish|I'd)\b", sentence[:m.start()], re.I):
                    continue
                out[gid] = {"group": gid, "sentence": sentence, "role": _role(m)}
                break
    return list(out.values())


# --------------------------------------------------------------------------- declarations found in the record

SCHEMA = """
CREATE TABLE IF NOT EXISTS declarations (
    record_id   TEXT NOT NULL,
    group_id    TEXT NOT NULL,
    person_id   TEXT NOT NULL,
    date        TEXT NOT NULL,
    house       TEXT,
    sentence    TEXT NOT NULL,
    role        TEXT,
    url         TEXT,
    PRIMARY KEY (record_id, group_id)
);
CREATE INDEX IF NOT EXISTS declarations_person ON declarations(person_id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def declarations_path(data_dir: Path) -> Path:
    return Path(data_dir) / "groups.sqlite"


class DeclarationStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._con()) as con, con:
            con.executescript(SCHEMA)

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def replace(self, record_ids: list[str], rows: list[dict[str, Any]], scanned_to: date | None = None) -> None:
        """What was found in these records replaces what was found in them before."""
        with closing(self._con()) as con, con:
            con.executemany("DELETE FROM declarations WHERE record_id = ?", [(r,) for r in record_ids])
            con.executemany("INSERT OR REPLACE INTO declarations VALUES (:record_id, :group_id, :person_id, :date, "
                            ":house, :sentence, :role, :url)", rows)
            if scanned_to:
                con.execute("INSERT OR REPLACE INTO meta VALUES ('scanned_to', ?)", [scanned_to.isoformat()])

    def scanned_to(self) -> date | None:
        with closing(self._con()) as con:
            row = con.execute("SELECT value FROM meta WHERE key = 'scanned_to'").fetchone()
        return date.fromisoformat(row[0]) if row else None

    def all(self) -> list[dict[str, Any]]:
        try:
            with closing(self._con()) as con:
                return [dict(r) for r in con.execute("SELECT * FROM declarations ORDER BY date, record_id")]
        except sqlite3.Error:
            return []


def read_declarations(path: Path) -> list[dict[str, Any]]:
    """The declarations saved at ``path`` (none if there is no such file yet)."""
    if not Path(path).exists():
        return []
    try:
        with closing(sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)) as con:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute("SELECT * FROM declarations ORDER BY date, record_id")]
    except sqlite3.Error:
        return []


RESCAN_DAYS = 30  # corrected records: the last month is read again at each update


def scan_declarations(settings, everything: bool = False, today: date | None = None) -> dict[str, Any]:
    """Find self-declarations in the record: since the last scan (less a month, for corrections), or in all of it."""
    import duckdb

    from hansard.reference.profiles import ProfileResolver
    from hansard.store.parquet import attach_store

    store = DeclarationStore(declarations_path(settings.data_dir))
    last = None if everything else store.scanned_to()
    since = last - timedelta(days=RESCAN_DAYS) if last else settings.archive_start
    con = duckdb.connect()
    if "records" not in attach_store(con, settings.parquet_dir):
        return {"records": 0, "declarations": 0}
    rows = con.execute(
        """SELECT record_id, arg_max(xml_person_id, revision), arg_max(date, revision), arg_max(house, revision),
                  arg_max(text, revision), arg_max(coalesce(official_url, twfy_url), revision)
           FROM records WHERE date >= ? AND xml_person_id IS NOT NULL AND regexp_matches(text, ?)
           GROUP BY record_id""", [since, PREFILTER]).fetchall()
    scanned_to = con.execute("SELECT max(date) FROM records").fetchone()[0]
    con.close()
    groups = {g["id"]: g for g in load_group_data().get("groups", [])}
    resolver = ProfileResolver(settings.reference_dir)
    profiles = resolver.profiles([(r[0], r[1], r[2]) for r in rows])
    found = []
    for rid, _xml, on, house, text, url in rows:
        prof = profiles.get(rid)
        if prof is None or not prof.person_id:
            continue
        for d in find_declarations(text, on, prof.party_id, groups):
            found.append({"record_id": rid, "group_id": d["group"], "person_id": prof.person_id,
                          "date": on.isoformat(), "house": prof.house or house, "sentence": d["sentence"],
                          "role": d["role"], "url": url})
    # what was found before in these dates' records goes; matches found again come back
    with closing(store._con()) as c:
        old = [r[0] for r in c.execute("SELECT record_id FROM declarations WHERE date >= ?", [since.isoformat()])]
    store.replace(sorted(set(old) | {r[0] for r in rows}), found, scanned_to)
    return {"since": since.isoformat(), "records": len(rows), "declarations": len(found)}


def declarations_report(settings) -> str:
    """Every declaration found, for a person to check: who, which group, when, what they said, and where."""
    import duckdb

    from hansard.reference.profiles import attach_reference

    rows = DeclarationStore(declarations_path(settings.data_dir)).all()
    con = duckdb.connect()
    attach_reference(con, settings.reference_dir)
    names = dict(con.execute("SELECT person_id, latest_name FROM ref_persons").fetchall())
    groups = {g["id"]: g["name"] for g in load_group_data().get("groups", [])}
    lines = [f"# Self-declarations of party groups found in the record ({len(rows)})", "",
             "Each was found by rule (first person only); check that the speaker says they belong.", ""]
    for r in rows:
        lines += [f"- **{names.get(r['person_id'], r['person_id'])}**, {groups.get(r['group_id'], r['group_id'])} "
                  f"({r['role']}), {r['date']}, {r['house']}: “{r['sentence']}” {r['url'] or r['record_id']}"]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- who is in which group, when


class Groups:
    """Every member's evidence of belonging to a group, by Parliament."""

    def __init__(self, con, declarations: list[dict[str, Any]] | None = None, show_ipsa: bool = False,
                 data: dict[str, Any] | None = None):
        data = data if data is not None else load_group_data()
        self.show_ipsa = show_ipsa
        self.groups = {g["id"]: g for g in data.get("groups", [])}
        by_mnis: dict[str, str] = {}
        with contextlib.suppress(Exception):  # no reference yet
            by_mnis = {str(m): p for p, m in con.execute(
                "SELECT person_id, datadotparl_id FROM ref_persons WHERE datadotparl_id IS NOT NULL").fetchall()}
        self.evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for m in data.get("memberships", []):
            if m["kind"] == "ipsa" and not show_ipsa:
                continue
            pid = by_mnis.get(str(m["member_id"]))
            group = self.groups.get(m["group"])
            if pid is None or group is None:
                continue
            start, end = named_parliament_span(m.get("parliament") or "", date.fromisoformat(m["date"]))
            self.evidence[pid].append(self._item(m, group, start, end))
        for d in declarations or []:
            group = self.groups.get(d["group_id"])
            if group is None:
                continue
            start, end = parliament_span(date.fromisoformat(d["date"]))
            item = {"kind": "declared", "date": d["date"], "house": d.get("house"), "sentence": d["sentence"],
                    "role": d.get("role"), "record_id": d["record_id"], "url": d.get("url")}
            self.evidence[d["person_id"]].append(self._item(item, group, start, end))
        for items in self.evidence.values():
            items.sort(key=lambda e: (e["group"], e["date"]))

    @staticmethod
    def _item(m: dict[str, Any], group: dict[str, Any], start: date, end: date) -> dict[str, Any]:
        return {"group": group["id"], "kind": m["kind"], "date": m["date"], "start": start, "end": end,
                "parliament": parliament_label(start, end), "label": evidence_label(m, group),
                "url": m.get("source_url") or m.get("url"), "archive_url": m.get("archive_url"),
                "publisher": m.get("publisher"), "role": m.get("role_as_published") or m.get("role"),
                "document": m.get("document"),
                **({"sentence": m["sentence"], "record_id": m["record_id"], "house": m.get("house")}
                   if m["kind"] == "declared" else {}),
                **({"note": m["note"]} if m.get("note") else {})}

    @classmethod
    def for_settings(cls, settings, con) -> Groups:
        return cls(con, read_declarations(declarations_path(settings.data_dir)),
                   show_ipsa=bool(settings.section("groups").get("show_ipsa", False)))

    def name(self, gid: str) -> str:
        g = self.groups.get(gid)
        return g["name"] if g else gid

    def on(self, person_id: str | None, on: date) -> list[dict[str, Any]]:
        """The groups a member belongs to on a date, on the evidence for that Parliament: [{"id", "name",
        "evidence": [...]}] (the evidence without its internal dates)."""
        if not person_id:
            return []
        out: dict[str, dict[str, Any]] = {}
        for e in self.evidence.get(person_id, []):
            if e["start"] <= on < e["end"]:
                out.setdefault(e["group"], {"id": e["group"], "name": self.name(e["group"]), "evidence": []})
                out[e["group"]]["evidence"].append({k: v for k, v in e.items() if k not in ("start", "end", "group")})
        return sorted(out.values(), key=lambda g: g["name"])

    def ids_on(self, person_id: str | None, on: date) -> set[str]:
        return {e["group"] for e in self.evidence.get(person_id or "", []) if e["start"] <= on < e["end"]}

    def all_for(self, person_id: str) -> list[dict[str, Any]]:
        """Every group a member has evidence for, in any Parliament (for their page), newest evidence first."""
        out: dict[str, dict[str, Any]] = {}
        for e in sorted(self.evidence.get(person_id, []), key=lambda e: e["date"], reverse=True):
            out.setdefault(e["group"], {"id": e["group"], "name": self.name(e["group"]), "evidence": []})
            out[e["group"]]["evidence"].append({k: v for k, v in e.items() if k not in ("start", "end", "group")})
        return sorted(out.values(), key=lambda g: g["name"])

    def people(self, group_ids: list[str], date_from: date | None = None, date_to: date | None = None) -> set[str]:
        """Members with evidence of belonging to any of these groups in a Parliament overlapping the dates."""
        lo, hi = date_from or date.min, date_to or date.max
        want = set(group_ids)
        return {pid for pid, items in self.evidence.items()
                if any(e["group"] in want and e["start"] <= hi and e["end"] > lo for e in items)}

    def offered(self) -> list[dict[str, Any]]:
        """The groups there is evidence for, to choose from (a group with none is not offered)."""
        have = {e["group"] for items in self.evidence.values() for e in items}
        return sorted(({"id": g, "name": self.name(g), "named": named(self.groups[g]),
                        "party": self.groups[g].get("party")} for g in have),
                      key=lambda g: (g["party"] or "", g["name"]))

    def coverage(self) -> dict[str, dict[str, int]]:
        """Members with evidence, by group and kind of evidence."""
        out: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for pid, items in self.evidence.items():
            for e in items:
                out[e["group"]][e["kind"]].add(pid)
        return {g: {k: len(v) for k, v in kinds.items()} for g, kinds in out.items()}

    def aliases(self) -> dict[str, list[str]]:
        """Names a reader may use for each offered group in a question (the unambiguous ones, and initials that
        name only one group)."""
        initials: dict[str, list[str]] = defaultdict(list)
        for g in self.groups.values():
            for a in g.get("aliases") or []:
                if re.fullmatch(r"[A-Z]{2,5}", a["alias"]):
                    initials[a["alias"]].append(g["id"])
        out = {}
        for g in self.offered():
            group = self.groups[g["id"]]
            names = [group["name"]] + [a["alias"] for a in group.get("aliases") or [] if not a.get("ambiguous")]
            names += [i for i, ids in initials.items() if ids == [g["id"]]]
            out[g["id"]] = sorted(set(names), key=len, reverse=True)
        return out


def group_names() -> dict[str, str]:
    """Every group's name as used in a sentence, by id (from the data file)."""
    return {g["id"]: named(g) for g in load_group_data().get("groups", [])}
