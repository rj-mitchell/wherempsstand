# The shared server

How to set up the server that runs Where MPs Stand and three of the charity's other sites, and how the parts that
serve every site on it work: the front door, email alerts, Cloudflare, the watchdog and disk space. Running Where
MPs Stand itself, day to day, is in [OPERATIONS.md](OPERATIONS.md).

## Contents

1. [The server and its sites](#the-server-and-its-sites)
2. [Setting up the server](#setting-up-the-server)
3. [Email from the server](#email-from-the-server)
4. [Cloudflare protection (optional)](#cloudflare-protection-optional)
5. [Other sites on this server](#other-sites-on-this-server)
6. [The watchdog](#the-watchdog)
7. [Disk space](#disk-space)

## The server and its sites

The server is a Hostkey virtual server in London running Ubuntu 24.04. Four sites run on it, each from a git
checkout under `/opt` with its own containers, data, settings and timers:

| Site | Folder on the server | Update unit (and timer) | When it runs |
|---|---|---|---|
| Where MPs Stand | `/opt/hansardgpt` | `hansard-sync.service` | 7.15am and 7.15pm (UK time) |
| Where Councils Stand | `/opt/wherecouncilsstand` | `councils-update.service` | 3.17am (UTC) |
| Where Countries Stand | `/opt/wherecountriesstand` | `wcs-update.service` | 5.41am and 5.41pm (UTC) |
| Where Landlords Stand | `/opt/wherelandlordsstand` | `landlords-update.service` | Thursdays at 11.40am (UK time) |

Each site also has its own nightly backup (`hansard-backup`, `councils-backup`, `wcs-backup`, `landlords-backup`).
Where MPs Stand's own timers are described in [OPERATIONS.md](OPERATIONS.md).

Where MPs Stand's `deploy/` folder, in `/opt/hansardgpt`, is also the front door for every site on the server:

- Caddy, the web server that answers every domain and passes each site's visitors to its container
  ([Other sites on this server](#other-sites-on-this-server));
- Cloudflare set-up for each domain (`deploy/cloudflare.sh`) and the health check (`deploy/check.sh`);
- email from the server (`deploy/mail.sh`) and alerts when any site's job fails (`alert@.service`);
- the watchdog, which restarts any site that stops answering (`wheretheystand-watchdog.timer`);
- the hourly disk check and monthly disk report (`wheretheystand-disk.timer`, `wheretheystand-disk-report.timer`);
- the look every 15 minutes for new readers' reports and statements on any site, emailed as counts only
  (`wheretheystand-waiting.timer`, `deploy/waiting-alert.sh`). It asks each site's `/api/check` with the family's
  check token from that site's `.env`, so a site without the token is not looked at;
- the server-wide update lock, `/var/lock/wheretheystand-update.lock`, which each site's update takes so that two
  never run at once.

The charity's `family/OPERATIONS.md` describes the server as a whole. The other sites' `docs/DEPLOY.md` link to the
sections "Other sites on this server", "Email from the server" and "Cloudflare protection" here, so keep those names
or update the links.

Never put the server's address in this or any other public repository, letters included: the sites sit behind
Cloudflare, and the server turns away connections that do not come through it. Where Lives Stand, the charity's own
site (wheretheystand.uk) and the game Understudy: Lumaya run on Cloudflare Workers, not on this server.

## Setting up the server

These steps take a newly provisioned server to a live site at wherempsstand.com. Allow about an hour, most of it
waiting for the first data load. They need the server's IP address and root password (in Hostkey's email and
control panel), the domain wherempsstand.com and an Anthropic API key.

### Server size

For Where MPs Stand alone: 4 to 8 vCPU, 16 GB of memory and 60 GB of disk. Its data is the largest part of the
shared server's.

| Data | Size |
|---|---|
| Raw Hansard XML since 1997 | about 9 to 10 GB as plain XML, about a quarter of that gzipped as stored |
| Parquet store | 1.2 GB |
| Keyword index | 7.9 GB (4.6 GB of passages, 3.3 GB of full-text and filter indexes) |
| Meaning-search vectors | about 7 GB |

Each sync deletes index versions more than an hour old; a large update, such as a big reshuffle, can add several
GB until the next sync. A smaller server (2 to 4 vCPU and 8 GB) works with recent years only (`hansard bootstrap
--from 2019-01-01`; later updates keep to that date) or keyword search only (`hansard bootstrap --keyword-only`,
which skips the vectors, so questions worded differently from the speeches are matched less well).

### 1. Point the domain at the server

In the domain's DNS settings, add an A record for `@` and one for `www`, each with the server's IP address. Remove
any "parking" A records first, and leave MX and TXT records alone: they deliver email to admin@wherempsstand.com.

If the domain is on Cloudflare, set both records to DNS only (the grey cloud) for now: while proxied, the site
cannot get its first HTTPS certificate. The main address can be put behind Cloudflare once the site works
([Cloudflare protection](#cloudflare-protection-optional)).

Changes usually take effect within an hour. `nslookup wherempsstand.com` should print the server's IP address
before step 3.

### 2. Log in and change the root password

```bash
ssh root@YOUR.SERVER.IP
```

Answer `yes` to trust the server the first time, then paste the root password from Hostkey (nothing appears while
typing it). Check the system with `grep PRETTY /etc/os-release`: it must say Ubuntu 24.04, or reinstall the server
from Hostkey's control panel. Then set a new root password with `passwd`, since Hostkey's was sent by email, and keep
it in a password manager.

### 3. Run the setup script

```bash
curl -fsSL https://raw.githubusercontent.com/rj-mitchell/wherempsstand/main/deploy/provision.sh -o provision.sh
bash provision.sh
```

It asks for the main domain (`wherempsstand.com`), other domains to redirect (leave blank: `www.wherempsstand.com`
is added automatically) and the Anthropic API key (hidden while pasted). It then:

- installs Docker, automatic security updates, a firewall that allows only SSH and the web, and fail2ban, which
  blocks addresses that keep guessing the password;
- clones the repository to `/opt/hansardgpt`, writes `deploy/.env` with a new admin token, and starts the site behind
  Caddy, which obtains HTTPS certificates automatically;
- starts the first data load in the background;
- installs the site's timers (the twice-daily update, nightly backup, backup check and weekly report) and the
  server-wide watchdog and disk timers.

At the end it prints a command for checking usage and spending, which contains the admin token. Keep it safe.

### 4. Switch to an SSH key

A key cannot be guessed, so password logins can then be turned off. On the maintainer's own computer:

1. Make a key, once: `ssh-keygen -t ed25519`, pressing Return at each question (a passphrase is optional).
2. Copy it to the server. On a Mac, `ssh-copy-id root@YOUR.SERVER.IP`. On Windows, in PowerShell:

   ```powershell
   type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh root@YOUR.SERVER.IP "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
   ```

3. Check that `ssh root@YOUR.SERVER.IP` now logs in without the password. Do not go on unless it does.
4. Stay logged in, log in again in a second window as a safety net, and in one of them run:

   ```bash
   printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\n' > /etc/ssh/sshd_config.d/00-keys-only.conf
   systemctl reload ssh
   ```

5. In a third window, check that `ssh root@YOUR.SERVER.IP` still logs in. If it does not, undo it from a window
   still logged in: `rm /etc/ssh/sshd_config.d/00-keys-only.conf && systemctl reload ssh`.

If the key is ever lost, Hostkey's control panel has a web console and can reset the root password.

### 5. Check everything is working

```bash
bash /opt/hansardgpt/deploy/check.sh
```

It changes nothing, and lists what works (✓) and what needs attention (✗), with what to do about each.

### 6. Watch the first data load

```bash
tail -f /var/log/hansard-bootstrap.log
```

Ctrl+C stops watching; the load carries on. When it finishes, the site at https://wherempsstand.com searches the
whole record by keyword. Meaning-based search is then built during each twice-daily update, newest records first;
on 8 cores the whole archive takes roughly a day. The [status page](https://wherempsstand.com/status) shows its
progress.

Then set up email alerts ([Email from the server](#email-from-the-server)) and off-site backups
([OPERATIONS.md](OPERATIONS.md), "Backups").

## Email from the server

The server emails wheretheystanduk@gmail.com, for every site on it, when:

- a scheduled job fails: any site's update or nightly backup (Where Countries Stand's update also fails on purpose
  when briefing spending reaches 80% of a cap, or briefings keep failing);
- the watchdog restarts a site, cannot restart it, is waiting for an update to finish, or has given up
  ([The watchdog](#the-watchdog));
- the disk is 80% full or more ([Disk space](#disk-space)).

On the 1st of each month it also sends a report on where the disk is going. Each email gives the job, what happened
and its last lines in the system log, and the same job is reported at most once every six hours.

It sends through Gmail with an app password, which can be revoked without changing the account's password. To set
it up, once:

1. Sign in to wheretheystanduk@gmail.com, open myaccount.google.com, Security, and turn on 2-Step Verification if
   it is off (Google offers app passwords only with it).
2. Open myaccount.google.com/apppasswords, name a new password "server" and press Create. Google shows it once.
3. On the server, run the following. `setup` asks for the password without showing it and saves it where only root
   can read it (`/etc/wheretheystand/mail.env`); `test` sends a message, which should arrive within a minute.

   ```bash
   bash /opt/hansardgpt/deploy/mail.sh setup
   bash /opt/hansardgpt/deploy/mail.sh test
   ```

4. Run each site's `deploy/update.sh` once, so its jobs know where to report.

To stop the server sending, delete the app password at myaccount.google.com/apppasswords.

The same script sends prepared letters, such as the requests in `outreach/` of this and other repositories. They
go from the account like any other email, so they appear in its Sent folder and replies come to its inbox. A letter
already sent is skipped, so the command is safe to run twice. `--test-to` sends a test copy instead, which is not
counted as sent:

```bash
bash /opt/hansardgpt/deploy/mail.sh send --test-to <address> /opt/hansardgpt/outreach/2026-11/mysociety.txt
bash /opt/hansardgpt/deploy/mail.sh send /opt/hansardgpt/outreach/2026-11/mysociety.txt
```

`/var/lib/wheretheystand/sent.log` lists what was sent and when.

## Cloudflare protection (optional)

Cloudflare's proxy (the orange cloud) puts Cloudflare between visitors and the server, so floods of automated
requests are absorbed before they reach it. The server then uses a certificate from Cloudflare, closes any
connection that does not come through Cloudflare, and still sees each visitor's own address, so the per-visitor
limits keep working. www.wherempsstand.com is redirected by Cloudflare itself, so no DNS record shows the server's
address. Old DNS records can still reveal it, so this protects against heavy load and casual abuse more than a
determined attack.

The charity's own domains (wheretheystand.uk, wheretheystand.co.uk and wheretheystand.org.uk) are served by
Cloudflare and never reach this server, so `REDIRECT_DOMAINS` in `deploy/.env` holds only `www.wherempsstand.com`.

1. Deploy the latest version (`bash /opt/hansardgpt/deploy/update.sh`), which fetches Cloudflare's list of
   addresses.
2. At dash.cloudflare.com, choose Add a domain, enter wherempsstand.com and pick the Free plan. Check the records it
   found: the A records for `@` and `www` (DNS only for now) and every MX and TXT record. At the domain's registrar,
   replace its nameservers with the two Cloudflare gives, and wait for Cloudflare to say the domain is Active.
3. In Cloudflare, for wherempsstand.com:
   - SSL/TLS, Overview: Full (strict). "Flexible" sends visitors round in circles.
   - SSL/TLS, Edge Certificates: Always Use HTTPS on.
   - Security, Settings, Client-side abuse: Email Address Obfuscation off (it rewrites pages).
   - Security, Settings, Bot traffic: Bot fight mode off (it can block the public API and sets a cookie), Block AI
     bots set to "Do not block", and Managed robots.txt off, since the site publishes readable text for AI
     assistants and has its own robots.txt. In AI Crawl Control, set any crawler marked Block to Allow.
   - Speed, Settings, Content Optimization: Rocket Loader off.
   - Speed, Settings: HTTP/3 (with QUIC), 0-RTT Connection Resumption and HTTP/2 to Origin on. Caching, Tiered
     Cache: Smart Tiered Caching on. Leave Caching, Configuration at Standard and "Respect existing headers", with
     no "Cache everything" rule: pages carry live data, and the site already marks which files can be kept.
   - Web Analytics (in the account's menu, under Analytics & Logs): once the domain is proxied Cloudflare turns it
     on, so find wherempsstand.com, choose Manage site, and under automatic setup choose Disable. It would add a
     tracking script, and the site keeps its own cookie-free counts.
4. Create the origin certificate: SSL/TLS, Origin Server, Create Certificate. Keep "Generate private key and CSR
   with Cloudflare" and RSA (2048), remove `*.wherempsstand.com` so that only `wherempsstand.com` is listed, choose
   15 years, press Create, and leave the page open with the format on PEM: the private key is shown only once.
5. In DNS, Records, set the A record for `wherempsstand.com` (`@`) to Proxied, and any AAAA record for `@` too (or
   delete it), or visitors using IPv6 are turned away in the next step.
6. A few minutes later, on the server, run `bash /opt/hansardgpt/deploy/cloudflare.sh on` and paste the Origin
   Certificate and then the Private Key, each followed by Return. It checks they belong together and are for
   wherempsstand.com, switches the server over and checks the site through Cloudflare. If that fails, it puts
   everything back and says what to check.
7. In Rules, Redirect Rules, create a rule named `To wherempsstand.com`: custom filter expression, Hostname equals
   `www.wherempsstand.com`; then type Dynamic, expression `concat("https://wherempsstand.com", http.request.uri.path)`,
   status 301, Preserve query string ticked; Deploy. Then edit the `www` DNS record: Proxied, with the IPv4 address
   `192.0.2.1` (an address that goes nowhere, since Cloudflare answers this name itself), and delete any AAAA record
   for `www`.
8. A few minutes later, on the server, run `bash /opt/hansardgpt/deploy/cloudflare.sh redirects on`. It checks that
   Cloudflare redirects each name, then stops the server doing it. If a name is not ready yet it changes nothing and
   says which; run it again later.
9. `bash /opt/hansardgpt/deploy/check.sh` should say "goes through Cloudflare's proxy" and that each name
   redirects. Then ask a question on the site and check the briefing appears as it is written.

To undo the redirects, point the records back at the server's IP address (DNS only), delete the redirect rules and
run `cloudflare.sh redirects off`. To undo the proxy, run `cloudflare.sh off` on the server first, then set the A
record back to DNS only. The certificate is kept, so `cloudflare.sh on` switches back without pasting it again
(`--replace` pastes a new one). Under attack, Cloudflare's Security, Settings, Under Attack mode asks every visitor's
browser to pass a short check first.

## Other sites on this server

The other sites run from their own folders under `/opt`, with their own containers, data, settings, spending caps
and update timers. Caddy, in this repository, answers every domain with the same protections (Cloudflare's
certificate, connections not through Cloudflare closed, a 1 MB request limit, the security headers) and passes each
site's visitors to its container over a shared Docker network called `sites`.

`SITES` in `deploy/.env` lists them as `domain=container` pairs (see `deploy/.env.example`). A site is served once
its Cloudflare origin certificate is added; until then Caddy leaves its domain alone.

Caddy sets each other site's security headers itself, in place of the site's own, so a site that turns on
measurement for Google Ad Grants (the charity's `family/MEASUREMENT.md`) needs its domain added to `MEASURED_SITES`
in `/opt/hansardgpt/deploy/.env` (space-separated), then `bash /opt/hansardgpt/deploy/update.sh`, which writes the
per-site blocks again (`deploy/caddy-local.sh`) and reloads Caddy. A domain in `MEASURED_SITES` is served with the
snippet `other_site_measured`, whose content security policy adds the Google tag's hosts from the family kit's
`measure-csp.json`; the others are served exactly as before. Where MPs Stand itself needs no entry: its own switch
sets its policy ([OPERATIONS.md](OPERATIONS.md#measurement)). Take a site out when its measurement is turned off.

To add a site (its own `docs/DEPLOY.md` has its details, such as the first data load):

1. In Cloudflare, for the site's domain: the settings in step 3 of [Cloudflare
   protection](#cloudflare-protection-optional), a Proxied A record for `@` pointing at this server, `www`
   redirected by a rule (step 7), and an origin certificate (step 4), with the page left open.
2. On the server:

   ```bash
   git clone https://github.com/rj-mitchell/<site> /opt/<site>
   cd /opt/<site>/deploy && cp .env.example .env && nano .env
   bash /opt/<site>/deploy/update.sh
   ```

   `update.sh` builds and starts the site and installs its timers.
3. Add the site to `SITES` in `/opt/hansardgpt/deploy/.env`, then paste its certificate:
   `bash /opt/hansardgpt/deploy/cloudflare.sh site <domain>`.
4. `bash /opt/hansardgpt/deploy/check.sh` lists each site and the memory left.

### Keeping out of each other's way

Each site's update runs at its own time (the table above). A long update can still run into the next one, so each
site's update unit wraps its command in `flock /var/lock/wheretheystand-update.lock` and waits while another update
holds the lock; a new site joins by doing the same. Backups do not take the lock, since they are small and quick.

While an update waits, `systemctl status hansard-sync --no-pager` shows `flock` as the running command, and
`journalctl -u hansard-sync` then says how long it waited. `fuser -v /var/lock/wheretheystand-update.lock` shows
which update holds the lock. If `check.sh` reports less than 3 GB of memory free for long, the server needs more
memory or a second server is needed.

## The watchdog

Each site's app checks itself every 30 seconds (Docker runs its `/api/health`), and Docker marks it unhealthy after
three failures in a row, but does not restart it. `deploy/watchdog.sh` does, every two minutes
(`wheretheystand-watchdog.timer`), for Where MPs Stand and each site in `SITES`, in its folder `/opt/<container>`. A
site listed but not installed yet is skipped. `update.sh` and `provision.sh` install it.

When a site is unhealthy, the watchdog restarts its app (`docker compose restart app` in its `deploy` folder) and
emails the alerts address. It is careful not to make things worse:

- an app that is not running or is still starting is left alone, and so is an update's one-off container;
- while an update holds the update lock it restarts nothing, and emails that it is waiting; once the update has
  finished, the next check restarts the site if it is still unhealthy. It holds the lock itself while it restarts a
  site, so no update starts in the middle;
- after restarting a site it leaves it alone for 10 minutes, then 20 after the next restart, and after three
  restarts in six hours it stops and emails that someone needs to look. It tries again once the oldest of those
  restarts is six hours old.

Each kind of email about each site is sent at most once every six hours (`alert.sh`). The restarts are listed in
`/var/lib/wheretheystand/watchdog/`, one file per site; deleting a site's file lets the watchdog restart it again at
once.

| Task | Command |
|---|---|
| See what the watchdog has done | `journalctl -t wheretheystand-watchdog --since -7d` |
| See each site's health now | `docker ps --format '{{.Names}}: {{.Status}}'` |
| Run it now | `systemctl start wheretheystand-watchdog` |
| Pause it, during maintenance | `systemctl stop wheretheystand-watchdog.timer`; `systemctl start wheretheystand-watchdog.timer`, or any `update.sh`, resumes it |

## Disk space

Two jobs watch the disk for every site, and email the alerts address ([Email from the
server](#email-from-the-server)):

- every hour, `deploy/disk-check.sh` emails if the disk is 80% full or more, with what is using it, and again at
  most every six hours while it stays that full;
- on the 1st of each month, `deploy/disk-report.sh` emails where the disk is going: each site's data and its largest
  folders, the search indexes and their old versions, Docker's images and build cache, swap, the system log, last
  month's figure, each container's memory use against its limit, and the meaning-search index's growth since its
  last rebuild (`hansard index-status`). At 20% growth it says to rebuild the index ([OPERATIONS.md](OPERATIONS.md),
  "Day to day", has the command).

`check.sh` also marks the disk ✗ from 80%, and `bash /opt/hansardgpt/deploy/disk-report.sh` prints and emails the
report at any time. `update.sh` caps the system log at 500 MB and has apt delete out-of-date downloads weekly
(`deploy/housekeeping.sh`).

If the disk is filling, these are safe at any time and free space at once:

```bash
docker builder prune -f            # Docker's build cache: rebuilt as needed
docker image prune -f              # old copies of the sites' programs that nothing uses
journalctl --vacuum-size=500M      # the system log
apt-get clean                      # downloaded updates that are already installed
```

Never run `docker volume prune`: it deletes the data of every site not running at that moment. Beyond these, the
next steps are each site's own, such as its search indexes; measure first.

### Memory limits

The other sites' containers have memory limits (4 GB for Where Countries Stand and Where Councils Stand, 1 GB for
Where Landlords Stand), so a runaway update stops itself. Where MPs Stand has none until its peak has been
measured: `mem_limit` in `deploy/docker-compose.yml` is commented out. To set it, watch `docker stats --no-stream`
during a sync and read the monthly reports, set the limit to about one and a half times the highest figure, remove
the `#` and run `update.sh`. A sync's one-off container gets the same limit again, so leave room for both.

### Docker's log limit

Each site's containers keep at most 30 MB of logs, set in its compose file. The default for any other container is
in `/etc/docker/daemon.json`, which `provision.sh` writes. If `cat /etc/docker/daemon.json` says "No such file or
directory", add it at a quiet time, away from the update times, since restarting Docker stops every site for up to
a minute:

```bash
printf '{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}\n' > /etc/docker/daemon.json
systemctl restart docker
bash /opt/hansardgpt/deploy/check.sh
```

The sites start again by themselves. If the file exists without these settings, add them by hand.

### Compressing raw Hansard files

The sync stores each of TheyWorkForYou's files gzipped (`<name>.xml.gz`, about a quarter of the size;
[DECISIONS.md](DECISIONS.md), 25). Files stored plain by an older mirror can be compressed in place with
`hansard compress-raw`, which keeps their dates, so nothing is read again and the search index is untouched. At a
quiet hour, from `/opt/hansardgpt/deploy`, see what it would do (this changes nothing), then run it under the update
lock:

```bash
docker compose run --rm -T app hansard compress-raw --dry-run
flock --verbose /var/lock/wheretheystand-update.lock docker compose run --rm -T app hansard compress-raw
```

It needs only a few MB free at a time, prints the GB before and after, and is safe to stop and run again.
`--limit 5000` compresses only that many files, to try it first.
