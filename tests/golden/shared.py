"""What the golden recorder (record.py, run on the site's code as it was before wts-core) and the parity tests
(tests/test_parity.py and its neighbours, run on the code as it is now) both need: the tables of inputs, the
scripted pipeline, a faked model, a simulated Backblaze B2 and the scenarios themselves. Nothing here imports the
site's code, so it reads the same under either. Whatever must touch the site (its settings, its web app, its state
database) is handed in by the caller.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import stat
import tarfile
import threading
from collections.abc import Callable, Iterator
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

import httpx

GOLDEN = Path(__file__).parent
START = "2026-11-13T12:00:00+00:00"   # where every scenario's clock starts
DAY = START[:10]
SALT = "5a" * 16                       # the day's visitor secret, so a visitor's code is the same in every run
TODAY = date(2026, 10, 1)              # the pipeline's "today" in the scenarios with a real index
DONATE = "https://example.org/support"
ADMIN_TOKEN = "golden-admin-token"     # a made-up value, for the admin routes in these scenarios only
ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # a saved briefing's address is ten of these


def read(name: str) -> Any:
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


def write(out: Path, name: str, value: Any) -> None:
    (out / name).write_text(json.dumps(value, indent=1, ensure_ascii=False, sort_keys=True, default=str) + "\n",
                            encoding="utf-8")


def plain(value: Any, **places: Path | str) -> Any:
    """``value`` as JSON would hold it, with each folder's path replaced by its name in angle brackets."""
    text = json.dumps(value, default=str)
    for name, path in sorted(places.items(), key=lambda kv: -len(str(kv[1]))):
        text = text.replace(str(path), f"<{name}>")
    return json.loads(text)


def briefing_ids() -> Iterator[str]:
    """Addresses for saved briefings, in a fixed order, each of the site's own shape (ten of ``ALPHABET``)."""
    for a in ALPHABET:
        for b in ALPHABET:
            yield f"briefing{a}{b}"


# ---- settings ------------------------------------------------------------------------------------------------------

SAMPLE_CONFIG = """\
[paths]
data_dir = "{data_dir}"

[archive]
start_date = "2005-05-05"
record_dirs = ["debates"]

[answer]
model = "sample-model"

[usage]
daily_spend_cap_usd = 1.5
"""
LOCAL = """\
# Per-installation settings written by `hansard bootstrap`; they override config/settings.toml.

[usage]
daily_spend_cap_usd = 9.0
per_visitor_daily_questions = 7

[embedding]
backend = "hashing"
dim = 64

[site]
donate_url = "https://example.test/donate"

[brand_new]
key = "value"
"""
SETTINGS_CASES: list[dict[str, Any]] = [
    {"name": "the real settings file", "config": "real", "env": {}},
    {"name": "the real file, the data folder from the environment (absolute)", "config": "real",
     "env": {"HANSARD_DATA_DIR": "<tmp>/elsewhere"}},
    {"name": "the real file, the data folder from the environment (relative)", "config": "real",
     "env": {"HANSARD_DATA_DIR": "other/data"}},
    {"name": "the real file, a local file layered over it", "config": "real",
     "env": {"HANSARD_DATA_DIR": "<tmp>/local"}, "local": LOCAL},
    {"name": "the real file, overrides as the tests give them", "config": "real",
     "env": {"HANSARD_DATA_DIR": "<tmp>/o"},
     "overrides": {"embedding.backend": "hashing", "embedding.dim": 64, "new.key": 1}},
    {"name": "the real file, a local file and overrides", "config": "real",
     "env": {"HANSARD_DATA_DIR": "<tmp>/both"}, "local": LOCAL, "overrides": {"usage.daily_spend_cap_usd": 0.0}},
    {"name": "a sample file named by HANSARD_CONFIG", "config": SAMPLE_CONFIG, "data_dir": "<tmp>/sample-data",
     "env": {}},
    {"name": "a sample file with a relative data folder", "config": SAMPLE_CONFIG, "data_dir": "var/data",
     "env": {}},
    {"name": "a sample file given as an argument, the data folder from the environment", "config": SAMPLE_CONFIG,
     "data_dir": "var/data", "env": {"HANSARD_DATA_DIR": "<tmp>/from-env"}, "as_argument": True},
    {"name": "an empty HANSARD_DATA_DIR counts as unset", "config": "real", "env": {"HANSARD_DATA_DIR": ""}},
]
ACCESSORS = ("archive_start", "record_dirs", "ranking", "answer", "embedding", "regions", "usage", "sources",
             "raw_xml_dir", "parlparse_dir", "reference_dir", "parquet_dir", "index_dir", "state_db", "log_dir")
# what `hansard bootstrap` and `hansard reindex-vectors` write into settings.local.toml, one call after another
LOCAL_UPDATES: list[dict[str, dict[str, Any]]] = [
    {"embedding": {"backend": "hashing", "dim": 64}},
    {"embedding": {"backend": "none"}, "ranking": {"vector_nprobes": 12, "vector_refine_factor": 5}},
    {"site": {"note": 'a "quoted" word and a back\\slash'}, "ranking": {"bm25_weight": 0.5, "on": True}},
]


def clear_environment(prefix: str = "HANSARD_") -> None:
    for key in [k for k in os.environ if k.startswith(prefix)]:
        del os.environ[key]


def settings_cases(tmp: Path, root: Path, load: Callable[..., Any]) -> list[dict[str, Any]]:
    """Each case resolved by ``load(config_path_or_None, **overrides)``, with the environment set as the case says."""
    out = []
    for n, case in enumerate(SETTINGS_CASES):
        clear_environment()
        os.environ.update({k: v.replace("<tmp>", str(tmp)) for k, v in case["env"].items()})
        argument = None
        if case["config"] != "real":
            path = tmp / f"sample-{n}.toml"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(case["config"].format(data_dir=case["data_dir"].replace("<tmp>", str(tmp))))
            if case.get("as_argument"):
                argument = path
            else:
                os.environ["HANSARD_CONFIG"] = str(path)
        if case.get("local"):
            folder = Path(os.environ["HANSARD_DATA_DIR"])
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "settings.local.toml").write_text(case["local"])
        s = load(argument, **case.get("overrides", {}))
        gives = {"raw": s.raw, "data_dir": str(s.data_dir), "config_path": str(s.config_path),
                 "overrides": s.overrides, "site": s.section("site"), "no_such_section": s.section("no_such_section"),
                 **{name: getattr(s, name) for name in ACCESSORS}}
        out.append({"name": case["name"], "gives": plain(gives, tmp=tmp, root=root)})
    clear_environment()
    return out


def local_settings_written(tmp: Path, save: Callable[[Path, dict[str, dict[str, Any]]], Path]) -> list[str]:
    """``settings.local.toml`` after each of ``LOCAL_UPDATES`` is saved on top of the last, byte for byte."""
    folder = tmp / "local-written"
    return [save(folder, updates).read_text() for updates in LOCAL_UPDATES]


# ---- the limit on requests a minute ------------------------------------------------------------------------------

A, B = "visitor:a", "user:7"
LIMITER_PER_MINUTE = 3
LIMITER_SCRIPT = (
    [[0.0, A]] * 4 + [[0.0, B]] + [[30.0, A], [59.9, A], [60.0, A], [60.1, A], [60.1, A], [60.1, A], [60.1, A]]
    + [[60.2, B]] * 4 + [[120.2, B], [120.3, B], [120.3, B], [200.0, A], [200.0, B]]
)


# ---- cost ----------------------------------------------------------------------------------------------------------

MODELS = ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-haiku-5-5-20260101", "claude-opus-5-5", None]
USAGES: list[dict[str, Any] | None] = [
    None,
    {},
    {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0},
    {"input_tokens": 1000, "output_tokens": 200},
    {"input_tokens": 9000, "output_tokens": 700, "cache_read_tokens": 3000, "cache_write_tokens": 2000},
    {"input_tokens": 1200, "output_tokens": 80, "cache_read_tokens": 0, "cache_write_tokens": 0},
    {"input_tokens": 123_457, "output_tokens": 15_999, "cache_read_tokens": 77_777, "cache_write_tokens": 3_333},
    # the SDK's own names: input_tokens does not include the cache writes
    {"input_tokens": 7000, "output_tokens": 700, "cache_read_input_tokens": 3000, "cache_creation_input_tokens": 2000},
    {"input_tokens": 100, "output_tokens": 50, "cache_read_tokens": 0, "cache_read_input_tokens": 999},
    {"input_tokens": None, "output_tokens": None, "cache_read_tokens": None, "cache_write_tokens": None},
    {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_tokens": 1_000_000,
     "cache_write_tokens": 1_000_000},
]


# ---- cache keys ----------------------------------------------------------------------------------------------------

Q = "What has been said about trial by jury since 2020?"
Q2 = "And what did peers say?"
Q3 = "What has been said about bus services in rural areas?"
Q4 = "How have ministers justified delays to HS2?"
EARLIER = [{"question": Q, "answer": "Ministers said that jury trial would be kept.", "topic": "trial by jury",
            "scope": "since 2020"}]
KEY_BODIES: list[dict[str, Any]] = [
    {"question": Q},
    {"question": "what has been said about  TRIAL by jury since 2020?"},
    {"question": Q, "mode": "comprehensive"},
    {"question": Q, "recency": 0.7},
    {"question": Q, "role_weight": 1.5},
    {"question": Q, "scope": {"written": False, "votes": False}},
    {"question": Q, "plan": {"search_text": "trial by jury", "filters": {"date_from": "2020-01-01"}}},
    {"question": Q, "sources_only": True},
    {"question": Q2},
    {"question": Q2, "history": EARLIER},
    {"question": Q2, "history": EARLIER, "follows": Q},
    {"question": Q, "compare": [{"kind": "party", "id": "labour"}, {"kind": "party", "id": "conservative"}]},
    {"question": "Où est la bibliothèque — “déjà vu”?"},
]
# what else a key depends on: each of these must change it
KEY_WORLDS: list[dict[str, Any]] = [
    {},
    {"model": "claude-opus-5-5"},
    {"data": 41},
    {"data": None},
    {"instructions": "0123456789ab"},
]


# ---- caps, and briefings in progress ---------------------------------------------------------------------------

CAP_LIMITS = {"per_visitor_daily_questions": 2, "per_user_daily_questions": 3, "daily_spend_cap_usd": 1.0,
              "monthly_spend_cap_usd": 2.0}
# (when, what): spend(kind, model, usage, visitor, account) or check(visitor or None, account or None, pending)
CAPS_SCRIPT: list[list[Any]] = [
    ["2026-10-31T23:59:59+00:00", "spend", "answer", "claude-sonnet-5-5", {"input_tokens": 1_000_000}, "old", None],
    ["2026-11-01T00:00:00+00:00", "spend", "answer", "claude-sonnet-5-5", {"input_tokens": 225_000}, "v0", None],
    ["2026-11-12T23:59:59+00:00", "spend", "answer", "claude-sonnet-5-5", {"input_tokens": 25_000}, "v1", None],
    ["2026-11-13T00:00:00+00:00", "check", "v1", None, 0.0],
    ["2026-11-13T00:00:00+00:00", "spend", "answer", "claude-sonnet-5-5", {"input_tokens": 50_000}, "v1", None],
    ["2026-11-13T00:00:01+00:00", "check", "v1", None, 0.0],
    ["2026-11-13T09:00:00+00:00", "spend", "parse", "claude-haiku-5-5", {"input_tokens": 100_000}, "v1", None],
    ["2026-11-13T09:00:00+00:00", "spend", "interests", "claude-haiku-5-5", {"input_tokens": 100_000}, "v1", None],
    ["2026-11-13T09:00:01+00:00", "check", "v1", None, 0.0],
    ["2026-11-13T09:00:02+00:00", "spend", "answer", "claude-sonnet-5-5", {"input_tokens": 50_000}, "v1", None],
    ["2026-11-13T09:00:03+00:00", "check", "v1", None, 0.0],
    ["2026-11-13T09:00:03+00:00", "check", "v2", None, 0.0],
    ["2026-11-13T09:00:03+00:00", "check", None, None, 0.0],
    # an account is counted by its own limit, never as a visitor; three briefings, and the fourth is refused
    ["2026-11-13T09:00:03+00:00", "check", None, 7, 0.0],
    ["2026-11-13T09:00:04+00:00", "spend", "answer", "claude-sonnet-5-5", {"input_tokens": 1_000}, None, 7],
    ["2026-11-13T09:00:04+00:00", "spend", "parse", "claude-haiku-5-5", {"input_tokens": 1_000}, None, 7],
    ["2026-11-13T09:00:05+00:00", "spend", "answer", "claude-sonnet-5-5", {"input_tokens": 1_000}, None, 7],
    ["2026-11-13T09:00:05+00:00", "check", None, 7, 0.0],
    ["2026-11-13T09:00:06+00:00", "spend", "answer", "claude-sonnet-5-5", {"input_tokens": 1_000}, None, 7],
    ["2026-11-13T09:00:07+00:00", "check", None, 7, 0.0],
    ["2026-11-13T09:00:07+00:00", "check", None, 8, 0.0],
    ["2026-11-13T09:00:07+00:00", "check", "v1", 8, 0.0],   # signed in: the visitor's own count is not looked at
    ["2026-11-13T09:00:08+00:00", "check", "v2", None, 0.77],
    ["2026-11-13T09:00:08+00:00", "check", "v2", None, 0.78],
    ["2026-11-13T09:00:09+00:00", "spend", "answer", "claude-sonnet-5-5", {"input_tokens": 385_000}, "v3", None],
    ["2026-11-13T09:00:10+00:00", "check", "v2", None, 0.0],
    ["2026-11-13T09:00:11+00:00", "spend", "answer", "claude-sonnet-5-5", {"input_tokens": 5_000}, "v3", None],
    ["2026-11-13T09:00:12+00:00", "check", "v2", None, 0.0],
    ["2026-11-13T09:00:12+00:00", "check", None, 8, 0.0],
    ["2026-11-14T00:00:00+00:00", "check", "v1", None, 0.0],
    ["2026-11-14T00:00:00+00:00", "check", None, 7, 0.0],
    ["2026-11-14T00:00:00+00:00", "check", "v1", None, 0.48],
    ["2026-11-14T00:00:00+00:00", "check", "v1", None, 0.50],
    ["2026-11-14T00:00:01+00:00", "spend", "answer", "claude-sonnet-5-5", {"input_tokens": 300_000}, "v9", None],
    ["2026-11-14T00:00:02+00:00", "check", "v1", None, 0.0],
    ["2026-12-01T00:00:00+00:00", "check", "v1", None, 0.0],
]
# (start or finish, key) or (pending, besides), for places of 2 a reader, 3 in all, at $0.25
IN_PROGRESS = {"per_key": 2, "total": 3, "estimate_usd": 0.25}
IN_PROGRESS_SCRIPT: list[list[Any]] = [
    ["start", "a"], ["start", "a"], ["start", "a"], ["pending", 0], ["pending", 1], ["start", "b"], ["start", "c"],
    ["pending", 1], ["finish", "a"], ["start", "c"], ["start", "d"], ["finish", "a"], ["finish", "b"],
    ["finish", "c"], ["pending", 0], ["pending", 5], ["start", "d"], ["finish", "d"],
]


# ---- the checks: briefings as the model writes them, and saved briefings shown again ---------------------------

BENN = "The right to trial by jury is a cornerstone of our liberties. The organization of the courts must not " \
       "weaken it."
CLARKE = "We will keep jury trial for serious offences. Gas was supplied to two million homes last winter."
SOURCES = [
    {"n": 1, "kind": "record", "title": "Hilary Benn (Leeds Central, Labour), House of Commons, 4 March 2021",
     "url": "https://www.theyworkforyou.com/debates/?id=2021-03-04a.1.0", "blocks": [BENN],
     "meta": {"heading": "Courts and Tribunals — Trial by jury"}},
    {"n": 2, "kind": "record", "title": "Kenneth Clarke (Rushcliffe, Conservative), House of Commons, 4 March 2021",
     "url": "https://www.theyworkforyou.com/debates/?id=2021-03-04a.2.0", "blocks": [CLARKE], "meta": {}},
]


def cite(n: int, text: str) -> dict[str, Any]:
    return {"type": "search_result_location", "search_result_index": n, "cited_text": text, "source": "x"}


CHECK_CASES: list[dict[str, Any]] = [
    {"name": "a cited claim, a lead-in and an uncited claim", "blocks": [
        {"text": "Hilary Benn said that "},
        {"text": "the right to trial by jury is a cornerstone of our liberties.", "citations": [cite(0, BENN)]},
        {"text": " This was a turning point for the House."}]},
    {"name": "a quotation found word for word, and one that is not", "blocks": [
        {"text": "Mr Benn called it \"a cornerstone of our liberties\"", "citations": [cite(0, BENN)]},
        {"text": " and Mr Clarke said it was \"a relic of the past\".", "citations": [cite(1, CLARKE)]}]},
    {"name": "a citation to a source that is not there", "blocks": [
        {"text": "A third member agreed.", "citations": [cite(7, "I agree")]},
        {"text": " Mr Clarke promised to keep it.", "citations": [cite(1, CLARKE)]}]},
    {"name": "judgemental words in the site's own voice, and inside a quotation", "blocks": [
        {"text": "Mr Benn slammed the plan, and the minister admitted the delay.", "citations": [cite(0, BENN)]},
        {"text": " Mr Clarke claimed that it was \"a scandal\". The Opposition's regime of amendments failed."},
        {"text": " The savings claimed were large.", "citations": [cite(1, CLARKE)]}]},
    {"name": "American spelling in the site's words, and in a quotation", "blocks": [
        {"text": "Mr Benn criticized the organization of the courts, saying \"The organization of the courts must "
                 "not weaken it.\"", "citations": [cite(0, BENN)]}]},
    {"name": "an opener about the records, then the briefing", "blocks": [
        {"text": "Across the records retrieved, two positions emerge. "},
        {"text": "Mr Benn wants jury trial kept.", "citations": [cite(0, BENN)]}]},
    {"name": "notes about the evidence, wide and narrow", "blocks": [
        {"text": "Mr Benn wants jury trial kept.", "citations": [cite(0, BENN)]},
        {"text": " Gas was supplied to two million homes. The votes on the second reading were not supplied. No "
                 "vote records were retrieved for 2021. Only two records were retrieved. The minister said that "
                 "gas was supplied late."}]},
    {"name": "an honorific's full stop ends no sentence", "blocks": [
        {"text": "Mr. Clarke said "},
        {"text": "the Government would keep jury trial for serious offences.", "citations": [cite(1, CLARKE)]},
        {"text": " Dr. Smith disagreed. The hon. Member for St. Ives was not heard."}]},
    {"name": "headings, a list and a lead-in in pieces", "blocks": [
        {"text": "## Labour MPs\n\n**Backbenchers**\n\n"},
        {"text": "- Hilary Benn (Leeds Central, Lab), 4 March 2021: "},
        {"text": "jury trial is a cornerstone", "citations": [cite(0, BENN)]},
        {"text": "\n- Kenneth Clarke (Rushcliffe, Con): "},
        {"text": "jury trial will be kept for serious offences", "citations": [cite(1, CLARKE)]},
        {"text": "\n\nIn short:"}]},
    {"name": "no records", "blocks": [
        {"text": "No records found. The archive holds nothing on this subject between those dates."}]},
    {"name": "nothing at all", "blocks": []},
    {"name": "quotes and apostrophes", "blocks": [
        {"text": "Mr Benn's speech said \""},
        {"text": "the right to trial by jury is a cornerstone", "citations": [cite(0, BENN)]},
        {"text": "\" in 2021, and it's 'urgent'."}]},
]


def an_answer() -> dict[str, Any]:
    """A checked briefing as the site's verifier reports one: a cited sentence with a quotation, a sentence marked
    for checking that today's rules clear when it is shown again (it only leads in to the citation after it), one
    that stays marked, and a judgemental-word mark that the list does not hold any more."""
    segments = [
        {"text": "Hilary Benn said that trial by jury was \"a cornerstone\".", "sources": [1],
         "cited": [{"n": 1, "text": "The right to trial by jury is a cornerstone of our liberties"}],
         "status": "cited", "loaded": [], "misquotes": []},
        {"text": " In the debate that followed:", "sources": [], "cited": [], "status": "unsupported", "loaded": [],
         "misquotes": []},
        {"text": " Kenneth Clarke said jury trial would be kept for serious offences.", "sources": [2],
         "cited": [{"n": 2, "text": "We will keep jury trial for serious offences"}], "status": "cited",
         "loaded": [], "misquotes": []},
        {"text": " This was a turning point for every member in the Chamber.", "sources": [], "cited": [],
         "status": "unsupported", "loaded": [{"start": 12, "end": 19, "word": "turning"}], "misquotes": []},
    ]
    return {"segments": segments, "markdown": "".join(s["text"] for s in segments), "no_records": False,
            "citations_total": 2, "citations_invalid": 0, "grounded_ratio": 0.5, "unsupported_segments": 2,
            "loaded_words": 1, "quotes_total": 1, "quotes_unverified": 0}


def _saved(answer: Any, **more: Any) -> list[dict[str, Any]]:
    """A saved briefing's events in this site's shape: the plan, the sources and the final event; no text event."""
    return [{"type": "plan", "plan": {"question": Q, "interpretation": "Debates on \"trial by jury\"."},
             "usage": None},
            {"type": "sources", "sources": [{"n": 1, "title": "Hilary Benn's \"speech\""}], "candidates": 12,
             "semantic": True, "evidence": {"records": 1}},
            {"type": "final", "answer": answer, **more}]


_ADMITTED = 'Mr Benn called it "a cornerstone" and the minister admitted the delay.'
_LEAD = {"segments": [{"text": 'He said "no." Mr Smith said ', "status": "unsupported"},
                      {"text": "we will act", "status": "cited", "sources": [1]}], "unsupported_segments": 1}
INTERESTS = [
    {"source": 1, "person_id": "uk.org.publicwhip/person/10669", "name": "Hilary Benn",
     "register": "Register of Members' Financial Interests", "link": "/people/10669#h-interests", "items": [
         {"category": "3 Gifts", "summary": "Bar Council - £500.00", "registered": "2021-01-05",
          "connection": "The gift is from the barristers' professional body and the speech is on jury trial.",
          "kind": "register", "register": "commons", "url": None},
         {"category": "1 Employment", "summary": "Parliamentary Under-Secretary of State, Ministry of Justice",
          "registered": "2020-02-01", "connection": "A post in the department.", "kind": "register",
          "register": "commons", "url": None}]},
    {"source": 2, "person_id": "uk.org.publicwhip/person/10115", "name": "Kenneth Clarke",
     "register": "Register of Members' Financial Interests", "link": "/people/10115#h-interests", "items": [
         {"category": "1 Employment", "summary": "Salary as Lord Chancellor", "registered": "2020-02-01",
          "connection": "A post in the Government.", "kind": "register", "register": "commons", "url": None}]},
]
# without posts in government or Parliament, as the site has shown them since they were left out
INTERESTS_NOW = [{**INTERESTS[0], "items": INTERESTS[0]["items"][:1]}]
SAVED_BRIEFINGS: list[dict[str, Any]] = [
    {"name": "marks made by rules since corrected", "events": _saved({
        "markdown": "Hilary Benn said jury trial matters. The savings claimed were large.",
        "segments": [{"text": "Hilary Benn said ", "status": "unsupported"},
                     {"text": "jury trial matters.", "status": "cited", "sources": [1]},
                     {"text": " The savings claimed were large.", "status": "cited", "sources": [1],
                      "loaded": [{"start": 13, "end": 20, "word": "claimed"}]}],
        "unsupported_segments": 1, "grounded_ratio": 0.5, "loaded_words": 1})},
    {"name": "a mark that stands", "events": _saved({
        "segments": [{"text": _ADMITTED, "status": "cited", "sources": [1],
                      "loaded": [{"start": _ADMITTED.index("admitted"), "end": _ADMITTED.index("admitted") + 8,
                                  "word": "admitted"}]}], "loaded_words": 1})},
    {"name": "a claim before a quotation", "events": _saved(_LEAD)},
    {"name": "a short claim stays a point to check", "events": _saved({
        "segments": [{"text": "Mr Benn agreed.", "status": "unsupported"},
                     {"text": " Mr Clarke said so too.", "status": "cited", "sources": [2]}],
        "unsupported_segments": 1, "grounded_ratio": 0.6})},
    {"name": "the scripted briefing, with interests saved before posts in government were left out",
     "events": _saved(an_answer(), model="claude-sonnet-5-5", stop_reason="end_turn", interests=INTERESTS)},
    {"name": "no records", "events": _saved({
        "segments": [{"text": "No records found. The archive contains no records matching this question and these "
                              "filters.", "sources": [], "cited": [], "status": "connective"}],
        "markdown": "No records found.", "no_records": True, "citations_total": 0, "citations_invalid": 0,
        "grounded_ratio": 1.0, "unsupported_segments": 0})},
    {"name": "an answer with no segments", "events": _saved({"markdown": "x", "segments": []})},
    {"name": "a briefing with no final event", "events": _saved(None)[:2]},
]


# ---- the interests check: what the small model is asked, and what is made of its answer ------------------------

GIFT = {"type": "3", "name": "Gifts, benefits and hospitality from UK sources", "items": [
    {"summary": "Bar Council - £1,920.00", "details": [["Payment Description", "Dinner at Middle Temple"]],
     "registered": "02 July 2026", "children": []}]}
LAND = {"type": "6", "name": "Land and property (within or outside the UK)", "items": [
    {"summary": "Agricultural in County Fermanagh", "details": [], "registered": "10 January 2020", "children": []}]}
INTEREST_SOURCES = [
    {"n": 1, "kind": "record", "title": "A, 10 September 2026", "url": "u1", "blocks": ["Juries must stay."],
     "meta": {"speaker": {"person_id": "uk.org.publicwhip/person/1", "house": "commons", "name": "Alice Example"},
              "date": "2026-09-10", "heading": "Trial by jury", "text": "Juries must stay, as the Bar says."}},
    {"n": 2, "kind": "record", "title": "B, 10 September 2026", "url": "u2", "blocks": ["Farms need help."],
     "meta": {"speaker": {"person_id": "uk.org.publicwhip/person/2", "house": "commons", "name": "Bob Example"},
              "date": "2026-09-10", "heading": "Farming", "text": "Farms need help with the price of feed."}},
    {"n": 3, "kind": "division", "title": "A vote", "url": "u3", "blocks": ["Ayes 300"], "meta": {}},
]
REGISTER = {"uk.org.publicwhip/person/1": [GIFT], "uk.org.publicwhip/person/2": [LAND]}
MATCHES = [
    {"contribution": 1, "interest": "A1", "subjects": "the Bar; jury trial", "relevant": True,
     "connection": "The gift is from the  barristers' body and the speech is on jury trial."},
    {"contribution": 2, "interest": "B1", "subjects": "farmland; farming", "relevant": True,
     "connection": "There is no direct link between the land and the speech."},
    {"contribution": 2, "interest": "Z9", "subjects": "made up", "relevant": True, "connection": "A made-up label."},
    {"contribution": 1, "interest": "B1", "subjects": "another member's", "relevant": True,
     "connection": "Another member's entry."},
]


class Register:
    """In place of the register of members' interests: each member's categories."""

    def __init__(self, people: dict[str, list[dict[str, Any]]]) -> None:
        self.people = people

    def for_person(self, pid: str) -> dict[str, Any]:
        return {"categories": self.people.get(pid, [])}


class InterestsSDK:
    """In place of the Anthropic SDK's client, for the interests check alone: it answers with ``matches`` (None:
    an answer with nothing parsed in it; an exception: the call fails), and keeps what it was asked."""

    def __init__(self, matches: Any) -> None:
        self.matches, self.asked = matches, []
        self.messages = NS(parse=self._parse)

    def _parse(self, **kw: Any) -> Any:
        self.asked.append({k: (v.__name__ if k == "output_format" else v) for k, v in kw.items()})
        if isinstance(self.matches, Exception):
            raise self.matches
        usage = NS(input_tokens=400, output_tokens=30, cache_read_input_tokens=0, cache_creation_input_tokens=0)
        if self.matches is None:
            return NS(stop_reason="end_turn", parsed_output=None, usage=usage)
        return NS(stop_reason="end_turn", parsed_output=kw["output_format"](matches=self.matches), usage=usage)


INTEREST_CASES: dict[str, Any] = {
    "matches, kept only where they are real": MATCHES,
    "the model finds none": [],
    "an answer with nothing parsed in it": None,
}


# ---- a scripted pipeline, in the site's own shapes ---------------------------------------------------------------

PARSE_USAGE = {"input_tokens": 1200, "output_tokens": 80, "cache_read_tokens": 0, "cache_write_tokens": 0}
ANSWER_USAGE = {"input_tokens": 9000, "output_tokens": 700, "cache_read_tokens": 3000, "cache_write_tokens": 2000}
INTERESTS_USAGE = {"input_tokens": 400, "output_tokens": 30, "cache_read_tokens": 0, "cache_write_tokens": 0}
DECLINED = "The model declined to answer this question."
NO_KEY_NOTE = "Showing the evidence set only (no Anthropic API key on the server)."
CARDS = [
    {"n": 1, "kind": "record", "title": "Hilary Benn, House of Commons, 4 March 2021", "group": "opposition",
     "date": date(2021, 3, 4)},
    {"n": 2, "kind": "record", "title": "Kenneth Clarke, House of Commons, 4 March 2021", "group": "government"},
    {"n": 3, "kind": "edm", "title": "Early day motion 123: Trial by jury", "group": "edm"},
    {"n": 4, "kind": "division", "title": "Division 45: Courts Bill, second reading"},
]
NO_RECORDS = {"segments": [{"text": "No records found. The archive contains no records matching this question and "
                                    "these filters.", "sources": [], "cited": [], "status": "connective"}],
              "markdown": "No records found.", "no_records": True, "citations_total": 0, "citations_invalid": 0,
              "grounded_ratio": 1.0, "unsupported_segments": 0}


class FakePlan:
    """What the site's ``QueryPlan`` gives its route when the model has read the question."""

    def __init__(self, question: str, parser: str, parse_error: str | None = None) -> None:
        self.question, self.parser, self.parse_error, self.group_by = question, parser, parse_error, "role"

    def to_dict(self) -> dict[str, Any]:
        return {"question": self.question, "parser": self.parser, "filters": {"date_from": "2020-01-01"},
                "interpretation": "Debates on \"trial by jury\" since 2020."}


class FakeSource:
    def __init__(self, card: dict[str, Any]) -> None:
        self._card, self.kind = card, card["kind"]

    def card(self) -> dict[str, Any]:
        return dict(self._card)


class ScriptedPipeline:
    """In place of the site's ``Pipeline``: ``run`` sends the events of the next behaviour in ``plays`` (or of
    ``"written"``), in the site's own shapes, moving the clock as a real one takes time. ``make_plan`` and ``find``
    are what the site's sources-only search asks of it: where the model does not read the question, the site's real
    built-in rules do. ``calls`` keeps the behaviours run, and "make_plan" for each question the model read alone."""

    def __init__(self, tick: Callable[[float], None], model: str, *, has_model: bool = True) -> None:
        self.tick, self.model, self.model_on = tick, model, has_model
        self.today = date(2026, 11, 13)
        self.engine = NS(index=NS(stats=lambda: {"passages": 123456, "vectors": 61728}))
        self.votes = NS(available=False)
        self.plays: list[str] = []
        self.calls: list[str] = []
        self.entered, self.gate, self.closed = threading.Event(), threading.Event(), threading.Event()

    def has_model(self) -> bool:
        return self.model_on

    def make_plan(self, req: Any) -> tuple[Any, dict[str, int] | None]:
        self.calls.append("make_plan")
        unread = bool(self.plays) and self.plays[0] == "unread_alone"
        if unread:
            self.plays.pop(0)
        return (FakePlan(req.question, "model", "AuthenticationError: bad key" if unread else None),
                dict(PARSE_USAGE))

    def group_aliases(self) -> dict[str, Any]:
        return {}

    def _set_comparison(self, plan: Any, req: Any) -> None:
        pass

    def find(self, plan: Any, req: Any) -> tuple[Any, ...]:
        result = NS(candidates=12, semantic_available=True, evidence={"records": 2, "divisions": 1},
                    timings={"keyword": 0.0124, "meaning": 0.0401}, records=[1, 2])
        return result, ["a vote"], [FakeSource(c) for c in CARDS]

    def _plan(self, question: str, **more: Any) -> dict[str, Any]:
        return {"type": "plan", "plan": FakePlan(question, "model" if self.model_on else "offline").to_dict(),
                "usage": dict(PARSE_USAGE) if self.model_on else None, **more}

    def run(self, req: Any) -> Iterator[dict[str, Any]]:
        play = self.plays.pop(0) if self.plays else "written"
        self.calls.append(play)
        tick = self.tick
        yield {"type": "progress", "step": "reading", "passages": 123456}
        tick(0.4)
        yield self._plan(req.question, **({"parse_error": "AuthenticationError: bad key"} if play == "unread" else
                                          {"parse_error": ""} if play == "unread_empty" else {}))
        tick(0.3)
        yield {"type": "sources", "sources": [dict(c) for c in CARDS], "candidates": 12, "semantic": True,
               "evidence": {"records": 2, "divisions": 1, "devolution": [], "edms": 1, "devolved": 0,
                            "committee_evidence": 0}}
        if play == "no_records":
            yield {"type": "final", "answer": json.loads(json.dumps(NO_RECORDS)), "usage": None}
            return
        if not self.model_on:
            yield {"type": "final", "answer": None, "usage": None, "note": NO_KEY_NOTE}
            return
        if play == "held":  # a briefing still being written while another question arrives
            self.entered.set()
            assert self.gate.wait(20)
        tick(1.5)
        # as the model really sends them: a citation comes before the text of the block it belongs to
        yield {"type": "citation", "search_result_index": 0,
               "cited_text": "The right to trial by jury is a cornerstone of our liberties"}
        yield {"type": "text", "text": "Hilary Benn said that trial by jury "}
        if play == "raises":
            raise RuntimeError("the model's stream broke")
        if play == "left":  # the reader goes while the briefing is being written
            try:
                self.entered.set()
                assert self.gate.wait(20)
                for word in ("was ", "\"a ", "cornerstone\"."):
                    tick(0.1)
                    yield {"type": "text", "text": word}
            finally:
                self.closed.set()
            raise AssertionError("the pipeline was not stopped when the reader left")
        yield {"type": "text", "text": "was \"a cornerstone\"."}
        yield {"type": "citation", "search_result_index": 1, "cited_text": "We will keep jury trial for serious offences"}
        yield {"type": "text", "text": " Kenneth Clarke said jury trial would be kept for serious offences."}
        if play != "no_interests":
            yield {"type": "interests", "interests": json.loads(json.dumps(INTERESTS_NOW))}
        tick(2.0)
        if play == "declined":
            yield {"type": "error", "message": DECLINED}
            return
        yield {"type": "progress", "step": "checking"}
        tick(0.6)
        final = {"type": "final", "answer": an_answer(), "usage": dict(ANSWER_USAGE), "model": self.model,
                 "stop_reason": "end_turn", "interests": json.loads(json.dumps(INTERESTS_NOW)),
                 "interests_usage": dict(INTERESTS_USAGE)}
        if play == "no_interests":  # the check found nobody with an entry that applied: no call was made
            final.update(interests=[], interests_usage=None)
        yield final


# ---- the two tellings of /api/ask ------------------------------------------------------------------------------


class Side:
    """One telling of a scenario: the site's web app (as it was, or as it is), a pipeline and the state database.

    ``client`` is a ``fastapi.testclient.TestClient``; ``stream(body)`` gives the answer's pieces as the web server
    would take them, so that it can be closed part-way; ``connect()`` opens the state database; ``new`` says which
    tables to read (the site's own before wts-core, the package's after); ``sign_in()`` makes an account and signs
    this side's client in as it."""

    def __init__(self, *, client: Any, pipeline: Any, connect: Callable[[], sqlite3.Connection], new: bool,
                 stream: Callable[[dict[str, Any]], Iterator[bytes]], another_client: Callable[[], Any],
                 sign_in: Callable[[], None], calls: Callable[[], list[Any]] | None = None) -> None:
        self.client, self.pipeline, self.connect, self.new = client, pipeline, connect, new
        self.stream, self.another_client, self.sign_in = stream, another_client, sign_in
        self._calls = calls

    def ask(self, question: str, play: str = "written", **body: Any) -> list[Any]:
        if play != "written":
            self.pipeline.plays.append(play)
        response = self.client.post("/api/ask", json={"question": question, **body})
        return [response.status_code, response.content.decode()]

    def post(self, path: str, body: dict[str, Any] | None = None) -> list[Any]:
        response = self.client.post(path, json=body or {})
        return [response.status_code, response.json()]

    def get(self, path: str) -> list[Any]:
        response = self.client.get(path)
        return [response.status_code, response.json()]

    def calls(self) -> list[Any]:
        return self._calls() if self._calls else list(self.pipeline.calls)

    def rows(self) -> dict[str, list[list[Any]]]:
        return state_rows(self.connect(), self.new)

    def edit_cache(self, change: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]) -> None:
        """Rewrite every cached answer's events: for an answer cached by an older version of the site."""
        con = self.connect()
        table = "wts_answer_cache" if self.new else "answer_cache"
        try:
            for key, events in con.execute(f"SELECT key, events FROM {table}").fetchall():
                con.execute(f"UPDATE {table} SET events = ? WHERE key = ?",
                            (json.dumps(change(json.loads(events)), default=str), key))
            con.commit()
        finally:
            con.close()

    def latest_record(self, key: str = "debates/debates2026-11-12") -> None:
        """A file of the record read in (the site's own table, before and after): a briefing saved from now on
        says the record ran up to the day in its name."""
        con = self.connect()
        try:
            con.execute("INSERT INTO manifest (source, key, revision, file_name, size, mtime_ns, rows, parsed_at) "
                        "VALUES ('debates', ?, 'a', 'debates2026-11-12a.xml', 1, 1, 1, ?)", (key, START))
            con.commit()
        finally:
            con.close()


def state_rows(con: sqlite3.Connection, new: bool) -> dict[str, list[list[Any]]]:
    """What the flow of a briefing wrote, in one shape whichever tables hold it. A saved briefing's last column
    holds what only this site keeps with one (the newest record's date and the model), as the package keeps it."""
    try:
        if new:
            briefings = [[*list(r)[:7], json.loads(r[7])] for r in con.execute(
                "SELECT id, created_at, question, options, events, withdrawn_at, withdrawn_reason, extra "
                "FROM wts_briefings ORDER BY id")]
        else:
            briefings = [[*list(r)[:7], {"records_up_to": r[7], "model": r[8]}] for r in con.execute(
                "SELECT id, created_at, question, settings, events, withdrawn_at, withdrawn_reason, records_up_to, "
                "model FROM briefings ORDER BY id")]
        t = {"usage": "wts_spending", "account": "account", "cache": "wts_answer_cache",
             "failures": "wts_failures"} if new else \
            {"usage": "usage", "account": "user_id", "cache": "answer_cache", "failures": "failures"}
        return {
            "spending": [list(r) for r in con.execute(
                f"SELECT at, kind, model, visitor, {t['account']}, input_tokens, output_tokens, cache_read_tokens, "
                f"cache_write_tokens, cost_usd FROM {t['usage']} ORDER BY id")],
            "cache": [list(r) for r in con.execute(f"SELECT key, created_at, events FROM {t['cache']} ORDER BY key")],
            "briefings": briefings,
            "failures": [list(r) for r in con.execute(f"SELECT at, kind, question, error FROM {t['failures']} "
                                                      "ORDER BY id")],
            # the site's own counts, in the site's own tables before and after
            "question_counts": [list(r) for r in con.execute(
                "SELECT day, question, n FROM question_counts ORDER BY day, question")],
            "day_events": [list(r) for r in con.execute(
                "SELECT day, event, visitor FROM day_events ORDER BY day, event, visitor")],
        }
    finally:
        con.close()


def while_one_is_written(side: Side, then: Callable[[], list[Any]]) -> list[Any]:
    """Start a briefing that stays in progress, do ``then`` while it is, and let it finish. What was sent: the
    held briefing's answer first, then whatever ``then`` sent."""
    side.pipeline.plays.append("held")
    held: list[Any] = []
    other = side.another_client()
    thread = threading.Thread(target=lambda: held.append(other.post("/api/ask", json={"question": Q})))
    thread.start()
    assert side.pipeline.entered.wait(20)
    try:
        sent = then()
    finally:
        side.pipeline.gate.set()
        thread.join(30)
    return [[held[0].status_code, held[0].content.decode()], *sent]


def the_reader_leaves(side: Side) -> list[Any]:
    side.pipeline.plays.append("left")
    stream = side.stream({"question": Q})
    received = b""
    for chunk in stream:
        received += chunk
        if chunk.startswith(b"event: text"):
            break
    stream.close()  # the reader has gone: the server closes the response
    side.pipeline.gate.set()
    assert side.pipeline.closed.wait(20)  # the pipeline was stopped, and with it the model
    for thread in threading.enumerate():
        if thread.name == "sse":
            thread.join(20)
    return [[200, received.decode()], side.ask(Q)]


def signed_in(side: Side) -> list[Any]:
    """An account's own daily limit and its message; an account is never shown the note on costs."""
    side.sign_in()
    return [side.ask(Q), side.ask(Q2), side.ask(Q), side.ask(Q2, sources_only=True)]


def sign_in_required(side: Side) -> list[Any]:
    refused = [side.ask(Q), side.post("/api/search", {"question": Q}), side.get("/api/meta")[:1]]
    side.sign_in()
    return [*refused, side.ask(Q)]


def the_note_on_costs(side: Side) -> list[Any]:
    """After every second new briefing a visitor has had written today (the scenario's setting), this reader is
    sent the count with the final event; never from the cache, never in what is saved, and never again today once
    they have gone through to the donation page."""
    sent = [side.ask(Q), side.ask(Q2), side.ask(Q2)]
    sent.append(side.post("/api/support-clicked"))
    return [*sent, side.ask(Q3), side.ask(Q4)]


def _before_addresses(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in e.items() if k not in ("briefing_id", "prepared_at", "records_up_to")}
            if e.get("type") == "final" else e for e in events]


def cached_before_addresses(side: Side) -> list[Any]:
    """An answer cached before briefings had addresses is given one when it is next served, and keeps it."""
    first = side.ask(Q)
    side.edit_cache(_before_addresses)
    return [first, side.ask(Q), side.ask(Q)]


def cached_with_posts_in_government(side: Side) -> list[Any]:
    """An answer cached when posts in government were still listed among members' interests is served without
    them; the cached row itself is left as it is."""
    first = side.ask(Q)
    side.edit_cache(lambda events: [{**e, "interests": json.loads(json.dumps(INTERESTS))}
                                    if e.get("type") == "final" else e for e in events])
    return [first, side.ask(Q)]


def sources_only_while_the_model_cannot_read(side: Side) -> list[Any]:
    side.pipeline.plays.append("unread_alone")
    return [side.ask(Q, sources_only=True)]


AT_ONCE = "briefings_at_once_per_reader"
# Every case of wts-core's docs/adr/0009 "Parity", and what only this site has (docs/MIGRATION.md 1.8).
# name -> (what is done, has a model, settings for the telling: [usage] keys, and "auth" for sign-in required)
SCRIPTED: dict[str, tuple[Callable[[Side], list[Any]], bool, dict[str, Any]]] = {
    "a fresh question, and the same again from the cache": (
        lambda s: [s.ask(Q), s.ask("what has been said about  TRIAL by jury since 2020?")], True, {}),
    "with no hours to keep an answer, every question is written afresh": (
        lambda s: [s.ask(Q), s.ask(Q)], True, {"answer_cache_hours": 0}),
    "a follow-up is cached too": (
        lambda s: [s.ask(Q), s.ask(Q2, history=EARLIER, follows=Q), s.ask(Q2, history=EARLIER, follows=Q),
                   s.ask(Q2)], True, {}),
    "a question the model could not read": (lambda s: [s.ask(Q, "unread"), s.ask(Q)], True, {}),
    "a question whose reading failed with nothing said": (lambda s: [s.ask(Q, "unread_empty")], True, {}),
    "a briefing with no interests to check": (lambda s: [s.ask(Q, "no_interests"), s.ask(Q)], True, {}),
    "sources only: the model reads the question": (
        lambda s: [s.ask(Q, sources_only=True), s.ask(Q), s.ask(Q, sources_only=True)], True, {}),
    "sources only, from an edited plan": (
        lambda s: [s.ask(Q, sources_only=True, plan={"search_text": "jury", "filters": {"houses": ["lords"]}})],
        True, {}),
    "sources only, while the model cannot read": (sources_only_while_the_model_cannot_read, True, {}),
    "sources only, with the site's budget spent: the built-in rules read the question": (
        lambda s: [s.ask(Q, sources_only=True)], True, {"daily_spend_cap_usd": 0.0}),
    "the visitor's limit stops new briefings, and cached ones still load": (
        lambda s: [s.ask(Q), s.ask(Q2), s.ask(Q)], True, {"per_visitor_daily_questions": 1}),
    "the daily cap": (lambda s: [s.ask(Q)], True, {"daily_spend_cap_usd": 0.0}),
    "the monthly cap": (lambda s: [s.ask(Q)], True, {"monthly_spend_cap_usd": 0.0}),
    "too many requests in a minute": (
        lambda s: [s.ask(Q), s.ask(Q2), s.post("/api/search", {"question": Q}), s.post("/api/feedback", {
            "question": Q, "useful": True})], True, {"requests_per_minute": 1}),
    "one place too many for the reader": (
        lambda s: [*while_one_is_written(s, lambda: [s.ask(Q2)]), s.ask(Q2)], True, {AT_ONCE: 1}),
    "one place too many for the site": (
        lambda s: [*while_one_is_written(s, lambda: [s.ask(Q2)]), s.ask(Q2)], True,
        {AT_ONCE: 5, "briefings_at_once": 1}),
    "the second check of the caps counts the others in progress": (
        lambda s: while_one_is_written(s, lambda: [s.ask(Q2)]), True, {"daily_spend_cap_usd": 0.05}),
    "the pipeline raising mid-stream": (lambda s: [s.ask(Q, "raises"), s.ask(Q)], True, {AT_ONCE: 1}),
    "the model refusing": (lambda s: [s.ask(Q, "declined"), s.ask(Q)], True, {}),
    "no records": (lambda s: [s.ask(Q, "no_records"), s.ask(Q)], True, {}),
    "no API key": (lambda s: [s.ask(Q), s.ask(Q), s.ask(Q, sources_only=True)], False, {}),
    "the reader leaving while the briefing is written": (the_reader_leaves, True, {AT_ONCE: 1}),
    "a question too short, and one too long": (
        lambda s: [s.ask("Hi"), s.ask("x" * 2001), s.ask(Q, mode="everything"),
                   s.ask(Q, plan={"search_text": "x" * 20_001})], True, {}),
    # what only this site has
    "a signed-in account's own daily limit": (
        signed_in, True, {"per_user_daily_questions": 1, "support_reminder_every": 1}),
    "sign-in required": (sign_in_required, True, {"auth": True}),
    "the note on what briefings cost": (the_note_on_costs, True, {"support_reminder_every": 2}),
    "an answer cached before briefings had addresses": (cached_before_addresses, True, {}),
    "an answer cached with posts in government among the interests": (cached_with_posts_in_government, True, {}),
}


def scripted_asks(make: Callable[..., Side], only: str | None = None) -> dict[str, Any]:
    """Each scripted case played on a side of its own: what was sent, what was written, what the pipeline did, and
    each saved briefing as its address serves it (one object, judged again as it is shown).
    ``make(has_model=…, auth=…, **usage)`` gives the side."""
    out = {}
    for name, (scenario, has_model, settings) in SCRIPTED.items():
        if only and name != only:
            continue
        usage = {k: v for k, v in settings.items() if k != "auth"}
        side = make(has_model=has_model, auth=bool(settings.get("auth")), **usage)
        side.latest_record()
        sent = scenario(side)
        rows = side.rows()
        out[name] = {"sent": sent, "rows": rows, "calls": side.calls(),
                     "saved": {row[0]: side.get(f"/api/briefings/{row[0]}") for row in rows["briefings"]},
                     "status": {k: v for k, v in (side.get("/api/status")[1] or {}).items()
                                if k in ("briefings", "briefings_note", "latest_record", "detail")}}
    return out


# ---- the same through the site's real pipeline, over the tests' own small index ------------------------------------


class FakeStream:
    """The SDK's stream of one message, as the site's own tests fake it."""

    def __init__(self, blocks: list[dict[str, Any]], stop_reason: str = "end_turn") -> None:
        self.blocks, self.stop_reason = blocks, stop_reason

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *a: Any) -> bool:
        return False

    def __iter__(self) -> Iterator[Any]:
        for b in self.blocks:
            yield NS(type="content_block_delta", delta=NS(type="text_delta", text=b["text"]))
            for c in b.get("citations", []):
                yield NS(type="content_block_delta", delta=NS(type="citations_delta", citation=NS(**c)))

    def get_final_message(self) -> Any:
        content = [NS(type="text", text=b["text"], citations=[NS(type="search_result_location", **c)
                                                              for c in b.get("citations", [])])
                   for b in self.blocks]
        return NS(content=content, stop_reason=self.stop_reason, model="claude-sonnet-5-5",
                  usage=NS(input_tokens=1000, output_tokens=200, cache_read_input_tokens=400,
                           cache_creation_input_tokens=300))


class FakeSDK:
    """In place of the Anthropic SDK's client. It reads a question as ``reading`` says (or fails to, when that is
    None), and answers by citing the first block of the first source it is given, then adds a sentence with a
    judgemental word and an American spelling. What it was asked is kept in ``calls`` (briefings) and ``parses``."""

    def __init__(self, reading: dict[str, Any] | None = None, refuse: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.parses: list[dict[str, Any]] = []
        self.reading, self.refuse = reading, refuse
        self.beta = NS(messages=NS(stream=self._stream))
        self.messages = NS(stream=self._stream, parse=self._parse)

    def _parse(self, **kw: Any) -> Any:
        self.parses.append(kw)
        if self.reading is None or kw["output_format"].__name__ != "ParsedQuery":
            raise RuntimeError("force offline parse in tests")
        return NS(stop_reason="end_turn", parsed_output=kw["output_format"](**self.reading),
                  usage=NS(input_tokens=1500, output_tokens=90, cache_read_input_tokens=0,
                           cache_creation_input_tokens=1100))

    def _stream(self, **kw: Any) -> FakeStream:
        self.calls.append(kw)
        if self.refuse:
            return FakeStream([], stop_reason="refusal")
        first = next(b for b in kw["messages"][0]["content"] if b["type"] == "search_result")
        quote = first["content"][0]["text"]
        return FakeStream([
            {"text": "According to the record, \""},
            {"text": quote[:80], "citations": [{"search_result_index": 0, "cited_text": quote, "source": "x"}]},
            {"text": "\" The minister admitted that the organization was at fault."},
        ])


RQ = "What has been said about trial by jury?"
RQ2 = "And in the Lords?"
R_EARLIER = [{"question": RQ, "answer": "According to the record, juries were defended.", "topic": "trial by jury",
              "scope": ""}]
READING = {"search_text": "trial by jury", "date_from": None, "date_to": None, "houses": [], "regions": [],
           "parties": [], "speakers": [], "offices": [], "genders": [], "age_bands": [], "tenure": [],
           "majority_bands": [], "seat_types": [], "runner_up_parties": [], "groups": [], "group_by": "role",
           "portfolios": ["justice"], "wants_votes": False,
           "interpretation": "Debates on \"trial by jury\", with the color of each party's view."}
PLAN_NOTHING = {"search_text": "jury", "filters": {"date_from": "2011-01-01", "date_to": "2011-01-02"}}

# name -> (the SDK, the requests, [usage] settings)
REAL: dict[str, tuple[Callable[[], FakeSDK | None], list[dict[str, Any]], dict[str, Any]]] = {
    "a briefing, the same again, a follow-up, sources only": (
        lambda: FakeSDK(READING),
        [{"question": RQ}, {"question": RQ}, {"question": RQ2, "history": R_EARLIER, "follows": RQ},
         {"question": RQ, "sources_only": True},
         {"question": RQ, "mode": "comprehensive", "recency": 0.9, "scope": {"votes": False}}], {}),
    "the model cannot read the question": (lambda: FakeSDK(None), [{"question": RQ}, {"question": RQ}], {}),
    "the model declines": (lambda: FakeSDK(READING, refuse=True), [{"question": RQ}, {"question": RQ}], {}),
    "no records": (lambda: FakeSDK(READING), [{"question": "anything", "plan": PLAN_NOTHING},
                                              {"question": "anything", "plan": PLAN_NOTHING}], {}),
    "an edited plan": (lambda: FakeSDK(READING), [
        {"question": "security", "plan": {"search_text": "security", "filters": {"houses": ["lords"]}},
         "scope": {"written": False, "votes": False}}], {}),
    "a comparison of two parties": (lambda: FakeSDK(READING), [
        {"question": RQ, "compare": [{"kind": "party", "id": "labour"}, {"kind": "party", "id": "conservative"}]}],
        {}),
    "no API key": (lambda: None, [{"question": RQ}, {"question": RQ, "sources_only": True}], {}),
    "the daily cap": (lambda: FakeSDK(READING), [{"question": RQ}], {"daily_spend_cap_usd": 0.0}),
}


def sdk_calls(sdk: FakeSDK | None) -> list[Any]:
    """What the model was sent, whole: a change to a request is a change a reader could notice."""
    if sdk is None:
        return []
    return [{"parse": {k: (v.__name__ if k == "output_format" else v) for k, v in kw.items()}} for kw in sdk.parses] \
        + [{"stream": kw} for kw in sdk.calls]


def real_asks(make: Callable[..., Side], only: str | None = None) -> dict[str, Any]:
    """Each case through the site's real pipeline. ``make(sdk, **usage)`` gives the side."""
    out = {}
    for name, (make_sdk, requests, usage) in REAL.items():
        if only and name != only:
            continue
        sdk = make_sdk()
        side = make(sdk, **usage)
        sent = []
        for body in requests:
            r = side.client.post("/api/ask", json=body)
            sent.append([r.status_code, r.content.decode()])
        rows = side.rows()
        out[name] = {"sent": sent, "rows": rows, "calls": sdk_calls(sdk),
                     "saved": {row[0]: side.get(f"/api/briefings/{row[0]}") for row in rows["briefings"]},
                     "search": side.post("/api/search", requests[0]),
                     "status": {k: v for k, v in side.get("/api/status")[1].items()
                                if k in ("briefings", "briefings_note", "latest_record", "passages")}}
    return out


def events_of(body: str) -> list[Any]:
    out = []
    for chunk in body.split("\n\n"):
        data = [line[6:] for line in chunk.splitlines() if line.startswith("data: ")]
        out.append(json.loads(data[0]) if data else chunk)
    return out


# ---- a simulated Backblaze B2 --------------------------------------------------------------------------------------


class FakeB2:
    def __init__(self, bucket: str = "wts-backups", fail_uploads: int = 0, can_delete: bool = True) -> None:
        self.bucket, self.fail_uploads, self.can_delete = bucket, fail_uploads, can_delete
        self.files: dict[str, bytes] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("b2_authorize_account"):
            if request.headers.get("authorization") != httpx.BasicAuth("kid", "secret")._auth_header:
                return httpx.Response(401)
            return httpx.Response(200, json={"accountId": "acc", "apiUrl": "https://api.b2.test",
                                             "authorizationToken": "tok",
                                             "allowed": {"bucketId": "b1", "bucketName": self.bucket}})
        assert request.headers.get("authorization") in ("tok", "up"), "every call carries a token"
        body = json.loads(request.content or b"{}") if path.startswith("/b2api") else {}
        if path.endswith("b2_get_upload_url"):
            return httpx.Response(200, json={"uploadUrl": "https://up.b2.test/upload", "authorizationToken": "up"})
        if path == "/upload":
            if self.fail_uploads:
                self.fail_uploads -= 1
                return httpx.Response(503)
            assert request.headers["x-bz-content-sha1"] == hashlib.sha1(request.content).hexdigest()  # noqa: S324
            self.files[request.headers["x-bz-file-name"]] = request.content
            return httpx.Response(200, json={"fileName": request.headers["x-bz-file-name"]})
        if path.endswith("b2_list_file_names"):
            return httpx.Response(200, json={"files": [{"fileName": n, "fileId": f"id-{n}"} for n in sorted(self.files)
                                                       if n.startswith(body["prefix"])]})
        if path.endswith("b2_delete_file_version"):
            if not self.can_delete:
                return httpx.Response(401, json={"code": "unauthorized"})
            del self.files[body["fileName"]]
            return httpx.Response(200, json={})
        return httpx.Response(404)


B2_ENV = {"HANSARD_B2_BUCKET": "wts-backups", "HANSARD_B2_KEY_ID": "kid", "HANSARD_B2_APPLICATION_KEY": "secret"}
PREFIX = "where-they-stand-"


def b2_client(b2: FakeB2) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(b2))


def offsite_scenario(tmp: Path, *, send: Callable[..., dict[str, Any]], check: Callable[..., dict[str, Any]],
                     error: type[Exception]) -> list[dict[str, Any]]:
    """Off-site copies: ``send(archive, keep, env, client)`` and ``check(env, client)``, at a fixed time."""
    out: list[dict[str, Any]] = []

    def archive(folder: Path, day: str) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / f"{PREFIX}{day}.tar.gz"
        p.write_bytes(f"backup {day}".encode())
        return p

    def step(name: str, folder: Path, do: Callable[[], Any], b2: FakeB2 | None = None) -> None:
        try:
            result: Any = do()
        except error as exc:
            result = {"raised": str(exc)}
        status = folder / "offsite.json"
        out.append({"case": name, "result": result, "stored": sorted(b2.files) if b2 else None,
                    "offsite.json": json.loads(status.read_text()) if status.exists() else None})

    f = tmp / "not-set-up"
    step("not set up: nothing is sent", f, lambda: send(archive(f, "2026-11-01"), 60, {}, None))
    step("not set up: the check says how", f, lambda: check({}, None))
    b2, f = FakeB2(fail_uploads=2), tmp / "retries"
    for day in ("2026-11-01", "2026-11-02", "2026-11-03"):
        step(f"a copy is stored and the newest two kept ({day})", f,
             lambda day=day, b2=b2, f=f: send(archive(f, day), 2, B2_ENV, b2_client(b2)), b2)
    step("the check counts the copies", f, lambda: check(B2_ENV, b2_client(b2)), b2)
    b2, f = FakeB2(can_delete=False), tmp / "no-delete"
    for day in ("2026-11-01", "2026-11-02"):
        step(f"a key that cannot delete still stores ({day})", f,
             lambda day=day, b2=b2, f=f: send(archive(f, day), 1, B2_ENV, b2_client(b2)), b2)
    b2, f = FakeB2(fail_uploads=5), tmp / "fails"
    step("an upload that keeps failing is reported and recorded", f,
         lambda: send(archive(f, "2026-11-01"), 60, B2_ENV, b2_client(b2)), b2)
    step("a key that is refused", f,
         lambda: send(archive(f, "2026-11-02"), 60, {**B2_ENV, "HANSARD_B2_KEY_ID": "wrong"}, b2_client(b2)), b2)
    step("a key for another bucket", f,
         lambda: send(archive(f, "2026-11-03"), 60, {**B2_ENV, "HANSARD_B2_BUCKET": "another"}, b2_client(b2)), b2)
    b2, f = FakeB2(), tmp / "shared"
    b2.files["wcs-state-2026-11-01.sqlite.gz"] = b"another site's"
    for day in ("2026-11-01", "2026-11-02"):
        step(f"sites sharing a bucket keep their own ({day})", f,
             lambda day=day, b2=b2, f=f: send(archive(f, day), 1, B2_ENV, b2_client(b2)), b2)
    step("the check counts this site's only", f, lambda: check(B2_ENV, b2_client(b2)), b2)
    return out


# ---- backups -------------------------------------------------------------------------------------------------------


def listing(folder: Path) -> list[dict[str, Any]]:
    """Each file in a folder: its name, who may read it, and what is inside (a tar's members, with the tables and
    rows of the database among them and the text of the settings; or a JSON file's value)."""
    out = []
    for p in sorted(folder.iterdir()) if folder.is_dir() else []:
        entry: dict[str, Any] = {"name": p.name, "mode": oct(stat.S_IMODE(p.stat().st_mode))}
        if p.name.endswith(".tar.gz"):
            try:
                entry["holds"] = tar_holds(p, folder)
            except (OSError, EOFError, tarfile.TarError, sqlite3.DatabaseError) as exc:
                entry["holds"] = type(exc).__name__
        elif p.suffix == ".json":
            entry["holds"] = json.loads(p.read_text())
        out.append(entry)
    return out


def tar_holds(archive: Path, scratch: Path) -> dict[str, Any]:
    holds: dict[str, Any] = {}
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            data = tar.extractfile(member).read()  # type: ignore[union-attr]
            holds[member.name] = database_holds(data, scratch) if member.name.endswith(".sqlite") else data.decode()
    return holds


def database_holds(data: bytes | Path, scratch: Path) -> dict[str, Any]:
    """The rows of every table of the site's own in a database (given as its bytes, or as a file to be read where
    it is, with its write-ahead log if it has one)."""
    tmp = None
    if isinstance(data, bytes):
        tmp = scratch / ".reading.sqlite"
        tmp.write_bytes(data)
    con = sqlite3.connect(tmp or data)
    try:
        tables = sorted(r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT "
                                                  "LIKE 'sqlite_%' AND name NOT LIKE 'wts_%'"))
        return {t: [list(r) for r in con.execute(f"SELECT * FROM {t} ORDER BY 1")] for t in tables}
    finally:
        con.close()
        if tmp:
            tmp.unlink()


def a_state_database(path: Path, marker: str) -> Path:
    """A state database with the tables every backup of this site has had, and one row to tell it by."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript("CREATE TABLE IF NOT EXISTS manifest (source TEXT, key TEXT);"
                      "CREATE TABLE IF NOT EXISTS sync_log (run_id INTEGER PRIMARY KEY, kind TEXT);"
                      "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                      "CREATE TABLE IF NOT EXISTS usage (id INTEGER PRIMARY KEY, cost REAL);"
                      "CREATE TABLE IF NOT EXISTS briefings (id TEXT PRIMARY KEY);"
                      "CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY);")
    con.execute("INSERT OR REPLACE INTO kv (key, value) VALUES ('marker', ?)", (marker,))
    con.commit()
    con.close()
    return path


def tar_of(path: Path, **members: bytes) -> Path:
    """A .tar.gz holding the given files (``a__b`` names ``a.b``), for the cases a backup would never write."""
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name.replace("__", "."))
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def backups_scenario(tmp: Path, *, run: Callable[[list[str]], tuple[int, str]],
                     set_day: Callable[[str], None], environment: Callable[..., None],
                     alerts: Callable[[Path, dict[str, str], str], list[dict[str, str]]]) -> dict[str, Any]:
    """The owner's backup commands, word for word, and the alerts for each state of the backups' folder.
    ``run(args)`` runs the site's command line; ``set_day("2026-11-01")`` sets the day a backup is named by;
    ``environment(data_dir, **more)`` sets the site's environment; ``alerts(folder, env, now)`` gives the alerts."""
    out: dict[str, Any] = {"commands": [], "alerts": []}
    data, to = tmp / "data", tmp / "backups"
    db = data / "state.sqlite"

    def command(case: str, args: list[str]) -> None:
        code, text = run(args)
        out["commands"].append({"case": case, "args": plain(args, tmp=tmp), "exit": code,
                                "output": plain(text, tmp=tmp), "backups": plain(listing(to), tmp=tmp)})

    def files() -> list[str]:
        return sorted(p.name for p in data.iterdir() if not p.name.endswith("-shm"))

    environment(data)
    set_day("2026-11-01")
    command("a new site has nothing to back up", ["backup", "--to", str(to)])
    command("the off-site check, not set up", ["offsite"])
    a_state_database(db, "day one")
    for n, day in enumerate(("2026-11-01", "2026-11-02", "2026-11-02", "2026-11-03")):
        set_day(day)
        a_state_database(db, f"backup {n}")
        if n == 1:  # the local settings go into the backup from the day they exist
            (data / "settings.local.toml").write_text('[archive]\nstart_date = "2015-01-01"\n')
        command(f"a backup on {day}, keeping two", ["backup", "--to", str(to), "--keep", "2"])
    command("a backup with nothing said of how many to keep", ["backup", "--to", str(to)])
    # restoring: the database being replaced has its newest write still in its write-ahead log
    a_state_database(db, "before the restore")
    (data / "settings.local.toml").write_text('[archive]\nstart_date = "2020-01-01"\n')
    live = sqlite3.connect(db)
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("UPDATE kv SET value = 'in the log only' WHERE key = 'marker'")
    live.commit()  # the connection stays open, as when the site is killed: the write is in the log only
    (data / "state.sqlite-journal").write_bytes(b"left over")
    command("restore, refused at the question", ["restore", str(to / f"{PREFIX}2026-11-02.tar.gz")])
    command("restore a backup", ["restore", str(to / f"{PREFIX}2026-11-02.tar.gz"), "--yes"])
    out["restored"] = plain({"state.sqlite": database_holds(db, tmp),
                             "kept": database_holds(data / "state.sqlite.before-restore", tmp),
                             "settings.local.toml": (data / "settings.local.toml").read_text(),
                             "mode": oct(stat.S_IMODE(db.stat().st_mode)), "files": files()}, tmp=tmp)
    live.close()
    command("a second restore never writes over the kept copy",
            ["restore", str(to / f"{PREFIX}2026-11-03.tar.gz"), "--yes"])
    for kept in data.glob("state.sqlite.before-restore*"):
        kept.unlink()
    command("a backup that is not there", ["restore", str(to / "missing.tar.gz"), "--yes"])
    (to / "damaged.tar.gz").write_bytes(b"not a tar at all")
    command("a damaged backup", ["restore", str(to / "damaged.tar.gz"), "--yes"])
    tar_of(to / "no-database.tar.gz", settings__local__toml=b"[archive]\n")
    command("a backup with no database in it", ["restore", str(to / "no-database.tar.gz"), "--yes"])
    tar_of(to / "text.tar.gz", state__sqlite=b"plainly not a database, however long it goes on " * 40)
    command("a backup whose database is not one", ["restore", str(to / "text.tar.gz"), "--yes"])
    other = sqlite3.connect(tmp / "other.sqlite")
    other.execute("CREATE TABLE something (x)")
    other.commit()
    other.close()
    tar_of(to / "foreign.tar.gz", state__sqlite=(tmp / "other.sqlite").read_bytes())
    command("another database altogether", ["restore", str(to / "foreign.tar.gz"), "--yes"])
    good = (tmp / "good.sqlite")
    a_state_database(good, "repacked by hand")
    with tarfile.open(to / "repacked.tar.gz", "w:gz") as tar:  # as `tar czf x.tar.gz ./state.sqlite` names it
        tar.add(good, arcname="./state.sqlite")
    command("a backup repacked by hand", ["restore", str(to / "repacked.tar.gz"), "--yes"])
    out["after"] = plain({"state.sqlite": database_holds(db, tmp), "files": files(),
                          "settings.local.toml": (data / "settings.local.toml").read_text()}, tmp=tmp)
    for extra in ("damaged.tar.gz", "no-database.tar.gz", "text.tar.gz", "foreign.tar.gz", "repacked.tar.gz"):
        (to / extra).unlink()

    # the alerts, for each state of the folder
    now = "2026-11-06T12:00:00+00:00"
    seconds = datetime.fromisoformat(now).timestamp()

    def alert(case: str, folder: Path, env: dict[str, str]) -> None:
        out["alerts"].append({"case": case, "alerts": alerts(folder, env, now)})

    alert("no backups folder", tmp / "none", {})
    folder = tmp / "alerts"
    folder.mkdir()
    alert("an empty folder, no off-site copy set up", folder, {})
    alert("an empty folder, off-site set up", folder, B2_ENV)
    newest = folder / f"{PREFIX}2026-11-01.tar.gz"
    newest.write_bytes(b"x")
    os.utime(newest, (seconds - 3 * 86400, seconds - 3 * 86400))
    alert("a backup three days old, no off-site copy yet", folder, B2_ENV)
    alert("a backup three days old, on this server only", folder, {})
    os.utime(newest, (seconds - 50 * 3600, seconds - 50 * 3600))
    alert("a backup exactly fifty hours old", folder, B2_ENV)
    os.utime(newest, (seconds - 3 * 3600, seconds - 3 * 3600))
    (folder / "offsite.json").write_text(json.dumps({"ok": False, "when": "2026-11-06T03:00:00+00:00",
                                                     "error": "Backblaze did not accept the key"}))
    alert("the last off-site copy failed", folder, B2_ENV)
    (folder / "offsite.json").write_text(json.dumps({"ok": True, "when": "2026-11-02T03:00:00+00:00"}))
    alert("the last off-site copy is four days old", folder, B2_ENV)
    (folder / "offsite.json").write_text(json.dumps({"ok": True, "when": "2026-11-06T03:00:00+00:00"}))
    alert("all is well", folder, B2_ENV)
    (folder / "offsite.json").write_text("not json")
    alert("a record of the off-site copy that cannot be read", folder, B2_ENV)
    return out


# ---- reports, statements, saved briefings: what is sent, and what the owner types ------------------------------

WORDS = "I have always supported the right to trial by jury, and the record of my speeches shows it in full."
BRIEFING_1, BRIEFING_2, WITHDRAWN = "briefingaa", "briefingab", "briefingac"
MEMBER, NO_MEMBER = "/people/10669", "/people/99999999"   # Hilary Benn, in the tests' own data; and nobody
STATEMENT = {"item": f"/b/{BRIEFING_1}", "name": "  Hilary   Benn ", "role": "MP for Leeds  South",
             "text": f"  {WORDS}\r\n\r\n\r\nSecond paragraph.  ", "link": "", "email": " office@example.org "}
LONG_WORDS = " ".join(["word"] * 151)
LONG_CHARS = " ".join(["extraordinarily"] * 130)   # within 150 words and 2,000 characters; the form allows it
REPORT = {"kind": "correction", "message": "  The date of the debate is wrong.  "}
ADMIN = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

HTTP_STEPS: list[dict[str, Any]] = [
    # reports: ten are kept from one visitor in a day, and the eleventh is refused
    {"case": "a report of something wrong in a briefing", "post": "/api/report",
     "json": {**REPORT, "briefing_id": BRIEFING_1, "contact": " reader@example.org "}},
    {"case": "a report about a page of the site", "post": "/api/report",
     "json": {"kind": "complaint", "message": "A loaded word about a member.",
              "page": "https://wherempsstand.com/people/10669/hilary-benn?x=1#votes"}},
    {"case": "a takedown request", "post": "/api/report",
     "json": {"kind": "takedown", "page": "/debates/2021-03-04/commons/trial-by-jury",
              "message": "Please remove my name from this page.", "contact": "named@example.org"}},
    {"case": "a report about a page of another site: the page is not kept", "post": "/api/report",
     "json": {"kind": "general", "message": "Something else entirely.", "page": "https://example.org/x"}},
    {"case": "a report with a briefing and a page: the briefing is kept", "post": "/api/report",
     "json": {**REPORT, "briefing_id": BRIEFING_2, "page": "/topics/justice"}},
    {"case": "a report with a briefing id that is not one", "post": "/api/report",
     "json": {**REPORT, "briefing_id": "not-an-id"}},
    {"case": "a report from a robot, which fills the hidden field", "post": "/api/report",
     "json": {**REPORT, "website": "https://spam.example"}},
    {"case": "a report of a kind there is not", "post": "/api/report", "json": {"kind": "praise", "message": "Lovely site."}},
    {"case": "a report with no kind", "post": "/api/report", "json": {"message": "Something is wrong here."}},
    {"case": "a report too short", "post": "/api/report", "json": {"kind": "correction", "message": "No"}},
    {"case": "a report of spaces", "post": "/api/report", "json": {"kind": "correction", "message": " " * 12}},
    {"case": "a report too long", "post": "/api/report", "json": {"kind": "correction", "message": "x" * 4001}},
    {"case": "a report with a contact too long", "post": "/api/report", "json": {**REPORT, "contact": "c" * 201}},
    {"case": "a report with a page too long", "post": "/api/report", "json": {**REPORT, "page": "/" + "p" * 300}},
    *({"case": f"report {n} of the day", "post": "/api/report", "json": {**REPORT, "message": f"Report number {n}."}}
      for n in range(8, 11)),
    {"case": "the eleventh report of the day", "post": "/api/report", "json": REPORT},
    # statements: five are kept from one visitor in a day, and the sixth is refused
    {"case": "a statement beside a briefing", "post": "/api/statements", "json": STATEMENT},
    {"case": "a statement by a member's page, with its name and a link", "post": "/api/statements",
     "json": {**STATEMENT, "item": "https://www.wherempsstand.com/people/10669/hilary-benn/", "name": "Second Sender",
              "link": " https://hansard.parliament.uk/commons/2021-03-04 ", "text": "Five words are just enough."}},
    {"case": "a statement by an address with no scheme", "post": "/api/statements",
     "json": {**STATEMENT, "item": f"wherempsstand.com/b/{BRIEFING_2}", "name": "Third Sender"}},
    {"case": "a statement beside a debate, which is not looked up", "post": "/api/statements",
     "json": {**STATEMENT, "item": "/debates/2021-03-04/commons/trial-by-jury", "name": "Fourth Sender"}},
    {"case": "a statement from a robot, which fills the hidden field", "post": "/api/statements",
     "json": {**STATEMENT, "website": "https://spam.example"}},
    {"case": "a statement with no address", "post": "/api/statements", "json": {**STATEMENT, "item": "  "}},
    {"case": "a statement beside another site's page", "post": "/api/statements",
     "json": {**STATEMENT, "item": f"https://example.org/b/{BRIEFING_1}"}},
    {"case": "a statement beside a page no statement can sit beside", "post": "/api/statements",
     "json": {**STATEMENT, "item": "/about"}},
    {"case": "a statement beside a briefing there is not", "post": "/api/statements",
     "json": {**STATEMENT, "item": "/b/briefingzz"}},
    {"case": "a statement beside a withdrawn briefing", "post": "/api/statements",
     "json": {**STATEMENT, "item": f"/b/{WITHDRAWN}"}},
    {"case": "a statement beside a member's page there is not", "post": "/api/statements",
     "json": {**STATEMENT, "item": NO_MEMBER}},
    {"case": "a statement with a name of one letter", "post": "/api/statements", "json": {**STATEMENT, "name": " A "}},
    {"case": "a statement with no role", "post": "/api/statements", "json": {**STATEMENT, "role": "  "}},
    {"case": "a statement with no words", "post": "/api/statements", "json": {**STATEMENT, "text": "   "}},
    {"case": "a statement of four words", "post": "/api/statements", "json": {**STATEMENT, "text": "Only four words here."}},
    {"case": "a statement of 151 words", "post": "/api/statements", "json": {**STATEMENT, "text": LONG_WORDS}},
    {"case": "a statement within 150 words, of long ones", "post": "/api/statements",
     "json": {**STATEMENT, "text": LONG_CHARS, "name": "Fifth Sender"}},
    {"case": "a statement with a link that is not a web address", "post": "/api/statements",
     "json": {**STATEMENT, "link": "javascript:alert(1)"}},
    {"case": "a statement with a link with a quote in it", "post": "/api/statements",
     "json": {**STATEMENT, "link": "https://example.org/\"onmouseover=\"x"}},
    {"case": "a statement with no email address", "post": "/api/statements", "json": {**STATEMENT, "email": "nobody"}},
    {"case": "a statement with two things wrong: the name and the words", "post": "/api/statements",
     "json": {**STATEMENT, "name": " A ", "text": LONG_WORDS}},
    {"case": "a statement with two things wrong: the address and the words", "post": "/api/statements",
     "json": {**STATEMENT, "item": "/about", "text": LONG_WORDS}},
    {"case": "a statement with a name too long for the form", "post": "/api/statements",
     "json": {**STATEMENT, "name": "n" * 121}},
    {"case": "a statement with text too long for the form", "post": "/api/statements",
     "json": {**STATEMENT, "text": "x" * 2001}},
    {"case": "a statement with a field missing", "post": "/api/statements",
     "json": {k: v for k, v in STATEMENT.items() if k != "email"}},
    {"case": "the fifth statement of the day", "post": "/api/statements", "json": {**STATEMENT, "name": "Fifth Sender"}},
    {"case": "the sixth statement of the day", "post": "/api/statements", "json": STATEMENT},
    # what the public may read, before anything is published
    {"case": "no statement is shown while it waits", "get": f"/api/statements?path=/b/{BRIEFING_1}"},
    {"case": "statements asked for with nothing said", "get": "/api/statements"},
    {"case": "statements beside something that is not an item", "get": "/api/statements?path=/about"},
    {"case": "a saved briefing", "get": f"/api/briefings/{BRIEFING_1}"},
    {"case": "a withdrawn briefing", "get": f"/api/briefings/{WITHDRAWN}"},
    {"case": "a briefing there is not", "get": "/api/briefings/briefingzz"},
    {"case": "an address that is not a briefing's", "get": "/api/briefings/Not-An-Id"},
    {"case": "the corrections log", "get": "/api/corrections"},
    {"case": "a saved briefing's page", "get": f"/b/{BRIEFING_1}", "page": True},
    {"case": "a withdrawn briefing's page", "get": f"/b/{WITHDRAWN}", "page": True},
    {"case": "the page of a briefing there is not", "get": "/b/briefingzz", "page": True},
    # the owner's pages: only with the token
    {"case": "the owner's list of statements, with no token", "get": "/api/admin/statements"},
    {"case": "the owner's list of statements", "get": "/api/admin/statements", "admin": True},
    {"case": "the owner's list of statements waiting", "get": "/api/admin/statements?status=pending", "admin": True},
    {"case": "the owner's list of reports", "get": "/api/admin/reports", "admin": True},
    {"case": "the owner's list of open reports", "get": "/api/admin/reports?status=open", "admin": True},
    {"case": "the owner's list of saved briefings", "get": "/api/admin/briefings", "admin": True},
]

# The owner's commands as they were typed before wts-core, in order, on the rows the steps above left.
CLI_STEPS: list[dict[str, Any]] = [
    {"case": "the statements waiting", "args": ["statements", "list"]},
    {"case": "publish with nothing said of how the sender was confirmed", "args": ["statements", "publish", "1"]},
    {"case": "publish with an empty note", "args": ["statements", "publish", "1", "--checked", "  "]},
    {"case": "publish", "args": ["statements", "publish", "1", "--checked",
                                 "Replied from their parliament.uk address, 14 November.", "--person", "10669"]},
    {"case": "publish it again", "args": ["statements", "publish", "1", "--checked", "Again."]},
    {"case": "publish one there is not", "args": ["statements", "publish", "99", "--checked", "x"]},
    {"case": "decline", "args": ["statements", "decline", "3", "--reason", "It is about another member's conduct."]},
    {"case": "decline with no reason", "args": ["statements", "decline", "4"]},
    {"case": "decline with a reason of spaces", "args": ["statements", "decline", "4", "--reason", " "]},
    {"case": "decline one already declined", "args": ["statements", "decline", "3", "--reason", "Again."]},
    {"case": "update the words of a published statement", "args": [
        "statements", "update", "1", "--text-file", "<words>"]},
    {"case": "update with too many words", "args": ["statements", "update", "1", "--text-file", "<long>"]},
    {"case": "update the name, the role and the link", "args": [
        "statements", "update", "1", "--name", "Hilary Benn MP", "--role", "MP for Leeds South", "--link",
        "https://hansard.parliament.uk/commons/2021-03-05"]},
    {"case": "update with a bad link", "args": ["statements", "update", "1", "--link", "ftp://example.org/x"]},
    {"case": "take the link off", "args": ["statements", "update", "1", "--link", ""]},
    {"case": "update with nothing to change", "args": ["statements", "update", "1"]},
    {"case": "update a declined statement", "args": ["statements", "update", "3", "--name", "Someone Else"]},
    {"case": "publish the second, which is by the member's own page", "args": [
        "statements", "publish", "2", "--checked", "Telephoned the office."]},
    {"case": "withdraw a statement, at its author's request", "args": [
        "statements", "withdraw", "2", "--note", "Asked by email on 14 November."]},
    {"case": "withdraw it again", "args": ["statements", "withdraw", "2"]},
    {"case": "publish the fourth", "args": ["statements", "publish", "4", "--checked", "Replied from their office."]},
    {"case": "take down a published statement that breaks the rules, as it was typed before wts-core", "args": [
        "statements", "decline", "4", "--reason", "It names a private person."]},
    {"case": "the statements waiting, now", "args": ["statements", "list"]},
    {"case": "the published statements", "args": ["statements", "list", "--status", "published"]},
    {"case": "every statement", "args": ["statements", "list", "--status", "all"]},
]

ADMIN_STEPS: list[dict[str, Any]] = [
    {"case": "close a report with a public note", "post": "/api/admin/reports/1/close", "admin": True,
     "json": {"status": "resolved", "resolution": "The date was wrong; the briefing is withdrawn.",
              "public_note": "  A briefing gave the wrong date for a debate and has been withdrawn.  "}},
    {"case": "close a report with no public note", "post": "/api/admin/reports/2/close", "admin": True,
     "json": {"status": "dismissed", "resolution": "The word is the member's own."}},
    {"case": "close a report there is not", "post": "/api/admin/reports/99/close", "admin": True,
     "json": {"status": "resolved", "resolution": "Nothing."}},
    {"case": "close a report with an outcome there is not", "post": "/api/admin/reports/3/close", "admin": True,
     "json": {"status": "ignored", "resolution": "Nothing."}},
    {"case": "close a report with no token", "post": "/api/admin/reports/3/close",
     "json": {"status": "resolved", "resolution": "Nothing."}},
    {"case": "withdraw a briefing", "post": f"/api/admin/briefings/{BRIEFING_2}/withdraw", "admin": True,
     "json": {"reason": "It gave the wrong date for a debate."}},
    {"case": "withdraw a briefing there is not", "post": "/api/admin/briefings/briefingzz/withdraw", "admin": True,
     "json": {"reason": "Nothing."}},
    {"case": "publish with a note of spaces", "post": "/api/admin/statements/5/publish", "admin": True,
     "json": {"checked": "   "}},
    {"case": "publish the fifth statement from the owner's page", "post": "/api/admin/statements/5/publish",
     "admin": True, "json": {"checked": "Replied from their office.", "person": 10669}},
    {"case": "publish it again from the owner's page", "post": "/api/admin/statements/5/publish",
     "admin": True, "json": {"checked": "Again."}},
    {"case": "take down a published statement from the owner's page", "post": "/api/admin/statements/1/decline",
     "admin": True, "json": {"reason": "It names a private person."}},
    {"case": "decline with a reason of spaces", "post": "/api/admin/statements/5/decline", "admin": True,
     "json": {"reason": "    "}},
    {"case": "update it from the owner's page", "post": "/api/admin/statements/5/update", "admin": True,
     "json": {"text": "New words from the owner's page, as sent.", "link": "https://example.org/record"}},
    {"case": "update it with too few words", "post": "/api/admin/statements/5/update", "admin": True,
     "json": {"text": "Too few."}},
    {"case": "take its link off", "post": "/api/admin/statements/5/update", "admin": True, "json": {"link": ""}},
    {"case": "decline one there is not", "post": "/api/admin/statements/99/decline", "admin": True,
     "json": {"reason": "Nothing."}},
    {"case": "withdraw one already withdrawn", "post": "/api/admin/statements/2/withdraw", "admin": True, "json": {}},
    {"case": "withdraw the fifth from the owner's page", "post": "/api/admin/statements/5/withdraw", "admin": True,
     "json": {"note": "Asked by telephone."}},
]

AFTER_STEPS: list[dict[str, Any]] = [
    {"case": "the published statement beside its briefing", "get": f"/api/statements?path=/b/{BRIEFING_1}"},
    {"case": "the same by the briefing's full address", "get":
     f"/api/statements?path=https://wherempsstand.com/b/{BRIEFING_1}"},
    {"case": "a member's statements", "get": "/api/statements?person=10669"},
    {"case": "a briefing's and a member's together", "get": f"/api/statements?path=/b/{BRIEFING_1}&person=10669"},
    {"case": "another member's", "get": "/api/statements?person=10115"},
    {"case": "a briefing with no statement shown", "get": f"/api/statements?path=/b/{BRIEFING_2}"},
    {"case": "the corrections log, with a note and two withdrawn", "get": "/api/corrections"},
    {"case": "the corrections feed", "get": "/feeds/corrections.xml", "text": True},
    {"case": "the briefing withdrawn just now", "get": f"/api/briefings/{BRIEFING_2}"},
    {"case": "the owner's list of reports, now", "get": "/api/admin/reports", "admin": True},
    {"case": "the owner's list of closed reports", "get": "/api/admin/reports?status=resolved", "admin": True},
    {"case": "the owner's list of statements, now", "get": "/api/admin/statements", "admin": True},
    {"case": "the owner's list of declined statements", "get": "/api/admin/statements?status=declined", "admin": True},
    {"case": "the owner's list of saved briefings, now", "get": "/api/admin/briefings", "admin": True},
]


def usage_error(code: int, text: str) -> str:
    """A command's output; for one the command line itself refused (exit 2: an option missing), only what it says is
    missing, since the box it is drawn in is the terminal library's and follows its version and the terminal."""
    if code != 2:
        return text
    return " ".join(line.strip("│╭╰─╮╯ ") for line in text.splitlines() if "Missing" in line)


def a_saved_briefing(question: str) -> list[dict[str, Any]]:
    """A briefing's events as this site saves them: no text event, and no token counts."""
    return [{"type": "plan", "plan": {"question": question}, "usage": None},
            {"type": "sources", "sources": [{k: v for k, v in c.items() if k != "date"} for c in CARDS[:2]],
             "candidates": 12, "semantic": True, "evidence": {"records": 2}},
            {"type": "final", "answer": an_answer(), "model": "claude-sonnet-5-5", "stop_reason": "end_turn",
             "interests": json.loads(json.dumps(INTERESTS))}]


def replies_scenario(*, client: Any, run: Callable[[list[str]], tuple[int, str]], tick: Callable[[float], None],
                     save: Callable[[str, str, dict[str, Any], list[dict[str, Any]]], Any],
                     kept: Callable[[], dict[str, Any]], scratch: Path) -> dict[str, Any]:
    """What the forms are answered, what the owner's pages do and what the owner's commands print, step by step,
    a minute apart (so that all of it falls on one day, UTC). ``save(id, question, options, events)`` keeps a
    briefing; ``kept()`` gives the rows of reports and statements; ``scratch`` is a folder for the files of words
    the update command reads."""
    for bid, question in ((BRIEFING_1, Q), (BRIEFING_2, Q2), (WITHDRAWN, "An older question?")):
        save(bid, question, {"mode": "summary"}, a_saved_briefing(question))
        tick(60)
    r = client.post(f"/api/admin/briefings/{WITHDRAWN}/withdraw", json={"reason": "It quoted the wrong debate."},
                    headers=ADMIN)
    assert r.status_code == 200, r.text
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "words.txt").write_text("These are the author's new words, sent by email, exactly as written.\n")
    (scratch / "long.txt").write_text(LONG_WORDS)
    files = {"<words>": str(scratch / "words.txt"), "<long>": str(scratch / "long.txt")}

    def http(step: dict[str, Any]) -> dict[str, Any]:
        tick(60)
        headers = ADMIN if step.get("admin") else {}
        r = client.get(step["get"], headers=headers) if "get" in step else \
            client.post(step["post"], json=step["json"], headers=headers)
        if step.get("page"):  # only what a saved briefing's page changes: its status, title and whether to index it
            html = r.text
            title = html[html.index("<title>"):html.index("</title>") + 8]
            return {"case": step["case"], "status": r.status_code, "title": title,
                    "noindex": '<meta name="robots" content="noindex" />' in html,
                    "description": [ln.strip() for ln in html.splitlines() if 'name="description"' in ln]}
        if step.get("text"):
            return {"case": step["case"], "status": r.status_code, "text": r.text}
        return {"case": step["case"], "status": r.status_code, "json": r.json()}

    out: dict[str, Any] = {"http": [http(step) for step in HTTP_STEPS]}
    out["kept_after_the_forms"] = kept()
    out["cli"] = []
    for step in CLI_STEPS:
        tick(60)
        code, text = run([files.get(a, a) for a in step["args"]])
        out["cli"].append({"case": step["case"], "args": step["args"], "exit": code, "output": usage_error(code, text)})
    out["admin"] = [http(step) for step in ADMIN_STEPS]
    out["after"] = [http(step) for step in AFTER_STEPS]
    out["kept"] = kept()
    return out


def close_hits(rows: list[dict[str, Any]], score: str, keep: int = 8) -> list[list[Any]]:
    """The best ``keep`` of a search's rows as [passage, score], in a settled order. Rows that tie with the last
    one kept are left out with it: which of them a search returns first is not fixed."""
    hits = sorted(([r["passage_id"], round(r[score], 4)] for r in rows),
                  key=lambda h: (-h[1] if score == "_score" else h[1], h[0]))
    if len(hits) > keep:
        edge = hits[keep][1]
        hits = [h for h in hits[:keep] if h[1] != edge]
    return hits
