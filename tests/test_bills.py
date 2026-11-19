"""Bill pages: bills found in the record, their stages and votes."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hansard.bills import (
    _display,
    bill_key,
    display_name,
    parse_heading,
    sentence,
    slugify,
    stage_of,
    years_of,
)

from .test_api import make_app


def test_headings_and_names():
    assert parse_heading("Safety of Rwanda (Asylum and Immigration) Bill - Committee (1st Day)") == (
        "Safety of Rwanda (Asylum and Immigration) Bill", "Committee (1st Day)")
    assert parse_heading("SAFETY OF RWANDA (ASYLUM AND IMMIGRATION) BILL (PROGRAMME)")[1] == "(PROGRAMME)"
    assert parse_heading("Bill of Rights") is None and parse_heading("Prisons") is None
    assert bill_key("Finance (No. 2) Bill") == bill_key("Finance No. 2 Bill")
    assert bill_key("Victims and Prisoners Bill [HL]") == bill_key("Victims and Prisoners Bill")
    assert sentence("Consideration of Commons Amendments") == "Consideration of Commons amendments"
    # a motion about Public Bill Committees names no bill (it made a "Proceedings in Public Bill" page a year)
    assert parse_heading("Proceedings in Public Bill Committees etc. (Welsh and Scottish Matters)") is None
    assert parse_heading("Public Bill Committees") is None


def test_names_as_the_pages_show_them():
    """353 bills began "Orders of the Day — …", some were in capitals and some kept "[Lords]" (audit, 19 November
    2026). The page shows the bill's own name; its address is unchanged."""
    assert display_name("Orders of the Day — CONSOLIDATED FUND (APPROPRIATION) (NO. 2) BILL") == \
        "Consolidated Fund (Appropriation) (No. 2) Bill"
    assert display_name("Business without Debate — PARTNERSHIPS (PROSECUTION) (SCOTLAND) BILL [LORDS]") == \
        "Partnerships (Prosecution) (Scotland) Bill"
    assert display_name("Remaining Private Members' Bills — OLDER PEOPLE'S COMMISSIONERS BILL") == \
        "Older People's Commissioners Bill"
    assert display_name("Orders of the Day — Employment Tribunals (Representation and Assistance in "
                        "Discrimination Proceedings) Bill").startswith("Employment Tribunals (")
    assert display_name("Harbours Bill [Lords]") == "Harbours Bill"
    assert display_name("Recognition of Armenian Genocide BilL") == "Recognition of Armenian Genocide Bill"
    assert display_name("Railways Bill") == "Railways Bill" == display_name(display_name("Railways Bill"))
    # the address is made from the name before it is tidied, as it always was
    from collections import Counter

    assert slugify(_display(Counter({"ORDERS OF THE DAY — CIVIL SERVICE BILL [LORDS]": 3}))) == \
        "orders-of-the-day-civil-service-bill-lords"
    assert years_of({"first": "2025-12-09", "last": "2026-11-18"}) == "2025–26"
    assert years_of({"first": "2024-02-01", "last": "2024-03-01"}) == "2024"
    assert years_of({"first": "1999-11-01", "last": "2000-03-01"}) == "1999–2000"


def sitting(**kw):
    base = {"source": "debates", "house": "commons", "minors": [], "raw_is_presented": False, "second": False,
            "third": False, "lords_amendments": False, "commons_amendments": False}
    return {**base, **kw}


def test_stages_from_the_wording_of_motions():
    assert stage_of(sitting(second=True), "") == ["Second reading"]
    assert stage_of(sitting(minors=["[1st Allocated Day]", "Clause 2"], third=True), "") == [
        "Committee of the whole House", "Third reading"]
    assert stage_of(sitting(lords_amendments=True), "") == ["Consideration of Lords amendments"]
    assert stage_of(sitting(), "(PROGRAMME)") == ["Programme motion"]
    assert stage_of(sitting(house="lords", source="lordspages"), "Report (2nd Day) (Continued)") == ["Report (2nd day)"]
    assert stage_of(sitting(source="standing"), "") == ["Public Bill Committee"]
    assert stage_of(sitting(), "") == ["Debate"]


def test_bills_api_and_pages(indexed_settings, tmp_path):
    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    bills = client.get("/api/bills").json()["bills"]
    assert bills, "the fixtures include bill debates"
    b = bills[0]
    detail = client.get(f"/api/bills/{b['slug']}").json()
    assert detail["name"] == b["name"] and detail["sittings"]
    assert [s["date"] for s in detail["sittings"]] == sorted(s["date"] for s in detail["sittings"])
    page = client.get(f"/bills/{b['slug']}")
    assert page.status_code == 200 and b["name"] in page.text and 'name="robots"' not in page.text
    # bills of the same name are told apart by their years, in the title and the description
    assert b["years"] and f"<title>{b['name']} ({b['years']}) | Where MPs Stand</title>" in page.text
    assert f'content="The {b["name"]} ({b["years"]}) in Parliament' in page.text
    assert client.get("/api/bills/no-such-bill-2020").status_code == 404
    assert client.get("/bills/no-such-bill-2020").status_code == 404
    assert f"/bills/{b['slug']}" in client.get("/sitemap-bills.xml").text
