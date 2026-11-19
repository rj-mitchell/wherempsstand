"""Titles as the pages show them: capitals written as a title, acronyms kept; long titles cut at a word."""

from hansard.wording import shorten, title_case


def test_titles_in_capitals_are_written_as_titles_keeping_acronyms():
    assert title_case("WINNIE EWING") == "Winnie Ewing"
    assert title_case("CUTS TO THE NHS IN DEVON") == "Cuts to the NHS in Devon"
    assert title_case("CONSOLIDATED FUND (NO. 2) BILL") == "Consolidated Fund (No. 2) Bill"
    assert title_case("OLDER PEOPLE'S COMMISSIONERS BILL") == "Older People's Commissioners Bill"
    assert title_case("ANTI-SOCIAL BEHAVIOUR AND THE BBC") == "Anti-Social Behaviour and the BBC"
    assert title_case("PAY FOR MPS AND GPS") == "Pay for MPs and GPs"
    assert title_case("MR SPEAKER AND ST HELENS") == "Mr Speaker and St Helens"
    assert title_case("GAZA: THE HUMANITARIAN CRISIS") == "Gaza: The Humanitarian Crisis"
    assert title_case("DEATH OF ROBERT O'SULLIVAN") == "Death of Robert O'Sullivan"


def test_titles_not_in_capitals_are_left_as_written():
    for t in ("NHS funding in Devon", "Winnie Ewing", "BBC", "Gaza"):
        assert title_case(t) == t


def test_shortening_cuts_at_a_word():
    assert shorten("Short title", 60) == "Short title"
    long = "All-Party Parliamentary Group on Something Very Long Indeed, And Even Longer Than Sixty"
    cut = shorten(long, 60)
    assert len(cut) <= 60 and cut.endswith("…") and long.startswith(cut[:-1]) and not cut[:-1].endswith(" ")
    assert long[len(cut) - 1] == " " or long[len(cut) - 1] in ",;:"  # never half a word
