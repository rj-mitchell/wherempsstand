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
- On the home page the example questions sit beside the panel on how briefings are checked; on a narrow screen they
  stack, with the panel first, just under the question box. Under a briefing the question box is pinned to the foot
  of the screen.
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
| `--warn`, `--danger` | Warnings (statements not tied to a source), errors | Amber and red, always with words |

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
  Every page without an image of its own shares the kit's link card (`/family/card.png`); a briefing also has its
  own card for embedding (`/b/<id>/card.png`, drawn by `src/hansard/api/cards.py`);
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

## Links to sister sites

Under a briefing, a short line offers the same question on a sister site, by name and with its spine from the
family shelf: "Ask Where Councils Stand" for local services, "Ask Where Countries Stand" for foreign affairs. A short
rule for each (`web/src/sisters.ts`) decides when it shows, so it appears only where the other site's record deals
with the subject. Both sites take the question in `?q=` on their home page.

## Accessibility

The target is WCAG 2.2 AA. CI runs axe (`scripts/a11y.mjs`) on the main pages, light and dark, at desktop and phone
widths, against the built site on sample data. The site's accessibility statement is at
[/accessibility](https://wherempsstand.com/accessibility).
