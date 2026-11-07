# The test set, and measured performance

The test set and what it showed, then how fast each step runs (at the end). The test set was first run on
11 October 2026. The questions are in `eval/questions.toml`, the summaries and the question readings in `eval/runs/`
(the full runs, with every source and briefing, are large and kept out of the repository: `hansard
eval sources` and `hansard eval briefings` write them again), and the relevance grades in `eval/judgments.jsonl`.

## What it is

62 questions covering everything the site understands: plain topics, parties, places, dates, the Lords, ministers and
the opposition, named people, gender, age, first-term MPs, marginal and safe seats, votes, combinations, loaded or
misspelt wording, an attempt to override the instructions, and follow-up questions. For each one, the test set
records the filters a careful reader would choose.

Three things are scored:

1. **Reading the question**: are the filters (the chips shown above each briefing) right? Every filter counts, and
   adding one the question did not ask for is a mistake.
2. **Choosing the sources**: how relevant are the 24 sources chosen, and how many parties and speaker groups do they
   cover? A small model grades each source 0 (off topic), 1 (related) or 2 (directly on the question); each source
   is only ever graded once, so later comparisons are nearly free.
3. **The briefings**: the automatic checks (citations found word for word in their sources, sentences without
   support, loaded words), and a blind side-by-side judgement of two models' briefings on the same sources.

Run it with `hansard eval plans`, `hansard eval sources` and `hansard eval briefings` (see `hansard eval --help`).
On the server, run them from `/opt/hansardgpt` inside the app container. `hansard eval retrieval` runs the searches
alone, for free ([Retrieval only](#retrieval-only-checking-a-change-to-the-search-index)).

## 1. Reading the question

| Who reads the question | All filters right | Cost per question | Typical time |
|---|---|---|---|
| Opus 5.5 | 62 of 62 | 0.43p | 2.6 s |
| Sonnet 5.5 | 61 of 62 | 0.24p | 2.3 s |
| **Haiku 5.5** | **60 of 62** | **0.013p** | **1.5 s** |
| Built-in rules (free) | 47 of 62 | free | instant |

Haiku's two misses were defensible: it ended "during the pandemic" in mid-2022 rather than at the end of 2022, and
limited "the Safety of Rwanda Bill" to the months the bill was before Parliament. **Haiku now reads the questions**,
including for "Sources only", which used the far weaker built-in rules before. One line in `config/settings.toml`
(`query_parse_model`) switches back.

The test set also found a real bug, now fixed: when the model answered with the name of a group of regions
("northern") rather than the regions themselves, the filter was silently dropped, so "northern MPs" searched everyone.

## 2. Choosing the sources: re-ranking

Re-ranking means a small model on the server re-reads the best candidates alongside the question and re-scores them
(no charge per question). Tested here on keyword search, since only 1% of the record had meaning-based search on the
test machine (the live server will have all of it).

| Ranking | Sources directly on the question | Related or direct | Parties | Speaker groups | Time to choose |
|---|---|---|---|---|---|
| Current | 64% | 96% | 4.0 | 4.8 | 1.1 s |
| Re-rank best 60 | 67% | 97% | 4.1 | 4.8 | 3.8 s |
| Re-rank best 100 | 68% | 97% | 4.1 | 4.8 | 5.8 s |
| Re-rank best 150 | 69% | 97% | 4.1 | 4.8 | 8.1 s |

(Re-ranker: ms-marco-MiniLM-L-6-v2. A second model, jina-reranker-v1-tiny-en, did about the same. Times are on a
4-core machine.)

The first version of re-ranking narrowed the balance: on average one fewer speaker group per question. That is
fixed: re-ranking may now change the order of the evidence but never which groups are included.

**Decision: re-ranking is built but off for now.** It gives a modest gain for several seconds' wait on every search.
It should be tried again on the live server once meaning-based search is complete, because that may change both the
starting point and the speed. Turning it on is one line (`reranker` in `[ranking]`).

**How far to trust the grades:** Sonnet re-graded a random 120 of Haiku's grades. They agreed on relevant or not 91%
of the time, and never differed by more than one step. Haiku is a little more generous with "related". Every ranking
is graded by the same grader, so the comparisons are fair even where the absolute numbers are a little high.

## 3. Briefings: Opus or Sonnet, and tighter citations

Ten varied questions, each answered by both models from the same sources.

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

**Decision at the time (11 October): Opus stayed for briefings.** Both are equally accurate; Opus covers a little
more and reads a little better, at twice the price. The judge was Opus itself, which may favour its own writing.

**Since later in October 2026, briefings use Sonnet**, to halve their cost for the same accuracy (`model` in
`[answer]`, `config/settings.toml`). Switching back to Opus is that one line, if costs allow.

A lesson from the first attempt at judging: the judge was given shortened extracts to save money, and then
"found" invented quotations that were really in the full speeches. The judge now sees the full sources, and,
better, **every quotation in every briefing is now checked automatically**, word for word, against the speech it is
attributed to (free). Any quotation that cannot be found is shaded for readers.

**Tighter citations (new instructions).** Most uncited text was of two kinds: notes about the evidence ("the records
span 2013 to 2026") scattered through the briefing, and summaries of a group's view without an example. The
instructions now ask for every such summary to carry its quoted, cited example, and for notes about the evidence to
go in a closing "About the evidence:" paragraph, which is not counted as a claim (unless it reports what someone
said). Opus, same ten questions:

| | Before | After |
|---|---|---|
| Share of the text backed by a citation | 76% | 83% or more |
| Passages flagged to readers as unsupported | 30 | 12 |
| Quotations not found in their source | 0 of 108 | 0 of 108 |
| Cost per briefing | 11p | 11p |

(The 83% was measured before a final refinement that stops a closing note being flagged merely for containing the
word "said", so the live figure should be a little higher.)

## Cost of this round of testing

About **$5.10** in all. First round about $2.10: reading the questions with each model about $0.54; grading sources
about $0.11; checking the grader with Sonnet about $0.15; Opus briefings about $1.30 (including about $0.10 lost to
a bug in the scoring script). Second round about $3.00: Sonnet briefings $0.52; two side-by-side judgements $1.33
(the first, with shortened sources, had to be redone); Opus briefings with the new instructions $1.14.

## Retrieval only: checking a change to the search index

`hansard eval retrieval` runs the 62 questions' searches as `eval sources` does, from question readings already
saved in `eval/runs` (the newest made by a model, by default), but grades nothing: no model is called and it costs
nothing. It saves each question's 24 chosen sources and its 300 nearest passages by meaning, with their
similarities. Run again after a change with `--compare <the first run>`, it reports how many questions chose
exactly the same sources, the overlap of the sources, and for the meaning search the share of the nearest 10
(`overlap_at_10`) and 300 (`overlap_all`) found by both, how far the nearest 10 moved in rank, and how far a
passage's similarity changed.

On the server, before and after the change (the test set is mounted into a one-off container, and the runs kept
in the data volume):

```
cd /opt/hansardgpt/deploy
docker compose run --rm -T -v /opt/hansardgpt/eval:/app/eval:ro app hansard eval retrieval --out /data/eval-runs
# ... the change, e.g. hansard vectors-f16 (HOSTING.md, "Disk space") ...
docker compose run --rm -T -v /opt/hansardgpt/eval:/app/eval:ro app hansard eval retrieval --out /data/eval-runs --compare /data/eval-runs/<the first run's file>.json
```

### 16-bit search vectors (6 November 2026)

Measured before decision 26, on Hansard from 1 July to 5 November 2026 (105,154 passages from 410 files, embedded
with the site's local model, 384 numbers each), with the site's settings (300 results, each re-checked against the
full vectors, `refine_factor` 5). The full results are in `eval/runs/20261106-retrieval-float16.json`.

**The vectors themselves** (`scripts/compare_float16.py`: two 32-bit tables and one 16-bit table from the same
embeddings, 376 searches: the 14 sample searches, the 62 questions and 300 headings from the record):

| | Nearest 10 found by both | Same 10 in the same order | Nearest 300 found by both | Similarity change, mean / largest |
|---|---|---|---|---|
| 16-bit against 32-bit, every vector compared (no index) | 99.97% | 371 of 376 | 99.97% | 0.000013 / 0.000066 |
| 16-bit against 32-bit, as the site searches | 99.97% | 371 of 376 | 97.4% | 0.000013 / 0.000066 |
| For comparison: 32-bit against the same 32-bit index built again | 100% | 376 of 376 | 97.4% | 0 |

Each finds the same share of the truly nearest 300 passages: 97.5% for 32-bit, 97.3% for the same rebuilt, 97.3%
for 16-bit. The largest rounding of any stored number was 0.00012; the table took 86 MB rather than 163 MB.

**The test set, before and after `hansard vectors-f16`** (`hansard eval retrieval --compare`), beside the same
comparison after merely rebuilding the 32-bit index, which is how far results move with no change to the numbers
at all:

| | Questions with exactly the same 24 sources | Overlap of the sources | Meaning search: nearest 10 found by both | Largest similarity change |
|---|---|---|---|---|
| Run twice, nothing changed | 62 of 62 | 100% | 100% | 0 |
| The 32-bit index rebuilt | 50 of 62 | 99.3% | 100% | 0 |
| **Converted to 16-bit** | **55 of 62** | **99.4%** | **99.8%** | **0.00006** |

So 16-bit vectors change what is found less than rebuilding the index does, which `hansard reindex-vectors`
already does from time to time. A source that changes is one of two passages scored within a hair of each other.
The conversion itself took 84 seconds for this corpus, 75 of them building the index; on the full record expect
about an hour.

### The conversion on the server, and the probes it needed (7 November 2026)

`hansard vectors-f16` converted the server's tables on 7 November 2026: 7,887,156 passages' vectors (5.65 GB
saved) and the committee evidence's (0.74 GB saved). For the passages it built a new meaning-search index with
8,192 vectors to a partition (962 partitions), the shape `hansard reindex-vectors` builds, but searched it with the
old setting of 20 partitions (`vector_nprobes`, the default). Its own check found only 90.8% of the old table's
nearest 10 (`overlap_at_10` 0.908, just over the 0.9 it requires), and the committee evidence 92.9%.

The test set's `hansard eval retrieval --compare`, before and after: 18 of 62 questions chose exactly the same 24
sources, the sources overlapped by 93.1%, and the meaning search found 96.45% of the same nearest 10 (lowest 60%).
The largest change in a passage's similarity was 0.000055. This comparison is muddied: the twice-daily update added
new Hansard between the two runs.

`hansard reindex-vectors` then rebuilt the index the same way (463 seconds) and measured each setting against a
search that compares every vector (2.5 seconds a search). The share of the true nearest 300 passages found, and the
median time of a search:

| Partitions searched (`nprobes`) | No re-check | Re-check 5 times as many (`refine_factor` 5) | Re-check 10 times |
|---|---|---|---|
| 20 | 63.2%, 15.5 ms | 81.5%, 30.7 ms | 82.4%, 41.7 ms |
| 40 | 65.5%, 21.2 ms | 87.6%, 33.8 ms | 88.6%, 38.4 ms |
| 80 | 68.2%, 35.1 ms | **94.0%, 42.1 ms** | 95.1%, 53.6 ms |

Before the rebuild, the converted table's index with the site's settings then (20 partitions, re-check 5) found
85.2%, in 26.3 ms. 80 partitions with re-check 5 was recommended and saved to `/data/settings.local.toml`.

So the conversion's lower overlap came from the new index searched with too few partitions, not from the 16-bit
numbers. Searching 80 of its 962 partitions rather than 20, it finds 94% of the nearest passages, against 85.2%; the
largest change in a similarity on the test set, 0.000055, is the size of 16-bit rounding, as measured above. Since
then `hansard vectors-f16` measures the new index and saves its settings itself,
and every rebuild is recorded beside the index (`index/vector-index.json`) for the monthly report's reminder
([HOSTING.md](HOSTING.md), "Disk space").

## Measured performance

Moved here from the README on 6 November 2026.

On a 4-vCPU cloud VM, measured when the archive started in 2010 (the record now goes back to 1997):

| Step | Result |
|---|---|
| Download (HTTPS) | 24,695 files, 4.9 GB, 14 minutes |
| Ingest | 3.73 M records, 5,344 divisions, 2.35 M votes; 3 minutes; peak 664 MB across all processes |
| Keyword index | 4.68 M passages in 9 minutes; peak 2.7 GB during the full-text build |
| Daily sync (12 new day-files) | 28 s; peak 2.4 GB |
| Member reshuffle (96 people; 225 k passages updated in place, text and vectors kept) | 21 s; peak 1.7 GB |
| Multi-year SQL (Parquet + DuckDB) | 20–50 ms |
| Search (filters + BM25 + vectors + ranking + full records + speaker profiles) | 0.25–0.5 s warm |
| Embedding (`arctic-embed-xs`, CPU, batch 8) | 20–110 passages/s depending on length and CPU load; peak ~1 GB |

Embedding the whole archive is a one-off backlog of roughly two days on 4 vCPUs. It runs newest first and
resumes where it stopped. Keyword search covers everything in the meantime, and semantic matching covers
whatever has been embedded so far.
