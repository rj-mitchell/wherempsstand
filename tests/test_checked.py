"""How a checked briefing is shown (the family's BRIEFINGS.md; wts-core 0.4.0): the display rule for quotations on
every answer a reader is sent, and the check strip's counts.

The fixtures in tests/fixtures/briefings/ are fixed copies of real saved briefings, as their public addresses gave
them on 21 November 2026: Where MPs Stand's topic briefings q3k4u3vqh8 (the cost of living, 19 of 19 quotations
found), sw3c524d23 (immigration and asylum, two quotations not found word for word) and wbhnb95p3u (local government
funding, two sentences with no source, five registered interests), and the answer of Where Countries Stand's
kGfKBI3cRLw, where the model attached Italy's citation to "in November 2025" rather than to its quotation. The
web app's tests (web/src/__tests__/checked.test.tsx) read the same files and the same counts.json, so the strip's
counts in the browser and wts-core's ``check_counts`` on the server are held to one answer."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from wts_core.checks import check_counts

from hansard.api.app import AsShown
from hansard.rag.checks import as_shown, shown_again

FIXTURES = Path(__file__).parent / "fixtures" / "briefings"
COUNTS = json.loads((FIXTURES / "counts.json").read_text())
MPS = {"q3k4u3vqh8": "q3k4u3vqh8-2026-11-21.json", "sw3c524d23": "sw3c524d23-2026-11-21.json",
       "wbhnb95p3u": "wbhnb95p3u-2026-11-21.json"}
ITALY = "“supports all initiatives that seek to impose self-limitations on the use of veto”"


def answer_of(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())["answer"]


def veto() -> dict:
    return answer_of("kGfKBI3cRLw-answer-2026-11-21.json")


def test_a_quotation_found_word_for_word_is_never_shown_as_having_no_source():
    saved = veto()
    assert saved["segments"][0]["status"] == "unsupported" and ITALY in saved["segments"][0]["text"]
    shown = as_shown(saved)
    quote = next(s for s in shown["segments"] if ITALY in s["text"])
    assert quote == {"text": ITALY, "sources": [1], "cited": [], "status": "cited", "loaded": [], "misquotes": [],
                     "beside": True}
    # the words before it keep their mark; the text, and every count the verifier made, are as they were
    assert shown["segments"][0]["status"] == "unsupported" and shown["segments"][0]["text"].endswith("Italy said it ")
    assert "".join(s["text"] for s in shown["segments"]) == "".join(s["text"] for s in saved["segments"])
    assert {k: v for k, v in shown.items() if k != "segments"} == {k: v for k, v in saved.items() if k != "segments"}
    assert not any(ITALY in s["text"] and s["status"] in ("unsupported", "invalid") for s in shown["segments"])
    # and a saved briefing's address shows it the same way, after its flags are judged again
    assert next(s for s in shown_again(saved)["segments"] if ITALY in s["text"])["status"] == "cited"


@pytest.mark.parametrize("name", [*MPS, "kGfKBI3cRLw"])
def test_the_strips_counts_are_check_counts_on_the_answer_as_shown(name):
    answer = veto() if name == "kGfKBI3cRLw" else answer_of(MPS[name])
    assert dataclasses.asdict(check_counts(as_shown(answer))) == COUNTS[name]


@pytest.mark.parametrize("name", list(MPS))
def test_the_display_rule_changes_nothing_in_briefings_with_no_quotation_beside_its_citation(name):
    answer = answer_of(MPS[name])
    assert as_shown(answer) == answer


def test_every_answer_a_reader_is_sent_is_the_answer_as_shown_and_closing_the_stream_closes_the_desks():
    closed = []

    class Desk:
        def __init__(self, events):
            self.events = iter(events)

        def __iter__(self):
            return self

        def __next__(self):
            return next(self.events)

        def close(self):
            closed.append(True)

    events = [{"type": "plan", "plan": {}}, {"type": "sources", "sources": []}, {"type": "final", "answer": veto()}]
    sent = list(AsShown(Desk(events)))
    assert sent[:2] == events[:2] and sent[2]["answer"] == as_shown(veto())
    assert events[2]["answer"] == veto()  # what the desk saved and cached is the verifier's own
    AsShown(Desk([])).close()  # a reader who leaves before the first event still closes the desk's stream
    assert closed == [True]


def test_a_saved_briefing_is_served_as_shown(indexed_settings, tmp_path):
    from .test_api import make_app

    app, _, _ = make_app(indexed_settings, tmp_path, auth=False)
    client = TestClient(app)
    saved = app.state.parts.archive.save("What has been said about the veto?", {}, [
        {"type": "plan", "plan": None}, {"type": "sources", "sources": []},
        {"type": "final", "answer": {**veto(), "rules": 2}}])
    view = client.get(f"/api/briefings/{saved.id}").json()
    assert next(s for s in view["answer"]["segments"] if ITALY in s["text"])["status"] == "cited"
    page = client.get(f"/b/{saved.id}").text
    # the link preview says the strip's counts, which count the sentences as shown
    assert ("checked against Hansard: 16 of 16 citations matched to the record, 15 of 15 quotations found word for "
            "word, 2 sentences without a source.") in page
