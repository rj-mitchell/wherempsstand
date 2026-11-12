"""The party that came second in each MP's seat: reading it, choosing it, at the time they spoke, filtering, grouping
and reading questions about it (never a judgement about which seats are "threatened")."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from hansard.engine import Engine, Filters
from hansard.rag.pipeline import plan_facets, plan_note
from hansard.rag.query_parse import QueryPlan, parse_offline, system_prompt
from hansard.rag.sources import group_label, speaker_label
from hansard.rank.evidence import demographic_group, evidence_summary
from hansard.sources.elections import (
    add_library_results,
    fetch_runner_up,
    keep_runner_ups,
    library_results,
    load_elections,
    needs_runner_up,
    parse_candidates,
    party_key,
    refresh_elections,
    runner_up,
    runner_up_facts,
    seat_key,
    seed_runner_ups,
)
from tests.conftest import BENN, CAMERON


def cand(party, votes, rank=None):
    return {"party": party, "votes": votes, "rank": rank}


# Thirsk and Malton, 2024 general election, as the Members API gives it (constituency 4359, election 422), trimmed
THIRSK_2024 = {"value": {"electionId": 422, "majority": 7550, "candidates": [
    {"name": "Luke Brownlee", "party": {"name": "The Yorkshire Party"}, "rankOrder": 6, "votes": 931},
    {"name": "Richard McLane", "party": {"name": "Green Party"}, "rankOrder": 5, "votes": 2986},
    {"name": "Steve Mason", "party": {"name": "Liberal Democrat"}, "rankOrder": 4, "votes": 5379},
    {"name": "Mark Robinson", "party": {"name": "Reform UK"}, "rankOrder": 3, "votes": 8963},
    {"name": "Lisa Banes", "party": {"name": "Labour"}, "rankOrder": 2, "votes": 11994},
    {"name": "Kevin Hollinrake", "party": {"name": "Conservative"}, "rankOrder": 1, "votes": 19544}]}}


def test_the_runner_up_is_read_from_a_results_candidates():
    cands = parse_candidates(THIRSK_2024)
    assert len(cands) == 6 and cands[0] == {"party": "The Yorkshire Party", "votes": 931, "rank": 6}
    second = runner_up(cands)
    assert second == {"party": "Labour", "votes": 11994}
    assert 19544 - second["votes"] == THIRSK_2024["value"]["majority"], "the majority is the margin over the runner-up"
    assert parse_candidates({"value": []}) == [] and parse_candidates(None) == [] and parse_candidates({}) == []


def test_ties_missing_votes_and_single_candidates_leave_the_runner_up_unknown():
    assert runner_up([cand("Labour", 100)]) is None, "one candidate: nobody came second"
    assert runner_up([]) is None
    # a tie for second place between two parties: neither came second alone
    assert runner_up([cand("Labour", 300), cand("Conservative", 100), cand("Reform UK", 100)]) is None
    # level at the top: the result's ranking says who won (by lot); without it, unknown
    assert runner_up([cand("Labour", 100, 2), cand("Conservative", 100, 1)]) == {"party": "Labour", "votes": 100}
    assert runner_up([cand("Labour", 100), cand("Conservative", 100)]) is None
    # votes missing: only the result's own ranking can say
    assert runner_up([cand("Labour", None, 1), cand("Green Party", None, 2)]) == {"party": "Green Party", "votes": None}
    assert runner_up([cand("Labour", 500), cand("Green Party", None)]) is None
    assert runner_up([cand("Labour", 500), cand(None, 300)]) is None, "a runner-up with no party is not guessed"


def test_the_speakers_seat_has_a_runner_up_like_any_other():
    # Chorley, 2019: the Speaker stood as the Speaker; the main parties did not stand against him
    cands = [cand("Speaker", 26831, 1), cand("Independent", 9439, 2), cand("Green Party", 3600, 3)]
    assert runner_up(cands) == {"party": "Independent", "votes": 9439}
    assert party_key("Independent") == "independent"


def test_party_names_become_the_sites_party_ids():
    assert party_key("Reform UK") == party_key("The Brexit Party") == party_key("Brexit Party") == "reform"
    assert party_key("Labour") == party_key("Labour and Co-operative") == "labour"
    assert party_key("Conservative") == "conservative" and party_key("Liberal Democrat") == "liberal-democrat"
    assert party_key("Scottish National Party") == "scottish-national-party"
    assert party_key("Green Party") == party_key("Green") == party_key("Scottish Green Party") == "green"
    assert party_key("Social Democratic & Labour Party") == party_key("Social Democratic and Labour Party") == \
        "social-democratic-and-labour-party"
    assert party_key("Sinn Féin") == "sinn-fein" and party_key("UK Independence Party") == "ukip"
    assert party_key("Democratic Unionist Party") == "dup" and party_key("Ulster Unionist Party") == "uup"
    assert party_key("The Yorkshire Party") == "yorkshire-party" and party_key("British National Party") == \
        "british-national-party"
    assert party_key("Blaenau Gwent People's Voice") == "blaenau-gwent-peoples-voice"  # as parlparse has it
    assert party_key("People Before Profit Alliance") == "people-before-profit-alliance"
    assert party_key("Other") is None and party_key(None) is None and party_key("") is None


def test_runner_up_facts_name_the_party_as_it_stood_then():
    result = {"date": "2019-12-12", "general": True, "majority": 3571, "runner_up": "The Brexit Party",
              "runner_up_votes": 11233}
    assert runner_up_facts(result) == {"party": "The Brexit Party", "party_id": "reform", "votes": 11233,
                                       "majority": 3571, "election": "2019 general election", "date": "2019-12-12"}
    assert runner_up_facts({**result, "runner_up": None}) is None and runner_up_facts(None) is None


def test_library_files_give_the_runner_up_except_where_other_parties_are_added_together():
    library = library_results()
    mitcham = library[(seat_key("Mitcham and Morden"), "1997-05-01")]
    assert (mitcham["runner_up"], mitcham["runner_up_votes"], mitcham["majority"]) == ("Conservative", 14243, 13741)
    west_derby = library[(seat_key("Liverpool West Derby"), "1997-05-01")]
    assert west_derby["runner_up"] is None and west_derby["majority"] is None, "'Other' came second: not known"
    tatton = library[(seat_key("Tatton"), "1997-05-01")]
    assert tatton["runner_up"] is None, "won by an independent counted in 'Other': who came second is not known"
    # by-elections: the Information Office's and the Library's papers name the runner-up
    leeds = library[(seat_key("Leeds Central"), "1999-06-10")]
    assert (leeds["runner_up"], leeds["runner_up_votes"]) == ("Liberal Democrat", 4068)
    assert library[(seat_key("Brent East"), "2003-09-18")]["runner_up"] == "Labour"
    reps = {"193": [{"id": 1986, "name": "Mitcham and Morden", "start": "1997-05-01", "end": "2010-05-06"}]}
    results: dict = {}
    add_library_results(reps, results, library)
    assert all("runner_up" in r for r in results["1986"])


def test_runner_ups_already_found_are_kept_and_seeded_never_fetched_twice():
    old = [{"date": "2019-12-12", "election_id": 397, "runner_up": "Labour", "runner_up_votes": 10, "candidates": 4}]
    new = [{"date": "2019-12-12", "election_id": 397, "majority": 5}, {"date": "2024-07-04", "election_id": 422}]
    kept = keep_runner_ups(new, old)
    assert kept[0]["runner_up"] == "Labour" and kept[0]["majority"] == 5 and "runner_up" not in kept[1]
    assert "runner_up" not in keep_runner_ups([{"date": "2019-12-12", "election_id": 1}], old)[0], "another election"
    # looked up before election ids were kept: matched by date
    assert keep_runner_ups([{"date": "2019-12-12", "election_id": 397}],
                           [{"date": "2019-12-12", "runner_up": "Labour", "candidates": 3}])[0]["runner_up"] == "Labour"
    results = {"7": [{"date": "2019-12-12", "majority": 5}, {"date": "2024-07-04", "majority": 6}]}
    saved = {"7": [{"date": "2019-12-12", "election_id": 397, "runner_up": "Reform UK", "runner_up_votes": 9,
                    "candidates": 5},
                   {"date": "2024-07-04", "election_id": 422, "runner_up": None, "candidates": 0,
                    "runner_up_checked": "2026-01-01T00:00:00+00:00"}]}  # the saved copy never got this one
    assert seed_runner_ups(results, saved) == 1
    assert results["7"][0]["runner_up"] == "Reform UK" and results["7"][0]["election_id"] == 397
    assert "runner_up" not in results["7"][1]
    week_ago = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    assert not needs_runner_up(results["7"][0], week_ago) and needs_runner_up(results["7"][1], week_ago)
    assert not needs_runner_up({"runner_up": None, "candidates": 1}, week_ago), "one candidate: final"
    assert needs_runner_up({"runner_up": None, "candidates": 0, "runner_up_checked": "2020-01-01"}, week_ago)


def _api(calls: list[str], candidates=None, with_ids: bool = True):
    """A stand-in Members API: one member, one seat (7), results in 2019 and 2024."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if "Biography" in path:
            return httpx.Response(200, json={"value": {"representations": [
                {"house": 1, "id": 7, "name": "Seat", "startDate": "2019-12-12T00:00:00", "endDate": None}]}})
        if "/ElectionResult/" in path:
            eid = int(path.split("/")[-1])
            return httpx.Response(200, json={"value": {"electionId": eid, "candidates": candidates if candidates
                                                       is not None else [
                {"party": {"name": "Labour"}, "votes": 300, "rankOrder": 1},
                {"party": {"name": "The Brexit Party" if eid == 397 else "Reform UK"}, "votes": 200, "rankOrder": 2},
                {"party": {"name": "Conservative"}, "votes": 100, "rankOrder": 3}]}})
        return httpx.Response(200, json={"value": [
            {"electionDate": f"{d}T00:00:00", "isGeneralElection": True, "isNotional": False, "majority": 100,
             "turnout": 600, "result": "Lab Hold", "winningParty": {"name": "Labour"},
             **({"electionId": eid} if with_ids else {})} for d, eid in (("2019-12-12", 397), ("2024-07-04", 422))]})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_refresh_looks_up_each_runner_up_once(tmp_path):
    cache, calls = tmp_path / "elections.json", []
    out = refresh_elections(cache, ["1"], client=_api(calls), sleep=lambda s: None, saved={})
    assert out["status"] == "updated" and out["runner_ups"] == 2
    assert calls[-2:] == ["/api/Location/Constituency/7/ElectionResult/397",
                          "/api/Location/Constituency/7/ElectionResult/422"]
    data = load_elections(cache, snapshot=False)
    assert [(r["runner_up"], r["runner_up_votes"]) for r in data["results"]["7"]] == [
        ("The Brexit Party", 200), ("Reform UK", 200)]
    calls.clear()
    assert refresh_elections(cache, ["1"], client=_api(calls), sleep=lambda s: None, saved={})["status"] == "fresh"
    # the weekly refresh lists the seat again but keeps the runner-ups: no request for them
    later = datetime.now(UTC) + timedelta(days=8)
    refresh_elections(cache, ["1"], client=_api(calls), sleep=lambda s: None, saved={}, now=lambda: later)
    assert not any("/ElectionResult/" in c for c in calls)
    assert load_elections(cache, snapshot=False)["results"]["7"][1]["runner_up"] == "Reform UK"


def test_refresh_takes_runner_ups_from_the_saved_copy_and_lists_seats_saved_without_election_ids(tmp_path):
    cache, calls = tmp_path / "elections.json", []
    refresh_elections(cache, ["1"], client=_api(calls, with_ids=False), sleep=lambda s: None, saved={})
    assert not any("/ElectionResult/" in c for c in calls), "no election id: nothing to ask for"
    calls.clear()
    # the saved copy has the 2019 runner-up: only 2024's is asked for, after listing the seat for its election id
    saved = {"7": [{"date": "2019-12-12", "election_id": 397, "runner_up": "The Brexit Party", "candidates": 3}]}
    import json
    raw = json.loads(cache.read_text())
    for r in raw["results"]["7"]:
        for k in ("runner_up", "runner_up_votes", "candidates", "runner_up_checked"):
            r.pop(k, None)
    cache.write_text(json.dumps(raw))
    out = refresh_elections(cache, ["1"], client=_api(calls), sleep=lambda s: None, saved=saved)
    assert out["runner_ups_from_saved_copy"] == 1
    assert calls == ["/api/Location/Constituency/7/ElectionResults", "/api/Location/Constituency/7/ElectionResult/422"]


def test_a_result_with_no_candidates_is_asked_again_a_week_later(tmp_path):
    cache, calls = tmp_path / "elections.json", []
    refresh_elections(cache, ["1"], client=_api(calls, candidates=[]), sleep=lambda s: None, saved={})
    calls.clear()
    assert refresh_elections(cache, ["1"], client=_api(calls), sleep=lambda s: None, saved={})["status"] == "fresh"
    later = datetime.now(UTC) + timedelta(days=8)
    refresh_elections(cache, ["1"], client=_api(calls), sleep=lambda s: None, saved={}, now=lambda: later)
    assert sum("/ElectionResult/" in c for c in calls) == 2
    assert fetch_runner_up(_api([], candidates=[{"party": {"name": "Labour"}, "votes": 9}]), 7, 422)["candidates"] == 1


# ---- MPs on the day, the filter and the grouping (fixture members, real results)


def test_the_runner_up_is_from_the_last_election_before_they_spoke(resolver):
    cameron = resolver.profile(CAMERON, date(2005, 6, 1)).to_dict()  # Witney, 2005 (Library file)
    assert cameron["runner_up"] == "liberal-democrat" and cameron["runner_up_then"] == {
        "party": "Liberal Democrat", "party_id": "liberal-democrat", "votes": 12415, "majority": 14156,
        "election": "2005 general election", "date": "2005-05-05"}
    assert resolver.profile(CAMERON, date(2004, 6, 1)).to_dict()["runner_up"] == "labour"  # 2001: Labour second
    benn = resolver.profile(BENN, date(2000, 1, 1)).to_dict()  # Leeds Central, after the 1999 by-election
    assert benn["runner_up_then"]["election"] == "1999 by-election" and benn["runner_up"] == "liberal-democrat"
    benn_2003 = resolver.profile(BENN, date(2003, 1, 1)).to_dict()
    assert benn_2003["runner_up_then"]["election"] == "2001 general election"
    lord = resolver.profile(CAMERON, date(2024, 2, 1)).to_dict()
    assert "runner_up" not in lord and "runner_up_then" not in lord  # a peer: no seat facts


def test_runner_up_is_not_known_rather_than_taken_from_an_older_election(resolver):
    facts = resolver.seat_facts(BENN, date(2000, 1, 1))
    idx = resolver._seat_index()
    seat = next(r for r in idx["reps"][BENN] if r[0] <= date(2000, 1, 1) <= r[1])[2]
    saved = idx["results"][seat]
    try:
        idx["results"][seat] = [dict(r, runner_up=None) if r["date"] == "1999-06-10" else r for r in saved]
        unknown = resolver.seat_facts(BENN, date(2000, 1, 1))
        assert unknown["runner_up"] is None and "runner_up_then" not in unknown
        assert unknown["majority_then"] == facts["majority_then"]
    finally:
        idx["results"][seat] = saved


def test_labels_and_groups_say_who_came_second_only_when_asked(resolver):
    sp = resolver.profile(BENN, date(2000, 1, 1)).to_dict()
    rec = {"speaker": sp}
    assert "second" not in speaker_label(rec)
    assert "second at the 1999 by-election: Liberal Democrat, majority over them 2,293" in \
        speaker_label(rec, {"runner_up"})
    assert group_label(sp, "runner_up") == "Where the Liberal Democrats came second"
    assert demographic_group(sp, "runner_up") == "liberal-democrat"
    reform = {**sp, "runner_up": "reform", "runner_up_then": {"party": "The Brexit Party", "party_id": "reform"}}
    assert group_label(reform, "runner_up") == "Where Reform UK (formerly the Brexit Party) came second"
    assert group_label({**reform, "runner_up_then": {"party": "Reform UK"}}, "runner_up") == \
        group_label(reform, "runner_up"), "one heading for one party, whatever it was called at the time"
    assert group_label({"house": "commons", "runner_up": "independent"}, "runner_up") == \
        "Where an independent candidate came second"
    assert group_label({"house": "lords"}, "runner_up") == "Runner-up not known"
    summary = evidence_summary([{"record_id": "a", "date": "2000-01-01", "speaker": sp},
                                {"record_id": "b", "date": "2000-01-01", "speaker": {**sp, "runner_up": None,
                                                                                     "runner_up_then": None}}])
    assert summary["runner_ups"] == {"The Liberal Democrats": 1, "unknown": 1}


@pytest.fixture(scope="module")
def engine(indexed_settings):
    return Engine(indexed_settings, today=date(2026, 10, 10))


def test_runner_up_filter_checks_the_day(engine):
    everyone = engine.search("the", Filters(houses=["commons"]), limit=200)
    seconds = {r["speaker"].get("runner_up") for r in everyone.records if r.get("speaker")}
    known = sorted(p for p in seconds if p)
    assert known, "the fixture has MPs with a known runner-up"
    party = known[0]
    hits = engine.search("the", Filters(runner_up_parties=[party]), limit=200)
    assert hits.records and all(r["speaker"]["runner_up"] == party for r in hits.records)
    assert all(r["speaker"]["house"] == "commons" for r in hits.records)
    none = engine.search("the", Filters(runner_up_parties=["no-such-party"]), limit=200)
    assert none.records == []


# ---- reading questions


@pytest.mark.parametrize("question, parties, runner_up_parties, group_by", [
    ("What have Labour MPs in seats where Reform came second said about immigration?", ["labour"], ["reform"], "role"),
    ("What do MPs in Reform-facing seats say about the two-child limit?", [], ["reform"], "role"),
    ("How have Labour MPs in seats threatened by Reform UK talked about welfare?", ["labour"], ["reform"], "role"),
    ("What have Conservative MPs where the Lib Dems are the main challengers said about sewage?",
     ["conservative"], ["liberal-democrat"], "role"),
    ("What have MPs in seats where the Tories came second said about housing?", [], ["conservative"], "role"),
    ("Do Labour MPs in seats threatened by Reform have a different opinion on cuts than Labour MPs in seats "
     "threatened by others?", ["labour"], [], "runner_up"),
    ("Compare Labour MPs where Reform came second with those where others came second on welfare cuts",
     ["labour"], [], "runner_up"),
    ("How do MPs differ by runner-up on housing?", [], [], "runner_up"),
    ("What have Reform UK MPs said about immigration?", ["reform"], [], "role"),
])
def test_offline_rules_read_who_came_second(question, parties, runner_up_parties, group_by):
    plan = parse_offline(question, {})
    assert plan.filters.parties == parties
    assert plan.filters.runner_up_parties == runner_up_parties
    assert plan.group_by == group_by
    assert plan.filters.majority_bands == [], "who came second says nothing about the size of the majority"
    if runner_up_parties or group_by == "runner_up":
        assert plan.filters.houses == ["commons"]
        assert not any(w in plan.search_text.lower() for w in ("reform", "threat", "second", "facing"))


def test_marginality_is_added_only_when_asked():
    plan = parse_offline("How have MPs in marginal seats where Reform UK came second talked about net zero?", {})
    assert plan.filters.majority_bands == ["marginal"] and plan.filters.runner_up_parties == ["reform"]
    assert plan.search_text == "talked net zero"


def test_plans_carry_the_filter_and_state_it_as_a_fact():
    plan = QueryPlan.from_dict({"filters": {"runner_up_parties": ["reform", "Not A Party!", "labour"]},
                                "group_by": "runner_up"})
    assert plan.filters.runner_up_parties == ["reform", "labour"] and plan.group_by == "runner_up"
    assert plan.to_dict()["filters"]["runner_up_parties"] == ["reform", "labour"]
    note = plan_note(plan, __import__("hansard.engine", fromlist=["Scope"]).Scope())
    assert "MPs in seats where Reform UK (formerly the Brexit Party) or Labour came second at the most recent " \
           "election before the day they spoke" in note
    assert not any(w in note.lower() for w in ("threat", "risk", "target"))
    assert "runner_up" in plan_facets(plan)


def test_the_models_instructions_map_threat_wording_to_the_fact():
    text = system_prompt({}, [("reform", "Reform UK"), ("labour", "Labour")], date(2026, 11, 12))
    assert "runner_up_parties" in text and '"seats threatened by Reform"' in text
    assert "never add majority_bands unless" in text and "never use polls or projections" in text
    assert '"runner_up" if it compares MPs by which party came second' in text
