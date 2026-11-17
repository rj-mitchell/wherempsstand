"""The test set file is valid, scoring is right, and re-ranking reorders as intended."""

from datetime import date
from pathlib import Path

import pytest

from hansard.engine import Filters
from hansard.evaluate import load_questions, parse_variant, plan_values, score_plan
from hansard.rag.query_parse import QueryPlan
from hansard.rank.composite import Candidate, Scored
from hansard.rank.rerank import rerank

ROOT = Path(__file__).resolve().parents[1]


def test_questions_file_is_valid():
    today, qs = load_questions(ROOT / "eval" / "questions.toml")
    assert today == date(2026, 10, 11)
    assert len(qs) >= 50
    fields = {"date_from", "date_to", "houses", "regions", "parties", "offices", "genders", "age_bands", "tenure",
              "majority_bands", "runner_up_parties", "groups", "speakers", "group_by", "wants_votes", "portfolios"}
    for q in qs:
        assert {k.removesuffix("_any") for k in q.expect} <= fields, q.id


def plan(**kw) -> QueryPlan:
    speakers = kw.pop("speakers", [])
    p = QueryPlan("q", "text", Filters(**{k: v for k, v in kw.items() if k not in ("group_by", "portfolios")}),
                  portfolios=kw.get("portfolios", []), group_by=kw.get("group_by", "role"))
    p.unresolved_speakers = speakers
    return p


def test_scoring_counts_extra_filters_as_mistakes():
    got = plan_values(plan(parties=["labourco-operative"], houses=["commons"], portfolios=["education"]))
    s = score_plan({"parties": ["labour"], "houses": ["commons"], "portfolios": ["education", "treasury"]}, got)
    assert all(s.values())
    s = score_plan({"parties": ["labour"]}, got)
    assert s["parties"] and not s["houses"]  # houses was not asked for


def test_scoring_dates_and_alternatives():
    got = plan_values(plan(date_from=date(2024, 7, 4), speakers=["Hilary Benn"]))
    assert score_plan({"date_from": "2024-07-01", "speakers": ["hilary benn"], "portfolios_any": [[]]}, got)["date_from"]
    assert not score_plan({"date_from": "2023-01-01"}, got)["date_from"]
    s = score_plan({"date_from_any": ["", "2024-07-04"], "speakers": ["Hilary Benn"]}, got)
    assert s["date_from"] and s["speakers"] and s["portfolios"]


def test_variants():
    assert parse_variant("baseline").reranker == ""
    v = parse_variant("rerank:Xenova/ms-marco-MiniLM-L-6-v2:question:0.8")
    assert (v.reranker, v.rerank_query, v.rerank_weight) == ("Xenova/ms-marco-MiniLM-L-6-v2", "question", 0.8)


class FakeReranker:
    def scores(self, query, texts):
        return [0.9 if "wind" in t else 0.1 for t in texts]


def scored(rid: str, text: str, rel: float) -> Scored:
    c = Candidate(rid, date(2024, 1, 1), best_passage_id="p", data={"passages": {"p": {"text": text}}})
    return Scored(c, rel, 1.0, 1.0, 0.0, "", rel)


def test_rerank_promotes_the_closer_match():
    out = rerank([scored("a", "tribute to a colleague", 0.9), scored("b", "onshore wind farms", 0.6),
                  scored("c", "more on wind", 0.95)], "onshore wind", FakeReranker(), top=2, weight=0.6)
    assert [s.candidate.record_id for s in out] == ["b", "a", "c"]  # c was beyond the re-ranked set
    assert out[0].rerank == 0.9 and out[0].breakdown()["reranker"] == 0.9


def test_prices_per_model():
    import dataclasses

    from hansard.config import load_settings
    from hansard.evaluate import cost_usd

    settings = load_settings()
    priced = dataclasses.replace(settings, raw={**settings.raw, "usage": {
        **settings.usage, "price_input_per_mtok": 4.0, "price_output_per_mtok": 20.0,
        "models": {"claude-haiku-5-5": {"price_input_per_mtok": 0.1, "price_output_per_mtok": 0.5}}}})
    u = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert cost_usd(u, priced, None) == 24.0
    assert cost_usd(u, priced, "claude-haiku-5-5") == 0.6
    assert cost_usd(None, priced, "claude-haiku-5-5") == 0.0


def test_region_groups_are_expanded_not_dropped():
    from hansard.rag.query_parse import _open_end, expand_regions

    groups = {"northern": ["North East", "North West", "Yorkshire and The Humber"]}
    assert expand_regions(["northern"], groups) == ["North East", "North West", "Yorkshire and The Humber"]
    assert expand_regions(["scotland", "London", "Atlantis"], groups) == ["London", "Scotland"]
    assert _open_end(date(2026, 10, 11), date(2026, 10, 11)) is None
    assert _open_end(date(2020, 1, 1), date(2026, 10, 11)) == date(2020, 1, 1)


def test_retrieval_runs_are_compared_without_grading(tmp_path):
    from hansard.evaluate import compare_retrieval, newest_plans

    def rec(*ids):
        return [{"record_id": i} for i in ids]

    a = {"rows": [{"id": "q1", "records": rec("r1", "r2"), "meaning": [["p1", 0.9], ["p2", 0.8]]},
                  {"id": "q2", "records": rec("r3"), "meaning": [["p3", 0.7]]}]}
    b = {"rows": [{"id": "q1", "records": rec("r1", "r2"), "meaning": [["p1", 0.9001], ["p2", 0.8]]},
                  {"id": "q2", "records": rec("r4"), "meaning": [["p3", 0.7]]}]}
    out = compare_retrieval(a, b)
    assert out["questions"] == 2 and out["sources_identical"] == 1
    assert out["questions_with_different_sources"] == ["q2"] and out["sources_overlap"] == 0.5
    assert out["meaning"]["overlap_at_10"] == 1.0
    assert out["meaning"]["max_similarity_difference"] == pytest.approx(1e-4, abs=1e-6)

    for name in ("20261011-1-plans-a.json", "20261011-2-plans-b.json", "20261011-3-plans-offline.json"):
        (tmp_path / name).write_text("{}")
    assert newest_plans(tmp_path).name == "20261011-2-plans-b.json"
    assert newest_plans(tmp_path / "none") is None


# the shared harness: `hansard eval run`, `compare`, `replay` (wts-core, ``wts_core.eval``)


def test_the_sample_questions_are_valid_and_the_harness_reads_both_files():
    from wts_core.eval import load_cases

    sample = load_cases(ROOT / "eval" / "sample-questions.toml")
    assert [c.id for c in sample] == ["jury", "ni-security", "building-safety", "media-plurality",
                                      "money-laundering", "mars"]
    assert len(load_cases(ROOT / "eval" / "questions.toml")) >= 50  # the test set, in the same form


def test_the_briefings_recorded_on_the_sample_data_replay_and_the_checks_say_what_they_said():
    """eval/fixtures/ holds five briefings a real model wrote on the bundled sample data (15 November 2026), each
    with the sources it was given and what the checks said of it. The checks are run over them again, free and
    with no model: they must say exactly what they said, and a change to the checks that says less fails here."""
    from typer.testing import CliRunner
    from wts_core.eval import assert_never_weaker, load_fixture, replay

    from hansard import cli
    from hansard.rag.checks import LOADED_WORDS, VERIFIER

    folder = ROOT / "eval" / "fixtures"
    names = sorted(p.stem for p in folder.glob("*.jsonl"))
    assert names == ["building-safety", "jury", "media-plurality", "money-laundering", "ni-security"]
    fixtures = [load_fixture(folder / f"{name}.jsonl", report=folder / f"{name}.report.json") for name in names]
    assert all(f.report is not None and f.report["citations_total"] > 0 for f in fixtures)
    assert sum(f.report["citations_invalid"] + f.report["quotes_unverified"] for f in fixtures) == 0
    differences = replay(fixtures, VERIFIER, LOADED_WORDS)
    assert_never_weaker(differences)
    assert differences == []
    result = CliRunner().invoke(cli.app, ["eval", "replay", str(folder)])
    assert (result.exit_code, result.output) == (
        0, "The checks say of 5 recorded briefings exactly what they said when each was recorded.\n")
    # nothing but the public record and the briefing is in them: no key, no address of a machine
    for path in folder.iterdir():
        text = path.read_text()
        assert not any(word in text for word in ("sk-ant", "api_key", "Authorization", "/home/", "/tmp/")), path.name


def test_an_evaluation_run_asks_the_real_pipeline_saves_in_the_data_folder_and_spends_outside_the_caps(
        indexed_settings, tmp_path, monkeypatch):
    """`hansard eval run`, with the model faked: each question goes to the site's real pipeline (not through the
    flow readers' questions take), the run is saved under the data folder (on the server, the data volume), each
    briefing is kept as a fixture when asked, and nothing is written to the spending record. The run's own cost
    counts the check of registered interests with the other two calls (``HansardEval.usages``, wts-core 0.3.0)."""
    import json
    import sqlite3
    from types import SimpleNamespace as NS

    import shared
    from typer.testing import CliRunner
    from wts_core.adapters.hashing import HashingEmbedder
    from wts_core.testing import FixedClock

    from hansard import cli
    from hansard import evaluate as ev
    from hansard.engine import Engine
    from hansard.rag.pipeline import Pipeline
    from hansard.store.wts import open_core

    from .conftest import model_of
    from .sides import real_side

    side = real_side(indexed_settings, tmp_path, shared.FakeSDK(shared.READING), monkeypatch)
    settings = side.settings
    engine = Engine(settings, embedder=HashingEmbedder(64), today=shared.TODAY)
    sdk = shared.FakeSDK(shared.READING)
    reads = sdk.messages.parse

    def parse(**kw):  # the small model reads the question as recorded, and finds no interest that matters
        if kw["output_format"].__name__ != "Matches":
            return reads(**kw)
        return NS(stop_reason="end_turn", parsed_output=kw["output_format"](matches=[]),
                  usage=NS(input_tokens=400, output_tokens=30, cache_read_input_tokens=0,
                           cache_creation_input_tokens=0))

    sdk.messages = NS(stream=sdk._stream, parse=parse)

    class EveryoneHasLand:  # so that the check of registered interests is made for each briefing
        def for_person(self, pid):
            return {"categories": [{"type": "6", "name": "Land and property (within or outside the UK)", "items": [
                {"summary": "Farm land", "details": [], "registered": "10 January 2000", "children": []}]}]}

    class NoLords:
        def for_person(self, pid):
            return []

    pipeline = Pipeline(settings, engine, client=model_of(sdk), today=shared.TODAY)
    pipeline._registers = (EveryoneHasLand(), NoLords(), None)
    questions = tmp_path / "questions.toml"
    questions.write_text('today = "2026-10-01"\n\n[[q]]\nid = "jury"\nquestion = "What has been said about trial by '
                         'jury?"\nexpect = { portfolios = ["justice"] }\n\n[[q]]\nid = "jury-lords"\n'
                         'question = "And what did peers say about trial by jury?"\n')
    monkeypatch.setattr(cli, "_settings", lambda: settings)
    monkeypatch.setattr(cli, "CLOCK", FixedClock("2026-11-15T10:00:00+00:00"))
    monkeypatch.setattr(cli, "_eval_site", lambda: ev.HansardEval(settings, pipeline, questions))
    fixtures = tmp_path / "fixtures"
    result = CliRunner().invoke(cli.app, ["eval", "run", "--max-cost", "1.0", "--record-fixtures", str(fixtures)])
    assert result.exit_code == 0, result.output
    assert "jury: completed" in result.output and "jury-lords: completed" in result.output
    assert "Briefings written: 2" in result.output and "Citations not found in their source: 0" in result.output
    runs = settings.data_dir / "eval" / "runs"  # in the data folder: on the server, the data volume
    (saved,) = [p for p in runs.iterdir() if p.name != "passed.jsonl"]
    assert f"Saved as {saved}" in result.output
    run = json.loads(saved.read_text())
    assert [r["id"] for r in run["results"]] == ["jury", "jury-lords"]
    first = run["results"][0]
    assert first["outcome"] == "completed" and first["citations_total"] >= 1 and first["cost_usd"] > 0
    assert first["expect_met"]["portfolios"] is True and run["results"][1]["expect_met"] == {}
    # the configuration is the one the record of briefings gives the same settings
    from wts_core.observe import config_id

    from hansard.api.app import configuration

    assert run["config_id"] == config_id(configuration(settings, None).settings)
    # nothing went through the flow readers' questions take: no spending row, no saved briefing, no record
    core = open_core(settings)
    con = sqlite3.connect(settings.state_db)
    for table in ("wts_spending", "wts_briefings", "wts_answer_cache", "wts_briefing_log"):
        assert table in core.tables() and con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
    con.close()
    # the run's own cost counts the interests check: the same run without it costs exactly that call less
    from wts_core.ports import Usage

    class WithoutInterests(ev.HansardEval):
        def usages(self, event):
            return []

    monkeypatch.setattr(cli, "_eval_site", lambda: WithoutInterests(settings, pipeline, questions))
    less = CliRunner().invoke(cli.app, ["eval", "run", "--max-cost", "1.0", "--out", str(tmp_path / "less")])
    assert less.exit_code == 0, less.output
    (cheaper,) = list((tmp_path / "less").glob("*.json"))
    one_check = cli._prices().cost(Usage.from_mapping({"input_tokens": 400, "output_tokens": 30}),
                                   settings.answer["query_parse_model"])
    for full, short in zip(run["results"], json.loads(cheaper.read_text())["results"], strict=True):
        assert full["cost_usd"] == pytest.approx(short["cost_usd"] + one_check)
    # each briefing written was kept, and the checks say of it what they said
    assert sorted(p.name for p in fixtures.iterdir()) == ["jury-lords.jsonl", "jury-lords.report.json", "jury.jsonl",
                                                          "jury.report.json"]
    replayed = CliRunner().invoke(cli.app, ["eval", "replay", str(fixtures)])
    assert replayed.exit_code == 0 and "exactly what they said" in replayed.output
    # a run stops at its limit: with none left, nothing is asked, and the command ends with status 1
    stopped = CliRunner().invoke(cli.app, ["eval", "run", "--max-cost", "0"])
    assert stopped.exit_code == 1 and "Questions asked: 0" in stopped.output
