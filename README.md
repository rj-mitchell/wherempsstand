# Where MPs Stand

Where MPs Stand writes short, sourced briefings on what the UK Parliament has said about a subject, grouped by who
said it, and publishes pages built directly from the parliamentary record for every MP and peer, debate, bill and
vote since 1997. Every point in a briefing links to the Hansard record it comes from. It is a project of
[Where They Stand](https://wheretheystand.uk), which is applying to register as a charity in England and Wales.

Live site: [wherempsstand.com](https://wherempsstand.com). Status: beta, open to everyone and in regular use; the
record is updated twice a day.

Where MPs Stand is independent. It is not affiliated with or endorsed by the UK Parliament, and does not use
Parliament's logo or the crowned portcullis.

## Features

A reader asks a question in their own words, such as "What have northern Labour MPs said about HS2?". The site reads
it into filters (dates, Houses, parties, regions, speakers, roles and seat details), shown as chips the reader can
change. It then chooses evidence across ministers, the opposition, committee chairs, backbenchers in city, town and
rural seats, and peers, and an AI model writes a briefing from those sources alone. Each citation and quotation is
checked word for word against the record, and statements that no source supports are flagged. If nothing relevant
is found, the site says "No records found." and no briefing is written.

Pages built from the record, with no AI, cover:

- MPs and peers: roles, seats and majorities, the party that came second, party groups and caucuses with their
  evidence, voting records and rebellions, registered interests, donations, company roles, all-party groups and
  early day motions;
- debates, bills with every sitting and vote, divisions with a neutral note on each kind of vote, and the week in
  Parliament;
- committees, their reports and the Government's responses, and the witnesses who gave evidence;
- the Scottish Parliament, the Senedd and the Northern Ireland Assembly;
- charts, comparisons and timelines of how often a subject is raised;
- a glossary, RSS feeds, a documented search API (`/developers`), and Word and PDF downloads of briefings.

The record covers the Commons from the 1997 general election, the Lords from November 1999, Westminster Hall from
2000, written answers from 2001 and written statements from 2002, Public Bill Committees, every division, the
devolved parliaments from their first sittings, select committee evidence from 2014, early day motions from 1997,
the Commons register of interests from 2000, political donations, and election results from 1997.

## How it works

The twice-daily update downloads Hansard's XML from TheyWorkForYou and member data from mySociety's parlparse, stores
the record as Parquet files queried with DuckDB, and indexes it in LanceDB for keyword search and meaning-based
search with a small local model. A question becomes a filter plan, retrieval and ranking pick a balanced set of
sources the same way every time, and Anthropic's model writes the briefing from those sources only, which the site
then checks. [docs/METHOD.md](docs/METHOD.md) has the details and a diagram.

- `src/hansard/` is the Python package: ingestion, the search index, retrieval and ranking, the FastAPI app and the
  `hansard` command-line tool.
- `web/` is the front end: TypeScript, with React for the briefing page and Vite, and static pages for the rest.
- [wts-core](https://github.com/rj-mitchell/wts-core), the package shared by the Where They Stand sites, provides
  settings, spending caps, the answer cache, saved briefings, the checks on briefings, the model client, the
  embedder, reports and the right of reply, backups and the evaluation harness, at the version fixed in
  `pyproject.toml`.
- `config/` holds the settings (`settings.toml`) and the topic briefings (`topics.toml`); `data_static/` holds
  lookups and saved copies of reference data; `eval/` holds the test set.
- `deploy/` holds the server's Docker Compose setup, Caddy configuration, scripts and systemd timers.

The Python package is called `hansard`, and the server checkout `/opt/hansardgpt`, for historical reasons
([docs/OPERATIONS.md](docs/OPERATIONS.md#names-kept-from-earlier)).

## Quick start

With Python 3.12 or later, [uv](https://docs.astral.sh/uv/) and Node 22, the offline sample needs no downloads and
no API key:

```bash
uv sync --extra embed
(cd web && npm ci && npm run build)
uv run hansard bootstrap --sample     # a small set of real Hansard excerpts and a simple offline embedder
uv run hansard serve                  # then open http://127.0.0.1:8000
```

Briefings need an Anthropic API key (`uv run hansard set-key`, or `HANSARD_ANTHROPIC_API_KEY`); without one the
site shows the chosen sources only. To run the full site with real Hansard in Docker, see
[docs/OPERATIONS.md](docs/OPERATIONS.md#running-the-full-site-locally). Tests and checks are in
[CONTRIBUTING.md](CONTRIBUTING.md).

## Documentation

[docs/README.md](docs/README.md) lists every document and who it is for. The main ones:

- [docs/METHOD.md](docs/METHOD.md): how the record is read and searched, how evidence is chosen, how briefings are
  checked, and the known limitations
- [docs/DECISIONS.md](docs/DECISIONS.md): product decisions, with their dates
- [docs/OPERATIONS.md](docs/OPERATIONS.md): running the site, deploying and backups
- [docs/HOSTING.md](docs/HOSTING.md): setting up the server shared with the other sites
- [CONTRIBUTING.md](CONTRIBUTING.md): setting up for development, tests and conventions

## Data sources and licences

Hansard and member data come from TheyWorkForYou and parlparse (mySociety) and the UK Parliament's APIs, under the
Open Parliament Licence. Other sources include the devolved parliaments, the Electoral Commission, Companies House,
the ONS and the House of Commons Library. [ATTRIBUTION.md](ATTRIBUTION.md) lists every source, its licence and how
it is credited.

The code is licensed under the GNU Affero General Public License v3.0 ([LICENSE](LICENSE)): anyone who runs a
changed version as a public website must offer its source code to its users under the same licence. Text written by
the site (briefings) is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); quotations from
Hansard remain under the Open Parliament Licence. Data is under each publisher's terms
([ATTRIBUTION.md](ATTRIBUTION.md)). The name and mark are not covered by these licences.
