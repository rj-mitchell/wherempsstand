"""The site gives what it gave before it moved onto wts-core (the Where They Stand sites' shared package).

tests/golden/ holds what the site's own code gave on 14 November 2026, recorded by tests/golden/record.py before
anything changed. Each test here plays the same scenario (tests/golden/shared.py) on the code as it is now and
compares. Where the two differ on purpose, the difference is asserted, with the wts-core rule it
comes from; nothing is skipped.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
import shared

from hansard.config import REPO_ROOT, load_settings, save_local_settings

# Keys this move added to config/settings.toml, each with the value the code had as its fallback before or a
# setting of something new: the recorded settings are the same but for these.
ADDED_SETTINGS: dict[str, dict[str, Any]] = {
    # how the instructions to the model are cached: "short", as the code always did
    "answer": {"cache_instructions": "short",
               # how long a briefing waits for each side lookup (docs/EVALUATION.md, "The devolved parliaments'
               # lookup"): before, it waited however long they took
               "side_lookup_seconds": 6},
    # how alike two questions are to count as one theme in the weekly report (wts-core docs/adr/0012)
    "embedding": {"theme_similarity": 0.8},
}
# whole sections that came with the record of each briefing and the evaluation runs (wts-core docs/adr/0012)
ADDED_SECTIONS = ("logs", "alerts", "eval")
# and a table of prices for the larger model the base prices are for, so that every model the settings may name
# has prices of its own (the site now refuses to start with a model it has none for)
ADDED_MODELS = {"claude-opus-5-5": {"price_input_per_mtok": 4.0, "price_output_per_mtok": 20.0,
                                    "price_cache_read_per_mtok": 0.2}}


# Settings changed on purpose since, each with its value then and now: one visitor's new briefings a day, lowered so
# that a few visitors cannot use up the daily cap between them (docs/DECISIONS.md, 38); and what a briefing in
# progress is reserved at, raised above what 16,000 tokens of output alone can cost ($0.16) plus its input; and the
# effort the question reader gives each question, raised from low to medium after a run of the test set read 9 in 10
# questions' filters right at low (a reading costs a fiftieth of a penny); and the daily spending cap, raised by the owner
# from $10 to $50, and the monthly cap from $150 to $250, on 19 November 2026
CHANGED_SETTINGS: dict[str, dict[str, tuple[Any, Any]]] = {
    "usage": {"per_visitor_daily_questions": (40, 10), "briefing_estimate_usd": (0.15, 0.30),
              "daily_spend_cap_usd": (10.0, 50.0), "monthly_spend_cap_usd": (150.0, 250.0)},
    "answer": {"query_parse_effort": ("low", "medium")}}

# The version of the instructions the goldens were recorded with. The question reader's instructions have changed
# on purpose since (it now says when a message is plainly not a question about Parliament's record,
# docs/DECISIONS.md, 38), so the version is new, and every cached answer is written afresh, as the rule is. The
# scenarios are played with the old version, so that every other part of each key is checked as before.
INSTRUCTIONS_THEN = "4ccf1bce0bc2"


def instructions_then(monkeypatch: pytest.MonkeyPatch) -> None:
    from hansard.api import app as web

    assert web.INSTRUCTIONS_VERSION != INSTRUCTIONS_THEN
    monkeypatch.setattr(web, "INSTRUCTIONS_VERSION", INSTRUCTIONS_THEN)


def research_rule() -> str:
    """The rule the question reader was given since (docs/DECISIONS.md, 38): its whole text, as sent."""
    from datetime import date

    from hansard.rag.query_parse import system_prompt

    text = system_prompt({}, [], date(2000, 1, 1))
    return text[text.index("- research_question:"):text.index("- interpretation:")]


def strings(value: Any, change: Any) -> Any:
    """``value`` with ``change`` made to every string in it."""
    if isinstance(value, dict):
        return {k: strings(v, change) for k, v in value.items()}
    if isinstance(value, list):
        return [strings(v, change) for v in value]
    return change(value) if isinstance(value, str) else value


def without_research_rule(value: Any) -> Any:
    """What was sent, without the rule the question reader was given since."""
    rule = research_rule()
    return strings(value, lambda text: text.replace(rule, ""))


def parse_effort_then(value: Any) -> Any:
    """What was sent, with the question reader asked at the low effort it was then (CHANGED_SETTINGS: medium now).
    Every reading of a question is checked to be at medium, and only those are changed back."""
    calls = []
    for call in value["calls"]:
        if "parse" in call:
            parse = dict(call["parse"])
            assert parse["output_config"] == {"effort": "medium"}
            call = {**call, "parse": {**parse, "output_config": {"effort": "low"}}}
        calls.append(call)
    return {**value, "calls": calls}


KEY = re.compile(r"\b[0-9a-f]{64}\b")


def follow_ups_as_now(was: Any, now: Any, follow_ups: int) -> Any:
    """The golden as a follow-up is asked now (docs/DECISIONS.md, 37): the answer the browser sent with the earlier
    question is in neither request to the model, and the follow-up's cache key, which held it, is new. Each of
    those keys, and only those, is the one now in its place."""
    was = strings(was, lambda text: re.sub(r"\nA \(summary\): .*?(?=\n\n)", "", re.sub(
        r"The start of the answer to the last of them:\n.*?\n\n", "", text, flags=re.S), flags=re.S))
    then_text, now_text = json.dumps(was), json.dumps(now)
    then_keys = [k for k in dict.fromkeys(KEY.findall(then_text)) if k not in now_text]
    now_keys = [k for k in dict.fromkeys(KEY.findall(now_text)) if k not in then_text]
    assert len(then_keys) == len(now_keys) == follow_ups
    for then, key in zip(then_keys, now_keys, strict=True):
        then_text = then_text.replace(then, key)
    was = json.loads(then_text)
    if was.get("rows", {}).get("cache"):  # the answer cache is listed in the order of its keys
        was["rows"]["cache"] = sorted(was["rows"]["cache"], key=lambda row: row[0])
    return was


def changed_back(section: str, values: Any) -> Any:
    """A section of the settings with each setting changed since put back to its value then."""
    if not isinstance(values, dict):
        return values
    out = dict(values)
    for key, (then, now) in CHANGED_SETTINGS.get(section, {}).items():
        if out.get(key) == now:
            out[key] = then
    return out


def without_added(section: str, values: Any) -> Any:
    """A section of the settings without what this move added to the settings file."""
    if not isinstance(values, dict):
        return values
    out = {k: v for k, v in values.items() if k not in ADDED_SETTINGS.get(section, {})}
    if section == "usage" and isinstance(out.get("models"), dict):
        out["models"] = {k: v for k, v in out["models"].items() if k not in ADDED_MODELS}
    return out


# Version 2 of the checks on briefings (wts-core 0.3.2, ``checks.RULES_VERSION``): a short uncited line that is a
# sentence of its own is a claim, to be checked, where version 1 took it for a join. Each passage of the recordings
# that this changes, every one a claim the recordings were wrong to pass; it is "unsupported" now, and was
# "connective" when they were recorded.
CLAIMS_NOW = (
    # after a citation, in the scenario of the checks "a cited claim, a lead-in and an uncited claim" (the lead-in,
    # "Hilary Benn said that ", is a join as before)
    " This was a turning point for the House.",
    # the faked model's sentence after its quotation, in every briefing of the real pipeline (wts-core's changelog
    # names it: the one passage of the sites' saved briefings that version 2 changed)
    '" The minister admitted that the organisation was at fault.',
)


def judged_then(answer: dict[str, Any], moved: list[str] | None = None) -> dict[str, Any]:
    """A checked answer as version 1 of the rules judged it: each passage of ``CLAIMS_NOW`` that is a point to
    check now put back as a join, and the counts of the answer worked out again as the package does when it judges
    a saved briefing again (``Verifier.recheck``). Each passage put back is added to ``moved``."""
    segments = answer["segments"]
    back = [s.get("status") == "unsupported" and s.get("text") in CLAIMS_NOW for s in segments]
    if not any(back):
        return answer
    if moved is not None:
        moved += [s["text"] for s, b in zip(segments, back, strict=True) if b]
    segments = [{**s, "status": "connective"} if b else s for s, b in zip(segments, back, strict=True)]
    cited = sum(len(s["text"]) for s in segments if s.get("status") == "cited")
    unsupported = sum(len(s["text"]) for s in segments if s.get("status") in ("unsupported", "invalid"))
    return {**answer, "segments": segments,
            "unsupported_segments": sum(1 for s in segments if s.get("status") == "unsupported"),
            "grounded_ratio": round(1.0 if not cited + unsupported else cited / (cited + unsupported), 3)}


def claims_then(value: Any, moved: list[str]) -> Any:
    """What was sent, saved or cached, with each checked answer in it as version 1 of the rules judged it
    (``judged_then``): in the views of saved briefings, and in the JSON of each event sent and each row written."""
    if isinstance(value, dict):
        if isinstance(value.get("segments"), list) and "unsupported_segments" in value:
            return judged_then(value, moved)
        return {k: claims_then(v, moved) for k, v in value.items()}
    if isinstance(value, list):
        return [claims_then(v, moved) for v in value]
    if isinstance(value, str) and value.startswith(("[", "{")):
        try:
            data = json.loads(value)
        except ValueError:
            return value
        then = claims_then(data, moved)
        return value if then == data else json.dumps(then)
    if isinstance(value, str) and "\ndata: {" in value:  # an event stream: each event's data, one at a time
        events = []
        for event in value.split("\n\n"):
            head, data = event.split("data: ", 1) if "data: {" in event else (event, None)
            if data is not None and (then := claims_then(json.loads(data), moved)) != json.loads(data):
                event = f"{head}data: {json.dumps(then)}"
            events.append(event)
        return "\n\n".join(events)
    return value


# Briefings that end without an answer are charged since wts-core 0.3.2, in a spending row of kind "unfinished"
# that counts towards the daily limits: what the model's adapter saw used, or, where it saw nothing, what a
# briefing in progress is reserved at (CHANGED_SETTINGS: $0.30). Before, they cost nothing. Each scenario that ends
# one so, with the rows it now writes (all but the time and the reader, which are those of the briefing's other
# rows); the recordings hold none.
ESTIMATE = CHANGED_SETTINGS["usage"]["briefing_estimate_usd"][1]
AS_ANSWERED = [1300, 200, 400, 300, 0.00483]  # what the faked SDK's briefing reports using, as an answer is charged
UNFINISHED: dict[str, list[list[Any]]] = {
    # the site's scripted pipeline reports no model calls: its model had begun writing, so the estimate
    "the pipeline raising mid-stream": [["unfinished", "claude-sonnet-5-5", 0, 0, 0, 0, ESTIMATE]],
    "the reader leaving while the briefing is written": [["unfinished", "claude-sonnet-5-5", 0, 0, 0, 0, ESTIMATE]],
    # the site's error event when the model declines carries no counts: what the adapter saw, for each of the two
    "the model declines": [["unfinished", "claude-sonnet-5-5", *AS_ANSWERED]] * 2,
}


def without_unfinished(now: Any, name: str) -> Any:
    """What was written, without the rows of briefings that ended without an answer: each is checked to be one
    ``UNFINISHED`` names, charged to the same reader as the briefing's other rows."""
    spending = now["rows"]["spending"]
    unfinished = [row for row in spending if row[1] == "unfinished"]
    kept = [row for row in spending if row[1] != "unfinished"]
    assert [[row[1], row[2], *row[5:]] for row in unfinished] == UNFINISHED.get(name, []), name
    assert all(row[3:5] == kept[0][3:5] for row in unfinished), name
    return {**now, "rows": {**now["rows"], "spending": kept}}


# settings


def test_settings_resolve_as_before(tmp_path):
    golden = shared.read("settings.json")
    now = shared.settings_cases(tmp_path / "settings", REPO_ROOT, load_settings)
    assert [c["name"] for c in now] == [c["name"] for c in golden["cases"]]
    for was, case in zip(golden["cases"], now, strict=True):
        gives = dict(case["gives"])
        if "sample" not in case["name"]:  # the sample files are their own: nothing was added to them
            real = load_settings()
            assert real.answer["cache_instructions"] == "short" and real.usage["models"]["claude-opus-5-5"] == \
                ADDED_MODELS["claude-opus-5-5"]
            assert real.usage["per_visitor_daily_questions"] == CHANGED_SETTINGS["usage"]["per_visitor_daily_questions"][1]
            gives["raw"] = {name: changed_back(name, without_added(name, values))
                            for name, values in gives["raw"].items() if name not in ADDED_SECTIONS}
            for name in ("answer", "usage", "embedding"):
                if name in gives:
                    gives[name] = changed_back(name, without_added(name, gives[name]))
        assert gives == was["gives"], case["name"]


def test_the_local_settings_file_is_written_byte_for_byte_as_before(tmp_path):
    assert shared.local_settings_written(tmp_path, save_local_settings) == shared.read("settings.json")["local_file"]


# the checks on briefings


def test_the_checks_report_each_briefing_as_before():
    """Twelve briefings as the model might write them, through every check. Two differences, on purpose: each
    answer now carries ``rules``, the version of the checking rules that wrote it (wts-core's
    to_dict() writes today's keys and then rules); and under version 2 of the rules the uncited claim of one
    (``CLAIMS_NOW``) is a point to check, where version 1 took it for a join."""
    from wts_core.checks import RULES_VERSION

    from hansard.rag.checks import checked
    from hansard.rag.sources import Source

    sources = [Source(**s) for s in shared.SOURCES]
    golden = shared.read("checks.json")["written"]
    assert [c["name"] for c in golden] == [c["name"] for c in shared.CHECK_CASES]
    for was, case in zip(golden, shared.CHECK_CASES, strict=True):
        answer = dict(checked(case["blocks"], sources))
        assert list(answer)[-1] == "rules" and answer.pop("rules") == RULES_VERSION == 2, case["name"]
        moved: list[str] = []
        assert judged_then(answer, moved) == was["answer"], case["name"]
        assert moved == (list(CLAIMS_NOW[:1]) if case["name"] == "a cited claim, a lead-in and an uncited claim"
                         else []), case["name"]


def test_the_wide_rule_for_notes_about_the_evidence_is_kept():
    """This site has always taken a sentence with "was supplied" or "were retrieved" for a note about the
    evidence, not a claim to check. The package has a stricter form too; narrowing is a change of its own, with an
    evaluation run before and after, and is not part of this move."""
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
    """A briefing stamped with today's rules is left alone when it is shown again:
    judging it again could only repeat, less well informed, what the verifier did when it was written. Today's
    rules are version 2 (wts-core 0.3.2); one stamped with version 1 is judged again, as one with no stamp is."""
    from wts_core.checks import RULES_VERSION

    from hansard.rag.checks import shown_again

    assert RULES_VERSION == 2
    stamped = shown_again({**shared.an_answer(), "rules": RULES_VERSION})
    assert [s["status"] for s in stamped["segments"]] == ["cited", "unsupported", "cited", "unsupported"]
    assert stamped["unsupported_segments"] == 2 and stamped["grounded_ratio"] == 0.5
    assert stamped["loaded_words"] == 0  # a judgemental-word mark is judged by today's list every time it is shown
    unstamped = shown_again(shared.an_answer())
    assert [s["status"] for s in unstamped["segments"]] == ["cited", "connective", "cited", "unsupported"]
    assert unstamped["loaded_words"] == 0
    assert shown_again({**shared.an_answer(), "rules": 1}) == {**unstamped, "rules": 1}


# the interests check


def test_the_interests_check_asks_the_model_the_same_and_makes_the_same_of_its_answer():
    """What the small model is sent (the whole request), the flags made of its answer and the usage handed back
    to be recorded, case for case: an answer with nothing in it among them, which finds no matches and is still
    counted for what it cost (400 tokens in and 30 out in the recording), as the site's own code did before the
    shared package. (On wts-core 0.2.1 the client's error did not carry the usage and this test held that
    shortfall with ``usage is None``; wts-core 0.3.0 closed it.) The stand-in for the SDK here has only ``messages.parse``, which is
    the path of the shared client these tests exercise; the package's own wire test holds its other path, on a
    real client, to the same request."""
    from hansard.rag import interests
    from hansard.rag.sources import Source

    from .conftest import model_of

    golden = shared.read("interests.json")
    sources = [Source(**s) for s in shared.INTEREST_SOURCES]
    for name, matches in shared.INTEREST_CASES.items():
        sdk = shared.InterestsSDK(matches)
        flags, usage = interests.check(model_of(sdk), "claude-haiku-5-5", "What has been said about trial by jury?",
                                       sources, shared.Register(shared.REGISTER), None, effort="low")
        was = golden[name]
        assert shared.plain(sdk.asked) == was["asked"], name
        assert shared.plain(flags) == was["flags"], name
        if matches is None:
            assert flags == [] and (was["usage"]["input_tokens"], was["usage"]["output_tokens"]) == (400, 30)
        assert usage.as_stored() == {  # every case, the answer with nothing in it too
            "input_tokens": was["usage"]["input_tokens"] + was["usage"]["cache_creation_input_tokens"],
            "output_tokens": was["usage"]["output_tokens"],
            "cache_read_tokens": was["usage"]["cache_read_input_tokens"],
            "cache_write_tokens": was["usage"]["cache_creation_input_tokens"]}, name
    sdk = shared.InterestsSDK([])
    assert interests.check(model_of(sdk), "claude-haiku-5-5", "q", sources[2:], shared.Register({}), None) == (
        [], None)
    assert sdk.asked == [] == golden["nobody quoted has an entry: the model is not asked"]["asked"]


def test_an_interests_check_whose_answer_has_nothing_in_it_is_still_counted_for_what_it_cost():
    """As the site's own code did before the shared package: the call was billed, so it counts towards the caps."""
    from hansard.rag import interests
    from hansard.rag.sources import Source

    from .conftest import model_of

    was = shared.read("interests.json")["an answer with nothing parsed in it"]
    flags, usage = interests.check(model_of(shared.InterestsSDK(None)), "claude-haiku-5-5",
                                   "What has been said about trial by jury?",
                                   [Source(**s) for s in shared.INTEREST_SOURCES], shared.Register(shared.REGISTER),
                                   None, effort="low")
    assert flags == [] and usage is not None
    assert usage.as_stored()["input_tokens"] == was["usage"]["input_tokens"] == 400
    assert usage.as_stored()["output_tokens"] == was["usage"]["output_tokens"] == 30


def test_on_the_providers_own_client_the_interests_check_puts_on_the_wire_what_the_old_code_did(monkeypatch):
    """The two tests above give the shared client a stand-in for the provider's library that has only
    ``messages.parse``, and so exercise the path the client keeps for such a stand-in. On the provider's own client
    (the server's) the shared client takes the other path from wts-core 0.3.0: it fetches the answer with
    ``messages.create`` and reads it itself, so that the cost of an answer it cannot use is never lost. Here the
    provider's real library is given a transport that answers in place of the network (no call leaves the
    machine; the key is a placeholder): the request the interests check sends that way is, in body, address and
    the header that names the helper, the request the library's own ``messages.parse`` sends for the old code's
    recorded call; and its answers are read as before, the one with nothing in it counted for what it cost."""
    import anthropic
    import httpx2 as httpx

    from hansard.rag import interests
    from hansard.rag.sources import Source

    from .conftest import model_of

    golden = shared.read("interests.json")
    sent: list[httpx.Request] = []
    answer: dict = {}

    def network(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={
            "id": "msg_0", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
            "content": [{"type": "text", "text": answer["text"]}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 400, "output_tokens": 30, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": 0}})

    provider = anthropic.Anthropic(api_key="placeholder", base_url="http://provider.invalid", max_retries=0,
                                   http_client=httpx.Client(transport=httpx.MockTransport(network)))
    sources = [Source(**s) for s in shared.INTEREST_SOURCES]
    for name, matches in shared.INTEREST_CASES.items():
        answer["text"] = "" if matches is None else json.dumps({"matches": matches})
        sent.clear()
        flags, usage = interests.check(model_of(provider), "claude-haiku-5-5",
                                       "What has been said about trial by jury?", sources,
                                       shared.Register(shared.REGISTER), None, effort="low")
        assert len(sent) == 1, name
        now = sent[0]
        was = golden[name]
        assert shared.plain(flags) == was["flags"], name
        assert usage is not None and usage.as_stored()["input_tokens"] == 400, name  # nothing in it: still counted
        # the old code's call, as recorded, made by the provider's own library
        sent.clear()
        old_call = {k: v for k, v in was["asked"][0].items() if k != "output_format"}
        if matches is None:
            with pytest.raises(Exception):  # noqa: B017  # the library's own reading of an empty answer
                provider.messages.parse(**old_call, output_format=interests.Matches)
        else:
            provider.messages.parse(**old_call, output_format=interests.Matches)
        then = sent[0]
        assert (now.method, str(now.url)) == (then.method, str(then.url)), name
        assert json.loads(now.content) == json.loads(then.content), name
        assert now.headers.get("x-stainless-helper") == then.headers.get("x-stainless-helper") == "messages.parse"


# the flow of a question, with a scripted pipeline

SCRIPTED = shared.read("ask-scripted.json")
# the scenarios with a follow-up, and how many follow-up keys each has (``follow_ups_as_now``)
FOLLOW_UPS = {"a follow-up is cached too": 1, "a briefing, the same again, a follow-up, sources only": 1}


@pytest.mark.parametrize("name", list(shared.SCRIPTED))
def test_the_flow_of_a_question_sends_and_writes_what_it_did(name, tmp_path, monkeypatch):
    """Every byte sent and every row written, case for case, with the shared briefing desk in place of the
    route's own flow: what is sent, the spending record, the answer cache, the saved briefings, the notes of
    failures, the site's own counts, what the pipeline was asked to do, each saved briefing as its address serves
    it, and what the status page says. One difference, on purpose: a briefing that ends without an answer is
    charged (``UNFINISHED``)."""
    from .sides import scripted_side

    instructions_then(monkeypatch)
    now = shared.plain(shared.scripted_asks(lambda **kw: scripted_side(tmp_path, monkeypatch, **kw), only=name),
                       tmp=tmp_path)[name]
    now = without_unfinished(now, name)
    was = SCRIPTED[name]
    assert now == (follow_ups_as_now(was, now, FOLLOW_UPS[name]) if name in FOLLOW_UPS else was)


# the same through the site's real pipeline, over the tests' own index

REAL = shared.read("ask-real.json")
RULES = '"rules": 2'


def without_rules(value: Any) -> Any:
    """What was sent or saved, without the stamp of the checking rules on each written answer."""
    if isinstance(value, dict):
        return {k: without_rules(v) for k, v in value.items() if k != "rules"}
    if isinstance(value, list):
        return [without_rules(v) for v in value]
    if isinstance(value, str) and RULES in value:
        return value.replace(", " + RULES, "")
    return value


@pytest.mark.parametrize("name", list(shared.REAL))
def test_the_real_pipeline_sends_and_writes_what_it_did(name, indexed_settings, tmp_path, monkeypatch):
    """The whole of each answer through the site's real search, ranking, instructions and checks, with a faked
    Anthropic SDK; and the requests the model was sent, whole. Three differences, on purpose: each answer the
    checks wrote carries ``rules``, the version of the checking rules, in what is sent,
    saved and cached, and a saved briefing with the stamp is shown as it was saved; under version 2 of the rules
    the faked model's uncited sentence is a point to check (``CLAIMS_NOW``), wherever the answer is; and a
    briefing that ends without an answer is charged (``UNFINISHED``)."""
    from .sides import real_side

    instructions_then(monkeypatch)
    n = iter(range(100))
    now = shared.plain(shared.real_asks(
        lambda sdk, **usage: real_side(indexed_settings, tmp_path / f"real-{next(n)}", sdk, monkeypatch, **usage),
        only=name), tmp=tmp_path)[name]
    was = REAL[name]
    now = without_unfinished(parse_effort_then(without_research_rule(now)), name)
    moved: list[str] = []
    now = claims_then(now, moved)
    if name in FOLLOW_UPS:
        was = follow_ups_as_now(was, now, FOLLOW_UPS[name])
    assert now["calls"] == was["calls"]  # what the model was asked: every request, key for key
    written = [body for _, body in now["sent"] if '"no_records": false' in body]
    assert all(RULES in body for body in written)
    assert set(moved) == ({CLAIMS_NOW[1]} if written else set())
    assert without_rules(now) == was
    if written:
        assert all(RULES in row[4] for row in now["rows"]["briefings"])  # each saved briefing's events
        assert all(RULES in row[2] for row in now["rows"]["cache"] if '\\"no_records\\": false' in row[2])


# the small tables


def test_the_limit_on_requests_a_minute_decides_as_before():
    from wts_core.testing import FixedClock
    from wts_core.usage import RateLimiter

    golden = shared.read("limiter.json")
    clock = FixedClock(shared.START)
    rate = RateLimiter(golden["per_minute"], forget_over=50_000, clock=clock)
    at, allowed = 0.0, []
    for t, who in golden["script"]:
        clock.advance(t - at)
        at = t
        allowed.append(rate.allow(who))
    assert allowed == golden["allowed"]


def test_every_recorded_usage_costs_what_it_did():
    from wts_core.ports import Usage
    from wts_core.usage import PriceTable

    prices = PriceTable.from_settings(load_settings().usage)
    golden = shared.read("costs.json")
    assert len(golden) == len(shared.USAGES) * len(shared.MODELS)
    for was in golden:
        usage = Usage.from_mapping(was["usage"])
        assert prices.cost(usage if was["usage"] else None, was["model"]) == was["cost_usd"], was
        assert usage.as_stored() == was["stored_as"], was


def test_the_answer_caches_keys_are_what_they_were(tmp_path, monkeypatch):
    """The key is computed exactly as before, so the week's cached answers are still found after the move. Two
    differences, on purpose: the instructions' version is new (``INSTRUCTIONS_THEN``); and a follow-up's key no
    longer holds the earlier answer the browser sent, which is not taken from it now, but the earlier briefing's
    id (docs/DECISIONS.md, 37), so a follow-up's key is new and is the same for the same earlier briefing."""
    from hansard.api import app as web

    monkeypatch.setenv("HANSARD_DATA_DIR", str(tmp_path))
    golden = shared.read("cache-keys.json")
    assert len(golden) == len(shared.KEY_WORLDS) * len(shared.KEY_BODIES)
    follow_ups = set()
    for was in golden:
        world = was["world"]
        settings = load_settings(**({"answer.model": world["model"]} if "model" in world else {}))
        with monkeypatch.context() as patch:
            patch.setattr(web, "INSTRUCTIONS_VERSION", world.get("instructions", INSTRUCTIONS_THEN))
            key = web.answer_key(settings, web.AskBody(**was["body"]), world.get("data", 7), model=True,
                                 cache_hours=168)
            if "history" in was["body"]:
                assert key != was["key"], was
                follow_ups.add(key)
                again = web.answer_key(settings, web.AskBody(**{**was["body"], "history": [
                    {k: v for k, v in h.items() if k != "answer"} for h in was["body"]["history"]]}),
                    world.get("data", 7), model=True, cache_hours=168)
                assert again == key, was  # the answer sent is ignored: the same key without it
                continue
        assert key == was["key"], was
    assert len(follow_ups) == len({json.dumps(w["world"], sort_keys=True) for w in golden}) * sum(
        "history" in b for b in shared.KEY_BODIES)  # still a key of its own for each
    settings = load_settings()
    assert web.answer_key(settings, web.AskBody(question=shared.Q), 7, model=False, cache_hours=168) is None
    assert web.answer_key(settings, web.AskBody(question=shared.Q), 7, model=True, cache_hours=0) is None


def test_the_caps_decide_as_before_over_a_scripted_month(settings):
    """Two visitors, an account and the site's own spending, across the end of a month and of a day: each cost and
    each refusal's words; then the places among the briefings in progress."""
    import dataclasses

    from wts_core.testing import FixedClock
    from wts_core.usage import BriefingsInProgress, busy_refusal

    from hansard.api.usage import desk
    from hansard.messages import MESSAGES
    from hansard.store.wts import open_core

    golden = shared.read("caps.json")
    clock = FixedClock(shared.START)
    limited = dataclasses.replace(settings, raw={**settings.raw, "usage": {**settings.usage, **golden["limits"]}})
    parts = desk(limited, open_core(limited, clock), clock)
    said = []
    for when, what, *rest in golden["script"]:
        clock.set(when)
        if what == "spend":
            kind, model, usage, visitor, account = rest
            said.append(parts.spending.add(kind, model, usage, visitor=visitor, account=account))
        else:
            visitor, account, pending = rest
            refusal = parts.caps.refusal(visitor=visitor, account=account, pending_usd=pending)
            said.append(refusal.message if refusal else None)
    assert said == golden["said"]
    flight = BriefingsInProgress(**golden["in_progress"])
    places = []
    for what, arg in golden["in_progress_script"]:
        if what == "start":
            busy = flight.start(arg)
            places.append(busy_refusal(busy, MESSAGES).message if busy else None)
        elif what == "finish":
            flight.finish(arg)
            places.append(dict(flight.running))
        else:
            places.append(flight.pending_usd(besides=arg))
    assert places == golden["places"]


# topic briefings


@pytest.mark.parametrize("case", shared.TOPIC_CASES, ids=[c[0] for c in shared.TOPIC_CASES])
def test_topic_briefings_are_refreshed_as_before(case, tmp_path, monkeypatch):
    """The update's refresh of the standing questions, on the shared spending record, caps, failures and saved
    briefings: what is said of each topic, the rows written, and what the topic pages serve."""
    import dataclasses

    from hansard import topics

    from .sides import scripted_side

    name, plays, usage, has_model = case
    side = scripted_side(tmp_path, monkeypatch, has_model=has_model, **usage)
    (tmp_path / "topics.toml").write_text(shared.TOPICS)
    settings = dataclasses.replace(side.settings, raw={**side.settings.raw, "topics": {
        "file": str(tmp_path / "topics.toml"), "max_per_run": 4}})
    side.app = None
    side = _with_settings(side, settings, tmp_path, monkeypatch, has_model)
    side.pipeline.plays.extend(plays)
    side.latest_record()
    parts = side.app.state.parts
    said = [topics.refresh_topics(settings, side.state, side.pipeline, parts, progress=lambda line: None)]
    side.clock.advance(3600)
    said.append(topics.refresh_topics(settings, side.state, side.pipeline, parts, progress=lambda line: None))
    with side.state.connect() as con:
        versions = [list(r) for r in con.execute("SELECT slug, briefing_id, prepared_at, records_up_to "
                                                 "FROM topic_versions ORDER BY slug, prepared_at")]
        checked = [list(r) for r in con.execute("SELECT slug, checked_at, outcome FROM topic_checks ORDER BY 1")]
    now = shared.plain({"said": said, "rows": side.rows(), "versions": versions, "checks": checked,
                        "calls": side.calls(), "listing": side.get("/api/topics"),
                        "page": side.get("/api/topics/trial-by-jury")}, tmp=tmp_path)
    assert now == shared.read("topics.json")[name]


def _with_settings(side: Any, settings: Any, folder: Any, monkeypatch: Any, has_model: bool) -> Any:
    """The same telling with other settings: its web app made again over them (and a pipeline of its own)."""
    from wts_core.testing import FixedClock

    from .sides import new_side

    clock = FixedClock(shared.START)
    pipeline = shared.ScriptedPipeline(clock.advance, settings.answer["model"], has_model=has_model)
    return new_side(pipeline, settings, clock, monkeypatch)
