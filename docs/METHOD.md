# How Where MPs Stand works

How the record is read, stored and searched, how the evidence for a briefing is chosen and ranked, and how a
briefing is kept to what the record says. The reader-facing version is the site's [methodology
page](https://wherempsstand.com/methodology) (`web/methodology.html`); this is the technical one. Moved here from
the README on 6 November 2026.

## Contents

1. [From the record to an answer](#from-the-record-to-an-answer)
2. [Ranking: recency and role weighting](#ranking-recency-and-role-weighting)
3. [How answers stay grounded](#how-answers-stay-grounded)
4. [Sources](#sources)

## From the record to an answer

```
 TheyWorkForYou scrapedxml ──► streaming parser ──► Parquet (zstd, by source & year) ──► DuckDB SQL
 (rsync or HTTPS, daily)        (lxml iterparse)            │
                                                           ▼
 mysociety/parlparse members/ ──► reference tables ──► speaker-on-date enrichment
 (git pull, daily)                (people, seats, offices,      │
 ONS / mySociety boundaries       regions, parties)             ▼
                                                  LanceDB: BM25 passages + vectors (local model)
                                                           │
 question ─► filter plan (Claude, shown as editable chips) ─► deterministic retrieval & ranking
                                                           │
                                       Claude (Sonnet 5.5) answers ONLY from the sources,
                                       with native search-result citations ─► verification ─► UI
```

* **Ingest.** `lxml.etree.iterparse` streams each file and frees elements as it goes, so memory stays flat
  however large the file is. Ingesting everything since 2010 (24,695 files, 3.7 million records) took about
  3 minutes with a peak of 664 MB across all worker processes, measured when the archive started in 2010
  ([EVALUATION.md](EVALUATION.md#measured-performance)).
* **Storage.** Zstd-compressed Parquet, partitioned by source and year. Each sitting day is replaced atomically
  when Hansard issues a correction. Days older than 90 days are compacted into one file per partition, so
  queries spanning many years run in tens of milliseconds.
* **Republished records.** About 37,000 written answers, mostly corrected Lords answers, are republished in a
  later day's file under the same ID. As on TheyWorkForYou, only the newest copy is searched and cited.
* **Identity.** Speakers are resolved **only by parlparse IDs**, never by name. One person ID covers every
  name and title change. For example, David Cameron is "David Cameron", MP for Witney, Prime Minister in 2012,
  and "Lord Cameron of Chipping Norton", Foreign Secretary in the Lords in 2024. Both are the same person.
* **Temporal joins.** Every open-ended or partial date is normalised: a missing or empty end becomes
  `9999-12-31`, and a year on its own like `"1983"` covers the whole year. Duplicate office rows in the source
  data are removed and overlapping spells merged. Each join is aggregated per record, so a minister with two
  posts never duplicates a speech.
* **Search.** LanceDB holds a BM25 full-text index over 4.7 million passages, plus vectors from a local
  open-source embedding model (`snowflake-arctic-embed-xs`, run on CPU). Both are filtered by date, House,
  region, party, person and role. A Labour filter also includes Labour/Co-operative MPs.
* **Balanced evidence.** The best-ranked candidates (eight per source wanted, at least 150) are grouped into
  government ministers, opposition frontbench, select committee chairs, backbench MPs in city, town and rural
  seats, and backbench peers. Members who raised the subject on three or more separate days among them (not
  counting what they said as ministers) are included first, up to four, so a backbencher who keeps returning to a
  subject is not crowded out; the briefing may say how often they raised it, and the evidence summary lists who
  raised it most. The rest of the evidence set is drawn round-robin across the groups, with at most two
  contributions per speaker and a relevance floor, so a briefing can compare groups instead of echoing one minister
  six times.
* **Seat type.** Every constituency in both boundary sets is classified from official statistics by the size of
  the urban area most of its residents live in, not by city status (`data_static/geo/constituencies.csv`):
  * **city:** the main urban area has 200,000+ people (ONS's "major" built-up area class). Reading, Milton
    Keynes and Bolton count; Bolton because it is part of the Greater Manchester built-up area;
  * **town:** a smaller urban area. Bath, Canterbury and Lichfield count, despite their city status;
  * **rural:** most residents live outside urban areas of 10,000+, the official ONS rural definition. Wells
    and Ely count.

  The urban areas are:
  * England and Wales: ONS's 2011 built-up areas, with 2011 Census populations. A conurbation stops at its
    own county: Bolton is part of Greater Manchester, but towns physically joined to London or another
    conurbation from outside it (Watford, St Albans, Woking, Guildford, Wilmslow) count as towns in their own
    right, using ONS's own sub-division populations;
  * Scotland: National Records of Scotland's Census 2022 settlements, where Greater Glasgow includes Paisley;
  * Northern Ireland: NISRA's 2015 settlements, where Belfast City is the only one of 200,000+.

  Each source's own lookups link small areas to both sets of seats. That gives about 315 city, 225 town and 110 rural seats in each set. Each source label names the urban area and its size. The rules and source URLs are in
  `scripts/build_geo_lookups.py`.

## Ranking: recency and role weighting

Ranking is soft. Nothing is excluded unless you set a filter.

```
score = relevance × recency × authority

relevance = ½ · BM25/max(BM25)  +  ½ · calibrated cosine similarity
recency   = 0.35 + 0.65 · 0.5^(age / half-life)           (slider: off … half-life 2 years)
authority = 1 + (W − 1) · m                               (slider W: 1.0× … 2.0×)
```

The authority multiplier `m` depends on the speaker's role on the date of the record:

| Speaker's role on the date | m |
|---|---|
| Government minister, shadow minister or select committee chair whose portfolio matches the question | 1.0 |
| Minister, shadow minister or chair, other portfolio | 0.5 |
| Ordinary member of a select committee on the question's subject | 0.3 |
| Backbencher | 0 |

* **Recency floor.** Because of the 0.35 floor, a much stronger 2011 match still beats a weak 2025 one.
* **Portfolios.** Departments are mapped to stable portfolios that survive renames. For example DECC → BEIS
  → DESNZ all map to `energy_climate`, so an energy question boosts the energy minister of every era. Every
  post-2010 role title in parlparse maps to a portfolio or is deliberately marked as having none, and a test
  enforces this.
* **Frontbench search.** When the question has a subject, its shadow ministers, and its ministers and committee
  chairs, are also searched by keyword on their own (apart, so ministers' many answers cannot crowd out the
  opposition), so the speeches setting out each side's position are considered even on a subject so widely
  debated that they fall outside the best few hundred passages of the whole record.
* **Transparency.** The source drawer shows each source's score breakdown.

## How answers stay grounded

1. **Editable filters.** The question becomes an editable filter plan: dates, Houses, regions, parties,
   speakers, roles and boosted portfolios. "Northern MPs" means the North East, North West, and Yorkshire and
   The Humber; you can change this in settings. You see and can edit every filter.
2. **Synthesis rules.** The model is told to synthesise rather than list, to group speakers by the attributes
   given in each source's title (role on the date, party, seat type, region), and to use no other attributes.
   It characterises each group's stance with one to three cited examples, preferably short exact quotations,
   and describes the strength of evidence honestly ("two MPs", "in the records retrieved").
3. **Neutral wording.** The model reports with plain verbs (said, argued, criticised) and may not use loaded or
   judgemental words in its own voice (lied, misled, U-turn, admitted, attacked and the like). Such words can
   appear only inside an exact, cited quotation from the speaker who used them. It never says an individual
   contradicted themselves; it gives what they said on each date. Fair and accurate reports of Parliament have
   legal protection; editorialising would fall outside it.
4. **British English.** Briefings and the one-line interpretation of each question are written in British
   English (spelling, vocabulary, and dates such as 9 October 2026). A final deterministic pass then converts
   any American spelling left in the model's own words (organize, program, percent, defense and so on). It never
   touches quotations, which stay exactly as Hansard records them, nor capitalised names such as the World
   Health Organization.
5. **Deterministic retrieval.** The same plan and sliders always give the same sources.
6. **Sources only.** Claude (Sonnet 5.5) receives only those sources, as search-result blocks with citations
   enabled, under rules that forbid outside knowledge. When the sources don't answer the question, it must say
   "No records found."
7. **Verification.** Every citation is re-checked against our copy of the source text. Any uncited
   substantive sentence is flagged in the UI as unsupported, never passed off as sourced.
8. **No guessing on empty results.** If retrieval finds nothing, the model is not called at all.

## Sources

* **Proceedings:** [TheyWorkForYou / ParlParse](https://parser.theyworkforyou.com/hansard.html) XML (mySociety),
  under the Open Parliament Licence v3.0. The Commons division lists of 1997–2001, which those files lack, come
  from [Public Whip](https://www.publicwhip.org.uk/)'s vote matrix (`data_static/votes`).
* **Members, offices and committees:** [mysociety/parlparse](https://github.com/mysociety/parlparse)
  `members/`, comprising `people.json`, `ministers-2010.json` and `posts/westminster-parliament-posts.json`.
* **Early day motions and committees:** the UK Parliament [oral questions and motions](https://oralquestionsandmotions-api.parliament.uk/)
  and [Committees](https://committees-api.parliament.uk/) APIs, under the Open Parliament Licence v3.0.
* **Register of Members' Financial Interests, devolved parliaments and vote descriptions:** TheyWorkForYou's
  published data (`pwdata/scrapedxml/regmem`, `sp-new`, `senedd`, `ni`, `pwdata/votes`), with thanks to mySociety.
* **Election results, members' gender and Parliament IDs:** the UK Parliament
  [Members API](https://members-api.parliament.uk/); general elections of 1997–2005 and by-elections of 2001–2010
  from the House of Commons Library, and by-elections of 1997–2001 from the Commons Information Office's Factsheet
  M16 (`data_static/elections`).
* **Political donations:** the Electoral Commission's register. **Company roles:** Companies House. **Dates of
  birth:** Wikidata. **All-party groups:** the Register of All-Party Parliamentary Groups.
* **Constituency → region:** ONS (Dec 2021 constituency names and codes) and mySociety's
  [2025-constituencies](https://github.com/mysociety/2025-constituencies) data, under the Open Government
  Licence v3.0. The vendored lookup in `data_static/geo/` is rebuilt by `scripts/build_geo_lookups.py`.

The app shows these attributions on every page; [ATTRIBUTION.md](../ATTRIBUTION.md) gives every source and its
licence in full.

Hugging Face is used only for a one-off download of the embedding model's weights, which are pinned by SHA-256. No
data comes from there.
