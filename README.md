# Where MPs Stand

Where MPs Stand writes short, sourced briefings on what the UK Parliament has said about a subject, grouped by who
said it, and publishes pages built directly from the parliamentary record for every MP and peer, debate, bill and
vote since 1997. Every point in a briefing links to the Hansard record it comes from. It is a Where They Stand
project ([wheretheystand.uk](https://wheretheystand.uk)); Where They Stand is applying to register as a charity in
England and Wales.

Live site: [wherempsstand.com](https://wherempsstand.com)

Where MPs Stand is independent. It is not affiliated with or endorsed by the UK Parliament, and does not use
Parliament's logo or the crowned portcullis.

## Status

Beta. The site is open to everyone and in regular use, and is still being improved. The record is updated twice a
day.

## Features

A reader asks a question in their own words, such as "What have northern Labour MPs said about HS2?". The site reads
it into filters (dates, Houses, parties, regions, speakers, roles and seat details), which are shown as chips the
reader can change. It then chooses evidence across ministers, the opposition, committee chairs, backbenchers in city,
town and rural seats, and peers, and an AI model writes a briefing from those sources alone. Each citation and
quotation is checked word for word against the record, and statements that no source supports are counted under the
briefing. If nothing relevant is found, the site says "No records found." and no briefing is written.

Pages built from the record, with no AI, cover:

- MPs and peers: roles, seats and majorities, the party that came second, party groups and caucuses with their
  evidence, voting records and rebellions, registered interests since 2000, donations, company roles, all-party
  groups and early day motions;
- debates, bills with every sitting and vote, divisions with a neutral note on each kind of vote, and the week in
  Parliament;
- committees, their reports and the Government's responses, and the witnesses who gave evidence;
- the Scottish Parliament, the Senedd and the Northern Ireland Assembly;
- charts, comparisons and timelines of how often a subject is raised;
- a glossary, RSS feeds, a documented search API (`/developers`), and Word and PDF downloads of briefings.

The record covers the Commons from the 1997 general election, the Lords from November 1999, Westminster Hall from
2000, written answers from 2001 and written statements from 2002 in both Houses, Public Bill Committees, every
division (the Commons votes of 1997 to 2001 from Public Whip), the devolved parliaments from their first sittings,
oral evidence to select committees from 2014, early day motions from 1997, the Commons register of interests from
2000, political donations, and election results (general elections from 1997, by-elections from 2001).

## Architecture

```
 TheyWorkForYou scrapedxml ──► streaming parser ──► Parquet (zstd, by source and year) ──► DuckDB SQL
 (HTTPS, twice a day)          (lxml iterparse)            │
                                                           ▼
 mysociety/parlparse members/ ──► reference tables ──► speaker-on-date enrichment
 ONS / mySociety boundaries       (people, seats, offices,      │
                                   regions, parties)            ▼
                                                  LanceDB: BM25 passages + vectors (local model)
                                                           │
 question ─► filter plan (shown as editable chips) ─► deterministic retrieval and ranking
                                                           │
                         briefing written from the chosen sources only (Anthropic API),
                         with search-result citations ─► verification ─► web app
```

- `src/hansard/` is the Python package: ingestion, the search index, retrieval and ranking, the FastAPI app and the
  `hansard` command-line tool.
- `web/` is the front end (TypeScript, React for the briefing page, Vite), with static pages for the rest of the
  site.
- Settings, spending caps, the answer cache, saved briefings, the checks on briefings, the model client, the
  embedder, reports and the right of reply, backups and the evaluation harness come from
  [wts-core](https://github.com/rj-mitchell/wts-core), the package shared by the Where They Stand sites. Its version
  is fixed in `pyproject.toml`.
- `deploy/` holds the Docker Compose setup, the Caddy configuration and the systemd timers for the server.

[docs/METHOD.md](docs/METHOD.md) describes how the record is read, how evidence is chosen and how briefings are
checked.

## Running locally

With Python 3.12 or later, [uv](https://docs.astral.sh/uv/) and Node 22, the offline sample needs no downloads:

```bash
uv sync --extra embed
(cd web && npm ci && npm run build)
uv run hansard bootstrap --sample     # a small set of real Hansard excerpts and a simple offline embedder
uv run hansard serve                  # then open http://127.0.0.1:8000
```

Briefings need an Anthropic API key (`uv run hansard set-key`, or `HANSARD_ANTHROPIC_API_KEY`); without one the
site shows the chosen sources only. To run the full site with real Hansard in Docker, see
[docs/OPERATIONS.md](docs/OPERATIONS.md).

## Testing

```bash
uv run ruff check src tests scripts
uv run pytest                         # offline: the model is faked
cd web && npm run typecheck && npm test && npm run build
```

CI (`.github/workflows/ci.yml`) runs these, checks the copy of the family kit in `web/public/family/`, builds the
Docker image, starts the site in it on the sample data, and checks every page for accessibility problems with axe
(`scripts/a11y.mjs`).

## Deployment

The site runs in Docker on a server shared with the other Where They Stand sites, behind Caddy. Setting up a server
is described in [docs/HOSTING.md](docs/HOSTING.md), and day-to-day running, backups and deploying new code in
[docs/OPERATIONS.md](docs/OPERATIONS.md).

## Documentation

- [docs/METHOD.md](docs/METHOD.md): reading and searching the record, choosing evidence, and checking briefings
- [docs/DECISIONS.md](docs/DECISIONS.md): product decisions, with their dates
- [docs/DESIGN.md](docs/DESIGN.md): the visual design, the family kit and accessibility
- [docs/OPERATIONS.md](docs/OPERATIONS.md): running the site, commands, spending controls, backups and the state
  database
- [docs/HOSTING.md](docs/HOSTING.md): setting up the server, which is shared with the other sites
- [docs/COSTS.md](docs/COSTS.md): running costs and how spending is controlled
- [docs/EVALUATION.md](docs/EVALUATION.md): the test set of questions and what it measured

## Data sources and licences

Hansard and member data come from TheyWorkForYou and parlparse (mySociety) and the UK Parliament's APIs, under the
Open Parliament Licence. Other sources include the devolved parliaments, the Electoral Commission, Companies House,
the ONS and the House of Commons Library. [ATTRIBUTION.md](ATTRIBUTION.md) lists every source, its licence and how
it is credited.

## Known limitations

- Hansard's XML has no official URL for each speech. For debates, the official link opens the sitting day, and the
  column number and the TheyWorkForYou link identify the speech. Written questions and statements link to the exact
  official record.
- Region filters apply to MPs only, since peers have no constituencies.
- Party on a date comes from parlparse memberships, so any gaps there are gaps here. Committee and role data are as
  current as mySociety's daily updates.
- Seats before the 2010 boundaries have no ONS code. They take their region and seat type from the later seat of the
  same name, or their region from `data_static/geo/pre2010_regions.csv` (117 seats abolished or renamed, with no seat
  type). Posts of members who left before 2010 come from parlparse's `ministers.json`, and their select committee
  places from a saved copy of Parliament's records (`data_static/committees_before_2010.json`). Committee pages start
  in 2010.
- Sixteen MPs of 1997 to 2010 could not be matched safely to Parliament's records, so their pages show no election
  results.
- About 110 members since 2010, mostly peers, have no recorded date of birth and are left out of groupings by age.
- Of Public Whip's 1,273 divisions of 1997 to 2001, about 1,165 are matched to the result printed in Hansard. The
  rest keep Public Whip's title and link to the day.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

The code is licensed under the GNU Affero General Public License v3.0 ([LICENSE](LICENSE)): if you run a changed
version as a public website, you must offer its source code to its users under the same licence. Text written by the
site (briefings) is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); quotations from Hansard
remain under the Open Parliament Licence. Data is under each publisher's terms ([ATTRIBUTION.md](ATTRIBUTION.md)).
The name and mark are not covered by these licences.
