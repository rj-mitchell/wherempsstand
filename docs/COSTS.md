# Running costs

What Where MPs Stand costs to run and how spending is kept within limits. Figures were last checked on
6 November 2026. AI prices are Anthropic's, per call, measured on this site, at £1 = $1.27.

## Free to use

These run on the server, which is paid for whatever the use, so extra use costs nothing:

- searching and showing sources, including "Sources only" and the sources under every briefing (keyword and
  meaning-based search both run on the server; only reading the question costs anything, as below);
- repeat questions: a question already answered with the same settings is served from the saved copy for up to a
  week, or until new records arrive;
- shared briefing links, and printing or copying a briefing;
- MP and peer pages, finding an MP by postcode, election results and majorities, voting records and rebellions;
- re-ranking, if it is turned on: it uses a small model on the server;
- the twice-daily data update, the search index, the backups, and the status, admin and information pages;
- the pages that use no AI: In Parliament this week, charts, bill and debate pages, timelines, the glossary, RSS
  feeds, early day motions, the registers of interests, committee pages and witness lists.

## Small costs (well under 1p each)

- Reading the question (choosing the filters shown as chips) costs about 0.01p with Haiku. Haiku also reads the
  question for "Sources only".
- Running the test set: reading 62 questions costs about 1p with Haiku. Grading the relevance of sources costs a
  few pence a run, and only for passages not graded before.

## Larger costs

- A new briefing costs about 4p (5 cents) with Sonnet, and is most of the running cost. Opus would cost about 9p.
  Briefings in "comprehensive" mode use more sources and more thinking, so cost roughly double.
- A comparison (two MPs, parties or nations side by side) costs one or two briefings.
- A topic briefing costs about 5p each time it is rewritten. The 16 topics cost about £2 to write the first time,
  then roughly £2 to £6 a month. A topic is rewritten at most weekly, and only when new records have arrived and
  the sources found have changed. At most 4 are rewritten per update, and none once a daily or monthly cap is
  reached.
- Featured briefings are free, since they are briefings already written.
- A full run of the test set with every briefing written (`hansard eval run`) costs about $6 (the first baseline,
  19 November 2026: $5.76, [EVALUATION.md](EVALUATION.md#the-first-baseline-on-the-real-archive-19-november-2026)).
  It stops cleanly at `[eval] max_cost_usd` ($7), and says what it spent.

## Fixed costs

- The server (Hostkey), monthly, shared with the charity's other sites on it (Where Councils Stand, Where Landlords
  Stand and Where Countries Stand).
- The domain names, yearly.
- Email for admin@wherempsstand.com, if it is a paid service.

## Spending controls

The limits are in `[usage]` in `config/settings.toml`. To change one, edit it there and deploy the change
([OPERATIONS.md](OPERATIONS.md), "New code").

- The site stops writing new briefings for the day at $50 (raised from $10 on 19 November 2026), and for the month at $250 (raised from $150 the same day). Sources are still shown.
- One visitor can have at most 10 new briefings a day, as a backstop against automated use, and make at most 20
  requests a minute. (It was 40 until November 2026, when three or four visitors could have used up the daily cap
  between them.) A question that plainly is not about Parliament's record (a request for a poem, instructions to the
  site) is read by the small model and then answered "No records found." without a briefing being written. Visitors are told apart by a salted hash of their IP address; the address is never stored.
- After every 10 new briefings in a day, a visitor sees a short note on what the site costs, with a link to the
  charity's Donate page (wheretheystand.uk/donate). Closing it carries on as before, and a visitor who goes
  through to the Donate page is not reminded again that day.
- Identical questions with the same settings are answered from a cache for up to a week, cleared when new records
  arrive, so a popular question is paid for once.
- The admin page shows spending, with warnings at 50% and 80% of the daily and monthly caps. Each briefing's
  tokens and cost are recorded in the state database.
- A spending limit set in the Anthropic Console (Settings, Limits) is a second safeguard.
