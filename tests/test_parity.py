"""The site gives what it gave before it moved onto wts-core (the Where They Stand sites' shared package).

tests/golden/ holds what the site's own code gave on 14 November 2026, recorded by tests/golden/record.py before
anything changed. Each test here plays the same scenario (tests/golden/shared.py) on the code as it is now and
compares. Where the two differ on purpose, the difference is asserted, with the entry of wts-core's
docs/AS-BUILT.md or docs/MIGRATION.md that it comes from; nothing is skipped.
"""

from __future__ import annotations

from typing import Any

import shared

from hansard.config import REPO_ROOT, load_settings, save_local_settings

# Keys this move added to config/settings.toml, each with the value the code had as its fallback before or a
# setting of something new: the recorded settings are the same but for these.
ADDED_SETTINGS: dict[str, dict[str, Any]] = {}


def without_added(raw: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for section, values in raw.items():
        added = ADDED_SETTINGS.get(section, {})
        kept = {k: v for k, v in values.items() if k not in added} if isinstance(values, dict) else values
        if kept or section not in ADDED_SETTINGS:
            out[section] = kept
    return out


# ---- settings --------------------------------------------------------------------------------------------------


def test_settings_resolve_as_before(tmp_path):
    golden = shared.read("settings.json")
    now = shared.settings_cases(tmp_path / "settings", REPO_ROOT, load_settings)
    assert [c["name"] for c in now] == [c["name"] for c in golden["cases"]]
    for was, case in zip(golden["cases"], now, strict=True):
        gives = dict(case["gives"])
        for name in ("raw", *ADDED_SETTINGS):
            if name == "raw":
                gives["raw"] = without_added(gives["raw"])
            elif name in gives:
                gives[name] = {k: v for k, v in gives[name].items() if k not in ADDED_SETTINGS[name]}
        assert gives == was["gives"], case["name"]


def test_the_local_settings_file_is_written_byte_for_byte_as_before(tmp_path):
    assert shared.local_settings_written(tmp_path, save_local_settings) == shared.read("settings.json")["local_file"]


# ---- the checks on briefings -----------------------------------------------------------------------------------


def test_the_checks_report_each_briefing_as_before():
    """Twelve briefings as the model might write them, through every check. One difference, on purpose: each
    answer now carries ``rules``, the version of the checking rules that wrote it (wts-core docs/MIGRATION.md 1.4;
    docs/AS-BUILT.md, checks: "to_dict() writes today's keys and then rules")."""
    from wts_core.checks import RULES_VERSION

    from hansard.rag.checks import checked
    from hansard.rag.sources import Source

    sources = [Source(**s) for s in shared.SOURCES]
    golden = shared.read("checks.json")["written"]
    assert [c["name"] for c in golden] == [c["name"] for c in shared.CHECK_CASES]
    for was, case in zip(golden, shared.CHECK_CASES, strict=True):
        answer = dict(checked(case["blocks"], sources))
        assert list(answer)[-1] == "rules" and answer.pop("rules") == RULES_VERSION == 1, case["name"]
        assert answer == was["answer"], case["name"]


def test_the_wide_rule_for_notes_about_the_evidence_is_kept():
    """This site has always taken a sentence with "was supplied" or "were retrieved" for a note about the
    evidence, not a claim to check. The package has a stricter form too; narrowing is a change of its own, with an
    evaluation run before and after (wts-core docs/MIGRATION.md 1.4), and is not part of this move."""
    from wts_core.checks import EVIDENCE_NOTES_STRICT, EVIDENCE_NOTES_WIDE, Verifier

    from hansard.rag.checks import VERIFIER
    from hansard.rag.prompts import NO_RECORDS
    from hansard.rag.sources import Source

    sources = [Source(**s) for s in shared.SOURCES]
    blocks = [{"text": "Mr Benn wants jury trial kept.", "citations": [shared.cite(0, shared.BENN)]},
              {"text": " Gas was supplied to two million homes across the north of England last winter."}]
    assert [s.status for s in VERIFIER.verify(blocks, sources).segments] == ["cited", "connective"]
    strict = Verifier(no_records=NO_RECORDS, evidence_notes=EVIDENCE_NOTES_STRICT)
    assert [s.status for s in strict.verify(blocks, sources).segments] == ["cited", "unsupported"]
    assert EVIDENCE_NOTES_WIDE.pattern != EVIDENCE_NOTES_STRICT.pattern


def test_a_saved_briefing_is_shown_as_before_with_its_flags_judged_again():
    """Each saved briefing as ``/api/briefings/<id>`` serves it: one object, its points to check and its
    judgemental-word marks judged again, posts in government left out of the interests. None of these was
    stamped with the rules' version (they were saved before there was one), so each is judged again, as before."""
    from hansard.api.briefings import public_view, summary_text

    golden = shared.read("checks.json")
    for was, case in zip(golden["shown_again"], shared.SAVED_BRIEFINGS, strict=True):
        view = public_view({"id": "briefingaa", "question": shared.Q, "created_at": shared.START,
                            "records_up_to": "2026-11-12", "settings": {"mode": "summary"},
                            "events": shared.plain(case["events"])})
        assert shared.plain(view) == was["view"], case["name"]
    assert [summary_text(case["events"][-1].get("answer")) for case in shared.SAVED_BRIEFINGS] == golden["summaries"]


def test_a_briefing_written_under_todays_rules_is_shown_as_it_was_saved():
    """A briefing stamped with today's rules is left alone when it is shown again (wts-core docs/MIGRATION.md
    1.4): judging it again could only repeat, less well informed, what the verifier did when it was written."""
    from hansard.rag.checks import shown_again

    stamped = shown_again({**shared.an_answer(), "rules": 1})
    assert [s["status"] for s in stamped["segments"]] == ["cited", "unsupported", "cited", "unsupported"]
    assert stamped["unsupported_segments"] == 2 and stamped["grounded_ratio"] == 0.5
    assert stamped["loaded_words"] == 0  # a judgemental-word mark is judged by today's list every time it is shown
    unstamped = shown_again(shared.an_answer())
    assert [s["status"] for s in unstamped["segments"]] == ["cited", "connective", "cited", "unsupported"]
    assert unstamped["loaded_words"] == 0
