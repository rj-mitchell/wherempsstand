# Decisions

Product decisions for Where MPs Stand, made by the owner. They override anything else in this repository. Add new
decisions here once they are made, with the date.

This list was put together on 6 November 2026 from what the site's documents of October 2026 already recorded (the
README, `docs/EVALUATION.md`, `docs/COSTS.md`, `docs/HOSTING.md`, `config/settings.toml`, the methodology page and
the open items moved from the pre-public repository, `rj-mitchell/hansardgpt`), and from the charity's family plan
(`plans/family/PLAN.md` in `rj-mitchell/wheretheystand-charity`, section 8), whose decisions apply to every site.

## What the site is

| # | Topic | Decision |
|---|---|---|
| 1 | Name and address | **Where MPs Stand**, at wherempsstand.com. It was called Where They Stand, at wheretheystand.uk, until 31 October 2026, when that name and address passed to the charity. The repository was renamed from `wheretheystand` to `wherempsstand` on 6 November 2026 (family decision 7). |
| 2 | Not a search engine | The site answers questions; it does not try to find every mention. Hansard's own search does that well, and the site sends readers to it, and to the other sites listed on [/resources](https://wherempsstand.com/resources), for what they do better. |
| 3 | Open to everyone | No sign-in. An invite-only mode exists (`[auth] required`) and is off. |
| 4 | The record | Both Houses from the 1997 general election (the Lords from November 1999), every division including the Commons votes of 1997–2001 from Public Whip, and the devolved parliaments from their first sittings. The archive first started in 2010 and was extended in October 2026. |
| 5 | Who said it | Speakers are identified only by parlparse's IDs, never by name, and described as they were on the day: party, seat, role, majority and age. |
| 6 | Seat type | City, town or rural by the size of the urban area most residents live in (ONS, NRS and NISRA figures), not by city status. |
| 7 | Independence | Independent of Parliament, every party and every campaign. The site does not use Parliament's logo or the crowned portcullis. |
| 8 | Part of a family | A Where They Stand project, carrying the family's binding on every page: the endorsement, the footer shelf, the favicon, theme colour and link card in Commons green, and Source Serif 4 for headings (family decisions 1 and 4). The open-book mark stays as the site's own mark on the page. See [DESIGN.md](DESIGN.md). |

## Briefings

| # | Topic | Decision |
|---|---|---|
| 9 | Balance | The evidence for a briefing is chosen for balance across speaker groups (ministers, the opposition frontbench, committee chairs, backbenchers in city, town and rural seats, peers), not keyword score alone. The same plan always gives the same sources. See [METHOD.md](METHOD.md). |
| 10 | Sources only | The model is given only the chosen sources and may use no outside knowledge. If nothing relevant is found it is not called at all, and the site says "No records found." Every citation and quotation is checked word for word, and statements not tied to a source are counted and can be shown. |
| 11 | Neutral wording | Plain reporting verbs (said, argued, criticised, supported, opposed). Loaded or judgemental words (lied, misled, U-turn, admitted, attacked and the like) appear only inside an exact, cited quotation. "Claimed" only as a verb of saying. The site never says a member contradicted themselves; it gives what they said on each date. |
| 12 | British English | Briefings are written in British English, and a final pass converts any American spelling in the model's own words. Quotations are never changed. |
| 13 | Models | The smallest, cheapest model reads the question (the filters shown as chips), from 11 October 2026: 60 of 62 right in the test set, at a thirtieth of the cost of the largest. Briefings were written by the largest model until later in October 2026, and by the middle one since, for half the cost with the same accuracy. Each is one line in `config/settings.toml` (`query_parse_model`, and `model` in `[answer]`). See [EVALUATION.md](EVALUATION.md). |
| 14 | Re-ranking | Built but off: a modest gain for several seconds' wait on every search. To be tried again once meaning-based search covers the whole record. |
| 15 | Registered interests | A speaker's registered interest is flagged beside what they said when an AI model judges it may relate, with the note "Matched by AI; a link to check, not a finding of wrongdoing." |
| 16 | Shared briefings | Every briefing is saved at a permanent address (`/b/<id>`), with the date it was prepared and the latest record it used. Briefings are kept out of search engines unless featured. A withdrawn briefing returns 410 Gone with the reason. Embeds are an image and a link, not a frame, so pages can't be framed by other sites. |

## Money

| # | Topic | Decision |
|---|---|---|
| 17 | Spending caps | $10 a day and $150 a month for the whole site; 40 new briefings a visitor a day; 20 requests a minute. When a cap is reached, briefings pause and sources keep working. A repeated question is answered from the saved copy for up to a week. See [COSTS.md](COSTS.md). |
| 18 | Donations | Accepted from individuals only; none from parties, elected representatives, campaigns, lobbying firms or organisations seeking to influence policy; any single donation over £500 acknowledged on the About page, with the donor's agreement, or returned. A short reminder every ten new briefings a visitor writes in a day, never a paywall. Since 6 November 2026 the link goes to the charity's Donate page, wheretheystand.uk/donate, not straight to Ko-fi (family decision 9). |

## Readers' privacy and the licence

| # | Topic | Decision |
|---|---|---|
| 19 | No tracking | No cookies, no advertising, no third-party scripts, fonts or analytics. Visitors are counted with a code made from their IP address with a secret that changes daily; the address is not stored. Cloudflare is told not to add its own analytics (`Cache-Control: no-transform`). |
| 20 | Licence | Code AGPL-3.0. Briefing text CC BY 4.0. Quotations from Hansard remain under the Open Parliament Licence. |

## Decided against, or not yet

| # | Topic | Decision |
|---|---|---|
| 21 | A briefing for every debate | No ready-made briefing is written and stored for every debate, as for bill stages, to hold costs down. A reader who wants one asks. |
| 22 | Labels that take a side | No labels such as "wrecking amendment". Each kind of vote has a neutral note on what voting either way usually means. |
| 23 | Speeches beside interests and donations | Parked: a timeline of a member's speeches beside their declared interests and donations would come closest to implying wrongdoing. Not to be built unless a legal review clears it. |
| 24 | Topic alerts by email | Not for this site alone. Planned once for the whole family, with a consented mailing service and a change to the privacy notice (family decision 15, 6 November 2026); this site goes first, since its RSS feeds nearly provide it. |
| 25 | Raw Hansard files | Compressed in place, which keeps TheyWorkForYou's corrections to old days arriving, rather than moving 1997–2009 to off-site storage (family decision 14, 6 November 2026). Built on 6 November 2026: the sync fetches over HTTPS and stores each file gzipped; the files already on the server are compressed once with `hansard compress-raw` ([HOSTING.md](HOSTING.md), "Disk space"), which reads nothing again. rsync, which can only keep plain files, is used only if `[sources] compress_raw_xml` is set to false. |
| 26 | Search vectors | Stored as 16-bit numbers rather than 32-bit: half the space (about 6–7 GB on the server) for no measurable change in what is found, approved by the owner on 6 November 2026 (the charity's server review, R9). Embeddings are still computed in 32 bits and rounded when stored. New tables are 16-bit; the tables already on the server are converted once with `hansard vectors-f16`, which checks the new copy against the old before swapping it in ([HOSTING.md](HOSTING.md), "Disk space"; the measurements are in [EVALUATION.md](EVALUATION.md), "Retrieval only"). The passages' vectors first, the committee evidence's in the same run; the sister sites can follow. |
