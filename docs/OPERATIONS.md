# Running Where MPs Stand

How to run Where MPs Stand and look after it on the server: the commands, day-to-day tasks, readers' reports and
statements, backups, monitoring, deploying new code, and going back to the code from before wts-core. Setting up
the server, and what it runs for every site on it, is in [HOSTING.md](HOSTING.md). What costs money is in
[COSTS.md](COSTS.md).

On the server the site is checked out at `/opt/hansardgpt`, and its commands run inside the app container, from
`/opt/hansardgpt/deploy`: `docker compose exec app hansard <command>`. [Names kept from
earlier](#names-kept-from-earlier) explains the older names.

## Contents

1. [Running the full site locally](#running-the-full-site-locally)
2. [Commands](#commands)
3. [Day to day](#day-to-day)
4. [Saved briefings](#saved-briefings)
5. [Reports and statements](#reports-and-statements)
6. [Backups](#backups)
7. [How briefings went](#how-briefings-went)
8. [The state database](#the-state-database)
9. [New code](#new-code)
10. [If search by meaning is off](#if-search-by-meaning-is-off)
11. [Moving onto wts-core](#moving-onto-wts-core)
12. [Names kept from earlier](#names-kept-from-earlier)

## Running the full site locally

This runs the real site, with real Hansard, in Docker on a Mac or Windows PC at <http://localhost:8000>, reachable
from that computer only. For work on the code, the offline sample in the [README](../README.md#quick-start) is
quicker. It needs:

- [Docker Desktop](https://www.docker.com/products/docker-desktop/), running, with at least 8 GB of memory if its
  Settings, Resources page offers a memory setting;
- about 5 GB of free disk for the most recent year or so, or about 40 GB for everything since 1997;
- an Anthropic API key for written briefings (searching works without one).

Download the code (on GitHub: Code, Download ZIP), unzip it, open a terminal in that folder, and run these one at a
time:

```bash
docker compose build
docker compose run --rm app hansard set-key
docker compose run --rm app hansard bootstrap --from 2025-01-01 --embed-minutes 20
docker compose up -d
```

| Command | What it does | Time |
|---|---|---|
| `build` | Builds the app | 5 to 10 minutes the first time |
| `set-key` | Asks for the API key (hidden while pasted), checks it with Anthropic and saves it in the app's data folder | seconds |
| `bootstrap` | Downloads Hansard from 1 January 2025 and member data, builds the search index, and spends up to 20 minutes on meaning-based search | about 30 minutes |
| `up -d` | Starts the site in the background | seconds |

Then open <http://localhost:8000>.

- For the whole archive since 1997, leave out `--from 2025-01-01`. The download is about 9 to 10 GB and takes a
  few hours; keyword search covers everything straight away.
- `docker compose run --rm app hansard embed --max-minutes 60` continues meaning-based search, newest first, and can
  be repeated.
- `docker compose run --rm app hansard sync` brings in the latest records, from the start date chosen.
- `docker compose down` stops the site and keeps its data; `docker compose down -v` also deletes the data.

Briefings are charged to the Anthropic account at roughly 5 US cents each, with the same caps as the public site.

## Commands

Run `hansard --help`, or `hansard <command> --help`, for every option.

| Command | What it does |
|---|---|
| `hansard sync` | The twice-daily update (the timer runs it): member data, new and corrected Hansard, the index, embeddings, compaction and the sync log |
| `hansard status` | Files ingested, indexed and embedded; the parlparse commit; recent runs |
| `hansard search "…" --from 2018-01-01 --region "North East"` | Ranked sources in the terminal, with no model call |
| `hansard user add`, `list`, `disable` | Accounts, used only in invite-only mode (`[auth] required = true`) |
| `hansard embed --max-minutes 60` | Continue building meaning-search vectors. The index records which embedding model made its vectors and refuses another's; after a deliberate change of model, `--start-again` drops the vectors and builds them afresh |
| `hansard reindex-vectors` | Rebuild the meaning-search index for the current number of vectors, measure each setting against an exact search, and save the fastest that finds as much. Each rebuild is recorded in `index/vector-index.json` |
| `hansard index-status [--text]` | Passages searched by meaning now, against the number at the last rebuild, and whether to rebuild (at 20% more) |
| `hansard reference` | Rebuild member and role tables from parlparse; the next sync refreshes the changed people in the index |
| `hansard interests`, `devolved`, `evidence`, `edms`, `committees`, `donations`, `groups`, `whips`, `directorships` | Fetch one source now instead of waiting for the update (each is also part of `sync`). Options such as `interests --archive 500` fill in the history in one go |
| `hansard compress-raw [--dry-run] [--limit N]` | One-off: gzip raw Hansard files stored plain, keeping their dates, so nothing is read again ([HOSTING.md](HOSTING.md), "Disk space") |
| `hansard vectors-f16 [--dry-run] [--keep-old]` | One-off: convert a meaning-search index with 32-bit vectors to 16-bit, checking the new copy against the old before swapping it in |
| `hansard topics list`, `refresh [--only SLUG]` | Topic briefings (`config/topics.toml`): their state, or rewrite them now |
| `hansard statements …`, `hansard reports …` | The right of reply and readers' reports ([Reports and statements](#reports-and-statements)) |
| `hansard hide`, `unhide`, `hidden`, `waiting` | Hide a saved briefing while a concern is decided, and list what is hidden or waiting ([Saved briefings](#saved-briefings)) |
| `hansard backup`, `restore`, `offsite`, `backup-check` | Backups ([Backups](#backups)) |
| `hansard stats`, `weekly-report`, `tidy` | How briefings went, and the nightly housekeeping ([How briefings went](#how-briefings-went)) |
| `hansard check-settings` | Check the settings and look at the state database, changing nothing (`update.sh` runs it on a new version before restarting) |
| `hansard meaning-check` | Whether search by meaning is working ([If search by meaning is off](#if-search-by-meaning-is-off)) |
| `hansard eval plans`, `sources`, `retrieval`, `briefings`, `run`, `compare`, `replay` | The test set ([EVALUATION.md](EVALUATION.md)) |

## Day to day

The admin page, <https://wherempsstand.com/admin>, is the easiest way to keep an eye on the site. With the admin
token it shows spending and alerts, readers' reports, statements, feedback, visits, recent briefings, topics and
the update history, and can close reports, feature or withdraw a briefing, and decide statements. The token
is `HANSARD_ADMIN_TOKEN` in `/opt/hansardgpt/deploy/.env`, generated by `provision.sh`. The same data is at
`/api/admin/usage`, `/api/admin/sync` and `/api/admin/briefings`, with the header
`Authorization: Bearer <token>`.

On the server, from `/opt/hansardgpt/deploy`:

| Task | Command |
|---|---|
| Restart the site | `docker compose restart app` |
| Run the health check | `bash /opt/hansardgpt/deploy/check.sh` (changes nothing; ✓ for what works, ✗ with what to do) |
| See whether an update is running, and how the last one ended | `systemctl status hansard-sync --no-pager` (`active` while it runs or waits for another site's update; `failed` with `status=137` means it ran out of memory: run `bash /opt/hansardgpt/deploy/swap.sh` and start it again) |
| Start an update now | `systemctl start --no-block hansard-sync.service`, then `journalctl -u hansard-sync -f` |
| See how much is still to do | `docker compose run --rm -T app hansard status` (`files_unembedded` falls to 0 as meaning-based search catches up, up to 4 hours of it per update) |
| Rebuild the meaning-search index, when the monthly disk report says so (about 30 to 60 minutes; the site works throughout) | `docker compose exec -d app sh -c 'mkdir -p /data/logs && hansard reindex-vectors > /data/logs/reindex.log 2>&1'`; when it has finished, `docker compose exec app cat /data/logs/reindex.log`, then `docker compose restart app` |
| Fetch a source's history in one go (free; hours, so in the background) | `docker compose exec -d app hansard devolved --files 5000`, `committees`, `interests --archive 500`, `whips --count 20000`, `directorships --count 4000` or `edms --months 400 --signatures 60000`; committee evidence with `docker compose exec -d app sh -c 'mkdir -p /data/logs && hansard evidence > /data/logs/evidence.log 2>&1'` |
| See which background jobs are still running (nothing printed means none) | `docker compose exec app sh -c 'for p in /proc/[0-9]*; do tr "\0" " " < $p/cmdline 2>/dev/null; echo; done \| grep "[h]ansard "'` |
| Check that early day motions can be read from Parliament | `docker compose exec app hansard edms --check` |
| Rewrite the topic briefings now (about 5p each) | `docker compose exec app hansard topics refresh` (add `--only cost-of-living` for one) |
| Add, change or remove a topic | Edit `config/topics.toml` and deploy. Never change a published topic's `slug`: it is its address |
| Feature a briefing (listed on the Topics page and open to search engines) | The admin page: "Feature" beside a recent briefing |
| Remove a company role matched to the wrong member | Add a line to `data_static/directorships_exclude.csv` (the file says what goes in each column) and deploy; the role goes at the next update |
| See the company roles found for a name | `docker compose exec app hansard directorships --show "Smith"` |
| Change the Anthropic API key | `read -rsp "Anthropic API key: " K && echo && sed -i "s\|^HANSARD_ANTHROPIC_API_KEY=.*\|HANSARD_ANTHROPIC_API_KEY=$K\|" .env && unset K && docker compose up -d`, then `check.sh` should say "Anthropic accepts the API key" |
| Set the Companies House API key (free, from developer.company-information.service.gov.uk) | `read -rsp "Companies House API key: " K && echo && sed -i -e '/^HANSARD_COMPANIES_HOUSE_KEY=/d' -e '$a\' .env && printf 'HANSARD_COMPANIES_HOUSE_KEY=%s\n' "$K" >> .env && unset K && docker compose up -d` |
| Point "Support this site" somewhere other than the charity's Donate page | Add `HANSARD_DONATE_URL=https://…` to `.env`, then `docker compose up -d` |
| Change a spending cap or limit | Edit `[usage]` in `config/settings.toml` and deploy ([COSTS.md](COSTS.md)) |

For a private, invite-only site, set `[auth] required = true` and create accounts with `hansard user add`.

## Saved briefings

Every completed briefing is saved at `/b/<id>` (ten unguessable characters), with its sources, checks, the date
prepared and the most recent record it drew on. Its page has its own title, description and preview card
(`/b/<id>/card.png`), and is marked `noindex` unless featured.

To withdraw a briefing for good (after a complaint, say), use the admin page or
`curl -X POST -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d '{"reason": "…"}' https://wherempsstand.com/api/admin/briefings/<id>/withdraw`.
Its page, data and card then return 410 Gone, the reason is shown publicly on the corrections page, and the same
question asked again is written afresh.

<a id="hiding-a-briefing"></a>
### Hiding a briefing

While a concern about a briefing is decided, it can be hidden without being withdrawn:

```bash
docker compose exec app hansard hide /b/<id> --note "This briefing is not shown while a concern about it is looked into."
```

Its address then shows the note; it is left out of topic pages, featured briefings, the site map and the feeds; a
statement beside it is hidden too; and the same question is written afresh. `hansard unhide /b/<id>` shows it again
as it was, `hansard hidden` lists what is hidden, and `hansard waiting` counts it with everything else waiting for
a decision. Hiding is not a correction, so nothing goes on the corrections page. Only saved briefings can be hidden;
a request to take down something from the official record (a debate, a member's page) is a report to answer.

## Reports and statements

The corrections and right-of-reply policy is the charity's impartiality policy, section 7
([DECISIONS.md](DECISIONS.md), 31), explained to the people it concerns on `/named`.

Reports come from the form on `/corrections` or under a briefing. The admin page and `hansard reports list` show
the open ones, requests to remove a private person's details first. Close one with what was done:
`hansard reports close <id> --note "…" [--outcome resolved|dismissed] [--public-note "…"]`; a public note goes to the
corrections log.

Statements sent from `/named` wait as pending. For each:

1. Confirm who sent it, usually by writing to their parliament.uk, parliament.scot, senedd.wales or
   niassembly.gov.uk address. Reply within 5 working days and decide within 20.
2. Publish it as written, `hansard statements publish <id> --checked "<how the sender was confirmed>"` (add
   `--person <id>` when the item is not the member's own page), or decline it, `hansard statements decline <id>
   --reason "…"`. Statements are never edited. The admin page does the same.
3. Later, `update <id> --text-file <file>` replaces it with the author's new words, `withdraw <id>` removes it at
   their request, and `remove <id> --reason "…"` takes down a published one that breaks the rules.

A statement about a briefing or a member's page is accepted only if that briefing or member exists; other pages are
not looked up, so check the address before publishing. `hansard statements list`, `show <id>` and `log <id>` show
the statements, one in full, and everything done to one.

The rules on time are wts-core's: a sender's email address is deleted three months after a statement is withdrawn,
declined or removed; a statement nobody decides in three months is declined with the reason "No decision was made
in time; please send it again."; and a report left open that long loses its contact details. Statements are kept
only in the state database, so backups include them, and nothing that searches the record or writes briefings
reads them (`tests/test_privacy.py`).

## Backups

Everything can be rebuilt from public sources except the state database, `data/state.sqlite` ([The state
database](#the-state-database)). `hansard-backup.timer` backs it up every night at 3.30am, with
`data/settings.local.toml`, as `where-they-stand-<date>.tar.gz` in `/opt/hansardgpt/deploy/backups`, keeping the
last 14. Each backup is also copied to a Backblaze B2 bucket, once that is set up, which keeps the last 60. Each
morning at 9.40am `hansard-backup-check` checks that backups and their off-site copies are still being made; if not,
it fails and the alert email goes. The admin page warns too.

To make a backup now: `docker compose run --rm -T app hansard backup --to /backups`.

### Setting up the off-site copy

1. At backblaze.com, create a private B2 bucket and an application key allowed to access only that bucket.
2. On the server, run this. It asks for the bucket name, keyID and applicationKey (the key is hidden while pasted)
   and saves them in `deploy/.env`:

   ```bash
   cd /opt/hansardgpt/deploy && read -rp "B2 bucket name: " B && read -rp "B2 keyID: " I && read -rsp "B2 applicationKey: " K && echo && sed -i -e '/^HANSARD_B2_/d' -e '$a\' .env && printf 'HANSARD_B2_BUCKET=%s\nHANSARD_B2_KEY_ID=%s\nHANSARD_B2_APPLICATION_KEY=%s\n' "$B" "$I" "$K" >> .env && unset B I K
   ```

3. `docker compose run --rm -T app hansard offsite` should say `"ok": true`.
4. Make a backup now (above). It should end "Off-site copy: stored in …".

### Restoring a backup

```bash
cd /opt/hansardgpt/deploy
docker compose stop app
docker compose run --rm -T app hansard restore /backups/where-they-stand-2026-11-16.tar.gz --yes
docker compose start app
```

`ls backups` lists the backups. After losing the server, download the newest backup from the B2 bucket into
`/opt/hansardgpt/deploy/backups/` on the new server first.

A restore checks the backup before it replaces anything: a damaged file, or one that is not this site's backup, is
refused with "Not restored: …" and nothing changes. It needs free space of about twice the database's size. The
database it replaces is kept as `state.sqlite.before-restore` in the data folder. That copy holds readers' contact
details, so delete it once the restored site is right
(`docker compose run --rm -T app sh -c 'rm -f /data/state.sqlite.before-restore*'`). While it is there, another
restore is refused, so the only copy of what the first restore replaced is never overwritten.

## How briefings went

Every question leaves one record: how long each stage took, what it cost, how many sources and how they were
spread, what the checks found, and how it ended. It holds nothing about the reader, and keeps only the hour it was
asked in. The question is kept only where the site already keeps it (a saved, cached or failed briefing). Records
are kept 13 months.

- `hansard stats --since 7d` prints the figures for a period beside the four weeks before (`--json` for a program,
  `--themes` to group the questions by meaning, `--check` to end with status 1 when a figure is over a threshold).
- `hansard weekly-report` saves the last whole week as a page and as JSON in `data/logs/reports/`, readable only by
  the site's own user, since they quote readers' questions. `hansard-weekly-report.timer` runs it on Monday
  mornings.
- Alert thresholds are set by the owner in `[alerts]` of `config/settings.toml` (or `data/settings.local.toml`):
  `p95_full_briefing_ms`, `error_rate`, `check_failure_rate`, `not_useful_rate` and `daily_cost_usd`. None is set
  until chosen; the weekly report proposes values and prints the lines to copy. A weekly report over a threshold
  ends with status 1, and the alert email goes.
- `data/logs/briefings.jsonl` has a line of JSON for each stage a briefing reaches and for how it ended, never a
  question, an address or a visitor's code, kept 14 days or 50 MB.
- A lookup made beside the search (votes, early day motions, the devolved parliaments, committee evidence) still
  running `[answer] side_lookup_seconds` (6) after the search began is left out of that briefing.
  `docker compose logs app | grep "side lookup"` lists each time. Many of one kind are worth timing
  ([EVALUATION.md](EVALUATION.md), "The devolved parliaments' lookup").
- `GET /api/check` gives counts and ages (what is waiting, the newest backup's age, the last day's figures) for the
  family's weekly summary, only to the holder of `HANSARD_CHECK_TOKEN` in `deploy/.env`. Without the token set the
  address does not exist.

### The nightly tidy

`hansard tidy` runs after the nightly backup, whether or not the backup worked. It deletes what the privacy notice
says is deleted in time: briefing records after 13 months; failed questions after 90 days; visitor codes, accounts
and times of day on spending rows once their day is over; old visitor secrets; cached answers past
`[usage] answer_cache_hours`; weekly reports after 13 months; and the right of reply's expired details. It then
writes the database file out afresh, since SQLite leaves deleted bytes in the file until it is rewritten.
`journalctl -u hansard-backup -n 30` shows its lines. If the site is using the database at that moment, the file
cannot be rewritten: one night of "The database was NOT written out afresh tonight" is ordinary; two nights running
fail `hansard-backup.service`, and the alert email goes.

## The state database

`data/state.sqlite` is one SQLite file, opened by the web app and by the update. It holds two sets of tables:

- the site's own (`src/hansard/store/state.py`): the files of the record read in, the update history, accounts and
  sessions, visit and question counts, feedback, topic versions, featured briefings and supporter keys;
- wts-core's, each named `wts_…`: spending, the answer cache, saved briefings, notes of failed briefings, the record
  of each briefing, reports, statements and their log, hidden briefings and the day's visitor secret. The site
  reads and writes them only through the package (`src/hansard/store/wts.py`).

A change to the database (a new table, or the [move onto wts-core](#moving-onto-wts-core)) is made by whichever
command first opens it on the new code, not only by the site. So never run a `hansard` command of a newly built
image by hand before the site has restarted on it; `update.sh` keeps to this.

## New code

The server runs whatever is on `main` at <https://github.com/rj-mitchell/wherempsstand>. To deploy it, on the
server:

```bash
bash /opt/hansardgpt/deploy/update.sh
```

Do not deploy while the site's timers run (3.30am the backup, 6.10am on Mondays the weekly report, 7.15am and
7.15pm the update, 9.40am the backup check). `update.sh`:

1. refuses to start while one of the site's jobs is running, and holds their timers while it works (they are
   started again whatever happens);
2. builds the new image (if that fails, nothing has changed);
3. tries the new image against the server's own settings with `hansard check-settings`, changing nothing; if the new
   version would refuse to start (most often a model in `/data/settings.local.toml` with no prices under
   `[usage.models]`), it stops here with the old site still running;
4. takes the server's update lock, so that the watchdog and other sites' updates wait, and restarts the site (if
   another site's update holds the lock, it stops here and can be run again later);
5. waits up to ten minutes for the site to answer;
6. then releases the lock, clears out old images, installs this version's timers and runs the health check.

### If the new site does not answer

`update.sh` stops with the timers as they were and the old image still on the server. Read the log first:
`docker compose logs --tail 50 app`. To go back to the version the site was on (`update.sh` names its commit):

```bash
cd /opt/hansardgpt
git checkout <the commit it names>
cd deploy && docker compose up -d --build
```

If the failed deploy was the first on wts-core and the database was moved, run `deploy/undo-wts-core.sql` first
([Going back, and coming forward again](#going-back-and-coming-forward-again)). When the fault is mended,
`git checkout main` and deploy again.

## If search by meaning is off

The health check at the end of `update.sh` (and `check.sh`) should show a tick beside "Search by meaning is on."
A cross, "Search by meaning is OFF: …", means the index's vectors were made with a different embedding model from
the one the site now loads (the settings name another, or the model's files in `/data/models` changed), and every
search is by words alone. Each update then ends as failed, the alert email goes, and the family's nightly check is
told.

1. `docker compose exec app hansard meaning-check` and `docker compose logs app | grep "search by meaning"` say
   which model made the index and which is in use.
2. If the settings or the model's files were changed by mistake, put them back (the model is named under
   `[embedding]`), restart the site and run `check.sh` again.
3. If the change was meant, make the vectors again with the model now in use, at a quiet hour, under the update
   lock. It takes as long as the first embedding; search works by words meanwhile.

   ```bash
   cd /opt/hansardgpt/deploy
   nohup flock --verbose /var/lock/wheretheystand-update.lock docker compose run --rm -T app hansard embed --start-again < /dev/null > /root/embed-again.log 2>&1 &
   ```

## Moving onto wts-core

The site moved onto wts-core (version 0.3.0) in November 2026: the first start of the new code moved the state
database into wts-core's tables. Until 1 February 2027 the move can be undone and the site put back on its earlier
code, which is kept in the private archived repository `rj-mitchell/wherempsstand_old`. Two files in `deploy/`
serve this:

- `undo-wts-core.sql`, which takes the database back for the earlier code;
- `redo-wts-core.sql`, which makes the move again if the earlier code wrote to a moved database.

Each file's opening comment says exactly what it does, keeps and discards.

### What the move does

On its first start the new code copies `state.sqlite` to `state.pre-wts-core.<date>.sqlite` beside it, then copies
every row of the old tables (`usage`, `answer_cache`, `briefings`, `failures`, `reports`, `statements`, and the
secret in `kv`) into the `wts_` tables, in one transaction, keeping their ids, so every saved briefing keeps its
address. If a row cannot be copied, nothing is, the site does not start, and the log names the row. The same
happens when the site comes forward again after going back (below).

The old tables stay in the file, unread, so the move can be undone. Each night the tidy deletes from them what it
deletes from the new tables, deletes the copy once it is 14 days old, and on 1 February 2027 drops the old tables.
Until then the database is about twice its size.

The first model call after the move forgets, on every spending row of an earlier day, the visitor's code, the
account and the time of day ([DECISIONS.md](DECISIONS.md), 34). Totals, caps and reports by day and month are
unchanged. The first time anything writes or lists reports and statements, statements that have waited more than
three months are declined and their senders' addresses deleted, and reports open that long lose their contact
details. Neither can be undone.

### The models folder

`/data/models` holds the embedding model's files, and may also hold an old copy of the re-ranker's that the
earlier code fetched there (the re-ranker now has `/data/reranker-models`). The index records which model made its
vectors, and the site notes the model's weights. Change nothing in `/data/models` until the health check's line
about the models folder (`hansard meaning-check`) says it may be, or search by meaning switches off until every
vector is made again. That line says which case applies:

- "…anything else … may be moved or removed": the re-ranker's old files may be deleted, or left;
- "…has not yet taken that id on this version: change nothing…": wait for the next update and check again;
- "…cannot be told from another model's there…": leave the folder exactly as it is. Separating the models then
  means making every vector again (`hansard embed --start-again`), as one planned step.

`docker compose run --rm --no-deps -T app hansard meaning-check --look` reads only the index's note and the folder,
without loading a model, and prints the same line.

### Going back, and coming forward again

Until 1 February 2027 the site can go back to its earlier code and forward again without losing anything:

1. Going back. Stop the site, run `deploy/undo-wts-core.sql` (the command is at the top of the file), check out
   the earlier code (the last commit before the move, from `rj-mitchell/wherempsstand_old`), and start it with
   `docker compose up -d --build`. The undo writes the old
   tables again from wts-core's, including everything taken in since the move. It cannot carry back the log of
   what was done to each statement or hidden briefings (they are shown again), and spending rows of earlier days
   come back with no account or visitor.
2. Coming forward again. Deploy as usual: the new code makes the move afresh from the old tables.

Never start the earlier code on a moved database without the undo: what it writes goes to the old tables only.
If that happens, the new code refuses to start, `update.sh` stops before restarting, and the message counts the
rows each side has taken in since the move:

- if the new code never answered a reader (the usual case: it failed to start), stop the site and run
  `deploy/redo-wts-core.sql`, which forgets the move so the next start makes it again from the old tables;
- if both have answered readers, one side's new rows must go: `redo-wts-core.sql` keeps the earlier code's,
  `undo-wts-core.sql` keeps the new code's.

Neither file can be run twice, or on a database that was never moved: it refuses and changes nothing. From
1 February 2027 the old tables are gone, both files refuse, and going back means restoring a backup made before
the move, while one is kept (60 nights off site). A backup made before the move can also be restored onto the new
code at any time: its rows are brought across at the next start.

## Names kept from earlier

The project began under another name, and a few names from then are kept because changing them would break
something. Use the current names in new work.

| Name | What it is |
|---|---|
| `/opt/hansardgpt` | The site's checkout on the server, which also holds the front door for every site there ([HOSTING.md](HOSTING.md)) |
| `hansard` | The Python package and command |
| `HANSARD_*` | The site's settings in `deploy/.env`, including the Backblaze B2 keys the other sites copy |
| `hansardgpt-local` | The Docker Compose project name in `compose.yaml`, which keeps existing local data |
| `where-they-stand-<date>.tar.gz` | The site's backups |
| `/etc/wheretheystand/`, `/var/lock/wheretheystand-update.lock` | The server's mail settings and update lock, shared by every site |
| `hgpt.settings`, `wts.kind`, `wts.compare` | Keys in readers' browsers; renaming them would reset readers' settings |
