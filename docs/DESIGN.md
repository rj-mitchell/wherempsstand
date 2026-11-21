# Design

The visual design of Where MPs Stand, which follows version 2.0 of the Where They Stand family kit. The site's own
styles are in `web/src/styles.css`; the kit's shared tokens, fonts, endorsement and footer shelf are in
`web/public/family/`.

## Layout

- White pages with light grey panels. The briefing, source cards, the settings panel, forms and the footer shelf sit
  on panels (`--panel`); the page itself is `--paper`.
- A solid band of Commons green across the top of every page, holding the site's mark, its name, the family
  endorsement and the menu in white. Under it, a GOV.UK-style phase banner says the service is in beta.
- Every part of a page lines up to one column, the same width as the header band and the footer (the kit's 72rem
  with its gutter). Running text is held to about two-thirds of that (`--measure`), left-aligned; panels, tables,
  grids and lists of sources use the full column.
- Nearly square corners: panels have none (`--radius` is 0), and buttons, fields, tags and citation numbers have a
  small radius (`--radius-small`, 3px).
- On the home page the family's line on how briefings are written and its fold-out sit just under the question box,
  then the block that shows the checking (below), then the example questions beside other ways in. Under a briefing
  the question box is pinned to the foot of the screen.
- One marker for every fold-out (`<details>`): a triangle in the accent colour that turns down when it is open (the
  turn is animated only for people who have not asked for reduced motion). The header's menus and the Export menu
  keep their ▾.
- Colour only where it carries meaning: the House, a citation, a warning.

The site's mark is the bound record, open: handwriting and a pen on the left page, the same words found by search on
the right, under a magnifying glass. It is drawn in `web/public/icon.svg`, `web/partials/seal.html` and
`web/src/components/Mark.tsx`, which are kept the same by hand.

## Colour and type

| Token (`styles.css`) | Use | Value |
|---|---|---|
| `--paper`, `--panel`, `--ink` | Page, panels and text | The kit's white page, light grey panel and near-black ink by day; its dark equivalents by night |
| `--band`, `--cloth-commons` | The header band, Commons source numbers | The kit's Commons green, `#1D4535` (`#2F7056` in dark mode) |
| `--commons` (also `--accent`) | The Commons, links, focus rings, primary buttons | The kit's Commons green text colour, `#1D4535` (`#93C9AC` in dark mode) |
| `--lords`, `--cloth-lords` | The Lords | Lords red, `oklch(43% 0.13 24)` light, `oklch(75% 0.11 22)` dark |
| `--warn`, `--danger` | Warnings, errors | Amber and red, always with words (a briefing's marks use the kit's `--wts-warn`) |

The House decides the colour: green for the Commons and red for the Lords, as on the bound volumes and the benches.
These describe the Houses, not a side in any vote. Each source's number is set on its House's colour.

Votes have no colour. Every vote is shown in words and numbers ("Voted Aye", "Ayes 312, Noes 198", a table of each
party's ayes and noes), and nothing is green for aye or red for no, which would imply that one way is right. If
votes are ever drawn as marks, they should use the family's neutral marks, as Where Councils Stand and Where
Countries Stand do: filled for aye, outlined for no, hatched for abstained, always beside the word.

Charts draw each series with a different marker shape as well as a colour, so they can be read without colour.

Headings are in Source Serif 4 and everything else in Source Sans 3, both under the SIL Open Font Licence and both
served from the site itself: Source Sans 3 from the kit's `fonts.css`, and Source Serif 4 from
`@fontsource-variable/source-serif-4`, which the kit's styles are pointed at (`--wts-serif` on `body`). Nothing is
loaded from Google. Labels and headings are in sentence case at normal letter spacing.

Dark mode follows the system setting. The CSS uses logical properties, colour tokens that hold both the light and
dark values (`light-dark()`), one fluid scale for type and space, hover styles only where a mouse is present,
visible focus rings, animation only for people who have not asked for reduced motion, a print stylesheet that opens
collapsed sources, and a forced-colours mode.

## The family kit

Where MPs Stand carries the same family elements as every Where They Stand site, from the charity's family kit,
copied into `web/public/family/` by the kit's `sync.mjs`:

- the endorsement under the site's name in the header band: the site's spine and "A Where They Stand project",
  linking to wheretheystand.uk;
- the family shelf at the foot of every page: the charity, its status, wheretheystand.uk, the Donate page, and every
  project's spine with a link to each;
- the favicon, theme colour and link card: the site's open-book mark on its Commons green tile, and the theme
  colour `#1D4535`. The kit's `head.html` replaces the site's own icon links and theme colour, so there is one set;
  the web manifest points at the same icons, and `/favicon.ico` and `/apple-touch-icon.png` are copies of the kit's.
  Every page without an image of its own shares the kit's link card (`/family/card.png`); a saved briefing and each
  topic page have their own ("Share cards", below);
- the kit's tokens, fonts and focus ring, and Source Serif 4 for headings through `class="wts-headings"` on `<body>`.

The kit's files are used as they are and never edited here. The static pages include them at build time
(`<!--#include family/shelf-->` in the partials, `web/vite.config.ts`), and the React pages read the same files
(`web/src/family.ts`), so the two cannot drift. A test (`web/src/__tests__/family.test.tsx`) checks the copy against
the hash in its `VERSION` file, and that every page carries the family elements; CI checks the kit version. To
update the kit, from a checkout of the charity's repository:

```bash
node <charity checkout>/family/kit/sync.mjs --site mps --out web/public/family
cp web/public/family/favicon.ico web/public/family/apple-touch-icon.png web/public/
```

Then raise the version in the `kit` job of `.github/workflows/ci.yml` in the same change.

## The briefing

A briefing looks and behaves as on the family's other briefing sites, in Commons green: the kit's `briefing.css`
(linked by the pages that show briefings or their marks: the app's page and the methodology) and the markup and words
of the charity's `family/BRIEFINGS.md`. The site adds only the layout around the kit's parts (`styles.css`, "A checked
briefing"). The parts are drawn in `web/src/components/AnswerView.tsx`, `CheckStrip.tsx`, `Record.tsx` and
`Wait.tsx`, and the text by `render.ts`, from the rules in `checked.ts` (decisions 45 to 47).

- The check strip at the top: "Checked against Hansard" and three counts in words (citations matched, quotations
  found word for word, sentences without a source), from the answer as shown. The counts are wts-core's
  `check_counts`; the web app counts the same way, and `tests/fixtures/briefings/counts.json` holds the two to the
  same answers. A count with something to check says "To check" and links to the first one. The full note, "How this
  briefing was checked", is at the end with the verifier's own counts.
- The text: the record's own words in the record's typeface on its highlight, with "✓ word for word"; a quotation not
  found word for word underlined and saying so; chips naming who said it, where and when after each cited passage;
  text with no source underlined in dots, the first tagged "No source". Every mark has words for screen readers, and
  each chip is named by its own words.
- The record: beside the text on wide screens (64rem and over), a sticky panel open on the first source cited,
  following the reader, with a line from the chosen chip to its passage; on narrower screens a bottom sheet. Either
  links to the official report and to the source panel with every detail of the source.
- Printing keeps the chips' numbers and the list of sources; the Word download puts the check summary first.

## The wait

While a briefing is prepared, the page shows its stages from the stream's events: the family's spines, the question
as read in large chips, the records found dealt as cards in the form of a Hansard column into a pile (down the side
on wide screens, along a shelf on phones), each marked "Cited" as the text cites it, and, when the briefing ends, the
strip's counts running up through the real results before the pile settles into the record panel. With reduced
motion the stages are the same, without movement. Nothing waits for an animation (decision 47).

## The home page

Under the question box, the kit's proof block (`wts-proof`): the three steps, a real sentence of the topic briefing on
the cost of living with its record quotation and chip, and the Hansard column it came from with the same words
highlighted. It is plain HTML in the page (`web/partials/proof.html`, included in `index.html` for readers without
scripts and shown by the app from the same file).

## Share cards

A saved briefing's link preview is its own card (`/b/<id>/card.png`), and each topic page has one
(`/topics/<slug>/card.png`): wts-core's `briefing_card`, drawn once with Pillow in Commons green with Source Serif 4
and Source Sans 3 from `data_static/fonts/` and the site's mark, and kept in the data folder's `cards/`
(`src/hansard/api/cards.py`; raise `CARD_VERSION` when what a card shows changes). A briefing's card has its neutral
title, a topic's the topic's own title, never a reader's question (decision 36), and the check strip's counts.

## Links to sister sites

A link to a sister site always names it and shows its spine, drawn by the family kit (the same SVG as on the footer
shelf, read from `web/public/family/shelf.html`, never drawn here), so readers know they are moving to another site.

- Under a briefing, live or saved, a short line offers the same question on a sister site: "Ask the same question
  on Where Councils Stand" when the question as read touches local services, "Ask the same question on Where
  Countries Stand" when it touches foreign affairs. The rule (`web/src/sisters.ts`, decision 43) works from the
  policy areas in the plan the site made when it read the question, with no further AI. Both sites take the
  question in `?q=` on their home page.
- On a sitting MP's page, "Councils" lists the councils whose areas overlap the seat, in a quiet block headed "On
  Where Councils Stand" with that site's spine, as Where Councils Stand shows its own links to sister sites. Each
  council links to its page there. The county council comes first, then the others by how much of the seat they
  cover (decision 41).
- On a member's page, "Quoted in" lists the topic briefings whose checked citations quote them, newest first, each
  with the day it was prepared (decision 42). These are this site's own pages.

The server writes the councils and "Quoted in" into the member's page for readers and search engines that do not run
scripts (`src/hansard/api/plaintext.py`); the briefing's line is drawn by the app.

## Accessibility

The target is WCAG 2.2 AA. CI runs axe (`scripts/a11y.mjs`) on the main pages, light and dark, at desktop and phone
widths, against the built site on sample data, and on three real saved briefings with every kind of mark (from
`tests/fixtures/briefings/`, with the bottom sheet open on a phone) and on the wait part-way through. The site's accessibility statement is at
[/accessibility](https://wherempsstand.com/accessibility).
