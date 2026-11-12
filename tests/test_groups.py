"""Party groups and caucuses: the published evidence (with the owner's decisions), members' own declarations in
Parliament (first person only), the Parliament rule, IPSA records only when switched on, and the filter, grouping and
question reading built on them."""

from __future__ import annotations

import dataclasses
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from hansard.engine import Engine, Filters
from hansard.groups import (
    GROUPS_FILE,
    DeclarationStore,
    Groups,
    declarations_path,
    declarations_report,
    evidence_label,
    find_declarations,
    load_group_data,
    named_parliament_span,
    parliament_span,
    scan_declarations,
)
from hansard.rag.pipeline import plan_facets, plan_note
from hansard.rag.query_parse import QueryPlan, parse_offline, system_prompt
from hansard.rag.sources import group_label, speaker_label
from hansard.rank.evidence import demographic_group, evidence_summary

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from groups_from_research import convert  # noqa: E402

GROUPS = {g["id"]: g for g in load_group_data()["groups"]}
VICKERS, AHMED, CORBYN, HAYES = ("uk.org.publicwhip/person/25853", "uk.org.publicwhip/person/26483",
                                 "uk.org.publicwhip/person/10133", "uk.org.publicwhip/person/10265")


# ---- the data file and the owner's decisions


def _research() -> list[dict]:
    member = {"name_as_published": "A MP", "member_id": 4844, "match": "exact", "party_at_list_date": "Conservative",
              "matched_party": "Reform UK"}
    lst = {"url": "https://example.org/x", "archive_url": None, "publisher": "LabourList (reproducing it)",
           "date_kind": "published", "made_public_by": "the group", "kind": "signatories", "parliament": "2019-2024"}
    return [
        {"id": "socialist-campaign-group", "name": "Socialist Campaign Group", "party": "Labour", "aliases": [],
         "lists": [{**lst, "date": "2020-11-18", "title": "Signatories of the group's statement", "members": [member]},
                   {**lst, "date": "2026-09-29", "title": "Contributors to the pamphlet", "members": [member]}]},
        {"id": "conservative-environment-network", "name": "Conservative Environment Network (parliamentary caucus)",
         "party": "Conservative", "aliases": [], "lists": [
             {**lst, "kind": "members", "date": "2026-11-12", "date_kind": "accessed", "parliament": "2024-",
              "made_public_by": "the group, on its own website", "title": "Our parliamentarians", "members": [
                  member, {**member, "member_id": 1, "role_as_published": "CEN Fellow (former MP)"},
                  {**member, "member_id": None, "role_as_published": "MSP"}]}]},
        {"id": "european-research-group", "name": "European Research Group", "party": "Conservative", "aliases": [],
         "lists": [{**lst, "kind": "subscribers", "date": "2026-11-12", "title": "IPSA", "members": [
             {**member, "ipsa_claims": [{"date": "19/06/2020", "amount_claimed": "2000"},
                                        {"date": "07/02/2023", "amount_claimed": "2000"}]},
             {**member, "member_id": 2, "ipsa_claims": [{"date": "04/05/2023", "amount_claimed": "-3750"}]}]}]},
        {"id": "net-zero-scrutiny-group", "name": "Net Zero Scrutiny Group", "party": "Conservative", "aliases": [],
         "lists": [{**lst, "date": "2022-01-02", "title": "letter", "members": [member]},
                   {**lst, "date": "2023-06-28", "title": "letter coordinated by the chair", "members": [member]}]},
    ]


def test_conversion_applies_the_owners_decisions():
    data = convert(_research())
    ms = data["memberships"]
    by = {(m["group"], m["date"]) for m in ms}
    assert ("socialist-campaign-group", "2020-11-18") in by
    assert ("socialist-campaign-group", "2026-09-29") not in by, "pamphlet contributors are left out (decision 3)"
    cen = [m for m in ms if m["group"] == "conservative-environment-network"]
    assert [m["member_id"] for m in cen] == ["4844"], "Fellows and MSPs are left out (decision 5)"
    assert cen[0]["date_kind"] == "accessed" and "undated" in cen[0]["note"]
    erg = [m for m in ms if m["group"] == "european-research-group"]
    assert [(m["kind"], m["ipsa_years"]) for m in erg] == [("ipsa", "2020–2023")], "a refund alone is no payment"
    assert all(m["party_at_list_date"] == "Conservative" for m in ms), "the party on the list's date (decision 6)"
    assert not any(m["group"] == "net-zero-scrutiny-group" for m in ms), "pending and coordinated letters left out"
    pending = convert(_research(), include_pending=True)["memberships"]
    assert [m["date"] for m in pending if m["group"] == "net-zero-scrutiny-group"] == ["2022-01-02"]
    sig = next(m for m in ms if m["kind"] == "signatories")
    assert (sig["document"], sig["publisher_short"], sig["source_url"]) == ("statement", "LabourList",
                                                                              "https://example.org/x")
    assert next(g for g in data["groups"] if g["id"] == "conservative-environment-network")["name"] == \
        "Conservative Environment Network"


def test_the_committed_data_file_follows_the_decisions():
    data = json.loads(GROUPS_FILE.read_text(encoding="utf-8"))
    ms = data["memberships"]
    assert {m["kind"] for m in ms} == {"members", "signatories", "ipsa"}
    assert not any(m["group"] == "socialist-campaign-group" and m["date"] == "2026-09-29" for m in ms)
    assert not any(m["group"] == "net-zero-scrutiny-group" for m in ms)
    assert not any(m["role_as_published"] and "Fellow" in m["role_as_published"] for m in ms)
    assert any(m["group"] == "common-sense-group" and m["kind"] == "signatories" for m in ms)
    assert all(m["member_id"] and m["source_url"] and m["publisher"] for m in ms)
    assert any(m["group"] == "socialist-parliamentary-research-group" for m in ms), "kept apart from the SCG"


def test_evidence_labels_say_what_the_evidence_is():
    lgg, cen, erg = GROUPS["labour-growth-group"], GROUPS["conservative-environment-network"], \
        GROUPS["european-research-group"]
    assert evidence_label({"kind": "signatories", "date": "2024-07-30", "document": "letter",
                           "publisher_short": "LabourList"}, lgg) == \
        "Signed the Labour Growth Group's letter of 30 July 2024 (LabourList)"
    assert evidence_label({"kind": "members", "date": "2026-11-12", "publisher_short": "its website"}, cen) == \
        "Listed by the Conservative Environment Network (its website, accessed 12 November 2026)"
    ipsa = evidence_label({"kind": "ipsa", "date": "2026-11-12", "ipsa_years": "2020–2024"}, erg)
    assert ipsa == "Paid for the European Research Group's research service (IPSA records, 2020–2024)"
    assert "member" not in ipsa.lower()
    assert evidence_label({"kind": "declared", "date": "2022-02-02", "house": "commons", "sentence": "I chair it."},
                          GROUPS["tribune-group"]) == "Said in the Commons on 2 February 2022: “I chair it.”"
    assert evidence_label({"kind": "signatories", "date": "2025-09-01", "document": "founding statement",
                           "publisher_short": "its website"}, GROUPS["mainstream"]).startswith(
        "Signed Mainstream's founding statement")


def test_evidence_counts_for_its_own_parliament():
    assert parliament_span(date(2022, 2, 2)) == (date(2019, 12, 12), date(2024, 7, 4))
    assert parliament_span(date(2025, 1, 1))[0] == date(2024, 7, 4)
    assert named_parliament_span("2019-2024", date(2026, 11, 12)) == (date(2019, 12, 12), date(2024, 7, 4))
    assert named_parliament_span("2024-", date(2026, 11, 12))[0] == date(2024, 7, 4)


# ---- self-declarations: the labelled examples from the research, then our own

TRUE = [
    ("socialist-campaign-group", "2014-03-20", "labour", "chair",
     "Tony, along with my hon. Friend the Member for Bolsover (Mr Skinner), founded the Socialist Campaign Group, of "
     "which I am the chair. I apologise on behalf of my hon. Friend."),
    ("tribune-group", "2022-02-02", "labour", "chair",
     "He was an extremely generous person. I am privileged to chair the Tribune group of Labour MPs, of which Jack was "
     "an enthusiastic member. On many occasions, he would stop me."),
    ("labour-growth-group", "2024-10-22", "labour", "member",
     "My perspective is informed by my work as the chair of the all-party parliamentary group on UK food security "
     "—which the hon. Member is welcome to join—and also my role as a member of the Labour Growth Group, which I "
     "suspect she may not be willing to join."),
    ("labour-movement-for-europe", "2025-05-13", "labourco-operative", "chair",
     "Small businesses have sadly had to up sticks. I declare an interest as the chair of the Labour Movement for "
     "Europe. I am not standing here arguing to rejoin."),
    ("european-research-group", "2019-09-26", "conservative", "member",
     "I must sadly remind her that there is balance on both sides. I am a member of the European Research Group."),
    ("european-research-group", "2019-12-20", "conservative", "member",
     "They were told that they were racist. We in the ERG were told that we were fascists, Nazis and extremists."),
    ("northern-research-group", "2023-05-03", "conservative", "member",
     "The Northern Research Group of MPs, of which I am a member, collaborated with the Centre for Progressive Policy "
     "in 2021 to produce a research paper."),
    ("china-research-group", "2025-09-15", "conservative", "founder",
     "Further to that point of order, Mr Speaker, I am one of the MPs who is currently sanctioned by China, and as one "
     "of the founders of the China Research Group I am one of the MPs who was spied upon here."),
    ("conservative-environment-network", "2024-03-11", "conservative", "member",
     "As a farmer, I was very pleased that the Chancellor listened to calls from farming organisations and the "
     "Conservative Environment Network, of which I am a member, and addressed the anomalous disincentive."),
    ("conservative-environment-network", "2024-07-18", "conservative", "member",
     "I declare that I am a patron of the Conservative Animal Welfare Foundation and part of the parliamentary caucus "
     "of the Conservative Environment Network."),
    ("conservative-environment-network", "2023-05-24", "conservative", "founder member",
     "I am very proud to be a founder member of the Conservative Environment Network, a caucus that brings together "
     "over 150 Members on these Benches and in the other place."),
    ("covid-recovery-group", "2020-11-23", "conservative", "member",
     "As a member of the Covid Recovery Group, I am pleased that some of our concerns seem to have been met."),
    ("covid-recovery-group", "2020-12-14", "conservative", "member",
     "I am not here to be part of the difficult squad. I know I am in the Covid Recovery Group."),
    ("net-zero-scrutiny-group", "2022-03-15", "conservative", "chair",
     "Diverse organisations such as the Climate Change Committee and the Net Zero Scrutiny Group, which I chair, agree "
     "on one thing: gas will be part of our energy mix to 2050 and beyond."),
    # our own
    ("labour-movement-for-europe", "2024-12-17", "labourco-operative", "chair",
     "I am chair of the Labour Movement for Europe and I think there needs to be discussion about our future "
     "relationship with Europe."),
    ("common-sense-group", "2021-05-01", "conservative", "chair",
     "The Common Sense Group, which I chair, has published a collection of essays."),
    ("european-research-group", "2019-01-15", "conservative", "member",
     "I am a member of the European Research Group, as is my hon. Friend the Member for Wellingborough."),
]

FALSE = [
    # the research's labelled false positives
    ("2021-07-20", "conservative",
     "If were to open our borders completely, as it seems some of the Socialist Campaign Group members want us to do, "
     "millions of people would want to come."),
    ("1990-04-25", "labour", "I am not even especially left wing. I am not in the Campaign group or the Tribune group."),
    ("1992-01-22", "conservative",
     "I think that I accused the hon. Gentleman of being a member of the Tribune group, which was then a significant "
     "force in the Labour party."),
    ("2018-06-11", "liberal-democrat", "I believe that the Minister was a member of the European Research Group."),
    ("2022-03-14", "conservative",
     "My right hon. Friend and the One Nation group of Conservative MPs helped in the development of our policy."),
    ("2023-05-02", "labour",
     "I am sure that this has been pushed by the Common Sense Group. I consider myself to be a member of whatever "
     "common-sense group this place may offer."),
    ("2021-03-08", "conservative",
     "I put my signature to a letter from the Northern Research Group urging the Chancellor to continue providing "
     "business rates relief."),
    ("2022-11-23", "conservative",
     "I am happy to engage with him and with the Northern Research Group on the question of devolution."),
    # our own: negation, others, questions, hypotheses, quotations, irony across parties, everyday phrases
    ("2022-01-10", "conservative", "I am no longer a member of the European Research Group."),
    ("2022-01-10", "conservative", "I have never been a member of the European Research Group."),
    ("2022-01-10", "conservative", "My hon. Friend is a member of the European Research Group."),
    ("2022-01-10", "conservative", "I know that my hon. Friend, as a member of the European Research Group, will agree."),
    ("2022-01-10", "conservative", "Am I a member of the European Research Group?"),
    ("2022-01-10", "conservative", "If I were a member of the European Research Group, I would vote against it."),
    ("2022-01-10", "conservative", "He told the paper: “I am a member of the European Research Group.”"),
    ("2022-01-10", "labour", "I am a member of the European Research Group, apparently, according to the Minister."),
    ("2022-01-10", "conservative", "I declare an interest: my wife is a member of the Conservative Environment Network."),
    ("2022-01-10", "labour", "I am a member of Tribune and have written for it for years."),
    ("2022-01-10", "labour", "I am a member of a local campaign group that fights for the hospital."),
    ("2022-01-10", "conservative", "As a one nation Conservative, I believe in public services."),
    ("2022-01-10", "conservative", "I am a member of the growth group on the council."),
    ("2023-01-10", "labour", "I am a member of the Labour Growth Group."),  # before the group existed
    ("2022-01-10", "labour", "I am a member of the mainstream of my party."),
    ("2022-01-10", "conservative", "The Minister, as chair of the Net Zero Scrutiny Group, should know better."),
]


@pytest.mark.parametrize("group, on, party, role, text", TRUE)
def test_declarations_found(group, on, party, role, text):
    found = find_declarations(text, date.fromisoformat(on), party, GROUPS)
    assert [d["group"] for d in found] == [group]
    assert found[0]["role"] == role
    assert found[0]["sentence"] in " ".join(text.split()), "the sentence as said"


@pytest.mark.parametrize("on, party, text", FALSE)
def test_not_declarations(on, party, text):
    assert find_declarations(text, date.fromisoformat(on), party, GROUPS) == []


def test_precision_on_the_labelled_examples():
    hits = sum(bool(find_declarations(t, date.fromisoformat(d), p, GROUPS)) for _, d, p, _, t in TRUE)
    wrong = sum(bool(find_declarations(t, date.fromisoformat(d), p, GROUPS)) for d, p, t in FALSE)
    assert (hits, wrong) == (len(TRUE), 0)
    # a real declaration the rules miss (precision over recall): Freeman's "convening the one nation caucus"
    assert find_declarations("...convening the one nation caucus and insisting that the Conservative party needs to",
                             date(2020, 3, 11), "conservative", GROUPS) == []


# ---- who is in which group, when; IPSA only when switched on


def _data(ipsa: bool = True) -> dict:
    ms = [{"group": "labour-growth-group", "kind": "signatories", "member_id": "5213", "date": "2024-07-30",
           "parliament": "2024-", "source_url": "https://labourlist.org/x", "publisher": "LabourList",
           "publisher_short": "LabourList", "document": "letter"},
          {"group": "socialist-campaign-group", "kind": "signatories", "member_id": "185", "date": "2020-11-18",
           "parliament": "2019-2024", "source_url": "https://labourlist.org/y", "publisher": "LabourList",
           "publisher_short": "LabourList", "document": "statement"}]
    if ipsa:
        ms.append({"group": "european-research-group", "kind": "ipsa", "member_id": "4844", "date": "2026-11-12",
                   "parliament": "2024-", "source_url": "https://www.theipsa.org.uk/x", "publisher": "IPSA",
                   "ipsa_years": "2025"})
    return {"groups": list(GROUPS.values()), "memberships": ms}


def test_membership_applies_within_its_parliament(resolver):
    g = Groups(resolver.con, data=_data())
    assert g.ids_on(AHMED, date(2026, 9, 15)) == {"labour-growth-group"}
    assert g.ids_on(CORBYN, date(2022, 1, 1)) == {"socialist-campaign-group"}
    assert g.ids_on(CORBYN, date(2026, 9, 15)) == set(), "2019-24 evidence says nothing about the 2024 Parliament"
    on = g.on(AHMED, date(2026, 9, 15))
    assert on[0]["name"] == "Labour Growth Group" and on[0]["evidence"][0]["parliament"] == "2024–"
    assert on[0]["evidence"][0]["url"] == "https://labourlist.org/x"
    assert g.people(["socialist-campaign-group"], date(2025, 1, 1)) == set()
    assert g.people(["socialist-campaign-group"]) == {CORBYN}


def test_a_declaration_applies_across_its_parliament(resolver):
    decl = [{"record_id": "r1", "group_id": "tribune-group", "person_id": AHMED, "date": "2025-02-02",
             "house": "commons", "sentence": "I am privileged to chair the Tribune group.", "role": "chair",
             "url": "https://hansard.example/r1"}]
    g = Groups(resolver.con, decl, data=_data())
    assert "tribune-group" in g.ids_on(AHMED, date(2024, 9, 1)) and "tribune-group" in g.ids_on(AHMED, date(2026, 1, 1))
    assert "tribune-group" not in g.ids_on(AHMED, date(2024, 1, 1))
    tribune = next(x for x in g.on(AHMED, date(2026, 1, 1)) if x["id"] == "tribune-group")
    assert tribune["evidence"][0]["label"] == ("Said in the Commons on 2 February 2025: “I am privileged to chair the "
                                               "Tribune group.”")
    assert {o["id"] for o in g.offered()} == {"labour-growth-group", "socialist-campaign-group", "tribune-group"}


def test_ipsa_records_only_when_switched_on(resolver):
    off = Groups(resolver.con, data=_data(), show_ipsa=False)
    assert off.ids_on(VICKERS, date(2026, 9, 15)) == set()
    assert "european-research-group" not in {o["id"] for o in off.offered()}, "a group with no evidence is not offered"
    assert "european-research-group" not in off.coverage()
    on = Groups(resolver.con, data=_data(), show_ipsa=True)
    assert on.ids_on(VICKERS, date(2026, 9, 15)) == {"european-research-group"}
    assert on.on(VICKERS, date(2026, 9, 15))[0]["evidence"][0]["label"] == \
        "Paid for the European Research Group's research service (IPSA records, 2025)"
    assert on.coverage()["european-research-group"] == {"ipsa": 1}


def test_the_setting_is_off_by_default(indexed_settings):
    assert indexed_settings.section("groups").get("show_ipsa") is False
    g = Groups.for_settings(indexed_settings, Engine(indexed_settings, today=date(2026, 10, 10)).resolver.con)
    assert not g.show_ipsa and all(e["kind"] != "ipsa" for items in g.evidence.values() for e in items)


# ---- the filter, the grouping, the evidence summary


@pytest.fixture(scope="module")
def engine(indexed_settings):
    return Engine(indexed_settings, today=date(2026, 10, 10))


def test_group_filter_checks_the_parliament(engine):
    hits = engine.search("the", Filters(groups=["conservative-environment-network"]), limit=50)
    assert hits.records and all(
        "conservative-environment-network" in {g["id"] for g in r["speaker"]["party_groups"]} for r in hits.records)
    cen = hits.records[0]["speaker"]["party_groups"][0]
    assert cen["evidence"][0]["label"].startswith("Listed by the Conservative Environment Network (its website")
    # Jeremy Corbyn signed Socialist Campaign Group statements in 2020: nothing of his since 2024 counts
    scg = engine.search("the", Filters(groups=["socialist-campaign-group"], date_from=date(2024, 7, 4)), limit=50)
    assert scg.records == []


def test_grouping_by_party_group(engine):
    res = engine.search("the", Filters(groups=["conservative-environment-network", "labour-growth-group"]), limit=50,
                        diversify=True, group_by="group")
    labels = {group_label(r["speaker"], "group", ["conservative-environment-network", "labour-growth-group"])
              for r in res.records}
    assert labels <= {"Conservative Environment Network", "Labour Growth Group"} and labels
    assert group_label({"party_groups": []}, "group") == "No published evidence of a group"
    two = {"party_groups": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]}
    assert demographic_group(two, "group") == "a+b" and group_label(two, "group") == "A and B"
    assert demographic_group(two, "group", ["b"]) == "b"
    summary = evidence_summary(res.records)
    assert sum(summary["party_groups"].values()) >= len(res.records)
    assert "party groups on the published evidence" in speaker_label(res.records[0], {"group"})
    assert "party group" not in speaker_label(res.records[0])


# ---- reading questions

ALIASES = {"labour-growth-group": ["Labour Growth Group", "LGG"], "tribune-group": ["Tribune Group"],
           "socialist-campaign-group": ["Socialist Campaign Group", "SCG"], "european-research-group":
           ["European Research Group", "ERG"]}


def test_questions_about_groups():
    one = parse_offline("What have members of the Socialist Campaign Group said about welfare cuts?", {}, groups=ALIASES)
    assert one.filters.groups == ["socialist-campaign-group"] and one.group_by == "role"
    assert one.filters.parties == [] and "socialist" not in one.search_text.lower()
    both = parse_offline("How does the Labour Growth Group differ from the Tribune Group on planning?", {},
                         groups=ALIASES)
    assert sorted(both.filters.groups) == ["labour-growth-group", "tribune-group"] and both.group_by == "group"
    assert both.filters.parties == [], "a group's name is not a party filter"
    others = parse_offline("How do SCG members differ from other Labour MPs on Gaza?", {}, groups=ALIASES)
    assert others.filters.groups == [] and others.group_by == "group" and others.filters.parties == ["labour"]
    erg = parse_offline("What have ERG MPs said about the Windsor Framework?", {}, groups=ALIASES)
    assert erg.filters.groups == ["european-research-group"]
    assert parse_offline("What has the erg said about fishing?", {}, groups=ALIASES).filters.groups == [], \
        "initials only in capitals"
    assert parse_offline("What have MPs said about the mainstream media?", {},
                         groups={"mainstream": ["Mainstream"]}).filters.groups == []
    assert parse_offline("What have MPs said about the Socialist Campaign Group?", {}, groups={}).filters.groups == [], \
        "a group not offered is a topic, not a filter"


def test_plans_and_instructions_carry_groups():
    plan = QueryPlan.from_dict({"filters": {"groups": ["labour-growth-group", "Bad Id!"]}, "group_by": "group"})
    assert plan.filters.groups == ["labour-growth-group"] and plan.group_by == "group"
    assert "group" in plan_facets(plan)
    from hansard.engine import Scope

    assert "members of the Labour Growth Group on the published evidence" in plan_note(plan, Scope())
    text = system_prompt({}, [], date(2026, 11, 12), party_groups=[("labour-growth-group", "Labour Growth Group")])
    assert "labour-growth-group (Labour Growth Group)" in text and '"group"' in text


# ---- finding declarations in the record


def test_scan_finds_declarations_in_new_records(indexed_settings, tmp_path):
    s = dataclasses.replace(indexed_settings, data_dir=tmp_path / "data")  # its own groups.sqlite
    s.data_dir.mkdir(parents=True)
    for name in ("parquet", "reference"):
        (s.data_dir / name).symlink_to(indexed_settings.data_dir / name)
    out = scan_declarations(s, everything=True)
    assert out["declarations"] == 0, "the sample record has none"
    store = DeclarationStore(declarations_path(s.data_dir))
    assert store.scanned_to() is not None
    store.replace([], [{"record_id": "r9", "group_id": "tribune-group", "person_id": AHMED, "date": "2025-02-02",
                        "house": "commons", "sentence": "I chair the Tribune group.", "role": "chair", "url": None}])
    assert "I chair the Tribune group." in declarations_report(s)
    assert scan_declarations(s)["since"] < store.scanned_to().isoformat(), "corrections: the last month again"
    assert [d["record_id"] for d in store.all()] == ["r9"], "records outside the rescanned dates are kept"


def test_the_api_offers_groups_with_evidence_and_shows_it_on_member_pages(indexed_settings, tmp_path):
    from fastapi.testclient import TestClient

    from tests.test_api import make_app

    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    offered = {g["id"]: g for g in client.get("/api/meta").json()["party_groups"]}
    assert "conservative-environment-network" in offered and "european-research-group" not in offered
    assert offered["mainstream"]["named"] == "Mainstream"
    page = client.get(f"/api/people/{AHMED.rsplit('/', 1)[1]}?part=core").json()
    lgg = page["party_groups"][0]
    assert lgg["name"] == "Labour Growth Group" and lgg["evidence"][0]["parliament"] == "2024–"
    assert lgg["evidence"][0]["url"].startswith("https://")
    plan = {"search_text": "the", "filters": {"groups": ["labour-growth-group"]}}
    body = client.post("/api/ask", json={"question": "growth", "sources_only": True, "plan": plan}).text
    assert "Signed the Labour Growth Group" in body
