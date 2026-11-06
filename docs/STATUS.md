# Open items

What is still to do for Where MPs Stand, as of 6 November 2026 (updated the same day). Tick items off or delete
them when done.

Most of these were in two files of the pre-public repository, `rj-mitchell/hansardgpt` (`docs/ROADMAP.md` of
11 October and `docs/FOLLOW_UP.md` of 12–19 October), which is being archived. Everything there that was done or
overtaken has been left behind. Items that concern the whole family (uptime monitoring, the server's routine, name
and trade mark checks, legal review) are in the charity's plan (rj-mitchell/wheretheystand-charity, `plans/family/STATUS.md`).

## Licences

- [ ] **Companies House: confirm the terms for company roles.** The site looks up MPs' and peers' company roles
  through the Companies House public data API. It shows only company names, roles and dates, with no addresses,
  nationality or dates of birth. It credits Companies House, links each entry to its record and stays well under
  the rate limit (600 requests in five minutes). Read the terms accepted with the key. If they say anything about
  attribution wording, limits on showing officers' details, or combining the data with other sources, change the
  `/directorships` pages to match. Otherwise delete this item.
- [ ] **mySociety: licence for the TheyWorkForYou Votes descriptions.**
  - **What it covers.** The "Voting aye: …" sentences under about 1,600 votes, which briefings also use. They come
    from `theyworkforyou.com/pwdata/votes/divisions.parquet`.
  - **Where it stands.** TheyWorkForYou Votes states no licence. The paid API's terms put mySociety's own data
    under CC BY-SA 2.5, but they don't mention the vote descriptions. The site labels each sentence "(TheyWorkForYou
    Votes)" and credits mySociety on the methodology page and in ATTRIBUTION.md.
  - **To do.** Email hello@mysociety.org, through `deploy/mail.sh` so the letter is kept:

    > I run Where MPs Stand (wherempsstand.com), a free, non-commercial site about the UK Parliament's records,
    > and a project of Where They Stand, which is applying to register as a charity in England and Wales. Next to
    > votes it shows your TheyWorkForYou Votes "voting aye/no means" descriptions, from the divisions.parquet
    > download, credited to TheyWorkForYou Votes with a link. Is that OK, under what licence, and how would you
    > like to be credited?

  - **If the answer is share-alike:** nothing changes; say so in ATTRIBUTION.md.
  - **If the answer is no:** switch the descriptions off. The site's own vote labels ("amendment", "second reading"
    and so on) are unaffected.
- **Electoral Commission:** settled on 12 October 2026. The weekly download uses the register's own CSV export,
  credited as the Commission asks. To have it confirmed, email the Commission: "We download the register of donations
  once a week with the search site's CSV export and show donations to MPs, credited and linked to each record. Is
  that within your terms?"

## Data

- [ ] **Four committees that Parliament's service failed on** in the first full rebuild (about 170 inquiry pages
  show no committee). The weekly update retries them. Check once, and delete this item if nothing is printed:
  `journalctl -u hansard-sync --since "7 days ago" | grep "could not be listed"`
- [ ] **About 110 members with no date of birth** (since 2010, mostly peers without a Wikidata entry), so they are
  left out of age groupings. Optional: a small corrections file from published sources such as Parliament's own
  biographies, each entry checked by a person.

## Features

- [ ] **Email alerts when briefings fail, and at 80% of a spending cap.** The admin page shows both; nothing emails
  them. Copy Where Countries Stand's approach: `wcs usage` exits 1 and `alert@` sends the email
  (`wherecountriesstand/src/wcs/cli.py`).
- [ ] **Topic alerts for readers** (a weekly digest on chosen subjects). Decided on 6 November 2026: built once for
  the whole family, not for this site alone (the charity's plan, section 12). This site goes first, since its RSS
  feeds nearly provide it.
- [ ] **Plain-English summaries of committee evidence sessions**, written once each (about 4–15p a session).
- [ ] **Tracking committee recommendations**: whether the government accepted each, and what happened. Two or
  three weeks' work; best with a research partner.
- [ ] **An AI summary for "In Parliament this week"** (optional). The page itself exists.
- [ ] **Briefings asked about a member, shown on their page.** Needs a decision first: it would show what people
  ask about a named person. Perhaps featured briefings only.
- **Parked**, and **decided against**: see [DECISIONS.md](DECISIONS.md), 21 to 23.

## Quality

- [ ] **Accessibility testing with real users** (a screen reader user and a keyboard user). The charity's plan
  proposes one round for every site.
- [x] **`docs/EVALUATION.md` said "Opus stays for briefings"**, though the site has used Sonnet since October.
  Corrected on 6 November 2026.
- [x] **The home page was blank without JavaScript.** It now has a short text version with links to the pages that
  work without scripts (6 November 2026).
- **Re-ranking** is built but off (`config/settings.toml`). Revisit only if the quality of what is found becomes the
  complaint.

## Server

The server's disk is about 70% full. Done on 6 November 2026, and installed by the next `update.sh`:

- [x] an hourly email when the disk is 80% full, and a monthly report on where it is going (`docs/HOSTING.md`,
  "Disk space");
- [x] the system log capped at 500 MB, and apt clearing out old downloads weekly;
- [x] one server-wide lock, so the sync never runs at the same time as another site's update;
- [x] the raw Hansard files stored gzipped (decision 25): the sync now fetches over HTTPS and stores each new or
  corrected file compressed, in about a quarter of the space. Nothing already read is read again.

Still to do:

- [ ] **Compress the raw Hansard files already on the server**, once, at a quiet hour: `hansard compress-raw
  --dry-run`, then `hansard compress-raw` under the update lock (`docs/HOSTING.md`, "Disk space", has the commands).
  About 7 GB freed. Note the sync's `fetch` and `ingest` times before and after.
- [ ] **Docker's default log limit on the running server.** `provision.sh` sets it on a new server; this one needs
  it added by hand and Docker restarted, at a quiet time (`docs/HOSTING.md`, "Disk space").

## Documents and identity

Done on 6 November 2026, from the charity's family plan (`plans/family/PLAN.md`):

- [x] **The family binding** on every page: "A Where They Stand project" under the name, the family shelf in the
  footer, the favicon, theme colour and link card in Commons green, and the family's headings
  ([DESIGN.md](DESIGN.md), "The family binding");
- [x] **the pages that named a person as the operator** (About, Privacy, Terms, Press) now say the site is a Where
  They Stand project, run by its founder until the charity is registered; About has an "In the family" section;
- [x] **donations** go to the charity's Donate page, not straight to Ko-fi;
- [x] **under a briefing**, the same question on Where Councils Stand or Where Countries Stand, where their record
  deals with the subject;
- [x] **the README split** into [METHOD.md](METHOD.md), [OPERATIONS.md](OPERATIONS.md), [DESIGN.md](DESIGN.md) and
  [EVALUATION.md](EVALUATION.md), with a new [DECISIONS.md](DECISIONS.md), and the README rewritten to the family's
  template; [HOSTING.md](HOSTING.md) says at the top that it is the shared server's manual for now.

Still to do:

- [ ] **Check the server's donations link.** If `/opt/hansardgpt/deploy/.env` sets `HANSARD_DONATE_URL` to the Ko-fi
  page, delete that line (the setting now points at wheretheystand.uk/donate), then `docker compose up -d` in the
  same folder.
- [ ] **When the family kit changes** (a status, a new project), sync it again: [DESIGN.md](DESIGN.md), "The family
  binding". The kit isn't checked against the charity's copy in CI, since that repository is private; a test checks
  only that the copy here hasn't been edited.
- [ ] **An MP's page to the councils in the seat, and back** (the family plan, section 4): needs a lookup from
  constituencies to local authorities.
- [ ] **Move the shared server's parts** (the front door, email, alerts, the watchdog, disk checks, the update lock)
  to the family repository's `server/` and OPERATIONS document once it exists (family decision 6), and leave
  [HOSTING.md](HOSTING.md) with this site's steps only.
