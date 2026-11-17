# Running costs

Last updated 6 November 2026. AI prices are Anthropic's, per call, measured on this site, at £1 = $1.27.

## Free to use

These run on the server, which is paid for whatever the use, so extra use costs nothing:

- searching and showing sources, including "Sources only" and the sources shown under every briefing (keyword and
  meaning-based search both run on the server; only reading the question costs anything, as below);
- repeat questions: a question already answered with the same settings is served from the saved copy for up to a
  week, or until new records arrive;
- shared briefing links, and printing or copying a briefing;
- MP and peer pages, finding an MP by postcode, election results and majorities, voting records and rebellions;
- re-ranking, if it is turned on: it uses a small model on the server, not a paid AI service;
- the twice-daily data update, the search index, the backups, and the status, admin and information pages;
- the pages that use no AI: In Parliament this week, charts, bill and debate pages, timelines, the glossary, RSS
  feeds, early day motions, the registers of interests, committee pages and witness lists.

## Small costs (well under 1p each)

- Reading the question (choosing the filters shown as chips) costs about 0.01p with Haiku, which has done this since
  11 October 2026. With Opus it was about 0.4p, and the test set shows Haiku is nearly as accurate. Because it is so
  cheap, Haiku also reads the question for "Sources only".
- Running the test set: reading 60 questions costs about 1p with Haiku and about 25p with Opus. Grading the relevance
  of sources costs a few pence a run, and only for passages not graded before.

## Larger costs

- A new briefing costs about 4p (5 cents) with Sonnet, the model used since October 2026, and is most of the running
  cost. With Opus it was about 9p. Briefings in "comprehensive" mode use more sources and more thinking, so cost
  roughly double. The test set compares the quality of the two models.
- A comparison (two MPs, parties or nations side by side) costs one or two briefings.
- A topic briefing costs about 5p each time it is rewritten (Sonnet). The 16 topics cost about £2 to write the
  first time, then roughly £2 to £6 a month. A topic is rewritten at most weekly, and only when new records have
  arrived and the sources found have changed (checking that is nearly free). At most 4 are rewritten per update, and
  none once a daily or monthly budget is reached.
- Featured briefings are free, since they are briefings already written.
- An AI summary for In Parliament this week, if it is added, would cost about 50p a month (one briefing a week).
- Summaries of committee evidence, if they are added, would cost about 4p to 15p per session, written once.
- A full briefing comparison in the test set costs about £2 for 10 questions with two models.

## Fixed costs

- The server (Hostkey), monthly, shared with the charity's other sites on it (Where Councils Stand, Where Landlords
  Stand and Where Countries Stand).
- The domain names, yearly.
- Email for admin@wherempsstand.com, if it is a paid service.

## Spending controls

- The site stops writing new briefings for the day at $10, and for the month at $150. Sources are still shown.
- After every 10 new briefings in a day, a visitor sees a short note on what the site costs, with a link to the
  charity's Donate page (wheretheystand.uk/donate). It is not a paywall: closing it carries on as before, and a
  visitor who goes through to the Donate page is not reminded again that day.
- One visitor can have at most 40 new briefings a day, as a backstop against automated use.
- The admin page shows spending, with warnings at 50% and 80% of the monthly budget.
- A spending limit set in the Anthropic Console (Settings, Limits) is a second safeguard.
