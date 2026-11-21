"""Links to the sister sites: a sitting MP's councils on Where Councils Stand, from the ONS lookup and that site's own
list (kept fresh by the update, the last good copy kept), and the topic briefings that quote a member."""

from __future__ import annotations

import csv
import json
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from hansard import sisters
from hansard.config import STATIC_DIR

from .test_api import make_app
from .test_topics import (
    refresh,
    site,  # noqa: F401 - a fixture: the topic tests' site, with its topic
)

ALDERSHOT = "E14001063"
LEEDS_SOUTH = "E14001323"


@pytest.fixture()
def data(tmp_path):
    """A data folder with no copy of Councils' list yet: the one kept with the code is used."""
    return NS(data_dir=tmp_path)


def test_every_2024_seat_has_its_councils():
    with open(STATIC_DIR / "geo" / "constituencies.csv", encoding="utf-8") as fh:
        seats = {r["gss_code"] for r in csv.DictReader(fh) if r["boundary_set"] == "2024"}
    with open(sisters.OVERLAPS, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert {r["pcon_code"] for r in rows} == seats and len(seats) == 650
    assert {r["tier"] for r in rows} == {"unitary", "district", "county"}


def test_a_seat_lists_the_county_first_then_by_how_much_each_covers(data):
    found = sisters.councils_for_seat(data, ALDERSHOT)
    assert [(c["name"], c["tier"]) for c in found] == [
        ("Hampshire County Council", "county"), ("Rushmoor Borough Council", "district"),
        ("Hart District Council", "district")]
    assert found[0]["url"] == "https://wherecouncilsstand.com/councils/E10000014"
    assert sisters.councils_for_seat(data, None) == [] == sisters.councils_for_seat(data, "E14000001")


def test_barnsley_and_sheffield_are_linked_by_the_codes_councils_uses(data):
    barnsley = [c for seat in ("E14001074", "E14001075") for c in sisters.councils_for_seat(data, seat)]
    assert barnsley and all(c["gss"] == "E08000038" and c["url"].endswith("/E08000038") for c in barnsley)


def test_a_council_with_no_page_there_is_named_but_not_linked(data):
    path = sisters.councils_path(data)
    path.parent.mkdir(parents=True)
    listing = json.loads(sisters.COUNCILS_SEED.read_text())
    listing["councils"] = [c for c in listing["councils"] if c["gss"] != "E07000092"]  # Rushmoor, say hidden
    path.write_text(json.dumps(listing))
    rushmoor = next(c for c in sisters.councils_for_seat(data, ALDERSHOT) if c["gss"] == "E07000092")
    assert rushmoor == {"gss": "E07000092", "name": "Rushmoor", "tier": "district", "kind": None, "url": None}


def test_the_update_refreshes_the_list_and_keeps_the_last_good_copy(data):
    seed = json.loads(sisters.COUNCILS_SEED.read_text())
    answer = {"totals": {}, "councils": [{**c, "loaded": True} for c in seed["councils"]]}
    assert sisters.refresh_councils(data, fetch=lambda url: answer) == {"councils": len(seed["councils"])}
    path = sisters.councils_path(data)
    kept = path.read_text()

    def down(url):
        raise OSError("Where Councils Stand could not be reached")

    for fetch in (down, lambda url: {"error": "busy"}, lambda url: {"councils": answer["councils"][:150]},
                  lambda url: ["not", "a", "list"]):
        out = sisters.refresh_councils(data, fetch=fetch)
        assert "error" in out and out["kept"] and path.read_text() == kept
    assert sisters.listed_councils(data)["E10000014"]["name"] == "Hampshire County Council"


def test_the_spine_is_the_kits():
    assert sisters.spine("councils").startswith('<svg class="wts-spine wts-cloth--councils"')
    assert sisters.spine("councils").endswith("</svg>")


def test_a_sitting_mps_page_lists_their_councils(indexed_settings, tmp_path):
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False, pages=True)
    client = TestClient(app)
    core = client.get("/api/people/10669?part=core").json()
    assert core["current"]["gss_code"] == LEEDS_SOUTH
    assert [(c["name"], c["url"]) for c in core["councils"]] == [
        ("Leeds City Council", "https://wherecouncilsstand.com/councils/E08000035")]
    page = client.get("/people/10669/hilary-benn").text  # written by the server, for readers without scripts
    assert "<h2>Councils</h2>" in page and 'aria-label="On Where Councils Stand"' in page
    assert '<svg class="wts-spine wts-cloth--councils"' in page
    assert '<a href="https://wherecouncilsstand.com/councils/E08000035">Leeds City Council</a>' in page
    # a peer has no seat, and so no councils
    assert client.get("/api/people/10777?part=core").json()["councils"] == []


def test_the_topics_that_quote_a_member(site):  # noqa: F811 - the fixture
    from hansard.api.plaintext import person_text
    from hansard.people import person_number

    out = refresh(site)
    assert out[0]["outcome"].startswith("rewritten")
    client = site[3]
    detail = client.get("/api/topics/trial-by-jury").json()
    briefing = detail["briefing"]
    cited = {n for seg in briefing["answer"]["segments"] if seg["status"] == "cited" for n in seg["sources"]}
    speaker = {src["n"]: (src.get("speaker") or {}).get("person_id") for src in briefing["sources"]}
    quoted = {speaker[n] for n in cited} - {None}
    others = set(speaker.values()) - quoted - {None}
    assert quoted and others
    for pid in quoted:
        core = client.get(f"/api/people/{person_number(pid)}?part=core").json()
        assert core["quoted_in"] == [{"title": "Trial by jury", "path": "/topics/trial-by-jury",
                                      "prepared_at": detail["versions"][0]["prepared_at"]}]
        text = person_text(core, None)  # as the server writes it into the member's page
        assert "<h2>Quoted in</h2>" in text and '<a href="/topics/trial-by-jury">Trial by jury</a>, ' in text
    for pid in others:  # found for the briefing but not cited in it
        assert client.get(f"/api/people/{person_number(pid)}?part=core").json()["quoted_in"] == []
