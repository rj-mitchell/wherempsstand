# Evaluation

How Where MPs Stand is measured: the test set of questions and what it showed about each part of the pipeline,
checks on changes to the search index, and how fast each step runs. The questions are in `eval/questions.toml` and
the relevance grades in `eval/judgments.jsonl`. `eval/runs/20261011-plans-reference.json` holds one saved set of
question readings, which `hansard eval retrieval` uses by default; other runs are saved where the commands run.

## The test set

62 questions covering everything the site understands: plain topics, parties, places, dates, the Lords, ministers
and the opposition, named people, gender, age, first-term MPs, marginal and safe seats, votes, combinations, loaded
or misspelt wording, an attempt to override the instructions, and follow-up questions. For each one, the test set
records the filters a careful reader would choose.

Three things are scored:

1. Reading the question: are the filters (the chips shown above each briefing) right? Every filter counts, and
   adding one the question did not ask for is a mistake.
2. Choosing the sources: how relevant are the 24 sources chosen, and how many parties and speaker groups do they
   cover? A small model grades each source 0 (off topic), 1 (related) or 2 (directly on the question); each source
   is graded once, so later comparisons are nearly free.
3. The briefings: the automatic checks (citations found word for word in their sources, sentences without support,
   loaded words), and a blind side-by-side judgement of two models' briefings on the same sources.

The commands (`hansard eval --help` lists their options):

| Command | What it does | Cost |
|---|---|---|
| `hansard eval plans` | Scores how each question is read | about 1p with Haiku |
| `hansard eval sources` | Compares ranking variants on the relevance and balance of the sources | a few pence, for new grades only |
| `hansard eval retrieval` | Runs the searches alone, from saved question readings ([Retrieval only](#retrieval-only)) | free |
| `hansard eval briefings` | Writes briefings with each model and compares them | about 6 to 15 US cents a briefing |
| `hansard eval run` | Asks the whole test set through the whole pipeline and holds it to `[eval.thresholds]` in `config/settings.toml` | about $2 to $3, capped by `[eval] max_cost_usd` |
| `hansard eval compare A B` | Sets two runs side by side | free |
| `hansard eval replay eval/fixtures` | Runs the checks again over recorded briefings; the tests and CI do this on every change | free |

On the server, `hansard eval run` saves its runs in `/data/eval/runs/` on the data volume. What it spends is not on
the admin page and counts towards neither cap; the run says what it spent.

## Reading the question

The test set was first run on 11 October 2026.

| Who reads the question | All filters right | Cost per question | Typical time |
|---|---|---|---|
| Opus 5.5 | 62 of 62 | 0.43p | 2.6 s |
| Sonnet 5.5 | 61 of 62 | 0.24p | 2.3 s |
| Haiku 5.5 | 60 of 62 | 0.013p | 1.5 s |
| Built-in rules (free) | 47 of 62 | free | instant |

Haiku's two misses were defensible: it ended "during the pandemic" in mid-2022 rather than at the end of 2022, and
limited "the Safety of Rwanda Bill" to the months the bill was before Parliament. Haiku reads the questions,
including for "Sources only" (`query_parse_model` in `config/settings.toml`).

## Choosing the sources: re-ranking

Re-ranking means a small model on the server re-reads the best candidates alongside the question and re-scores them,
at no cost per question. It was tested on keyword search, since only 1% of the record had meaning-based search on the
test machine.

| Ranking | Sources directly on the question | Related or direct | Parties | Speaker groups | Time to choose |
|---|---|---|---|---|---|
| Current | 64% | 96% | 4.0 | 4.8 | 1.1 s |
| Re-rank best 60 | 67% | 97% | 4.1 | 4.8 | 3.8 s |
| Re-rank best 100 | 68% | 97% | 4.1 | 4.8 | 5.8 s |
| Re-rank best 150 | 69% | 97% | 4.1 | 4.8 | 8.1 s |

The re-ranker was ms-marco-MiniLM-L-6-v2 (jina-reranker-v1-tiny-en did about the same), on a 4-core machine.
Re-ranking may change the order of the evidence but never which speaker groups are included. It is built but off
([DECISIONS.md](DECISIONS.md), 14): a modest gain for several seconds' wait. Turning it on is one line (`reranker`
in `[ranking]`).

To check the grades, Sonnet re-graded a random 120 of Haiku's. They agreed on relevant or not 91% of the time and
never differed by more than one step; Haiku is a little more generous with "related". Every ranking is graded by the
same grader, so comparisons are fair even where the absolute numbers are a little high.

## Briefings: Opus or Sonnet

Ten varied questions, each answered by both models from the same sources (October 2026):

| Measure | Opus 5.5 | Sonnet 5.5 |
|---|---|---|
| Quotations not found word for word in the speech cited | 0 of 108 | 0 of 124 |
| Citations not found in their source | 0 | 1 (removed automatically) |
| Share of the text backed by a citation | 76% | 76% |
| Loaded words in the briefing's own voice | 0 | 0 |
| Cost per briefing | 11p | 5p |
| Time | 21 s | 13 s |
| Blind side-by-side judgement, overall (Opus judging) | better in 4 | better in 2 (4 ties) |
| …accuracy | better in 2 | better in 2 (6 ties) |
| …coverage of the arguments | better in 5 | better in 4 |

Both are equally accurate; Opus covers a little more and reads a little better, at twice the price, and the judge
was Opus, which may favour its own writing. Briefings use Sonnet ([DECISIONS.md](DECISIONS.md), 13). The judge sees
the full sources: given shortened extracts, it "found" invented quotations that were in the full speeches.

Most uncited text was of two kinds: notes about the evidence scattered through the briefing, and summaries of a
group's view without an example. The instructions now ask for every summary to carry a quoted, cited example, and
for notes about the evidence to go in a closing "About the evidence:" paragraph, which is not counted as a claim.
On the same ten questions with Opus, the share of text backed by a citation rose from 76% to 83% or more, and the
passages flagged to readers as unsupported fell from 30 to 12, at the same cost.

The whole round of testing cost about $5.10.

## Retrieval only

`hansard eval retrieval` runs the 62 questions' searches from question readings already saved in `eval/runs`, and
grades nothing, so it costs nothing. It saves each question's 24 chosen sources and its 300 nearest passages by
meaning, with their similarities. Run again after a change with `--compare <the first run>`, it reports how many
questions chose exactly the same sources, the overlap of the sources, and for the meaning search the share of the
nearest 10 (`overlap_at_10`) and 300 (`overlap_all`) found by both, and how far similarities changed.

On the server, before and after a change to the search index (the test set is mounted into a one-off container, and
the runs are kept on the data volume):

```bash
cd /opt/hansardgpt/deploy
docker compose run --rm -T -v /opt/hansardgpt/eval:/app/eval:ro app hansard eval retrieval --out /data/eval-runs
# ... the change ...
docker compose run --rm -T -v /opt/hansardgpt/eval:/app/eval:ro app hansard eval retrieval --out /data/eval-runs --compare /data/eval-runs/<the first run's file>.json
```

### 16-bit search vectors

Measured on 6 November 2026, on Hansard from 1 July to 5 November 2026 (105,154 passages, 384 numbers each), with
the site's settings (300 results, each re-checked against the full vectors, `refine_factor` 5).
`scripts/compare_float16.py` compared two 32-bit tables and one 16-bit table from the same embeddings on 376
searches:

| | Nearest 10 found by both | Same 10 in the same order | Nearest 300 found by both | Similarity change, mean / largest |
|---|---|---|---|---|
| 16-bit against 32-bit, every vector compared (no index) | 99.97% | 371 of 376 | 99.97% | 0.000013 / 0.000066 |
| 16-bit against 32-bit, as the site searches | 99.97% | 371 of 376 | 97.4% | 0.000013 / 0.000066 |
| 32-bit against the same 32-bit index built again | 100% | 376 of 376 | 97.4% | 0 |

The test set, before and after conversion, beside the same comparison after merely rebuilding the 32-bit index:

| | Questions with exactly the same 24 sources | Overlap of the sources | Meaning search: nearest 10 found by both | Largest similarity change |
|---|---|---|---|---|
| Run twice, nothing changed | 62 of 62 | 100% | 100% | 0 |
| The 32-bit index rebuilt | 50 of 62 | 99.3% | 100% | 0 |
| Converted to 16-bit | 55 of 62 | 99.4% | 99.8% | 0.00006 |

So 16-bit vectors change what is found less than rebuilding the index does. The table took 86 MB rather than
163 MB.

### Partitions searched

The server's vectors were converted on 7 November 2026, saving 6.4 GB. The new index had 962 partitions, and
searched with the old setting of 20 partitions it found only 85.2% of the true nearest 300 passages.
`hansard reindex-vectors` measured each setting against a search that compares every vector:

| Partitions searched (`nprobes`) | No re-check | Re-check 5 times as many (`refine_factor` 5) | Re-check 10 times |
|---|---|---|---|
| 20 | 63.2%, 15.5 ms | 81.5%, 30.7 ms | 82.4%, 41.7 ms |
| 40 | 65.5%, 21.2 ms | 87.6%, 33.8 ms | 88.6%, 38.4 ms |
| 80 | 68.2%, 35.1 ms | 94.0%, 42.1 ms | 95.1%, 53.6 ms |

80 partitions with re-check 5 was saved to the server's `/data/settings.local.toml`. The lower overlap came from
searching too few partitions; the 16-bit numbers made no measurable difference. `hansard vectors-f16` and `hansard reindex-vectors` now measure the
index and save its settings themselves, and record each rebuild in `index/vector-index.json` for the monthly disk
report's reminder.

## The move onto wts-core

The move onto the shared package was meant to change nothing a reader sees. The proof is the parity tests:
`tests/golden/` records what the site's earlier code gave, and `tests/test_parity.py`, `test_replies_parity.py`,
`test_import.py` and `test_ops.py` compare it with what the code gives now and name every difference.

On the bundled sample data (`hansard bootstrap --sample`), six questions were asked of the earlier code and the new
with real models on 15 November 2026. Every citation was found in its source, no loaded word was flagged, and the
question with nothing on the record ("Lighthouses on Mars") was answered "no records" in both. The number of
sources differed from run to run because a model reads each question afresh; with the question read by the built-in
rules, both versions chose exactly the same sources in the same order for all six. The five briefings of the shared
harness's run are kept in `eval/fixtures/`, each with its sources and what the checks said, for
`hansard eval replay`. A change to the checks that says less of one of them fails the tests.

## The devolved parliaments' lookup

Measured on 16 November 2026. The first evaluation run on the real archive kept readers waiting minutes for the
first word whenever a question named a long period ("coalition", 2010 to 2015: 276 s). The cause was the lookup in
the devolved parliaments' record (`DevolvedStore.relevant`, run beside the main search): for a question with dates,
SQLite walked every speech of the period and asked the full-text index about each in turn. The query now reads the
matches from the full-text index first (`speech_text CROSS JOIN speeches`, which SQLite takes in the order written),
then applies the filters and the same ranking. Nothing on the server needed rebuilding.

On a synthetic record of the real one's size (1.6 million speeches), on a 4-vCPU test machine:

| Lookup | Before | After | Found (the same, in the same order) |
|---|---|---|---|
| "austerity" | 0.11 s | 0.13 s | 24 |
| "austerity", 2010-05-06 to 2015-05-07 | 6.6 s | 0.10 s | 16 |
| "austerity public spending cuts" | 0.16 s | 0.17 s | 24 |
| "austerity public spending cuts", 2010-05-06 to 2015-05-07 | 241.7 s | 0.14 s | 16 |

On 84 random lookups, the old query and the new found the same contributions in the same order. Ties in relevance
now go to the earlier stored speech rather than the earlier day; none occurred in the comparison.

Each lookup beside the main search (votes, early day motions, the devolved parliaments, committee evidence) now has
a time limit, `[answer] side_lookup_seconds` (6). A lookup still running then is left out of that briefing, with a
warning in the log ("side lookup 'devolved' took longer than 6 s: the briefing goes on without it"); its thread
finishes on its own. While one kind is still running past its limit, that kind is left out of new briefings, so a
slow kind holds at most one of the pool's eight threads.

## Measured performance

On a 4-vCPU cloud machine, measured when the archive started in 2010 (it now starts in 1997):

| Step | Result |
|---|---|
| Download (HTTPS) | 24,695 files, 4.9 GB, 14 minutes |
| Ingest | 3.73 million records, 5,344 divisions, 2.35 million votes; 3 minutes; peak 664 MB across all processes |
| Keyword index | 4.68 million passages in 9 minutes; peak 2.7 GB during the full-text build |
| Daily sync (12 new day-files) | 28 s; peak 2.4 GB |
| Member reshuffle (96 people; 225,000 passages updated in place, text and vectors kept) | 21 s; peak 1.7 GB |
| Multi-year SQL (Parquet and DuckDB) | 20 to 50 ms |
| Search (filters, BM25, vectors, ranking, full records and speaker profiles) | 0.25 to 0.5 s warm |
| Embedding (`arctic-embed-xs`, CPU, batch 8) | 20 to 110 passages a second, depending on length and load; peak about 1 GB |

Embedding the whole archive is a one-off backlog of roughly two days on 4 vCPUs. It runs newest first and resumes
where it stopped; keyword search covers everything meanwhile.
