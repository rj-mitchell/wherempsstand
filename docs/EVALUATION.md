# The test set, and measured performance

The test set and what it showed, then how fast each step runs (at the end). The test set was first run on
11 October 2026. The questions are in `eval/questions.toml`, the relevance grades in `eval/judgments.jsonl`, and one
set of saved question readings in `eval/runs/20261011-plans-reference.json`, which `hansard eval retrieval` uses by
default. Other runs are kept on the machine that made them, not in the repository: the `hansard eval` commands write
them again.

## The test set

62 questions covering everything the site understands: plain topics, parties, places, dates, the Lords, ministers and
the opposition, named people, gender, age, first-term MPs, marginal and safe seats, votes, combinations, loaded or
misspelt wording, an attempt to override the instructions, and follow-up questions. For each one, the test set
records the filters a careful reader would choose.

Three things are scored:

1. Reading the question: are the filters (the chips shown above each briefing) right? Every filter counts, and
   adding one the question did not ask for is a mistake.
2. Choosing the sources: how relevant are the 24 sources chosen, and how many parties and speaker groups do they
   cover? A small model grades each source 0 (off topic), 1 (related) or 2 (directly on the question); each source
   is only ever graded once, so later comparisons are nearly free.
3. The briefings: the automatic checks (citations found word for word in their sources, sentences without
   support, loaded words), and a blind side-by-side judgement of two models' briefings on the same sources.

Run it with `hansard eval plans`, `hansard eval sources` and `hansard eval briefings` (see `hansard eval --help`).
`hansard eval run` asks the whole set of the whole pipeline, each briefing written, and holds the run to the
thresholds in `config/settings.toml` ([The move onto the shared package](#the-move-onto-the-shared-package-before-and-after-15-november-2026)).
On the server, run them from `/opt/hansardgpt` inside the app container. `hansard eval retrieval` runs the searches
alone, for free ([Retrieval only](#retrieval-only-checking-a-change-to-the-search-index)).

## 1. Reading the question

| Who reads the question | All filters right | Cost per question | Typical time |
|---|---|---|---|
| Opus 5.5 | 62 of 62 | 0.43p | 2.6 s |
| Sonnet 5.5 | 61 of 62 | 0.24p | 2.3 s |
| Haiku 5.5 | 60 of 62 | 0.013p | 1.5 s |
| Built-in rules (free) | 47 of 62 | free | instant |

Haiku's two misses were defensible: it ended "during the pandemic" in mid-2022 rather than at the end of 2022, and
limited "the Safety of Rwanda Bill" to the months the bill was before Parliament. Haiku now reads the questions,
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

### Decision: re-ranking is built but off for now

It gives a modest gain for several seconds' wait on every search.
It should be tried again on the live server once meaning-based search is complete, because that may change both the
starting point and the speed. Turning it on is one line (`reranker` in `[ranking]`).

### Reliability of the grades

Sonnet re-graded a random 120 of Haiku's grades. They agreed on relevant or not 91%
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

Decision at the time (11 October): Opus stayed for briefings. Both are equally accurate; Opus covers a little
more and reads a little better, at twice the price. The judge was Opus itself, which may favour its own writing.

Since later in October 2026, briefings use Sonnet, to halve their cost for the same accuracy (`model` in
`[answer]`, `config/settings.toml`). Switching back to Opus is that one line, if costs allow.

A lesson from the first attempt at judging: the judge was given shortened extracts to save money, and then
"found" invented quotations that were really in the full speeches. The judge now sees the full sources, and,
better, every quotation in every briefing is now checked automatically, word for word, against the speech it is
attributed to (free). Any quotation that cannot be found is shaded for readers.

### Tighter citations (new instructions)

Most uncited text was of two kinds: notes about the evidence ("the records
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

About $5.10 in all. First round about $2.10: reading the questions with each model about $0.54; grading sources
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
full vectors, `refine_factor` 5).

The vectors themselves (`scripts/compare_float16.py`: two 32-bit tables and one 16-bit table from the same
embeddings, 376 searches: the 14 sample searches, the 62 questions and 300 headings from the record):

| | Nearest 10 found by both | Same 10 in the same order | Nearest 300 found by both | Similarity change, mean / largest |
|---|---|---|---|---|
| 16-bit against 32-bit, every vector compared (no index) | 99.97% | 371 of 376 | 99.97% | 0.000013 / 0.000066 |
| 16-bit against 32-bit, as the site searches | 99.97% | 371 of 376 | 97.4% | 0.000013 / 0.000066 |
| For comparison: 32-bit against the same 32-bit index built again | 100% | 376 of 376 | 97.4% | 0 |

Each finds the same share of the truly nearest 300 passages: 97.5% for 32-bit, 97.3% for the same rebuilt, 97.3%
for 16-bit. The largest rounding of any stored number was 0.00012; the table took 86 MB rather than 163 MB.

The test set, before and after `hansard vectors-f16` (`hansard eval retrieval --compare`), beside the same
comparison after merely rebuilding the 32-bit index, which is how far results move with no change to the numbers
at all:

| | Questions with exactly the same 24 sources | Overlap of the sources | Meaning search: nearest 10 found by both | Largest similarity change |
|---|---|---|---|---|
| Run twice, nothing changed | 62 of 62 | 100% | 100% | 0 |
| The 32-bit index rebuilt | 50 of 62 | 99.3% | 100% | 0 |
| Converted to 16-bit | 55 of 62 | 99.4% | 99.8% | 0.00006 |

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
| 80 | 68.2%, 35.1 ms | 94.0%, 42.1 ms | 95.1%, 53.6 ms |

Before the rebuild, the converted table's index with the site's settings then (20 partitions, re-check 5) found
85.2%, in 26.3 ms. 80 partitions with re-check 5 was recommended and saved to `/data/settings.local.toml`.

So the conversion's lower overlap came from the new index searched with too few partitions, not from the 16-bit
numbers. Searching 80 of its 962 partitions rather than 20, it finds 94% of the nearest passages, against 85.2%; the
largest change in a similarity on the test set, 0.000055, is the size of 16-bit rounding, as measured above. Since
then `hansard vectors-f16` measures the new index and saves its settings itself,
and every rebuild is recorded beside the index (`index/vector-index.json`) for the monthly report's reminder
([HOSTING.md](HOSTING.md), "Disk space").

## The move onto the shared package: before and after (15 November 2026)

The site's checks, limits, model client, embedder and index now come from the Where They Stand sites' shared
package (wts-core; [DECISIONS.md](DECISIONS.md)). The move was meant to change nothing a reader sees, and the proof
of that is the parity tests (`tests/test_parity.py` and its neighbours: what the old code gave, recorded, compared
with what the new code gives). This section adds a look with a real model, on the bundled sample data only
(`hansard bootstrap --sample`: a few hundred speeches of 2021 to 2026 with the offline search model). It says
nothing of the live site's figures: the archive there is millions of speeches, with the real search model. The
whole test set was not run on the archive for this change, because nothing that decides what a briefing says
changed; the first full `hansard eval run` on the server gives the baseline for the thresholds.

The same six questions, the same script, old code and new (one run each), with the models the settings name for writing briefings and for reading
questions.

| Question | Code | Sources | First word | Finished | Cost | Citations (not found) | Quotations (not found word for word) | Grounded | Unsupported sentences |
|---|---|---|---|---|---|---|---|---|---|
| Trial by jury | before | 14 | 7.3 s | 14.1 s | 3.4c | 13 (0) | 13 (0) | 0.78 | 2 |
| | after | 11 | 4.4 s | 11.5 s | 3.2c | 13 (0) | 13 (0) | 0.84 | 1 |
| Security in Northern Ireland | before | 23 | 4.0 s | 11.8 s | 4.1c | 13 (0) | 12 (0) | 1.00 | 0 |
| | after | 21 | 3.4 s | 11.0 s | 3.2c | 15 (0) | 13 (1) | 1.00 | 0 |
| Making residential buildings safe | before | 19 | 3.3 s | 12.8 s | 4.4c | 17 (0) | 16 (0) | 1.00 | 0 |
| | after | 10 | 4.0 s | 13.0 s | 4.0c | 13 (0) | 8 (0) | 0.71 | 1 |
| Media plurality, in the Lords | before | 12 | 2.9 s | 10.6 s | 2.4c | 13 (0) | 13 (0) | 0.53 | 3 |
| | after | 14 | 2.7 s | 10.0 s | 2.5c | 12 (0) | 13 (0) | 0.55 | 3 |
| Money laundering and asset recovery | before | 5 | 2.2 s | 7.3 s | 1.9c | 9 (0) | 7 (0) | 0.80 | 1 |
| | after | 7 | 2.5 s | 7.7 s | 2.0c | 9 (0) | 9 (0) | 0.50 | 4 |
| Lighthouses on Mars | before | 23 | 3.3 s | 4.1 s | 2.2c | none: "no records" | | | |
| | after | 23 | 3.1 s | 4.3 s | 2.3c | none: "no records" | | | |

Cost is in US cents, by the site's own prices. In both runs every citation was found in its source, no judgemental
word was flagged, and the question with nothing on the record was answered "no records" without a briefing. One
quotation in the second run was not found word for word, and was marked for the reader as such, as the check is
there to do.

### Differences in the number of sources

A model reads each question into a search afresh each time, and does not
always choose the same words or filters; the briefing is then written afresh too. With the reading held still, the
two versions find the same thing: with no model at all (the question read by the site's built-in rules), the old
code and the new chose exactly the same sources, in the same order, for all six questions (11, 8, 16, 10, 4 and
24). So the differences in the table are one run against another of a model, not one version of the site against
another, and six questions are far too few to say that either column is better.

The shared harness on the same questions (`HANSARD_EVAL_QUESTIONS=eval/sample-questions.toml hansard eval run
--max-cost 0.5 --record-fixtures …`): six asked, five
briefings and one "no records", no error; 59 citations, none not found; 57 quotations, none not found word for
word; five unsupported sentences in four of the five briefings; no loaded word; grounded 0.92 on average; the
first word after 3.2 seconds and the end after 9.9 (medians); 15 cents. The five briefings are kept in
`eval/fixtures/`, each with the sources the model was given and what the checks said of it: `hansard eval replay
eval/fixtures` runs the checks over them again, free, and the tests and CI do so on every change. A change to the
checks that says less of one of them than it said fails there.

### Cost

18 cents before, 17 cents after, 15 cents for the harness's run: about 51 cents in all.

### On the server

`hansard eval run` asks the whole test set (62 questions, about $2 to $3 at these prices; it stops at
`[eval] max_cost_usd`) and saves the run in `/data/eval/runs/`, on the data volume, where it is kept between
deploys. What it spends is not on the admin page and counts towards neither cap: the run says what it spent,
counting each of a briefing's three model calls, the check of registered interests among them (from wts-core 0.3.0).
`hansard eval compare A B` sets two runs side by side. The site's own `hansard eval plans`, `sources`, `retrieval`
and `briefings` are as they were.

## The devolved parliaments' lookup (16 November 2026)

### The slow step

The first evaluation run on the real archive kept readers waiting minutes before the first word
of a briefing whenever the question named a period, in proportion to the period's length ("coalition", 2010 to
2015: 276 s; the last Parliament: 229 s; this year: 30 s; no dates: 14 s; the median of all questions 5 s). Timed
step by step on the server, with the question already read, it was the lookup in the devolved parliaments'
record (`DevolvedStore.relevant`, which `Pipeline.find` runs beside the main search): 17.5 s for "austerity"
between 2010-05-06 and 2015-05-07, 80.7 s for "austerity public spending cuts", against 1.5 s for the main search
and under 1.5 s for each other lookup.

### The cause

For a question with dates, SQLite's query planner chose to walk every speech of the period in each
parliament by the (legislature, date) index, and to ask the full-text index, for each one in turn, whether it
matched. Each of those questions to the index costs a search of its own, and several words cost several (with three
words or more, the lookup also tries every way of leaving one word out, which makes twelve phrases for four words).
Without dates the planner did the reverse, read the matches from the full-text index and looked each one up, which
is fast. The code is unchanged since the first release; the record has grown.

### The change

The query now says which comes first (`speech_text CROSS JOIN speeches`, which SQLite always takes
in the order written): the matches from the full-text index, each looked up by its number, then the filters
(parliament, a member speaking, at least 300 characters, the dates) and the ranking (bm25, with the same weights) as
before. Nothing on the server changes: no index is rebuilt and no table altered. Ties in relevance now go to the
earlier stored speech with or without dates; before, with dates, they went to the earlier day. Exact ties are rare
(two speeches with the same number of words in each part and the same counts of the words searched for); the comparison below found none.

Measured on a synthetic record of the real one's size, made with the site's own schema (1.6 million speeches:
the Scottish Parliament from 1999, the Assembly from 1998, the Senedd from 2016; a vocabulary in which "austerity"
is in 19,793 speeches, mostly 2010 to 2016, "public" in 243,882, "spending" in 84,536 and "cuts" in 63,835; 1.9
GB), on a 4-vCPU test machine with the file in memory. `relevant` as the pipeline calls it for a summary (eight a
parliament):

| Lookup | Before | After | Found (the same, in the same order) |
|---|---|---|---|
| "austerity" | 0.11 s | 0.13 s | 24 |
| "austerity", 2010-05-06 to 2015-05-07 | 6.6 s | 0.10 s | 16 |
| "austerity public spending cuts" | 0.16 s | 0.17 s | 24 |
| "austerity public spending cuts", 2010-05-06 to 2015-05-07 | 241.7 s | 0.14 s | 16 |

The slowest after the change, of a few tried on the commonest words ("public"; "public services health budget
education", with and without dates), took under a second. On the same record, the old query and the new found
exactly the same contributions in the same order for 42 questions of one to three words drawn at random (32 with
dates, from one month to two years, and 10 without), each with a pool of eight a parliament and of twelve: 84
lookups, all the same, taking 619 s in all before and 16 s after ("health" over 2010 to 2015, both pool sizes: 99 s
before, 1.3 s after). The synthetic record makes four words slower against one than the server did (37 times,
against 4.6), because its common words are commoner; the cause is the same. On the server's disk, with less of the
file in memory, both columns will be slower; the size of the difference is the point.

### A time limit for every side lookup

So that no lookup can hold a briefing up like this again, `Pipeline.find`
now waits for the votes, early day motions, devolved parliaments and committee evidence only until
`[answer] side_lookup_seconds` (6) after the search began. One still running then is left out of that briefing,
which goes on with what the others found, and the log says so at WARNING ("side lookup 'devolved' took longer than
6 s: the briefing goes on without it"). The lookup cannot be stopped safely, so it finishes on its own thread and
its result is dropped. The lookups run on one pool of eight threads for the process, and while a lookup of one
kind is still running past its limit, that kind is left out of new briefings (with its own log line) rather than
given another thread: a slow kind holds one thread at most. The record of each briefing cannot yet say that a
lookup was left out (the shared package's record has no field for it); the log line is the trace.

## Measured performance

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
