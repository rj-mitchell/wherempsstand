# Where MPs Stand

**Sourced briefings on what the UK Parliament has said, and who stands where.**

[wherempsstand.com](https://wherempsstand.com) · Status: Live (beta)

For journalists, researchers, policy analysts and anyone else who wants to understand what Parliament has said on a
subject. Ask a question in your own words, such as "What have northern Labour MPs said about HS2?", and get a short
briefing that groups the positions taken, by ministers, the opposition, backbenchers in city, town and rural seats,
and peers. Every point links to the Hansard record it comes from, and pages built straight from the record, with no
AI, cover every MP and peer, debate, bill and vote since 1997.

A Where They Stand project. Where They Stand is applying to register as a charity in England and Wales
([wheretheystand.uk](https://wheretheystand.uk)).

> Where MPs Stand is independent. It is not affiliated with or endorsed by the UK Parliament. It does not use
> Parliament's logo or the crowned portcullis.

## Status

6 November 2026. Live (beta) at wherempsstand.com: open to everyone and in regular use, still being improved. The
banner on every page links to the report form.

The record covers May 1997 (the 1997 general election) to today: the Commons, the Lords (from November 1999),
Westminster Hall (from 2000), written answers (from 2001) and statements (from 2002) in both Houses, Public Bill
Committees, and every division (vote), including the Commons votes of 1997–2001 from Public Whip. Also the Scottish
Parliament, the Senedd and the Northern Ireland Assembly (from their first sittings), oral evidence to select
committees (from 2014), early day motions (from 1997), registers of interests (the Commons register since 2000),
political donations, and election results (general elections from 1997, by-elections from 2001). It is updated twice
a day.

What is still to do is in [docs/STATUS.md](docs/STATUS.md).

## In the family

Where MPs Stand came first. It began in a private repository, `rj-mitchell/hansardgpt` (frozen on 19 October 2026),
was published as this repository on 19 October, and was called Where They Stand until October 2026, when that name
passed to the charity. The repository was renamed from `wheretheystand` on 6 November 2026. Where Countries Stand
adapted its briefing engine for the United Nations, and Where Councils Stand adapted Where Countries Stand's, with
some parts taken directly from this one (same licence).

**Where it runs.** On the charity's shared server in London, beside Where Councils Stand, Where Landlords Stand and
Where Countries Stand. This repository's `deploy/` is the front door for every site there (Caddy, email from the
server, alerts, disk checks and the update lock), so the other sites' deployment guides link to
[docs/HOSTING.md](docs/HOSTING.md).

**Links to and from the sister sites.**

- Every page carries the family binding from the charity's kit (`web/public/family/`): "A Where They Stand project"
  under the name, linking to wheretheystand.uk, and the family shelf in the footer, linking to every sister site and
  the charity's Donate page. It depends on the kit's files being synced in ([docs/DESIGN.md](docs/DESIGN.md)).
- The About page's "In the family" names and links each sister site.
- Under a briefing on local services, "Ask Where Councils Stand" asks the same question there; on foreign affairs,
  "Ask Where Countries Stand". These depend on both sites taking a question in `?q=` on their home page, and on a
  short rule for each (`web/src/sisters.ts`).
- Planned (the family plan, section 4): from an MP's page to the councils in the seat, and back, once there is a
  lookup from constituencies to local authorities.

**How it was built.** Most of Where MPs Stand was built with the assistance of
[Claude Code](https://claude.com/claude-code), Anthropic's AI coding assistant, using a $100 free credit grant
(available at the time of writing) and a $20-a-month Claude plan. Running the public site is a separate cost: the
server, and about 5 US cents for each briefing written. Many people could build something similar: if there is a
public record you think should be easier to search and understand, this repository is free to learn from and reuse
under its [licence](LICENSE).

## Documents

| Document | What it covers |
|---|---|
| [docs/DECISIONS.md](docs/DECISIONS.md) | The owner's product decisions, which override everything else |
| [ATTRIBUTION.md](ATTRIBUTION.md) | The data: every source, its licence and how it is credited |
| [docs/METHOD.md](docs/METHOD.md) | How the record is read and searched, how evidence is chosen and ranked, and how briefings stay grounded |
| [docs/DESIGN.md](docs/DESIGN.md) | The look, colour and type, the family binding, and the links to sister sites |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Running it locally or on a server, day-to-day commands, spending controls, backups and tests |
| [docs/HOSTING.md](docs/HOSTING.md) | Putting it online, and the shared server's front door for every site on it |
| [docs/COSTS.md](docs/COSTS.md) | What costs money, and how spending is controlled |
| [docs/EVALUATION.md](docs/EVALUATION.md) | The 62-question test set, what it showed, and measured performance |
| [docs/STATUS.md](docs/STATUS.md) | Open items |

## How it works

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


A question becomes a filter plan (dates, Houses, regions, parties, speakers, roles), shown as chips the reader can
change. The search then chooses evidence for balance across speaker groups, not keyword score alone, and Claude
writes the briefing from those sources only, with a citation for every point. Each citation and quotation is checked
word for word against the record, and statements not tied to a source are counted under the briefing. If nothing
relevant is found, the site says "No records found." instead of guessing. The details are in
[docs/METHOD.md](docs/METHOD.md).

Every example in a briefing is a `[Source n]` citation that opens the verbatim record, with the speaker's party,
seat and seat type, and their role, all **on that date**, the Hansard column number, and links to the official
record and to TheyWorkForYou.

Besides briefings, the site has free pages built from the record (no AI):

* **MPs and peers**: roles, seats and majorities, what they speak about most, recent contributions, voting record
  and rebellions, registered interests (as they stood on any date since 2000), donations, company roles, all-party
  groups, early day motions, evidence given to committees, and an RSS feed of what they say.
* **Debates** in full, **This week** in Parliament, **Bills** (every sitting and vote), **Charts** of how often a
  subject is raised (by party, House, nation, or across all five UK parliaments), **Compare** and **Timelines**.
* **Every vote explained**: its kind (amendment, second reading, programme motion…) read from the record, with a
  neutral note on what voting either way means, and mySociety's plain-English descriptions where they exist.
* **Early day motions** and who signed them; **Committees**, their reports, the government's responses and who gave
  evidence; the **devolved parliaments** to read and search.
* **Witnesses** to committees, searchable by keyword and meaning; **Donations** and **Company roles**; **All-party
  groups**; **Topic briefings** kept up to date; and a page of the record's lighter moments.
* A **glossary**, RSS feeds, a documented search **API** (`/developers`), Word and PDF downloads, and embed codes.

Briefings also flag where a speaker had a registered interest that may relate to what they said, and offer
follow-up questions that build on the briefing.

## Try it

With Python 3.12+, [uv](https://docs.astral.sh/uv/) and Node 20+, an offline sample needs no downloads and finishes
in seconds:

```bash
uv sync --extra embed
(cd web && npm ci && npm run build)
uv run hansard bootstrap --sample     # a small set of real Hansard excerpts, simple offline embedder
uv run hansard serve                  # open http://127.0.0.1:8000
```

Tests: `uv run pytest`, `uv run ruff check src tests scripts`, and in `web/`, `npm test` and `npm run typecheck`.
To run the real site with real Hansard on your own computer (with Docker), or on a server, see
[docs/OPERATIONS.md](docs/OPERATIONS.md).

## Known limitations

* **Official links.** Hansard's modern XML has no per-speech official URL. For debates, the official link
  opens the sitting day; the column number and the TheyWorkForYou link pinpoint the speech. Written questions
  and statements link to the exact official record.
* **Regions.** Region filters apply to MPs; peers have no constituencies.
* **Party data.** Party on a date comes from parlparse memberships, so the tool reflects any gaps in that
  source.
* **Recency of member data.** Committee and role data reflect what mySociety has published, refreshed daily.
* **Before 2010.** Seats before the 2010 boundaries have no ONS code: they take their region and seat type
  from the later seat of the same name, or their region from `data_static/geo/pre2010_regions.csv` (117 seats
  abolished or renamed; no seat type). Posts of members who left before 2010 come from parlparse's
  `ministers.json`, and their select committee places from a saved copy of Parliament's records
  (`data_static/committees_before_2010.json`). Committee pages start in 2010.
* **Elections before 2010.** Sixteen MPs of 1997–2010 could not
  be matched safely to Parliament's records, so their pages show no election results.
* **Votes of 1997–2001.** Of Public Whip's 1,273 divisions, about 1,165 are matched to the result printed in
  Hansard, which gives them their place in the debate and their kind of vote; the rest keep Public Whip's title and
  link to the day.

## Licence

Code: AGPL-3.0 ([LICENSE](LICENSE)). You may use, change and share it, and if you run a changed version as a public
website you must offer its source code to its users under the same licence. Text written by the site (briefings):
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); quotations from Hansard remain under the Open Parliament
Licence. Data: each publisher's terms ([ATTRIBUTION.md](ATTRIBUTION.md)). The site's [terms](web/terms.html) and
[privacy notice](web/privacy.html) give the details. The name and mark are not covered by these licences.
