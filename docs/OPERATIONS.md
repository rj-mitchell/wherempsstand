# Running Where MPs Stand

How to run the site on your own computer or a server, the commands for day-to-day work, how spending is controlled,
backups, and the tests.

The live site runs on a server shared with the charity's other sites. Putting it online, the server's front door,
email, alerts, the watchdog, disk checks and the update lock are in [HOSTING.md](HOSTING.md); what costs money is in
[COSTS.md](COSTS.md).

## Contents

1. [Running it on your own computer](#running-it-on-your-own-computer)
2. [Running it on a server](#running-it-on-a-server)
3. [Day-to-day operation](#day-to-day-operation)
4. [The state database](#the-state-database)
5. [New code](#new-code), and [the move onto the shared package](#at-deploy-the-move-onto-the-shared-package-the-version-of-november-2026)
6. [Development and tests](#development-and-tests)
7. [Names kept from earlier](#names-kept-from-earlier)

## Running it on your own computer

This runs the real site, with real Hansard, on your own Mac or Windows PC at <http://localhost:8000>. Only
your computer can open it. You need:

* [Docker Desktop](https://www.docker.com/products/docker-desktop/) (free for personal use). Install it and start
  it. If its *Settings → Resources* page offers a memory setting, give it at least 8 GB.
* Free disk space: about 5 GB for the most recent year or so, or about 40 GB for everything since 1997.
* An Anthropic API key for the written briefings. Searching works without one.

### 1. Get the code

On GitHub, use the green Code button, then Download ZIP, and unzip it.

### 2. Open a terminal in that folder

* Mac: open *Terminal*, type `cd ` (with a space), drag the unzipped folder onto the window, and press Return.
* Windows: open the unzipped folder, right-click an empty space and choose *Open in Terminal*.

### 3. Run these commands one at a time


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

* The whole archive since 1997: leave out `--from 2025-01-01`. The download is about 9–10 GB and takes
  a few hours; keyword search covers everything straight away.
* Better semantic matching: `docker compose run --rm app hansard embed --max-minutes 60` continues it, newest
  first, and can be repeated.
* Bring in the latest debates: `docker compose run --rm app hansard sync`. It keeps to the start date you chose.
* Stop: `docker compose down`. Your data is kept for next time; `docker compose up -d` starts it again.
* Remove everything, including the downloaded data: `docker compose down -v`.

Briefings are charged to your Anthropic account, at roughly 5 US cents each (Claude Sonnet 5.5). The same spending limits as the
public site apply.

### For developers: offline sample

With Python 3.12+, [uv](https://docs.astral.sh/uv/) and Node 22, this needs
no downloads and finishes in seconds:

```bash
uv sync --extra embed
(cd web && npm ci && npm run build)
uv run hansard bootstrap --sample     # a small set of real Hansard excerpts, simple offline embedder
uv run hansard serve                  # open http://127.0.0.1:8000
```

## Running it on a server

### Step-by-step guide

[HOSTING.md](HOSTING.md) covers ordering a server (written for a Hostkey VPS in
London), pointing the domains at it, and running the setup.

Recommended for this site alone: 4–8 vCPU, 16 GB RAM and 60 GB of disk. The live server is shared with the
charity's other sites, and Where MPs Stand's data, about 40 GB, is the largest part of it ([HOSTING.md](HOSTING.md),
"Disk space").

A smaller server (for example 2–4 vCPU and 8 GB) works with either or both of:
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

### Semantic-search build time

The initial embedding of about 4.7 million passages is resumable and runs
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
| `hansard embed --max-minutes 60` | Continue building semantic vectors. The index remembers which embedding model made its vectors and refuses another's; after a deliberate change of model, `hansard embed --start-again` drops the vectors and builds them afresh |
| `hansard reindex-vectors` | Rebuild the meaning-search index for the current number of vectors, measure each setting against an exact search, keep it only if it finds as much as before, and save the fastest setting. Each kept rebuild is recorded in `index/vector-index.json` |
| `hansard index-status [--text]` | The passages searched by meaning now, against the number at the index's last recorded rebuild, and whether to rebuild it (at 20% more). Reads no vectors; the monthly disk report prints its `--text` line |
| `hansard reference` | Rebuild member and role tables from parlparse; the next sync refreshes the changed people in the index |
| `hansard interests`, `devolved`, `evidence`, `edms`, `committees`, `donations`, `groups`, `whips`, `directorships` | Fetch one source now rather than waiting for the twice-daily update (each is also part of `sync`); options such as `interests --archive 500` or `devolved --files 5000` fill in the history in one go. They never run at the same time as an update |
| `hansard compress-raw [--dry-run] [--limit N]` | Once, on a mirror made before November 2026: gzip the raw Hansard files in place, keeping their dates, so nothing is read again. Run on the server under the update lock ([HOSTING.md](HOSTING.md), "Disk space", has the exact commands) |
| `hansard vectors-f16 [--dry-run] [--keep-old]` | Once, on an index made before November 2026: convert the meaning-search vectors (the passages' and the committee evidence's) from 32-bit to 16-bit numbers, about half their space, while the site keeps searching: a new copy is made, indexed and checked against the old (row counts, a sample of vectors, and the same searches finding the same passages), then swapped in one step. The passages' new index is measured and its settings saved as `reindex-vectors` does. Refuses if the disk is short of space; safe to stop and run again. Run on the server under the update lock ([HOSTING.md](HOSTING.md), "Disk space", has the exact commands) |
| `hansard statements list [--status pending\|published\|declined\|withdrawn\|removed\|all]`, `show ID`, `log ID` | Statements beside items (the right of reply), with the email address to confirm who sent each; one statement in full; everything done to one, in order |
| `hansard statements publish ID --checked "…" [--person N]`, `decline ID --reason "…"`, `update ID [--text-file F] [--name] [--role] [--link]`, `withdraw ID`, `remove ID --reason "…"` | Publish a statement as written once its sender is confirmed (`--person`: the member, when its address isn't their page), decline a waiting one with the reason for the author, replace it with the author's new words, withdraw it at their request, or take down a published one that breaks the rules (`remove`; the admin page's "Take down" does the same). Also on the admin page |
| `hansard reports list [--all]`, `show ID`, `close ID --note "…" [--outcome resolved\|dismissed] [--public-note "…"]` | Reports sent through "Report a problem": the open ones (requests to remove a private person's name or details first), one in full, and closing one with what was done. Also on the admin page |
| `hansard hide /b/ID --note "…"`, `unhide /b/ID`, `hidden`, `waiting` | Hide a saved briefing while a concern about it is decided, show it again, list what is hidden, and say what is waiting for a decision ([Hiding a briefing](#hiding-a-briefing)) |
| `hansard backup`, `restore`, `offsite`, `backup-check` | Back up the site's own records (saved briefings, reports, usage), restore them, check the off-site bucket, and check that backups are still being made (a timer runs the last each morning) |
| `hansard stats [--since 7d] [--json] [--themes] [--check]`, `weekly-report`, `tidy` | How briefings went, the week's report, and the nightly housekeeping ([How briefings went](#how-briefings-went)) |
| `hansard eval plans`, `eval sources`, `eval briefings` | The test set of 62 questions ([EVALUATION.md](EVALUATION.md)) |
| `hansard eval run [--max-cost N] [--record-fixtures DIR]`, `eval compare A B`, `eval replay DIR` | The whole test set through the whole pipeline, each briefing written (it spends money, up to `[eval] max_cost_usd`; runs are saved in `/data/eval/runs/`); two runs side by side; the checks run again over recorded briefings, free ([EVALUATION.md](EVALUATION.md)) |
| `hansard eval retrieval [--compare <earlier run>]` | The test set's searches alone, from saved question readings: free (no model is called and nothing is graded). Run it before and after a change to the search index, such as `vectors-f16`, and compare ([EVALUATION.md](EVALUATION.md), "Retrieval only") |

### Checking a change to the search index, free

`hansard eval retrieval` runs the test set's 62 searches from the
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

### Open access and cost control

The site is public. Searching costs nothing, but each new briefing is
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

### Shared briefings

Every completed briefing is saved at `/b/<id>` (ten unguessable characters), with its
sources, checks, the date prepared and the most recent record it drew on. The page carries its own title,
description and a 1200×630 preview card (`/b/<id>/card.png`, drawn with Pillow in the site's typefaces), and is
marked `noindex`. A briefing can be withdrawn with `POST /api/admin/briefings/<id>/withdraw` (admin token); its
page, data and card then return 410 Gone, and it is no longer played from the answer cache to the next reader with
the same question, whose briefing is written afresh. `GET /api/admin/briefings` lists recent ones.

<a id="hiding-a-briefing"></a>
### Hiding a briefing

While a concern about a briefing is being decided (a
complaint that needs checking, say), it can be hidden without being withdrawn:
`hansard hide /b/<id> --note "This briefing is not shown while a concern about it is looked into."` Its address
then shows the note in its place; it is left out of the topic pages, the featured briefings, the site map and the
feeds; a statement beside it is hidden with it; and the same question asked again is written afresh, not played
from the cache. `hansard unhide /b/<id>` shows it again exactly as it was, and `hansard hidden` lists what is
hidden, so that nothing is left hidden by oversight (`hansard waiting` counts them too). Hiding is not a
correction: nothing goes on the corrections page. Only a saved briefing can be hidden (a topic's briefing is one,
at its own `/b/` address). A page built from the official record (a debate, a member's page) is not hidden this
way: a request to take something of the record down is a report to answer ("Reports and statements", below).

### Reports and statements

The corrections and right-of-reply policy (the charity's impartiality policy, section
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
and on the member's page. Its email address is deleted three months after it is withdrawn, declined or removed, by
the nightly tidy, the twice-daily update and whenever statements are written or listed. A statement nobody has
decided within three months lapses: it is declined with the reason "No decision was made in time; please send it
again." and its sender's address is deleted; a report left open that long keeps its message and loses its contact
details. Everything done to a statement is kept in its log (`hansard statements log ID`). Statements live only in
`data/state.sqlite`, so backups include them; nothing that searches the record or writes briefings reads them
(`tests/test_privacy.py` checks that with the real pipeline).

### Backups

Everything can be rebuilt from public sources except `data/state.sqlite`, which holds the saved
briefings, reports, statements, usage, the answer cache, the record of each briefing, feedback, visit counts, the
sync log and any accounts ([The state database](#the-state-database)).
`hansard backup` makes a checked copy (with `settings.local.toml`) as `data/backups/where-they-stand-DATE.tar.gz`,
keeping the last 14; `hansard restore FILE` puts one back. On a server set up by `provision.sh`, a timer runs it
nightly into `deploy/backups/` (see [HOSTING.md](HOSTING.md#backups)). A restore checks the backup before it
replaces anything: a file that is damaged, or is not a backup of this site's records, is refused with
"Not restored: …" and nothing changes. The database it replaces is kept as `data/state.sqlite.before-restore`
(with its `-wal` file, if it had one). It holds readers' contact details like the database itself, and nothing
deletes it for you: delete it once the restored site is right. While it is there, another restore is refused
("… is still there from an earlier restore: move or delete it first"), so a second restore can never write over
the only copy of what the first one replaced. `hansard backup-check` says what the admin page says of backups
(none yet, the last one over two days old, the off-site copy failed or stale) and ends with status 1 when backups
have stopped; `hansard-backup-check.timer` runs it each morning, and the alert email goes if it fails.

<a id="how-briefings-went"></a>
### How briefings went

Every question leaves one record: how long each stage
took, what it cost, how many sources and how they were spread, what the checks found, and how it ended (written,
from the cache, "no records", sources only, paused, failed). It holds nothing about the reader. The question is
in it only where the site already kept it (a briefing saved or cached, or one that failed: that question is
removed after 90 days); records are kept 13 months, each with the hour it was asked in and no finer time.

* `hansard stats --since 7d` prints the figures for a period beside the four weeks before; `--json` for a
  program, `--themes` groups the questions asked by meaning (it loads the search model), `--check` ends with
  status 1 if a figure is over a threshold.
* `hansard weekly-report` saves the last whole week as a page and as JSON in `data/logs/reports/` (they quote
  readers' questions, so only the site's user can read them; kept 13 months). `hansard-weekly-report.timer` runs
  it on Monday mornings.
* Alerts come only from thresholds the owner has chosen, in `[alerts]` of `config/settings.toml` (or
  `data/settings.local.toml`): `p95_full_briefing_ms`, `error_rate`, `check_failure_rate`, `not_useful_rate`,
  `daily_cost_usd`. None is set at first: the weekly report proposes them from the week's figures, and prints
  the lines to copy. Once set, a weekly report over a threshold ends with status 1 and the alert email goes.
* `data/logs/briefings.jsonl` is a line of JSON for each stage a briefing reaches and for how it ended: an id,
  a stage's name, milliseconds, an error's class name. Never a question, an address or a visitor's code. Kept 14
  days or 50 MB, whichever is less.
* A lookup made beside the search (votes, early day motions, the devolved parliaments, committee evidence) that
  is still running `[answer] side_lookup_seconds` (6) after the search began is left out of that briefing, which
  goes on without it; `docker compose logs app | grep "side lookup"` lists each time it happened, by kind. A few
  are no cause for concern (a cold disk after a restart); many of one kind are worth timing that lookup
  ([EVALUATION.md](EVALUATION.md), "The devolved parliaments' lookup").
* "Was this briefing useful?", a report that names a briefing, its withdrawal, and a public note on a report of
  it are marked on that briefing's record, so the statistics can say how often each happens.
* `GET /api/check` gives counts and ages (what is waiting, how old the newest backup is, the last day's figures,
  and the newest weekly report's totals, numbers only, for the family's weekly summary) to whoever holds the
  family's check token, and to no one else: set `HANSARD_CHECK_TOKEN` in `deploy/.env` to
  turn it on. Without it the address is not there at all.

### The nightly tidy

`hansard tidy` runs after the nightly backup (`hansard-backup.service`), whether or not the
backup worked. It deletes what the privacy notice says is deleted in time, whether or not anyone visits: a
briefing's record after 13 months; the question of a failed one, and the note of the failure, after 90 days; on a
spending row after its day, the visitor's code, the signed-in account and the time of day (the row keeps its day,
and each account's calls and cost are kept as one line a calendar month, to the cent); every day's visitor secret
but today's; cached answers past `[usage] answer_cache_hours`; weekly reports after 13 months; a statement's
confirming address three months after it ends; what nobody decided in three months; the copy of the database made
before the move once it is 14 days old, and an unfinished copy left by a start that was cut short once it is a day
old (both in its line for copies); then the old tables, after the right of reply's deletions, so that what those
took tonight is gone from the old rows tonight. The order and the lines are wts-core's (from version 0.3.0). The
nightly backup, made just before, already does the same forgetting of the days before today in the database it
copies, so no backup holds yesterday's codes, accounts or seconds. It ends by writing the database file out afresh,
because SQLite does not wipe what it deletes: the bytes stay in the file's unused space, and in every backup of it,
until the file is rewritten. On a database of 430 MB (5,000 saved briefings, more than the live site has had time
to save) the whole tidy took under ten seconds on a test machine; the site goes on answering meanwhile. If another
connection is part-way through a query at that moment, the file's write-ahead log cannot be emptied: the tidy then
says "The database was NOT written out afresh tonight: the site was using it at that moment…" and tries again the
next night, ending with status 0 (one such night is ordinary). If that happens two nights running it ends with
status 1, so `hansard-backup.service` fails and the alert email goes: what was deleted should not sit in the log's
bytes for days unseen.

## The state database

`data/state.sqlite` is one SQLite file with a write-ahead log, opened by the web app and by the update. Two sets
of tables live in it:

* the site's own (`src/hansard/store/state.py`): the files of the record read in, the update history,
  accounts and sessions, visit and question counts, feedback, topic versions, featured briefings and supporter
  keys;
* the shared package's (wts-core; each named `wts_…`): spending against the caps, the answer cache, saved
  briefings, notes of failed briefings, the record of each briefing, reports, statements and their log, hidden
  briefings, and the day's visitor secret. The package makes them and looks after them; the site reads and writes
  them only through it (`src/hansard/store/wts.py`).

### The move (November 2026)

Before the shared package the site kept the second set in tables of its own
(`usage`, `answer_cache`, `briefings`, `failures`, `reports`, `statements`, and the visitor secret in `kv`). The
first time the new code opens a database that has them, it keeps a copy of the whole file beside it
(`data/state.pre-wts-core.<date>.sqlite`) and then copies their rows into the `wts_` tables, once, in one
transaction: every row, with the same ids, so every saved briefing keeps its address and every count carries on.
If a row cannot be copied nothing is copied, the site does not start, and the log says which row.

### The old tables afterwards

They stay in the file so that the move can be undone, and nothing reads them. So
that they do not keep what the site has promised to delete, the nightly tidy keeps them to the same promises (a
statement's address and a report's contact details go from the old row when they go from the new one; visitor
codes and their secrets after their day; notes of failures after 90 days), and on 1 February 2027 it drops
them. The copy made before the move is deleted by the tidy once it is 14 days old, as a backup on the server is.

<a id="going-back"></a>
### Going back, and coming forward again

Until 1 February 2027 the site can be taken back
to the code before the move, and brought forward again, without losing anything, if two rules are kept:

1. Going back: run `deploy/undo-wts-core.sql` before the old code is started, with the site stopped (the
   file's first lines give the commands). It writes the old tables again from the package's, so the old code finds
   everything the site still keeps, including what came in since the move; and it forgets the move, so that
   nothing is left half in one set of tables and half in the other. What it cannot carry back is said in the
   file: the log of what was done to each statement (but for how its sender was confirmed), and a hidden
   briefing is shown again on the old code. Then check out the earlier version and start it
   (`docker compose up -d --build`).
2. Coming forward again: just deploy. The new code makes the move afresh from the old tables, with whatever
   the old code has taken in meanwhile.

If the old code was started on a moved database without the undo (after a deploy that did not start, say), it
has written to the old tables only, and those rows are in no table the new code reads: a briefing saved then
would answer "no briefing at this address", and its cost would not count towards the caps. The new code looks for
this before it opens the database, and refuses to start, saying how many rows each side has taken in since the
move and naming the two ways out. `update.sh` meets the same refusal in its look before the restart, so the old
site is still running when it is found.

* If the new code never answered a reader after the move (the usual case: it did not start), keep the old code's
  rows: stop the site and run `deploy/redo-wts-core.sql`, which forgets the move so that the next start makes it
  again from the old tables. It discards only what the new tables alone hold, which is then nothing.
* If both have answered readers since the move, one side's new rows must go, and the message's counts say how
  many: `redo-wts-core.sql` keeps the old code's and discards the new code's (their saved briefings then answer
  "no briefing at this address", and their cost leaves the totals); `undo-wts-core.sql` keeps the new code's and
  discards the old code's. Each file says exactly what it discards. Neither can be run twice, or on a database
  that was never moved: it refuses, and changes nothing.

### Spending records after going back

Once a day is over the site keeps, on each of that day's spending
rows, no visitor's code, no account and no time of day (wts-core 0.3.0; the privacy notice says so). So the undo
writes a spending row of a day gone by into the old table with no `user_id`, no `visitor` and the day's midnight:
the old code's report of who the spending was for counts it among the calls that were nobody's. Its cost, token
counts and day are exact, and so are the daily and monthly caps. Which account an earlier day's call was for is
not known to anything any more; each account's calls and cost by the month (`wts_account_months`) have no place in
the old tables and are emptied with the spending rows, by both files, so that coming forward again does not count
them twice. Today's rows go back whole.

What the look for the old code at work cannot see, so do not rely on it in place of the undo:

* a row the old code wrote in the very second the move was made;
* a row with no time;
* and it can be wrong the other way: if the server's clock was put back between the old code's last write and the
  move, that row looks newer than the move and the site refuses to start though nothing was lost. The mend is the
  same and loses nothing: `redo-wts-core.sql`, then start again.

A row the old code changed that was already there (a briefing withdrawn, a report closed, a statement published,
updated or decided) is seen, by this site's own look; the shared package's guard, which decides a row added, sees
only rows added.

After 1 February 2027 the old tables are gone and neither file does anything: each checks that they are there
before it deletes anything, and on a database whose old tables have been dropped it fails at that check and
changes nothing. Going back then means restoring a backup made before the move, while one is kept (60 nights
off site). A backup made before the move can also be restored onto the new code at any time (`hansard restore`):
its rows are brought across at the next start.

## New code

`bash /opt/hansardgpt/deploy/update.sh` installs whatever is on `main`. The order it works in is the safeguard:

1. It refuses to start while one of the site's own jobs is running (the update, the backup, the backup check, the
   weekly report), and holds their timers while it works. Whatever goes wrong after that, the timers that were
   running are started again as they were.
2. It builds the new image. If the build fails nothing has changed.
3. It tries the new image against this server's own settings, changing nothing (`hansard check-settings`): the
   settings are read and checked as the site checks them when it starts (every limit and cap, prices for every
   model named, every message), and the state database is looked at read-only. If the new version would refuse to
   start, the deploy stops here: the old site is still running, and its database has not been touched.
4. It takes the server's update lock (`/var/lock/wheretheystand-update.lock`) and restarts the site on the new
   image. While the lock is held the watchdog restarts no site, and no site's update starts. If another site's
   update holds the lock, the deploy stops before anything is restarted: run it again when that has finished.
5. It waits up to ten minutes for the site to answer, the same as the health check's start period
   (`start_period: 10m` in `deploy/docker-compose.yml`), during which Docker reports the site as "starting" and the
   watchdog leaves it alone. A start is usually well under a minute; the room is for a first start that moves a
   large state database (3 seconds for 430 MB on a test machine: ten minutes is room for some 9 GB at a tenth of
   that speed).
6. Only once the site answers does it let the lock go, clear out old images, install this version's timer units,
   start the timers again, and run the health check.

<a id="if-the-new-site-does-not-answer"></a>If the new site does not answer, `update.sh` stops with the timers
as they were and nothing cleared out. Read the log first: `docker compose logs --tail 50 app`. To go back to the
version the site was on (the message names its commit):

```
cd /opt/hansardgpt
git checkout <the commit it names>
cd deploy && docker compose up -d --build     # quick: the earlier image's layers are still on the server
```

Before that, if the log shows that the site got as far as moving the state database (or
`docker compose run --rm --no-deps -T app hansard check-settings`, which changes nothing, says nothing is amiss),
find out whether the move was made: the file `state.pre-wts-core.<date>.sqlite` in the data folder means it was
started. If it was made, run `deploy/undo-wts-core.sql` before starting the old code (above, "Going back, and
coming forward again"); the file refuses, and changes nothing, on a database that was not moved, so running it is
the safe way to find out. Then `git checkout main` when the fault is mended, and deploy again.

A change to the state database (a new table, the one-off move above) is made by whichever command first opens
the database on the new code, not only by the site. So: do not deploy in the hours the timers run (3.30am the
backup, 6.10am on Mondays the weekly report, 7.15am and 7.15pm the update, 9.40am the backup check), and run no
`hansard` command of the new image by hand before the site has restarted on it. `update.sh` does neither.

### At deploy: the move onto the shared package (the version of November 2026)

This is the one deploy where the state database is moved, from the site's own code straight onto wts-core 0.3.0.
Read this through first; it takes a few minutes.

Before, on the site as it is running (old code; nothing is changed by any of this):

1. Look at what the move will meet (`update.sh` also tries the new version on the server's settings before it
   restarts anything, but what lapses is for you to see first):

   ```
   cd /opt/hansardgpt
   git fetch origin
   git show origin/main:deploy/before-wts-core.py | docker compose -f deploy/docker-compose.yml exec -T app python -
   ```

   It prints numbers, dates and sizes only. It says which statements and reports will lapse: once the new code
   is running, the first thing that writes to or lists reports and statements (a reader sending one, the admin
   page's lists, a `hansard statements` or `reports` command, the next update or the night's tidy) declines every
   statement that has waited more than three months and deletes its sender's address, and deletes the contact
   details of every report left open that long. That happens at that first write or listing, not on the first
   night, and cannot be undone. Decide any that still deserve a decision first, on the old code. It also says
   whether anything would stop the move (it ends with status 1 if so, with the mend), and how big the database
   is against the free space: the move needs about three times the database's size free for the first night.
2. If the server's own settings (`/data/settings.local.toml`) name a model for briefings or for reading questions
   that `config/settings.toml` has no prices for under `[usage.models]`, add its prices there first, as one
   table (`[usage.models."the-model-name"]` with the three prices): the site now refuses to start with a model it
   cannot cost, rather than cost it at another model's prices. The models in the repository's own settings
   (`claude-sonnet-5-5`, `claude-haiku-5-5`, `claude-opus-5-5`) all have theirs. A model whose name begins with
   one of those names is costed at that one's prices.
3. Take a backup now, so that the newest one is from just before: `sudo systemctl start hansard-backup.service`.
   (It runs on the old image, which copies the file as it is.)
4. Leave `/data/models` as it is, and keep leaving it until the check after the deploy says otherwise ([The models
   folder](#the-models-folder)): the old code fetched the re-ranker there, beside the embedding model. The search
   index carries no note yet of the model that made its vectors, so it takes the id this version gives the
   embedding model the first time it is opened, and search by meaning carries on whatever the folder holds.

#### The deploy

`bash /opt/hansardgpt/deploy/update.sh`, away from the timers' hours above. What is different
this once:

* The first start adds the shared package's tables, copies `state.sqlite` to `state.pre-wts-core.<date>.sqlite`
  beside it (readable by the site's own user only) and brings the rows across before the site answers. On a
  database of 430 MB (5,000 saved briefings) that took 3 seconds on a test machine; allow a minute on the server.
  `update.sh` waits up to ten minutes for the site to answer.
* The first model call after it (the first reader's briefing) forgets, on every spending row of a day before
  today, who it was for and the time of day: the visitor's code and the signed-in account go and the time becomes
  the day's midnight, and each account's calls and cost are added to its line for the month. The record of each
  briefing keeps the hour it was asked in. None of this can be undone; the totals, the caps and the reports by
  the day and the month are what they were. The admin page's spending "by who" for the month then reads each
  account's line from its month's total, to the cent, and the public's is what the rest came to, so it can be a
  cent or two out; the site's own work is its own line, as before.
* The database is then about twice its size until 1 February 2027 (the rows are in the old tables and the new),
  and the copy is as large again for 14 days.
* Two timers are new (`hansard-backup-check`, `hansard-weekly-report`), and the nightly backup now ends with
  `hansard tidy`. `update.sh` installs them after the site is up, so the site has opened its database on the new
  version before any of them runs. (The new backup does the night's forgetting in the database before it copies
  it; on a database with spending rows this version had not yet opened it would say so and end with status 1.
  Because the site starts first, it never meets one.)

#### Afterwards, look at


* the end of `update.sh`'s output: the health check, with a tick for each timer;
* `docker compose logs --tail 50 app`: no line about an import that was skipped or a row that could not be
  brought across;
* the admin page: today's and the month's spending, the recent briefings and the statements are what they were
  before the deploy; a saved briefing opens at its old address;
* `docker compose exec app hansard waiting`: what is waiting, after anything that lapsed;
* search by meaning: the health check's line "Search by meaning is on." (or
  `docker compose exec app hansard meaning-check`). If it says OFF, see
  [If search by meaning is off](#if-search-by-meaning-is-off) below;
* the models folder: the health check's line about it. Until it says anything else in `/data/models` "may be
  moved or removed", leave the folder alone ([The models folder](#the-models-folder));
* the next morning: `journalctl -u hansard-backup -n 30` shows the backup and then the tidy's lines: what it
  forgot and deleted (records, visitor codes and accounts on yesterday's spending rows, notes of failed
  briefings, old visitor secrets, cached answers, old copies of the database), the right of reply's line, what it
  did to the old tables, and last "Wrote the database out afresh: nothing deleted is left in the file." One night
  that says the database was NOT written out afresh is ordinary; two running fail the unit, and the email goes.

If the deploy stops:

* *At "Trying the new version against this server's settings"*: the new version would refuse to start, and says
  why (most often a model in `/data/settings.local.toml` with no prices). Nothing has been changed: the old site
  is running and its database is untouched. Mend what it names and run `update.sh` again.
* *At "Another site's update is running"*: nothing has been restarted. Run it again later.
* *At "The site did not answer"*: see [If the new site does not answer](#if-the-new-site-does-not-answer) above.
  If the log says a row could not be brought across, nothing was copied (the move is one transaction): the
  database is as it was, with an empty set of new tables and the copy `state.pre-wts-core.<date>.sqlite` beside
  it, and the earlier version runs on it. If the site failed for another reason after the move was made, the move
  stands: going back to the earlier version then needs `deploy/undo-wts-core.sql` first ([Going back, and
  coming forward again](#going-back)). Never start the earlier version on a moved database without it. If that
  has happened all the same, the new version refuses to start until it is settled, and says how.

To go back later, until 1 February 2027: [Going back, and coming forward again](#going-back), with the
undo first. What it gives back of the spending of days gone by (the day and the cost, without the account or the
visitor) is said there.

<a id="if-search-by-meaning-is-off"></a>If search by meaning is off. Straight after the deploy the health
check at the end of `update.sh` should show a tick beside "Search by meaning is on." The index now remembers
which embedding model made its vectors; the first start notes the model in use beside the index, and nothing is
embedded again. If instead it shows a cross, "Search by meaning is OFF: …", the index's vectors were made with
another model than the one the site now loads (the settings name another, or the model's files in
`/data/models` have changed), and every search is by words alone until that is settled. It is not left quiet:
`bash deploy/check.sh` fails on it, each update (`hansard-sync`) ends as failed and the alert email goes, and
the family's nightly check is told (`meaning_search_refused`). What to do:

1. `docker compose exec app hansard meaning-check` and `docker compose logs app | grep "search by meaning"` say
   which model the index was made with and which is in use.
2. If the settings or the model's files were changed by mistake, put them back (the model is named under
   `[embedding]`; its files are fetched once into `/data/models`), restart the site, and run `check.sh` again.
3. If the change was meant, make the vectors again with the model now in use, under the update lock as for any
   long job, at a quiet hour:

   ```
   cd /opt/hansardgpt/deploy
   nohup flock --verbose /var/lock/wheretheystand-update.lock docker compose run --rm -T app hansard embed --start-again < /dev/null > /root/embed-again.log 2>&1 &
   ```

   It takes as long as the first embedding did; search is by words meanwhile, and by meaning again as the vectors
   come back.

<a id="the-models-folder"></a>
#### The models folder

`/data/models` holds the embedding model's files. The site's
code before the shared package also fetched the re-ranker's there; the re-ranker now has a folder of its own
(`/data/reranker-models`, fetched afresh by the first update), and its old files are left in `/data/models`, where
nothing reads them. From wts-core 0.3.0 the id the index records for its vectors covers the embedding model's own
files only, where they can be told from the others; the index takes that id the first time it is opened on this
version (the first search by meaning), and the site's own note of the weights at the next update. Until both
have, change nothing in `/data/models`: moving the re-ranker's old files out before then changes what the
earlier note covered, and search by meaning goes off until every vector is made again. `hansard meaning-check`
(and so `check.sh`, in its line about the models folder) says which case the server is in:

* *"…the index and the site's note of the weights carry it: anything else … may be moved or removed."* The
  re-ranker's old files may now be deleted from `/data/models`, to free their space, or left.
* *"…has not yet taken that id on this version: change nothing…"* Wait for the next update (7.15am or 7.15pm), and
  run the check again.
* *"…cannot be told from another model's there…"* The id covers the whole folder, as before. Leave the folder
  exactly as it is, the re-ranker's files included. Giving each model a folder of its own means making every
  vector again (`hansard embed --start-again`, above): do the two together, as one planned step, when it suits.

Before a deploy, `docker compose run --rm --no-deps -T app hansard meaning-check --look` on the new image reads only
the index's note and the folder (no model is loaded and the state database is only read), and prints the id the
index is stamped with, the id this version opens it as, the one it accepts once, and the same line.

## Development and tests

```bash
uv sync --extra embed
uv run pytest                 # Python tests (offline; Claude is mocked)
uv run ruff check src tests scripts
cd web && npm ci && npm test && npm run typecheck
```

* Python tests. The fixtures are trimmed copies of real data (`scripts/make_fixtures.py`). They cover:
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
* What the old code gave. `tests/golden/` holds what the site's own code gave before it moved onto the shared
  package (the bytes sent for scripted and real questions, every refusal, the owner's commands' output, backups,
  a state database with rows of every kind), recorded by `tests/golden/record.py` run on that code.
  `tests/test_parity.py`, `test_replies_parity.py`, `test_import.py` and `test_ops.py` play the same scenarios on
  the code as it is and assert every difference by name; anything not named is the same.
* What is kept about a reader. `tests/test_privacy.py` runs the shared package's two checks over the real
  pipeline: nothing about a reader is kept but the day's code and the question, and a statement never reaches the
  model, the search index, the record or a log.
* The deploy. `tests/test_deploy.py` runs `deploy/update.sh` against stand-ins for git, docker and systemctl,
  and tests the look before the move and the way back.
* Live role-mapping check. `PARLPARSE_MEMBERS=/path/to/members uv run pytest tests/test_portfolios.py`
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
