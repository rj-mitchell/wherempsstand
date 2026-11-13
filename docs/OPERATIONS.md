# Running Where MPs Stand

How to run the site on your own computer or a server, the commands for day-to-day work, how spending is controlled,
backups, and the tests. Moved here from the README on 6 November 2026.

The live site runs on a server shared with the charity's other sites. Putting it online, the server's front door,
email, alerts, the watchdog, disk checks and the update lock are in [HOSTING.md](HOSTING.md); what costs money is in
[COSTS.md](COSTS.md).

## Contents

1. [Running it on your own computer](#running-it-on-your-own-computer)
2. [Running it on a server](#running-it-on-a-server)
3. [Day-to-day operation](#day-to-day-operation)
4. [Development and tests](#development-and-tests)
5. [Names kept from earlier](#names-kept-from-earlier)

## Running it on your own computer

This runs the real site, with real Hansard, on your own Mac or Windows PC at <http://localhost:8000>. Only
your computer can open it. You need:

* [Docker Desktop](https://www.docker.com/products/docker-desktop/) (free for personal use). Install it and start
  it. If its *Settings → Resources* page offers a memory setting, give it at least 8 GB.
* Free disk space: about 5 GB for the most recent year or so, or about 40 GB for everything since 1997.
* An Anthropic API key for the written briefings. Searching works without one.

**1. Get the code.** On GitHub, use the green **Code** button, then **Download ZIP**, and unzip it.

**2. Open a terminal in that folder.**
* Mac: open *Terminal*, type `cd ` (with a space), drag the unzipped folder onto the window, and press Return.
* Windows: open the unzipped folder, right-click an empty space and choose *Open in Terminal*.

**3. Run these commands one at a time.**

```bash
docker compose build
docker compose run --rm app hansard set-key
docker compose run --rm app hansard bootstrap --from 2025-01-01 --embed-minutes 20
docker compose up -d
```

| Command | What it does | Time |
|---|---|---|
| `build` | Builds the app | 5–10 minutes the first time |
| `set-key` | Asks for your API key (it stays hidden as you paste it), checks it with Anthropic and saves it in the app's data folder on your computer | seconds |
| `bootstrap` | Downloads Hansard from 1 January 2025 and member data, builds the search index, and spends up to 20 minutes on semantic matching | about 30 minutes |
| `up -d` | Starts the site in the background | seconds |

Then open <http://localhost:8000>.

* **The whole archive since 1997:** leave out `--from 2025-01-01`. The download is about 9–10 GB and takes
  a few hours; keyword search covers everything straight away.
* **Better semantic matching:** `docker compose run --rm app hansard embed --max-minutes 60` continues it, newest
  first, and can be repeated.
* **Bring in the latest debates:** `docker compose run --rm app hansard sync`. It keeps to the start date you chose.
* **Stop:** `docker compose down`. Your data is kept for next time; `docker compose up -d` starts it again.
* **Remove everything**, including the downloaded data: `docker compose down -v`.

Briefings are charged to your Anthropic account, at roughly 5 US cents each (Claude Sonnet 5.5). The same spending limits as the
public site apply.

**For developers: offline sample.** With Python 3.12+, [uv](https://docs.astral.sh/uv/) and Node 20+, this needs
no downloads and finishes in seconds:

```bash
uv sync --extra embed
(cd web && npm ci && npm run build)
uv run hansard bootstrap --sample     # a small set of real Hansard excerpts, simple offline embedder
uv run hansard serve                  # open http://127.0.0.1:8000
```

## Running it on a server

**Step-by-step guide:** [HOSTING.md](HOSTING.md) covers ordering a server (written for a Hostkey VPS in
London), pointing the domains at it, and running the setup.

Recommended for this site alone: 4–8 vCPU, 16 GB RAM and 60 GB of disk. The live server is shared with the
charity's other sites, and Where MPs Stand's data, about 40 GB, is the largest part of it ([HOSTING.md](HOSTING.md),
"Disk space").

**A smaller server** (for example 2–4 vCPU and 8 GB) works with either or both of:
* recent years only: `hansard bootstrap --from 2019-01-01`. The start date is remembered, so later updates keep to it;
* keyword search only: `hansard bootstrap --keyword-only`. This skips meaning-based search (about 8 GB of vectors
  and a long one-off build). Questions that use different words from the speeches are matched less well.

| Data | Size |
|---|---|
| Raw XML since 1997 | about 9–10 GB as plain XML; about a quarter of that stored gzipped, as the sync now does ([HOSTING.md](HOSTING.md), "Disk space") |
| Parquet store | 1.2 GB |
| Keyword index | 7.9 GB (4.6 GB of passages, 3.3 GB of full-text and filter indices) |
| Vectors | about 8 GB |

Each sync deletes index versions more than an hour old. A large update, such as a big member reshuffle,
can temporarily add several GB until the next sync cleans it up.

On a fresh Ubuntu 24.04 server, with a domain name pointing at it:

```bash
sudo bash deploy/provision.sh
```

The script:
* asks for the main domain name, any other names that should redirect to it (www is added automatically),
  and the Anthropic API key (the key is not shown as you paste it);
* installs Docker;
* starts the app behind Caddy, which provides automatic HTTPS;
* runs the first data load in the background (30–60 minutes);
* installs a timer that runs the sync at 07:15 and 19:15 UK time.

**Semantic-search build time.** The initial embedding of about 4.7 million passages is resumable and runs
newest first. It takes roughly a day on 4 cores, and less on 8. Keyword search works from the start, and
recent years become semantically searchable within the first hours. Each sync continues the embedding for up
to 4 hours.

## Day-to-day operation

| Command | What it does |
|---|---|
| `hansard sync` | Full incremental update (this is what the timer runs): member data, new and corrected Hansard, index, embeddings, compaction, sync log |
| `hansard status` | Files ingested, indexed and embedded; parlparse commit; recent runs |
| `hansard search "…" --from 2018-01-01 --region "North East"` | Ranked sources from the terminal (no model call) |
| `hansard user add/list/disable` | Accounts, only if you switch on invite-only mode (argon2 password hashes) |
| `hansard embed --max-minutes 60` | Continue building semantic vectors |
| `hansard reindex-vectors` | Rebuild the meaning-search index for the current number of vectors, measure each setting against an exact search, keep it only if it finds as much as before, and save the fastest setting. Each kept rebuild is recorded in `index/vector-index.json` |
| `hansard index-status [--text]` | The passages searched by meaning now, against the number at the index's last recorded rebuild, and whether to rebuild it (at 20% more). Reads no vectors; the monthly disk report prints its `--text` line |
| `hansard reference` | Rebuild member and role tables from parlparse; the next sync refreshes the changed people in the index |
| `hansard interests`, `devolved`, `evidence`, `edms`, `committees`, `donations`, `groups`, `whips`, `directorships` | Fetch one source now rather than waiting for the twice-daily update (each is also part of `sync`); options such as `interests --archive 500` or `devolved --files 5000` fill in the history in one go. They never run at the same time as an update |
| `hansard compress-raw [--dry-run] [--limit N]` | Once, on a mirror made before November 2026: gzip the raw Hansard files in place, keeping their dates, so nothing is read again. Run on the server under the update lock ([HOSTING.md](HOSTING.md), "Disk space", has the exact commands) |
| `hansard vectors-f16 [--dry-run] [--keep-old]` | Once, on an index made before November 2026: convert the meaning-search vectors (the passages' and the committee evidence's) from 32-bit to 16-bit numbers, about half their space, while the site keeps searching: a new copy is made, indexed and checked against the old (row counts, a sample of vectors, and the same searches finding the same passages), then swapped in one step. The passages' new index is measured and its settings saved as `reindex-vectors` does. Refuses if the disk is short of space; safe to stop and run again. Run on the server under the update lock ([HOSTING.md](HOSTING.md), "Disk space", has the exact commands) |
| `hansard statements list [--status pending\|published\|declined\|withdrawn\|all]` | Statements beside items (the right of reply), with the email address to confirm who sent each |
| `hansard statements publish ID --checked "…" [--person N]`, `decline ID --reason "…"`, `update ID [--text-file F] [--name] [--role] [--link]`, `withdraw ID` | Publish a statement as written once its sender is confirmed (`--person`: the member, when its address isn't their page), decline or take one down with the reason for the author, replace it with the author's new words, or withdraw it at their request. Also on the admin page |
| `hansard backup`, `restore`, `offsite` | Back up the site's own records (saved briefings, reports, usage), restore them, or copy backups off the server |
| `hansard eval plans`, `eval sources`, `eval briefings` | The test set of 62 questions ([EVALUATION.md](EVALUATION.md)) |
| `hansard eval retrieval [--compare <earlier run>]` | The test set's searches alone, from saved question readings: free (no model is called and nothing is graded). Run it before and after a change to the search index, such as `vectors-f16`, and compare ([EVALUATION.md](EVALUATION.md), "Retrieval only") |

**Checking a change to the search index, free.** `hansard eval retrieval` runs the test set's 62 searches from the
question readings saved in `eval/runs` (no model reads the questions again and nothing is graded, so it costs
nothing) and saves what each found. On the server the test set is mounted into a one-off container, and the runs are
kept in the data volume. Before the change:

```
cd /opt/hansardgpt/deploy
docker compose run --rm -T -v /opt/hansardgpt/eval:/app/eval:ro app hansard eval retrieval --out /data/eval-runs
```

After it, the same with `--compare /data/eval-runs/<the first run's file>.json`, which prints how far the two
differ. For the 16-bit vectors the whole sequence, with the conversion between, is in [HOSTING.md](HOSTING.md),
"Disk space".

**Open access and cost control.** The site is public. Searching costs nothing, but each new briefing is
written by Claude and costs money. So, in `config/settings.toml`, `[usage]`:

* every 10 new briefings in a day, a visitor is shown a short note on the site's costs with the donation link
  (`support_reminder_every`); going through to donate stops it for the day;
* each visitor may have 40 new briefings a day, as a backstop against automated use. Visitors are told apart by a
  salted hash of their IP address; the address itself is never stored;
* each visitor may make 20 requests a minute;
* the whole site has a daily budget ($10) and a monthly budget ($150). When one is reached, briefings pause,
  and source search keeps working;
* identical questions with the same settings are answered from a cache for up to a week, cleared whenever new
  data arrives. Popular questions are paid for once;
* each briefing's token usage and cost is logged to SQLite.

Usage and the sync log are at `/api/admin/usage` and `/api/admin/sync`, with the header
`Authorization: Bearer $HANSARD_ADMIN_TOKEN` (`provision.sh` generates the token). For a private site instead,
set `[auth] required = true` and create accounts with `hansard user add`.

**Shared briefings.** Every completed briefing is saved at `/b/<id>` (ten unguessable characters), with its
sources, checks, the date prepared and the most recent record it drew on. The page carries its own title,
description and a 1200×630 preview card (`/b/<id>/card.png`, drawn with Pillow in the site's typefaces), and is
marked `noindex`. A briefing can be withdrawn with `POST /api/admin/briefings/<id>/withdraw` (admin token); its
page, data and card then return 410 Gone. `GET /api/admin/briefings` lists recent ones.

**Reports and statements.** The corrections and right-of-reply policy (the charity's impartiality policy, section
7; decision 31) is explained to the people it concerns on `/named`. Reports from the form on `/corrections`
(or under a briefing) are listed on the admin page and at `GET /api/admin/reports` (open requests to remove a
private person's name or details first, then newest first), and closed with
`POST /api/admin/reports/<id>/close`; a public note goes to the corrections log. Statements sent from
`/named` wait as pending: confirm who sent each (usually by writing to their parliament.uk, parliament.scot,
senedd.wales or niassembly.gov.uk address), then publish it as written or decline it with reasons, never editing it,
with `hansard statements` or the admin page (`/api/admin/statements`, `…/<id>/publish`, `decline`, `update`,
`withdraw`). Publishing needs a note of how the sender was confirmed (`--checked`, or `checked` on the admin
route): it is kept privately. A statement about a briefing or a member's page is accepted only if that briefing
exists and has not been withdrawn, or that member exists; the other kinds of page are not looked up, so check the
address before publishing. Reply within 5 working days and decide within 20. A published statement shows at once
beside its item
and on the member's page. Its email address is deleted three months after it is withdrawn or declined, by the
twice-daily update and whenever statements are written or listed. Statements live only in `data/state.sqlite`, so
backups include them; nothing that searches the record or writes briefings reads them.

**Backups.** Everything can be rebuilt from public sources except `data/state.sqlite`, which holds the saved
briefings, reports, statements, usage, the answer cache, feedback, visit counts, the sync log and any accounts.
`hansard backup` makes a checked copy (with `settings.local.toml`) as `data/backups/where-they-stand-DATE.tar.gz`,
keeping the last 14; `hansard restore FILE` puts one back. On a server set up by `provision.sh`, a timer runs it
nightly into `deploy/backups/` (see [HOSTING.md](HOSTING.md#backups)). A restore checks the backup before it
replaces anything: a file that is damaged, or is not a backup of this site's records, is refused with
"Not restored: …" and nothing changes. The database it replaces is kept as `data/state.sqlite.before-restore`
(with its `-wal` file, if it had one). It holds readers' contact details like the database itself, and nothing
deletes it for you: delete it once the restored site is right.

## Development and tests

```bash
uv sync --extra embed
uv run pytest                 # Python tests (offline; Claude is mocked)
uv run ruff check src tests scripts
cd web && npm ci && npm test && npm run typecheck
```

* **Python tests.** The fixtures are trimmed copies of real data (`scripts/make_fixtures.py`). They cover:
  * identity across name and title changes (Cameron);
  * role history and duplicate source rows (Benn);
  * concurrent posts (Greening);
  * open-ended and year-only dates;
  * boundary invariance across the 2024 review;
  * every file type in the parser;
  * incremental ingest, including corrections to already-compacted days;
  * memory that stays flat with file size;
  * composite scoring;
  * filters;
  * citation verification;
  * republished written answers (only the newest copy is kept);
  * the "no records found" path, refusals, auth and caps;
  * the daily sync with a member reshuffle, including a sync that fails part-way.
* **Live role-mapping check.** `PARLPARSE_MEMBERS=/path/to/members uv run pytest tests/test_portfolios.py`
  checks the role mapping against the current live data.

## Names kept from earlier

The site was called Where They Stand until October 2026, and began as `rj-mitchell/hansardgpt`. Some names from then
are kept because changing them would break something; don't copy them into new work:

| Name | What it is |
|---|---|
| `/opt/hansardgpt` | The site's checkout on the server, which also holds the front door for every site there ([HOSTING.md](HOSTING.md)) |
| `HANSARD_*` | The site's settings in `deploy/.env`, including the Backblaze B2 keys the other sites copy |
| `where-they-stand-DATE.tar.gz` | The site's backups |
| `hansard` | The Python package and command |
| `/etc/wheretheystand/`, `/var/lock/wheretheystand-update.lock` | The server's mail settings and update lock, for every site |
| `hgpt.settings`, `wts.kind`, `wts.compare` | Keys in readers' browsers; renaming them would reset readers' settings |
